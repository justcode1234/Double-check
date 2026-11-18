"""Supervised 1-share SPY paper round trip on an isolated validation ledger.

Every broker call is relayed by a human/agent operator through request/response
files; responses are AGENT_RELAYED, not independently authenticated transport.
The strategy comparison ledger is never opened.
"""
import hashlib
import itertools
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from dual_strategy.cli import initialize, project_digest
from dual_strategy.execution.audit import PAPER_AUDIT_TOOLS, audit_orders, record_observation
from dual_strategy.execution.broker_history import HISTORY_TOOL, reconcile_history_order
from dual_strategy.execution.moomoo_paper import PaperExecutor, PracticeAccount, Request
from dual_strategy.portfolio.ledger import Ledger

PROJECT = Path(__file__).resolve().parents[1]
CANCEL_MODE = '--cancel' in sys.argv[1:]
WORK = PROJECT / 'private' / ('lifecycle-cancel' if CANCEL_MODE else 'lifecycle')
RELAY = WORK / 'relay'
LEDGER = WORK / 'validation.sqlite3'
STRATEGY, SYMBOL = 'noise_area', 'US.SPY'
counter = itertools.count(1)


def log(**fields):
    print(json.dumps(fields, default=str), flush=True)


def relay(tool: str, arguments: dict, timeout: float = 240):
    n = next(counter)
    (RELAY / f'{n:02d}-request.json').write_text(json.dumps({'tool': tool, 'arguments': arguments}, indent=1))
    log(relay=n, tool=tool)
    response = RELAY / f'{n:02d}-response.json'
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if response.exists():
            time.sleep(0.3)
            return json.loads(response.read_text(encoding='utf-8')), datetime.fromtimestamp(response.stat().st_mtime, timezone.utc)
        time.sleep(0.2)
    (RELAY / f'{n:02d}-EXPIRED').write_text('expired: do not execute this request')
    raise TimeoutError(f'relay {n} expired')


def observe(ledger, account_key, tool, arguments):
    data, observed = relay(tool, arguments)
    digest = None
    if tool in PAPER_AUDIT_TOOLS:
        with ledger.transaction():
            digest = record_observation(ledger, account_key, tool, data, observed)
    if data.get('ret_code') != 0:
        raise RuntimeError(f'{tool} unsuccessful')
    return data, observed, digest


def fresh_account(ledger, account_id, account_key, authorized):
    data, observed, _ = observe(ledger, account_key, 'mcp__moomoo__sim_trade_account_list', {})
    rows = [r for r in data['data']['accounts'] if r['account_id'] == account_id]
    if len(rows) != 1:
        raise RuntimeError('Authorized practice account not uniquely listed')
    row = rows[0]
    # The simulated-account endpoint scopes the environment; no literal field exists.
    return PracticeAccount(account_id, row['account_title'], row['account_type'], row['market_id'],
                           'SIMULATE', authorized, observed)


def cancel_leg(ledger, executor, account_id, account_key, authorized):
    """Non-marketable BUY limit: observe WORKING, request cancel, reconcile CANCELLED."""
    price = Decimal(relay('operator.limit_price', {'side': 'BUY', 'symbol': SYMBOL, 'purpose': 'non-marketable'})[0]['limit_price'])
    cash, _, _ = observe(ledger, account_key, 'mcp__moomoo__sim_trade_cash_info', {'acc_id': account_id, 'market': 100})
    account = fresh_account(ledger, account_id, account_key, authorized)
    request = Request(STRATEGY, SYMBOL, 'lifecycle-validation-cancel-1', datetime.now(timezone.utc), 1, 1,
                      float(price), 'ETF')
    intent = executor.submit(account, request, float(Decimal(cash['data']['balance'])))
    log(submitted=intent, side=1, limit=str(price))
    begin = int((request.at - timedelta(minutes=2)).timestamp() * 1e6)
    status, cancelled = None, False
    for attempt in range(6):
        time.sleep(3)
        end = int((datetime.now(timezone.utc) + timedelta(minutes=2)).timestamp() * 1e6)
        _, _, digest = observe(ledger, account_key, HISTORY_TOOL,
                               {'acc_id': account_id, 'market': 100, 'time_begin': begin, 'time_end': end})
        reconcile_history_order(ledger, intent, digest)
        status = ledger.db.execute('SELECT status FROM orders WHERE intent_id=?', (intent,)).fetchone()['status']
        log(intent=intent, attempt=attempt, status=status)
        if status in ('FILLED', 'CANCELLED', 'REJECTED'):
            return status
        if status in ('WORKING', 'PARTIAL') and not cancelled:
            executor.cancel(fresh_account(ledger, account_id, account_key, authorized), intent)
            cancelled = True
    return status


