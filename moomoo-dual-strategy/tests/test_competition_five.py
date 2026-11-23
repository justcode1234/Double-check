"""Five-strategy competition runner (QODER_FIVE_STRATEGIES.md, commit eca98ae, section 6): $20,000 caps, the shared
per-symbol cap, shorts, the VWAP/ORB stop/target/trail monitor, Noise-area target changes, halts with shorts, signed
attribution, and parity of the live Noise-area/VWAP/ORB code with reports/claude_regime/rstrats.py."""

import math
import runpy
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from dual_strategy.competition import intraday as I
from dual_strategy.competition import planner
from dual_strategy.competition.broker import CompetitionBroker, parse_positions
from dual_strategy.competition.signals import Bar5
from dual_strategy.competition.store import Store, load_config, sleeve_performance
from dual_strategy.data.calendar import NY
from dual_strategy.execution.moomoo_paper import ExecutionBlocked

PROJECT = Path(__file__).resolve().parents[1]
CFG = load_config(PROJECT)
CFG["risk"]["legacy_exit_only"] = False  # Exercise the original entry and sizing rules.
NOW = datetime(2026, 11, 23, 11, 0, tzinfo=NY)
SESSION = "2026-11-23"


def acct(nav=100_000.0, positions=None, quotes=None, total=None):
    positions = positions or {}
    quotes = {"US.SPY": 500.0, "US.QQQ": 400.0, "US.IWM": 200.0, **(quotes or {})}
    cash = nav - sum(q * quotes[s] for s, q in positions.items())
    return planner.Account(datetime.now(timezone.utc), cash, positions, quotes, {s: 1.0 for s in quotes},
                           {s: 20_000.0 for s in planner.SLEEVES}, broker_total=total)


def state(**books):
    st = planner.new_state()
    for k, v in books.items():
        st["sleeves"][k] = dict(v)
    planner.mark_risk(st, acct(), SESSION, CFG)
    return st


def test_five_sleeves_named_not_numbered():
    assert planner.SLEEVES == ("b3", "h_etf", "noise", "vwap", "orb")
    assert CFG["allocation"] == {s: 0.20 for s in planner.SLEEVES}
    assert CFG["risk"]["short_sleeves"] == ["noise", "vwap", "orb"] and CFG["risk"]["long_only"] is False
    assert "fbr" not in CFG and "pairs" not in CFG


# ------------------------------------------------------------------ $20,000 caps
def test_each_sleeve_starts_at_20k_and_moves_only_with_its_own_pnl():
    st = {**state(), "initial_nav": 100_000.0}
    perf = sleeve_performance(Store(PROJECT, {"state_dir": "nonexistent-state"}), st, asdict(acct()), CFG)
    assert {s: v["initial_capital"] for s, v in perf["sleeves"].items()} == {s: 20_000.0 for s in planner.SLEEVES}


def test_noise_area_trades_its_whole_20k_sleeve():
    st = state()
    out, why = planner.plan_target(st, acct(), SESSION, "noise", "US.SPY", 1, 498.0, 500.0, "noise_long", CFG, NOW, "1030")
    assert why == "entry" and out[0].qty == math.floor(20_000 / out[0].limit_price)


def test_orb_trade_never_exceeds_10k_and_vwap_never_exceeds_its_sleeve():
    st = state()
    out, _ = planner.plan_entry(st, acct(), SESSION, "orb", "US.IWM", 1, 10_000, 200.0, "orb_long", CFG, NOW, "e4")
    assert out[0].qty * out[0].limit_price <= 10_000 + 1e-6
    out, _ = planner.plan_entry(st, acct(), SESSION, "vwap", "US.QQQ", 1, 10_000, 400.0, "vwap_long", CFG, NOW, "e9")
    assert out[0].qty * out[0].limit_price <= 20_000 + 1e-6


def test_half_size_halves_risk_qty_and_notional():
    st = state()
    st["reduced"] = True
    out, _ = planner.plan_entry(st, acct(), SESSION, "vwap", "US.SPY", 1, 30, 500.0, "vwap_long", CFG, NOW, "e9")
    assert out[0].qty == 15
    out, _ = planner.plan_target(st, acct(), SESSION, "noise", "US.SPY", 1, 500.0, 500.0, "noise_long", CFG, NOW, "1030")
    assert out[0].qty == math.floor(10_000 / out[0].limit_price)


# ------------------------------------------------------------------ shared per-symbol cap
def test_spy_cap_is_shared_first_come_across_all_five_sleeves():
    st = state(b3={"US.SPY": 39}, h_etf={"US.SPY": 10}, vwap={"US.SPY": -11})        # 60 x $500 = $30,000 used
    a = acct(positions={"US.SPY": 38})
    assert planner.exposure(st, "US.SPY", 500.0) == 30_000
    out, why = planner.plan_target(st, a, SESSION, "noise", "US.SPY", 1, 500.0, 500.0, "noise_long", CFG, NOW, "1030")
    assert why == "entry" and out[0].qty * 500.0 <= 10_000                               # gets the remainder
    st["sleeves"]["orb"] = {"US.SPY": -20}
    out, why = planner.plan_target(st, acct(positions={"US.SPY": 18}), SESSION, "noise", "US.SPY", 1, 500.0, 500.0,
                                   "noise_long", CFG, NOW, "1130")
    assert out == [] and why == "entry size zero"                                       # cap used up: skip


