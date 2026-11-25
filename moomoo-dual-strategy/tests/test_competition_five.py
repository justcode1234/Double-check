"""Five-strategy competition runner (QODER_FIVE_STRATEGIES.md, commit eca98ae, section 6): $20,000 caps, the shared
per-symbol cap, shorts, the VWAP/ORB stop/target/trail monitor, Noise-area target changes, halts with shorts, signed
attribution, and parity of the live Noise-area/VWAP/ORB code with reports/claude_regime/rstrats.py."""

import json
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
from dual_strategy.execution.moomoo_cli import CliTransport
from dual_strategy.execution.moomoo_paper import ExecutionBlocked

PROJECT = Path(__file__).resolve().parents[1]
CFG = load_config(PROJECT)
CFG["risk"]["legacy_exit_only"] = False  # Exercise the original entry and sizing rules.
# Exercise the original exit mechanics on positions set directly in state (no receipts). The live profit-only rule
# (risk.long_sales_only_at_profit) has its own tests in test_long_sales_only_at_profit.py.
CFG["risk"]["long_sales_only_at_profit"] = False
NOW = datetime(2026, 11, 23, 11, 0, tzinfo=NY)
SESSION = "2026-11-23"


def acct(nav=100_000.0, positions=None, quotes=None, total=None):
    positions = positions or {}
    quotes = {"US.SPY": 500.0, "US.QQQ": 400.0, "US.IWM": 200.0, **(quotes or {})}
    cash = nav - sum(q * quotes[s] for s, q in positions.items())
    return planner.Account(datetime.now(timezone.utc), cash, positions, quotes, {s: 1.0 for s in quotes},
                           {s: (80_000.0 if s == "two_vote" else 20_000.0) for s in planner.SLEEVES}, broker_total=total)


def state(**books):
    st = planner.new_state()
    for k, v in books.items():
        st["sleeves"][k] = dict(v)
    planner.mark_risk(st, acct(), SESSION, CFG)
    return st


def test_five_sleeves_named_not_numbered():
    assert planner.SLEEVES == ("b3", "h_etf", "noise", "vwap", "orb", "two_vote")
    assert CFG["allocation"] == {**{s: 0.20 for s in planner.SLEEVES if s != "two_vote"}, "two_vote": 0.0}
    assert CFG["risk"]["short_sleeves"] == ["noise", "vwap", "orb", "two_vote"] and CFG["risk"]["long_only"] is False
    assert "fbr" not in CFG and "pairs" not in CFG


# ------------------------------------------------------------------ $20,000 caps
def test_each_sleeve_starts_at_20k_and_moves_only_with_its_own_pnl():
    st = {**state(), "initial_nav": 100_000.0}
    perf = sleeve_performance(Store(PROJECT, {"state_dir": "nonexistent-state"}), st, asdict(acct()), CFG)
    assert {s: v["initial_capital"] for s, v in perf["sleeves"].items()} == {
        s: (80_000.0 if s == "two_vote" else 20_000.0) for s in planner.SLEEVES}


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
    st = state(b3={"US.SPY": 39}, h_etf={"US.SPY": 10}, vwap={"US.SPY": 11})        # 60 x $500 = $30,000 used
    a = acct(positions={"US.SPY": 60})
    assert planner.exposure(st, "US.SPY", 500.0) == 30_000
    out, why = planner.plan_target(st, a, SESSION, "noise", "US.SPY", 1, 500.0, 500.0, "noise_long", CFG, NOW, "1030")
    assert why == "entry" and out[0].qty * 500.0 <= 10_000                               # gets the remainder
    st["sleeves"]["orb"] = {"US.SPY": 20}
    out, why = planner.plan_target(st, acct(positions={"US.SPY": 80}), SESSION, "noise", "US.SPY", 1, 500.0, 500.0,
                                   "noise_long", CFG, NOW, "1130")
    assert out == [] and why == "entry size zero"                                       # cap used up: skip


def test_b3_counts_noise_area_spy_toward_the_cap():
    st = state(noise={"US.SPY": 70})
    dec = SimpleNamespace(target=1, at=NOW, reason="long", metrics={"open_0930": 500.0})
    out, why = planner.plan_b3(st, acct(positions={"US.SPY": 70}), SESSION, dec, CFG, NOW)
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


@pytest.mark.parametrize("reason,nav", [("drawdown_halt", 92_900.0), ("daily_loss_lock", 97_400.0)])
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


