import json
from dataclasses import replace
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from dual_strategy.models import Quote, Security
from dual_strategy.risk.gate import (
    AccountIdentity, GateContext, OrderIntent, PendingOrder, PositionSnapshot,
    ShortVerification, check_order, size_noise_area, size_orb,
)
from dual_strategy.risk.state_machine import (
    REQUIRED_RELEASE_GATES, GateEvidence, RearmApproval, ReleaseChecklist, RiskState, RuntimeState,
)

NY = ZoneInfo("America/New_York")
NOW = datetime(2026, 11, 17, 10, 0, tzinfo=NY)
DAY = NOW.date()
HASH = "synthetic-config-sha256"


class Calendar:
    def session_bounds(self, day):
        if day == date(2026, 11, 26) or day.weekday() >= 5:
            return None
        return (datetime(day.year, day.month, day.day, 9, 30, tzinfo=NY),
                datetime(day.year, day.month, day.day, 13 if day == date(2026, 11, 27) else 16, tzinfo=NY))

    def flatten_time(self, day):
        return self.session_bounds(day)[1] - timedelta(minutes=5)

    def decision_times(self, day):
        opened, closed = self.session_bounds(day)
        ticks = []
        tick = opened + timedelta(minutes=30)
        while tick < closed:
            ticks.append(tick)
            tick += timedelta(minutes=30)
        return ticks


def release(strategy="orb", shorts=False):
    gates = set(REQUIRED_RELEASE_GATES)
    if strategy == "orb":
        gates.add("orb_adverse_stop_tests")
    if shorts:
        gates.add("practice_short_and_cover_lifecycle")
    return ReleaseChecklist(strategy, HASH, tuple(GateEvidence(
        name, True, NOW - timedelta(minutes=1), HASH, "synthetic:" + name) for name in sorted(gates)), shorts)


def active(strategy="orb"):
    state = RiskState(strategy).mark(50_000, DAY)
    state = state.transition(RuntimeState.PAPER_READY, now=NOW, checklist=release(strategy), config_hash=HASH)
    return state.transition(RuntimeState.PAPER_ACTIVE, now=NOW, checklist=release(strategy), config_hash=HASH)


def context(strategy="orb"):
    symbol, kind = ("XYZ", "STOCK") if strategy == "orb" else ("SPY", "ETF")
    return GateContext(
        now=NOW, account=AccountIdentity("paper-1", strategy, "SIMULATE", "US", "Separate practice", "PRACTICE"),
        allowed_accounts={"orb": "paper-1", "noise_area": "paper-1"}, risk=active(strategy),
        release=release(strategy), config_hash=HASH, cash=50_000, broker_buying_power=100_000,
        broker_reserved_cash=0, positions=(), pending=(), quote=Quote(symbol, NOW, 99.99, 100),
        security=Security(symbol, kind, "TECH", 1e9, DAY), account_at=NOW, positions_at=NOW,
        bar_at=NOW, expected_bar_at=NOW, sentinel_at=NOW, liquidity_cap=1000,
        positions_complete=True, orders_complete=True, sentinel_healthy=True, calendar=Calendar())


def order(strategy="orb", **changes):
    return replace(OrderIntent(strategy, "paper-1", "XYZ" if strategy == "orb" else "SPY", "BUY",
                               75 if strategy == "orb" else 200, 100, NOW,
                               NOW + timedelta(seconds=30), stop_distance=1 if strategy == "orb" else None), **changes)


def position(symbol="XYZ", quantity=50, strategy="orb", **changes):
    return replace(PositionSnapshot("paper-1", strategy, symbol, quantity, 100, "TECH",
                                   "STOCK" if strategy == "orb" else "ETF", NOW, 99), **changes)


def reservation(symbol="OTHER", quantity=10, **changes):
    return replace(PendingOrder("paper-1", "orb", symbol, "BUY", quantity, quantity * 100,
                                quantity * 100, "OTHER"), **changes)


def test_serialization_preserves_daily_baseline_and_terminal_state():
    state = active().mark(49_375, DAY)
    restored = RiskState.from_dict(json.loads(json.dumps(state.to_dict())))
    restored = restored.mark(49_500, DAY)
    assert restored.day_start_nav == 50_000
    assert restored.daily_locked and not restored.entries_allowed
    terminal = restored.mark(47_500, DAY)
    assert terminal.runtime == RuntimeState.TERMINATED
    terminal = RiskState.from_dict(terminal.to_dict()).mark(60_000, DAY + timedelta(days=1))
    assert terminal.runtime == RuntimeState.TERMINATED
    assert terminal.high_water_nav == 60_000
    assert not terminal.entries_allowed