def test_b3_counts_noise_area_spy_toward_the_cap():
    st = state(noise={"US.SPY": -70})
    dec = SimpleNamespace(target=1, at=NOW, reason="long", metrics={"open_0930": 500.0})
    out, why = planner.plan_b3(st, acct(positions={"US.SPY": -70}), SESSION, dec, CFG, NOW)
    assert why == "entry" and out[0].qty * out[0].limit_price <= 5_000 + 1e-6


# ------------------------------------------------------------------ shorts
def test_shorts_only_for_noise_vwap_and_orb():
    st = state()
    for sleeve in ("b3", "h_etf"):
        out, why = planner.plan_entry(st, acct(), SESSION, sleeve, "US.SPY", -1, 10, 500.0, "x", CFG, NOW, "e1")
        assert out == [] and "may not short" in why
    for sleeve in ("vwap", "orb"):
        out, why = planner.plan_entry(st, acct(), SESSION, sleeve, "US.SPY", -1, 10, 500.0, "x", CFG, NOW, "e1")
        assert why == "entry" and out[0].side == "SELL" and out[0].opens_short and not out[0].close_only
    out, why = planner.plan_target(st, acct(), SESSION, "noise", "US.SPY", -1, 500.0, 500.0, "noise_short", CFG, NOW, "1030")
    assert why == "entry" and out[0].opens_short


def test_broker_gate_refuses_shorts_outside_the_short_sleeves():
    b = CompetitionBroker.__new__(CompetitionBroker)
    b.acc_id, b._last, b.cfg = "T", 0.0, CFG
    b.transport = lambda tool, args: {"data": {"order_id": "1"}}
    mk = lambda sleeve, side, short, close: planner.Intent("i", "t", sleeve, "US.SPY", side, 1, 1.0, 1.0, "r", close,
                                                           opens_short=short)
    for sleeve in ("b3", "h_etf", "fbr", "pairs"):
        with pytest.raises(ExecutionBlocked):
            b.place(mk(sleeve, "SELL", True, False))
    with pytest.raises(ExecutionBlocked):
        b.place(mk("vwap", "SELL", False, False))
    with pytest.raises(ExecutionBlocked):
        b.place(mk("orb", "BUY", True, False))
    assert all(b.place(mk(s, "SELL", True, False)) == "1" for s in ("noise", "vwap", "orb"))
    assert b.place(mk("b3", "SELL", False, True)) == "1" and b.place(mk("orb", "BUY", False, True)) == "1"
    no_cfg = CompetitionBroker.__new__(CompetitionBroker)
    no_cfg.acc_id, no_cfg._last, no_cfg.transport = "T", 0.0, b.transport
    with pytest.raises(ExecutionBlocked):
        no_cfg.place(mk("vwap", "SELL", True, False))


def test_short_collateral_is_reserved_in_cash_checks():
    a = acct(nav=100_000.0, positions={"US.QQQ": -200})                         # $80,000 short: cash 180,000
    assert a.cash == 180_000 and planner.cash_room(a) == pytest.approx(20_000.0)
    st = state(vwap={"US.QQQ": -200})
    out, _ = planner.plan_entry(st, a, SESSION, "orb", "US.SPY", 1, 10_000, 500.0, "orb_long", CFG, NOW, "e4")
    assert out[0].qty * out[0].limit_price <= 10_000 + 1e-6


def test_flatten_covers_shorts_first():
    st = state(b3={"US.SPY": 20}, noise={"US.SPY": -5}, orb={"US.QQQ": -3}, vwap={"US.IWM": 7})
    a = acct(positions={"US.SPY": 15, "US.QQQ": -3, "US.IWM": 7})
    out = planner.plan_flatten(st, a, SESSION, "drawdown_halt", CFG, NOW)
    sides = [(i.sleeve, i.symbol, i.side, i.qty, i.close_only) for i in out]
    assert set(sides[:2]) == {("noise", "US.SPY", "BUY", 5, True), ("orb", "US.QQQ", "BUY", 3, True)}
    assert set(sides[2:]) == {("b3", "US.SPY", "SELL", 20, True), ("vwap", "US.IWM", "SELL", 7, True)}