def test_sleeve_performance_lists_every_trade_with_action_reason_and_order_time(tmp_path):
    store = Store(tmp_path, {"state_dir": "st"})
    st = {**state(), "initial_nav": 100_000.0}
    rows = [("a1", "orb", "US.QQQ", "SELL", 10, 400.0, "orb_short", "2026-11-20T10:00:00-05:00"),
            ("a2", "orb", "US.QQQ", "BUY", 10, 390.0, "orb_trail", "2026-11-20T15:05:00+00:00"),   # 10:05 ET, UTC text
            ("a3", "vwap", "US.SPY", "SELL", 4, 500.0, "vwap_short", "2026-11-20T10:02:00-05:00"),
            ("a4", "h_etf", "US.XLV", "BUY", 3, 100.0, "h_rsi2_entry", "2026-11-20T09:30:30-05:00"),
            ("a5", "h_etf", "US.XLV", "SELL", 3, 101.5, "h_etf_take_profit", "2026-11-20T10:10:00-05:00")]
    for k, (aid, sleeve, sym, side, q, px, reason, created) in enumerate(rows):
        intent = {"intent_id": f"i{k}", "sleeve": sleeve, "symbol": sym, "side": side, "qty": q, "reason": reason,
                  "created_at": created}
        store.append(SESSION, {"event": "intent", "audit_id": aid, **intent, "mode": "EXECUTE"})
        store.append(SESSION, {"event": "acknowledged", "audit_id": aid, "intent_id": f"i{k}", "order_id": f"o{k}"})
        store.append(SESSION, {"event": "outcome", "audit_id": aid, "intent_id": f"i{k}", "order_id": f"o{k}",
                               "status": "FILLED", "filled": q, "qty": q, "avg_price": px,
                               "logged_at": "2026-11-20T20:00:00+00:00"})
    st["sleeves"]["vwap"] = {"US.SPY": -4}
    perf = sleeve_performance(store, st, asdict(acct(positions={"US.SPY": -4}, quotes={"US.SPY": 495.0})), CFG)
    trades = perf["trades"]
    assert [(t["intent_id"], t["action"], t["realized_gross"], t["reason"]) for t in trades] == [
        ("i3", "buy", None, "h_rsi2_entry"), ("i0", "short", None, "orb_short"), ("i2", "short", None, "vwap_short"),
        ("i1", "cover", 100.0, "orb_trail"), ("i4", "sell", 4.5, "h_etf_take_profit")]
    assert all(t["verified"] and t["source"] == "automatic" for t in trades)
    assert trades[3]["ordered_at"] == "2026-11-20T15:05:00+00:00"
    assert trades[0]["shares"] == 3 and trades[0]["notional"] == pytest.approx(300.0)
    assert sleeve_performance(store, None, None, CFG)["trades"] == []


def test_account_pnl_uses_moomoo_total_assets_while_short(tmp_path):
    """Moomoo cash excludes short-sale proceeds: with 15 QQQ short it reported $94,412 cash but $100,124 total assets."""
    store = Store(tmp_path, {"state_dir": "st"})
    st = {**state(), "initial_nav": 100_000.0}
    short_account = planner.Account(datetime.now(timezone.utc), 94_412.248, {"US.QQQ": -15}, {"US.QQQ": 634.6},
                                    {"US.QQQ": 1.0}, {}, broker_total=100_124.188)
    assert sleeve_performance(store, st, asdict(short_account), CFG)["account_pnl"] == pytest.approx(124.188)
    no_total = asdict(acct(nav=100_050.0))
    no_total.pop("broker_total")
    assert sleeve_performance(store, st, no_total, CFG)["account_pnl"] == pytest.approx(50.0)


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
    assert all(v["capital"] == (80_000.0 if s == "two_vote" else 20_000.0) for s, v in rep["sleeves"].items()) \
        and rep["halted"] is None


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
    assert 9800 <= buy.qty * buy.limit_price <= 10000
    assert planner.entry_block(runner.state, account, SESSION, runner.cfg, "b3") == "legacy strategies are exit-only"
    runner.send(intents, SESSION, "h-entry")
    assert runner.state["sleeves"]["h_etf"] == {"US.XLP": 2, "US.IWM": buy.qty}


@pytest.mark.parametrize("sold,buys", [(0, []), (10, [("US.IWM", 4)]), (200, [("US.IWM", 49), ("US.QQQ", 24)])])
def test_h_open_resizes_from_confirmed_exit_and_entry_fills(runner, monkeypatch, sold, buys):
    monkeypatch.delattr(runner, "audit")
    runner.cfg = load_config(PROJECT)
    runner.cfg["risk"]["long_sales_only_at_profit"] = False   # resizing mechanics; exits at a loss are tested elsewhere
    runner.quotes["US.XLP"] = 100
    runner.state["sleeves"]["h_etf"] = {"US.XLP": 200}
    runner.state["h_entry_session"]["US.XLP"] = "2026-11-20"
    runner.state["h_plan"] = {"asof": "2026-11-20", "entries": ["US.IWM", "US.QQQ"],
                              "exits": {"US.XLP": "rsi_recovery"}}
    link = {"intent_id": "previous-buy", "audit_id": "test", "order_id": "prior"}
    for event, fields in [
        ("intent", {"mode": "EXECUTE", "sleeve": "h_etf", "symbol": "US.XLP", "side": "BUY", "qty": 200}),
        ("acknowledged", {}),
        ("outcome", {"status": "FILLED", "filled": 200, "qty": 200, "avg_price": 100}),
    ]:
        runner.store.append("2026-11-20", {"event": event, **link, **fields})
    runner.fill["US.XLP", "SELL"] = sold

    runner.h_open()
    assert [(i.symbol, i.side, i.qty) for i in runner.sent][:1] == [("US.XLP", "SELL", 200)]
    assert [(i.symbol, i.qty) for i in runner.sent if i.side == "BUY"] == buys
    exposure = sum(q * runner.quotes[s] for s, q in runner.state["sleeves"]["h_etf"].items())
    assert exposure <= 20000
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
    runner.state = saved
    late_close = planner.Intent("close", "t", "h_etf", "US.IWM", "SELL", 2, 200, 200, "scheduled_flatten", True)
    assert runner.send([late_close], SESSION) == []
    assert len(runner.sent) == 1


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
    pulses[[6, 12, 14]] = 2                               # 3 fresh agreements; only 2 lots fit
    neutral = np.zeros_like(pulses)
    cash = replay["VoteCash"]()
    lot = replay["LOT_USD"]
    assert (lot, replay["LOTS_PER_SYMBOL"]) == (20_000, 2)
    trades = replay["replay_day"](0, {s: SimpleNamespace(open=a) for s, a in opens.items()},
                                  {spy: (neutral, pulses), qqq: (pulses, neutral)}, lot, 2, capital=cash)
    assert len(trades) == 4
    for symbol in (spy, qqq):
        lots = [t for t in trades if t.symbol == symbol]
        assert [t.entry_slot for t in lots] == [6, 12]
        assert all(t.shares * t.entry * (1 + I.FR) <= lot for t in lots)
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


