"""Continuous close-only sentinel service.

Drives the deterministic Sentinel core in a bounded loop over an authenticated
practice-only transport (CliTransport). It reconciles broker account/positions
against the independent strategy ledger, fetches fresh quotes, writes
SENTINEL_HEARTBEAT ledger events, persists close-only liquidation state for
restart recovery, and records measured quote-to-submit and callback latency.

This service can only cancel/close own-strategy positions; it never opens,
adds, reverses or crosses portfolio/account scope. It is not a substitute for
release gates: real stop latency must be measured against the authenticated
broker, and entries remain blocked until every release gate passes.
"""
import hashlib
import json
import os
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from dual_strategy.execution.moomoo_cli import (
    ACCOUNT_LIST, CANCEL, CASH_INFO, HISTORY, PLACE, POSITION_LIST,
)
from dual_strategy.models import Quote, aware
from dual_strategy.portfolio.ledger import Ledger
from dual_strategy.risk.gate import AccountIdentity, PendingOrder, PositionSnapshot
from dual_strategy.risk.sentinel import (
    CloseRequest, Heartbeat, ObservedQuote, Reconciliation, Sentinel, SentinelState,
)
from dual_strategy.risk.state_machine import RiskState


class SentinelService:
    def __init__(self, ledger: Ledger, transport, strategy: str, account_id: str, *,
                 digest: str, quote_feed, state_dir: Path, calendar=None,
                 interval: float = 1.0, monotonic=time.monotonic):
        if strategy not in ('orb', 'noise_area'):
            raise ValueError('Unknown strategy')
        self.ledger, self.transport = ledger, transport
        self.strategy, self.account_id = strategy, account_id
        self.account_key = hashlib.sha256(account_id.encode()).hexdigest()
        self.digest, self.quote_feed = digest, quote_feed
        self.calendar, self.interval = calendar, interval
        self.monotonic = monotonic
        self.allowed_accounts = {strategy: account_id}
        self.state_path = Path(state_dir) / f'sentinel-{strategy}.json'
        self.state = self._load_state()
        self._latest_quotes: dict[str, ObservedQuote] = {}
        self._tick_now = None

    def _load_state(self) -> SentinelState:
        if self.state_path.exists():
            return SentinelState.from_dict(json.loads(self.state_path.read_text(encoding='utf-8')))
        return SentinelState(self.strategy, self.account_id)

    def _persist_state(self, state: SentinelState) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix('.tmp')
        tmp.write_text(json.dumps(state.to_dict(), sort_keys=True), encoding='utf-8')
        os.replace(tmp, self.state_path)

    def _persist_risk(self, risk: RiskState) -> None:
        with self.ledger.transaction():
            self.ledger.db.execute('INSERT INTO state(strategy,payload) VALUES(?,?) '
                                   'ON CONFLICT(strategy) DO UPDATE SET payload=excluded.payload',
                                   (self.strategy, json.dumps(risk.to_dict(), sort_keys=True)))

    def _write_heartbeat(self, heartbeat: Heartbeat) -> None:
        self.ledger.append(self.strategy, 'SENTINEL_HEARTBEAT',
                           {'healthy': heartbeat.healthy, 'account_id': heartbeat.account_id,
                            'reconciliation_revision': heartbeat.reconciliation_revision},
                           self.digest, f'heartbeat:{self.strategy}:{heartbeat.at.isoformat()}',
                           heartbeat.at.isoformat())

    def _account_identity(self) -> AccountIdentity:
        data = self.transport(ACCOUNT_LIST, {})
        rows = [r for r in data['data']['accounts'] if r['account_id'] == self.account_id]
        if len(rows) != 1:
            raise ValueError('Practice account not uniquely listed')
        row = rows[0]
        if row.get('account_type') != 1 or row.get('market_id') != 100:
            raise ValueError('Not an ordinary U.S. practice account')
        return AccountIdentity(self.account_id, self.strategy, 'SIMULATE', 'US',
                               row['account_title'], 'PRACTICE')

    def reconcile(self) -> Reconciliation:
        now = self._tick_now or datetime.now(timezone.utc)
        account = self._account_identity()
        cash = self.transport(CASH_INFO, {'acc_id': self.account_id, 'market': 100})
        broker_positions = self.transport(POSITION_LIST, {'acc_id': self.account_id, 'market': 100})
        broker_qty = {p['symbol']: int(p.get('qty', 0)) for p in broker_positions['data']['positions']}
        marks = {s: (q.quote.bid + q.quote.ask) / 2 for s, q in self._latest_quotes.items()}
        snap = self.ledger.snapshot(self.strategy, marks or None)
        stops = self._stop_prices()
        positions = []
        for pos in snap['positions']:
            symbol = pos['symbol']
            observed = self._latest_quotes.get(symbol)
            price = (observed.quote.bid + observed.quote.ask) / 2 if observed else pos['mark']
            kind = 'ETF' if symbol in ('SPY', 'US.SPY') else 'STOCK'
            positions.append(PositionSnapshot(self.account_id, self.strategy, symbol, pos['qty'],
                                              float(price), pos['sector'], kind, now, stops.get(symbol)))
        pending = self._pending_orders()
        revision = hashlib.sha256(json.dumps(
            [snap['nav'], [(p.symbol, p.quantity) for p in positions],
             [(o.symbol, o.side, o.quantity) for o in pending], sorted(broker_qty.items())],
            sort_keys=True).encode()).hexdigest()[:16]
        return Reconciliation(account, now, now, float(snap['nav']), tuple(positions), tuple(pending),
                              True, revision, ())

    def _stop_prices(self) -> dict:
        stops = {}
        for row in self.ledger.db.execute('SELECT symbol,payload FROM orders WHERE strategy=?', (self.strategy,)):
            payload = json.loads(row['payload'])
            if payload.get('stop_price') is not None:
                stops[row['symbol']] = float(payload['stop_price'])
        return stops

    def _pending_orders(self) -> list:
        pending = []
        rows = self.ledger.db.execute(
            "SELECT symbol,side,qty,reserved,payload FROM orders WHERE strategy=? AND account_key=? "
            "AND status IN ('SUBMITTING','UNKNOWN','WORKING','PARTIAL','CANCEL_PENDING')",
            (self.strategy, self.account_key))
        for row in rows:
            payload = json.loads(row['payload'])
            reserved = float(row['reserved'])
            pending.append(PendingOrder(self.account_id, self.strategy, row['symbol'],
                                        'BUY' if row['side'] == 1 else 'SELL', int(row['qty']),
                                        reserved, reserved, payload.get('sector', 'UNKNOWN'),
                                        bool(payload.get('close_only', False)), ''))
        return pending

    def close(self, request: CloseRequest) -> str:
        observed = self._latest_quotes.get(request.symbol)
        if observed is None or not observed.quote.bid or not observed.quote.ask:
            raise ValueError('No fresh quote to price a close-only order')
        price = observed.quote.bid if request.side == 'SELL' else observed.quote.ask
        arguments = {'acc_id': self.account_id, 'market': 100, 'symbol': request.symbol,
                     'order_type': 1, 'order_side': 2 if request.side == 'SELL' else 1,
                     'qty': str(request.quantity), 'price': str(price),
                     'text': f'close:{self.strategy}:{request.request_id}'[:100]}
        result = self.transport(PLACE, arguments)
        return str(result['data']['order_id'])

    def cancel(self, strategy: str, account_id: str) -> bool:
        if (strategy, account_id) != (self.strategy, self.account_id):
            return False
        rows = self.ledger.db.execute(
            "SELECT broker_order_id FROM orders WHERE strategy=? AND account_key=? "
            "AND status IN ('WORKING','PARTIAL') AND broker_order_id IS NOT NULL",
            (self.strategy, self.account_key)).fetchall()
        for row in rows:
            result = self.transport(CANCEL, {'acc_id': self.account_id, 'market': 100,
                                             'order_id': row['broker_order_id']})
            if result.get('ret_code') != 0:
                return False
        return True

    def _sentinel(self) -> Sentinel:
        return Sentinel(self.state, allowed_accounts=self.allowed_accounts,
                        reconcile=self.reconcile, close=self.close, cancel=self.cancel,
                        persist_state=self._persist_state, persist_risk=self._persist_risk,
                        write_heartbeat=self._write_heartbeat, monotonic=self.monotonic,
                        calendar=self.calendar)

    def tick(self, *, now: datetime, risk: RiskState, quotes: dict[str, ObservedQuote],
             feed_at, executor_heartbeat_at):
        self._latest_quotes = dict(quotes)
        self._tick_now = now
        sentinel = self._sentinel()
        result = sentinel.tick(now=now, risk=risk, quotes=quotes, feed_at=feed_at,
                               executor_heartbeat_at=executor_heartbeat_at)
        self.state = sentinel.state
        return result

    def run_forever(self, *, risk: RiskState, stop=None, max_ticks: int | None = None):
        """Bounded loop; measures actual tick frequency. Stops on stop() truthy or max_ticks."""
        ticks = 0
        start = self.monotonic()
        while stop is None or not stop():
            now = datetime.now(timezone.utc)
            quotes = {}
            for symbol in self._own_symbols():
                quote = self.quote_feed(symbol)
                if quote is not None:
                    quotes[symbol] = ObservedQuote(quote, self.monotonic())
            feed_at = max((q.quote.at for q in quotes.values()), default=None)
            self.tick(now=now, risk=risk, quotes=quotes, feed_at=feed_at, executor_heartbeat_at=now)
            ticks += 1
            if max_ticks is not None and ticks >= max_ticks:
                break
            time.sleep(self.interval)
        elapsed = self.monotonic() - start
        return {'ticks': ticks, 'elapsed_seconds': elapsed,
                'measured_frequency_hz': ticks / elapsed if elapsed > 0 else None}

    def _own_symbols(self) -> set:
        snap = self.ledger.snapshot(self.strategy)
        symbols = {p['symbol'] for p in snap['positions']}
        symbols.update(r['symbol'] for r in self.ledger.db.execute(
            "SELECT DISTINCT symbol FROM orders WHERE strategy=? AND status IN ('WORKING','PARTIAL')",
            (self.strategy,)))
        if self.strategy == 'noise_area':
            symbols.add('US.SPY')
        return symbols