@pytest.mark.parametrize("reason,nav", [("drawdown_halt", 96_000.0), ("daily_loss_lock", 98_700.0)])
def test_halt_and_lock_flatten_every_sleeve_including_shorts(reason, nav):
    st = state()
    st["high_water"] = st["day_start_nav"] = 100_000.0
    for s, book in {"b3": {"US.SPY": 4}, "h_etf": {"US.IWM": 3}, "noise": {"US.SPY": -2}, "vwap": {"US.QQQ": -2},
                    "orb": {"US.IWM": 1}}.items():
        st["sleeves"][s] = book
    a = acct(nav=nav, positions={"US.SPY": 2, "US.IWM": 4, "US.QQQ": -2})
    assert planner.mark_risk(st, a, SESSION, CFG) == reason
    out = planner.plan_flatten(st, a, SESSION, reason, CFG, NOW)
    assert {(i.sleeve, i.symbol) for i in out} == {("b3", "US.SPY"), ("h_etf", "US.IWM"), ("noise", "US.SPY"),
                                                   ("vwap", "US.QQQ"), ("orb", "US.IWM")}
    assert all(i.close_only for i in out) and [i.side for i in out][:2] == ["BUY", "BUY"]


def test_runner_nav_must_match_broker_total_before_any_halt():
    st = state()
    st["high_water"] = 100_000.0
    wrong = acct(nav=95_000.0, total=100_000.0)
    assert planner.mark_risk(st, wrong, SESSION, CFG) is None and st["halted"] is None
    assert "disagrees with broker total" in planner.entry_block(st, wrong, SESSION, CFG)
    ok = acct(nav=99_900.0, total=99_950.0)
    planner.mark_risk(st, ok, SESSION, CFG)
    assert st["nav_mismatch"] is None and planner.entry_block(st, ok, SESSION, CFG) is None


# ------------------------------------------------------------------ Noise-area target changes
def test_noise_area_acts_only_on_target_changes_long_to_short_to_flat():
    st = state()
    out, why = planner.plan_target(st, acct(), SESSION, "noise", "US.SPY", 0, 500.0, 500.0, "noise_flat", CFG, NOW, "1030")
    assert out == [] and why == "unchanged"
    st["sleeves"]["noise"] = {"US.SPY": 40}
    out, why = planner.plan_target(st, acct(positions={"US.SPY": 40}), SESSION, "noise", "US.SPY", 1, 500.0, 500.0,
                                   "noise_long", CFG, NOW, "1130")
    assert out == [] and why == "unchanged"
    out, why = planner.plan_target(st, acct(positions={"US.SPY": 40}), SESSION, "noise", "US.SPY", -1, 500.0, 500.0,
                                   "noise_short", CFG, NOW, "1330")
    assert why == "entry" and [(i.side, i.close_only, i.opens_short) for i in out] == [("SELL", True, False), ("SELL", False, True)]
    assert out[0].qty == 40 and out[1].qty == math.floor(20_000 / out[1].limit_price)
    st["sleeves"]["noise"] = {"US.SPY": -39}
    out, why = planner.plan_target(st, acct(positions={"US.SPY": -39}), SESSION, "noise", "US.SPY", 0, 500.0, 500.0,
                                   "noise_flat", CFG, NOW, "1430")
    assert why == "exit to flat" and [(i.side, i.qty, i.close_only) for i in out] == [("BUY", 39, True)]


def _bars(day, closes, open0=100.0, vol=1000.0):
    opened = datetime(day.year, day.month, day.day, 9, 30, tzinfo=NY)
    out, prev = [], open0
    for s, c in enumerate(closes):
        out.append(Bar5(opened + timedelta(minutes=5 * (s + 1)), prev, max(prev, c), min(prev, c), c, vol, vol * c))
        prev = c
    return opened, out


def test_noise_decision_long_short_and_flat():
    p = {**CFG["noise"], "lookback_sessions": 2}
    days = pd.bdate_range("2026-11-02", periods=3).date
    prior = {d: _bars(d, [100.5] * 78)[1] for d in days[:2]}                        # noise 0.5% at every slot
    opened, up = _bars(days[2], [100.2 + 0.02 * k for k in range(12)])            # 10:30 close 100.42
    at = opened + timedelta(minutes=60)
    assert I.noise_decision(up, prior, 99.5, at, opened, p)["target"] == 0        # inside the band
    opened, up = _bars(days[2], [100.5 + 0.1 * k for k in range(12)])
    assert I.noise_decision(up, prior, 99.5, at, opened, p)["target"] == 1        # above upper and VWAP
    opened, dn = _bars(days[2], [99.8 - 0.1 * k for k in range(12)])
    assert I.noise_decision(dn, prior, 100.5, at, opened, p)["target"] == -1      # below lower and VWAP
    assert I.noise_decision(dn, prior, 100.5, at, opened, {**p, "shorts": False})["target"] == 0
    assert I.noise_decision(dn[:-1], prior, 100.5, at, opened, p)["target"] is None   # missing bar: fail closed


def test_noise_decision_times_skip_half_day_afternoon():
    o = datetime(2026, 11, 27, 9, 30, tzinfo=NY)
    times = I.noise_decision_times(o, o.replace(hour=13), CFG["noise"]["decision_times"])
    assert [t.strftime("%H:%M") for t in times] == ["10:30", "11:30"]


