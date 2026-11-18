import json
from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from dual_strategy.models import Quote
from dual_strategy.risk.gate import AccountIdentity, PendingOrder
from dual_strategy.risk.sentinel import (
    ObservedQuote, Reconciliation, Sentinel, SentinelState,
)
from dual_strategy.risk.state_machine import RiskState, RuntimeState
from test_risk import Calendar, DAY, NOW, NY, active, position


class Clock:
    def __init__(self):
        self.value = 100.0

    def __call__(self):
        return self.value


class Broker:
    def __init__(self, strategy="orb", positions=None):
        self.strategy = strategy
        self.account = AccountIdentity("paper-1", strategy, "SIMULATE", "US", "Practice only", "PRACTICE")
        self.positions = tuple(positions if positions is not None else [position(quantity=10)])
        self.at = NOW
        self.positions_at = NOW
        self.nav = 50_000
        self.complete = True
        self.revision = 1
        self.pending = ()
        self.resolved = []
        self.calls = []
        self.cancellations = []
        self.states = []
        self.risks = []
        self.heartbeats = []
        self.clock = Clock()
        self.partial = None
        self.timeout = False
        self.cancel_ok = True
        self.reconcile_fail = False
        self.close_hook = None
        self.cancel_hook = None
        self.reconcile_hook = None
        self.reconcile_calls = 0

    def reconcile(self):
        self.reconcile_calls += 1
        if self.reconcile_hook:
            self.reconcile_hook(self)
        if self.reconcile_fail:
            raise ConnectionError("synthetic reconciliation outage")
        return Reconciliation(self.account, self.at, self.positions_at, self.nav, self.positions,
                              self.pending, self.complete, str(self.revision), tuple(self.resolved))

    def cancel(self, strategy, account_id):
        self.cancellations.append((strategy, account_id))
        if self.cancel_hook:
            self.cancel_hook(self)
        return self.cancel_ok

    def close(self, request):
        self.calls.append(request)
        if self.close_hook:
            self.close_hook(self, request)
        self.clock.value += 0.025
        if self.timeout:
            raise TimeoutError("synthetic ambiguous acknowledgement")
        self.at = max(self.at, request.at)
        self.positions_at = self.at
        fill = request.quantity if self.partial is None else min(request.quantity, self.partial)
        updated = []
        for p in self.positions:
            if (p.strategy, p.account_id, p.symbol) == (request.strategy, request.account_id, request.symbol):
                p = replace(p, quantity=p.quantity + (fill if request.side == "BUY" else -fill), at=self.at)
            updated.append(p)
        self.positions = tuple(updated)
        self.resolved.append(request.request_id)
        self.revision += 1
        return "synthetic-order-" + str(self.revision)

    def persist(self, state):
        self.states.append(json.loads(json.dumps(state.to_dict())))

    def sentinel(self, state=None, **overrides):
        kwargs = dict(allowed_accounts={"orb": "paper-1", "noise_area": "paper-1"},
                      reconcile=self.reconcile, close=self.close, cancel=self.cancel,
                      persist_state=self.persist, persist_risk=self.risks.append,
                      write_heartbeat=self.heartbeats.append, monotonic=self.clock,
                      calendar=Calendar())
        kwargs.update(overrides)
        return Sentinel(state or SentinelState(self.strategy, "paper-1"), **kwargs)

    def advance(self, now):
        self.at = self.positions_at = now
        self.positions = tuple(replace(p, at=now) for p in self.positions)
        self.revision += 1
        self.clock.value += 1


def quotes(broker, now=NOW, price=100):
    return {p.symbol: ObservedQuote(Quote(p.symbol, now, price, price + 0.01), broker.clock.value - 0.2)
            for p in broker.positions}


def tick(sentinel, broker, now=NOW, risk=None, **changes):
    args = dict(now=now, risk=risk or active(broker.strategy), quotes=quotes(broker, now),
                feed_at=now, executor_heartbeat_at=now)
    args.update(changes)
    return sentinel.tick(**args)