# ------------------------------------------------------------------ two-vote live sleeve
def test_vote_lot_uses_configured_size_within_book_name_cap_and_cash():
    lot = CFG["two_vote"]["entry_usd"]
    # User decisions 2026-11-25: $20k lots, no lot count (the per-ticker dollar cap alone), no profit target.
    assert lot == 20_000 and CFG["two_vote"]["max_lots_per_symbol"] is None and CFG["two_vote"]["take_profit_usd"] is None
    assert CFG["two_vote"]["min_lot_usd"] == 5_000 and CFG["risk"]["name_cap_usd"] == 40_000
    assert CFG["risk"]["name_cap_fraction_of_nav"] == 0.395
    assert planner.name_cap(CFG, acct()) == pytest.approx(39_500)                 # 39.5% of a $100,000 NAV
    assert planner.name_cap(CFG, acct(nav=110_000)) == 40_000                    # never above the $40k cap
    assert planner.name_cap(CFG, acct(nav=90_000)) == pytest.approx(35_550)
    st = state()
    out, why = planner.plan_vote_entry(st, acct(), SESSION, "US.SPY", 1, CFG, NOW, "e6")
    assert why == "entry" and out[0].sleeve == "two_vote" and not out[0].close_only and not out[0].opens_short
    assert out[0].qty == math.floor(lot / out[0].limit_price)
    out, why = planner.plan_vote_entry(st, acct(), SESSION, "US.QQQ", -1, CFG, NOW, "e6")
    assert why == "entry" and out[0].side == "SELL" and out[0].opens_short
    st["sleeves"]["two_vote"] = {"US.SPY": 39}                                   # a second lot of the same symbol
    out, why = planner.plan_vote_entry(st, acct(positions={"US.SPY": 39}), SESSION, "US.SPY", 1, CFG, NOW, "e7")
    assert why == "entry" and out[0].qty * out[0].limit_price <= lot + 1e-6
    st["sleeves"]["vwap"] = {"US.SPY": 20}                                       # $29,500 of the $39,500 name cap used
    out, why = planner.plan_vote_entry(st, acct(positions={"US.SPY": 59}), SESSION, "US.SPY", 1, CFG, NOW, "e8")
    assert why == "entry" and 5_000 <= out[0].qty * out[0].limit_price <= 10_000 + 1e-6   # a top-up to the cap
    st["sleeves"]["vwap"] = {"US.SPY": 30}                                       # $34,500 used: only $5,000 left
    out, why = planner.plan_vote_entry(st, acct(positions={"US.SPY": 69}), SESSION, "US.SPY", 1, CFG, NOW, "e8")
    assert out == [] and "below the $5,000 minimum lot" in why                   # 9 shares = $4,502: dust, skipped
    st["sleeves"]["vwap"] = {}                                                   # a lower NAV lowers the cap
    out, why = planner.plan_vote_entry(st, acct(nav=90_000, positions={"US.SPY": 39}), SESSION, "US.SPY", 1, CFG, NOW, "e8")
    assert why == "entry" and 39 * 500 + out[0].qty * out[0].limit_price <= 35_550 + 1e-6
    st = state(two_vote={"US.SPY": 80, "US.QQQ": 100})                           # $80,000 book fully deployed
    out, why = planner.plan_vote_entry(st, acct(positions={"US.SPY": 80, "US.QQQ": 100}), SESSION, "US.SPY", 1,
                                       CFG, NOW, "e9")
    assert out == [] and why == "entry size zero"
    st = state()
    st["reduced"] = True
    out, why = planner.plan_vote_entry(st, acct(), SESSION, "US.SPY", 1, CFG, NOW, "e6")
    assert why == "entry" and out[0].qty == math.floor(lot / 2 / out[0].limit_price)
    st = state()
    st["halted"] = "drawdown"
    out, why = planner.plan_vote_entry(st, acct(), SESSION, "US.SPY", 1, CFG, NOW, "e6")
    assert out == [] and "drawdown_halt" in why
    st = state()
    st["locked_day"] = SESSION
    out, why = planner.plan_vote_entry(st, acct(), SESSION, "US.SPY", 1, CFG, NOW, "e6")
    assert out == [] and "daily_loss_lock" in why