# ------------------------------------------------------------------ attribution with negative quantities
def test_attribution_with_negative_quantities_and_netting():
    st = state(b3={"US.SPY": 20}, noise={"US.SPY": -5})
    assert planner.attribution_errors(st, acct(positions={"US.SPY": 15})) == []
    assert planner.attribution_errors(st, acct(positions={"US.SPY": 20}))
    st["sleeves"]["h_etf"] = {"US.IWM": -1}
    assert any("may not short" in e for e in planner.attribution_errors(st, acct(positions={"US.SPY": 15, "US.IWM": -1})))
    fill = lambda sleeve, side, q, short=False: planner.apply_fill(
        st, planner.Intent("i", "t", sleeve, "US.QQQ", side, q, 1.0, 1.0, "r", not short, opens_short=short), q, SESSION)
    fill("orb", "SELL", 3, short=True)
    assert st["sleeves"]["orb"]["US.QQQ"] == -3
    fill("orb", "BUY", 3)
    assert "US.QQQ" not in st["sleeves"]["orb"]
    fill("b3", "SELL", 2)
    assert "US.QQQ" not in st["sleeves"]["b3"]


def test_sleeve_performance_realizes_short_pnl(tmp_path):
    store = Store(tmp_path, {"state_dir": "st"})
    st = {**state(), "initial_nav": 100_000.0}
    rows = [("a1", "orb", "US.QQQ", "SELL", 10, 400.0), ("a2", "orb", "US.QQQ", "BUY", 10, 390.0),
            ("a3", "vwap", "US.SPY", "SELL", 4, 500.0)]
    for k, (aid, sleeve, sym, side, q, px) in enumerate(rows):
        intent = {"intent_id": f"i{k}", "sleeve": sleeve, "symbol": sym, "side": side, "qty": q}
        store.append(SESSION, {"event": "intent", "audit_id": aid, **intent, "mode": "EXECUTE"})
        store.append(SESSION, {"event": "acknowledged", "audit_id": aid, "intent_id": f"i{k}", "order_id": f"o{k}"})
        store.append(SESSION, {"event": "outcome", "audit_id": aid, "intent_id": f"i{k}", "order_id": f"o{k}",
                               "status": "FILLED", "filled": q, "qty": q, "avg_price": px})
    st["sleeves"]["vwap"] = {"US.SPY": -4}
    perf = sleeve_performance(store, st, asdict(acct(positions={"US.SPY": -4}, quotes={"US.SPY": 495.0})), CFG)
    assert perf["sleeves"]["orb"]["realized_gross"] == pytest.approx(100.0)
    assert perf["sleeves"]["vwap"]["unrealized_gross"] == pytest.approx(20.0)
    assert perf["sleeves"]["orb"]["capital"] == pytest.approx(20_100.0)


def test_position_parser_reads_signed_and_unsigned_shorts_or_fails_closed():
    rows = [{"symbol": "US.SPY", "qty": "6", "mv": "4200", "cur_price": "700"},
            {"symbol": "US.QQQ", "qty": "-2", "mv": "-1220", "cur_price": "610"}]
    assert parse_positions(rows, {"short_mv": "1220"}) == {"US.SPY": 6, "US.QQQ": -2}
    rows[1] = {"symbol": "US.QQQ", "qty": "2", "mv": "-1220", "cur_price": "610"}
    assert parse_positions(rows, {"short_mv": "1220"}) == {"US.SPY": 6, "US.QQQ": -2}
    rows[1] = {"symbol": "US.QQQ", "qty": "2", "mv": "1220", "cur_price": "610"}
    with pytest.raises(ExecutionBlocked, match="ambiguous"):
        parse_positions(rows, {"short_mv": "1220"})
    assert parse_positions(rows, {"short_mv": "1220"}, {"US.SPY": 6, "US.QQQ": -2}) == {"US.SPY": 6, "US.QQQ": -2}
    with pytest.raises(ExecutionBlocked):
        parse_positions(rows, {"short_mv": "5000"}, {"US.SPY": 6, "US.QQQ": -2})


# ------------------------------------------------------------------ dated gates
def test_dated_gates_wednesday_and_half_day_friday():
    wed = datetime(2026, 11, 25, 10, 25, tzinfo=NY)
    for s in ("noise", "vwap", "orb"):
        assert "10:30" in planner.no_entry_reason(s, "2026-11-25", wed, CFG)
        assert planner.no_entry_reason(s, "2026-11-25", wed + timedelta(minutes=5), CFG) is None
        assert "12:00" in planner.no_entry_reason(s, "2026-11-27", datetime(2026, 11, 27, 12, 0, tzinfo=NY), CFG)
        assert planner.no_entry_reason(s, "2026-11-27", datetime(2026, 11, 27, 11, 59, tzinfo=NY), CFG) is None
    out, why = planner.plan_entry(state(), acct(), "2026-11-25", "orb", "US.SPY", 1, 10, 500.0, "orb", CFG, wed, "e4")
    assert out == [] and "10:30" in why


