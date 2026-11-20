"""Competition portfolio: B3/H signals, account risk ladder, sleeve sizing and the execution gates."""

import json
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import pytest

from dual_strategy.competition import planner
from dual_strategy.competition.broker import CompetitionBroker
from dual_strategy.competition.signals import Bar5, b3_decision, b3_decision_times, h_plan, rsi2
from dual_strategy.competition.store import Store, load_config
from dual_strategy.data.calendar import NY
from dual_strategy.execution.moomoo_paper import ExecutionBlocked
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
CFG = load_config(PROJECT)


def session(day, close_hour=16):
    o = datetime(day.year, day.month, day.day, 9, 30, tzinfo=NY)
    return o, datetime(day.year, day.month, day.day, close_hour, 0, tzinfo=NY)


def bars(day, path, vol=1000.0):
    """5-minute bars whose closes follow `path` (one value per bar), open of first bar = path[0]."""
    o, _ = session(day)
    out, prev = [], path[0]
    for k, c in enumerate(path):
        out.append(Bar5(o + timedelta(minutes=5 * (k + 1)), prev, max(prev, c), min(prev, c), c, vol, vol * c))
        prev = c
    return out


def prior_days(n=14, move=0.002):
    days, d = {}, date(2026, 10, 26)
    while len(days) < n:
        if d.weekday() < 5:
            days[d] = bars(d, [100.0] * 5 + [100.0 * (1 + move)] + [100.0] * 72)   # bar ending 10:00 = slot 5
        d += timedelta(days=1)
    return days


def test_b3_long_when_close_above_band_and_vwap():
    day = date(2026, 11, 20)
    o, c = session(day)
    prior = prior_days()                                  # noise at 10:00 = 0.2%
    today = bars(day, [100.0, 100.1, 100.2, 100.3, 100.35, 100.40])
    d = b3_decision(today, prior, prev_close=100.0, dividend_today=0.0, at=o + timedelta(minutes=30), session_open=o)
    assert d.target == 1 and abs(d.metrics["noise"] - 0.002) < 1e-12 and abs(d.metrics["upper"] - 100.2) < 1e-9


def test_b3_flat_inside_band_and_blocked_on_missing_bar():
    day = date(2026, 11, 20)
    o, _ = session(day)
    prior = prior_days()
    flat = b3_decision(bars(day, [100.0, 100.0, 100.05, 100.1, 100.1, 100.15]), prior, 100.0, 0.0, o + timedelta(minutes=30), o)
    assert flat.target == 0
    gappy = bars(day, [100.0, 100.1, 100.2, 100.3, 100.35, 100.40])
    del gappy[2]
    assert b3_decision(gappy, prior, 100.0, 0.0, o + timedelta(minutes=30), o).target is None
    assert b3_decision(bars(day, [100.0] * 6), dict(list(prior.items())[:10]), 100.0, 0.0, o + timedelta(minutes=30), o).reason == "insufficient_noise_history"


def test_b3_decision_times_half_day():
    o, c = session(date(2026, 11, 27), close_hour=13)
    assert [t.strftime("%H:%M") for t in b3_decision_times(o, c, CFG["b3"]["decision_times"])] == ["10:00", "11:00", "12:00"]
    o, c = session(date(2026, 11, 23))
    assert len(b3_decision_times(o, c, CFG["b3"]["decision_times"])) == 6


def acct(nav=100_000.0, positions=None, quotes=None):
    positions = positions or {}
    quotes = {"US.SPY": 500.0, **(quotes or {})}
    cash = nav - sum(q * quotes[s] for s, q in positions.items())
    return planner.Account(datetime.now(timezone.utc), cash, positions, quotes, {s: 1.0 for s in quotes})


def test_risk_ladder_halt_is_permanent_and_lock_is_daily():
    st = planner.new_state()
    assert planner.mark_risk(st, acct(100_000), "2026-11-20", CFG) is None and st["high_water"] == 100_000
    assert planner.mark_risk(st, acct(97_900), "2026-11-20", CFG) == "daily_loss_lock" and st["reduced"]
    assert planner.entry_block(st, acct(97_900), "2026-11-20", CFG) == "daily_loss_lock"
    assert planner.mark_risk(st, acct(97_900), "2026-11-23", CFG) is None            # new session clears the lock
    assert planner.entry_block(st, acct(97_900), "2026-11-23", CFG) is None
    assert planner.mark_risk(st, acct(96_490), "2026-11-23", CFG) == "drawdown_halt"
    assert planner.mark_risk(st, acct(99_000), "2026-11-24", CFG) is None            # flat: nothing to close
    assert st["halted"] is not None and "drawdown_halt" in planner.entry_block(st, acct(99_000), "2026-11-24", CFG)
    st2 = planner.new_state()
    planner.mark_risk(st2, acct(100_000), "d", CFG)
    planner.mark_risk(st2, acct(97_990), "e", CFG)
    assert st2["reduced"]
    planner.mark_risk(st2, acct(98_400), "e", CFG)
    assert st2["reduced"]                                                            # hysteresis: needs > 98.5%
    planner.mark_risk(st2, acct(98_600), "e", CFG)
    assert not st2["reduced"]


