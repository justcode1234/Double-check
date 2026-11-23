"""Bounded native startup for the authorized November 2026 competition sessions."""

from __future__ import annotations

import argparse
import fcntl
import json
import math
import os
import plistlib
import shlex
import subprocess
import sys
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

PROJECT = Path(__file__).resolve().parents[1]
NY = ZoneInfo('America/New_York')
SESSIONS = frozenset({'2026-11-23', '2026-11-24', '2026-11-25', '2026-11-27'})
LABEL = 'com.portbrain.moomoo-competition-start'


class Blocked(Exception):
    pass


def clock():
    return datetime.now(NY)


def start_window(at):
    at = at.astimezone(NY)
    return at.date().isoformat() in SESSIONS and time(9) <= at.time() < time(9, 25)


def python_path(root):
    return root / '.venv/bin/python'


def environment(root):
    home = Path.home()
    return {'HOME': str(home),
            'PATH': f'{home}/.moomoo/bin:{home}/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin',
            'PYTHONPATH': str(root / 'src'), 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUNBUFFERED': '1',
            'LANG': 'en_US.UTF-8' if sys.platform == 'darwin' else 'C.UTF-8'}


def runner_command(root):
    command = [str(python_path(root)), str(root / 'scripts/run_competition.py'), 'loop', '--execute']
    if sys.platform == 'darwin':
        return ['/usr/bin/caffeinate', '-i', *command]
    if sys.platform.startswith('linux'):
        return command
    raise Blocked('unsupported_scheduler_platform')


def runner_processes():
    result = subprocess.run(['/bin/ps', '-axo', 'pid=,stat=,command='], capture_output=True, text=True,
                            check=True, timeout=10)
    found = []
    local_prefix = f'{python_path(PROJECT)} {PROJECT / "scripts/run_competition.py"} '
    for line in result.stdout.splitlines():
        fields = line.strip().split(None, 2)
        if len(fields) != 3:
            continue
        pid, state, command = fields
        if command.startswith(local_prefix):
            args = command[len(local_prefix):].split()
            if 'loop' in args and '--execute' in args:
                found.append({'pid': int(pid), 'state': state})
            continue
        if not Path(command.split()[0]).name.lower().startswith('python'):
            continue
        args = shlex.split(command)
        if (args and Path(args[0]).name.lower().startswith('python')
                and any(Path(a).name == 'run_competition.py' for a in args[1:])
                and 'loop' in args and '--execute' in args):
            found.append({'pid': int(pid), 'state': state})
    return found


