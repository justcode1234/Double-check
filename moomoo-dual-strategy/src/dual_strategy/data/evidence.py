import csv
import hashlib
import io
import json
import re
from dataclasses import asdict, replace
from datetime import datetime
from zoneinfo import ZoneInfoNotFoundError

from dual_strategy.data.ingest import CSV_FIELDS, IngestConvention, IngestedBars, normalize_moomoo_response
from dual_strategy.models import aware
from dual_strategy.portfolio.ledger import Ledger, STRATEGIES
from dual_strategy.risk.state_machine import RiskState

MAX_CSV_BYTES = 5 * 1024 * 1024
_MAX_PAYLOAD_CHARS = 6 * MAX_CSV_BYTES + 65536
_PAYLOAD_FIELDS = frozenset({
    'schema_version', 'kind', 'raw_csv', 'raw_sha256', 'convention', 'source',
    'retrieved_at', 'provenance', 'normalized_sha256', 'provider_origin_verified',
})


class MarketEvidenceError(ValueError):
    pass


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _identity(strategy: str, digest: str) -> None:
    if strategy not in STRATEGIES:
        raise MarketEvidenceError('Unknown strategy for market observation')
    if not isinstance(digest, str) or not digest.strip():
        raise MarketEvidenceError('Nonempty config digest required')