def test_canonical_spy_symbol_keeps_own_close_scope():
    broker = Broker('noise_area', [position('US.SPY', 1, 'noise_area')])
    result = tick(broker.sentinel(), broker, feed_at=None)
    assert result.entries_blocked
    assert len(broker.calls) == 1
    assert broker.calls[0].symbol == 'US.SPY'
    assert broker.calls[0].quantity == 1
    assert broker.account.account_id not in broker.calls[0].request_id


def test_healthy_reconciliation_writes_heartbeat_without_broker_action():
    broker = Broker()
    result = tick(broker.sentinel(), broker)
    assert not result.entries_blocked and result.reconciled and result.heartbeat_written
    assert broker.heartbeats[-1].healthy
    assert not broker.calls and not broker.cancellations
    assert broker.risks[-1].nav == 50_000


@pytest.mark.parametrize("field", ["feed_at", "executor_heartbeat_at"])
@pytest.mark.parametrize("age", [None, -1, 11])
def test_stale_missing_future_heartbeat_or_feed_closes_fresh_own_positions(field, age):
    broker = Broker(positions=[position(quantity=10), position("SPY", 20, "noise_area")])
    stamp = None if age is None else NOW - timedelta(seconds=age)
    result = tick(broker.sentinel(), broker, **{field: stamp})
    assert result.entries_blocked
    assert [(r.strategy, r.symbol, r.quantity) for r in broker.calls] == [("orb", "XYZ", 10)]
    assert broker.cancellations == [("orb", "paper-1")]
    assert broker.positions[1].quantity == 20
    assert broker.heartbeats[-1].healthy is False


@pytest.mark.parametrize("field,limit", [("feed_at", 2), ("executor_heartbeat_at", 5)])
def test_inclusive_health_freshness_boundaries(field, limit):
    broker = Broker()
    result = tick(broker.sentinel(), broker, **{field: NOW - timedelta(seconds=limit)})
    assert not result.entries_blocked
    broker = Broker()
    assert tick(broker.sentinel(), broker,
                **{field: NOW - timedelta(seconds=limit, microseconds=1)}).entries_blocked
    assert len(broker.calls) == 1


@pytest.mark.parametrize("field", ["at", "positions_at"])
@pytest.mark.parametrize("stamp", [NOW - timedelta(seconds=10, microseconds=1),
                                  NOW + timedelta(microseconds=1), NOW.replace(tzinfo=None)])
def test_stale_naive_future_account_never_invents_closing_quantity(field, stamp):
    broker = Broker()
    setattr(broker, field, stamp)
    result = tick(broker.sentinel(), broker)
    assert result.entries_blocked and not result.reconciled and not result.heartbeat_written
    assert not broker.calls and not broker.heartbeats
    assert broker.cancellations == [("orb", "paper-1")]


def test_naive_now_fails_without_any_callback():
    broker = Broker()
    result = broker.sentinel().tick(now=NOW.replace(tzinfo=None), risk=active(), quotes=quotes(broker),
                                    feed_at=NOW, executor_heartbeat_at=NOW)
    assert result.entries_blocked and not result.heartbeat_written
    assert not broker.calls and not broker.cancellations and broker.reconcile_calls == 0


@pytest.mark.parametrize("quantity,price,stop", [(10, 99, 99), (-10, 98.99, 99)])
def test_stop_loss_is_inclusive_and_closes_the_correct_side(quantity, price, stop):
    broker = Broker(positions=[position(quantity=quantity, stop_price=stop)])
    result = tick(broker.sentinel(), broker, quotes=quotes(broker, price=price))
    assert result.entries_blocked
    request = broker.calls[0]
    assert request.side == ("SELL" if quantity > 0 else "BUY")
    assert request.quantity == abs(quantity)
    assert broker.positions[0].quantity == 0
    assert result.latency_samples[0].callback_seconds == pytest.approx(0.025)
    assert result.latency_samples[0].quote_observation_to_submit_seconds == pytest.approx(0.2)


def test_stop_only_closes_triggered_symbol_not_other_strategy_or_untriggered_stock():
    broker = Broker(positions=[position(quantity=10), position("OTHER", 10, stop_price=95),
                               position("SPY", 20, "noise_area")])
    result = tick(broker.sentinel(), broker, quotes=quotes(broker, price=99))
    assert len(broker.calls) == 1 and broker.calls[0].symbol == "XYZ"
    assert broker.positions[1].quantity == 10 and broker.positions[2].quantity == 20
    assert result.entries_blocked


