"""Offline adapters only: explicit units, field mappings and timestamp convention.

Scales are DIVISORS: raw price / price_scale, raw turnover / turnover_scale,
and raw volume / volume_scale. No scale, timezone, symbol, or bar-label guessing.
"""

import csv
import hashlib
import io
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from math import isfinite
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from dual_strategy.data.calendar import NY
from dual_strategy.data.validation import DataValidationError
from dual_strategy.models import Bar, aware


@dataclass(frozen=True)
class IngestConvention:
    price_scale: float
    timestamp_semantics: Literal["start", "end"]
    minutes: int
    source_timezone: str | None = None
    turnover_scale: float | None = None
    volume_scale: float = 1.0
    adjustment: str = "unspecified"

    def __post_init__(self) -> None:
        scales = [self.price_scale, self.volume_scale]
        if self.turnover_scale is not None:
            scales.append(self.turnover_scale)
        if any(not isfinite(s) or s <= 0 for s in scales):
            raise ValueError("Explicit finite positive scale divisors are required")
        if self.timestamp_semantics not in ("start", "end"):
            raise ValueError("Timestamp semantics must explicitly be start or end")
        if isinstance(self.minutes, bool) or not isinstance(self.minutes, int) or self.minutes < 1:
            raise ValueError("Explicit positive integer bar minutes are required")
        if self.source_timezone is not None:
            ZoneInfo(self.source_timezone)
        if not self.adjustment.strip():
            raise ValueError("Record adjustment policy, or explicitly use unspecified")


@dataclass(frozen=True)
class Provenance:
    source: str
    retrieved_at: datetime
    raw_sha256: str
    row_count: int
    convention: IngestConvention
    field_map: dict[str, str]
    symbol_map: dict[str, str]

    def as_dict(self) -> dict:
        result = asdict(self)
        result["retrieved_at"] = self.retrieved_at.isoformat()
        return result


@dataclass(frozen=True)
class IngestedBars:
    bars: tuple[Bar, ...]
    provenance: Provenance


CSV_FIELDS = {name: name for name in ("symbol", "end", "open", "high", "low", "close", "volume", "turnover")}
MOOMOO_FIELDS = {**CSV_FIELDS, "symbol": "code", "end": "time_key"}


def _timestamp(raw: object, convention: IngestConvention) -> datetime:
    if isinstance(raw, datetime):
        value = raw
    elif isinstance(raw, str):
        value = datetime.fromisoformat(raw)
    else:
        raise DataValidationError("Timestamp must be explicit ISO text or datetime; epochs need an explicit adapter")
    if value.tzinfo is None or value.utcoffset() is None:
        if convention.source_timezone is None:
            raise DataValidationError("Naive source timestamp requires explicit source_timezone")
        zone = ZoneInfo(convention.source_timezone)
        first, second = value.replace(tzinfo=zone, fold=0), value.replace(tzinfo=zone, fold=1)
        if first.utcoffset() != second.utcoffset():
            raise DataValidationError("Ambiguous/nonexistent local timestamp; supply an explicit UTC offset")
        if first.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) != value:
            raise DataValidationError("Nonexistent local timestamp")
        value = first
    if convention.timestamp_semantics == "start":
        value = value.astimezone(timezone.utc) + timedelta(minutes=convention.minutes)
    return value.astimezone(NY)