def test_vote_book_banks_gains_and_counts_losses(tmp_path):
    store = Store(tmp_path, {"state_dir": "state"})
    st = {**state(), "initial_nav": 100_000.0}

    def receipt(tag, side, price):
        link = {"audit_id": tag, "intent_id": tag}
        store.append(SESSION, {"event": "intent", "mode": "EXECUTE", **link, "sleeve": "two_vote",
                               "symbol": "US.SPY", "side": side, "qty": 10})
        store.append(SESSION, {"event": "acknowledged", **link, "order_id": f"o{tag}"})
        store.append(SESSION, {"event": "outcome", **link, "order_id": f"o{tag}", "status": "FILLED",
                               "qty": 10, "filled": 10, "avg_price": price})

    receipt("a", "BUY", 500.0)
    receipt("b", "SELL", 550.0)
    v = sleeve_performance(store, st, asdict(acct()), CFG)["sleeves"]["two_vote"]
    assert v["status"] == "available" and v["gross_pnl"] == 500.0 and v["capital"] == 80_000.0
    assert v["reserved_profit"] == 500.0
    receipt("c", "BUY", 500.0)
    receipt("d", "SELL", 400.0)
    v = sleeve_performance(store, st, asdict(acct()), CFG)["sleeves"]["two_vote"]
    assert v["realized_gross"] == -500.0 and v["reserved_profit"] == 500.0
    assert v["capital"] == v["limits"]["allocation_budget"] == 79_000.0
    receipt("e", "SELL", 600.0)
    receipt("f", "SELL", 450.0)
    receipt("g", "BUY", 500.0)
    st["sleeves"]["two_vote"] = {"US.SPY": -10}
    v = sleeve_performance(store, st, asdict(acct(positions={"US.SPY": -10})), CFG)["sleeves"]["two_vote"]
    assert v["open_entry_lots"] == {"US.SPY": 1} and v["reserved_profit"] == 1_500.0 and v["capital"] == 79_000.0


def _vote_report(opened, k, spy, qqq=0):
    def row(signal):
        return {"buy": 3 if signal > 0 else 0, "sell": 3 if signal < 0 else 0, "rules": [],
                "entry_window": True, "signal": signal}
    return {"status": "current", "bar_end": (opened + timedelta(minutes=5 * k)).isoformat(),
            "tickers": {"US.SPY": row(spy), "US.QQQ": row(qqq)}}


def _run_vote_step(runner, monkeypatch, k, spy):
    opened = NOW.replace(hour=9, minute=30)
    runner._vote_monitor = SimpleNamespace(observe=lambda now: _vote_report(opened, k, spy))
    monkeypatch.setitem(runner.monitor.__globals__, "datetime", _clock(opened + timedelta(minutes=5 * k, seconds=20)))
    runner.vote_step()


def _recorded_vote_lots(runner):
    perf = sleeve_performance(runner.store, runner.store.load(), asdict(runner.account()), runner.cfg)
    return perf["sleeves"]["two_vote"]["open_entry_lots"]


def _historic_vote_lots(runner):
    """Mechanics tests written around $10k lots, 4 per ticker and a $50 target. The live sizes are plain config
    (see test_vote_lot_uses_configured_size_within_book_name_cap_and_cash); copy, never mutate the shared CFG."""
    runner.cfg = {**runner.cfg, "two_vote": {**runner.cfg["two_vote"], "entry_usd": 10_000, "max_lots_per_symbol": 4,
                                             "take_profit_usd": 50}}
    if getattr(runner, "broker", None) is not None:
        runner.broker.cfg = runner.cfg


def _profit_quotes(runner, age=0):
    runner.market.timestamped_quotes = lambda symbols: {
        sym: (runner.quotes[sym], runner.take_profit.__globals__["datetime"].now(timezone.utc) - timedelta(seconds=age))
        for sym in symbols
    }


def _profit_buy(runner, sleeve, symbol, qty, tag="buy"):
    price = runner.quotes[symbol]
    intent = planner.Intent(f"{sleeve}-{symbol}-{tag}", NOW.isoformat(), sleeve, symbol, "BUY", qty, price, price, "test", False)
    runner.send([intent], SESSION)