@pytest.mark.parametrize("nav,runtime,reduced", [
    (49_375.01, RuntimeState.PAPER_ACTIVE, False),
    (49_375, RuntimeState.RISK_HALTED, False),
    (49_000.01, RuntimeState.RISK_HALTED, False),
    (49_000, RuntimeState.RISK_HALTED, True),
    (48_250, RuntimeState.RISK_HALTED, True),
    (47_500.01, RuntimeState.RISK_HALTED, True),
    (47_500, RuntimeState.TERMINATED, True),
    (0, RuntimeState.TERMINATED, True),
    (-1, RuntimeState.TERMINATED, True),
])
def test_inclusive_risk_ladder(nav, runtime, reduced):
    state = active().mark(nav, DAY)
    assert state.runtime == runtime
    assert state.reduced == reduced


def test_high_water_is_never_reset_by_session_rollover_or_approval():
    state = active().mark(48_250, DAY).mark(48_501, DAY + timedelta(days=1))
    tomorrow = NOW + timedelta(days=1)
    approval = RearmApproval("orb", tomorrow.date(), tomorrow, HASH, "operator:synthetic")
    ready = state.rearm(approval, now=tomorrow, checklist=release(), config_hash=HASH)
    assert ready.runtime == RuntimeState.PAPER_READY
    assert ready.high_water_nav == 50_000 and ready.day_start_nav == 48_501
    assert not ready.daily_locked
    active_again = ready.transition(RuntimeState.PAPER_ACTIVE, now=tomorrow, checklist=release(), config_hash=HASH)
    assert active_again.mark(48_300, tomorrow.date()).entries_allowed
    assert active_again.mark(48_250, tomorrow.date()).runtime == RuntimeState.RISK_HALTED


@pytest.mark.parametrize("nav", [48_000, 48_250, 48_500])
def test_rearm_rejects_at_or_above_three_percent_drawdown(nav):
    tomorrow = NOW + timedelta(days=1)
    state = active().mark(48_250, DAY).mark(nav, tomorrow.date())
    with pytest.raises(ValueError):
        state.rearm(RearmApproval("orb", tomorrow.date(), tomorrow, HASH, "approval"),
                    now=tomorrow, checklist=release(), config_hash=HASH)
    assert state.high_water_nav == 50_000


def test_new_daily_breach_while_already_halted_relocks_the_current_session():
    tomorrow = NOW + timedelta(days=1)
    state = active().mark(49_375, DAY).mark(50_000, tomorrow.date())
    state = state.mark(49_375, tomorrow.date()).mark(50_000, tomorrow.date())
    assert state.halted_on == tomorrow.date() and state.daily_locked
    with pytest.raises(ValueError):
        state.rearm(RearmApproval("orb", tomorrow.date(), tomorrow, HASH, "approval"),
                    now=tomorrow, checklist=release(), config_hash=HASH)


def test_same_day_rearm_and_implicit_next_day_recovery_forbidden():
    state = active().mark(49_375, DAY).mark(50_000, DAY)
    with pytest.raises(ValueError):
        state.rearm(RearmApproval("orb", DAY, NOW, HASH, "approval"), now=NOW,
                    checklist=release(), config_hash=HASH)
    next_day = state.mark(50_000, DAY + timedelta(days=1))
    assert next_day.runtime == RuntimeState.RISK_HALTED and next_day.daily_locked
    with pytest.raises(ValueError):
        next_day.transition(RuntimeState.SHADOW, now=NOW + timedelta(days=1))


@pytest.mark.parametrize("target", list(RuntimeState))
def test_terminal_dominates_all_transitions_and_rearm(target):
    state = active().mark(47_500, DAY).mark(50_000, DAY + timedelta(days=1))
    with pytest.raises(ValueError):
        state.transition(target, now=NOW + timedelta(days=1), checklist=release(), config_hash=HASH)
    with pytest.raises(ValueError):
        state.rearm(RearmApproval("orb", DAY + timedelta(days=1), NOW + timedelta(days=1), HASH, "approval"),
                    now=NOW + timedelta(days=1), checklist=release(), config_hash=HASH)


