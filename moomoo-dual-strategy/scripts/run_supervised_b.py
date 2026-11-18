"""Tool-driven supervised Portfolio B trading runner (deterministic execution).

The TOOL computes signals, applies deterministic risk gates, executes via the
guarded PaperExecutor + authenticated CliTransport, reconciles fills, and runs
the close-only sentinel backstop. The human supervises (launch/monitor/stop);
the agent does not manually place orders.

This is SUPERVISED practice trading under the user's documented authorization
(reports/supervised_trading_authorization.md): the historical-validation release
gate is relaxed by user decision for practice only. It is NOT a full autonomous
PAPER_ACTIVE transition of the main comparison ledger. Runs on an ISOLATED
supervised ledger; the main comparison ledger and competition account are never
 touched. Account guards, paper-only tools, deterministic NAV/exposure/daily-loss/
drawdown gates and the close-only sentinel remain enforced.

Default is dry-run (prints decisions, places no orders). Pass --execute to trade.
"""
import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from dual_strategy.cli import initialize, project_digest
from dual_strategy.data.calendar import NY, session_bounds
from dual_strategy.execution.audit import PAPER_AUDIT_TOOLS, audit_orders, record_observation
from dual_strategy.execution.broker_history import HISTORY_TOOL, reconcile_history_order
from dual_strategy.execution.moomoo_cli import CliTransport, default_cli_path
from dual_strategy.execution.moomoo_paper import PaperExecutor, PracticeAccount, Request
from dual_strategy.models import Quote
from dual_strategy.portfolio.ledger import Ledger
from dual_strategy.portfolio.runtime import RuntimeStore
from dual_strategy.risk.sentinel import ObservedQuote
from dual_strategy.risk.sentinel_service import SentinelService
from dual_strategy.risk.state_machine import RuntimeState
from dual_strategy.strategies.noise_area import noise_area_signal

sys.path.insert(0, str(Path(__file__).resolve().parent))
from supervised_b_signal import get_latest_signal  # noqa: E402

PROJECT = Path(__file__).resolve().parents[1]
STRATEGY, SYMBOL = 'noise_area', 'US.SPY'


