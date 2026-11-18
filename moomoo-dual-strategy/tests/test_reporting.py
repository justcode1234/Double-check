import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from dual_strategy.data.evidence import record_bars
from dual_strategy.data.ingest import IngestConvention
from dual_strategy.portfolio.ledger import Ledger, STRATEGIES
from dual_strategy.portfolio.runtime import RuntimeStore
from dual_strategy.reporting.metrics import performance
from dual_strategy.reporting.service import csv_export, report_status
from dual_strategy.risk.state_machine import RiskState, RuntimeState


def test_empty_history_does_not_invent_observation_sessions():
    result = performance([{'at': '2026-11-17T17:00:00+00:00', 'nav': 50000, 'observation': False}], [])
    assert result['sample_sessions'] == 0
    assert result['sharpe'] is None
    assert result['win_rate_pct'] is None


def test_drawdown_and_small_sample_ratios():
    equity = [{'at': f'2026-11-{day}T21:00:00+00:00', 'nav': nav} for day, nav in [(16, 51000), (17, 49000), (18, 50500)]]
    result = performance(equity, [{'net_pnl': 100, 'r_multiple': 1}, {'net_pnl': -50, 'r_multiple': -.5}])
    assert abs(result['max_drawdown_pct'] - 2000 / 51000 * 100) < 1e-8
    assert result['sharpe'] is None
    assert result['sortino'] is None
    assert result['profit_factor'] == 2
    assert result['expectancy_dollars'] == 25


def test_status_health_and_redaction(tmp_path):
    ledger = Ledger(tmp_path / 'ledger.sqlite3')
    ledger.initialize('test')
    now = datetime.now(timezone.utc)
    ledger.append('orb', 'SENTINEL_HEARTBEAT', {}, 'test', 'beat', (now - timedelta(seconds=6)).isoformat())
    ledger.append('orb', 'AUDIT', {'acc_id': 'sensitive', 'secret': 'never-render'}, 'test', 'audit')
    ledger.close()
    result = report_status(tmp_path / 'ledger.sqlite3', tmp_path, now)
    assert result['portfolios'][0]['sentinel_status'] == 'STALE'
    assert result['portfolios'][1]['sentinel_status'] == 'NOT_RUNNING'
    assert result['portfolios'][0]['label'] == 'SHADOW / HYPOTHETICAL FILLS'
    assert 'sensitive' not in str(result)
    assert 'never-render' not in str(result)
    assert result['execution_enabled'] is False


def test_recent_unhealthy_heartbeat_is_not_healthy(tmp_path):
    ledger = Ledger(tmp_path / 'ledger.sqlite3')
    ledger.initialize('test')
    now = datetime.now(timezone.utc)
    ledger.append('orb', 'SENTINEL_HEARTBEAT', {'healthy': False}, 'test', 'beat', now.isoformat())
    ledger.close()
    result = report_status(tmp_path / 'ledger.sqlite3', tmp_path, now)
    assert result['portfolios'][0]['sentinel_status'] == 'NOT_HEALTHY'


def test_local_consistency_cannot_certify_actual_paper_results(tmp_path, monkeypatch):
    from dual_strategy.reporting import service
    db = tmp_path / 'ledger.sqlite3'
    ledger = Ledger(db)
    ledger.initialize('test')
    ledger.append('orb', 'FILL', {'symbol': 'US.AAPL', 'qty': 1, 'side': 1, 'price': '100',
                                'fee': '0.99', 'source': 'MOOMOO_PAPER_RECONCILED'}, 'test', 'test-fill')
    ledger.close()
    for broker_lifecycle, expected in ((False, 'UNVERIFIED PAPER EVIDENCE'),
                                       (True, 'BROKER-HISTORY-LINKED PAPER FILLS')):
        monkeypatch.setattr(service, 'audit_orders', lambda ledger: {
            'reconciled': True, 'broker_lifecycle': broker_lifecycle, 'provider_origin_verified': False})
        label = report_status(db, tmp_path)['portfolios'][0]['label']
        assert label.startswith(expected)
        assert 'REAL MOOMOO' not in label


def test_csv_spreadsheet_injection_is_escaped():
    text = csv_export([{'symbol': '=1+1', 'pnl': -5, 'note': '@formula'}])
    assert "'=1+1" in text
    assert "'@formula" in text
    assert '-5' in text


REPORT_AT = datetime(2026, 11, 17, 15, tzinfo=timezone.utc)
REPORT_DIGEST = 'reporting-test-config'
REPORT_PRICES = {'US.AAPL': 101, 'US.SPY': 601}