def test_profit_exit_uses_legacy_h_target_and_remembers_new_h_target(runner):
    runner.cfg = load_config(PROJECT)
    _profit_quotes(runner)
    _profit_buy(runner, "h_etf", "US.IWM", 10)
    runner.state.pop("h_take_profit_targets")  # Old $2,000 entry had no per-position target in its saved state.
    _profit_buy(runner, "h_etf", "US.QQQ", 10)
    runner.state = runner.store.load()
    assert runner.state["h_take_profit_targets"] == {"US.QQQ": 20}

    runner.quotes.update({"US.IWM": 200.50, "US.QQQ": 401})
    runner.take_profit()
    assert [(i.symbol, i.qty, i.reason) for i in runner.sent if i.close_only] == [("US.IWM", 10, "h_etf_take_profit")]
    assert runner.store.load()["take_profit_counts"] == {"h_etf": {"US.IWM": 1}}
    assert runner.state["sleeves"]["h_etf"] == {"US.QQQ": 10}

    runner.quotes["US.QQQ"] = 402
    runner.take_profit()
    assert runner.state["sleeves"]["h_etf"] == {} and runner.state["h_take_profit_targets"] == {}
    assert runner.store.load()["take_profit_counts"]["h_etf"] == {"US.IWM": 1, "US.QQQ": 1}


def test_two_vote_profit_exit_rejects_stale_data_and_retries_partial_without_counting_again(runner, monkeypatch):
    runner.cfg = load_config(PROJECT)
    _historic_vote_lots(runner)
    monkeypatch.setattr(runner, "risk", runner.account)
    _profit_buy(runner, "two_vote", "US.SPY", 20)
    _profit_buy(runner, "two_vote", "US.QQQ", 20)
    runner.quotes.update({"US.SPY": 502.50, "US.QQQ": 402.49})
    _profit_quotes(runner, age=40)
    runner.take_profit()
    assert len(runner.sent) == 2 and "take_profit_counts" not in runner.state

    _profit_quotes(runner)
    runner.fill[("US.SPY", "SELL")] = 1
    runner.take_profit()
    assert (runner.sent[-1].symbol, runner.sent[-1].qty, runner.sent[-1].reason) == ("US.SPY", 20, "two_vote_take_profit")
    runner.state = runner.store.load()
    assert runner.state["take_profit_pending"]["two_vote"] == {"US.SPY": runner.sent[-1].take_profit_lot_id}
    assert runner.state["sleeves"]["two_vote"] == {"US.SPY": 19, "US.QQQ": 20}

    runner.fill.clear()
    runner.quotes["US.SPY"] = 500.50
    monkeypatch.setitem(runner.take_profit.__globals__, "datetime", _clock(NOW + timedelta(seconds=10)))
    runner.take_profit()
    assert (runner.sent[-1].symbol, runner.sent[-1].qty) == ("US.SPY", 19)
    assert runner.store.load()["take_profit_counts"] == {"two_vote": {"US.SPY": 1}}
    assert runner.state["take_profit_pending"]["two_vote"] == {}
    activations = [r for r in runner.store.outbox(SESSION) if r["event"] == "take_profit_activation"]
    assert len(activations) == 1 and activations[0]["shares"] == 20 and activations[0]["target_usd"] == 50
    submitted = len(runner.sent)
    _run_vote_step(runner, monkeypatch, 20, 1)                         # agreement still holds after the profit exit
    reentry = runner.sent[-1]
    assert len(runner.sent) == submitted + 1
    assert (reentry.symbol, reentry.side, reentry.close_only, reentry.reason) == ("US.SPY", "BUY", False, "two_vote_long")
    _run_vote_step(runner, monkeypatch, 21, 0)                         # lapsed: nothing new
    assert len(runner.sent) == submitted + 1
    _run_vote_step(runner, monkeypatch, 22, 1)
    assert runner.sent[-1].symbol == "US.SPY" and runner.sent[-1].side == "BUY"


def test_two_vote_profit_exits_only_the_qualifying_entry_lot(runner):
    runner.cfg = load_config(PROJECT)
    _historic_vote_lots(runner)
    _profit_quotes(runner)
    runner.quotes["US.QQQ"] = 655
    _profit_buy(runner, "two_vote", "US.QQQ", 15, "first")
    runner.quotes["US.QQQ"] = 649
    _profit_buy(runner, "two_vote", "US.QQQ", 15, "second")
    runner.state = runner.store.load()
    runner.quotes["US.QQQ"] = 653

    runner.take_profit()
    exit = runner.sent[-1]
    assert exit.close_only and exit.side == "SELL" and exit.qty == 15
    assert exit.take_profit_lot_id.endswith("-second")
    performance = runner.attribute_capital(runner.account())["sleeves"]["two_vote"]
    assert performance["positions"]["US.QQQ"]["cost_basis"] == 655 * 15
    assert performance["realized_gross"] == 60
    assert performance["open_entry_lots"] == {"US.QQQ": 1}
    assert runner.state["take_profit_pending"]["two_vote"] == {}
    assert runner.state["take_profit_counts"]["two_vote"] == {"US.QQQ": 1}



