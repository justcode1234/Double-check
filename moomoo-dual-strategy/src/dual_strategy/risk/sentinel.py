from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from math import isfinite
from typing import Callable, Mapping

from dual_strategy.models import Quote, StrategyId, aware
from dual_strategy.risk.gate import (
    NY, AccountIdentity, PendingOrder, PositionSnapshot, SessionCalendar,
    _pending_valid, _position_valid, account_allowed, finite, fresh, get_calendar,
)
from dual_strategy.risk.state_machine import RiskState, RuntimeState


@dataclass(frozen=True)
class ObservedQuote:
    quote: Quote
    received_monotonic: float


@dataclass(frozen=True)
class Reconciliation:
    account: AccountIdentity
    at: datetime
    positions_at: datetime
    nav: float
    positions: tuple[PositionSnapshot, ...]
    pending: tuple[PendingOrder, ...] = ()
    complete: bool = False
    revision: str = ""
    resolved_request_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class CloseRequest:
    request_id: str
    strategy: StrategyId
    account_id: str
    symbol: str
    side: str
    quantity: int
    reason: str
    at: datetime
    snapshot_revision: str


@dataclass(frozen=True)
class PendingClose:
    request: CloseRequest
    broker_order_id: str | None = None


@dataclass(frozen=True)
class SentinelState:
    strategy: StrategyId
    account_id: str
    sequence: int = 0
    unresolved: tuple[PendingClose, ...] = ()
    last_reconciled_at: datetime | None = None
    last_heartbeat_at: datetime | None = None
    liquidate_all: bool = False
    liquidating_symbols: tuple[str, ...] = ()

    def __post_init__(self):
        if self.strategy not in ("orb", "noise_area") or not self.account_id:
            raise ValueError("Sentinel requires exact strategy/account scope")
        if type(self.sequence) is not int or self.sequence < 0:
            raise ValueError("Invalid sentinel sequence")
        if type(self.liquidate_all) is not bool or any(not isinstance(s, str) or not s for s in self.liquidating_symbols):
            raise ValueError("Invalid liquidation state")
        for stamp in (self.last_reconciled_at, self.last_heartbeat_at):
            if stamp is not None:
                aware(stamp)
        for item in self.unresolved:
            r = item.request
            aware(r.at)
            if ((r.strategy, r.account_id) != (self.strategy, self.account_id)
                    or type(r.quantity) is not int or r.quantity <= 0 or r.side not in ("BUY", "SELL")
                    or not r.symbol or not r.request_id or not r.snapshot_revision):
                raise ValueError("Invalid persisted close request")
        if len({p.request.request_id for p in self.unresolved}) != len(self.unresolved):
            raise ValueError("Duplicate persisted close request")

    def to_dict(self) -> dict:
        return dict(strategy=self.strategy, account_id=self.account_id, sequence=self.sequence,
                    liquidate_all=self.liquidate_all, liquidating_symbols=list(self.liquidating_symbols),
                    last_reconciled_at=self.last_reconciled_at.isoformat() if self.last_reconciled_at else None,
                    last_heartbeat_at=self.last_heartbeat_at.isoformat() if self.last_heartbeat_at else None,
                    unresolved=[dict(request=dict(request_id=p.request.request_id,
                        strategy=p.request.strategy, account_id=p.request.account_id, symbol=p.request.symbol,
                        side=p.request.side, quantity=p.request.quantity, reason=p.request.reason,
                        at=p.request.at.isoformat(), snapshot_revision=p.request.snapshot_revision),
                        broker_order_id=p.broker_order_id) for p in self.unresolved])

    @classmethod
    def from_dict(cls, value: dict) -> "SentinelState":
        pending = []
        for item in value["unresolved"]:
            request = dict(item["request"])
            request["at"] = datetime.fromisoformat(request["at"])
            pending.append(PendingClose(CloseRequest(**request), item["broker_order_id"]))
        return cls(value["strategy"], value["account_id"], value["sequence"], tuple(pending),
                   datetime.fromisoformat(value["last_reconciled_at"]) if value["last_reconciled_at"] else None,
                   datetime.fromisoformat(value["last_heartbeat_at"]) if value["last_heartbeat_at"] else None,
                   value["liquidate_all"], tuple(value["liquidating_symbols"]))


@dataclass(frozen=True)
class Heartbeat:
    strategy: StrategyId
    account_id: str
    at: datetime
    healthy: bool
    reconciliation_revision: str


@dataclass(frozen=True)
class LatencySample:
    request_id: str
    symbol: str
    quote_observation_to_submit_seconds: float | None
    callback_seconds: float


@dataclass(frozen=True)
class SentinelResult:
    entries_blocked: bool
    reasons: tuple[str, ...]
    requests: tuple[CloseRequest, ...]
    latency_samples: tuple[LatencySample, ...]
    risk: RiskState
    reconciled: bool
    heartbeat_written: bool