def _initialize_report_ledger(path):
    ledger = Ledger(path)
    ledger.initialize(REPORT_DIGEST)
    for strategy in STRATEGIES:
        ledger.save_state(strategy, RiskState(strategy, runtime=RuntimeState.SHADOW).to_dict())
    return ledger


@pytest.fixture
def report_ledger(tmp_path):
    ledger = _initialize_report_ledger(tmp_path / 'report.sqlite3')
    try:
        yield ledger
    finally:
        ledger.close()


def _record_report_bars(ledger, *, strategy='orb', provenance='SYNTHETIC',
                        bar_at=REPORT_AT, retrieved_at=REPORT_AT):
    # Deliberately unsorted: a mark must use each symbol's latest normalized close.
    raw = ('symbol,end,open,high,low,close,volume\n'
           f'US.SPY,{bar_at.isoformat()},600,602,599,601,1000\n'
           f'US.AAPL,{bar_at.isoformat()},100,102,99,101,1000\n'
           f'US.AAPL,{(bar_at - timedelta(minutes=1)).isoformat()},100,102,99,100,1000\n')
    return record_bars(ledger, strategy, raw, convention=IngestConvention(1, 'end', 1),
                       retrieved_at=retrieved_at, source='offline-report-test.csv',
                       digest=REPORT_DIGEST, provenance=provenance)


def _append_report_mark(ledger, reference, *, changes=None, at=REPORT_AT, digest=REPORT_DIGEST):
    payload = {'prices': REPORT_PRICES, 'observation_hash': reference,
               'market_provenance': 'SYNTHETIC', 'provider_origin_verified': False}
    ledger.append('orb', 'MARK', payload | (changes or {}), digest,
                  f'test-mark:{len(ledger.events())}', at.isoformat())


def _report(ledger, tmp_path, now=REPORT_AT):
    result = report_status(Path(ledger.path), tmp_path, now)
    assert result['execution_enabled'] is False
    for portfolio in result['portfolios']:
        assert portfolio['feed_provider_origin_verified'] is False
        assert portfolio['feed_status'] not in {'FRESH', 'HEALTHY', 'CONNECTED'}
        assert 'do not authenticate' in portfolio['feed_note']
        assert 'streaming connectivity' in portfolio['feed_note']
        assert portfolio['controls']['start_enabled'] is False
    return result


@pytest.mark.parametrize('provenance,fresh', [
    ('SYNTHETIC', 'SYNTHETIC'), ('RECORDED', 'RECORDED_UNVERIFIED'),
])
@pytest.mark.parametrize('mark_delay,now_delay,status', [
    (0, 0, 'fresh'), (0, 90, 'fresh'), (90, 90, 'fresh'),
    (0, 90.001, 'STALE'), (30, 91, 'STALE'), (0, 180, 'STALE'),
    (0, -1, 'UNVERIFIED'), (30, 29, 'UNVERIFIED'),
])
def test_persisted_runtime_marks_never_authenticate_connectivity(
        report_ledger, tmp_path, provenance, fresh, mark_delay, now_delay, status):
    reference = _record_report_bars(report_ledger, provenance=provenance)
    RuntimeStore(report_ledger, REPORT_DIGEST).mark(
        'orb', REPORT_PRICES, REPORT_AT + timedelta(seconds=mark_delay), reference)
    result = _report(report_ledger, tmp_path, REPORT_AT + timedelta(seconds=now_delay))
    portfolio = result['portfolios'][0]
    assert result['chain_verified'] is True
    assert portfolio['feed_status'] == (fresh if status == 'fresh' else status)
    assert portfolio['mark_age_seconds'] == pytest.approx(now_delay - mark_delay)
    assert result['portfolios'][1]['feed_status'] == 'NOT_CONNECTED'
    assert result['portfolios'][1]['mark_age_seconds'] is None


@pytest.mark.parametrize('provenance', ['SYNTHETIC', 'RECORDED'])
def test_flat_empty_runtime_mark_is_unverified(report_ledger, tmp_path, provenance):
    reference = _record_report_bars(report_ledger, provenance=provenance)
    RuntimeStore(report_ledger, REPORT_DIGEST).mark('orb', {}, REPORT_AT, reference)
    portfolio = _report(report_ledger, tmp_path)['portfolios'][0]
    assert portfolio['positions'] == []
    assert portfolio['nav'] == 50000
    assert portfolio['feed_status'] == 'UNVERIFIED'