# ------------------------------------------------------------------ runner: VWAP/ORB monitor, ORB half and trail
def _clock(now):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz) if tz else now.replace(tzinfo=None)
    return Clock


@pytest.fixture
def runner(tmp_path, monkeypatch):
    ns = runpy.run_path(str(PROJECT / "scripts" / "run_competition.py"))
    r = ns["Runner"].__new__(ns["Runner"])
    r.cfg = {**CFG, "state_dir": "state"}
    r.store = Store(tmp_path, r.cfg)
    r.state = r.store.load()
    planner.mark_risk(r.state, acct(), SESSION, CFG)
    r._provenance_id = "p"
    r.broker = CompetitionBroker.__new__(CompetitionBroker)
    r.broker.cfg = r.cfg
    r.quotes = {"US.SPY": 500.0, "US.QQQ": 400.0, "US.IWM": 200.0}
    r.bars = {}
    r.market = SimpleNamespace(quote=lambda s: (r.quotes[s], datetime.now(timezone.utc)),
                               five_minute_today=lambda s, d: r.bars.get(s, []))
    r.sent, r.fill = [], {}
    oid = iter(range(1000))

    def place(i):
        r.sent.append(i)
        return f"o{next(oid)}"

    def wait(order_id, placed, seconds):
        i = r.sent[-1]
        f = r.fill.get((i.symbol, i.side), i.qty)
        return {"order_id": order_id, "status": "FILLED" if f == i.qty else "CANCELLED", "filled": f,
                "avg_price": r.quotes[i.symbol], "qty": i.qty}

    r.broker.place, r.broker.wait = place, wait
    monkeypatch.setattr(r, "audit", lambda *a, **k: "audit/1")

    def account(extra=()):
        pos = {}
        for book in r.state["sleeves"].values():
            for s, q in book.items():
                pos[s] = pos.get(s, 0) + q
        return acct(positions=pos, quotes=r.quotes)

    monkeypatch.setattr(r, "account", account)
    monkeypatch.setattr(r, "session", lambda now=None: SESSION)
    monkeypatch.setattr(r, "_atr", lambda s, d: math.nan)                    # no new scans in these exit tests
    monkeypatch.setitem(r.monitor.__globals__, "datetime", _clock(NOW))
    return r


@pytest.mark.parametrize("side,px,why", [(1, 489.0, "stop"), (1, 521.0, "target"), (-1, 506.0, "stop"), (-1, 470.0, "target")])
def test_monitor_closes_vwap_at_stop_and_target(runner, side, px, why):
    runner.state["sleeves"]["vwap"] = {"US.SPY": 10 * side}
    day = runner._intraday(SESSION)
    day["positions"]["vwap|US.SPY"] = {"side": side, "stop": 490.0 if side > 0 else 505.0, "target": 520.0 if side > 0 else 475.0,
                                       "entry_ref": 500.0, "entry_slot": 5, "qty0": 10, "half_done": False}
    runner.quotes["US.SPY"] = px
    runner.monitor()
    assert [(i.sleeve, i.side, i.qty, i.close_only) for i in runner.sent] == [("vwap", "SELL" if side > 0 else "BUY", 10, True)]
    assert runner.sent[0].reason == f"vwap_{why}" and "vwap|US.SPY" not in day["positions"]


def test_orb_takes_half_at_target_then_breakeven_stop(runner):
    runner.state["sleeves"]["orb"] = {"US.IWM": -9}
    day = runner._intraday(SESSION)
    day["positions"]["orb|US.IWM"] = {"side": -1, "stop": 202.0, "target": 196.0, "entry_ref": 200.0, "entry_slot": 4,
                                      "qty0": 9, "half_done": False}
    runner.quotes["US.IWM"] = 195.9
    runner.monitor()
    assert [(i.side, i.qty, i.close_only) for i in runner.sent] == [("BUY", 4, True)]           # round(9 x 0.5) = 4
    pos = day["positions"]["orb|US.IWM"]
    assert pos["half_done"] and pos["stop"] == 200.0 and runner.state["sleeves"]["orb"]["US.IWM"] == -5
    runner.quotes["US.IWM"] = 199.0
    runner.monitor()
    assert len(runner.sent) == 1                                                                # between levels: hold
    runner.quotes["US.IWM"] = 200.0
    runner.monitor()
    assert [(i.side, i.qty) for i in runner.sent][-1] == ("BUY", 5) and "orb|US.IWM" not in day["positions"]


