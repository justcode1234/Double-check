"""Research-only Decimal book; final state independently replayed by Ledger.

No broker transport, account mapping, or forward state is opened here. Bar stops
are hypothetical barrier fills, not proof that a local sentinel can achieve them.
"""
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from dual_strategy.models import Bar
from dual_strategy.portfolio.ledger import INITIAL, Ledger, amount, challenge_fee

D = amount
ZERO = Decimal(0)


@dataclass
class ResearchRisk:
    limits: dict
    peak: Decimal = INITIAL
    day_start: Decimal = INITIAL
    day: date | None = None
    reduced: bool = False
    daily_locked: bool = False
    halted: bool = False
    terminal: bool = False

    def begin(self, day: date, nav: Decimal):
        if self.day is not None and day <= self.day:
            raise ValueError('Sessions must advance')
        self.day, self.day_start = day, nav
        # Preauthorized daily-only research rearm; permanent DD halt is never reset.
        self.daily_locked = False
        self.update(nav)

    def update(self, nav: Decimal):
        self.peak = max(self.peak, nav)
        dd = 1 - nav / self.peak
        self.daily_locked |= nav <= self.day_start * (1 - D(self.limits['daily_loss_fraction']))
        self.terminal |= dd >= D(self.limits['terminal_drawdown'])
        self.halted |= dd >= D(self.limits['halt_drawdown']) or self.terminal
        self.reduced = dd >= D(self.limits['reduce_recovery'] if self.reduced else self.limits['reduce_drawdown'])

    @property
    def blocked(self):
        return self.daily_locked or self.halted or self.terminal

    @property
    def factor(self):
        return D('.5') if self.reduced else D(1)

    def export(self):
        return dict(day=str(self.day), day_start=str(self.day_start), peak=str(self.peak),
                    reduced=self.reduced, daily_locked=self.daily_locked, halted=self.halted,
                    terminal=self.terminal, rearm='daily-only on next session; DD halt sticky; terminal absolute')


def adverse_stop(bar: Bar, side: int, stop: Decimal) -> Decimal | None:
    if side == 1 and D(bar.low) <= stop:
        return min(D(bar.open), stop)
    if side == -1 and D(bar.high) >= stop:
        return max(D(bar.open), stop)
    return None


@dataclass
class Position:
    qty: int = 0
    average: Decimal = ZERO
    entry_at: datetime | None = None
    entry_fees: Decimal = ZERO
    stop: Decimal | None = None
    stop_distance: Decimal | None = None
    total_entry_qty: int = 0
    total_entry_notional: Decimal = ZERO
    total_exit_qty: int = 0
    total_exit_notional: Decimal = ZERO
    net: Decimal = ZERO
    initial_risk: Decimal = ZERO
    fees: Decimal = ZERO
    slippage: Decimal = ZERO
    fills: list = field(default_factory=list)
    direction: int = 0


