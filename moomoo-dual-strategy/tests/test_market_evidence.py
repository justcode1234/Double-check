import hashlib
import json
import sqlite3
from dataclasses import asdict, replace
from datetime import datetime, timedelta

import pytest

from dual_strategy.data.calendar import NY
from dual_strategy.data.evidence import (
    MAX_CSV_BYTES, MarketEvidenceError, observation_provenance, record_bars, resolve_bars,
)
from dual_strategy.data.ingest import IngestConvention, IngestedBars
from dual_strategy.portfolio.ledger import Ledger, STRATEGIES
from dual_strategy.risk.state_machine import RiskState, RuntimeState

AT = datetime(2026, 11, 17, 10, tzinfo=NY)
CONVENTION = IngestConvention(1, 'end', 1)
HEADER = 'symbol,end,open,high,low,close,volume'
RAW = HEADER + '\nUS.AAPL,2026-11-17T10:00:00-05:00,100,102,99,101,1000\n'
DIGEST = 'test-config'


@pytest.fixture
def ledger(tmp_path):
    value = Ledger(tmp_path / 'evidence.sqlite3')
    value.initialize(DIGEST)
    for strategy in STRATEGIES:
        value.save_state(strategy, RiskState(strategy, runtime=RuntimeState.SHADOW).to_dict())
    yield value
    value.close()


def record(ledger, raw=RAW, **kwargs):
    options = dict(convention=CONVENTION, retrieved_at=AT, source='isolated-test-fixture',
                   digest=DIGEST, provenance='SYNTHETIC')
    options.update(kwargs)
    return record_bars(ledger, 'orb', raw, **options)