@pytest.mark.parametrize("quote_map", [None, "future", "naive_mono", "future_mono", "stale_mono", "wrong_symbol"])
def test_invalid_quotes_block_and_close_using_only_fresh_position_snapshot(quote_map):
    broker = Broker()
    observed = quotes(broker)
    if quote_map is None:
        observed = {}
    elif quote_map == "future":
        observed["XYZ"] = replace(observed["XYZ"], quote=replace(observed["XYZ"].quote, at=NOW + timedelta(seconds=1)))
    elif quote_map == "naive_mono":
        observed["XYZ"] = replace(observed["XYZ"], received_monotonic=float("nan"))
    elif quote_map == "future_mono":
        observed["XYZ"] = replace(observed["XYZ"], received_monotonic=101)
    elif quote_map == "stale_mono":
        observed["XYZ"] = replace(observed["XYZ"], received_monotonic=97)
    else:
        observed["XYZ"] = replace(observed["XYZ"], quote=replace(observed["XYZ"].quote, symbol="OTHER"))
    result = tick(broker.sentinel(), broker, quotes=observed)
    assert result.entries_blocked and len(broker.calls) == 1
    assert broker.calls[0].quantity == 10


@pytest.mark.parametrize("nav,expected", [(49_375, RuntimeState.RISK_HALTED),
                                         (48_250, RuntimeState.RISK_HALTED),
                                         (47_500, RuntimeState.TERMINATED)])
def test_nav_triggers_persist_halt_and_cancel_close(nav, expected):
    broker = Broker()
    broker.nav = nav
    result = tick(broker.sentinel(), broker)
    assert result.risk.runtime == expected and result.entries_blocked
    assert len(broker.calls) == 1 and broker.risks[-1].runtime == expected
    assert result.risk.high_water_nav == 50_000


def test_noise_area_monitor_closes_only_b_positions_without_half_hour_tick():
    now = NOW + timedelta(minutes=7)
    broker = Broker("noise_area", [position("SPY", -20, "noise_area", stop_price=None), position(quantity=10)])
    broker.advance(now)
    broker.nav = 49_375
    result = tick(broker.sentinel(), broker, now=now)
    assert result.entries_blocked
    assert [(r.strategy, r.symbol, r.side, r.quantity) for r in broker.calls] == [("noise_area", "SPY", "BUY", 20)]
    assert broker.positions[1].quantity == 10


def test_drawdown_reduction_uses_own_nav_caps_without_resetting_high_water():
    broker = Broker(positions=[position(quantity=75)])
    broker.nav = 49_000
    risk = replace(active(), nav=49_000, day_start_nav=49_000)
    result = tick(broker.sentinel(), broker, risk=risk)
    assert "drawdown_exposure_reduction" in result.reasons
    assert result.risk.high_water_nav == 50_000 and result.risk.reduced
    assert len(broker.calls) == 1


def test_missing_orb_stop_is_unhealthy():
    broker = Broker(positions=[position(stop_price=None)])
    result = tick(broker.sentinel(), broker)
    assert "missing_stop:XYZ" in result.reasons and broker.calls


@pytest.mark.parametrize("stamp,closes", [(datetime(2026, 11, 17, 15, 55, tzinfo=NY), True),
                                         (datetime(2026, 11, 27, 12, 55, tzinfo=NY), True),
                                         (datetime(2026, 11, 27, 13, 0, tzinfo=NY), False)])
def test_preclose_uses_exchange_calendar_and_never_trades_after_close(stamp, closes):
    broker = Broker()
    broker.advance(stamp)
    result = tick(broker.sentinel(), broker, now=stamp)
    assert result.entries_blocked and bool(broker.calls) == closes
    assert broker.cancellations == [("orb", "paper-1")]