def test_two_vote_short_lot_takes_profit_at_a_configured_target_when_price_falls(runner):
    """With a $100 target set, 15 QQQ sold short at $634.875 covers once the lot is up $100 (price <= ~$628.21)."""
    runner.cfg = load_config(PROJECT)
    _profit_quotes(runner)
    runner.quotes["US.QQQ"] = 634.875
    short = planner.Intent("two_vote-US.QQQ-short", NOW.isoformat(), "two_vote", "US.QQQ", "SELL", 15, 634.875, 634.875,
                           "two_vote_short", False, opens_short=True)
    runner.send([short], SESSION)
    runner.state = runner.store.load()
    assert runner.state["sleeves"]["two_vote"] == {"US.QQQ": -15}
    sent = len(runner.sent)
    runner.cfg = {**runner.cfg, "two_vote": {**runner.cfg["two_vote"], "take_profit_usd": 100}}   # live config has none
    for price in (640.0, 631.50):          # a loss, then +$50.63 (the old target): neither reaches $100
        runner.quotes["US.QQQ"] = price
        runner.take_profit()
        assert len(runner.sent) == sent, price
    runner.quotes["US.QQQ"] = 628.0        # 15 x (634.875 - 628.0) = +$103.13
    runner.take_profit()
    cover = runner.sent[-1]
    assert (cover.side, cover.qty, cover.close_only, cover.reason) == ("BUY", 15, True, "two_vote_take_profit")
    assert cover.take_profit_lot_id.endswith("-short")
    assert runner.state["sleeves"]["two_vote"] == {}
    assert runner.state["take_profit_counts"]["two_vote"] == {"US.QQQ": 1}
    activation = [r for r in runner.store.outbox(SESSION) if r["event"] == "take_profit_activation"][-1]
    assert activation["target_usd"] == 100 and activation["gross_usd"] == pytest.approx(103.125)


def test_live_two_vote_has_no_profit_target_but_h_etf_keeps_its_target(runner):
    """Exit-backtest decision 2026-11-25: Two-vote lets winners run (vote flip or scheduled flatten only)."""
    runner.cfg = load_config(PROJECT)
    assert runner.cfg["two_vote"]["take_profit_usd"] is None and runner.cfg["h_etf"]["take_profit_usd"] == 20
    _profit_quotes(runner)
    runner.quotes.update({"US.QQQ": 400.0, "US.IWM": 200.0})
    _profit_buy(runner, "two_vote", "US.QQQ", 50)
    _profit_buy(runner, "h_etf", "US.IWM", 10)
    runner.state = runner.store.load()
    sent = len(runner.sent)
    runner.quotes.update({"US.QQQ": 440.0, "US.IWM": 203.0})       # two_vote +$2,000, h_etf +$30
    runner.take_profit()
    closes = [(i.sleeve, i.symbol, i.reason) for i in runner.sent[sent:]]
    assert closes == [("h_etf", "US.IWM", "h_etf_take_profit")]
    assert runner.state["sleeves"]["two_vote"] == {"US.QQQ": 50}

@pytest.mark.parametrize("overlap", [None, "cash", "other_order"])
def test_two_vote_close_reconciles_cancelled_remainder_from_moomoo(runner, monkeypatch, overlap):
    from dual_strategy.competition import broker as broker_module

    runner.cfg = load_config(PROJECT)
    runner.cfg["risk"]["order_wait_seconds"] = 0
    _historic_vote_lots(runner)
    _profit_quotes(runner)
    runner.quotes["US.QQQ"] = 655
    _profit_buy(runner, "two_vote", "US.QQQ", 15, "first")
    runner.quotes["US.QQQ"] = 649
    _profit_buy(runner, "two_vote", "US.QQQ", 15, "second")
    runner.quotes["US.QQQ"] = 653
    monkeypatch.delattr(runner, "audit")
    monkeypatch.delattr(runner.broker, "place")
    monkeypatch.delattr(runner.broker, "wait")
    clock = runner.take_profit.__globals__["datetime"]
    monkeypatch.setattr(broker_module, "time", SimpleNamespace(monotonic=lambda: 0, sleep=lambda *_: None))
    runner.broker.acc_id = "test-paper"
    net, cash = 30, 80000.0
    submitted, history = [], []
    cancellations = []

    def account(extra=()):
        result = acct(positions={"US.QQQ": net}, quotes=runner.quotes)
        result.cash = cash
        return result

    def cli(argv):
        nonlocal net, cash
        args = argv[1:]
        if args[:3] == ["sim", "order", "sell"]:
            qty = int(args[4])
            stamp = int(clock.now(timezone.utc).timestamp()) * 1_000_000
            order = {"order_id": f"new-{len(submitted)}", "status": 2, "symbol": "QQQ", "side": 2,
                     "text": args[args.index("--text") + 1], "qty": str(qty), "cum_qty": "0",
                     "price": args[args.index("--price") + 1], "avg_fill_price": "653.6",
                     "create_time": stamp, "update_time": stamp}
            history.append(order)
            submitted.append(qty)
            if len(submitted) > 1:
                order.update(status=4, cum_qty=str(qty))
                net -= qty
                cash += qty * 653.6 - 1
            return json.dumps({"order_id": order["order_id"]})
        if args[:3] == ["sim", "order", "cancel"]:
            cancellations.append(args[3])
            history[-1].update(status=5, qty="5")  # Moomoo reports only the remaining quantity and zero fills.
            net -= 10
            cash += 10 * 653.6 - (2 if overlap == "cash" else 1)
            return "{}"
        if args[:3] == ["sim", "order", "history"]:
            rows = history.copy()
            if cancellations and overlap == "other_order":
                rows.append({**history[-1], "order_id": "outside", "status": 4})
            return json.dumps({"data": {"orders": rows}})
        if args[:3] == ["sim", "account", "funds"]:
            return json.dumps({"balance": str(cash), "short_mv": "0"})
        if args[:2] == ["sim", "position"]:
            return json.dumps({"positions": [{"symbol": "QQQ", "qty": str(net)}]})
        raise AssertionError(args)

    runner.broker.call = CliTransport(frozenset({"test-paper"}), frozenset(), cli_path="fake", runner=cli)
    monkeypatch.setattr(runner, "account", account)
    if overlap:
        with pytest.raises(ExecutionBlocked, match="did not reconcile"):
            runner.take_profit()
        assert runner.store.load()["halted"]["kind"] == "order_error"
        assert runner.state["sleeves"]["two_vote"] == {"US.QQQ": 30} and submitted == [15]
        return

    runner.take_profit()
    assert submitted == [15] and runner.state["sleeves"]["two_vote"] == {"US.QQQ": 20}
    outcome = runner.store.outbox(SESSION)[-1]
    assert outcome["filled"] == 10 and outcome["broker_terminal"]["qty"] == 5
    runner.state = runner.store.load()
    monkeypatch.setitem(runner.take_profit.__globals__, "datetime", _clock(NOW + timedelta(seconds=10)))
    runner.take_profit()
    assert submitted == [15, 5] and len(cancellations) == 1
    assert runner.state["sleeves"]["two_vote"] == {"US.QQQ": 15}
    assert runner.state["halted"] is None and runner.state["take_profit_counts"]["two_vote"] == {"US.QQQ": 1}