def test_reduce_hysteresis_and_strategy_isolation():
    a = active().mark(49_000, DAY)
    b = active("noise_area")
    assert a.exposure_factor == 0.5 and b.exposure_factor == 1
    assert a.mark(49_250, DAY).reduced
    assert not a.mark(49_250.01, DAY).reduced
    with pytest.raises(ValueError):
        a.mark(50_000, DAY - timedelta(days=1))


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), float("-inf"), True])
def test_nonfinite_state_and_sizing_rejected(invalid):
    with pytest.raises(ValueError):
        active().mark(invalid, DAY)
    with pytest.raises(ValueError):
        size_orb(invalid, 100, 1, 100)
    with pytest.raises(ValueError):
        size_noise_area(50_000, invalid)


def test_sizing_formulas_floor_and_liquidity():
    assert size_orb(50_000, 100, 1, 1000) == 75
    assert size_orb(50_000, 100, 2, 1000) == 62
    assert size_orb(50_000, 100, 1, 17.9) == 17
    assert size_orb(50_000, 100, 1, 0) == 0
    assert size_noise_area(50_000, 101) == 198
    for args in [(50_000, 0, 1, 1), (50_000, 100, 0, 1), (50_000, 100, 1, -1)]:
        with pytest.raises(ValueError):
            size_orb(*args)


@pytest.mark.parametrize("gate", sorted(REQUIRED_RELEASE_GATES | {"orb_adverse_stop_tests"}))
def test_each_release_gate_is_mandatory(gate):
    checklist = release()
    missing = replace(checklist, evidence=tuple(e for e in checklist.evidence if e.gate != gate))
    with pytest.raises(ValueError):
        RiskState("orb").mark(50_000, DAY).transition(RuntimeState.PAPER_READY, now=NOW,
                                                    checklist=missing, config_hash=HASH)
    assert not check_order(order(), replace(context(), release=missing)).allowed


@pytest.mark.parametrize("change", [
    {"passed": False}, {"passed": 1}, {"verified_at": NOW + timedelta(microseconds=1)},
    {"verified_at": NOW.replace(tzinfo=None)}, {"config_hash": "other"}, {"evidence_ref": ""},
])
def test_release_evidence_cannot_be_boolean_or_stale_configuration_bypass(change):
    checklist = release()
    bad = replace(checklist, evidence=(replace(checklist.evidence[0], **change), *checklist.evidence[1:]))
    assert bad.failures(NOW, HASH, "orb")
    assert not check_order(order(), replace(context(), release=bad)).allowed


def test_release_roundtrip_duplicates_and_direct_activation():
    checklist = release()
    assert ReleaseChecklist.from_dict(json.loads(json.dumps(checklist.to_dict()))) == checklist
    assert replace(checklist, evidence=(*checklist.evidence, checklist.evidence[0])).failures(NOW, HASH, "orb")
    with pytest.raises(ValueError):
        RiskState("orb").transition(RuntimeState.PAPER_ACTIVE, now=NOW, checklist=True, config_hash=HASH)
    assert not check_order(order(), replace(context(), release=True)).allowed


@pytest.mark.parametrize("strategy", ["orb", "noise_area"])
def test_exact_instrument_cap_is_allowed_but_one_extra_share_is_not(strategy):
    assert check_order(order(strategy), context(strategy)).allowed
    assert "instrument_exposure" in check_order(order(strategy, quantity=order(strategy).quantity + 1),
                                                context(strategy)).reasons


@pytest.mark.parametrize("changes", [
    {"environment": "REAL"}, {"market": "HK"}, {"strategy": "noise_area"}, {"account_id": "other"},
    {"account_type": "COMPETITION"}, {"title": "Official Competition"}, {"title": "AI Challenge"},
    {"title": "Contest paper"}, {"title": ""},
])
def test_account_guard(changes):
    c = context()
    assert not check_order(order(), replace(c, account=replace(c.account, **changes))).allowed


@pytest.mark.parametrize("changes", [
    {"account_id": "other"}, {"strategy": "noise_area"}, {"symbol": "SPY"},
    {"quantity": 0}, {"quantity": -1}, {"quantity": 1.5}, {"quantity": True},
    {"entry_price": float("nan")}, {"estimated_fees": float("inf")}, {"stop_distance": float("nan")},
    {"signal_at": NOW + timedelta(microseconds=1)}, {"signal_at": NOW.replace(tzinfo=None)},
    {"deadline": NOW}, {"deadline": NOW.replace(tzinfo=None)}, {"close_only": 1},
])
def test_invalid_order_fields_fail_closed(changes):
    assert not check_order(order(**changes), context()).allowed