def log(**fields):
    print(json.dumps(fields, default=str), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--execute', action='store_true', help='place orders (default dry-run)')
    parser.add_argument('--ticks', type=int, default=1, help='number of decision ticks to process')
    parser.add_argument('--minutes', type=float, default=0, help='run for N minutes (overrides --ticks)')
    parser.add_argument('--work-dir', default='private/supervised-b')
    args = parser.parse_args()
    work = PROJECT / args.work_dir
    work.mkdir(parents=True, exist_ok=True)
    ledger_path = work / 'supervised.sqlite3'
    fresh = not ledger_path.exists()
    if fresh:
        initialize(PROJECT, ledger_path)
    mapping = json.loads((PROJECT / 'private' / 'accounts.json').read_text(encoding='utf-8'))
    aid = mapping['candidate']['account_id']
    account_key = hashlib.sha256(aid.encode()).hexdigest()
    digest = project_digest(PROJECT)
    ledger = Ledger(ledger_path)
    transport = CliTransport(frozenset({aid}), frozenset(mapping['denied_account_ids']))
    runtime = RuntimeStore(ledger, digest)

    def observe(tool, arguments):
        data = transport(tool, arguments)
        observed = datetime.now(timezone.utc)
        if tool == HISTORY_TOOL:
            for row in data.get('data', {}).get('orders', []):
                if row.get('update_time'):
                    bt = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=int(row['update_time']))
                    if bt > observed:
                        observed = bt
        obs = None
        if tool in PAPER_AUDIT_TOOLS:
            with ledger.transaction():
                obs = record_observation(ledger, account_key, tool, data, observed)
        return data, obs

    def fresh_account():
        data, _ = observe('mcp__moomoo__sim_trade_account_list', {})
        rows = [r for r in data['data']['accounts'] if r['account_id'] == aid]
        if len(rows) != 1:
            raise RuntimeError('practice account not uniquely listed')
        r = rows[0]
        return PracticeAccount(aid, r['account_title'], r['account_type'], r['market_id'],
                               'SIMULATE', bool(mapping['candidate']['authorized_for_orders']),
                               datetime.now(timezone.utc))

    def risk_check(account, request, now):
        if request.symbol != SYMBOL or request.qty <= 0:
            return False
        snap = ledger.snapshot(STRATEGY)
        notional = request.qty * request.limit_price
        if notional > 0.40 * snap['nav'] + 0.01:
            return False
        if snap['gross_exposure'] + notional > snap['nav'] + 0.01:
            return False
        risk = runtime.state(STRATEGY).mark(snap['nav'], now.astimezone(NY).date())
        if risk.daily_locked or risk.runtime in (RuntimeState.RISK_HALTED, RuntimeState.TERMINATED):
            return False
        return True

    executor = PaperExecutor(
        ledger, transport, frozenset({aid}), frozenset(mapping['denied_account_ids']),
        # Supervised practice authorization (user decision); historical gate relaxed for practice only.
        release_check=lambda strategy, d, now: strategy == STRATEGY and d == digest,
        risk_check=risk_check, digest=digest)

    def reconcile_leg(intent, request_at):
        begin = int((request_at - timedelta(minutes=2)).timestamp() * 1e6)
        for attempt in range(15):
            time.sleep(2)
            end = int((datetime.now(timezone.utc) + timedelta(minutes=2)).timestamp() * 1e6)
            _, obs = observe(HISTORY_TOOL, {'acc_id': aid, 'market': 100, 'time_begin': begin, 'time_end': end})
            reconcile_history_order(ledger, intent, obs)
            status = ledger.db.execute('SELECT status FROM orders WHERE intent_id=?', (intent,)).fetchone()['status']
            if status in ('FILLED', 'CANCELLED', 'REJECTED'):
                return status
            if status in ('WORKING', 'PARTIAL') and attempt == 10:
                executor.cancel(fresh_account(), intent)
        return status

    processed = set()
    cli = default_cli_path()

    def quote_feed(symbol):
        import subprocess
        r = subprocess.run([cli, 'quote', symbol, '--format', 'json'], capture_output=True, text=True, timeout=30)
        row = json.loads(r.stdout)[0]
        last = float(row['last_price'])
        return Quote(symbol, datetime.now(timezone.utc), last, last + 0.01)

    sentinel = SentinelService(ledger, transport, STRATEGY, aid, digest=digest,
                               quote_feed=quote_feed, state_dir=work, interval=1.0)
    try:
        start = time.monotonic()
        iteration = 0
        while True:
            sig = get_latest_signal()
            if 'error' in sig:
                log(decision='no_signal', **sig)
                break
            at = sig['at']
            snap = ledger.snapshot(STRATEGY)
            position = next((p['qty'] for p in snap['positions'] if p['symbol'] == SYMBOL), 0)
            side = sig['target_side']
            price = sig['metrics']['price']
            action = 'hold'
            if at not in processed:
                processed.add(at)
                if side == 1 and position == 0:
                    qty = int(Decimal('0.40') * Decimal(str(snap['nav'])) / Decimal(str(price)))
                    if qty > 0 and args.execute:
                        cash, _ = observe('mcp__moomoo__sim_trade_cash_info', {'acc_id': aid, 'market': 100})
                        req = Request(STRATEGY, SYMBOL, f'supervised-b-{at}', datetime.now(timezone.utc),
                                      1, qty, float(price) + 1.0, 'ETF')
                        intent = executor.submit(fresh_account(), req, float(Decimal(cash['data']['balance'])))
                        status = reconcile_leg(intent, req.at)
                        action = f'buy_{qty}_{status}'
                    else:
                        action = f'would_buy_{qty}' if qty > 0 else 'qty_zero'
                elif side == 0 and position > 0:
                    if args.execute:
                        req = Request(STRATEGY, SYMBOL, f'supervised-b-close-{at}', datetime.now(timezone.utc),
                                      -1, position, float(price) - 1.0, 'ETF', close_only=True)
                        intent = executor.submit(fresh_account(), req, 0.0)
                        status = reconcile_leg(intent, req.at)
                        action = f'close_{position}_{status}'
                    else:
                        action = f'would_close_{position}'
            log(at=at, target_side=side, reason=sig['reason'], position=position,
                nav=snap['nav'], action=action, execute=args.execute,
                audit=audit_orders(ledger))
            now = datetime.now(timezone.utc)
            q = quote_feed(SYMBOL)
            risk = runtime.state(STRATEGY).mark(ledger.snapshot(STRATEGY)['nav'], now.astimezone(NY).date())
            sres = sentinel.tick(now=now, risk=risk,
                                 quotes={SYMBOL: ObservedQuote(q, time.monotonic())},
                                 feed_at=now, executor_heartbeat_at=now)
            log(sentinel={'entries_blocked': sres.entries_blocked, 'reasons': list(sres.reasons),
                          'closes': len(sres.requests), 'heartbeat': sres.heartbeat_written})
            iteration += 1
            if args.minutes > 0:
                if time.monotonic() - start >= args.minutes * 60:
                    break
                time.sleep(30)
            else:
                if iteration >= args.ticks:
                    break
                if args.ticks > 1:
                    time.sleep(30)
        log(final={'nav': ledger.snapshot(STRATEGY)['nav'], 'audit': audit_orders(ledger),
                   'chain_verified': ledger.verify_chain(), 'mode': 'SUPERVISED_PRACTICE',
                   'execute': args.execute})
    finally:
        ledger.close()


if __name__ == '__main__':
    main()