def resolve(ledger, reference, **kwargs):
    options = dict(asof=AT, digest=DIGEST)
    options.update(kwargs)
    return resolve_bars(ledger, 'orb', reference, **options)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def sha(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def forge(ledger, payload, *, at=None, event_id=None):
    identity = 'market-observation:' + sha(canonical({
        'strategy': 'orb', 'digest': DIGEST, 'payload': payload,
    }))
    ledger.append('orb', 'MARKET_OBSERVATION', payload, DIGEST, event_id or identity,
                  at or payload['retrieved_at'])
    return ledger.events()[-1]['event_hash']


@pytest.mark.parametrize('provenance', ['RECORDED', 'SYNTHETIC'])
def test_schema_exact_raw_and_typed_normalization(ledger, provenance):
    raw = '\ufeff' + RAW.replace('\n', '\r\n')
    reference = record(ledger, raw, provenance=provenance)
    event = ledger.events()[-1]
    result = resolve(ledger, reference)
    assert isinstance(result, IngestedBars)
    expected_bars = [{
        'symbol': 'US.AAPL', 'end': AT.isoformat(), 'open': 100.0, 'high': 102.0,
        'low': 99.0, 'close': 101.0, 'volume': 1000.0, 'turnover': None, 'minutes': 1,
    }]
    expected = {
        'schema_version': 1, 'kind': 'bars_csv', 'raw_csv': raw, 'raw_sha256': sha(raw),
        'convention': asdict(CONVENTION), 'source': 'isolated-test-fixture',
        'retrieved_at': AT.isoformat(), 'provenance': provenance,
        'normalized_sha256': sha(canonical(expected_bars)), 'provider_origin_verified': False,
    }
    assert event['kind'] == 'MARKET_OBSERVATION'
    assert event['payload'] == expected
    assert event['config_hash'] == DIGEST
    assert event['at'] == AT.isoformat()
    assert reference == event['event_hash']
    assert event['event_id'] == 'market-observation:' + sha(canonical({
        'strategy': 'orb', 'digest': DIGEST, 'payload': expected,
    }))
    assert result.provenance.raw_sha256 == sha(raw)
    assert result.provenance.field_map == {key: key for key in HEADER.split(',') + ['turnover']}
    assert result.provenance.symbol_map == {}
    assert observation_provenance(ledger, 'orb', reference, asof=AT, digest=DIGEST) == provenance
    assert ledger.verify_chain()


def test_default_provenance_is_recorded_not_authenticated(ledger):
    reference = record_bars(ledger, 'orb', RAW, convention=CONVENTION,
                            retrieved_at=AT, source='unverified-provider-name', digest=DIGEST)
    assert observation_provenance(ledger, 'orb', reference, asof=AT, digest=DIGEST) == 'RECORDED'
    assert ledger.events()[-1]['payload']['provider_origin_verified'] is False


def test_explicit_scales_start_labels_and_sorting(ledger):
    convention = IngestConvention(10, 'start', 1, 'America/New_York', 100, 10, 'unadjusted')
    raw = HEADER + ',turnover\n' + (
        'US.SPY,2026-11-17T09:59:00,1000,1020,990,1010,100,101000\n'
        'US.AAPL,2026-11-17T09:58:00,1000,1020,990,1010,100,\n'
    )
    result = resolve(ledger, record(ledger, raw, convention=convention))
    assert [bar.symbol for bar in result.bars] == ['US.AAPL', 'US.SPY']
    assert result.bars[-1].end == AT
    assert result.bars[-1].close == 101
    assert result.bars[-1].volume == 10
    assert result.bars[-1].turnover == 1010
    assert result.bars[0].turnover is None


def test_idempotent_restart_preserves_capital_and_terminal_state(ledger):
    terminal = RiskState('orb', runtime=RuntimeState.TERMINATED, nav=47000,
                         high_water_nav=51000, session=AT.date(), halted_on=AT.date())
    ledger.save_state('orb', terminal.to_dict() | {'manual_lock': True, 'cursor': 'keep'})
    before_state = ledger.load_state('orb')
    before_snapshot = ledger.snapshot('orb')
    reference = record(ledger)
    count = len(ledger.events())
    assert record(ledger) == reference
    reopened = Ledger(ledger.path)
    try:
        assert record(reopened) == reference
        assert resolve(reopened, reference).bars == resolve(ledger, reference).bars
        assert len(reopened.events()) == count
        assert reopened.load_state('orb') == before_state
        assert reopened.snapshot('orb') == before_snapshot
        assert len([e for e in reopened.events('orb') if e['kind'] == 'INITIAL_CAPITAL']) == 1
    finally:
        reopened.close()


@pytest.mark.parametrize('raw', [
    '', HEADER + '\n', RAW.replace('volume', 'close'), RAW.replace('volume', 'amount'),
    RAW.replace('volume\n', 'volume,extra\n'), RAW.replace('volume\n', 'volume,turnover,extra\n'),
    RAW.replace(',1000\n', ',1000,123\n'), RAW.replace(',1000\n', '\n'),
    RAW.replace(',1000\n', ',"unterminated\n'), RAW + RAW.split('\n')[1] + '\n',
    RAW.replace('US.AAPL', ' US.AAPL'), RAW.replace('US.AAPL', ''),
    RAW.replace('T10:00:00-05:00', 'T10:00:01-05:00'),
    RAW.replace('T10:00:00-05:00', 'T10:00:00'),
    RAW.replace(',100,102,99,101,', ',103,102,99,101,'),
    RAW.replace(',100,102,99,101,', ',100,102,101,101,'),
    RAW.replace(',100,102,99,101,', ',0,102,99,101,'),
    RAW.replace(',1000\n', ',-1\n'), RAW.replace(',1000\n', ',nan\n'),
    RAW.replace(',1000\n', ',inf\n'), RAW.replace(',101,', ',nan,'),
    RAW.replace(',100,', ',inf,'), RAW.replace(',102,', ',1e309,'),
    RAW.replace('US.AAPL', '\ud800'),
])
def test_invalid_csv_is_rejected_without_writes(ledger, raw):
    before = ledger.events()
    state = ledger.load_state('orb')
    with pytest.raises(ValueError):
        record(ledger, raw)
    assert ledger.events() == before
    assert ledger.load_state('orb') == state
    assert not ledger.db.in_transaction


@pytest.mark.parametrize('raw', ['x' * (MAX_CSV_BYTES + 1), '\u00e9' * (MAX_CSV_BYTES // 2 + 1)])
def test_csv_byte_limit(ledger, raw):
    before = ledger.events()
    with pytest.raises(MarketEvidenceError, match='5 MiB'):
        record(ledger, raw)
    assert ledger.events() == before


def test_exact_csv_size_limit_is_accepted(ledger):
    raw = RAW + '\n' * (MAX_CSV_BYTES - len(RAW.encode('utf-8')))
    reference = record(ledger, raw)
    assert resolve(ledger, reference).provenance.raw_sha256 == sha(raw)


def test_stored_oversized_csv_is_rejected_before_normalization(ledger):
    record(ledger)
    raw = 'x' * (MAX_CSV_BYTES + 1)
    payload = ledger.events()[-1]['payload'] | {'raw_csv': raw, 'raw_sha256': sha(raw)}
    with pytest.raises(ValueError, match='5 MiB'):
        resolve(ledger, forge(ledger, payload))


@pytest.mark.parametrize('turnover,volume,scale', [
    ('100', '1000', None), ('nan', '1000', 1), ('inf', '1000', 1),
    ('-1', '1000', 1), ('100', '0', 1),
])
def test_invalid_turnover(ledger, turnover, volume, scale):
    raw = HEADER + ',turnover\n' + f'US.AAPL,{AT.isoformat()},100,102,99,101,{volume},{turnover}\n'
    with pytest.raises(ValueError):
        record(ledger, raw, convention=replace(CONVENTION, turnover_scale=scale))
    assert len(ledger.events()) == 2


@pytest.mark.parametrize('options', [
    {'source': ''}, {'source': ' '}, {'source': None}, {'digest': ''}, {'digest': None},
    {'retrieved_at': AT.replace(tzinfo=None)}, {'retrieved_at': 'invalid'},
    {'convention': {}}, {'provenance': 'MOOMOO_VERIFIED'}, {'provenance': None},
])
def test_invalid_metadata_has_no_writes(ledger, options):
    before = ledger.events()
    with pytest.raises(ValueError):
        record(ledger, **options)
    assert ledger.events() == before


def test_strategy_and_initialized_state_required(tmp_path):
    ledger = Ledger(tmp_path / 'uninitialized.sqlite3')
    try:
        with pytest.raises(ValueError, match='Initialize'):
            record(ledger)
        with pytest.raises(ValueError, match='Unknown strategy'):
            record_bars(ledger, 'unknown', RAW, convention=CONVENTION,
                        retrieved_at=AT, source='test', digest=DIGEST)
        assert ledger.events() == []
    finally:
        ledger.close()


@pytest.mark.parametrize('state', [{}, RiskState('noise_area').to_dict()])
def test_corrupt_or_mismatched_initialized_state_has_no_event_writes(ledger, state):
    ledger.save_state('orb', state)
    before = ledger.events()
    with pytest.raises(ValueError, match='initialized strategy state'):
        record(ledger)
    assert ledger.events() == before
    assert ledger.load_state('orb') == state


@pytest.mark.parametrize('reference', ['', 'fabricated', 'f' * 64, 'f' * 65, None])
def test_fabricated_references(ledger, reference):
    with pytest.raises(ValueError, match='reference'):
        resolve(ledger, reference)


def test_cross_ledger_kind_strategy_and_config_references(ledger, tmp_path):
    reference = record(ledger)
    other = Ledger(tmp_path / 'different.sqlite3')
    try:
        with pytest.raises(ValueError, match='this ledger'):
            resolve(other, reference)
    finally:
        other.close()
    with pytest.raises(ValueError, match='strategy mismatch'):
        resolve_bars(ledger, 'noise_area', reference, asof=AT, digest=DIGEST)
    with pytest.raises(ValueError, match='config digest mismatch'):
        resolve(ledger, reference, digest='other-config')
    with pytest.raises(ValueError, match='MARKET_OBSERVATION'):
        resolve(ledger, ledger.events('orb')[0]['event_hash'])
    assert record(ledger, digest='other-config') != reference


def test_availability_and_aware_asof(ledger):
    reference = record(ledger, retrieved_at=AT + timedelta(seconds=1))
    with pytest.raises(ValueError, match='availability.*future'):
        resolve(ledger, reference)
    assert resolve(ledger, reference, asof=AT + timedelta(seconds=1)).bars[0].end == AT
    with pytest.raises(ValueError, match='timezone'):
        resolve(ledger, reference, asof=AT.replace(tzinfo=None))
    with pytest.raises(ValueError, match='aware datetime'):
        resolve(ledger, reference, asof='invalid')


@pytest.mark.parametrize('field,value,match', [
    ('raw_csv', RAW.replace(',101,', ',100,'), 'raw CSV hash mismatch'),
    ('raw_sha256', '0' * 64, 'raw CSV hash mismatch'),
    ('normalized_sha256', '0' * 64, 'normalized hash mismatch'),
    ('convention', asdict(replace(CONVENTION, price_scale=2)), 'normalized hash mismatch'),
    ('provider_origin_verified', True, 'provider origin'),
    ('schema_version', True, 'schema'), ('schema_version', 2, 'schema'),
    ('kind', 'caller_normalized_bars', 'kind'), ('provenance', 'AUTHENTICATED', 'provenance'),
    ('retrieved_at', AT.replace(tzinfo=None).isoformat(), 'timezone'),
    ('convention', {}, 'IngestConvention'),
])
def test_valid_chain_does_not_bypass_payload_reverification(ledger, field, value, match):
    record(ledger)
    payload = ledger.events()[-1]['payload'] | {field: value}
    reference = forge(ledger, payload, at=AT.isoformat())
    assert ledger.verify_chain()
    with pytest.raises(ValueError, match=match):
        resolve(ledger, reference)
    with pytest.raises(ValueError, match=match):
        observation_provenance(ledger, 'orb', reference, asof=AT, digest=DIGEST)


def test_forged_future_unclosed_csv_is_renormalized(ledger):
    record(ledger)
    raw = RAW.replace('T10:00:00', 'T10:01:00')
    payload = ledger.events()[-1]['payload'] | {'raw_csv': raw, 'raw_sha256': sha(raw)}
    reference = forge(ledger, payload)
    with pytest.raises(ValueError, match='Future/unclosed'):
        resolve(ledger, reference, asof=AT + timedelta(minutes=2))


@pytest.mark.parametrize('change,match', [
    ({'at': (AT - timedelta(seconds=1)).isoformat()}, 'timestamp/availability mismatch'),
    ({'event_id': 'invented-id'}, 'payload/event identity mismatch'),
])
def test_forged_envelope(ledger, change, match):
    record(ledger)
    payload = ledger.events()[-1]['payload']
    if 'at' in change:
        payload = payload | {'source': 'changed-source'}
    reference = forge(ledger, payload, **change)
    with pytest.raises(ValueError, match=match):
        resolve(ledger, reference)


def test_unknown_payload_fields_are_rejected(ledger):
    record(ledger)
    payload = ledger.events()[-1]['payload'] | {'field_map': {'close': 'fabricated'}}
    with pytest.raises(ValueError, match='schema'):
        resolve(ledger, forge(ledger, payload))


@pytest.mark.parametrize('column,value', [
    ('payload', '{}'), ('previous_hash', 'b' * 64), ('event_hash', 'c' * 64),
])
def test_event_hash_and_chain_corruption(ledger, column, value):
    reference = record(ledger)
    ledger.db.execute('DROP TRIGGER events_no_update')
    ledger.db.execute(f'UPDATE events SET {column}=? WHERE event_hash=?', (value, reference))
    with pytest.raises(ValueError, match='hash/chain corruption'):
        resolve(ledger, value if column == 'event_hash' else reference)
    with pytest.raises(ValueError, match='hash/chain corruption'):
        record(ledger)


def test_corruption_elsewhere_in_chain_is_rejected(ledger):
    reference = record(ledger)
    ledger.append('noise_area', 'TEST', {}, DIGEST, 'later-event', AT.isoformat())
    ledger.db.execute('DROP TRIGGER events_no_update')
    ledger.db.execute("UPDATE events SET payload='{}' WHERE event_id='initial:noise_area'")
    with pytest.raises(ValueError, match='hash/chain corruption'):
        resolve(ledger, reference)


def test_observations_are_append_only(ledger):
    reference = record(ledger)
    with pytest.raises(sqlite3.IntegrityError, match='append-only'):
        ledger.db.execute("UPDATE events SET payload='{}' WHERE event_hash=?", (reference,))
    with pytest.raises(sqlite3.IntegrityError, match='append-only'):
        ledger.db.execute('DELETE FROM events WHERE event_hash=?', (reference,))
    assert resolve(ledger, reference)


def test_record_failure_rolls_back_and_rejects_outer_transaction(ledger, monkeypatch):
    before = ledger.events()
    state = ledger.load_state('orb')
    original = ledger._append

    def fail_after_append(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError('injected write failure')

    monkeypatch.setattr(ledger, '_append', fail_after_append)
    with pytest.raises(RuntimeError, match='injected'):
        record(ledger)
    assert ledger.events() == before
    assert ledger.load_state('orb') == state
    assert not ledger.db.in_transaction
    with ledger.transaction():
        with pytest.raises(ValueError, match='owns its transaction'):
            record(ledger)
        assert ledger.db.in_transaction
    assert ledger.events() == before
