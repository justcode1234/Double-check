import argparse
import hashlib
import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

import yaml

from dual_strategy.dashboard.server import create_server
from dual_strategy.execution.audit import audit_orders
from dual_strategy.portfolio.ledger import Ledger, STRATEGIES, config_hash
from dual_strategy.portfolio.runtime import RuntimeStore
from dual_strategy.risk.state_machine import RiskState, RuntimeState
from dual_strategy.reporting.service import daily_report, report_status


def project_digest(project: Path) -> str:
    configs = {name: yaml.safe_load((project / 'config' / f'{name}.yaml').read_text(encoding='utf-8'))
               for name in ('orb', 'noise_area', 'risk')}
    return config_hash(configs)


def initialize(project: Path, db: Path) -> dict:
    if not db.parent.exists():
        raise ValueError('Create the private database directory before initialization')
    if db.is_file():
        existing = Ledger(db, read_only=True)
        try:
            if existing.db.execute("SELECT 1 FROM events WHERE kind='SHADOW_JOURNAL' LIMIT 1").fetchone():
                raise ValueError('Decision-only journals cannot be initialized as trading ledgers')
        finally:
            existing.close()
    ledger = Ledger(db)
    try:
        digest = project_digest(project)
        ledger.initialize(digest)
        mapping_path = project / 'private' / 'accounts.json'
        mapping = json.loads(mapping_path.read_text(encoding='utf-8')) if mapping_path.exists() else {}
        shared = bool(mapping.get('candidate', {}).get('authorized_for_orders'))
        for strategy in STRATEGIES:
            if ledger.load_state(strategy) is None:
                state = RiskState(strategy, runtime=RuntimeState.SHADOW,
                                  reason='Release gates incomplete; no orders enabled')
                ledger.save_state(strategy, state.to_dict() | {'account_mode': 'shared' if shared else 'unconfigured'})
            if shared:
                account_key = hashlib.sha256(mapping['candidate']['account_id'].encode()).hexdigest()
                event_id = f'account-mapping:{strategy}:{account_key}'
                if not ledger.db.execute('SELECT 1 FROM events WHERE event_id=?', (event_id,)).fetchone():
                    ledger.append(strategy, 'ACCOUNT_MAPPING', {'account_key': account_key, 'mode': 'shared_practice',
                                                               'initial_virtual_nav': 50000, 'user_authorized': True}, digest, event_id)
        return {'initialized': True, 'allocations': {s: ledger.snapshot(s)['initial_nav'] for s in STRATEGIES},
                'execution_enabled': False, 'chain_verified': ledger.verify_chain()}
    finally:
        ledger.close()


def control(project: Path, db: Path, command: str, strategy: str) -> dict:
    if command not in ('pause', 'kill') or strategy not in (*STRATEGIES, 'all'):
        raise ValueError('Unsupported local control')
    if not db.is_file():
        raise OSError('Existing initialized ledger required')
    ledger = Ledger(db)
    try:
        targets = STRATEGIES if strategy == 'all' else (strategy,)
        status = {}
        if ledger.db.execute("SELECT 1 FROM events WHERE kind='SHADOW_JOURNAL' LIMIT 1").fetchone():
            from dual_strategy.forward import forward_digest, verify_journal
            digest = forward_digest(project)
            verify_journal(ledger, digest)
        else:
            digest = project_digest(project)
        store = RuntimeStore(ledger, digest)
        for target in targets:
            state = store.halt(target, datetime.now(timezone.utc), terminal=command == 'kill')
            snap = ledger.snapshot(target)
            working = ledger.db.execute("SELECT COUNT(*) FROM orders WHERE strategy=? AND status IN ('SUBMITTING','UNKNOWN','WORKING','PARTIAL','CANCEL_PENDING')", (target,)).fetchone()[0]
            status[target] = {'runtime': state.runtime.value, 'open_positions': len(snap['positions']), 'working_orders': working,
                              'flat_verified': not snap['positions'] and working == 0,
                              'broker_flat_verified': False,
                              'note': 'Local entries locked. Any broker exposure requires the close-only sentinel and broker reconciliation; this command does not claim liquidation.'}
        return status
    finally:
        ledger.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description='Audited two-portfolio paper research; no live orders')
    parser.add_argument('--project', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--db', type=Path)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('init', 'status', 'audit', 'report', 'backtest'):
        commands.add_parser(name)
    serve = commands.add_parser('serve')
    serve.add_argument('--port', type=int, default=8876)
    calendar = commands.add_parser('calendar')
    calendar.add_argument('--date', type=date.fromisoformat, required=True)
    for name in ('pause', 'kill'):
        child = commands.add_parser(name)
        child.add_argument('--strategy', choices=(*STRATEGIES, 'all'), default='all')
    args = parser.parse_args(argv)
    project = args.project.resolve()
    db = args.db.resolve() if args.db else project / 'private' / 'ledger.sqlite3'
    if args.command == 'init':
        result = initialize(project, db)
    elif args.command == 'serve':
        server = create_server(project, db, args.port)
        print(f'Local dashboard (Start blocked; Stop locks entries): http://127.0.0.1:{server.server_port}', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return
    elif args.command == 'status':
        result = report_status(db, project)
    elif args.command == 'audit':
        ledger = Ledger(db, read_only=True)
        try:
            result = audit_orders(ledger)
        finally:
            ledger.close()
    elif args.command == 'report':
        result = {'report': str(daily_report(db, project))}
    elif args.command in ('pause', 'kill'):
        result = control(project, db, args.command, args.strategy)
    elif args.command == 'calendar':
        from dual_strategy.data.calendar import decision_times, flatten_time, session_bounds
        bounds = session_bounds(args.date)
        result = {'session': str(args.date), 'open': bounds[0].isoformat() if bounds else None,
                  'close': bounds[1].isoformat() if bounds else None,
                  'flatten': flatten_time(args.date).isoformat() if bounds else None,
                  'decisions': [v.isoformat() for v in decision_times(args.date)]}
    elif args.command == 'backtest':
        from dual_strategy.backtest.runner import run_comparison
        result = run_comparison(project)
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
