import ast
import csv
import io
from dataclasses import asdict, replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from dual_strategy import forward
from dual_strategy.data.calendar import NY, prior_sessions
from dual_strategy.data.evidence import record_bars
from dual_strategy.data.ingest import IngestConvention
from dual_strategy.forward import MODE, open_journal, run_tick, verify_journal
from dual_strategy.portfolio.ledger import Ledger
from dual_strategy.portfolio.runtime import RuntimeStore
from dual_strategy.risk.state_machine import RiskState
from test_signals import session_bars

AT = datetime(2026, 11, 17, 10, tzinfo=NY)
DIGEST = 'isolated-forward-test'
CONVENTION = IngestConvention(1, 'end', 5, turnover_scale=1, adjustment='synthetic')


@pytest.fixture
def journal(tmp_path):
    ledger = open_journal(tmp_path / 'decisions.sqlite3', DIGEST, create=True)
    yield ledger
    ledger.close()


def csv_bars(bars):
    output = io.StringIO(newline='')
    names = ['symbol', 'end', 'open', 'high', 'low', 'close', 'volume', 'turnover']
    writer = csv.DictWriter(output, names)
    writer.writeheader()
    for bar in bars:
        value = asdict(bar)
        writer.writerow({key: bar.end.isoformat() if key == 'end' else value[key] for key in names})
    return output.getvalue()


def inputs(journal, *, strategy='noise_area', at=AT, variant='long', missing=False, recorded=False):
    symbol = 'US.SPY' if strategy == 'noise_area' else 'US.AAPL'
    history = [bar for day in prior_sessions(at.date(), 14)
               for bar in session_bars(day, symbol, minutes=5)]
    today = session_bars(at.date(), symbol, through=at, minutes=5)
    price = {'long': 110, 'flat': 100, 'short': 90}[variant]
    today[-1] = replace(today[-1], close=price, high=max(101, price), low=min(99, price))
    if missing:
        history.pop(1)
    refs = []
    for bars in (history, today):
        refs.append(record_bars(journal, strategy, csv_bars(bars), convention=CONVENTION,
                                retrieved_at=at + timedelta(seconds=2), source='isolated-forward-test',
                                digest=DIGEST, provenance='RECORDED' if recorded else 'SYNTHETIC'))
    return refs


def run(journal, refs=(), *, strategy='noise_area', at=AT, seconds=3):
    return run_tick(journal, strategy, list(refs), tick=at,
                    now=at + timedelta(seconds=seconds), digest=DIGEST)


@pytest.mark.parametrize('variant,side,quantity', [('long', 1, 181), ('flat', 0, 0), ('short', 0, 0)])
def test_real_signal_engine_decisions_never_make_orders_or_fills(journal, variant, side, quantity):
    before_other = journal.snapshot('orb')
    result = run(journal, inputs(journal, variant=variant))
    assert result['mode'] == MODE
    assert result['signal']['target_side'] == side
    assert result['signal']['metadata']['actionable'] is True
    assert result['risk']['candidate_target_qty'] == quantity
    assert result['risk']['approved_qty'] == 0
    assert not result['risk']['execution_allowed'] and not result['execution_allowed']
    assert 'corporate_actions_unverified' in result['risk']['execution_blockers']
    assert not result['provider_origin_verified']
    assert set(result['input_provenance'].values()) == {'SYNTHETIC'}
    assert result['mark_reference']
    assert result['runtime']['session'] == AT.date().isoformat()
    assert journal.snapshot('orb') == before_other
    assert journal.snapshot('noise_area')['nav'] == 50000
    assert journal.snapshot('noise_area')['positions'] == []
    assert not journal.db.execute('SELECT 1 FROM orders').fetchone()
    assert not any(e['kind'] == 'FILL' for e in journal.events())
    verify_journal(journal, DIGEST)


def test_recorded_input_is_not_provider_verified(journal):
    result = run(journal, inputs(journal, recorded=True))
    assert set(result['input_provenance'].values()) == {'RECORDED'}
    assert not result['provider_origin_verified'] and not result['execution_allowed']


def test_restart_retry_returns_same_decision_without_marks_or_capital_reset(journal):
    refs = inputs(journal)
    first = run(journal, refs)
    events = journal.events()
    reopened = open_journal(Path(journal.path), DIGEST, create=True)
    try:
        assert run(reopened, reversed(refs), seconds=600) == first
        assert reopened.events() == events
        assert reopened.load_state('noise_area') == journal.load_state('noise_area')
        assert len([e for e in events if e['kind'] == 'INITIAL_CAPITAL']) == 2
    finally:
        reopened.close()


def test_conflicting_same_tick_and_earlier_tick_are_rejected_without_writes(journal):
    refs = inputs(journal)
    run(journal, refs)
    before = journal.events()
    with pytest.raises(ValueError, match='Conflicting'):
        run(journal, refs[:1])
    with pytest.raises(ValueError, match='monotonically'):
        run(journal, at=AT - timedelta(minutes=5))
    assert journal.events() == before