@pytest.mark.parametrize("field,age", [("account_at", 10), ("positions_at", 10), ("bar_at", 60),
                                      ("expected_bar_at", 60), ("sentinel_at", 5)])
def test_freshness_boundaries_future_and_naive(field, age):
    c = context()
    for stamp in [NOW - timedelta(seconds=age, microseconds=1), NOW + timedelta(microseconds=1),
                  NOW.replace(tzinfo=None)]:
        assert not check_order(order(), replace(c, **{field: stamp})).allowed
    changes = {field: NOW - timedelta(seconds=age)}
    if field in ("bar_at", "expected_bar_at"):
        changes = {"bar_at": NOW - timedelta(seconds=age), "expected_bar_at": NOW - timedelta(seconds=age)}
    assert check_order(order(), replace(c, **changes)).allowed


@pytest.mark.parametrize("age,allowed", [(2, True), (2.000001, False), (-0.000001, False)])
def test_quote_age_boundary(age, allowed):
    c = context()
    assert check_order(order(), replace(c, quote=replace(c.quote, at=NOW - timedelta(seconds=age)))).allowed == allowed


@pytest.mark.parametrize("field,value", [("cash", float("nan")), ("broker_buying_power", float("inf")),
                                        ("broker_reserved_cash", -1), ("liquidity_cap", float("inf")),
                                        ("positions_complete", False), ("orders_complete", False),
                                        ("sentinel_healthy", False)])
def test_invalid_or_missing_context(field, value):
    assert not check_order(order(), replace(context(), **{field: value})).allowed


def test_pending_cash_gross_and_broker_buying_power_are_reserved():
    c = replace(context(), pending=(reservation(),), broker_reserved_cash=1000)
    assert check_order(order(), c).allowed
    assert "strategy_cash" in check_order(order(), replace(c, cash=8499.99)).reasons
    assert check_order(order(), replace(c, cash=8500, broker_buying_power=8500)).allowed
    assert "broker_buying_power" in check_order(order(), replace(c, broker_buying_power=8499.99)).reasons
    assert "broker_reservations_incomplete" in check_order(order(), replace(c, broker_reserved_cash=0)).reasons
    c = replace(context(), pending=(reservation(quantity=426),), broker_reserved_cash=42600)
    assert "gross_exposure" in check_order(order(), c).reasons


def test_existing_symbol_sector_and_four_position_limits_include_pending():
    c = replace(context(), positions=(position(quantity=1),))
    assert "instrument_exposure" in check_order(order(), c).reasons
    c = replace(context(), positions=(position("OTHER", 75),))
    assert check_order(order(), c).allowed
    assert "sector_exposure" in check_order(order(), replace(c, positions=(position("OTHER", 76),))).reasons
    positions = tuple(position(str(i), 1, sector="OTHER") for i in range(3))
    c = replace(context(), positions=positions)
    assert check_order(order(), c).allowed
    c = replace(c, pending=(reservation("FOURTH"),), broker_reserved_cash=1000)
    assert "orb_position_count" in check_order(order(), c).reasons


def test_spread_stop_liquidity_risk_and_reduced_exposure():
    c = context()
    assert "stop_not_wider_than_spread" in check_order(order(stop_distance=c.quote.ask - c.quote.bid), c).reasons
    assert "orb_trade_risk" in check_order(order(stop_distance=2), c).reasons
    assert check_order(order(), replace(c, liquidity_cap=75)).allowed
    assert "liquidity_cap" in check_order(order(), replace(c, liquidity_cap=74.999)).reasons
    c = replace(c, risk=replace(c.risk, reduced=True))
    assert "instrument_exposure" in check_order(order(), c).reasons
    assert check_order(order(quantity=37), c).allowed


def test_shorts_require_scoped_fresh_borrow_and_release_evidence():
    c = context()
    short = order(side="SELL")
    assert "short_not_verified" in check_order(short, c).reasons
    verification = ShortVerification("paper-1", "orb", "XYZ", NOW, 75, "synthetic-short-test")
    c = replace(c, short_verification=verification, release=release(shorts=True))
    assert check_order(short, c).allowed
    for changes in [{"borrow_available": 74}, {"account_id": "other"}, {"strategy": "noise_area"},
                    {"verified_at": NOW + timedelta(seconds=1)}, {"lifecycle_evidence_ref": ""}]:
        assert not check_order(short, replace(c, short_verification=replace(verification, **changes))).allowed
    c = replace(c, positions=(position(quantity=1),))
    assert "opposing_entry_requires_close_first" in check_order(short, c).reasons


