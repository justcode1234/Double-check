from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from math import floor, isfinite
from typing import Literal, Mapping, Protocol
from zoneinfo import ZoneInfo

from dual_strategy.models import Quote, Security, StrategyId, aware
from dual_strategy.risk.state_machine import ReleaseChecklist, RiskState

NY = ZoneInfo("America/New_York")


class SessionCalendar(Protocol):
    def session_bounds(self, day: date) -> tuple[datetime, datetime]: ...
    def flatten_time(self, day: date) -> datetime: ...
    def decision_times(self, day: date) -> tuple[datetime, ...]: ...


def get_calendar(calendar: SessionCalendar | None = None) -> SessionCalendar:
    if calendar is not None:
        return calendar
    from dual_strategy.data import calendar as exchange_calendar
    return exchange_calendar


def finite(value: float) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)


def fresh(at: datetime | None, now: datetime, max_age: float) -> bool:
    try:
        return (finite(max_age) and max_age >= 0
                and 0 <= (aware(now) - aware(at)).total_seconds() <= max_age)
    except (AttributeError, TypeError, ValueError, OverflowError):
        return False


def size_orb(nav: float, entry: float, stop_distance: float, liquidity_cap: float) -> int:
    if not all(finite(v) and v > 0 for v in (nav, entry, stop_distance)):
        raise ValueError("NAV, entry and stop distance must be finite and positive")
    if not finite(liquidity_cap) or liquidity_cap < 0:
        raise ValueError("Liquidity cap must be finite and nonnegative")
    return floor(min(0.0025 * nav / stop_distance, 0.15 * nav / entry, liquidity_cap))


def size_noise_area(nav: float, entry: float) -> int:
    if not all(finite(v) and v > 0 for v in (nav, entry)):
        raise ValueError("NAV and entry must be finite and positive")
    return floor(0.4 * nav / entry)


@dataclass(frozen=True)
class AccountIdentity:
    account_id: str
    strategy: StrategyId
    environment: str
    market: str
    title: str
    account_type: str


def account_allowed(account: AccountIdentity, allowlist: Mapping[str, str]) -> bool:
    if not isinstance(account, AccountIdentity) or not all(isinstance(value, str) for value in (
            account.account_id, account.strategy, account.environment, account.market, account.title, account.account_type)):
        return False
    return (account.strategy in ("orb", "noise_area") and bool(account.account_id)
            and allowlist.get(account.strategy) == account.account_id
            and account.environment == "SIMULATE" and account.market == "US"
            and account.account_type == "PRACTICE" and bool(account.title.strip())
            and not any(word in (account.title + " " + account.account_type).casefold()
                        for word in ("competition", "challenge", "contest", "official")))


@dataclass(frozen=True)
class PositionSnapshot:
    account_id: str
    strategy: StrategyId
    symbol: str
    quantity: int
    market_price: float
    sector: str
    kind: Literal["STOCK", "ETF"]
    at: datetime
    stop_price: float | None = None


@dataclass(frozen=True)
class PendingOrder:
    account_id: str
    strategy: StrategyId
    symbol: str
    side: Literal["BUY", "SELL"]
    quantity: int
    reserved_notional: float
    reserved_cash: float
    sector: str
    close_only: bool = False
    request_id: str = ""


@dataclass(frozen=True)
class ShortVerification:
    account_id: str
    strategy: StrategyId
    symbol: str
    verified_at: datetime
    borrow_available: int
    lifecycle_evidence_ref: str


@dataclass(frozen=True)
class OrderIntent:
    strategy: StrategyId
    account_id: str
    symbol: str
    side: Literal["BUY", "SELL"]
    quantity: int
    entry_price: float
    signal_at: datetime
    deadline: datetime
    close_only: bool = False
    stop_distance: float | None = None
    estimated_fees: float = 0.0


@dataclass(frozen=True)
class GateContext:
    now: datetime
    account: AccountIdentity
    allowed_accounts: Mapping[str, str]
    risk: RiskState
    release: ReleaseChecklist
    config_hash: str
    cash: float
    broker_buying_power: float
    broker_reserved_cash: float
    positions: tuple[PositionSnapshot, ...]
    pending: tuple[PendingOrder, ...]
    quote: Quote
    security: Security
    account_at: datetime
    positions_at: datetime
    bar_at: datetime
    expected_bar_at: datetime
    sentinel_at: datetime
    liquidity_cap: float
    positions_complete: bool = False
    orders_complete: bool = False
    sentinel_healthy: bool = False
    short_verification: ShortVerification | None = None
    calendar: SessionCalendar | None = field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class GateDecision:
    allowed: bool
    reasons: tuple[str, ...]
    quantity: int = 0


def _position_valid(p: PositionSnapshot) -> bool:
    return (bool(p.symbol) and type(p.quantity) is int and finite(p.market_price) and p.market_price > 0
            and bool(p.sector) and p.kind in ("STOCK", "ETF")
            and (p.stop_price is None or (finite(p.stop_price) and p.stop_price > 0)))