@pytest.mark.parametrize('strategy,reason', [
    ('orb', 'strict_orb_point_in_time_universe_and_metadata_unavailable'),
    ('noise_area', 'missing_market_observations'),
])
def test_missing_required_inputs_are_durable_rejections_not_fake_positions(journal, strategy, reason):
    result = run(journal, strategy=strategy)
    assert result['signal']['reason'] == reason
    assert result['mark_reference'] is None
    assert result['risk']['candidate_target_qty'] == 0
    assert not result['signal']['metadata']['actionable']
    assert len([e for e in journal.events(strategy) if e['kind'] == 'RISK_REJECTION']) == 1


def test_orb_cannot_invent_point_in_time_selection_from_bars(journal):
    result = run(journal, inputs(journal, strategy='orb'), strategy='orb')
    assert result['signal']['reason'].startswith('strict_orb')
    assert result['risk']['candidate_target_qty'] == 0
    assert not result['execution_allowed']
    verify_journal(journal, DIGEST)


def test_incomplete_prior_session_is_not_silently_skipped(journal):
    result = run(journal, inputs(journal, missing=True))
    assert result['signal']['reason'].startswith('invalid_data:')
    assert not result['signal']['metadata']['actionable']
    assert result['risk']['candidate_target_qty'] == 0


@pytest.mark.parametrize('at,seconds,reason', [
    (AT, 61, 'expired_tick_no_backfill'),
    (datetime(2026, 11, 26, 10, tzinfo=NY), 3, 'outside_regular_session'),
    (datetime(2026, 11, 27, 13, tzinfo=NY), 3, 'outside_regular_session'),
    (datetime(2026, 11, 27, 12, 55, tzinfo=NY), 3, 'preclose_flatten_required_no_execution'),
    (datetime(2026, 11, 17, 15, 55, tzinfo=NY), 3, 'preclose_flatten_required_no_execution'),
])
def test_expiry_holiday_and_early_close_do_not_backfill_or_trade(journal, at, seconds, reason):
    result = run(journal, at=at, seconds=seconds)
    assert result['signal']['reason'] == reason
    assert not result['execution_allowed'] and result['mark_reference'] is None


def test_non_decision_tick_is_not_an_actionable_flat_signal(journal):
    at = AT + timedelta(minutes=5)
    result = run(journal, inputs(journal, at=at), at=at)
    assert result['signal']['reason'] == 'not_decision_tick'
    assert not result['signal']['metadata']['actionable']


@pytest.mark.parametrize('terminal', [False, True])
def test_manual_and_terminal_lock_persist_through_decisions_and_restart(journal, terminal):
    RuntimeStore(journal, DIGEST).halt('noise_area', AT, terminal=terminal)
    refs = inputs(journal)
    result = run(journal, refs)
    assert 'persistent_risk_or_manual_lock' in result['risk']['rejections']
    assert result['risk']['candidate_target_qty'] == 0
    state = journal.load_state('noise_area')
    assert state['manual_lock'] is True
    assert state['runtime'] == ('TERMINATED' if terminal else 'RISK_HALTED')
    reopened = open_journal(Path(journal.path), DIGEST)
    try:
        assert reopened.load_state('noise_area') == state
        assert run(reopened, refs) == result
    finally:
        reopened.close()


@pytest.mark.parametrize('kind', ['MARK', 'SIGNAL', 'RISK_REJECTION', 'SHADOW_DECISION'])
def test_each_write_failure_rolls_back_decision_mark_and_runtime(journal, monkeypatch, kind):
    refs = inputs(journal)
    before, state = journal.events(), journal.load_state('noise_area')
    append = journal._append
    def fail(strategy, event_kind, *args):
        append(strategy, event_kind, *args)
        if event_kind == kind:
            raise RuntimeError('injected write failure')
    monkeypatch.setattr(journal, '_append', fail)
    with pytest.raises(RuntimeError, match='injected'):
        run(journal, refs)
    assert journal.events() == before
    assert journal.load_state('noise_area') == state
    assert not journal.db.in_transaction


@pytest.mark.parametrize('variant', ['wrong_strategy', 'unknown', 'duplicate', 'future', 'overlap'])
def test_bad_references_fail_without_any_decision_writes(journal, variant):
    refs = inputs(journal, strategy='orb' if variant == 'wrong_strategy' else 'noise_area')
    if variant == 'unknown':
        refs = ['f' * 64]
    elif variant == 'duplicate':
        refs += refs[:1]
    elif variant == 'overlap':
        raw = csv_bars(session_bars(AT.date(), 'US.SPY', through=AT, minutes=5))
        refs.append(record_bars(journal, 'noise_area', raw, convention=CONVENTION,
                                retrieved_at=AT, source='overlap-test', digest=DIGEST, provenance='SYNTHETIC'))
    before = journal.events()
    with pytest.raises(ValueError):
        run(journal, refs, seconds=1 if variant == 'future' else 3)
    assert journal.events() == before