class Book:
    def __init__(self, strategy: str, run_id: str, digest: str, at: datetime):
        self.strategy, self.run_id, self.digest = strategy, run_id, digest
        self.ledger = Ledger(':memory:')
        self.cash = INITIAL
        self.positions: dict[str, Position] = {}
        self.orders: list[dict] = []
        self.trades: list[dict] = []
        self.equity: list[dict] = []
        self.counter = 0
        self.fees = self.slippage = self.financing = ZERO
        self.event('INITIAL_CAPITAL', dict(amount=str(INITIAL), mode='SHADOW'), at)

    def event(self, kind: str, payload: dict, at: datetime):
        self.counter += 1
        self.ledger.append(self.strategy, kind, payload, self.digest,
                           f'{self.run_id}:{self.counter:07}', at.isoformat())

    def nav(self, marks: dict) -> Decimal:
        return self.cash + sum((p.qty * D(marks[s]) for s, p in self.positions.items() if p.qty), ZERO)

    def exposure(self, marks: dict) -> Decimal:
        return sum((abs(p.qty) * D(marks[s]) for s, p in self.positions.items() if p.qty), ZERO)

    def order(self, symbol: str, side: int, qty: int, at: datetime, reason: str, *,
              signal_bar_end: datetime | None = None, due: int = 0,
              stop_distance: Decimal | None = None, entry=False) -> dict:
        if qty <= 0 or side not in (-1, 1):
            raise ValueError('Invalid hypothetical order')
        order = dict(id=f'{self.run_id}:order:{len(self.orders)+1:05}', symbol=symbol,
                     side=side, qty=qty, filled=0, at=at.isoformat(), reason=reason,
                     signal_bar_end=signal_bar_end.isoformat() if signal_bar_end else None,
                     due=due, stop_distance=str(stop_distance) if stop_distance else None,
                     entry=entry, status='WORKING', fee='0', financing='0', slippage='0', fills=[])
        self.orders.append(order)
        self.event('ORDER_REQUEST', {k: v for k, v in order.items() if k != 'fills'}, at)
        self.event('ORDER_ACKNOWLEDGED', dict(order_id=order['id'], simulated=True), at)
        return order

    def cancel(self, order, at, reason):
        if order['status'] not in ('WORKING', 'PARTIAL'):
            return
        order['status'] = 'CANCELLED'
        self.event('ORDER_CANCELLED', dict(order_id=order['id'], remaining=order['qty'] - order['filled'], reason=reason), at)

    def fill(self, order, qty: int, reference, at: datetime, *, spread_bps=0, slippage_bps=0,
             execution_bar_end: datetime | None = None, financing=ZERO):
        if order['status'] not in ('WORKING', 'PARTIAL') or not 0 < qty <= order['qty'] - order['filled']:
            raise ValueError('Invalid fill or duplicate retry')
        if order['entry'] and order['signal_bar_end']:
            signal_at = datetime.fromisoformat(order['signal_bar_end'])
            if execution_bar_end is None or execution_bar_end <= signal_at or at < signal_at:
                raise ValueError('Cannot fill an entry on its signal bar or before its signal')
        side, symbol = order['side'], order['symbol']
        reference = D(reference)
        price = reference * (1 + side * (D(spread_bps) / 2 + D(slippage_bps)) / 10000)
        fee = challenge_fee(qty, side, already_filled=order['filled'])
        financing = D(financing)
        total_fee = fee + financing
        slip = qty * abs(price - reference)
        position = self.positions.setdefault(symbol, Position())
        old = position.qty
        if old and old * side < 0 and qty > abs(old):
            raise ValueError('Reversals must close then create a separate order')
        self.cash -= qty * side * price + total_fee
        self.fees += fee
        self.financing += financing
        self.slippage += slip
        position.fees += total_fee
        position.slippage += slip
        position.net -= total_fee
        fill_risk = ZERO
        if not old or old * side > 0:
            if not old:
                position.entry_at, position.direction = at, side
            position.average = (abs(old) * position.average + qty * price) / (abs(old) + qty)
            position.total_entry_qty += qty
            position.total_entry_notional += qty * price
            position.entry_fees += total_fee
            if order['stop_distance']:
                position.stop_distance = D(order['stop_distance'])
                fill_risk = qty * position.stop_distance
                position.initial_risk += fill_risk
                # Weighted actual executed entry, not signal close; no profit target.
                position.stop = position.average - side * position.stop_distance
        else:
            position.net += qty * (price - position.average) * position.direction
            position.total_exit_qty += qty
            position.total_exit_notional += qty * price
        position.qty += qty * side
        order['filled'] += qty
        order['status'] = 'FILLED' if order['filled'] == order['qty'] else 'PARTIAL'
        for key, value in [('fee', fee), ('financing', financing), ('slippage', slip)]:
            order[key] = str(D(order[key]) + value)
        payload = dict(symbol=symbol, qty=qty, side=side, price=str(price), fee=str(total_fee),
                       challenge_fee=str(fee), financing=str(financing), reference_price=str(reference),
                       slippage=str(slip), source='SHADOW', broker_fee=None, sector='UNVERIFIED',
                       order_id=order['id'], signal_at=order['signal_bar_end'],
                       execution_bar_end=execution_bar_end.isoformat() if execution_bar_end else None,
                       stop_price=str(position.stop) if position.stop else None,
                       initial_risk=str(fill_risk) if fill_risk else None,
                       reason=order['reason'])
        order['fills'].append(dict(at=at.isoformat(), **payload))
        position.fills.append(dict(at=at.isoformat(), **payload))
        self.event('FILL', payload, at)
        self.event('ORDER_' + order['status'], dict(order_id=order['id'], cumulative_qty=order['filled']), at)
        if position.qty == 0:
            self.trades.append(dict(symbol=symbol, side=position.direction,
                                    entry_at=position.entry_at.isoformat(), at=at.isoformat(),
                                    qty=position.total_entry_qty,
                                    average_entry=str(position.total_entry_notional / position.total_entry_qty),
                                    average_exit=str(position.total_exit_notional / position.total_exit_qty),
                                    net_pnl=float(position.net), net_pnl_decimal=str(position.net),
                                    fees=str(position.fees), slippage=str(position.slippage),
                                    initial_risk=str(position.initial_risk) if position.initial_risk else None,
                                    r_multiple=float(position.net / position.initial_risk) if position.initial_risk else None,
                                    exit_reason=order['reason'], fills=position.fills))
            del self.positions[symbol]
        return price

    def mark(self, marks: dict, at: datetime, phase='closed_bar'):
        nav, gross = self.nav(marks), self.exposure(marks)
        self.event('MARK', dict(prices={s: str(v) for s, v in marks.items()}, phase=phase), at)
        point = dict(at=at.isoformat(), nav=float(nav), nav_decimal=str(nav),
                     gross_exposure=float(gross), net_exposure=float(nav - self.cash), phase=phase)
        self.equity.append(point)
        return nav

    def verify(self):
        snap = self.ledger.snapshot(self.strategy)
        if not self.ledger.verify_chain() or abs(D(snap['cash']) - self.cash) > D('.00000001'):
            raise AssertionError('Decimal book failed ledger replay reconciliation')
        if sorted((p['symbol'], p['qty']) for p in snap['positions']) != sorted((s, p.qty) for s, p in self.positions.items()):
            raise AssertionError('Position replay differs')
        if abs(D(snap['fees']) - self.fees - self.financing) > D('.00000001'):
            raise AssertionError('Fee replay differs')
        if len(snap['trades']) != len(self.trades):
            raise AssertionError('Flat-to-flat trade replay count differs')
        for replayed, trade in zip(snap['trades'], self.trades):
            if (replayed['symbol'], replayed['qty'], replayed['started_at'], replayed['at']) != (
                    trade['symbol'], trade['qty'], trade['entry_at'], trade['at']):
                raise AssertionError('Flat-to-flat trade replay identity differs')
            if abs(D(replayed['net_pnl']) - D(trade['net_pnl_decimal'])) > D('.00000001'):
                raise AssertionError('Flat-to-flat trade replay P&L differs')
            replay_r, trade_r = replayed['r_multiple'], trade['r_multiple']
            if (replay_r is None) != (trade_r is None) or (
                    replay_r is not None and abs(D(replay_r) - D(trade_r)) > D('.00000001')):
                raise AssertionError('Flat-to-flat trade replay R differs')
        return snap
