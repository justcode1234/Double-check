"""Authenticated supervised 1-share SPY paper round trip on an isolated ledger.

Uses the official Moomoo CLI (OAuth) via CliTransport instead of agent relay.
Every guard from the agent-relayed supervised lifecycle is retained: allowlisted
ordinary U.S. SIMULATE practice account only, competition account denied,
1-share US.SPY risk gate, isolated validation ledger (never the comparison
ledger). Run only after `moomoo auth status` shows trade permission.

Usage:
  .venv/Scripts/python.exe scripts/supervised_lifecycle_cli.py --buy-price 731.5 --sell-price 730.5
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
from dual_strategy.execution.audit import PAPER_AUDIT_TOOLS, audit_orders, record_observation
from dual_strategy.execution.broker_history import HISTORY_TOOL, reconcile_history_order
from dual_strategy.execution.moomoo_cli import CliTransport
from dual_strategy.execution.moomoo_paper import PaperExecutor, PracticeAccount, Request
from dual_strategy.portfolio.ledger import Ledger

PROJECT = Path(__file__).resolve().parents[1]
WORK = PROJECT / 'private' / 'lifecycle-cli'
LEDGER = WORK / 'validation.sqlite3'
STRATEGY, SYMBOL = 'noise_area', 'US.SPY'


def log(**fields):
    print(json.dumps(fields, default=str), flush=True)


def main():
    global WORK, LEDGER
    parser = argparse.ArgumentParser()
    parser.add_argument('--buy-price', required=True)
    parser.add_argument('--sell-price', required=True)
    parser.add_argument('--work-dir', default='private/lifecycle-cli')
    args = parser.parse_args()
    WORK = PROJECT / args.work_dir
    LEDGER = WORK / 'validation.sqlite3'
    WORK.mkdir(parents=True, exist_ok=True)
    if LEDGER.exists():
        sys.exit('Isolated validation ledger already exists; inspect it before retrying (never reset)')
    log(initialized=initialize(PROJECT, LEDGER))
    mapping = json.loads((PROJECT / 'private' / 'accounts.json').read_text(encoding='utf-8'))
    candidate = mapping['candidate']
    account_id = candidate['account_id']
    account_key = hashlib.sha256(account_id.encode()).hexdigest()
    authorized = bool(candidate['authorized_for_orders'])
    digest = project_digest(PROJECT)
    ledger = Ledger(LEDGER)
    transport = CliTransport(frozenset({account_id}), frozenset(mapping['denied_account_ids']))

    def observe(tool, arguments):
        data = transport(tool, arguments)
        observed = datetime.now(timezone.utc)
        if tool == HISTORY_TOOL:
            # WHY: broker history timestamps are whole-second and ceiling-rounded, so a
            # precise receive time can precede update_time by <1s. Record the observation
            # no earlier than the latest broker update it reflects (never fabricate fills).
            for row in data.get('data', {}).get('orders', []):
                if row.get('update_time'):
                    broker_at = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=int(row['update_time']))
                    if broker_at > observed:
                        observed = broker_at
        obs_digest = None
        if tool in PAPER_AUDIT_TOOLS:
            with ledger.transaction():
                obs_digest = record_observation(ledger, account_key, tool, data, observed)
        if data.get('ret_code') != 0:
            raise RuntimeError(f'{tool} unsuccessful')
        return data, observed, obs_digest

    def fresh_account():
        data, observed, _ = observe('mcp__moomoo__sim_trade_account_list', {})
        rows = [r for r in data['data']['accounts'] if r['account_id'] == account_id]
        if len(rows) != 1:
            raise RuntimeError('Authorized practice account not uniquely listed')
        row = rows[0]
        return PracticeAccount(account_id, row['account_title'], row['account_type'], row['market_id'],
                               'SIMULATE', authorized, observed)

    def leg(side, signal, price):
        cash, _, _ = observe('mcp__moomoo__sim_trade_cash_info', {'acc_id': account_id, 'market': 100})
        account = fresh_account()
        request = Request(STRATEGY, SYMBOL, signal, datetime.now(timezone.utc), side, 1, float(price), 'ETF',
                          close_only=side == -1)
        intent = executor.submit(account, request, float(Decimal(cash['data']['balance'])))
        log(submitted=intent, side=side, limit=str(price))
        begin = int((request.at - timedelta(minutes=2)).timestamp() * 1e6)
        for attempt in range(15):
            time.sleep(2)
            end = int((datetime.now(timezone.utc) + timedelta(minutes=2)).timestamp() * 1e6)
            _, _, obs_digest = observe(HISTORY_TOOL, {'acc_id': account_id, 'market': 100,
                                                      'time_begin': begin, 'time_end': end})
            reconcile_history_order(ledger, intent, obs_digest)
            status = ledger.db.execute('SELECT status FROM orders WHERE intent_id=?', (intent,)).fetchone()['status']
            log(intent=intent, attempt=attempt, status=status)
            if status in ('FILLED', 'CANCELLED', 'REJECTED'):
                return status
            if status in ('WORKING', 'PARTIAL') and attempt == 10:
                executor.cancel(fresh_account(), intent)
        return status

    try:
        executor = PaperExecutor(
            ledger, transport, frozenset({account_id}), frozenset(mapping['denied_account_ids']),
            release_check=lambda strategy, d, now: strategy == STRATEGY and d == digest,
            risk_check=lambda account, request, now: request.qty == 1 and request.symbol == SYMBOL,
            digest=digest)
        buy = leg(1, f'lifecycle-cli-buy-{WORK.name}', Decimal(args.buy_price))
        sell = leg(-1, f'lifecycle-cli-sell-{WORK.name}', Decimal(args.sell_price)) if buy == 'FILLED' else None
        positions, _, _ = observe('mcp__moomoo__sim_trade_position_list', {'acc_id': account_id, 'market': 100})
        snapshot = ledger.snapshot(STRATEGY)
        log(result={'buy': buy, 'sell': sell, 'broker_positions': positions['data'].get('positions'),
                    'ledger_positions': snapshot['positions'], 'nav': snapshot['nav'],
                    'realized_pnl': snapshot.get('realized_pnl'), 'audit': audit_orders(ledger),
                    'chain_verified': ledger.verify_chain(), 'transport': 'AUTHENTICATED_CLI'})
    finally:
        ledger.close()


if __name__ == '__main__':
    main()