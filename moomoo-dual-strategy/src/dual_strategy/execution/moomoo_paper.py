import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from dual_strategy.data.calendar import NY
from dual_strategy.execution.audit import record_observation
from dual_strategy.models import aware
from dual_strategy.portfolio.ledger import Ledger, amount, stamp

PAPER_PLACE = 'mcp__moomoo__sim_trade_input_order'
PAPER_CANCEL = 'mcp__moomoo__sim_trade_cancel_order'
READ_ONLY = {'mcp__moomoo__sim_trade_account_list', 'mcp__moomoo__sim_trade_cash_info',
             'mcp__moomoo__sim_trade_position_list', 'mcp__moomoo__sim_trade_history_order_list'}


class ExecutionBlocked(RuntimeError):
    pass


@dataclass(frozen=True)
class PracticeAccount:
    account_id: str
    title: str
    account_type: int
    market_id: int
    environment: str
    user_confirmed: bool
    observed_at: datetime

    def verify(self, allowlist: frozenset[str], denylist: frozenset[str], now: datetime):
        aware(now)
        aware(self.observed_at)
        if self.environment != 'SIMULATE' or self.market_id != 100 or self.account_type != 1:
            raise ExecutionBlocked('Only ordinary U.S. SIMULATE accounts are allowed')
        if not self.account_id or self.account_id not in allowlist or self.account_id in denylist:
            raise ExecutionBlocked('Account not explicitly allowlisted')
        if not self.user_confirmed or 'paper' not in self.title.lower() or any(s in self.title.lower() for s in ('challenge', 'competition')):
            raise ExecutionBlocked('Practice selection unconfirmed or competition account')
        if not 0 <= (now - self.observed_at).total_seconds() <= 15:
            raise ExecutionBlocked('Stale account verification')


@dataclass(frozen=True)
class Request:
    strategy: str
    symbol: str
    signal_id: str
    at: datetime
    side: int
    qty: int
    limit_price: float
    sector: str
    close_only: bool = False
    initial_risk: float | None = None

    @property
    def intent_id(self):
        body = f'{self.strategy}|{aware(self.at).astimezone(NY).date()}|{self.symbol}|{self.signal_id}'
        return hashlib.sha256(body.encode()).hexdigest()[:32]