def _pending_valid(p: PendingOrder) -> bool:
    return (bool(p.symbol) and p.side in ("BUY", "SELL") and type(p.quantity) is int and p.quantity > 0
            and finite(p.reserved_notional) and p.reserved_notional >= 0
            and finite(p.reserved_cash) and p.reserved_cash >= 0 and bool(p.sector)
            and type(p.close_only) is bool
            and (p.close_only or (p.reserved_notional > 0 and p.reserved_cash >= p.reserved_notional)))


def check_order(intent: OrderIntent, context: GateContext) -> GateDecision:
    try:
        return _check_order(intent, context)
    except (AttributeError, TypeError, ValueError, KeyError, OverflowError):
        return GateDecision(False, ("invalid_or_missing_input",))


def _check_order(intent: OrderIntent, c: GateContext) -> GateDecision:
    errors: list[str] = []
    aware(c.now)
    if (not account_allowed(c.account, c.allowed_accounts)
            or (intent.account_id, intent.strategy) != (c.account.account_id, c.account.strategy)
            or c.risk.strategy != intent.strategy):
        errors.append("account_mapping")
    if (not intent.symbol or intent.side not in ("BUY", "SELL") or type(intent.quantity) is not int
            or intent.quantity <= 0 or not finite(intent.entry_price) or intent.entry_price <= 0
            or not finite(intent.estimated_fees) or intent.estimated_fees < 0
            or type(intent.close_only) is not bool
            or (intent.stop_distance is not None and
                (not finite(intent.stop_distance) or intent.stop_distance <= 0))):
        errors.append("invalid_order")
    if not all(finite(v) and v >= 0 for v in (c.cash, c.broker_buying_power, c.broker_reserved_cash,
                                            c.liquidity_cap)):
        errors.append("invalid_capital_or_liquidity")
    if not all(finite(v) for v in (c.risk.nav, c.risk.day_start_nav, c.risk.high_water_nav)):
        errors.append("invalid_nav")
    if c.positions_complete is not True or c.orders_complete is not True:
        errors.append("incomplete_reconciliation")
    if not fresh(c.account_at, c.now, 10) or not fresh(c.positions_at, c.now, 10):
        errors.append("stale_account_or_positions")
    own = (intent.account_id, intent.strategy)
    if any((p.account_id, p.strategy) != own or not _position_valid(p)
           or not fresh(p.at, c.now, 10) or p.at > c.positions_at for p in c.positions):
        errors.append("invalid_position_snapshot")
    if len({p.symbol for p in c.positions}) != len(c.positions):
        errors.append("duplicate_positions")
    if any((p.kind != "STOCK" or p.symbol in ("SPY", "US.SPY")) if intent.strategy == "orb"
           else (p.symbol not in ('SPY', 'US.SPY') or p.kind != "ETF") for p in c.positions if p.quantity):
        errors.append("portfolio_instrument_mismatch")
    if any((p.account_id, p.strategy) != own or not _pending_valid(p) for p in c.pending):
        errors.append("invalid_pending_reservations")
    if (c.quote.symbol != intent.symbol or not finite(c.quote.bid) or not finite(c.quote.ask)
            or c.quote.bid <= 0 or c.quote.ask < c.quote.bid):
        errors.append("invalid_quote")
    if not fresh(c.quote.at, c.now, 2):
        errors.append("stale_quote")
    day = c.now.astimezone(NY).date()
    calendar = get_calendar(c.calendar)
    opened, closed = calendar.session_bounds(day)
    flatten = calendar.flatten_time(day)
    if not (aware(opened) <= c.now < aware(closed)):
        errors.append("outside_regular_session")
    if not (opened < aware(flatten) < closed):
        errors.append("invalid_flatten_time")
    if (not fresh(intent.signal_at, c.now, 60) or aware(intent.deadline) <= c.now
            or intent.deadline <= intent.signal_at):
        errors.append("expired_or_invalid_signal")
    if errors:
        return GateDecision(False, tuple(errors))
    position = next((p for p in c.positions if p.symbol == intent.symbol and p.quantity != 0), None)
    working = [p for p in c.pending if p.symbol == intent.symbol]
    if intent.close_only:
        if position is None:
            errors.append("no_verified_position_to_close")
        else:
            close_side = "SELL" if position.quantity > 0 else "BUY"
            if any(not p.close_only or p.side != close_side for p in working):
                errors.append("conflicting_working_order")
            available = abs(position.quantity) - sum(p.quantity for p in working if p.close_only)
            if intent.side != close_side or intent.quantity > available:
                errors.append("over_close_or_reverse")
        return GateDecision(not errors, tuple(errors), intent.quantity if not errors else 0)
    if c.now >= flatten or intent.deadline > flatten:
        errors.append("preclose_entry_block")
    if c.risk.session != day or not c.risk.entries_allowed:
        errors.append("risk_state_blocks_entries")
    if not isinstance(c.release, ReleaseChecklist):
        errors.append("invalid_release_checklist")
    else:
        errors.extend(c.release.failures(c.now, c.config_hash, intent.strategy))
    if c.sentinel_healthy is not True or not fresh(c.sentinel_at, c.now, 5):
        errors.append("sentinel_unhealthy")
    if (not fresh(c.bar_at, c.now, 60) or not fresh(c.expected_bar_at, c.now, 60)
            or c.bar_at != c.expected_bar_at or c.bar_at > intent.signal_at):
        errors.append("missing_or_incorrect_completed_bar")
    if (c.security.symbol != intent.symbol or c.security.tradable is not True
            or c.security.asof != day or not finite(c.security.market_cap) or c.security.market_cap <= 0
            or not c.security.sector):
        errors.append("unverified_security")
    if intent.strategy == "orb":
        if c.security.kind != "STOCK" or intent.symbol in ("SPY", "US.SPY"):
            errors.append("orb_individual_stocks_only")
        if intent.signal_at < opened + timedelta(minutes=5):
            errors.append("opening_range_incomplete")
        if intent.stop_distance is None:
            errors.append("missing_stop_distance")
        elif intent.stop_distance <= c.quote.ask - c.quote.bid:
            errors.append("stop_not_wider_than_spread")
        elif intent.side == "BUY" and intent.stop_distance >= intent.entry_price:
            errors.append("invalid_long_stop")
    elif intent.strategy == "noise_area":
        if intent.symbol not in ('SPY', 'US.SPY') or c.security.kind != "ETF":
            errors.append("noise_area_spy_only")
        if intent.signal_at not in calendar.decision_times(day) or intent.signal_at != c.bar_at:
            errors.append("not_noise_area_decision_tick")
    else:
        errors.append("unknown_strategy")
    if working:
        errors.append("working_order_exists")
    if position and ((position.quantity > 0) != (intent.side == "BUY")):
        errors.append("opposing_entry_requires_close_first")
    if intent.side == "SELL":
        short = c.short_verification
        if (short is None or not c.release.shorts_enabled
                or (short.account_id, short.strategy, short.symbol) != (*own, intent.symbol)
                or not fresh(short.verified_at, c.now, 60) or not short.lifecycle_evidence_ref.strip()
                or type(short.borrow_available) is not int or short.borrow_available < intent.quantity):
            errors.append("short_not_verified")
    if intent.quantity > c.liquidity_cap:
        errors.append("liquidity_cap")
    if errors:
        return GateDecision(False, tuple(errors))
    price = max(intent.entry_price, c.quote.ask)
    proposed = intent.quantity * price
    factor = c.risk.exposure_factor
    nav = c.risk.nav
    reserved_cash = sum(p.reserved_cash for p in c.pending)
    if c.broker_reserved_cash < reserved_cash:
        errors.append("broker_reservations_incomplete")
    if reserved_cash + proposed + intent.estimated_fees > c.cash:
        errors.append("strategy_cash")
    if c.broker_reserved_cash + proposed + intent.estimated_fees > c.broker_buying_power:
        errors.append("broker_buying_power")
    entries = [p for p in c.pending if not p.close_only]
    instruments: dict[str, float] = {}
    sectors: dict[str, float] = {}
    for p in c.positions:
        mark = max(p.market_price, price) if p.symbol == intent.symbol else p.market_price
        notional = abs(p.quantity) * mark
        instruments[p.symbol] = instruments.get(p.symbol, 0.0) + notional
        sectors[p.sector] = sectors.get(p.sector, 0.0) + notional
    for pending in entries:
        instruments[pending.symbol] = instruments.get(pending.symbol, 0.0) + pending.reserved_notional
        sectors[pending.sector] = sectors.get(pending.sector, 0.0) + pending.reserved_notional
    instruments[intent.symbol] = instruments.get(intent.symbol, 0.0) + proposed
    sectors[c.security.sector] = sectors.get(c.security.sector, 0.0) + proposed
    gross = sum(instruments.values())
    if gross > nav * factor:
        errors.append("gross_exposure")
    cap = 0.15 if intent.strategy == "orb" else 0.4
    if any(value > cap * nav * factor for value in instruments.values()):
        errors.append("instrument_exposure")
    if intent.strategy == "orb":
        if any(value > 0.3 * nav * factor for value in sectors.values()):
            errors.append("sector_exposure")
        names = {p.symbol for p in c.positions if p.quantity} | {p.symbol for p in entries} | {intent.symbol}
        if len(names) > 4:
            errors.append("orb_position_count")
        if intent.quantity * intent.stop_distance > 0.0025 * nav * factor:
            errors.append("orb_trade_risk")
    if not finite(proposed) or not finite(gross + proposed + reserved_cash):
        errors.append("nonfinite_exposure")
    return GateDecision(not errors, tuple(errors), intent.quantity if not errors else 0)