def test_observation_without_mark_is_not_connected(report_ledger, tmp_path):
    _record_report_bars(report_ledger)
    result = _report(report_ledger, tmp_path)
    assert all(p['feed_status'] == 'NOT_CONNECTED' for p in result['portfolios'])


@pytest.mark.parametrize('changes', [
    {'observation_hash': 'forged'}, {'observation_hash': 'f' * 64},
    {'observation_hash': None}, {'observation_hash': {'event_hash': 'f' * 64}},
    {'market_provenance': 'RECORDED'}, {'market_provenance': None},
    {'provider_origin_verified': True}, {'provider_origin_verified': None},
    {'provider_origin_verified': 0}, {'prices': {'US.AAPL': 100}},
    {'prices': {'US.AAPL': 101, 'US.SPY': 600}}, {'prices': {'US.UNKNOWN': 101}},
])
def test_bad_latest_mark_proof_overrides_valid_earlier_mark(report_ledger, tmp_path, changes):
    reference = _record_report_bars(report_ledger)
    RuntimeStore(report_ledger, REPORT_DIGEST).mark('orb', REPORT_PRICES, REPORT_AT, reference)
    _append_report_mark(report_ledger, reference, changes=changes, at=REPORT_AT + timedelta(seconds=1))
    result = _report(report_ledger, tmp_path, REPORT_AT + timedelta(seconds=1))
    assert result['chain_verified'] is True
    assert result['portfolios'][0]['feed_status'] == 'UNVERIFIED'


@pytest.mark.parametrize('field', ['observation_hash', 'market_provenance', 'provider_origin_verified'])
def test_missing_mark_proof_is_unverified(report_ledger, tmp_path, field):
    reference = _record_report_bars(report_ledger)
    RuntimeStore(report_ledger, REPORT_DIGEST).mark('orb', REPORT_PRICES, REPORT_AT, reference)
    payload = report_ledger.events('orb')[-1]['payload'].copy()
    del payload[field]
    report_ledger.append('orb', 'MARK', payload, REPORT_DIGEST, 'missing-proof', REPORT_AT.isoformat())
    assert _report(report_ledger, tmp_path)['portfolios'][0]['feed_status'] == 'UNVERIFIED'


@pytest.mark.parametrize('prices', [{}, REPORT_PRICES])
def test_legacy_marks_without_proof_are_unverified(report_ledger, tmp_path, prices):
    report_ledger.append('orb', 'MARK', {'prices': prices}, REPORT_DIGEST, 'legacy', REPORT_AT.isoformat())
    assert _report(report_ledger, tmp_path)['portfolios'][0]['feed_status'] == 'UNVERIFIED'


@pytest.mark.parametrize('mismatch', ['strategy', 'config', 'kind', 'provenance'])
def test_observation_identity_and_provenance_must_match_mark(report_ledger, tmp_path, mismatch):
    reference = _record_report_bars(
        report_ledger, strategy='noise_area' if mismatch == 'strategy' else 'orb',
        provenance='RECORDED' if mismatch == 'provenance' else 'SYNTHETIC')
    if mismatch == 'kind':
        reference = report_ledger.events('orb')[0]['event_hash']
    _append_report_mark(report_ledger, reference,
                        digest='wrong-config' if mismatch == 'config' else REPORT_DIGEST)
    assert _report(report_ledger, tmp_path)['portfolios'][0]['feed_status'] == 'UNVERIFIED'


def test_observation_in_another_ledger_is_orphaned(report_ledger, tmp_path):
    other = _initialize_report_ledger(tmp_path / 'other.sqlite3')
    try:
        reference = _record_report_bars(other)
    finally:
        other.close()
    _append_report_mark(report_ledger, reference)
    assert _report(report_ledger, tmp_path)['portfolios'][0]['feed_status'] == 'UNVERIFIED'


@pytest.mark.parametrize('changes', [
    {'raw_csv': 'symbol,end,open,high,low,close,volume\n'},
    {'raw_sha256': '0' * 64}, {'normalized_sha256': '0' * 64},
    {'provider_origin_verified': True}, {'convention': {}},
])
def test_forged_observation_with_intact_chain_is_unverified(report_ledger, tmp_path, changes):
    _record_report_bars(report_ledger)
    payload = report_ledger.events('orb')[-1]['payload'] | changes
    identity = json.dumps({'strategy': 'orb', 'digest': REPORT_DIGEST, 'payload': payload},
                          sort_keys=True, separators=(',', ':'), allow_nan=False)
    report_ledger.append('orb', 'MARKET_OBSERVATION', payload, REPORT_DIGEST,
                         'market-observation:' + hashlib.sha256(identity.encode()).hexdigest(),
                         REPORT_AT.isoformat())
    _append_report_mark(report_ledger, report_ledger.events('orb')[-1]['event_hash'])
    result = _report(report_ledger, tmp_path)
    assert result['chain_verified'] is True
    assert result['portfolios'][0]['feed_status'] == 'UNVERIFIED'


