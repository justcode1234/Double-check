"""Load the versioned Moomoo history cache into session x slot arrays (exchange-local America/New_York).

Intraday prices stay RAW (autype 0). Cross-day features are split-adjusted with the Moomoo split list,
which is verified against the jump in the raw/adjusted daily close ratio (see verify_splits).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path

import exchange_calendars
import numpy as np
import pandas as pd

from dual_strategy.data.calendar import NY
from dual_strategy.research.fetch import END, HISTORY, START

OPEN_MINUTE = 9 * 60 + 30
FIELDS = ("open", "high", "low", "close", "volume", "turnover")


@lru_cache(maxsize=1)
def calendar_frame() -> pd.DataFrame:
    """All XNYS sessions in the fetch window with their bar counts (390 normal, 210 half-day)."""
    cal = exchange_calendars.get_calendar("XNYS")
    rows = []
    for s in cal.sessions_in_range(START.isoformat(), END.isoformat()):
        o = cal.session_open(s).tz_convert(NY)
        c = cal.session_close(s).tz_convert(NY)
        rows.append((s.date(), int((c - o).total_seconds() // 60), c.hour * 60 + c.minute))
    return pd.DataFrame(rows, columns=["day", "minutes", "close_minute"]).set_index("day")


def sessions() -> list[date]:
    return list(calendar_frame().index)


@dataclass
class Intraday:
    """RTH bars as (n_days, n_slots) arrays; slot j ends at 09:30 + (j+1)*minutes. Missing bars are NaN."""

    symbol: str
    minutes: int
    days: list[date]
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray
    turnover: np.ndarray
    expected: np.ndarray
    out_of_rth: int = 0
    duplicates: int = 0
    index: dict[date, int] = field(init=False)

    def __post_init__(self) -> None:
        self.index = {d: i for i, d in enumerate(self.days)}

    @property
    def n_slots(self) -> int:
        return self.open.shape[1]

    def end_minute(self, slot: int) -> int:
        return OPEN_MINUTE + (slot + 1) * self.minutes

    def slot_ending(self, hh: int, mm: int) -> int:
        return (hh * 60 + mm - OPEN_MINUTE) // self.minutes - 1

    def present(self) -> np.ndarray:
        return ~np.isnan(self.close)

    def complete(self) -> np.ndarray:
        cols = np.arange(self.n_slots)[None, :]
        need = cols < self.expected[:, None]
        return (self.present() | ~need).all(axis=1) & (self.expected > 0)

    def has_any(self) -> np.ndarray:
        return self.present().any(axis=1)


def _read_csvs(paths: list[Path]) -> pd.DataFrame:
    frames = [pd.read_csv(p) for p in paths]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["end_ms", *FIELDS])


def load_intraday(symbol: str, minutes: int) -> Intraday:
    ktype = {1: 1, 5: 6}[minutes]
    paths = sorted((HISTORY / f"k{ktype}" / symbol).glob("*.csv.gz"))
    df = _read_csvs(paths)
    cal = calendar_frame()
    days = list(cal.index)
    n_slots = 390 // minutes
    expected = (cal["minutes"].to_numpy() // minutes).astype(int)
    arrays = {f: np.full((len(days), n_slots), np.nan) for f in FIELDS}
    out_of_rth = duplicates = 0
    if len(df):
        duplicates = int(df.duplicated("end_ms").sum())
        df = df.drop_duplicates("end_ms", keep="last")
        ts = pd.to_datetime(df["end_ms"].astype("int64"), unit="ms", utc=True).dt.tz_convert(NY).dt.tz_localize(None)
        day = ts.dt.date
        offset = (ts.dt.hour * 60 + ts.dt.minute - OPEN_MINUTE).to_numpy()
        pos = pd.Index(days).get_indexer(day)
        slot = offset // minutes - 1
        exp = np.where(pos >= 0, expected[np.clip(pos, 0, None)], 0)
        ok = (pos >= 0) & (offset % minutes == 0) & (ts.dt.second.to_numpy() == 0) & (slot >= 0) & (slot < exp)
        out_of_rth = int((~ok).sum())
        for f in FIELDS:
            arrays[f][pos[ok], slot[ok]] = pd.to_numeric(df[f], errors="coerce").to_numpy()[ok]
    return Intraday(symbol, minutes, days, *(arrays[f] for f in FIELDS), expected, out_of_rth, duplicates)


def resample(bars: Intraday, minutes: int) -> Intraday:
    """Aggregate finer bars; a coarse bar with any missing constituent is NaN (never partially built)."""
    k = minutes // bars.minutes
    if k * bars.minutes != minutes or bars.n_slots % k:
        raise ValueError("Resample target must be a multiple of the source bar")
    n, m = bars.open.shape[0], bars.n_slots // k

    def blocks(a):
        return a.reshape(n, m, k)

    o, h, l, c, v, t = (blocks(getattr(bars, f)) for f in FIELDS)
    bad = np.isnan(c).any(axis=2)
    with np.errstate(all="ignore"):
        out = [o[:, :, 0].copy(), h.max(axis=2), l.min(axis=2), c[:, :, -1].copy(), v.sum(axis=2), t.sum(axis=2)]
    for a in out:
        a[bad] = np.nan
    return Intraday(bars.symbol, minutes, bars.days, *out, bars.expected * bars.minutes // minutes,
                    bars.out_of_rth, bars.duplicates)


def load_daily(symbol: str, autype: int = 0) -> pd.DataFrame:
    path = HISTORY / f"daily_a{autype}" / f"{symbol}.csv.gz"
    df = pd.read_csv(path)
    df.index = [datetime.strptime(str(int(d)), "%Y%m%d").date() for d in df.pop("date")]
    return df.astype(float)


def _actions(symbol: str) -> dict:
    return json.loads((HISTORY / "actions" / f"{symbol}.json").read_text())


def _parse_date(value: str) -> date:
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%b %d, %Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"Unparseable action date {value!r}")


def split_events(symbol: str) -> list[tuple[date, float]]:
    """(ex_date, new_shares/old_shares) for entries with a share ratio; ratio-less CONSOLIDATION rows are not splits."""
    data = _actions(symbol)["splits"]
    rows = (data.get("data") or data).get("split_list") or []
    out = []
    for r in rows:
        rate = r.get("rate")
        if not rate:
            continue
        old, new = (float(x) for x in rate.replace("->", "→").split("→"))
        out.append((_parse_date(r.get("ex_date_str") or r["ex_date"]), new / old))
    return sorted(out)


def dividends(symbol: str) -> dict[date, float]:
    data = _actions(symbol)["dividends"]
    rows = (data.get("data") or data).get("dividend_list") or []
    out: dict[date, float] = {}
    for r in rows:
        if r.get("dividend_per_share") is not None and r.get("ex_date") not in (None, "--"):
            d = _parse_date(r["ex_date"])
            out[d] = out.get(d, 0.0) + float(r["dividend_per_share"])
    return out


def split_back_factor(symbol: str, days: list[date]) -> np.ndarray:
    """B(d) = product of split ratios with ex_date in (d, END]. A day-s quantity in day-t share units:
    price_s * B(t) / B(s), volume_s * B(s) / B(t). Only splits between s and t enter, so no look-ahead."""
    events = [(d, f) for d, f in split_events(symbol) if d <= END]
    out = np.ones(len(days))
    for ex, f in events:
        out[np.array([d < ex for d in days])] *= f
    return out


def verify_splits(symbol: str, tol: float = 0.01) -> dict:
    """Every >3% jump in the raw/adjusted close ratio must match a listed split, and vice versa."""
    a0, a1 = load_daily(symbol, 0), load_daily(symbol, 1)
    common = a0.index.intersection(a1.index)
    ratio = (a0.loc[common, "close"] / a1.loc[common, "close"])
    jump = ratio / ratio.shift(1)
    observed = {d: float(j) for d, j in jump.items() if abs(j - 1) > 0.03}
    listed = {d: f for d, f in split_events(symbol) if common[0] < d <= common[-1]}
    unmatched_obs = {str(d): j for d, j in observed.items() if d not in listed or abs(j * listed[d] - 1) > tol}
    unmatched_list = {str(d): f for d, f in listed.items() if d not in observed}
    return {"symbol": symbol, "listed_in_window": {str(d): f for d, f in listed.items()},
            "unmatched_observed": unmatched_obs, "unmatched_listed": unmatched_list,
            "ok": not unmatched_obs and not unmatched_list}


def sector_map() -> dict[str, str]:
    path = HISTORY / "sectors.json"
    data = json.loads(path.read_text())["industry"] if path.exists() else {}
    return {s: (v[0] if v else "UNKNOWN") for s, v in data.items()}


def file_hashes() -> dict:
    path = HISTORY / "manifest.json"
    return json.loads(path.read_text()) if path.exists() else {}
