import importlib.util
import json
import os
import plistlib
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts' / 'schedule_competition.py'


@pytest.fixture
def scheduler(monkeypatch):
    assert SCRIPT.exists(), 'Missing deterministic competition scheduler'
    spec = importlib.util.spec_from_file_location('competition_scheduler', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'sys', SimpleNamespace(platform='darwin'))
    return module


@pytest.fixture
def project(tmp_path, scheduler, monkeypatch):
    (tmp_path / 'config').mkdir()
    cfg = yaml.safe_load((ROOT / 'config/competition.yaml').read_text())
    cfg['execution_enabled'] = True
    (tmp_path / 'config/competition.yaml').write_text(yaml.safe_dump(cfg))
    live = tmp_path / 'private/competition/live'
    live.mkdir(parents=True)
    state = {'version': 1, 'initial_nav': 100000, 'high_water': 100020, 'last_nav': 100010,
             'last_mark_at': '2026-11-20T20:59:30+00:00', 'day': '2026-11-20',
             'halted': None, 'locked_day': None, 'orders_today': 2, 'order_errors_today': 0,
             'sleeves': {'b3': {}, 'h_etf': {'US.XLV': 11}}, 'h_plan': {'asof': '2026-11-20'}}
    (live / 'state.json').write_text(json.dumps(state))
    (live.parent / 'account.json').write_text(json.dumps({'account_id': 'TEST-ONLY-ACCOUNT', 'account_type': 2,
        'market_id': 100, 'confirmed_competition_account': True}))
    status = {'mode': 'EXECUTE', 'execution_enabled': True, 'nav': 100010,
              'broker_positions': {'US.XLV': 11}, 'attribution_errors': [],
              'initial_nav': 100000, 'high_water': 100020, 'halted': None,
              'locked_day': None, 'sleeves': state['sleeves']}
    (tmp_path / 'fixture-status.json').write_text(json.dumps(status))
    (live / 'outbox').mkdir()
    rows = [
        {'event': 'intent', 'mode': 'EXECUTE', 'intent_id': 'one', 'audit_id': 'audit', 'symbol': 'US.XLV', 'qty': 11},
        {'event': 'acknowledged', 'intent_id': 'one', 'audit_id': 'audit', 'order_id': 'TEST-ORDER'},
        {'event': 'outcome', 'intent_id': 'one', 'audit_id': 'audit', 'order_id': 'TEST-ORDER',
         'status': 'FILLED', 'qty': 11, 'filled': 11, 'avg_price': 178.95},
    ]
    (live / 'outbox/2026-11-20.jsonl').write_text('\n'.join(map(json.dumps, rows)))
    monkeypatch.setattr(scheduler, 'broker_history', lambda root, session: [
        {'order_id': 'TEST-ORDER', 'status': 4, 'qty': '11', 'cum_qty': '11', 'avg_fill_price': '178.95'}], raising=False)
    (tmp_path / 'scripts').mkdir()
    (tmp_path / 'scripts/run_competition.py').write_text('''import json, sys
from pathlib import Path
root=Path(__file__).resolve().parents[1]
with (root/'calls.jsonl').open('a') as log: log.write(json.dumps(sys.argv[1:])+'\\n')
if sys.argv[1:] == ['status','--execute']:
    print((root/'fixture-status.json').read_text())
elif sys.argv[1:] == ['loop','--execute']:
    (root/'loop-ran').write_text('started')
else: raise SystemExit(2)
''')
    monkeypatch.setattr(scheduler, 'clock', lambda: datetime.fromisoformat('2026-11-23T09:00:00-05:00'))
    monkeypatch.setattr(scheduler, 'runner_processes', lambda: [])
    monkeypatch.setattr(scheduler, 'python_path', lambda root: Path(sys.executable))
    return tmp_path


@pytest.mark.parametrize('at,want', [
    ('2026-11-20T09:00:00-05:00', False), ('2026-11-21T09:00:00-05:00', False),
    ('2026-11-23T08:59:59-05:00', False), ('2026-11-23T09:00:00-05:00', True),
    ('2026-11-23T14:24:59+00:00', True), ('2026-11-23T09:25:00-05:00', False),
    ('2026-11-23T09:30:00-05:00', False), ('2026-11-24T09:00:00-05:00', True),
    ('2026-11-25T09:00:00-05:00', True), ('2026-11-26T09:00:00-05:00', False),
    ('2026-11-27T09:00:00-05:00', True), ('2026-11-30T09:00:00-05:00', False),
    ('2027-11-23T09:00:00-05:00', False)])