class AccountMismatch(ValueError):
    pass


class Sentinel:
    def __init__(self, state: SentinelState, *, allowed_accounts: Mapping[str, str],
                 reconcile: Callable[[], Reconciliation],
                 close: Callable[[CloseRequest], str],
                 cancel: Callable[[StrategyId, str], bool],
                 persist_state: Callable[[SentinelState], None],
                 persist_risk: Callable[[RiskState], None],
                 write_heartbeat: Callable[[Heartbeat], None],
                 monotonic: Callable[[], float], calendar: SessionCalendar | None = None):
        self.state = state
        self.allowed_accounts = dict(allowed_accounts)
        self.reconcile = reconcile
        self.close = close
        self.cancel = cancel
        self.persist_state = persist_state
        self.persist_risk = persist_risk
        self.write_heartbeat = write_heartbeat
        self.monotonic = monotonic
        self.calendar = calendar

    def _save(self, state: SentinelState) -> None:
        self.persist_state(state)
        self.state = state

    def _validated_snapshot(self, measured_now: Callable[[], datetime]) -> Reconciliation:
        snapshot = self.reconcile()
        now = measured_now()
        if (not account_allowed(snapshot.account, self.allowed_accounts)
                or (snapshot.account.strategy, snapshot.account.account_id) !=
                (self.state.strategy, self.state.account_id)):
            raise AccountMismatch("Account identity mismatch")
        if (snapshot.complete is not True or not snapshot.revision
                or not fresh(snapshot.at, now, 10) or not fresh(snapshot.positions_at, now, 10)
                or snapshot.positions_at > snapshot.at or not finite(snapshot.nav)):
            raise ValueError("Incomplete or stale reconciliation")
        if self.state.last_reconciled_at is not None and snapshot.at < self.state.last_reconciled_at:
            raise ValueError("Regressive reconciliation")
        own = self._positions(snapshot)
        if len({p.symbol for p in own}) != len(own):
            raise ValueError("Duplicate position rows")
        for p in own:
            if (not _position_valid(p) or not fresh(p.at, now, 10) or p.at > snapshot.positions_at
                    or (self.state.strategy == "orb" and (p.kind != "STOCK" or p.symbol in ("SPY", "US.SPY")))
                    or (self.state.strategy == "noise_area" and (p.symbol not in ('SPY', 'US.SPY') or p.kind != "ETF"))):
                raise ValueError("Unverified position")
        own_symbols = {p.symbol for p in own if p.quantity}
        if any(p.account_id == self.state.account_id and p.strategy != self.state.strategy
               and p.symbol in own_symbols and p.quantity for p in snapshot.positions):
            raise ValueError("Cross-strategy broker netting conflict")
        if any(not _pending_valid(p) for p in self._pending(snapshot)):
            raise ValueError("Unverified working orders")
        unresolved = tuple(p for p in self.state.unresolved if not (
            p.request.request_id in snapshot.resolved_request_ids
            and snapshot.at >= p.request.at and snapshot.positions_at >= p.request.at
            and snapshot.revision != p.request.snapshot_revision
            and not any(o.request_id == p.request.request_id for o in self._pending(snapshot))))
        live_symbols = ({p.symbol for p in own if p.quantity}
                        | {p.symbol for p in self._pending(snapshot)}
                        | {p.request.symbol for p in unresolved})
        self._save(replace(self.state, unresolved=unresolved, last_reconciled_at=snapshot.at,
                           liquidate_all=self.state.liquidate_all and bool(live_symbols),
                           liquidating_symbols=tuple(s for s in self.state.liquidating_symbols if s in live_symbols)))
        return snapshot

    def _positions(self, snapshot: Reconciliation) -> tuple[PositionSnapshot, ...]:
        return tuple(p for p in snapshot.positions if
                     (p.strategy, p.account_id) == (self.state.strategy, self.state.account_id))

    def _pending(self, snapshot: Reconciliation) -> tuple[PendingOrder, ...]:
        return tuple(p for p in snapshot.pending if
                     (p.strategy, p.account_id) == (self.state.strategy, self.state.account_id))

    def tick(self, *, now: datetime, risk: RiskState, quotes: Mapping[str, ObservedQuote],
             feed_at: datetime | None, executor_heartbeat_at: datetime | None) -> SentinelResult:
        reasons: list[str] = []
        requests: list[CloseRequest] = []
        latency: list[LatencySample] = []
        reconciled = False
        heartbeat_written = False
        if risk.strategy != self.state.strategy:
            return SentinelResult(True, ("risk_strategy_mismatch",), (), (), risk, False, False)
        try:
            aware(now)
            clock = self.monotonic()
            if not finite(clock):
                raise ValueError("Invalid monotonic clock")
            opened, closed = get_calendar(self.calendar).session_bounds(now.astimezone(NY).date())
            flatten = get_calendar(self.calendar).flatten_time(now.astimezone(NY).date())
            if not (aware(opened) < aware(flatten) < aware(closed)):
                raise ValueError("Invalid session bounds")
        except Exception:
            return SentinelResult(True, ("invalid_clock_or_session",), (), (), risk, False, False)
        def measured_now() -> datetime:
            elapsed = self.monotonic() - clock
            if not finite(elapsed) or elapsed < 0:
                raise ValueError("Regressive monotonic clock")
            return now + timedelta(seconds=elapsed)

        if not fresh(feed_at, now, 2):
            reasons.append("stale_feed")
        if not fresh(executor_heartbeat_at, now, 5):
            reasons.append("stale_heartbeat")
        if not opened <= now < closed:
            reasons.append("outside_regular_session")
        elif now >= flatten:
            reasons.append("preclose")
        try:
            snapshot = self._validated_snapshot(measured_now)
            reconciled = True
            risk = risk.mark(snapshot.nav, now.astimezone(NY).date())
            self.persist_risk(risk)
            observed_now = measured_now()
            observation_clock = clock + (observed_now - now).total_seconds()
        except AccountMismatch:
            return SentinelResult(True, ("account_mapping",), (), (), risk, False, False)
        except Exception:
            reasons.append("reconciliation_failed")
            try:
                self._save(replace(self.state, liquidate_all=True))
            except Exception:
                reasons.append("liquidation_persistence_failed")
            self._cancel(reasons)
            return SentinelResult(True, tuple(reasons), (), (), risk, False, False)
        if not fresh(feed_at, observed_now, 2):
            reasons.append("stale_feed")
        if not fresh(executor_heartbeat_at, observed_now, 5):
            reasons.append("stale_heartbeat")
        if observed_now >= flatten:
            reasons.append("preclose")
        if risk.runtime in (RuntimeState.RISK_HALTED, RuntimeState.TERMINATED) or risk.daily_locked:
            reasons.append("daily_or_drawdown_halt")
        own = self._positions(snapshot)
        stop_symbols: set[str] = set()
        for p in own:
            if not p.quantity:
                continue
            observed = quotes.get(p.symbol)
            if (observed is None or observed.quote.symbol != p.symbol
                    or not fresh(observed.quote.at, observed_now, 2)
                    or not finite(observed.received_monotonic) or observed.received_monotonic > observation_clock
                    or observation_clock - observed.received_monotonic > 2
                    or not finite(observed.quote.bid) or not finite(observed.quote.ask)
                    or observed.quote.bid <= 0 or observed.quote.ask < observed.quote.bid):
                reasons.append("stale_or_invalid_quote:" + p.symbol)
                continue
            if self.state.strategy == "orb" and p.stop_price is None:
                reasons.append("missing_stop:" + p.symbol)
            elif p.stop_price is not None and ((p.quantity > 0 and observed.quote.bid <= p.stop_price)
                    or (p.quantity < 0 and observed.quote.ask >= p.stop_price)):
                stop_symbols.add(p.symbol)
        if risk.exposure_factor < 1 and own:
            gross = sum(abs(p.quantity) * p.market_price for p in own)
            cap = (0.15 if self.state.strategy == "orb" else 0.4) * risk.nav * risk.exposure_factor
            sectors = {p.sector for p in own}
            sector_breach = self.state.strategy == "orb" and any(
                sum(abs(p.quantity) * p.market_price for p in own if p.sector == s)
                > 0.3 * risk.nav * risk.exposure_factor for s in sectors)
            if gross > risk.nav * risk.exposure_factor or sector_breach or any(
                    abs(p.quantity) * p.market_price > cap for p in own):
                # WHY: full close conservatively reduces exposure without inventing a fresh entry target.
                reasons.append("drawdown_exposure_reduction")
        emergency_all = bool(reasons) or self.state.liquidate_all
        if self.state.liquidate_all or self.state.liquidating_symbols:
            reasons.append("liquidation_in_progress")
        stop_symbols.update(self.state.liquidating_symbols)
        if stop_symbols:
            reasons.extend("stop_loss:" + symbol for symbol in sorted(stop_symbols))
        if reasons:
            try:
                # WHY: partial fills must finish liquidating even if the original alarm clears on restart.
                self._save(replace(self.state, liquidate_all=emergency_all,
                                   liquidating_symbols=tuple(sorted(stop_symbols))))
            except Exception:
                reasons.append("liquidation_persistence_failed")
                self._cancel(reasons)
                return SentinelResult(True, tuple(reasons), (), (), risk, reconciled, False)
            if not self._cancel(reasons):
                return SentinelResult(True, tuple(reasons), (), (), risk, reconciled, False)
            try:
                # WHY: cancellation can race a fill; never close from the pre-cancel quantity.
                snapshot = self._validated_snapshot(measured_now)
                risk = risk.mark(snapshot.nav, now.astimezone(NY).date())
                self.persist_risk(risk)
            except Exception:
                reasons.append("post_cancel_reconciliation_failed")
                return SentinelResult(True, tuple(reasons), (), (), risk, False, False)
            if risk.runtime in (RuntimeState.RISK_HALTED, RuntimeState.TERMINATED) or risk.daily_locked:
                emergency_all = True
            if opened <= now < closed:
                for p in self._positions(snapshot):
                    if not p.quantity or (not emergency_all and p.symbol not in stop_symbols):
                        continue
                    if any(item.request.symbol == p.symbol for item in self.state.unresolved):
                        reasons.append("unresolved_close:" + p.symbol)
                        continue
                    working = [o for o in self._pending(snapshot) if o.symbol == p.symbol]
                    side = "SELL" if p.quantity > 0 else "BUY"
                    if any(not o.close_only or o.side != side for o in working):
                        reasons.append("conflicting_working_order:" + p.symbol)
                        continue
                    quantity = abs(p.quantity) - sum(o.quantity for o in working)
                    if quantity <= 0:
                        continue
                    try:
                        current = measured_now()
                    except Exception:
                        reasons.append("invalid_monotonic_clock")
                        break
                    if (not opened <= current < closed or not fresh(snapshot.at, current, 10)
                            or not fresh(snapshot.positions_at, current, 10) or not fresh(p.at, current, 10)):
                        reasons.append("snapshot_expired_before_close:" + p.symbol)
                        continue
                    sequence = self.state.sequence + 1
                    request = CloseRequest(
                        f"{self.state.strategy}:{current.isoformat()}:{sequence}",
                        self.state.strategy, self.state.account_id, p.symbol, side, quantity,
                        ";".join(reasons), current, snapshot.revision)
                    try:
                        pending = PendingClose(request)
                        # WHY: durable intent precedes a non-idempotent callback, including timeout paths.
                        self._save(replace(self.state, sequence=sequence,
                                           unresolved=(*self.state.unresolved, pending)))
                        submitted = self.monotonic()
                        if not finite(submitted) or submitted < clock:
                            raise ValueError("Regressive monotonic clock")
                        requests.append(request)
                        try:
                            broker_id = self.close(request)
                        finally:
                            completed = self.monotonic()
                            if not finite(completed) or completed < submitted:
                                raise ValueError("Regressive monotonic clock")
                            observed = quotes.get(p.symbol)
                            delay = submitted - observed.received_monotonic if observed is not None else None
                            if delay is not None and (not isfinite(delay) or delay < 0):
                                delay = None
                            latency.append(LatencySample(request.request_id, p.symbol, delay, completed - submitted))
                        if not isinstance(broker_id, str) or not broker_id:
                            raise ValueError("Missing close acknowledgement")
                        self._save(replace(self.state, unresolved=tuple(
                            replace(item, broker_order_id=broker_id) if item.request.request_id == request.request_id
                            else item for item in self.state.unresolved)))
                    except Exception:
                        reasons.append("close_unconfirmed:" + p.symbol)
        try:
            final = self._validated_snapshot(measured_now)
            final_now = measured_now()
            risk = risk.mark(final.nav, final_now.astimezone(NY).date())
            self.persist_risk(risk)
            if self.state.unresolved:
                reasons.append("unresolved_close_requests")
            if (not fresh(feed_at, final_now, 2) or not fresh(executor_heartbeat_at, final_now, 5)):
                reasons.append("health_expired_during_reconciliation")
            if not opened <= final_now < flatten:
                reasons.append("entry_window_closed")
            healthy = not reasons and risk.entries_allowed
            if not risk.entries_allowed:
                reasons.append("risk_state_blocks_entries")
            heartbeat = Heartbeat(self.state.strategy, self.state.account_id, final_now, healthy, final.revision)
            self.write_heartbeat(heartbeat)
            heartbeat_written = True
            self._save(replace(self.state, last_heartbeat_at=final_now))
        except Exception:
            reasons.append("final_reconciliation_or_heartbeat_failed")
            reconciled = False
        return SentinelResult(bool(reasons), tuple(dict.fromkeys(reasons)), tuple(requests), tuple(latency),
                              risk, reconciled, heartbeat_written)

    def _cancel(self, reasons: list[str]) -> bool:
        try:
            if self.allowed_accounts.get(self.state.strategy) != self.state.account_id:
                raise ValueError("Unallowlisted account")
            if self.cancel(self.state.strategy, self.state.account_id) is not True:
                raise ValueError("Unconfirmed cancellation")
            return True
        except Exception:
            reasons.append("cancel_unconfirmed")
            return False