def test_orb_trail_exits_the_rest_when_a_bar_closes_beyond_the_previous_bar(runner, monkeypatch):
    opened = NOW.replace(hour=9, minute=30)
    runner.state["sleeves"]["orb"] = {"US.SPY": 5}
    day = runner._intraday(SESSION)
    day["orb_done"] = ["US.SPY", "US.QQQ", "US.IWM"]
    day["positions"]["orb|US.SPY"] = {"side": 1, "stop": 500.0, "target": 510.0, "entry_ref": 500.0, "entry_slot": 4,
                                      "qty0": 10, "half_done": True}
    closes = [500.0 + 0.5 * k for k in range(20)] + [506.0]                                     # slot 20 closes below slot 19 low
    bars = [Bar5(opened + timedelta(minutes=5 * (s + 1)), c, c + 0.3, c - 0.3, c, 1e5, 1e5 * c) for s, c in enumerate(closes)]
    for sym in ("US.SPY", "US.QQQ", "US.IWM"):
        runner.bars[sym] = bars
    monkeypatch.setattr(runner, "risk", lambda symbols=(): runner.account(symbols))
    runner.bar_step(opened + timedelta(minutes=5 * 21, seconds=15))
    assert [(i.side, i.qty, i.reason) for i in runner.sent] == [("SELL", 5, "orb_trail")]
    assert "orb|US.SPY" not in day["positions"]


def test_scheduled_flatten_reports_not_flat_instead_of_raising(runner, monkeypatch):
    monkeypatch.setattr(runner, "risk", lambda symbols=(): runner.account(symbols))
    runner.state["sleeves"]["noise"] = {"US.SPY": -20}
    runner.fill[("US.SPY", "BUY")] = 0
    runner.flatten_sleeves(("noise",), "noise_scheduled_flatten")
    assert not runner.is_flat(("noise",)) and runner.sent[-1].side == "BUY"
    runner.fill.clear()
    runner.flatten_sleeves(("noise",), "noise_scheduled_flatten")
    assert runner.is_flat(("noise",))


# ------------------------------------------------------------------ engine details and parity
def test_orb_rvol_uses_20_prior_sessions_with_15_valid():
    assert I.orb_rvol(200.0, [100.0] * 20) == 2.0
    assert I.orb_rvol(200.0, [100.0] * 15 + [math.nan] * 5) == 2.0
    assert math.isnan(I.orb_rvol(200.0, [100.0] * 14 + [math.nan] * 6))


def test_atr14_uses_the_14_prior_daily_bars_only():
    days = pd.bdate_range("2026-10-01", periods=20).date
    d = pd.DataFrame({"high": [102.0] * 20, "low": [100.0] * 20, "close": [101.0] * 20}, index=days)
    d.loc[days[-1], ["high", "low"]] = [150.0, 50.0]
    assert I.atr14(d, days[-1]) == pytest.approx(2.0)
    assert math.isnan(I.atr14(d.iloc[:10], days[9]))


@pytest.mark.skipif(not (PROJECT / "data-cache" / "regime_extra").exists(), reason="regime cache not present")
def test_parity_with_rstrats_on_the_last_week(monkeypatch):
    monkeypatch.chdir(PROJECT)
    monkeypatch.syspath_prepend(str(PROJECT / "reports" / "claude_regime"))
    parity = runpy.run_path(str(PROJECT / "scripts" / "competition_parity_regime.py"), run_name="parity")
    days = parity["RS"].in_window("2026-11-16", "2026-11-20")
    noise, vwap, orb = parity["noise_parity"](days), parity["vwap_orb_parity"]("vwap", days), parity["vwap_orb_parity"]("orb", days)
    inc = parity["incremental_parity"](days)
    assert noise["mismatch_days"] == vwap["candidate_mismatches"] == orb["candidate_mismatches"] == inc["mismatches"] == 0
    assert (vwap["pnl_live"], orb["pnl_live"]) == (248.32, -50.06) and inc["slot_checks"] > 1000


def test_daily_report_after_the_close(runner, tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "risk", lambda symbols=(): runner.account(symbols))
    runner.state["initial_nav"] = 100_000.0
    runner.state["sleeves"]["h_etf"] = {}
    calls = []
    monkeypatch.setattr("subprocess.run", lambda *a, **k: calls.append(a))
    runner.daily_report(SESSION)
    rep = __import__("json").loads((runner.store.dir / "reports" / f"{SESSION}.json").read_text())
    assert rep["session"] == SESSION and rep["mode"] == "EXECUTE" and set(rep["sleeves"]) == set(planner.SLEEVES)
    assert all(v["capital"] == 20_000.0 for v in rep["sleeves"].values()) and rep["halted"] is None


def test_legacy_exit_only_keeps_existing_h_exit_and_blocks_new_exposure(runner, monkeypatch):
    runner.cfg = {**runner.cfg, "risk": {**CFG["risk"], "legacy_exit_only": True, "entry_sleeves": []}}
    runner.state["sleeves"]["h_etf"] = {"US.SPY": 8, "US.QQQ": 6}
    account = acct(positions={"US.SPY": 8, "US.QQQ": 6})
    plan = {"entries": ["US.IWM"], "exits": {"US.SPY": "max_hold"}}
    exits, why = planner.plan_h_open(runner.state, account, SESSION, plan, runner.cfg, NOW)
    assert "exit-only" in why
    assert [(i.symbol, i.side, i.qty, i.close_only) for i in exits] == [("US.SPY", "SELL", 8, True)]
    monkeypatch.setattr(runner, "account", lambda symbols=(): acct(positions={"US.SPY": 4, "US.QQQ": 6}))
    assert not runner.send(exits, SESSION, "exit")
    assert not runner.sent
    monkeypatch.setattr(runner, "account", lambda symbols=(): account)
    runner.send(exits, SESSION, "exit")
    assert len(runner.sent) == 1
    assert runner.state["sleeves"]["h_etf"] == {"US.QQQ": 6}