class Decision:
    def __init__(self, target, at=datetime(2026, 11, 20, 10, 0, tzinfo=NY), open_0930=499.0):
        self.target, self.at, self.reason, self.metrics = target, at, "x", {"open_0930": open_0930}


def test_b3_sizing_from_config_with_cap_and_half_size():
    st = planner.new_state()
    planner.mark_risk(st, acct(), "2026-11-20", CFG)
    intents, why = planner.plan_b3(st, acct(), "2026-11-20", Decision(1), CFG, datetime.now(NY))
    i = intents[0]
    assert why == "entry" and i.side == "BUY" and i.limit_price == round(500 * 1.0005, 2)
    frac = CFG["allocation"]["b3"] * CFG["b3"]["position_fraction_of_sleeve"]
    cap = CFG["risk"]["name_cap_usd"]
    assert i.qty == int(min(frac * 100_000, cap) // i.limit_price)
    st["reduced"] = True
    half = planner.plan_b3(st, acct(), "2026-11-20", Decision(1), CFG, datetime.now(NY))[0][0].qty
    assert half == int(min(frac * 100_000 * 0.5, cap) // i.limit_price)


def test_b3_exit_allowed_but_entry_blocked_when_halted():
    st = planner.new_state()
    planner.mark_risk(st, acct(), "2026-11-20", CFG)
    st["halted"] = {"reason": "test"}
    assert planner.plan_b3(st, acct(), "2026-11-20", Decision(1), CFG, datetime.now(NY))[0] == []
    st["sleeves"]["b3"]["US.SPY"] = 60
    out, _ = planner.plan_b3(st, acct(positions={"US.SPY": 60}), "2026-11-20", Decision(0), CFG, datetime.now(NY))
    assert out[0].side == "SELL" and out[0].qty == 60 and out[0].close_only


def test_shared_spy_is_attributed_per_sleeve():
    st = planner.new_state()
    st["sleeves"]["b3"]["US.SPY"] = 60
    st["sleeves"]["h_etf"]["US.SPY"] = 4
    a = acct(positions={"US.SPY": 64})
    planner.mark_risk(st, a, "2026-11-20", CFG)
    assert planner.attribution_errors(st, a) == []
    assert "attribution" in planner.entry_block(st, acct(positions={"US.SPY": 63}), "2026-11-20", CFG)
    out = planner.plan_flatten(st, a, "2026-11-20", "x", CFG, datetime.now(NY), sleeves=("b3",))
    assert [(i.symbol, i.qty) for i in out] == [("US.SPY", 60)]


def test_h_open_limits_and_exits_first():
    st = planner.new_state()
    st["sleeves"]["h_etf"] = {"US.XLK": 10}
    st["h_entry_session"] = {"US.XLK": "2026-11-18"}
    quotes = {"US.XLK": 200.0, "US.IWM": 100.0, "US.QQQ": 400.0, "US.XLF": 50.0}
    a = acct(positions={"US.XLK": 10}, quotes=quotes)
    planner.mark_risk(st, a, "2026-11-20", CFG)
    plan = {"exits": {"US.XLK": "max_hold"}, "entries": ["US.IWM", "US.QQQ", "US.XLF", "US.SPY"]}
    out, _ = planner.plan_h_open(st, a, "2026-11-20", plan, CFG, datetime.now(NY))
    assert out[0].side == "SELL" and out[0].symbol == "US.XLK"
    buys = [i for i in out if i.side == "BUY"]
    assert all(i.qty * i.limit_price <= 0.10 * 0.20 * a.nav + 1e-6 for i in buys)
    assert sum(i.qty * i.limit_price for i in buys) <= 0.30 * 0.20 * a.nav + 1e-6
    assert [(i.symbol, i.qty) for i in buys] == [("US.IWM", 19), ("US.QQQ", 4), ("US.XLF", 39), ("US.SPY", 1)]  # room left


def test_apply_fill_tracks_h_entry_session():
    st = planner.new_state()
    i = planner.Intent("x", "t", "h_etf", "US.XLK", "BUY", 10, 200.0, 200.0, "r", False)
    planner.apply_fill(st, i, 10, "2026-11-20")
    assert st["sleeves"]["h_etf"]["US.XLK"] == 10 and st["h_entry_session"]["US.XLK"] == "2026-11-20"
    planner.apply_fill(st, planner.Intent("y", "t", "h_etf", "US.XLK", "SELL", 10, 1, 1, "r", True), 10, "2026-11-23")
    assert "US.XLK" not in st["sleeves"]["h_etf"] and "US.XLK" not in st["h_entry_session"]


def test_h_plan_rsi_trend_universe_and_max_hold():
    days = pd.bdate_range("2026-03-02", periods=130).date.tolist()
    cfg = {**CFG["h_etf"], "core": ["A"], "sectors": ["S1", "S2"], "top_sectors": 1}
    up = [100 + 0.5 * k for k in range(130)]
    dip = up[:-2] + [up[-3] - 3, up[-3] - 6]                 # two sharp down closes in an uptrend: RSI2 ~ 0
    daily = {"A": pd.DataFrame({"close": dip, "volume": 1e6, "adjusted": dip}, index=days),
             "S1": pd.DataFrame({"close": up, "volume": 2e6, "adjusted": up}, index=days),
             "S2": pd.DataFrame({"close": up, "volume": 1e6, "adjusted": up}, index=days)}
    p = h_plan(daily, days[-1], {"S1": days[-3]}, days, cfg)
    assert p.universe == ["A", "S1"] and p.entries == ["A"] and p.exits == {"S1": "rsi_recovery"}
    assert rsi2([1, 2, 3])[2] == 100.0


def test_broker_refuses_unless_all_three_locks_open(tmp_path):
    cfg = {**CFG, "account_file": "private/competition/account.json"}
    with pytest.raises(ExecutionBlocked):
        CompetitionBroker(tmp_path, cfg, execute=False)
    with pytest.raises(ExecutionBlocked, match="execution_enabled"):
        CompetitionBroker(tmp_path, {**cfg, "execution_enabled": False}, execute=True)
    on = {**cfg, "execution_enabled": True}
    with pytest.raises(ExecutionBlocked, match="missing"):
        CompetitionBroker(tmp_path, on, execute=True)
    f = tmp_path / "private" / "competition" / "account.json"
    f.parent.mkdir(parents=True)
    f.write_text(json.dumps({"account_id": "X1", "account_type": 1, "market_id": 100, "confirmed_competition_account": True}))
    with pytest.raises(ExecutionBlocked, match="competition account"):
        CompetitionBroker(tmp_path, on, execute=True)
    f.write_text(json.dumps({"account_id": "X1", "account_type": 2, "market_id": 100, "confirmed_competition_account": True}))
    listed = lambda t, a: {"data": {"accounts": [{"account_id": "X1", "account_type": 1}]}}
    with pytest.raises(ExecutionBlocked, match="uniquely listed"):
        CompetitionBroker(tmp_path, on, execute=True, transport=listed)
    calls = []

    def fake(tool, args):
        calls.append(tool)
        if tool.endswith("account_list"):
            return {"data": {"accounts": [{"account_id": "X1", "account_type": 2}]}}
        return {"data": {"order_id": "77"}}
    b = CompetitionBroker(tmp_path, on, execute=True, transport=fake)
    with pytest.raises(ExecutionBlocked, match="close-only"):
        b.place(planner.Intent("i", "t", "b3", "US.SPY", "SELL", 1, 1.0, 1.0, "r", False))
    assert b.place(planner.Intent("i", "t", "b3", "US.SPY", "BUY", 1, 1.0, 1.0, "r", False)) == "77"


def test_config_ships_disabled_and_split_80_20():
    assert CFG["execution_enabled"] is False
    assert CFG["allocation"] == {"b3": 0.80, "h_etf": 0.20}
    assert CFG["risk"]["halt_drawdown_fraction"] == 0.035


def test_store_is_atomic_and_append_only(tmp_path):
    s = Store(tmp_path, {"state_dir": "st"})
    st = s.load()
    st["high_water"] = 1.0
    s.save(st)
    s.append("2026-11-20", {"event": "intent", "id": 1})
    s.append("2026-11-20", {"event": "outcome", "id": 1})
    assert s.load()["high_water"] == 1.0 and [r["event"] for r in s.outbox("2026-11-20")] == ["intent", "outcome"]


def test_dashboard_payload_and_store_reads_create_nothing(tmp_path):
    import shutil
    from dual_strategy.competition.report import competition_payload
    (tmp_path / "config").mkdir()
    shutil.copy(PROJECT / "config" / "competition.yaml", tmp_path / "config" / "competition.yaml")
    payload = competition_payload(tmp_path)
    assert payload["live"] is None and payload["plan"] is None and payload["outbox"] == []
    s = Store(tmp_path, {"state_dir": "private/competition/plan"})
    s.load(), s.outbox("2026-11-20"), s.latest_session()
    assert not (tmp_path / "private").exists()


def test_spy_cap_is_per_symbol_across_sleeves_and_b3_is_80pct():
    assert CFG["allocation"]["b3"] * CFG["b3"]["position_fraction_of_sleeve"] == pytest.approx(0.80)
    st = planner.new_state()
    planner.mark_risk(st, acct(), "2026-11-20", CFG)
    i = planner.plan_b3(st, acct(), "2026-11-20", Decision(1), CFG, datetime.now(NY))[0][0]
    assert i.qty * i.limit_price <= CFG["risk"]["name_cap_usd"] and i.qty == int(40_000 // i.limit_price)
    st["sleeves"]["b3"]["US.SPY"] = 79
    a = acct(positions={"US.SPY": 79})
    out, _ = planner.plan_h_open(st, a, "2026-11-20", {"exits": {}, "entries": ["US.SPY"]}, CFG, datetime.now(NY))
    assert out == [] or out[0].qty * out[0].limit_price + 79 * 500 <= CFG["risk"]["name_cap_usd"] + 1e-6