@pytest.mark.parametrize('column,value', [
    ('previous_hash', 'f' * 64), ('event_hash', 'f' * 64),
    ('at', '2026-11-17T15:00:00'),
])
def test_corrupted_mark_chain_or_timestamp_is_unverified(report_ledger, tmp_path, column, value):
    reference = _record_report_bars(report_ledger)
    RuntimeStore(report_ledger, REPORT_DIGEST).mark('orb', REPORT_PRICES, REPORT_AT, reference)
    report_ledger.db.execute('DROP TRIGGER events_no_update')
    report_ledger.db.execute(f"UPDATE events SET {column}=? WHERE kind='MARK'", (value,))
    result = _report(report_ledger, tmp_path)
    assert result['chain_verified'] is False
    assert result['portfolios'][0]['feed_status'] == 'UNVERIFIED'


@pytest.mark.parametrize('bar_delay,retrieval_delay', [(1, 1), (0, 1)])
def test_future_bar_or_observation_availability_at_mark_is_unverified(
        report_ledger, tmp_path, bar_delay, retrieval_delay):
    reference = _record_report_bars(report_ledger,
                                   bar_at=REPORT_AT + timedelta(seconds=bar_delay),
                                   retrieved_at=REPORT_AT + timedelta(seconds=retrieval_delay))
    _append_report_mark(report_ledger, reference)
    assert _report(report_ledger, tmp_path, REPORT_AT + timedelta(seconds=2))['portfolios'][0]['feed_status'] == 'UNVERIFIED'


@pytest.mark.parametrize('prices,status', [
    (REPORT_PRICES, 'STALE'), ({'US.AAPL': 101, 'US.SPY': 600}, 'UNVERIFIED'),
])
def test_stale_at_mark_only_labels_otherwise_valid_proof_stale(report_ledger, tmp_path, prices, status):
    reference = _record_report_bars(report_ledger)
    at = REPORT_AT + timedelta(seconds=91)
    _append_report_mark(report_ledger, reference, at=at, changes={'prices': prices})
    portfolio = _report(report_ledger, tmp_path, at)['portfolios'][0]
    assert portfolio['mark_age_seconds'] == 0
    assert portfolio['feed_status'] == status


@pytest.mark.parametrize('provenance', ['SYNTHETIC', 'RECORDED'])
def test_public_audit_omits_raw_csv_but_preserves_proof_metadata(report_ledger, tmp_path, provenance):
    reference = _record_report_bars(report_ledger, provenance=provenance)
    RuntimeStore(report_ledger, REPORT_DIGEST).mark('orb', REPORT_PRICES, REPORT_AT, reference)
    stored = next(e for e in report_ledger.events() if e['kind'] == 'MARKET_OBSERVATION')
    result = _report(report_ledger, tmp_path)
    public = next(e for e in result['audit'] if e['kind'] == 'MARKET_OBSERVATION')
    assert public == stored | {'payload': {k: v for k, v in stored['payload'].items() if k != 'raw_csv'}}
    assert public['payload']['provenance'] == provenance
    assert public['payload']['provider_origin_verified'] is False
    assert public['event_hash'] == reference
    assert 'raw_csv' not in json.dumps(result)
    assert 'raw_csv' not in csv_export(result['audit'])
    assert report_ledger.events('orb')[1]['payload']['raw_csv'] == stored['payload']['raw_csv']


def test_reporting_does_not_write_or_change_runtime_state(report_ledger, tmp_path, monkeypatch):
    from dual_strategy.reporting import service

    reference = _record_report_bars(report_ledger)
    runtime = RuntimeStore(report_ledger, REPORT_DIGEST)
    runtime.mark('orb', REPORT_PRICES, REPORT_AT, reference)
    runtime.halt('orb', REPORT_AT + timedelta(seconds=1), terminal=True)
    before = list(report_ledger.db.iterdump())
    files = {p.name: p.stat().st_size for p in tmp_path.iterdir()}
    opened = []

    def read_only_ledger(path, *, read_only=False):
        assert read_only is True
        ledger = Ledger(path, read_only=read_only)
        assert ledger.db.execute('PRAGMA query_only').fetchone()[0] == 1
        opened.append(ledger)
        return ledger

    monkeypatch.setattr(service, 'Ledger', read_only_ledger)
    for _ in range(2):
        result = _report(report_ledger, tmp_path, REPORT_AT + timedelta(seconds=2))
        assert result['portfolios'][0]['runtime'] == 'TERMINATED'
        assert result['portfolios'][0]['controls']['manual_locked'] is True
    assert len(opened) == 2
    assert list(report_ledger.db.iterdump()) == before
    assert {p.name: p.stat().st_size for p in tmp_path.iterdir()} == files