def test_future_bar_cannot_be_filtered_out_to_create_apparently_causal_signal(journal):
    later = AT + timedelta(minutes=5)
    refs = inputs(journal, at=later)
    with pytest.raises(ValueError, match='Lookahead'):
        run(journal, refs, seconds=303)


@pytest.mark.parametrize('change', ['config', 'runtime', 'fill'])
def test_projection_or_version_drift_fails_closed(journal, change):
    if change == 'runtime':
        journal.save_state('noise_area', journal.load_state('noise_area') | {'high_water_nav': 51000})
    elif change == 'fill':
        journal.append('orb', 'FILL', {'symbol': 'US.AAPL', 'side': 1, 'qty': 1, 'price': 100,
                                      'fee': 0, 'source': 'SHADOW'}, DIGEST, 'forbidden-fill', AT.isoformat())
    before = journal.events()
    with pytest.raises(ValueError):
        run_tick(journal, 'noise_area', [], tick=AT, now=AT, digest='changed' if change == 'config' else DIGEST)
    assert journal.events() == before


def test_existing_unrelated_ledger_cannot_be_adopted_or_reinitialized(tmp_path):
    path = tmp_path / 'unrelated.sqlite3'
    ledger = Ledger(path)
    ledger.initialize(DIGEST)
    before = ledger.events()
    with pytest.raises(ValueError, match='dedicated'):
        open_journal(path, DIGEST, create=True)
    assert ledger.events() == before
    ledger.close()
    private = tmp_path / 'private' / 'ledger.sqlite3'
    with pytest.raises(ValueError, match='non-private'):
        open_journal(private, DIGEST, create=True)
    assert not private.exists()


@pytest.mark.parametrize('kind', ['SHADOW_JOURNAL', 'INITIAL_CAPITAL', 'RISK_STATE'])
def test_interrupted_initialization_leaves_destination_absent_and_retry_safe(tmp_path, monkeypatch, kind):
    path = tmp_path / 'retry.sqlite3'
    append = Ledger._append

    def fail(self, strategy, event_kind, *args):
        append(self, strategy, event_kind, *args)
        if strategy == 'noise_area' and event_kind == kind:
            raise RuntimeError('injected initialization failure')

    with monkeypatch.context() as patch:
        patch.setattr(Ledger, '_append', fail)
        with pytest.raises(RuntimeError, match='initialization failure'):
            open_journal(path, DIGEST, create=True)
    assert list(tmp_path.iterdir()) == []
    ledger = open_journal(path, DIGEST, create=True)
    try:
        verify_journal(ledger, DIGEST)
        assert len(ledger.events()) == 6
        assert ledger.snapshot('orb')['nav'] == ledger.snapshot('noise_area')['nav'] == 50000
    finally:
        ledger.close()


@pytest.mark.parametrize('collision', [False, True])
def test_publication_failure_never_replaces_another_file(tmp_path, monkeypatch, collision):
    path = tmp_path / 'publish.sqlite3'
    link = forward.os.link
    other = b'another process owns this destination'

    def fail(source, destination):
        if collision:
            Path(destination).write_bytes(other)
            return link(source, destination)
        raise OSError('hard links unavailable')

    monkeypatch.setattr(forward.os, 'link', fail)
    with pytest.raises(OSError):
        open_journal(path, DIGEST, create=True)
    assert list(tmp_path.iterdir()) == ([path] if collision else [])
    if collision:
        assert path.read_bytes() == other