class PaperExecutor:
    def __init__(self, ledger: Ledger, transport: Callable, allowlist: frozenset[str], denylist: frozenset[str],
                 release_check: Callable, risk_check: Callable, digest: str):
        self.ledger, self.transport = ledger, transport
        self.allowlist, self.denylist = allowlist, denylist
        self.release_check, self.risk_check, self.digest = release_check, risk_check, digest

    def submit(self, account: PracticeAccount, request: Request, broker_cash_available: float,
               now: datetime | None = None) -> str:
        now = now or datetime.now(timezone.utc)
        account.verify(self.allowlist, self.denylist, now)
        aware(request.at)
        if request.strategy not in ('orb', 'noise_area') or type(request.qty) is not int or request.qty <= 0 or request.side not in (-1, 1):
            raise ExecutionBlocked('Invalid order')
        if not request.symbol.startswith('US.') or amount(request.limit_price) <= 0:
            raise ExecutionBlocked('Invalid symbol or limit price')
        if not 0 <= (now - request.at).total_seconds() <= 90:
            raise ExecutionBlocked('Stale signal')
        if request.strategy == 'noise_area' and request.symbol != 'US.SPY':
            raise ExecutionBlocked('Noise-Area is SPY only')
        if not request.signal_id or len(request.signal_id) > 200:
            raise ExecutionBlocked('Stable signal identity required')
        if not request.close_only and not self.release_check(request.strategy, self.digest, now):
            raise ExecutionBlocked('Release gates incomplete')
        account_key = hashlib.sha256(account.account_id.encode()).hexdigest()
        with self.ledger.transaction():
            existing = self.ledger.db.execute('SELECT status FROM orders WHERE intent_id=?', (request.intent_id,)).fetchone()
            if existing:
                raise ExecutionBlocked('Intent already recorded; reconcile, never resubmit')
            if not self.risk_check(account, request, now):
                raise ExecutionBlocked('Deterministic risk gate rejected')
            snap = self.ledger.snapshot(request.strategy)
            current = next((p['qty'] for p in snap['positions'] if p['symbol'] == request.symbol), 0)
            if request.close_only and (not current or current * request.side >= 0 or request.qty > abs(current)):
                raise ExecutionBlocked('Close-only path cannot add or reverse')
            reserved = amount(request.limit_price) * request.qty + amount('.99') if not request.close_only else amount(0)
            shared_reserved = sum((amount(r[0]) for r in self.ledger.db.execute("SELECT reserved FROM orders WHERE account_key=? AND status IN ('SUBMITTING','UNKNOWN','WORKING','PARTIAL','CANCEL_PENDING')", (account_key,))), amount(0))
            if not request.close_only and (reserved > amount(snap['cash_available']) or reserved + shared_reserved > amount(broker_cash_available)):
                raise ExecutionBlocked('Independent or shared cash budget exhausted')
            payload = {'source': 'MOOMOO_PAPER_RECONCILED', 'config_hash': self.digest, 'sector': request.sector,
                       'initial_risk': request.initial_risk, 'reservation_per_share': str(reserved / request.qty)}
            try:
                self.ledger.db.execute('INSERT INTO orders(intent_id,strategy,account_key,symbol,side,qty,status,reserved,payload) VALUES(?,?,?,?,?,?,?,?,?)',
                    (request.intent_id, request.strategy, account_key, request.symbol, request.side, request.qty, 'SUBMITTING', str(reserved), json.dumps(payload)))
            except Exception as exc:
                raise ExecutionBlocked('Working order exists or durable intent write failed') from exc
            self.ledger._append(request.strategy, 'ORDER_REQUEST', {'intent_id': request.intent_id, 'symbol': request.symbol, 'side': request.side, 'qty': request.qty, 'limit_price': request.limit_price},
                                self.digest, f'request:{request.intent_id}', now.isoformat())
        arguments = {'acc_id': account.account_id, 'market': 100, 'symbol': request.symbol, 'order_type': 1,
                     'order_side': 1 if request.side == 1 else 2, 'qty': str(request.qty),
                     'price': str(request.limit_price), 'order_trade_time_type': 1,
                     'text': f'{request.strategy}:{request.at.date()}:{request.intent_id}'}
        try:
            result = self.transport(PAPER_PLACE, arguments)
            observed = datetime.now(timezone.utc)
            observation = record_observation(self.ledger, account_key, PAPER_PLACE, result, observed)
            if result.get('ret_code') != 0 or not result.get('data', {}).get('order_id'):
                raise ExecutionBlocked('No verified broker acknowledgement; reconcile')
            order_id = str(result['data']['order_id'])
            with self.ledger.transaction():
                self.ledger.db.execute("UPDATE orders SET status='WORKING',broker_order_id=? WHERE intent_id=?", (order_id, request.intent_id))
                self.ledger._append(request.strategy, 'ORDER_ACK', {'intent_id': request.intent_id, 'observation_hash': observation}, self.digest, f'ack:{request.intent_id}', observed.isoformat())
        except Exception:
            with self.ledger.transaction():
                self.ledger.db.execute("UPDATE orders SET status='UNKNOWN' WHERE intent_id=?", (request.intent_id,))
                self.ledger._append(request.strategy, 'ORDER_UNKNOWN', {'intent_id': request.intent_id}, self.digest, f'unknown:{request.intent_id}', stamp())
            raise
        return request.intent_id

    def cancel(self, account: PracticeAccount, intent_id: str, now: datetime | None = None) -> None:
        now = now or datetime.now(timezone.utc)
        account.verify(self.allowlist, self.denylist, now)
        account_key = hashlib.sha256(account.account_id.encode()).hexdigest()
        with self.ledger.transaction():
            order = self.ledger.db.execute('SELECT * FROM orders WHERE intent_id=?', (intent_id,)).fetchone()
            if not order or order['account_key'] != account_key:
                raise ExecutionBlocked('Cannot cancel an unattributed or mismapped order')
            if order['status'] not in ('WORKING', 'PARTIAL') or not order['broker_order_id']:
                raise ExecutionBlocked('Reconcile order before requesting cancellation')
            self.ledger.db.execute("UPDATE orders SET status='CANCEL_PENDING' WHERE intent_id=?", (intent_id,))
            self.ledger._append(order['strategy'], 'CANCEL_REQUEST', {'intent_id': intent_id}, self.digest, f'cancel:{intent_id}', now.isoformat())
        result = self.transport(PAPER_CANCEL, {'acc_id': account.account_id, 'market': 100, 'order_id': order['broker_order_id']})
        observation = record_observation(self.ledger, account_key, PAPER_CANCEL, result, datetime.now(timezone.utc))
        self.ledger.append(order['strategy'], 'CANCEL_RESPONSE', {'intent_id': intent_id, 'observation_hash': observation}, self.digest, f'cancel-response:{intent_id}')
        if result.get('ret_code') != 0:
            raise ExecutionBlocked('Cancellation not confirmed; reservation retained until reconciliation')


class DisabledTransport:
    def __call__(self, tool: str, arguments: dict):
        if tool not in READ_ONLY | {PAPER_PLACE, PAPER_CANCEL}:
            raise ExecutionBlocked('Non-paper tool forbidden')
        raise ExecutionBlocked('No supervised authenticated paper transport configured; shadow only')