def test_h_entries_are_restored_without_enabling_other_legacy_entries(runner):
    runner.cfg = load_config(PROJECT)
    runner.state["sleeves"]["h_etf"] = {"US.XLP": 2}
    account = acct(positions={"US.XLP": 2}, quotes={"US.XLP": 90.0})
    plan = {"entries": ["US.IWM"], "exits": {}}
    intents, why = planner.plan_h_open(runner.state, account, SESSION, plan, runner.cfg, NOW)
    assert why == "planned"
    buy, = intents
    assert buy.symbol == "US.IWM" and buy.side == "BUY" and not buy.close_only
    assert buy.qty * buy.limit_price <= 2000
    assert planner.entry_block(runner.state, account, SESSION, runner.cfg, "b3") == "legacy strategies are exit-only"
    runner.send(intents, SESSION, "h-entry")
    assert runner.state["sleeves"]["h_etf"] == {"US.XLP": 2, "US.IWM": buy.qty}


@pytest.mark.parametrize("sold,buys", [(0, []), (10, [("US.IWM", 4)]), (60, [("US.IWM", 9), ("US.QQQ", 4)])])
def test_h_open_resizes_from_confirmed_exit_and_entry_fills(runner, monkeypatch, sold, buys):
    monkeypatch.delattr(runner, "audit")
    runner.cfg = load_config(PROJECT)
    runner.quotes["US.XLP"] = 100
    runner.state["sleeves"]["h_etf"] = {"US.XLP": 60}
    runner.state["h_entry_session"]["US.XLP"] = "2026-11-20"
    runner.state["h_plan"] = {"asof": "2026-11-20", "entries": ["US.IWM", "US.QQQ"],
                              "exits": {"US.XLP": "rsi_recovery"}}
    link = {"intent_id": "previous-buy", "audit_id": "test", "order_id": "prior"}
    for event, fields in [
        ("intent", {"mode": "EXECUTE", "sleeve": "h_etf", "symbol": "US.XLP", "side": "BUY", "qty": 60}),
        ("acknowledged", {}),
        ("outcome", {"status": "FILLED", "filled": 60, "qty": 60, "avg_price": 100}),
    ]:
        runner.store.append("2026-11-20", {"event": event, **link, **fields})
    runner.fill["US.XLP", "SELL"] = sold

    runner.h_open()
    assert [(i.symbol, i.side, i.qty) for i in runner.sent][:1] == [("US.XLP", "SELL", 60)]
    assert [(i.symbol, i.qty) for i in runner.sent if i.side == "BUY"] == buys
    exposure = sum(q * runner.quotes[s] for s, q in runner.state["sleeves"]["h_etf"].items())
    assert exposure <= 6000
    runner.h_open()
    assert len(runner.sent) == 1 + len(buys)


@pytest.mark.parametrize("invalid", [
    {"order_id": "different"}, {"qty": 4}, {"filled": 3}, {"filled": True}, {"filled": 1},
    {"avg_price": math.nan}, {"avg_price": 0}, {"status": "WORKING"},
])
def test_invalid_terminal_fill_halts_before_retrying_or_opening_again(runner, monkeypatch, invalid):
    monkeypatch.setattr(runner.broker, "wait", lambda oid, *_: {
        "order_id": oid, "status": "FILLED", "filled": 2, "qty": 2, "avg_price": 200, **invalid,
    })
    intent = planner.Intent("buy", "t", "h_etf", "US.IWM", "BUY", 2, 200, 200, "test", False)
    with pytest.raises(ExecutionBlocked, match="invalid terminal fill"):
        runner.send([intent, intent], SESSION)
    assert len(runner.sent) == 1
    saved = runner.store.load()
    assert saved["halted"]["kind"] == "order_error"
    assert saved["sleeves"]["h_etf"] == {}


def test_h_open_atomic_claim_prevents_second_caller_after_a_clear_prescan(runner, monkeypatch):
    monkeypatch.delattr(runner, "audit")
    runner.state["h_plan"] = {"asof": "2026-11-20", "entries": ["US.IWM"], "exits": {}}
    original_audit = runner.store.audit

    def race(session, record, **kwargs):
        if record["kind"] == "h_open_started":
            original_audit(session, record, **kwargs)
        return original_audit(session, record, **kwargs)
    monkeypatch.setattr(runner.store, "audit", race)
    monkeypatch.setattr(runner, "risk", lambda *_: pytest.fail("Concurrent claimant did not own the session"))
    runner.h_open()
    assert runner.sent == []
    assert (runner.store.dir / "audit" / SESSION / "h_open_started.json").exists()