def test_scheduler_only_starts_authorized_sessions_before_open(scheduler, at, want):
    assert scheduler.start_window(datetime.fromisoformat(at)) is want


def test_inactive_tick_has_no_files_or_broker_calls(scheduler, tmp_path, monkeypatch):
    monkeypatch.setattr(scheduler, 'clock', lambda: datetime.fromisoformat('2026-11-20T10:00:00-05:00'))
    assert scheduler.tick(tmp_path)['status'] == 'outside_start_window'
    assert list(tmp_path.iterdir()) == []


def test_preflight_ignores_appledouble_but_blocks_corrupt_outbox(scheduler, project):
    outbox = project / 'private/competition/live/outbox'
    (outbox / '._2026-11-20.jsonl').write_bytes(b'\x00\x05\x16\x07\xffAppleDouble')
    assert scheduler.local_preflight(project, '2026-11-23')['sleeves']['h_etf'] == {'US.XLV': 11}
    (outbox / '2026-11-19.jsonl').write_bytes(b'\xff')
    with pytest.raises(UnicodeDecodeError):
        scheduler.local_preflight(project, '2026-11-23')


def test_reviewed_cancellation_preserves_the_raw_broker_receipt(scheduler, project, monkeypatch):
    from dual_strategy.competition.store import Store

    store = Store(project, {'state_dir': 'private/competition/live'})
    session = '2026-11-19'
    link = {'audit_id': 'original', 'intent_id': 'partial', 'order_id': 'TEST-PARTIAL'}
    terminal = {'status': 'CANCELLED', 'qty': 10, 'filled': 0, 'avg_price': 652.247}
    store.append(session, {'event': 'intent', **link, 'mode': 'EXECUTE', 'qty': 30})
    store.append(session, {'event': 'acknowledged', **link})
    store.append(session, {'event': 'order_error', **link})
    error = store.outbox(session)[-1]
    store.append(session, {'event': 'outcome', **link, 'status': 'CANCELLED', 'qty': 30, 'filled': 20,
                           'avg_price': 652.247, 'broker_terminal': terminal})
    with pytest.raises(scheduler.Blocked, match='prior_execution_error'):
        scheduler.execution_outcomes(project)
    audit_id = store.audit(session, {'kind': 'broker_reconciliation',
        'reviewed_errors': [scheduler.execution_error_key(error)], 'broker_cancellations': {'TEST-PARTIAL': terminal}})
    store.append(session, {'event': 'broker_reconciliation', 'audit_id': audit_id})
    result = scheduler.execution_outcomes(project)
    assert result['TEST-PARTIAL']['filled'] == 20 and result['TEST-PARTIAL']['broker_terminal'] == terminal

    first, = scheduler.broker_history(project, '2026-11-23')
    broker_row = {'order_id': 'TEST-PARTIAL', 'status': 5, 'qty': '10', 'cum_qty': '0', 'avg_fill_price': '0'}
    monkeypatch.setattr(scheduler, 'broker_history', lambda *_: [first, broker_row])
    assert scheduler.preflight(project, store.load(), '2026-11-23')['reconciled_orders'] == 2
    broker_row['qty'] = '9'
    with pytest.raises(scheduler.Blocked, match='broker_order_outcome_mismatch'):
        scheduler.preflight(project, store.load(), '2026-11-23')


@pytest.mark.parametrize('count', [1, 2])
def test_existing_loop_prevents_preflight_and_duplicate_launch(scheduler, project, monkeypatch, count):
    monkeypatch.setattr(scheduler, 'runner_processes', lambda: [{'pid': 100+i, 'state': 'S'} for i in range(count)])
    result = scheduler.tick(project)
    assert result['status'] == ('already_running' if count == 1 else 'blocked')
    assert not (project / 'calls.jsonl').exists()
    assert not (project / 'loop-ran').exists()


def test_process_probe_failure_never_means_no_runner(scheduler, project, monkeypatch):
    def failed():
        raise OSError('probe failed with secret details')
    monkeypatch.setattr(scheduler, 'runner_processes', failed)
    result = scheduler.tick(project)
    assert result['status'] == 'blocked'
    assert 'secret' not in json.dumps(result)
    assert not (project / 'calls.jsonl').exists()


