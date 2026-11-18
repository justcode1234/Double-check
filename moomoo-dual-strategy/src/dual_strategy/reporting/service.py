import csv
import io
import json
from datetime import datetime, timezone
from decimal import InvalidOperation
from pathlib import Path

from dual_strategy.data.evidence import MarketEvidenceError, observation_provenance, resolve_bars
from dual_strategy.execution.audit import audit_orders
from dual_strategy.models import aware
from dual_strategy.portfolio.ledger import Ledger, STRATEGIES, amount
from dual_strategy.reporting.metrics import performance

SHADOW_LABEL = 'SHADOW / HYPOTHETICAL FILLS'
START_BLOCKERS = [
    'Paper activation is not connected to this dashboard.',
    'Independently authenticated broker transport and continuous close-only supervision are unavailable.',
    'Real practice-order lifecycle and complete release evidence are not verified.',
]


def strategy_controls(strategy: str, state: dict) -> dict:
    blockers = list(START_BLOCKERS)
    if strategy == 'orb':
        blockers.append('ORB point-in-time historical validation and observed adverse stop latency remain unverified.')
    locked = state.get('manual_lock') is True
    terminal = state.get('runtime') == 'TERMINATED'
    if locked or terminal:
        blockers.append('Terminal stop cannot be cleared.' if terminal else 'Manual entry lock persists; explicit reviewed rearm is required.')
    return {'start_enabled': False, 'start_blockers': blockers,
            'stop_enabled': 'strategy' in state and not locked and not terminal,
            'manual_locked': locked, 'broker_flat_verified': False,
            'stop_note': 'Stop locks local entries only. It does not send broker cancels or closes, stop the server, or reset capital. No broker-connected supervisor is running.'}


def redacted(value):
    if isinstance(value, dict):
        return {k: redacted(v) for k, v in value.items() if k.lower() != 'raw_csv' and not any(s in k.lower() for s in ('account_id', 'account_key', 'acc_id', 'token', 'secret', 'authorization', 'broker_order_id'))}
    if isinstance(value, list):
        return [redacted(v) for v in value]
    return value


def _feed_status(ledger: Ledger, strategy: str, mark: dict | None, now: datetime) -> tuple[str, float | None]:
    if mark is None:
        return 'NOT_CONNECTED', None
    try:
        at = aware(datetime.fromisoformat(mark['at']))
    except (TypeError, ValueError):
        return 'UNVERIFIED', None
    age = (now - at).total_seconds()
    payload = mark['payload']
    prices = payload.get('prices')
    if (age < 0 or not isinstance(prices, dict) or not prices
            or payload.get('provider_origin_verified') is not False):
        return 'UNVERIFIED', age
    try:
        evidence = resolve_bars(ledger, strategy, payload.get('observation_hash'),
                                asof=at, digest=mark['config_hash'])
        provenance = observation_provenance(ledger, strategy, payload.get('observation_hash'),
                                            asof=at, digest=mark['config_hash'])
    except MarketEvidenceError:
        return 'UNVERIFIED', age
    if payload.get('market_provenance') != provenance:
        return 'UNVERIFIED', age
    latest = {bar.symbol: bar for bar in evidence.bars}
    stale = False
    for symbol, price in prices.items():
        bar = latest.get(symbol)
        if bar is None or bar.end > at or bar.end > now or isinstance(price, bool):
            return 'UNVERIFIED', age
        try:
            if amount(price) <= 0 or amount(price) != amount(bar.close):
                return 'UNVERIFIED', age
        except (InvalidOperation, ValueError):
            return 'UNVERIFIED', age
        stale |= (at - bar.end).total_seconds() > 90 or (now - bar.end).total_seconds() > 90
    if stale:
        return 'STALE', age
    return ('SYNTHETIC' if provenance == 'SYNTHETIC' else 'RECORDED_UNVERIFIED'), age


def _decision_only_marker(events: list[dict], chain_verified: bool) -> bool:
    markers = [e for e in events if e['kind'] == 'SHADOW_JOURNAL']
    if not chain_verified or len(markers) != 1:
        return False
    marker = markers[0]
    return (marker['payload'] == {'schema_version': 1, 'mode': 'SHADOW_DECISIONS_ONLY'}
            and type(marker['payload']['schema_version']) is int
            and marker['event_id'] == f"shadow-journal:{marker['strategy']}"
            and bool(marker['config_hash'].strip())
            and all(e['config_hash'] == marker['config_hash'] for e in events))