def test_partial_close_reconciles_then_closes_only_remaining_shares():
    broker = Broker()
    broker.partial = 4
    sentinel = broker.sentinel()
    first = tick(sentinel, broker, feed_at=None)
    assert first.requests[0].quantity == 10 and broker.positions[0].quantity == 6
    assert not sentinel.state.unresolved
    restored = SentinelState.from_dict(json.loads(json.dumps(sentinel.state.to_dict())))
    broker.partial = None
    broker.advance(NOW + timedelta(seconds=1))
    second = tick(broker.sentinel(restored), broker, now=broker.at, feed_at=None, risk=first.risk)
    assert second.requests[0].quantity == 6 and broker.positions[0].quantity == 0
    assert second.requests[0].request_id != first.requests[0].request_id


def test_partial_liquidation_latches_across_restart_after_alarm_clears():
    broker = Broker()
    broker.partial = 4
    sentinel = broker.sentinel()
    first = tick(sentinel, broker, feed_at=None)
    assert sentinel.state.liquidate_all and broker.positions[0].quantity == 6
    restored = SentinelState.from_dict(sentinel.state.to_dict())
    broker.partial = None
    broker.advance(NOW + timedelta(seconds=1))
    restarted = broker.sentinel(restored)
    second = tick(restarted, broker, now=broker.at, risk=first.risk)
    assert second.entries_blocked and second.requests[0].quantity == 6
    assert not restarted.state.liquidate_all and broker.positions[0].quantity == 0
    broker.advance(NOW + timedelta(seconds=2))
    assert not tick(restarted, broker, now=broker.at, risk=second.risk).entries_blocked


def test_partial_stop_remains_latched_when_quote_recovers():
    broker = Broker()
    broker.partial = 4
    sentinel = broker.sentinel()
    first = tick(sentinel, broker, quotes=quotes(broker, price=99))
    assert sentinel.state.liquidating_symbols == ("XYZ",)
    broker.advance(NOW + timedelta(seconds=1))
    broker.partial = None
    second = tick(sentinel, broker, now=broker.at, risk=first.risk)
    assert second.requests[0].quantity == 6 and not sentinel.state.liquidating_symbols


def test_timeout_is_durable_and_never_blindly_retried_after_restart():
    broker = Broker()
    broker.timeout = True
    sentinel = broker.sentinel()
    first = tick(sentinel, broker, feed_at=None)
    assert first.entries_blocked and len(broker.calls) == 1
    restored = SentinelState.from_dict(broker.states[-1])
    assert len(restored.unresolved) == 1
    assert broker.states[1]["unresolved"] == []
    assert any(s["unresolved"] for s in broker.states)
    broker.advance(NOW + timedelta(seconds=1))
    restarted = broker.sentinel(restored)
    second = tick(restarted, broker, now=broker.at, feed_at=None)
    assert len(broker.calls) == 1 and second.entries_blocked
    assert "unresolved_close:XYZ" in second.reasons
    broker.timeout = False
    broker.resolved.append(first.requests[0].request_id)
    broker.advance(NOW + timedelta(seconds=2))
    third = tick(restarted, broker, now=broker.at, feed_at=None)
    assert len(broker.calls) == 2 and third.requests[0].quantity == 10


def test_working_partial_close_is_not_double_counted_or_reversed():
    broker = Broker()
    broker.pending = (PendingOrder("paper-1", "orb", "XYZ", "SELL", 4, 0, 0, "TECH", True, "previous"),)
    result = tick(broker.sentinel(), broker, feed_at=None)
    assert result.requests[0].quantity == 6


def test_conflicting_or_oversized_working_orders_prevent_close():
    broker = Broker()
    broker.pending = (PendingOrder("paper-1", "orb", "XYZ", "BUY", 4, 400, 400, "TECH"),)
    result = tick(broker.sentinel(), broker, feed_at=None)
    assert not broker.calls and "conflicting_working_order:XYZ" in result.reasons
    broker.pending = (PendingOrder("paper-1", "orb", "XYZ", "SELL", 11, 0, 0, "TECH", True),)
    assert not tick(broker.sentinel(), broker, feed_at=None).requests