@pytest.mark.parametrize('change', ['halted', 'missing_state', 'order_errors', 'missing_capital', 'rejected', 'unconfirmed', 'working', 'malformed_outbox', 'wrong_account_type'])
def test_local_safety_failures_block_before_authentication(scheduler, project, change):
    live = project / 'private/competition/live'
    state_path = live / 'state.json'
    state = json.loads(state_path.read_text())
    if change == 'missing_state': state_path.unlink()
    elif change == 'halted': state['halted'] = {'kind': 'order_error'}; state_path.write_text(json.dumps(state))
    elif change == 'order_errors': state['order_errors_today'] = 1; state_path.write_text(json.dumps(state))
    elif change == 'missing_capital': state['high_water'] = None; state_path.write_text(json.dumps(state))
    elif change == 'wrong_account_type':
        account_path = live.parent / 'account.json'
        account = json.loads(account_path.read_text()); account['account_type'] = 1
        account_path.write_text(json.dumps(account))
    else:
        out = live / 'outbox'; out.mkdir(exist_ok=True)
        rows = [{'event':'intent','mode':'EXECUTE','intent_id':'one','audit_id':'audit'}]
        if change == 'rejected': rows += [{'event':'outcome','intent_id':'one','audit_id':'audit','status':'REJECTED'}]
        if change == 'working': rows += [{'event':'acknowledged','intent_id':'one','audit_id':'audit','order_id':'private'}]
        (out / '2026-11-20.jsonl').write_text('{' if change == 'malformed_outbox' else '\n'.join(map(json.dumps, rows)))
    assert scheduler.tick(project)['status'] == 'blocked'
    assert not (project / 'calls.jsonl').exists()


@pytest.mark.parametrize('field,value', [('mode','PLAN'), ('execution_enabled',False), ('nav',None), ('nav',float('nan')),
    ('nav',0), ('halted',{'kind':'drawdown'}), ('locked_day','2026-11-23'), ('attribution_errors',['mismatch']),
    ('broker_positions',{'US.XLV':12}), ('high_water',99999), ('sleeves',{'b3':{},'h_etf':{}})])
def test_preflight_rejects_invalid_or_mismatched_broker_status(scheduler, project, field, value):
    path = project / 'fixture-status.json'
    status = json.loads(path.read_text()); status[field] = value; path.write_text(json.dumps(status))
    result = scheduler.tick(project)
    assert result['status'] == 'blocked'
    assert not (project / 'loop-ran').exists()
    calls = [json.loads(line) for line in (project / 'calls.jsonl').read_text().splitlines()]
    assert calls == [['status','--execute'], ['status','--execute']]
    assert scheduler.tick(project)['status'] == 'attempt_already_recorded'
    assert len((project / 'calls.jsonl').read_text().splitlines()) == 2


def test_late_preflight_cannot_start_after_window(scheduler, project, monkeypatch):
    real = scheduler.preflight
    def late(root, state, session):
        result = real(root, state, session)
        monkeypatch.setattr(scheduler, 'clock', lambda: datetime.fromisoformat('2026-11-23T09:30:01-05:00'))
        return result
    monkeypatch.setattr(scheduler, 'preflight', late)
    assert scheduler.tick(project)['status'] == 'blocked'
    assert not (project / 'loop-ran').exists()


def test_same_day_evidence_prevents_replaying_open(scheduler, project):
    p=project/'private/competition/live/state.json'; state=json.loads(p.read_text())
    state['day']='2026-11-23'; p.write_text(json.dumps(state))
    assert scheduler.tick(project)['status']=='blocked'
    assert not (project/'calls.jsonl').exists()


def test_success_launches_once_detached_and_preserves_live_state(scheduler, project, monkeypatch):
    live = project / 'private/competition/live/state.json'; before=live.read_bytes()
    real_popen = subprocess.Popen
    launches=[]
    def spawn(argv, **kwargs):
        if argv[0] == '/usr/bin/caffeinate':
            assert argv[1:] == ['-i', sys.executable, str(project/'scripts/run_competition.py'), 'loop', '--execute']
            assert kwargs['start_new_session'] is True and kwargs['stdin'] == subprocess.DEVNULL
            assert kwargs['env']['PYTHONDONTWRITEBYTECODE']=='1'
            launches.append(argv)
            return real_popen(argv[2:], **kwargs)
        return real_popen(argv, **kwargs)
    monkeypatch.setattr(scheduler.subprocess, 'Popen', spawn)
    result=scheduler.tick(project)
    assert result['status']=='unexpected_exit'
    assert (project/'loop-ran').read_text()=='started'
    assert live.read_bytes()==before
    assert len(launches)==1
    assert scheduler.tick(project)['status']=='attempt_already_recorded'
    assert len(launches)==1
    assert 'TEST-ONLY-ACCOUNT' not in json.dumps(result)