@pytest.mark.parametrize('error', [RuntimeError('internal failure'), sqlite3.OperationalError('database failure')])
def test_reporting_does_not_swallow_internal_or_database_errors(report_ledger, tmp_path, monkeypatch, error):
    from dual_strategy.reporting import service

    reference = _record_report_bars(report_ledger)
    RuntimeStore(report_ledger, REPORT_DIGEST).mark('orb', REPORT_PRICES, REPORT_AT, reference)

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(service, 'resolve_bars', fail)
    with pytest.raises(type(error), match=str(error)):
        report_status(Path(report_ledger.path), tmp_path, REPORT_AT)


def _add_journal_markers(ledger):
    for strategy in STRATEGIES:
        ledger.append(strategy, 'SHADOW_JOURNAL', {'schema_version': 1, 'mode': 'SHADOW_DECISIONS_ONLY'},
                      REPORT_DIGEST, f'shadow-journal:{strategy}', REPORT_AT.isoformat())


def test_valid_shadow_journal_labels_decisions_without_fills(report_ledger, tmp_path):
    _add_journal_markers(report_ledger)
    reference = _record_report_bars(report_ledger)
    RuntimeStore(report_ledger, REPORT_DIGEST).mark('orb', REPORT_PRICES, REPORT_AT, reference)
    result = _report(report_ledger, tmp_path)
    for portfolio in result['portfolios']:
        assert portfolio['label'] == 'SHADOW / DECISIONS ONLY — NO FILLS'
        assert portfolio['paper_fill_count'] == portfolio['shadow_fill_count'] == 0


@pytest.mark.parametrize('defect', ['schema', 'boolean-schema', 'mode', 'identity', 'duplicate', 'config', 'chain'])
def test_invalid_shadow_markers_do_not_change_existing_label(report_ledger, tmp_path, defect):
    payload = {'schema_version': 1, 'mode': 'SHADOW_DECISIONS_ONLY'}
    if defect == 'schema':
        payload['schema_version'] = 2
    elif defect == 'boolean-schema':
        payload['schema_version'] = True
    elif defect == 'mode':
        payload['mode'] = 'SHADOW'
    report_ledger.append('orb', 'SHADOW_JOURNAL', payload,
                         'wrong-config' if defect == 'config' else REPORT_DIGEST,
                         'wrong-id' if defect == 'identity' else 'shadow-journal:orb', REPORT_AT.isoformat())
    if defect == 'duplicate':
        report_ledger.append('orb', 'SHADOW_JOURNAL', payload, REPORT_DIGEST, 'duplicate', REPORT_AT.isoformat())
    if defect == 'chain':
        report_ledger.db.execute('DROP TRIGGER events_no_update')
        report_ledger.db.execute("UPDATE events SET previous_hash=? WHERE kind='SHADOW_JOURNAL'", ('f' * 64,))
    assert _report(report_ledger, tmp_path)['portfolios'][0]['label'] == 'SHADOW / HYPOTHETICAL FILLS'


@pytest.mark.parametrize('sources,label', [
    (['SHADOW'], 'SHADOW / HYPOTHETICAL FILLS'),
    (['MOOMOO_PAPER_RECONCILED'], 'UNVERIFIED PAPER EVIDENCE — DO NOT USE RESULTS'),
    (['SHADOW', 'MOOMOO_PAPER_RECONCILED'], 'INVALID MIXED PROVENANCE — DO NOT USE RESULTS'),
])
def test_shadow_marker_never_hides_existing_trading_labels(report_ledger, tmp_path, sources, label):
    _add_journal_markers(report_ledger)
    for index, source in enumerate(sources):
        report_ledger.append('orb', 'FILL', {'symbol': 'US.AAPL', 'qty': 1, 'side': 1, 'price': '100',
                                           'fee': '0.99', 'source': source},
                             REPORT_DIGEST, f'fill:{index}', REPORT_AT.isoformat())
    assert _report(report_ledger, tmp_path)['portfolios'][0]['label'] == label