def test_cli_requires_explicit_journal_and_never_imports_broker_execution(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(forward, 'forward_digest', lambda _: DIGEST)
    with pytest.raises(SystemExit):
        forward.main(['init'])
    path = tmp_path / 'cli-decisions.sqlite3'
    args = ['--journal', str(path)]
    forward.main(args + ['init'])
    forward.main(args + ['tick', '--strategy', 'orb', '--tick', AT.isoformat()])
    ledger = open_journal(path, DIGEST)
    try:
        assert len([e for e in ledger.events() if e['kind'] == 'SHADOW_DECISION']) == 1
        assert not ledger.db.execute('SELECT 1 FROM orders').fetchone()
    finally:
        ledger.close()
    assert 'SHADOW_DECISIONS_ONLY' in capsys.readouterr().out
    for name in forward.SOURCES:
        tree = ast.parse((Path(forward.__file__).parent / name).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or '').startswith(('dual_strategy.execution', 'dual_strategy.backtest'))


def test_shadow_sizing_respects_reduction_and_virtual_cash_only():
    signal = forward.Signal('noise_area', 'US.SPY', AT, 1, 'test',
                            metadata={'actionable': True, 'price': 100})
    state = RiskState('noise_area', forward.RuntimeState.SHADOW, high_water_nav=51000, reduced=True)
    value = forward._assessment(signal, state, {'nav': 50000, 'cash': 50000, 'reserved': 0}, False)
    assert value['candidate_target_qty'] == 100
    value = forward._assessment(signal, state, {'nav': 50000, 'cash': 100, 'reserved': 0}, False)
    assert value['candidate_target_qty'] == 0
    assert 'insufficient_virtual_cash' in value['rejections']
    assert not value['shared_broker_cash_checked']


@pytest.mark.parametrize('filename,change', [
    ('noise_area.yaml', ('notional_fraction: 0.40', 'notional_fraction: 0.80')),
    ('noise_area.yaml', ('shorts_enabled: false', 'shorts_enabled: true')),
    ('noise_area.yaml', ('lookback_sessions: 14', 'lookback_sessions: 10')),
    ('risk.yaml', ('paper_execution_enabled: false', 'paper_execution_enabled: true')),
    ('risk.yaml', ('daily_loss_fraction: 0.0125', 'daily_loss_fraction: 0.5')),
])
def test_cli_rejects_configurations_not_implemented_by_diagnostic_runner(tmp_path, filename, change):
    project = Path(forward.__file__).resolve().parents[2]
    (tmp_path / 'config').mkdir()
    for name in ('noise_area.yaml', 'risk.yaml', 'orb.yaml'):
        content = (project / 'config' / name).read_text()
        (tmp_path / 'config' / name).write_text(content.replace(*change) if name == filename else content)
    with pytest.raises(ValueError, match='Unsupported decision-only configuration'):
        forward.forward_digest(tmp_path)


def test_digest_cannot_describe_a_different_checkout(tmp_path):
    project = Path(forward.__file__).resolve().parents[2]
    (tmp_path / 'config').mkdir()
    for name in ('noise_area.yaml', 'risk.yaml', 'orb.yaml'):
        (tmp_path / 'config' / name).write_bytes((project / 'config' / name).read_bytes())
    with pytest.raises(ValueError, match='source root differs from the loaded'):
        forward.forward_digest(tmp_path)


def test_current_project_contract_has_a_complete_source_and_config_digest():
    project = Path(forward.__file__).resolve().parents[2]
    digest = forward.forward_digest(project)
    assert len(digest) == 64 and int(digest, 16) > 0


def test_cli_import_records_exact_csv_without_opening_trading_ledger(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(forward, 'forward_digest', lambda _: DIGEST)
    journal = tmp_path / 'cli-import.sqlite3'
    source = tmp_path / 'bars.csv'
    source.write_bytes(csv_bars(session_bars(AT.date(), 'US.SPY', through=AT, minutes=5)).encode('utf-8'))
    args = ['--journal', str(journal)]
    forward.main(args + ['init'])
    forward.main(args + ['import-bars', '--strategy', 'noise_area', '--file', str(source),
                         '--source', 'synthetic-cli-fixture', '--provenance', 'SYNTHETIC',
                         '--minutes', '5', '--price-scale', '1', '--turnover-scale', '1',
                         '--volume-scale', '1', '--timestamp-semantics', 'end', '--adjustment', 'synthetic'])
    ledger = open_journal(journal, DIGEST)
    try:
        observations = [e for e in ledger.events() if e['kind'] == 'MARKET_OBSERVATION']
        assert len(observations) == 1
        assert observations[0]['payload']['raw_csv'] == source.read_bytes().decode('utf-8')
        assert not ledger.db.execute('SELECT 1 FROM orders').fetchone()
    finally:
        ledger.close()
    assert '"provider_origin_verified": false' in capsys.readouterr().out


@pytest.mark.parametrize('command', ['pause', 'kill'])
def test_dashboard_control_path_preserves_journal_version_and_restart(journal, monkeypatch, command):
    from dual_strategy.cli import control, initialize
    monkeypatch.setattr(forward, 'forward_digest', lambda _: DIGEST)
    before = journal.events()
    with pytest.raises(ValueError, match='cannot be initialized as trading ledgers'):
        initialize(Path(journal.path).parent, Path(journal.path))
    assert journal.events() == before
    result = control(Path(journal.path).parent, Path(journal.path), command, 'noise_area')
    assert result['noise_area']['runtime'] == ('TERMINATED' if command == 'kill' else 'RISK_HALTED')
    assert not result['noise_area']['broker_flat_verified']
    verify_journal(journal, DIGEST)
    after = journal.events()
    control(Path(journal.path).parent, Path(journal.path), command, 'noise_area')
    assert journal.events() == after
    reopened = open_journal(Path(journal.path), DIGEST)
    reopened.close()