def test_linux_scheduler_launches_the_reconciled_runner_directly(scheduler, project, monkeypatch):
    monkeypatch.setattr(scheduler, 'sys', SimpleNamespace(platform='linux'))
    assert scheduler.runner_command(project) == [sys.executable, str(project / 'scripts/run_competition.py'),
                                                'loop', '--execute']
    result = scheduler.tick(project)
    assert result['status'] == 'unexpected_exit'
    assert (project / 'loop-ran').read_text() == 'started'
    assert scheduler.tick(project)['status'] == 'attempt_already_recorded'


def test_concurrent_scheduler_lock_blocks_launch(scheduler, project):
    import fcntl
    path=project/'private/competition/scheduler'; path.mkdir()
    with (path/'scheduler.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
        assert scheduler.tick(project)['status']=='scheduler_busy'
    assert not (project/'calls.jsonl').exists()


def test_launchd_definition_uses_native_python_without_model(scheduler, project):
    spec=scheduler.launchd_spec(project)
    parsed=plistlib.loads(plistlib.dumps(spec))
    assert parsed['ProgramArguments']==[sys.executable,str(project/'scripts/schedule_competition.py'),'--run']
    assert parsed['StartInterval']==60 and parsed['RunAtLoad'] is True
    assert parsed.get('KeepAlive',False) is False
    assert parsed['WorkingDirectory']==str(project)
    assert parsed['EnvironmentVariables']['PYTHONPATH']==str(project/'src')
    assert parsed['EnvironmentVariables']['HOME']==str(Path.home())


def test_read_only_check_never_starts_runner_or_creates_state(scheduler, project):
    before={str(p.relative_to(project)):p.read_bytes() for p in project.rglob('*') if p.is_file()}
    result=scheduler.inspect(project)
    assert result['eligible_now'] is True and result['processes']==[]
    after={str(p.relative_to(project)):p.read_bytes() for p in project.rglob('*') if p.is_file()}
    assert after==before


@pytest.mark.parametrize('change', ['missing_ack', 'mismatched_ack', 'wrong_fill', 'missing_broker_order', 'unknown_broker_order', 'working_broker_order'])
def test_orders_must_reconcile_before_start(scheduler, project, monkeypatch, change):
    path = project / 'private/competition/live/outbox/2026-11-20.jsonl'
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    if change == 'missing_ack': rows.pop(1)
    if change == 'mismatched_ack': rows[1]['order_id'] = 'OTHER-ORDER'
    if change == 'wrong_fill': rows[2]['filled'] = 12
    path.write_text('\n'.join(map(json.dumps, rows)))
    if change == 'missing_broker_order':
        monkeypatch.setattr(scheduler, 'broker_history', lambda root, session: [])
    if change in ('unknown_broker_order', 'working_broker_order'):
        original = scheduler.broker_history
        def history(root, session):
            rows = original(root, session)
            if change == 'unknown_broker_order':
                rows += [{'order_id': 'UNOWNED', 'status': 4, 'qty': '1', 'cum_qty': '1', 'avg_fill_price': '10'}]
            else:
                rows[0]['status'] = 3
            return rows
        monkeypatch.setattr(scheduler, 'broker_history', history)
    assert scheduler.tick(project)['status'] == 'blocked'
    assert not (project / 'loop-ran').exists()


@pytest.mark.parametrize('prior_status', ['blocked', 'unexpected_exit', 'launched', 'preflight_started'])
def test_unresolved_scheduler_failure_blocks_future_sessions(scheduler, project, prior_status):
    directory = project / 'private/competition/scheduler'
    directory.mkdir()
    (directory / '2026-11-20.json').write_text(json.dumps({'status': prior_status}))
    assert scheduler.tick(project)['status'] == 'blocked'
    assert not (project / 'calls.jsonl').exists()


def test_one_transient_read_failure_gets_exactly_one_retry(scheduler, project, monkeypatch):
    real = scheduler.preflight
    real_popen = subprocess.Popen
    calls = []
    def transient(root, state, session):
        calls.append(session)
        if len(calls) == 1:
            raise subprocess.TimeoutExpired('status', 180)
        return real(root, state, session)
    def spawn(argv, **kwargs):
        return real_popen(argv[2:] if argv[0] == '/usr/bin/caffeinate' else argv, **kwargs)
    monkeypatch.setattr(scheduler, 'preflight', transient)
    monkeypatch.setattr(scheduler.subprocess, 'Popen', spawn)
    assert scheduler.tick(project)['status'] == 'unexpected_exit'
    assert calls == ['2026-11-23', '2026-11-23']
    assert (project / 'loop-ran').exists()


def test_changed_state_after_preflight_blocks_launch(scheduler, project, monkeypatch):
    real = scheduler.preflight
    def changed(root, state, session):
        result = real(root, state, session)
        p = root / 'private/competition/live/state.json'
        updated = json.loads(p.read_text()); updated['high_water'] += 1
        p.write_text(json.dumps(updated))
        return result
    monkeypatch.setattr(scheduler, 'preflight', changed)
    assert scheduler.tick(project)['status'] == 'blocked'
    assert not (project / 'loop-ran').exists()


def test_disabled_gate_blocks_without_broker_call(scheduler, project):
    path = project / 'config/competition.yaml'
    cfg = yaml.safe_load(path.read_text()); cfg['execution_enabled'] = False
    path.write_text(yaml.safe_dump(cfg))
    assert scheduler.tick(project)['status'] == 'blocked'
    assert not (project / 'calls.jsonl').exists()


def test_process_scan_ignores_caffeinate_and_other_commands(scheduler, monkeypatch):
    text = ('101 Ss /usr/bin/caffeinate -i /venv/python /project/scripts/run_competition.py loop --execute\n'
            '102 Ss /Library/Python /project/scripts/run_competition.py loop --execute\n'
            '103 S /bin/zsh -c unmatched quote "\n'
            '104 S /venv/python /project/scripts/run_competition.py status --execute\n')
    monkeypatch.setattr(scheduler.subprocess, 'run', lambda *a, **k: subprocess.CompletedProcess(a, 0, text, ''))
    assert scheduler.runner_processes() == [{'pid': 102, 'state': 'Ss'}]


def test_broker_history_uses_read_only_cli_with_bounded_window(scheduler, project, monkeypatch):
    from dual_strategy.execution.moomoo_cli import HISTORY
    calls = []
    def transport(allowed, denied):
        assert allowed == frozenset({'TEST-ONLY-ACCOUNT'}) and denied == frozenset()
        def call(tool, args):
            calls.append((tool, args))
            return {'ret_code': 0, 'data': {'orders': []}}
        return call
    import dual_strategy.execution.moomoo_cli as cli
    monkeypatch.setattr(cli, 'CliTransport', transport)
    spec = importlib.util.spec_from_file_location('history_scheduler', SCRIPT)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    assert callable(getattr(module, 'broker_history', None)), 'Broker history reconciliation is missing'
    assert module.broker_history(project, '2026-11-23') == []
    assert calls == [(HISTORY, {'acc_id': 'TEST-ONLY-ACCOUNT', 'market': 100,
                              'time_begin': 1795150800000000, 'time_end': 1795444200000000})]


@pytest.mark.parametrize('at', ['2026-11-23T16:20:00-05:00', '2026-11-27T13:20:00-05:00'])
def test_regular_and_half_day_runner_completion(scheduler, project, monkeypatch, at):
    end = datetime.fromisoformat(at)
    monkeypatch.setattr(scheduler, 'clock', lambda: end.replace(hour=9, minute=0))
    class Child:
        pid = os.getpid()
        def wait(self):
            monkeypatch.setattr(scheduler, 'clock', lambda: end)
            return 0
    real = scheduler.subprocess.Popen
    def spawn(argv, **kwargs):
        return Child() if argv[0] == '/usr/bin/caffeinate' else real(argv, **kwargs)
    monkeypatch.setattr(scheduler.subprocess, 'Popen', spawn)
    assert scheduler.tick(project)['status'] == 'completed'


def test_process_appearing_after_preflight_blocks_launch(scheduler, project, monkeypatch):
    real = scheduler.preflight
    def appeared(root, state, session):
        result = real(root, state, session)
        monkeypatch.setattr(scheduler, 'runner_processes', lambda: [{'pid': 123, 'state': 'S'}])
        return result
    monkeypatch.setattr(scheduler, 'preflight', appeared)
    assert scheduler.tick(project)['status'] == 'blocked'
    assert not (project / 'loop-ran').exists()