def test_cancellation_fill_race_uses_post_cancel_reconciled_quantity():
    broker = Broker()
    def partial_fill(b):
        b.positions = (replace(b.positions[0], quantity=3),)
        b.revision += 1
    broker.cancel_hook = partial_fill
    result = tick(broker.sentinel(), broker, feed_at=None)
    assert result.requests[0].quantity == 3


def test_unconfirmed_cancellation_or_stale_post_cancel_snapshot_never_closes():
    broker = Broker()
    broker.cancel_ok = False
    result = tick(broker.sentinel(), broker, feed_at=None)
    assert not broker.calls and not result.heartbeat_written
    broker = Broker()
    def stale(b):
        b.positions_at = NOW - timedelta(seconds=11)
    broker.cancel_hook = stale
    result = tick(broker.sentinel(), broker, feed_at=None)
    assert not broker.calls and not result.heartbeat_written
    assert "post_cancel_reconciliation_failed" in result.reasons


def test_monotonic_callback_delay_expires_position_snapshot_before_close():
    broker = Broker()
    def slow_cancel(b):
        b.clock.value += 11
    broker.cancel_hook = slow_cancel
    result = tick(broker.sentinel(), broker, feed_at=None)
    assert not result.requests and not result.heartbeat_written
    assert "post_cancel_reconciliation_failed" in result.reasons


def test_failed_final_reconciliation_never_writes_heartbeat():
    broker = Broker()
    def break_reconciliation(b, request):
        b.reconcile_fail = True
    broker.close_hook = break_reconciliation
    result = tick(broker.sentinel(), broker, feed_at=None)
    assert len(broker.calls) == 1 and not result.heartbeat_written
    assert not broker.heartbeats and result.entries_blocked


@pytest.mark.parametrize("changes", [{"environment": "REAL"}, {"account_type": "COMPETITION"},
                                    {"title": "Challenge"}, {"account_id": "other"}, {"strategy": "noise_area"}])
def test_bad_account_mapping_never_invokes_cancel_or_close(changes):
    broker = Broker()
    broker.account = replace(broker.account, **changes)
    result = tick(broker.sentinel(), broker)
    assert result.entries_blocked and not broker.calls and not broker.cancellations


def test_no_cross_strategy_netting_or_wrong_instrument_close():
    broker = Broker(positions=[position(), position("XYZ", 10, "noise_area", kind="STOCK")])
    result = tick(broker.sentinel(), broker, feed_at=None)
    assert result.entries_blocked and not broker.calls
    broker = Broker(positions=[position("SPY", 10, "orb", kind="ETF")])
    assert tick(broker.sentinel(), broker, feed_at=None).entries_blocked and not broker.calls


def test_persistence_failure_prevents_submission():
    broker = Broker()
    def fail_pending(state):
        if state.unresolved:
            raise OSError("synthetic persistence outage")
    result = tick(broker.sentinel(persist_state=fail_pending), broker, feed_at=None)
    assert result.entries_blocked and not broker.calls


def test_invalid_and_incomplete_snapshots_do_not_write_heartbeat():
    for changes in [{"complete": False}, {"nav": float("nan")}, {"positions": (position(quantity=1.5),)},
                    {"positions": (position(), position())}, {"reconcile_fail": True}]:
        broker = Broker()
        for name, value in changes.items():
            setattr(broker, name, value)
        result = tick(broker.sentinel(), broker)
        assert result.entries_blocked and not result.heartbeat_written and not broker.calls


def test_restored_terminal_state_never_rearms_on_new_day_or_normal_nav():
    tomorrow = NOW + timedelta(days=1)
    broker = Broker()
    broker.advance(tomorrow)
    risk = active().mark(47_500, DAY)
    risk = RiskState.from_dict(risk.to_dict())
    result = tick(broker.sentinel(), broker, now=tomorrow, risk=risk)
    assert result.risk.runtime == RuntimeState.TERMINATED and result.entries_blocked
    assert result.risk.high_water_nav == 50_000 and broker.calls


def test_mismatched_risk_never_touches_broker():
    broker = Broker()
    result = tick(broker.sentinel(), broker, risk=active("noise_area"))
    assert result.entries_blocked and broker.reconcile_calls == 0 and not broker.calls
