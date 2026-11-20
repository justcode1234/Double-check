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


@pytest.fixture
def competition_report(tmp_path, monkeypatch):
    import shutil
    import subprocess
    from types import SimpleNamespace
    from dual_strategy.competition import report
    from dual_strategy.competition.planner import new_state
    from dual_strategy.competition.store import Store

    (tmp_path / 'config').mkdir()
    shutil.copy(Path(__file__).resolve().parents[1] / 'config/competition.yaml', tmp_path / 'config/competition.yaml')
    store = Store(tmp_path, {'state_dir': 'private/competition/live'})
    state = new_state()
    state['h_plan'] = {'asof': '2026-11-19', 'entries': ['US.XLV', 'US.XLP'], 'exits': {}, 'blocked': None}
    store.save(state)

    def processes(args, **kwargs):
        assert args == ['ps', '-axo', 'pid=,stat=,args=']
        return SimpleNamespace(returncode=0, stdout=f'42 S /usr/bin/python {tmp_path}/scripts/run_competition.py loop --execute\n')

    monkeypatch.setattr(report, 'subprocess', SimpleNamespace(run=processes, TimeoutExpired=subprocess.TimeoutExpired), raising=False)
    return report, store


def test_competition_preopen_preserves_plan_without_inventing_nav(competition_report, tmp_path):
    report, store = competition_report
    before = {str(p): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    data = report.competition_payload(tmp_path, now=datetime(2026, 11, 20, 13, tzinfo=timezone.utc))
    assert data['live']['h_plan']['entries'] == ['US.XLV', 'US.XLP']
    assert data['live']['nav_estimate'] is None and data['live']['high_water'] is None
    assert data['runtime']['process']['status'] == 'running'
    assert data['runtime']['market_phase'] == 'preopen'
    assert data['runtime']['mark_status'] == 'not_recorded'
    assert data['runtime']['next_action']['label'] == 'H open orders'
    assert data['runtime']['next_action']['at'] == '2026-11-20T09:30:10-05:00'
    assert {str(p): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()} == before


@pytest.mark.parametrize('age,expected', [(30, 'recent'), (121, 'stale'), (-5, 'clock_mismatch')])
def test_competition_process_does_not_certify_fresh_marks(competition_report, tmp_path, age, expected):
    report, store = competition_report
    now = datetime(2026, 11, 20, 15, tzinfo=timezone.utc)
    state = store.load()
    state.update(initial_nav=100000, high_water=101000, last_nav=100500,
                 last_mark_at=(now - timedelta(seconds=age)).isoformat())
    store.save(state)
    data = report.competition_payload(tmp_path, now=now)
    assert data['runtime']['process']['status'] == 'running'
    assert data['runtime']['mark_status'] == expected
    assert data['runtime']['mark_age_seconds'] == age
    assert data['live']['nav_estimate'] == 100500


def test_competition_half_day_schedule_and_order_error_halt(competition_report, tmp_path):
    report, store = competition_report
    state = store.load()
    state.update(halted={'kind': 'order_error', 'reason': 'broker rejected order'}, order_errors_today=3)
    store.save(state)
    data = report.competition_payload(tmp_path, now=datetime(2026, 11, 27, 17, 56, tzinfo=timezone.utc))
    assert data['runtime']['session_close'] == '2026-11-27T13:00:00-05:00'
    flatten, = [s for s in data['runtime']['schedule'] if s['label'] == 'B3 flatten']
    assert flatten['at'] == '2026-11-27T12:55:00-05:00'
    assert data['live']['halted']['kind'] == 'order_error'
    assert data['live']['order_errors_today'] == 3
    assert data['runtime']['next_action']['label'] == 'Market close'


def test_competition_orders_require_ack_and_keep_early_intents(competition_report, tmp_path):
    report, store = competition_report
    day = '2026-11-20'
    store.append(day, {'event': 'intent', 'intent_id': 'first', 'audit_id': 'a', 'symbol': 'US.XLV', 'sleeve': 'h_etf', 'qty': 11})
    store.append(day, {'event': 'acknowledged', 'intent_id': 'first', 'audit_id': 'a', 'order_id': 'private-order'})
    for _ in range(45):
        store.append(day, {'event': 'runner_error', 'step': 'risk', 'error': 'secret connection detail'})
    store.append(day, {'event': 'outcome', 'intent_id': 'first', 'audit_id': 'a', 'order_id': 'private-order', 'status': 'FILLED', 'filled': 11, 'qty': 11, 'avg_price': 177.123456789})
    store.append(day, {'event': 'intent', 'intent_id': 'unsent', 'audit_id': 'b', 'symbol': 'US.XLP', 'qty': 22})
    store.append(day, {'event': 'order_refused', 'intent_id': 'unsent', 'audit_id': 'b', 'reason': '3 order errors today'})
    data = report.competition_payload(tmp_path, now=datetime(2026, 11, 20, 15, tzinfo=timezone.utc))
    assert data['order_counts'] == {'acknowledged': 1, 'filled': 1, 'partial': 0, 'rejected': 0, 'cancelled': 0, 'errors': 0, 'refused': 1}
    first, second = data['orders']
    assert first['status'] == 'FILLED' and first['avg_price'] == 177.123456789
    assert second['status'] == 'REFUSED' and second['filled'] is None
    assert data['step_errors'] == {'risk': 45}
    assert 'private-order' not in json.dumps(data) and 'secret connection detail' not in json.dumps(data)
    tomorrow = report.competition_payload(tmp_path, now=datetime(2026, 11, 23, 13, tzinfo=timezone.utc))
    assert tomorrow['orders'] == [] and tomorrow['order_counts']['acknowledged'] == 0


def test_competition_charts_only_execute_risk_marks_and_report_incomplete_audit(competition_report, tmp_path):
    report, store = competition_report
    for mode, nav, at in [('EXECUTE', 100000, '14:30:00'), ('PLAN', 999999, '14:31:00'), ('EXECUTE', 99000, '14:32:00')]:
        store.audit('2026-11-20', {'kind': 'risk', 'mode': mode, 'account': {'cash': nav, 'positions': {}, 'quotes': {}, 'at': f'2026-11-20T{at}+00:00'},
                                 'state_after': {'last_nav': nav, 'high_water': 100000, 'last_mark_at': f'2026-11-20T{at}+00:00'}})
    (store.dir / 'audit/2026-11-20/incomplete.json').write_text('{')
    data = report.competition_payload(tmp_path, now=datetime(2026, 11, 20, 15, tzinfo=timezone.utc))
    assert [(p['nav'], p['drawdown_pct']) for p in data['equity']] == [(100000, 0), (99000, -1)]
    assert data['evidence_warnings'] and '999999' not in json.dumps(data['equity'])
    assert data['account_snapshot']['cash'] == 99000


@pytest.mark.parametrize('rows,status', [('', 'stopped'), ('matching', 'running'), ('duplicate', 'multiple'), ('other', 'stopped'), ('zombie', 'stopped')])
def test_competition_detects_only_this_projects_python_loop(competition_report, tmp_path, monkeypatch, rows, status):
    from types import SimpleNamespace
    report, _ = competition_report
    command = f'/usr/bin/python {tmp_path}/scripts/run_competition.py loop --execute'
    listing = {'': '', 'matching': f'42 S {command}', 'duplicate': f'42 S {command}\n43 S {command}',
               'other': '77 S /usr/bin/python /other/scripts/run_competition.py loop --execute', 'zombie': f'42 Z {command}'}[rows]
    monkeypatch.setattr(report.subprocess, 'run', lambda *a, **kw: SimpleNamespace(returncode=0, stdout=listing))
    data = report.competition_payload(tmp_path, now=datetime(2026, 11, 20, 13, tzinfo=timezone.utc))
    assert data['runtime']['process']['status'] == status


def test_competition_process_probe_failure_is_unknown_not_stopped(competition_report, tmp_path, monkeypatch):
    report, _ = competition_report
    def unavailable(*args, **kwargs):
        raise OSError('ps unavailable')
    monkeypatch.setattr(report.subprocess, 'run', unavailable)
    assert report.competition_payload(tmp_path)['runtime']['process']['status'] == 'unknown'


@pytest.mark.parametrize('error,expected', [
    ('CLI quote failed: [AUTH_EXPIRED] expired private-secret', 'sign_in_required'),
    ('CLI history-kline failed: [AUTH_INVALID] invalid private-secret', 'sign_in_required'),
    ('CLI quote failed: {"error":{"code":"AUTH_EXPIRED"}}', 'sign_in_required'),
    ('ExecutionBlocked: Moomoo CLI command failed; reconcile before retry', 'check_required'),
    ('CLI quote failed: [PERMISSION_DENIED] private-secret', 'check_required'),
    ('CLI quote failed: [TIMEOUT] private-secret', 'check_required'),
    ('ValueError: missing daily bar', 'check_required'),
])
def test_competition_access_classifies_evidence_without_auth_probes(competition_report, tmp_path, error, expected):
    report, store = competition_report
    now = datetime(2026, 11, 20, 15, tzinfo=timezone.utc)
    store.append('2026-11-20', {'event': 'runner_error', 'step': 'risk', 'error': error,
                              'logged_at': '2026-11-20T14:59:00+00:00'})
    before = {str(p): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    data = report.competition_payload(tmp_path, now)
    assert data['access']['status'] == expected
    assert data['access']['at'] == '2026-11-20T14:59:00+00:00'
    assert 'private-secret' not in json.dumps(data)
    assert {str(p): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()} == before


def test_competition_access_audit_error_survives_generic_failure_and_recovers(competition_report, tmp_path):
    report, store = competition_report
    now = datetime(2026, 11, 23, 15, tzinfo=timezone.utc)
    store.audit('2026-11-20', {'kind': 'b3_blocked', 'mode': 'EXECUTE',
        'logged_at': '2026-11-20T15:00:00+00:00', 'error': 'CLI quote failed: [AUTH_EXPIRED] private-secret'})
    store.append('2026-11-23', {'event': 'runner_error', 'step': 'risk',
        'logged_at': '2026-11-23T14:58:00+00:00', 'error': 'Moomoo CLI command failed; reconcile before retry'})
    assert report.competition_payload(tmp_path, now)['access']['status'] == 'sign_in_required'
    state = store.load()
    state.update(last_nav=100000, high_water=100000, last_mark_at='2026-11-23T14:59:30+00:00')
    store.save(state)
    data = report.competition_payload(tmp_path, now)
    assert data['access']['status'] == 'recent_success'
    assert data['access']['at'] == state['last_mark_at']
    assert report.competition_payload(tmp_path, now + timedelta(minutes=3))['access']['status'] == 'unverified'
    assert 'private-secret' not in json.dumps(data)


def test_competition_access_plan_only_and_future_evidence_never_certify_auth(competition_report, tmp_path):
    report, store = competition_report
    store.audit('2026-11-20', {'kind': 'b3_blocked', 'mode': 'PLAN',
        'logged_at': '2026-11-20T14:00:00+00:00', 'error': '[AUTH_EXPIRED]'})
    now = datetime(2026, 11, 20, 15, tzinfo=timezone.utc)
    assert report.competition_payload(tmp_path, now)['access']['status'] == 'unverified'
    state = store.load()
    state['last_mark_at'] = '2026-11-20T16:00:00+00:00'
    store.save(state)
    assert report.competition_payload(tmp_path, now)['access']['status'] == 'unverified'


@pytest.mark.parametrize('status,process,age,expected', [
    ('blocked', 'running', 60, 'blocked'), ('unexpected_exit', 'stopped', 60, 'blocked'),
    ('preflight_started', 'stopped', 60, 'checking'), ('preflight_started', 'stopped', 1200, 'blocked'),
    ('launched', 'running', 60, 'launched'), ('launched', 'stopped', 60, 'blocked'),
    ('launched', 'unknown', 60, 'unverified'), ('completed', 'stopped', 60, 'completed'),
])
def test_competition_startup_record_states(competition_report, tmp_path, monkeypatch, status, process, age, expected):
    report, _ = competition_report
    monkeypatch.setattr(report, '_process', lambda project: {'status': process, 'count': None})
    now = datetime(2026, 11, 23, 15, tzinfo=timezone.utc)
    directory = tmp_path / 'private/competition/scheduler'
    directory.mkdir()
    record = {'status': status, 'at': (now - timedelta(seconds=age)).isoformat(), 'session': '2026-11-23',
              'reason': 'read_only_preflight_failed_twice', 'account_id': 'private-secret', 'pid': 999}
    (directory / '2026-11-23.json').write_text(json.dumps(record))
    before = {str(p): p.read_bytes() for p in directory.iterdir()}
    data = report.competition_payload(tmp_path, now)
    assert data['startup']['status'] == expected
    assert data['startup']['session'] == '2026-11-23'
    assert 'private-secret' not in json.dumps(data) and '999' not in json.dumps(data['startup'])
    assert {str(p): p.read_bytes() for p in directory.iterdir()} == before
    assert data['access']['status'] != 'sign_in_required'


def test_competition_prior_startup_block_is_not_hidden_by_later_completion(competition_report, tmp_path):
    report, _ = competition_report
    directory = tmp_path / 'private/competition/scheduler'
    directory.mkdir()
    first = directory / '2026-11-23.json'
    first.write_text(json.dumps({'status': 'blocked', 'session': '2026-11-23', 'at': '2026-11-23T14:05:00Z',
                                 'reason': 'persistent_safety_block'}))
    (directory / '2026-11-24.json').write_text(json.dumps({'status': 'completed', 'session': '2026-11-24',
                                                        'at': '2026-11-24T14:05:00Z'}))
    now = datetime(2026, 11, 25, 14, tzinfo=timezone.utc)
    data = report.competition_payload(tmp_path, now)
    assert data['startup']['status'] == 'blocked' and data['startup']['session'] == '2026-11-23'
    first.write_text(json.dumps({'status': 'completed', 'session': '2026-11-23', 'at': '2026-11-23T14:05:00Z'}))
    assert report.competition_payload(tmp_path, now)['startup']['status'] == 'completed'


@pytest.mark.parametrize('content', ['{', '[]', '{"status":"alien"}',
    '{"status":"completed","session":"2026-11-23","at":"2026-11-24T14:00:00Z"}'])
def test_competition_malformed_startup_is_unverified_not_healthy(competition_report, tmp_path, content):
    report, _ = competition_report
    directory = tmp_path / 'private/competition/scheduler'
    directory.mkdir()
    (directory / '2026-11-23.json').write_text(content)
    data = report.competition_payload(tmp_path, datetime(2026, 11, 23, 15, tzinfo=timezone.utc))
    assert data['startup']['status'] == 'unverified'


def test_competition_startup_absence_and_native_failure_before_daily_record(competition_report, tmp_path):
    report, _ = competition_report
    now = datetime(2026, 11, 20, 15, tzinfo=timezone.utc)
    assert report.competition_payload(tmp_path, now)['startup']['status'] == 'not_recorded'
    directory = tmp_path / 'private/competition/scheduler'
    directory.mkdir()
    log = directory / 'launchd.log'
    log.write_text('{"status":"blocked","reason":"process_probe_failed","error":"private-secret"}\n')
    data = report.competition_payload(tmp_path, now)
    assert data['startup']['status'] == 'blocked'
    assert 'private-secret' not in json.dumps(data)
    log.write_text('{"status":"outside_start_window"}\n')
    assert report.competition_payload(tmp_path, now)['startup']['status'] == 'not_recorded'


def _record_sleeve_fill(store, day, key, sleeve, symbol, side, qty, price, status='FILLED', requested=None):
    link = {'intent_id': key, 'audit_id': day + '/' + key}
    store.append(day, {'event': 'intent', **link, 'mode': 'EXECUTE', 'sleeve': sleeve,
                      'symbol': symbol, 'side': side, 'qty': requested or qty})
    store.append(day, {'event': 'acknowledged', **link, 'order_id': 'private-' + key})
    store.append(day, {'event': 'outcome', **link, 'order_id': 'private-' + key,
                      'status': status, 'qty': requested or qty, 'filled': qty, 'avg_price': price})


def _mark_sleeves(store, sleeves, quotes, cash=96000):
    state = store.load()
    state.update(initial_nav=100000, high_water=100100, last_nav=100020,
                 last_mark_at='2026-11-23T14:59:30+00:00', sleeves=sleeves)
    store.save(state)
    positions = {}
    for book in sleeves.values():
        for symbol, qty in book.items():
            positions[symbol] = positions.get(symbol, 0) + qty
    store.audit('2026-11-23', {'kind': 'risk', 'mode': 'EXECUTE',
        'account': {'at': state['last_mark_at'], 'cash': cash, 'positions': positions, 'quotes': quotes},
        'state_after': state})


def test_competition_profit_belongs_to_h_not_shared_nav_allocation(competition_report, tmp_path):
    report, store = competition_report
    _record_sleeve_fill(store, '2026-11-20', 'xlv-buy', 'h_etf', 'US.XLV', 'BUY', 11, 178.95)
    _record_sleeve_fill(store, '2026-11-20', 'xlp-buy', 'h_etf', 'US.XLP', 'BUY', 21, 91.02)
    _mark_sleeves(store, {'b3': {}, 'h_etf': {'US.XLV': 11, 'US.XLP': 21}},
                  {'US.XLV': 179.29, 'US.XLP': 91.85}, cash=96118.15)
    before = {str(p): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    p = report.competition_payload(tmp_path, datetime(2026, 11, 23, 15, tzinfo=timezone.utc))['performance']
    assert p['sleeves']['b3']['gross_pnl'] == 0
    assert p['sleeves']['b3']['fill_count'] == 0
    h = p['sleeves']['h_etf']
    assert h['realized_gross'] == 0
    assert h['unrealized_gross'] == pytest.approx(21.17)
    assert h['gross_pnl'] == pytest.approx(21.17)
    assert h['fees'] is None and h['net_pnl'] is None
    assert h['fill_count'] == 2
    assert h['positions']['US.XLV']['cost_basis'] == pytest.approx(1968.45)
    assert h['positions']['US.XLP']['unrealized_gross'] == pytest.approx(17.43)
    assert p['account_pnl'] == pytest.approx(19.19)
    assert p['unallocated_account_change'] == pytest.approx(-1.98)
    assert len(h['receipts']) == 2 and h['receipts'][0]['audit_id'] == '2026-11-20/xlv-buy'
    assert 'private-xlv-buy' not in json.dumps(p)
    assert {str(p): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()} == before


def test_competition_same_symbol_sleeves_and_realized_profit_stay_separate(competition_report, tmp_path):
    report, store = competition_report
    _record_sleeve_fill(store, '2026-11-20', 'h-spy', 'h_etf', 'US.SPY', 'BUY', 2, 100)
    _record_sleeve_fill(store, '2026-11-20', 'b-spy', 'b3', 'US.SPY', 'BUY', 10, 110)
    _record_sleeve_fill(store, '2026-11-20', 'b-add', 'b3', 'US.SPY', 'BUY', 10, 120)
    _record_sleeve_fill(store, '2026-11-23', 'b-exit', 'b3', 'US.SPY', 'SELL', 5, 130, 'CANCELLED', requested=20)
    _mark_sleeves(store, {'b3': {'US.SPY': 15}, 'h_etf': {'US.SPY': 2}}, {'US.SPY': 125})
    p = report.competition_payload(tmp_path, datetime(2026, 11, 23, 15, tzinfo=timezone.utc))['performance']
    b, h = p['sleeves']['b3'], p['sleeves']['h_etf']
    assert b['realized_gross'] == pytest.approx(75)
    assert b['unrealized_gross'] == pytest.approx(150)
    assert b['gross_pnl'] == pytest.approx(225)
    assert b['positions']['US.SPY']['cost_basis'] == pytest.approx(1725)
    assert h['gross_pnl'] == pytest.approx(50)
    assert h['positions']['US.SPY']['cost_basis'] == 200


@pytest.mark.parametrize('problem', ['missing_fill', 'missing_ack', 'wrong_ack', 'duplicate_outcome', 'missing_quote', 'wrong_ownership', 'ambiguous_order'])
def test_competition_incomplete_profit_evidence_is_not_guessed(competition_report, tmp_path, problem):
    report, store = competition_report
    if problem != 'missing_fill':
        _record_sleeve_fill(store, '2026-11-20', 'h-buy', 'h_etf', 'US.XLV', 'BUY', 11, 100)
        path = store.dir / 'outbox/2026-11-20.jsonl'
        rows = store.outbox('2026-11-20')
        if problem == 'missing_ack': rows = [r for r in rows if r['event'] != 'acknowledged']
        if problem == 'wrong_ack': rows[1]['order_id'] = 'different'
        if problem == 'duplicate_outcome': rows.append(rows[-1])
        if problem == 'ambiguous_order':
            rows.append({'event': 'intent', 'mode': 'EXECUTE', 'sleeve': 'h_etf', 'symbol': 'US.XLV',
                         'side': 'BUY', 'qty': 1, 'intent_id': 'uncertain', 'audit_id': 'unknown'})
        path.write_text(''.join(json.dumps(r) + '\n' for r in rows))
    _mark_sleeves(store, {'b3': {}, 'h_etf': {'US.XLV': 12 if problem == 'wrong_ownership' else 11}},
                  {} if problem == 'missing_quote' else {'US.XLV': 105})
    p = report.competition_payload(tmp_path, datetime(2026, 11, 23, 15, tzinfo=timezone.utc))['performance']
    assert p['sleeves']['h_etf']['status'] == 'incomplete'
    assert p['sleeves']['h_etf']['gross_pnl'] is None
    assert p['sleeves']['b3']['gross_pnl'] == 0
    assert p['unallocated_account_change'] is None


@pytest.mark.parametrize('problem', ['orphan_ack', 'reused_order_id'])
def test_competition_ambiguous_broker_receipts_cannot_certify_profit(competition_report, tmp_path, problem):
    report, store = competition_report
    _record_sleeve_fill(store, '2026-11-20', 'h-buy', 'h_etf', 'US.XLV', 'BUY', 11, 100)
    if problem == 'orphan_ack':
        store.append('2026-11-20', {'event': 'acknowledged', 'audit_id': 'unknown',
                                  'intent_id': 'missing', 'order_id': 'unknown-order'})
        books = {'b3': {}, 'h_etf': {'US.XLV': 11}}
    else:
        _record_sleeve_fill(store, '2026-11-20', 'b-buy', 'b3', 'US.SPY', 'BUY', 1, 100)
        rows = store.outbox('2026-11-20')
        for row in rows:
            if row.get('order_id') == 'private-b-buy': row['order_id'] = 'private-h-buy'
        (store.dir / 'outbox/2026-11-20.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in rows))
        books = {'b3': {'US.SPY': 1}, 'h_etf': {'US.XLV': 11}}
    _mark_sleeves(store, books, {'US.XLV': 105, 'US.SPY': 105})
    p = report.competition_payload(tmp_path, datetime(2026, 11, 23, 15, tzinfo=timezone.utc))['performance']
    assert all(s['status'] == 'incomplete' and s['gross_pnl'] is None for s in p['sleeves'].values())
    assert p['unallocated_account_change'] is None


def test_competition_closed_trade_keeps_profit_and_plan_never_contributes(competition_report, tmp_path):
    report, store = competition_report
    _record_sleeve_fill(store, '2026-11-20', 'b-buy', 'b3', 'US.SPY', 'BUY', 5, 100)
    _record_sleeve_fill(store, '2026-11-20', 'b-sell', 'b3', 'US.SPY', 'SELL', 5, 103)
    store.append('2026-11-23', {'event': 'intent', 'mode': 'PLAN', 'sleeve': 'h_etf', 'symbol': 'US.XLV', 'qty': 999})
    _mark_sleeves(store, {'b3': {}, 'h_etf': {}}, {}, cash=100013.02)
    p = report.competition_payload(tmp_path, datetime(2026, 11, 23, 15, tzinfo=timezone.utc))['performance']
    assert p['sleeves']['b3']['gross_pnl'] == 15
    assert p['sleeves']['b3']['unrealized_gross'] == 0
    assert p['sleeves']['h_etf']['gross_pnl'] == 0
    assert p['account_pnl'] == pytest.approx(13.02)
    assert p['unallocated_account_change'] == pytest.approx(-1.98)