def test_vote_cannot_open_against_h_inventory_and_h_cannot_open_against_vote():
    long_h = state(h_etf={"US.SPY": 20})
    intents, why = planner.plan_vote_entry(long_h, acct(positions={"US.SPY": 20}), SESSION, "US.SPY", -1, CFG, NOW, "e6")
    assert not intents and "opposing position in h_etf" in why
    short_vote = state(two_vote={"US.SPY": -20})
    intents, why = planner.plan_h_open(short_vote, acct(positions={"US.SPY": -20}), SESSION,
                                       {"entries": ["US.SPY", "US.IWM"]}, CFG, NOW)
    assert [i.symbol for i in intents] == ["US.IWM"] and "opposing position in two_vote" in why


def test_vote_step_adds_a_lot_each_bar_while_the_agreement_holds_up_to_the_cap(runner, monkeypatch):
    """User decisions 2026-11-25: a continuing 2-vote agreement adds one lot per bar until the per-ticker dollar cap is
    full (no lot count: $39,500 = 39.5% of NAV here, so two $20k lots) and re-enters after exits; never on an
    opposite-agreement exit bar or while a profit exit works."""
    monkeypatch.setattr(runner, "risk", runner.account)
    assert (runner.cfg["two_vote"]["entry_usd"], runner.cfg["two_vote"]["max_lots_per_symbol"]) == (20_000, None)

    def step(k, spy):
        _run_vote_step(runner, monkeypatch, k, spy)

    step(6, 1)
    assert [(i.sleeve, i.side) for i in runner.sent] == [("two_vote", "BUY")]
    lot = runner.sent[0].qty
    assert lot * runner.sent[0].limit_price <= 20_000 + 1e-6
    assert _recorded_vote_lots(runner) == {"US.SPY": 1} and runner.state["sleeves"]["two_vote"]["US.SPY"] == lot
    step(6, 1)                                                                   # same slot again: nothing
    assert len(runner.sent) == 1
    step(7, 1)                                                                   # agreement still holds: lot 2
    assert len(runner.sent) == 2 and _recorded_vote_lots(runner) == {"US.SPY": 2}
    assert runner.state["sleeves"]["two_vote"]["US.SPY"] == 2 * lot
    step(8, 1)                                                                   # dollar cap full ($39,000 of $39,500): nothing
    step(9, 0)                                                                   # agreement lapses: hold
    assert len(runner.sent) == 2
    step(10, -1)                                                                 # opposite agreement: close all lots
    assert len(runner.sent) == 3                                                 # no re-entry on the exit bar
    assert [(i.side, i.qty, i.close_only) for i in runner.sent][-1] == ("SELL", 2 * lot, True)
    assert runner.sent[-1].reason == "two_vote_opposite_agreement"
    assert not runner.state["sleeves"].get("two_vote") and _recorded_vote_lots(runner) == {}
    step(11, -1)                                                                 # next bar: short lot 1
    step(12, -1)                                                                 # still agreeing: short lot 2
    assert [(i.side, i.opens_short) for i in runner.sent[-2:]] == [("SELL", True), ("SELL", True)]
    assert _recorded_vote_lots(runner) == {"US.SPY": 2} and runner.state["sleeves"]["two_vote"]["US.SPY"] < 0
    runner.flatten_sleeves(("two_vote",), "two_vote_scheduled_flatten")
    assert _recorded_vote_lots(runner) == {} and not runner.state["sleeves"].get("two_vote")
    sent = len(runner.sent)
    runner.state.setdefault("take_profit_pending", {})["two_vote"] = {"US.SPY": "lot-still-closing"}
    step(13, -1)                                                                 # a profit exit is still working
    assert len(runner.sent) == sent
    runner.state["take_profit_pending"]["two_vote"] = {}
    step(14, -1)                                                                 # cleared: re-enter
    assert len(runner.sent) == sent + 1 and runner.sent[-1].opens_short