def leg(ledger, executor, account_id, account_key, authorized, *, side, signal):
    price = Decimal(relay('operator.limit_price', {'side': 'BUY' if side == 1 else 'SELL', 'symbol': SYMBOL})[0]['limit_price'])
    cash, _, _ = observe(ledger, account_key, 'mcp__moomoo__sim_trade_cash_info', {'acc_id': account_id, 'market': 100})
    account = fresh_account(ledger, account_id, account_key, authorized)
    request = Request(STRATEGY, SYMBOL, signal, datetime.now(timezone.utc), side, 1, float(price), 'ETF',
                      close_only=side == -1)
    intent = executor.submit(account, request, float(Decimal(cash['data']['balance'])))
    log(submitted=intent, side=side, limit=str(price))
    begin = int((request.at - timedelta(minutes=2)).timestamp() * 1e6)
    for attempt in range(4):
        time.sleep(3)
        end = int((datetime.now(timezone.utc) + timedelta(minutes=2)).timestamp() * 1e6)
        _, _, digest = observe(ledger, account_key, HISTORY_TOOL,
                               {'acc_id': account_id, 'market': 100, 'time_begin': begin, 'time_end': end})
        reconcile_history_order(ledger, intent, digest)
        status = ledger.db.execute('SELECT status FROM orders WHERE intent_id=?', (intent,)).fetchone()['status']
        log(intent=intent, attempt=attempt, status=status)
        if status in ('FILLED', 'CANCELLED', 'REJECTED'):
            return status
        if attempt == 2:
            executor.cancel(fresh_account(ledger, account_id, account_key, authorized), intent)
    return status


def main():
    RELAY.mkdir(parents=True, exist_ok=True)
    if any(RELAY.iterdir()):
        sys.exit('Relay directory not empty; inspect a previous run before retrying')
    log(initialized=initialize(PROJECT, LEDGER))
    mapping = json.loads((PROJECT / 'private' / 'accounts.json').read_text(encoding='utf-8'))
    candidate = mapping['candidate']
    account_id = candidate['account_id']
    account_key = hashlib.sha256(account_id.encode()).hexdigest()
    authorized = bool(candidate['authorized_for_orders'])
    digest = project_digest(PROJECT)
    ledger = Ledger(LEDGER)
    try:
        executor = PaperExecutor(
            ledger, lambda tool, arguments: relay(tool, arguments)[0],
            frozenset({account_id}), frozenset(mapping['denied_account_ids']),
            # Isolated supervised validation only: 1 share, never the comparison ledger.
            release_check=lambda strategy, d, now: strategy == STRATEGY and d == digest,
            risk_check=lambda account, request, now: request.qty == 1 and request.symbol == SYMBOL,
            digest=digest)
        if CANCEL_MODE:
            buy = cancel_leg(ledger, executor, account_id, account_key, authorized)
            sell = None
        else:
            buy = leg(ledger, executor, account_id, account_key, authorized, side=1, signal='lifecycle-validation-buy-1')
            sell = leg(ledger, executor, account_id, account_key, authorized, side=-1, signal='lifecycle-validation-sell-1') if buy == 'FILLED' else None
        positions, _, _ = observe(ledger, account_key, 'mcp__moomoo__sim_trade_position_list', {'acc_id': account_id, 'market': 100})
        snapshot = ledger.snapshot(STRATEGY)
        log(result={'buy': buy, 'sell': sell, 'broker_positions': positions['data'].get('positions'),
                    'ledger_positions': snapshot['positions'], 'nav': snapshot['nav'],
                    'realized_pnl': snapshot.get('realized_pnl'), 'audit': audit_orders(ledger),
                    'chain_verified': ledger.verify_chain()})
    finally:
        ledger.close()


if __name__ == '__main__':
    main()