@pytest.fixture
def vote_replay(monkeypatch):
    import sys

    ref = SimpleNamespace(CFG=load_config(PROJECT), I=I, R=SimpleNamespace(MINUTES=[390], DAYS=[NOW.date()]),
                          RS=None, spy_decision_bars=None)
    monkeypatch.setitem(sys.modules, "competition_parity_regime", ref)
    return runpy.run_path(str(PROJECT / "scripts" / "competition_vote_replay.py"))


def test_two_vote_replay_caps_each_ticker_across_lots_and_reserves_gains(vote_replay):
    replay = vote_replay
    spy, qqq = replay["SYMBOLS"]
    opens = {s: np.full((1, 78), 100.0) for s in (spy, qqq)}
    opens[spy][0, 12:], opens[qqq][0, 12:] = 102.0, 102.0
    opens[spy][0, -1], opens[qqq][0, -1] = 110.0, 90.0
    pulses = np.zeros(78, dtype=int)
    pulses[[6, 8, 10, 12, 14]] = 2
    neutral = np.zeros_like(pulses)
    cash = replay["VoteCash"]()
    trades = replay["replay_day"](0, {s: SimpleNamespace(open=a) for s, a in opens.items()},
                                  {spy: (neutral, pulses), qqq: (pulses, neutral)}, 10000, 2, capital=cash)
    assert len(trades) == 8
    for symbol in (spy, qqq):
        lots = [t for t in trades if t.symbol == symbol]
        assert [t.entry_slot for t in lots] == [6, 8, 10, 12]
        assert all(t.shares * t.entry * (1 + I.FR) <= 10000 for t in lots)
        assert sum(t.shares for t in lots) * 102.0 * (1 + I.FR) <= 40000
        assert lots[-1].shares < lots[0].shares
    assert all(t.pnl > 0 for t in trades)
    assert cash.reserved_profit == pytest.approx(sum(t.pnl for t in trades))
    assert cash.sizing_capital == 80000
    cash.settle(-200)
    assert cash.sizing_capital == 79800
    assert cash.reserved_profit == pytest.approx(sum(t.pnl for t in trades))


def test_two_vote_replay_blocks_bad_quotes_and_conflicting_votes(vote_replay):
    spy, qqq = vote_replay["SYMBOLS"]
    rows = np.full((1, 78), 100.0)
    blobs = {s: SimpleNamespace(open=rows) for s in (spy, qqq)}
    agree = np.full(78, 2)
    neutral = np.zeros_like(agree)
    replay = vote_replay["replay_day"]
    cash = vote_replay["VoteCash"]()
    cash.settle(50000)
    cash.settle(-80000)
    assert cash.sizing_capital == 0
    assert replay(0, blobs, {spy: (neutral, agree), qqq: (agree, neutral)}, 10000, 2, capital=cash) == []
    assert cash.reserved_profit == 50000
    assert replay(0, blobs, {spy: (agree, agree), qqq: (agree, agree)}, 10000, 2) == []
    rows[0, 6] = math.nan
    with pytest.raises(ValueError, match="invalid two-vote fill price"):
        replay(0, blobs, {spy: (neutral, agree), qqq: (agree, neutral)}, 10000, 2)


def test_two_vote_replay_caps_and_reserve_hold_across_200_seeded_paths(vote_replay):
    rng = np.random.default_rng(5218)
    for _ in range(200):
        prices = {s: SimpleNamespace(open=np.exp(rng.normal(0, 0.018, (1, 78)).cumsum(axis=1))
                                     * rng.uniform(80, 800)) for s in vote_replay["SYMBOLS"]}
        votes = {s: (rng.integers(0, 5, 78), rng.integers(0, 5, 78)) for s in prices}
        cash = vote_replay["VoteCash"]()
        trades = vote_replay["replay_day"](0, prices, votes, 10000, 2, capital=cash)
        for slot in {t.entry_slot for t in trades}:
            open_lots = [t for t in trades if t.entry_slot <= slot < t.exit_slot]
            assert len(open_lots) <= 8
            gross = 0.0
            for symbol, bars in prices.items():
                lots = [t for t in open_lots if t.symbol == symbol]
                assert len(lots) <= 4 and len({t.side for t in lots}) <= 1
                price = bars.open[0, slot] * (1 + I.FR)
                marked = sum(t.shares * price for t in lots)
                gross += marked
                for trade in (t for t in lots if t.entry_slot == slot):
                    assert trade.shares * price <= 10000 + 1e-7
                    assert marked <= 40000 + 1e-7
            realized_losses = sum(min(0, t.pnl) for t in trades if t.exit_slot <= slot)
            assert gross <= 80000 + realized_losses + 1e-7
        gains = sum(max(0, t.pnl) for t in trades)
        losses = sum(min(0, t.pnl) for t in trades)
        assert cash.reserved_profit == pytest.approx(gains)
        assert cash.sizing_capital == pytest.approx(80000 + losses)