def test_two_vote_short_backs_out_when_two_rules_turn_buy_before_profit_target(runner, monkeypatch):
    """Short on 2+ sells, add a lot while they hold, keep it while it lapses; 2+ buys cover every share before any target."""
    monkeypatch.setattr(runner, "risk", runner.account)
    opened = NOW.replace(hour=9, minute=30)

    def step(k, qqq):
        runner._vote_monitor = SimpleNamespace(observe=lambda now: _vote_report(opened, k, 0, qqq))
        monkeypatch.setitem(runner.monitor.__globals__, "datetime", _clock(opened + timedelta(minutes=5 * k, seconds=20)))
        runner.vote_step()

    step(12, -1)                                                       # 2+ sells: open a short
    entry = runner.sent[-1]
    assert (entry.symbol, entry.side, entry.opens_short) == ("US.QQQ", "SELL", True)
    short = runner.state["sleeves"]["two_vote"]["US.QQQ"]
    assert short < 0
    step(13, -1)                                                       # agreement still holds: second short lot
    assert len(runner.sent) == 2 and runner.sent[-1].opens_short
    short = runner.state["sleeves"]["two_vote"]["US.QQQ"]
    step(14, 0)                                                        # agreement lapses: still hold both lots
    assert len(runner.sent) == 2 and runner.state["sleeves"]["two_vote"]["US.QQQ"] == short
    step(15, 1)                                                        # 2+ rules vote Buy: back out of everything
    cover = runner.sent[-1]
    assert (cover.symbol, cover.side, cover.qty, cover.close_only, cover.reason) == (
        "US.QQQ", "BUY", -short, True, "two_vote_opposite_agreement")
    assert not runner.state["sleeves"].get("two_vote") and len(runner.sent) == 3   # no long re-entry on the exit bar

@pytest.mark.parametrize("partial_exit", [True, False], ids=["partial-exit", "restart"])
def test_vote_lot_cap_uses_confirmed_fills(runner, monkeypatch, partial_exit):
    _historic_vote_lots(runner)
    monkeypatch.setattr(runner, "risk", runner.account)
    for k, side in enumerate([1, 0, 1, 0, 1, 0], 6):
        _run_vote_step(runner, monkeypatch, k, side)
    assert [i.qty for i in runner.sent] == [19] * 3
    if partial_exit:
        _run_vote_step(runner, monkeypatch, 12, 1)
        runner.fill[("US.SPY", "SELL")] = 1
        _run_vote_step(runner, monkeypatch, 13, -1)
        assert runner.sent[-1].qty == 76 and runner.account().positions == {"US.SPY": 75}
    else:
        class Interrupted(BaseException):
            pass

        send = runner.send

        def interrupt_after_send(*args, **kwargs):
            send(*args, **kwargs)
            raise Interrupted

        runner.state["intraday"]["vote_lots"] = {"US.SPY": 3}  # Older saves can contain a stale counter.
        with monkeypatch.context() as m:
            m.setattr(runner, "send", interrupt_after_send)
            with pytest.raises(Interrupted):
                _run_vote_step(runner, m, 12, 1)
        runner.state = runner.store.load()
        assert runner.account().positions == {"US.SPY": 76}
    submitted = len(runner.sent)
    assert _recorded_vote_lots(runner) == {"US.SPY": 4}
    _run_vote_step(runner, monkeypatch, 14, 1)
    assert len(runner.sent) == submitted
    assert runner.state["intraday"]["decisions"][-1]["action"] == "entry blocked: maximum open lots"


def test_vote_step_entries_only_between_1000_and_1430(runner, monkeypatch):
    opened = NOW.replace(hour=9, minute=30)
    monkeypatch.setattr(runner, "risk", lambda symbols=(): runner.account(symbols))
    report = {}
    runner._vote_monitor = SimpleNamespace(observe=lambda now: report)
    for k in (5, 61):
        report.clear()
        report.update(_vote_report(opened, k, 1, 1))
        monkeypatch.setitem(runner.monitor.__globals__, "datetime",
                            _clock(opened + timedelta(minutes=5 * k, seconds=20)))
        runner.vote_step()
    assert runner.sent == []