def _normalize(
    rows: Sequence[Mapping[str, object]], *, convention: IngestConvention,
    retrieved_at: datetime, source: str, raw_sha256: str,
    field_map: Mapping[str, str], symbol_map: Mapping[str, str] | None,
) -> IngestedBars:
    aware(retrieved_at)
    if not source.strip():
        raise ValueError("A loggable source identifier is required")
    required = {"symbol", "end", "open", "high", "low", "close", "volume"}
    if not required <= field_map.keys():
        raise DataValidationError("Field mapping lacks required OHLCV columns")
    bars: list[Bar] = []
    seen: set[tuple[str, datetime]] = set()
    for index, row in enumerate(rows, start=1):
        try:
            raw_symbol = row[field_map["symbol"]]
            if not isinstance(raw_symbol, str) or not raw_symbol or raw_symbol != raw_symbol.strip():
                raise ValueError("Invalid symbol")
            symbol = symbol_map[raw_symbol] if symbol_map is not None else raw_symbol
            if not isinstance(symbol, str) or not symbol.strip():
                raise ValueError("Invalid mapped symbol")
            end = _timestamp(row[field_map["end"]], convention)
            turnover_key = field_map.get("turnover")
            raw_turnover = row.get(turnover_key) if turnover_key else None
            turnover = None
            if raw_turnover is not None and raw_turnover != "":
                if convention.turnover_scale is None:
                    raise ValueError("Explicit turnover_scale required when turnover is supplied")
                turnover = float(raw_turnover) / convention.turnover_scale
            bar = Bar(
                symbol=symbol, end=end,
                **{k: float(row[field_map[k]]) / convention.price_scale for k in ("open", "high", "low", "close")},
                volume=float(row[field_map["volume"]]) / convention.volume_scale,
                turnover=turnover, minutes=convention.minutes,
            )
            if bar.volume == 0 and bar.turnover not in (None, 0):
                raise ValueError("Nonzero turnover with zero volume")
            if bar.end > retrieved_at:
                raise ValueError("Future/unclosed bar at retrieval time")
            key = (bar.symbol, bar.end)
            if key in seen:
                raise ValueError("Duplicate symbol/timestamp")
            seen.add(key)
            bars.append(bar)
        except (KeyError, TypeError, ValueError) as exc:
            raise DataValidationError(f"Row {index}: {exc}") from exc
    if not bars:
        raise DataValidationError("Empty response")
    return IngestedBars(
        tuple(sorted(bars, key=lambda b: (b.symbol, b.end))),
        Provenance(source, retrieved_at, raw_sha256, len(rows), convention, dict(field_map), dict(symbol_map or {})),
    )


def load_csv(
    path: str | Path, *, convention: IngestConvention, retrieved_at: datetime,
    field_map: Mapping[str, str] | None = None, symbol_map: Mapping[str, str] | None = None,
) -> IngestedBars:
    """Read an explicitly supplied CSV; preserve a hash of the original file bytes."""
    path = Path(path).resolve()
    raw = path.read_bytes()
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
    if reader.fieldnames is None or len(set(reader.fieldnames)) != len(reader.fieldnames):
        raise DataValidationError("Missing or duplicate CSV headers")
    rows = list(reader)
    if any(None in row for row in rows):
        raise DataValidationError("CSV contains excess columns")
    return _normalize(
        rows, convention=convention, retrieved_at=retrieved_at, source=str(path),
        raw_sha256=hashlib.sha256(raw).hexdigest(),
        field_map=CSV_FIELDS if field_map is None else field_map, symbol_map=symbol_map,
    )


def normalize_moomoo_response(
    response: Sequence[Mapping[str, object]] | Mapping[str, object], *,
    convention: IngestConvention, retrieved_at: datetime, source: str,
    rows_key: str | None = None, field_map: Mapping[str, str] | None = None,
    symbol_map: Mapping[str, str] | None = None,
) -> IngestedBars:
    """Normalize already-recorded response rows, never call an API.

    An envelope requires its exact rows_key. Return/status-code interpretation
    belongs to the caller; SDK (ret, DataFrame) tuples are deliberately rejected.
    Symbols such as US.SPY are retained unless an explicit symbol_map is supplied.
    """
    raw = json.dumps(response, sort_keys=True, separators=(",", ":"), default=lambda x: x.isoformat() if isinstance(x, datetime) else str(x))
    if isinstance(response, Mapping):
        if rows_key is None or rows_key not in response:
            raise DataValidationError("Response envelope requires explicit rows_key")
        rows = response[rows_key]
    else:
        if rows_key is not None:
            raise DataValidationError("rows_key supplied for a non-envelope response")
        rows = response
    if not isinstance(rows, (list, tuple)) or any(not isinstance(row, Mapping) for row in rows):
        raise DataValidationError("Expected a sequence of raw row mappings")
    return _normalize(
        rows, convention=convention, retrieved_at=retrieved_at, source=source,
        raw_sha256=hashlib.sha256(raw.encode()).hexdigest(),
        field_map=MOOMOO_FIELDS if field_map is None else field_map, symbol_map=symbol_map,
    )