def _time(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise MarketEvidenceError('Market observation timestamp must be an aware datetime')
    return aware(value)


def _normalize(raw_csv: str, convention: IngestConvention, retrieved_at: datetime,
               source: str) -> IngestedBars:
    if not isinstance(raw_csv, str) or len(raw_csv) > MAX_CSV_BYTES:
        raise MarketEvidenceError('CSV must be UTF-8 text of at most 5 MiB')
    raw = raw_csv.encode('utf-8')
    if len(raw) > MAX_CSV_BYTES:
        raise MarketEvidenceError('CSV must be UTF-8 text of at most 5 MiB')
    reader = csv.reader(io.StringIO(raw_csv.removeprefix('\ufeff'), newline=''), strict=True)
    headers = next(reader, [])
    if len(headers) != len(set(headers)):
        raise MarketEvidenceError('Duplicate CSV headers')
    if len(headers) > len(CSV_FIELDS) or set(headers) - CSV_FIELDS.keys():
        raise MarketEvidenceError('Unknown or excess CSV headers')
    if not (CSV_FIELDS.keys() - {'turnover'}) <= set(headers):
        raise MarketEvidenceError('Missing required canonical CSV headers')
    rows = []
    for values in reader:
        if not values:
            continue
        if len(values) != len(headers):
            raise MarketEvidenceError('CSV row has missing or excess columns')
        rows.append(dict(zip(headers, values)))
    normalized = normalize_moomoo_response(
        rows, convention=convention, retrieved_at=retrieved_at, source=source,
        field_map=CSV_FIELDS, symbol_map=None,
    )
    return replace(normalized, provenance=replace(
        normalized.provenance, raw_sha256=hashlib.sha256(raw).hexdigest(),
    ))


def _payload(raw_csv: str, convention: IngestConvention, retrieved_at: datetime,
             source: str, provenance: str) -> tuple[dict, IngestedBars]:
    try:
        _time(retrieved_at)
        if not isinstance(source, str) or not source.strip():
            raise MarketEvidenceError('Nonempty market observation source required')
        if provenance not in ('RECORDED', 'SYNTHETIC'):
            raise MarketEvidenceError('Market provenance must be RECORDED or SYNTHETIC')
        if not isinstance(convention, IngestConvention):
            raise MarketEvidenceError('Explicit IngestConvention required')
        convention = IngestConvention(**asdict(convention))
        normalized = _normalize(raw_csv, convention, retrieved_at, source)
        bars = [asdict(bar) | {'end': bar.end.isoformat()} for bar in normalized.bars]
        payload = {
            'schema_version': 1, 'kind': 'bars_csv', 'raw_csv': raw_csv,
            'raw_sha256': normalized.provenance.raw_sha256, 'convention': asdict(convention),
            'source': source, 'retrieved_at': retrieved_at.isoformat(), 'provenance': provenance,
            'normalized_sha256': _sha(_canonical(bars)), 'provider_origin_verified': False,
        }
        if len(_canonical(payload)) > _MAX_PAYLOAD_CHARS:
            raise MarketEvidenceError('Oversized market observation payload')
        return payload, normalized
    except (ValueError, TypeError, AttributeError, OverflowError, csv.Error, ZoneInfoNotFoundError) as exc:
        raise MarketEvidenceError(f'Invalid market observation: {exc}') from exc


def _event_id(strategy: str, digest: str, payload: dict) -> str:
    return 'market-observation:' + _sha(_canonical({
        'strategy': strategy, 'digest': digest, 'payload': payload,
    }))


def record_bars(ledger: Ledger, strategy: str, raw_csv: str, *, convention: IngestConvention,
                retrieved_at: datetime, source: str, digest: str,
                provenance: str = 'RECORDED') -> str:
    _identity(strategy, digest)
    if ledger.db.in_transaction:
        raise MarketEvidenceError('record_bars owns its transaction; record before composing decisions')
    payload, _ = _payload(raw_csv, convention, retrieved_at, source, provenance)
    event_id = _event_id(strategy, digest, payload)
    with ledger.transaction():
        try:
            state = ledger.load_state(strategy)
            if state is None:
                raise MarketEvidenceError('Initialize strategy state before recording market observations')
            if RiskState.from_dict(state).strategy != strategy:
                raise MarketEvidenceError('Initialized state strategy mismatch')
        except (ValueError, KeyError, TypeError) as exc:
            raise MarketEvidenceError(f'Invalid initialized strategy state: {exc}') from exc
        if not ledger.verify_chain():
            raise MarketEvidenceError('Market observation ledger event hash/chain corruption')
        row = ledger.db.execute('SELECT event_hash FROM events WHERE event_id=?', (event_id,)).fetchone()
        if row:
            resolve_bars(ledger, strategy, row['event_hash'], asof=retrieved_at, digest=digest)
            return row['event_hash']
        ledger._append(strategy, 'MARKET_OBSERVATION', payload, digest, event_id, retrieved_at.isoformat())
        return ledger.db.execute('SELECT event_hash FROM events WHERE event_id=?', (event_id,)).fetchone()[0]


def _resolve(ledger: Ledger, strategy: str, observation_hash: str, *, asof: datetime,
             digest: str) -> tuple[IngestedBars, str]:
    _identity(strategy, digest)
    _time(asof)
    if not isinstance(observation_hash, str) or not re.fullmatch('[0-9a-f]{64}', observation_hash):
        raise MarketEvidenceError('Market observation provenance requires a ledger event_hash reference')
    rows = ledger.db.execute(
        'SELECT sequence, length(CAST(payload AS BLOB)) AS payload_size FROM events WHERE event_hash=? LIMIT 2',
        (observation_hash,),
    ).fetchall()
    if len(rows) != 1:
        raise MarketEvidenceError('Market observation reference not found uniquely in this ledger')
    if rows[0]['payload_size'] > _MAX_PAYLOAD_CHARS:
        raise MarketEvidenceError('Oversized market observation payload')
    row = ledger.db.execute('SELECT * FROM events WHERE sequence=?', (rows[0]['sequence'],)).fetchone()
    if row['kind'] != 'MARKET_OBSERVATION':
        raise MarketEvidenceError('Reference is not a MARKET_OBSERVATION event')
    if row['strategy'] != strategy:
        raise MarketEvidenceError('Market observation strategy mismatch')
    if row['config_hash'] != digest:
        raise MarketEvidenceError('Market observation config digest mismatch')
    if not ledger.verify_chain():
        raise MarketEvidenceError('Market observation ledger event hash/chain corruption')
    try:
        payload = json.loads(row['payload'])
        if not isinstance(payload, dict) or payload.keys() != _PAYLOAD_FIELDS:
            raise MarketEvidenceError('Invalid market observation payload schema')
        if (type(payload['schema_version']) is not int or payload['schema_version'] != 1
                or payload['kind'] != 'bars_csv' or payload['provider_origin_verified'] is not False):
            raise MarketEvidenceError('Unsupported market observation schema, kind or provider origin claim')
        if not isinstance(payload['convention'], dict):
            raise MarketEvidenceError('Invalid stored convention')
        convention = IngestConvention(**payload['convention'])
        if payload['convention'].keys() != asdict(convention).keys():
            raise MarketEvidenceError('Incomplete stored convention')
        retrieved_at = _time(datetime.fromisoformat(payload['retrieved_at']))
        if retrieved_at > asof:
            raise MarketEvidenceError('Market observation availability is in the future of asof')
        expected, normalized = _payload(
            payload['raw_csv'], convention, retrieved_at, payload['source'], payload['provenance'],
        )
        if payload['raw_sha256'] != expected['raw_sha256']:
            raise MarketEvidenceError('Market observation raw CSV hash mismatch')
        if payload['normalized_sha256'] != expected['normalized_sha256']:
            raise MarketEvidenceError('Market observation normalized hash mismatch')
        if row['payload'] != _canonical(expected):
            raise MarketEvidenceError('Market observation payload is not canonical')
        if row['at'] != retrieved_at.isoformat():
            raise MarketEvidenceError('Market observation event timestamp/availability mismatch')
        if row['event_id'] != _event_id(strategy, digest, expected):
            raise MarketEvidenceError('Market observation payload/event identity mismatch')
        if any(bar.end > asof for bar in normalized.bars):
            raise MarketEvidenceError('Market observation contains future bars')
        return normalized, payload['provenance']
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError,
            RecursionError, ZoneInfoNotFoundError) as exc:
        raise MarketEvidenceError(f'Invalid market observation: {exc}') from exc


def resolve_bars(ledger: Ledger, strategy: str, observation_hash: str, *, asof: datetime,
                 digest: str) -> IngestedBars:
    return _resolve(ledger, strategy, observation_hash, asof=asof, digest=digest)[0]


def observation_provenance(ledger: Ledger, strategy: str, observation_hash: str, *,
                           asof: datetime, digest: str) -> str:
    return _resolve(ledger, strategy, observation_hash, asof=asof, digest=digest)[1]