def report_status(db_path: Path, project: Path, now: datetime | None = None) -> dict:
    now = aware(now or datetime.now(timezone.utc))
    capabilities_path = project / 'evidence' / 'capabilities.json'
    capabilities = json.loads(capabilities_path.read_text(encoding='utf-8')) if capabilities_path.exists() else {}
    ledger = Ledger(db_path, read_only=True)
    try:
        ledger.db.execute('BEGIN')
        events = ledger.events()
        chain_verified = ledger.verify_chain()
        audit = audit_orders(ledger)
        portfolios = []
        for strategy in STRATEGIES:
            snap = ledger.snapshot(strategy)
            state = ledger.load_state(strategy) or {'runtime': 'SHADOW'}
            own = [e for e in events if e['strategy'] == strategy]
            orders = list(ledger.db.execute('SELECT qty,cumulative_qty,status FROM orders WHERE strategy=?', (strategy,)))
            requested = sum(row['qty'] for row in orders)
            filled = sum(row['cumulative_qty'] for row in orders)
            heartbeats = [e for e in own if e['kind'] == 'SENTINEL_HEARTBEAT']
            heartbeat = heartbeats[-1] if heartbeats else None
            age = (now - datetime.fromisoformat(heartbeat['at'])).total_seconds() if heartbeat else None
            recent = age is not None and 0 <= age <= 5
            sentinel = ('HEALTHY' if heartbeat['payload'].get('healthy') is True else 'NOT_HEALTHY') if recent else ('STALE' if heartbeat else 'NOT_RUNNING')
            marks = [e for e in own if e['kind'] == 'MARK']
            feed_status, mark_age = _feed_status(ledger, strategy, marks[-1] if marks else None, now)
            day_start = state.get('day_start_nav', snap['initial_nav'])
            if day_start is None:
                day_start = snap['initial_nav']
            daily_pnl = snap['nav'] - float(day_start)
            label = SHADOW_LABEL
            if (not orders and not snap['paper_fill_count'] and not snap['shadow_fill_count']
                    and _decision_only_marker(own, chain_verified)):
                label = 'SHADOW / DECISIONS ONLY — NO FILLS'
            if snap['paper_fill_count'] and not snap['shadow_fill_count']:
                label = 'UNVERIFIED PAPER EVIDENCE — DO NOT USE RESULTS'
                if audit['broker_lifecycle']:
                    label = ('REAL MOOMOO PRACTICE PAPER FILLS' if audit['provider_origin_verified'] else
                             'BROKER-HISTORY-LINKED PAPER FILLS — PROVIDER ORIGIN UNVERIFIED')
            elif snap['paper_fill_count'] and snap['shadow_fill_count']:
                label = 'INVALID MIXED PROVENANCE — DO NOT USE RESULTS'
            if state.get('account_mode') == 'shared':
                label += ' | SHARED PRACTICE ACCOUNT — TWO VIRTUAL $50K SUB-LEDGERS'
            metrics = performance(snap['equity'], snap['trades'])
            portfolios.append(snap | metrics | {
                'label': label, 'runtime': state.get('runtime', 'SHADOW'),
                'daily_pnl': daily_pnl, 'daily_loss_remaining': max(0, .0125 * float(day_start) + daily_pnl),
                'order_count': len(orders), 'requested_shares': requested, 'filled_shares': filled,
                'fill_ratio_pct': 100 * filled / requested if requested else None,
                'risk_rejections': sum(e['kind'] == 'RISK_REJECTION' for e in own),
                'emergency_stops': sum(e['kind'] == 'EMERGENCY_EXIT' for e in own),
                'sentinel_status': sentinel, 'sentinel_age_seconds': age,
                'feed_status': feed_status, 'feed_provider_origin_verified': False,
                'feed_note': 'Market observation records do not authenticate provider origin or streaming connectivity.',
                'mark_age_seconds': mark_age,
                'last_event_at': own[-1]['at'] if own else None,
                'cloud_status': 'READ_ONLY_ROLES_DEPLOYED',
                'cloud_note': 'Read-only reporter/auditor/watchdog Cloud deployments exist (on-demand, no schedules, no Vault, no trading tools). Orchestration pipelines and paper execution are NOT deployed to Cloud.',
                'controls': strategy_controls(strategy, state),
            })
        research_path = project / 'reports' / 'backtest_comparison.json'
        research = json.loads(research_path.read_text(encoding='utf-8')) if research_path.exists() else None
        return redacted({'generated_at': now.isoformat(), 'execution_enabled': False,
                         'chain_verified': chain_verified, 'portfolios': portfolios,
                         'capabilities': capabilities, 'research': research, 'order_audit': audit,
                         'audit': events[-200:], 'event_count': len(events)})
    finally:
        ledger.close()


def csv_export(rows: list[dict]) -> str:
    if not rows:
        return ''
    output = io.StringIO(newline='')
    keys = list(dict.fromkeys(k for row in rows for k in row))
    writer = csv.DictWriter(output, fieldnames=keys)
    writer.writeheader()
    for row in rows:
        safe = {}
        for k, value in row.items():
            value = json.dumps(value, sort_keys=True) if isinstance(value, (dict, list)) else value
            if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@', '\t', '\r')):
                value = "'" + value
            safe[k] = value
        writer.writerow(safe)
    return output.getvalue()


def daily_report(db_path: Path, project: Path) -> Path:
    result = report_status(db_path, project)
    day = datetime.now(timezone.utc).date().isoformat()
    destination = project / 'reports' / f'daily-{day}.json'
    destination.write_text(json.dumps(result, indent=2, allow_nan=False), encoding='utf-8')
    for strategy in STRATEGIES:
        portfolio = next(p for p in result['portfolios'] if p['strategy'] == strategy)
        (project / 'reports' / f'{strategy}-equity.csv').write_text(csv_export(portfolio['equity']), encoding='utf-8')
    return destination