@pytest.mark.parametrize("strategy,symbol,kind", [("orb", "QQQ", "ETF"), ("orb", "SPY", "STOCK"),
                                                  ("noise_area", "QQQ", "ETF"), ("noise_area", "SPY", "STOCK")])
def test_strategy_instruments_do_not_overlap(strategy, symbol, kind):
    c = context(strategy)
    c = replace(c, security=replace(c.security, symbol=symbol, kind=kind), quote=replace(c.quote, symbol=symbol))
    assert not check_order(order(strategy, symbol=symbol), c).allowed


@pytest.mark.parametrize("quantity,side,allowed", [(50, "SELL", True), (51, "SELL", False), (1, "BUY", False)])
def test_close_only_can_bypass_entry_caps_but_not_reverse(quantity, side, allowed):
    c = replace(context(), positions=(position(),), risk=active().mark(47_000, DAY),
                cash=0, broker_buying_power=0, sentinel_healthy=False, release=ReleaseChecklist("orb", HASH),
                liquidity_cap=0)
    decision = check_order(order(close_only=True, side=side, quantity=quantity), c)
    assert decision.allowed == allowed


@pytest.mark.parametrize("strategy,symbol,kind", [("orb", "SPY", "ETF"), ("noise_area", "XYZ", "STOCK")])
def test_close_cannot_bypass_strategy_instrument_ownership(strategy, symbol, kind):
    c = context(strategy)
    c = replace(c, positions=(position(symbol, 50, strategy, kind=kind),),
                quote=replace(c.quote, symbol=symbol))
    decision = check_order(order(strategy, symbol=symbol, side="SELL", quantity=50, close_only=True), c)
    assert not decision.allowed and "portfolio_instrument_mismatch" in decision.reasons


def test_short_cover_needs_no_new_short_verification():
    c = replace(context(), positions=(position(quantity=-50),))
    assert check_order(order(close_only=True, side="BUY", quantity=50), c).allowed
    assert not check_order(order(close_only=True, side="SELL", quantity=50), c).allowed


def test_close_deducts_pending_closes_and_rejects_invented_stale_mismapped_positions():
    pending = reservation("XYZ", 20, side="SELL", close_only=True, reserved_cash=0, reserved_notional=0)
    c = replace(context(), positions=(position(),), pending=(pending,))
    assert check_order(order(close_only=True, side="SELL", quantity=30), c).allowed
    assert not check_order(order(close_only=True, side="SELL", quantity=31), c).allowed
    for positions in [(), (position(at=NOW - timedelta(seconds=11)),), (position(strategy="noise_area"),),
                      (position(account_id="other"),), (position(quantity=0),), (position(), position())]:
        assert not check_order(order(close_only=True, side="SELL", quantity=1), replace(c, positions=positions)).allowed


def test_closed_bar_mismatch_and_half_hour_ticks():
    c = context("noise_area")
    assert not check_order(order("noise_area", signal_at=NOW - timedelta(seconds=1)), c).allowed
    assert not check_order(order(), replace(context(), expected_bar_at=NOW - timedelta(seconds=1))).allowed


@pytest.mark.parametrize("stamp", [datetime(2026, 11, 17, 9, 29, tzinfo=NY),
                                    datetime(2026, 11, 17, 16, tzinfo=NY),
                                    datetime(2026, 11, 26, 10, tzinfo=NY),
                                    datetime(2026, 11, 27, 12, 55, tzinfo=NY)])
def test_calendar_entry_blocks_rth_holiday_and_inclusive_preclose(stamp):
    c = context()
    c = replace(c, now=stamp, quote=replace(c.quote, at=stamp), account_at=stamp, positions_at=stamp,
                sentinel_at=stamp, bar_at=stamp, expected_bar_at=stamp,
                security=replace(c.security, asof=stamp.date()), risk=replace(c.risk, session=stamp.date()))
    assert not check_order(order(signal_at=stamp, deadline=stamp + timedelta(seconds=30)), c).allowed


def test_real_exchange_calendar_early_close_integration():
    c = replace(context(), calendar=None)
    assert check_order(order(), c).allowed
    from dual_strategy.data.calendar import flatten_time
    assert flatten_time(date(2026, 11, 27)).hour == 12
    assert flatten_time(date(2026, 11, 27)).minute == 55