def positive(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def local_preflight(root, session):
    cfg = yaml.safe_load((root / 'config/competition.yaml').read_text())
    if (cfg.get('execution_enabled') is not True or cfg.get('state_dir') != 'private/competition'
            or cfg.get('account_file') != 'private/competition/account.json'):
        raise Blocked('execution_gate_or_storage_changed')
    account = json.loads((root / cfg['account_file']).read_text())
    if not (account.get('account_type') == 2 and account.get('market_id') == 100
            and account.get('confirmed_competition_account') is True
            and isinstance(account.get('account_id'), str) and account['account_id']):
        raise Blocked('competition_account_not_confirmed')
    live = root / cfg['state_dir'] / 'live'
    state = json.loads((live / 'state.json').read_text())
    if not all(positive(state[k]) for k in ('initial_nav', 'high_water', 'last_nav')):
        raise Blocked('persisted_capital_invalid')
    if state['halted'] is not None or state['locked_day'] == session or state.get('order_errors_today', 0):
        raise Blocked('persistent_safety_block')
    if any(state['sleeves'].get(s) for s in ('b3', 'noise', 'vwap', 'orb')):
        raise Blocked('prior_intraday_position_not_flat')     # B3, Noise-area, VWAP and ORB never hold overnight
    if (state['day'] >= session
            or datetime.fromisoformat(state['last_mark_at']).astimezone(NY).date().isoformat() >= session
            or (live / 'outbox' / f'{session}.jsonl').exists()):
        raise Blocked('session_already_touched')
    execution_outcomes(root)
    return state


def execution_outcomes(root):
    confirmed = {}
    for path in sorted((root / 'private/competition/live/outbox').glob('*.jsonl')):
        intents, acknowledgements, outcomes = {}, {}, {}
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            event = row['event']
            if event in ('order_error', 'order_refused', 'runner_error'):
                raise Blocked('prior_execution_error_requires_review')
            if event == 'intent' and row['mode'] == 'EXECUTE':
                key = (row['audit_id'], row['intent_id'])
                if key in intents:
                    raise Blocked('duplicate_execution_intent')
                intents[key] = row
            elif event == 'acknowledged':
                key = (row['audit_id'], row['intent_id'])
                if key in acknowledgements:
                    raise Blocked('duplicate_order_acknowledgement')
                acknowledgements[key] = str(row['order_id'])
            elif event == 'outcome':
                key = (row['audit_id'], row['intent_id'])
                if key in outcomes or row['status'] not in ('FILLED', 'CANCELLED'):
                    raise Blocked('unresolved_order_outcome')
                outcomes[key] = row
        if not intents.keys() == outcomes.keys() == acknowledgements.keys():
            raise Blocked('unresolved_execution_intent')
        for key, outcome in outcomes.items():
            oid = str(outcome['order_id'])
            qty, filled = outcome['qty'], outcome['filled']
            if (acknowledgements[key] != oid or oid in confirmed or qty != intents[key]['qty']
                    or type(filled) is not int or not 0 <= filled <= qty
                    or (outcome['status'] == 'FILLED' and filled != qty)
                    or (filled > 0 and not positive(outcome['avg_price']))):
                raise Blocked('inconsistent_order_evidence')
            confirmed[oid] = outcome
    return confirmed


def broker_history(root, session):
    from dual_strategy.execution.moomoo_cli import CliTransport, HISTORY

    account = json.loads((root / 'private/competition/account.json').read_text())['account_id']
    transport = CliTransport(frozenset({account}), frozenset())
    begin = datetime(2026, 11, 20, tzinfo=NY)
    end = datetime.fromisoformat(session).replace(tzinfo=NY, hour=9, minute=30)
    return transport(HISTORY, {'acc_id': account, 'market': 100,
                              'time_begin': int(begin.timestamp() * 1e6),
                              'time_end': int(end.timestamp() * 1e6)})['data']['orders']


def preflight(root, state, session):
    result = subprocess.run([str(python_path(root)), str(root / 'scripts/run_competition.py'), 'status', '--execute'],
                            cwd=root, env=environment(root), stdin=subprocess.DEVNULL, capture_output=True,
                            text=True, check=True, timeout=180)
    status = json.loads(result.stdout)
    if not (status['mode'] == 'EXECUTE' and status['execution_enabled'] is True and positive(status['nav'])
            and status['halted'] is None and status['locked_day'] != session and status['attribution_errors'] == []
            and status['initial_nav'] == state['initial_nav'] and status['high_water'] == state['high_water']
            and status['sleeves'] == state['sleeves']):
        raise Blocked('broker_status_failed_safety_checks')
    owned = {}
    for sleeve in state['sleeves'].values():
        for symbol, quantity in sleeve.items():
            if type(quantity) is not int or quantity < 0:
                raise Blocked('invalid_ownership')
            if quantity:
                owned[symbol] = owned.get(symbol, 0) + quantity
    if owned != {s: q for s, q in status['broker_positions'].items() if q}:
        raise Blocked('broker_ownership_mismatch')
    expected = execution_outcomes(root)
    history = broker_history(root, session)
    if len(history) != len(expected) or {str(row['order_id']) for row in history} != expected.keys():
        raise Blocked('broker_order_history_mismatch')
    for row in history:
        outcome = expected[str(row['order_id'])]
        if (row['status'] not in (4, 5, 7)
                or ('FILLED' if row['status'] == 4 else 'CANCELLED') != outcome['status']
                or float(row['qty']) != outcome['qty'] or float(row['cum_qty']) != outcome['filled']
                or (outcome['filled'] > 0 and not math.isclose(float(row['avg_fill_price']), outcome['avg_price'],
                                                             rel_tol=0, abs_tol=0.000001))):
            raise Blocked('broker_order_outcome_mismatch')
    return {'nav': status['nav'], 'reconciled_orders': len(history)}


def inspect(root):
    return {'checked_at': clock().isoformat(), 'eligible_now': start_window(clock()),
            'authorized_sessions': sorted(SESSIONS), 'processes': runner_processes()}


def write_result(path, result, exclusive=False):
    flags = os.O_WRONLY | os.O_CREAT | (os.O_EXCL if exclusive else os.O_TRUNC)
    target = path if exclusive else path.with_suffix('.tmp')
    with os.fdopen(os.open(target, flags, 0o600), 'w') as stream:
        json.dump(result, stream)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    if not exclusive:
        os.replace(target, path)


def tick(root):
    now = clock().astimezone(NY)
    if not start_window(now):
        return {'status': 'outside_start_window'}
    session = now.date().isoformat()
    directory = root / 'private/competition/scheduler'
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (directory / 'scheduler.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {'status': 'scheduler_busy'}
        try:
            running = runner_processes()
        except Exception:
            return {'status': 'blocked', 'reason': 'process_probe_failed'}
        if running:
            return {'status': 'already_running' if len(running) == 1 else 'blocked', 'processes': running}
        path = directory / f'{session}.json'
        result = {'status': 'preflight_started', 'session': session, 'at': now.isoformat()}
        try:
            write_result(path, result, exclusive=True)
        except FileExistsError:
            return {'status': 'attempt_already_recorded', 'session': session}
        try:
            for prior in directory.glob('????-??-??.json'):
                if prior.stem < session and json.loads(prior.read_text())['status'] != 'completed':
                    raise Blocked('prior_startup_requires_review')
            state = local_preflight(root, session)
            for attempt in range(2):
                try:
                    result['preflight'] = preflight(root, state, session)
                    result['preflight_attempts'] = attempt + 1
                    break
                except Exception:
                    if attempt == 1:
                        raise Blocked('read_only_preflight_failed_twice') from None
            if not start_window(clock()) or clock().astimezone(NY).date().isoformat() != session:
                raise Blocked('startup_window_elapsed')
            if runner_processes():
                raise Blocked('runner_appeared_during_preflight')
            if local_preflight(root, session) != state:
                raise Blocked('state_changed_during_preflight')
            logpath = root / 'private/competition' / f'runner-{session}.log'
            with os.fdopen(os.open(logpath, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600), 'a') as log:
                child = subprocess.Popen(runner_command(root),
                                         cwd=root, env=environment(root), stdin=subprocess.DEVNULL,
                                         stdout=log, stderr=subprocess.STDOUT, start_new_session=True, umask=0o077)
                result.update(status='launched', pid=child.pid, pgid=os.getpgid(child.pid), launched_at=clock().isoformat())
                write_result(path, result)
                code = child.wait()
            ended = clock().astimezone(NY)
            close_hour = 13 if session == '2026-11-27' else 16
            complete_after = now.replace(hour=close_hour, minute=20, second=0, microsecond=0)
            complete = code == 0 and complete_after <= ended < complete_after + timedelta(hours=2)
            result.update(status='completed' if complete else 'unexpected_exit', returncode=code, ended_at=ended.isoformat())
        except Blocked as exc:
            result.update(status='blocked', reason=str(exc))
        except Exception as exc:
            result.update(status='blocked', reason='scheduler_failure', error_type=type(exc).__name__)
        write_result(path, result)
        return result


def launchd_spec(root):
    directory = root / 'private/competition/scheduler'
    return {'Label': LABEL,
            'ProgramArguments': [str(python_path(root)), str(root / 'scripts/schedule_competition.py'), '--run'],
            'WorkingDirectory': str(root), 'EnvironmentVariables': environment(root),
            'RunAtLoad': True, 'StartInterval': 60, 'Umask': 0o077,
            'StandardOutPath': str(directory / 'launchd.log'), 'StandardErrorPath': str(directory / 'launchd-error.log')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--run', action='store_true')
    mode.add_argument('--check', action='store_true')
    mode.add_argument('--plist', action='store_true')
    args = parser.parse_args()
    if args.plist:
        print(plistlib.dumps(launchd_spec(PROJECT)).decode(), end='')
    else:
        try:
            result = tick(PROJECT) if args.run else inspect(PROJECT)
        except Exception as exc:
            result = {'status': 'blocked', 'reason': 'scheduler_failure', 'error_type': type(exc).__name__}
        print(json.dumps(result), flush=True)
        return 1 if result.get('status') in ('blocked', 'unexpected_exit') else 0
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
