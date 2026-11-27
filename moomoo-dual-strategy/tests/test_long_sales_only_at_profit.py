"""User decision 2026-11-25: never sell a long at a loss; only shorts may be closed at a loss. A short signal on a
ticker held long is traded in its twin ETF (SPY -> IVV, QQQ -> QQQM) so the long lots are kept (same decision).

Every path that can sell a long is covered with the LIVE config: opposite votes, the scheduled flatten, the daily-loss
lock and drawdown halt, the H exit at the open, a manual flatten, and the last-line guard inside Runner.send. Lots are
judged one by one on their own entry price and size (the old $10k lots and the new $20k lots each have their own
break-even), net of the $0.99 entry fee and the sale's $0.99 + activity fee, at the worst-case fill (the SELL limit).
"""

import json
import math
import runpy
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from dual_strategy.competition import planner
from dual_strategy.competition.broker import CompetitionBroker
from dual_strategy.competition.store import Store, load_config, sleeve_performance
from dual_strategy.data.calendar import NY

PROJECT = Path(__file__).resolve().parents[1]
CFG = load_config(PROJECT)                       # the live config, unmodified
NOW = datetime(2026, 11, 23, 11, 0, tzinfo=NY)
SESSION = "2026-11-23"
LOT_A = "a:2026-11-23-two_vote-e1-QQQ-BUY"       # 15 QQQ @ 639.784 (an old $10k lot)
LOT_B = "b:2026-11-23-two_vote-e2-QQQ-BUY"       # 15 QQQ @ 638.064 (an old $10k lot)


def acct(positions=None, quotes=None, nav=100_000.0):
    quotes = {"US.SPY": 500.0, "US.QQQ": 639.0, "US.XLP": 100.0, **(quotes or {})}
    positions = positions or {}
    cash = nav - sum(q * quotes[s] for s, q in positions.items())
    return planner.Account(datetime.now(timezone.utc), cash, positions, quotes, {s: 1.0 for s in quotes},
                           {s: (80_000.0 if s == "two_vote" else 20_000.0) for s in planner.SLEEVES})


def qqq_basis(status="available"):
    lots = [{"id": LOT_A, "shares": 15, "entry_price": 639.784, "side": 1},
            {"id": LOT_B, "shares": 15, "entry_price": 638.064, "side": 1}]
    return {"two_vote": {"status": status, "positions": {"US.QQQ": {"qty": 30, "cost_basis": 15 * (639.784 + 638.064)}},
                         "entry_lots": {"US.QQQ": lots}}}


def qqq_state(**extra):
    st = planner.new_state()
    st["sleeves"]["two_vote"] = {"US.QQQ": 30}
    for sleeve, book in extra.items():
        st["sleeves"][sleeve] = dict(book)
    return st


# ------------------------------------------------------------------ the live config
def test_live_config_never_sells_a_long_at_a_loss():
    r = CFG["risk"]
    assert r["long_sales_only_at_profit"] is True and r["min_long_sale_net_usd"] == 0.0
    assert planner.loss_guard(CFG) and planner.min_sale_net(CFG) == 0.0
    assert CFG["two_vote"]["take_profit_usd"] is None                      # no target: exits are votes and the flatten
    assert planner.loss_guard({"risk": {"long_sales_only_at_profit": "yes"}}) is False   # only a real true enables it
    assert planner.min_sale_net({"risk": {"min_long_sale_net_usd": -5}}) == 0.0          # never a loss allowance


# ------------------------------------------------------------------ money: each lot has its own break-even
@pytest.mark.parametrize("entry,shares,floor", [
    (719.63, 13, 720.15),      # SPY lot carried from 2026-11-24 ($10k size)
    (639.784, 15, 640.24),     # QQQ lot carried from 2026-11-24 ($10k size)
    (638.064, 15, 638.52),     # QQQ lot carried from 2026-11-24 ($10k size)
    (720.00, 27, 720.44),      # a new $20k SPY lot: more shares spread the fixed fees thinner
])
def test_each_lot_sells_only_at_or_above_its_own_break_even(entry, shares, floor):
    assert planner.break_even_quote(entry, shares, CFG) == floor
    limit = lambda quote: planner._limit(quote, "SELL", CFG)
    assert planner.long_sale_net(entry, shares, limit(floor)) > 0
    assert planner.long_sale_net(entry, shares, limit(round(floor - 0.01, 2))) <= 0   # one cent lower is not a profit


def test_fees_match_the_broker_corroborated_schedule():
    assert planner.sale_fee(15) == pytest.approx(0.99 + 15 * 0.000166)
    assert planner.sale_fee(100_000) == pytest.approx(0.99 + 8.30)                     # activity fee cap
    # $0.30 a share on 15 shares is $4.50 gross, $2.51 after the $0.99 entry and $0.99 + $0.0025 sale fees
    assert planner.long_sale_net(100.0, 15, 100.30) == pytest.approx(4.50 - 0.99 - 0.99 - 0.00249)
    assert planner.long_sale_net(100.0, 15, 100.13) < 0                               # $1.95 gross: a net loss


# ------------------------------------------------------------------ the guard itself
def test_long_sale_block_judges_the_named_lot_or_the_oldest_lots_first():
    st, basis, limit = qqq_state(), qqq_basis(), planner._limit(639.0, "SELL", CFG)    # 638.68
    assert planner.long_sale_block(st, "two_vote", "US.QQQ", 15, limit, CFG, basis, LOT_B) is None
    assert "net" in planner.long_sale_block(st, "two_vote", "US.QQQ", 15, limit, CFG, basis, LOT_A)
    # Unnamed sales are attributed oldest first (the store's rule): LOT_A is sold first and it is at a loss.
    assert "639.7840" in planner.long_sale_block(st, "two_vote", "US.QQQ", 15, limit, CFG, basis)
    assert "639.7840" in planner.long_sale_block(st, "two_vote", "US.QQQ", 30, limit, CFG, basis)
    assert planner.long_sale_block(st, "two_vote", "US.QQQ", 31, 700.0, CFG, basis) == "close exceeds the verified lots"
    assert "not open" in planner.long_sale_block(st, "two_vote", "US.QQQ", 16, 700.0, CFG, basis, LOT_B)
    assert planner.long_sale_block(st, "two_vote", "US.QQQ", 30, 641.0, CFG, basis) is None   # both lots in profit


@pytest.mark.parametrize("basis", [None, {}, qqq_basis("incomplete")])
def test_an_unverified_cost_basis_never_sells_a_long(basis):
    why = planner.long_sale_block(qqq_state(), "two_vote", "US.QQQ", 15, 700.0, CFG, basis, LOT_B)
    assert why.startswith("cost basis unverified")


def test_shorts_and_disabled_rule_are_not_guarded():
    short = planner.new_state()
    short["sleeves"]["two_vote"] = {"US.QQQ": -30}
    assert planner.long_sale_block(short, "two_vote", "US.QQQ", 30, 600.0, CFG, None) is None
    off = {**CFG, "risk": {**CFG["risk"], "long_sales_only_at_profit": False}}
    assert planner.long_sale_block(qqq_state(), "two_vote", "US.QQQ", 30, 600.0, off, None) is None


# ------------------------------------------------------------------ planned exits
def test_flatten_sells_only_lots_in_profit_and_still_covers_shorts_at_a_loss():
    st = qqq_state(noise={"US.SPY": -10})                                     # a short now at a loss
    a = acct({"US.QQQ": 30, "US.SPY": -10}, {"US.SPY": 510.0})
    kept = []
    out = planner.plan_flatten(st, a, SESSION, "daily_loss_lock", CFG, NOW, basis=qqq_basis(), kept=kept)
    assert [(i.side, i.symbol, i.qty, i.close_only) for i in out] == [("BUY", "US.SPY", 10, True), ("SELL", "US.QQQ", 15, True)]
    assert out[1].take_profit_lot_id == LOT_B and out[1].limit_price == 638.68
    assert len({i.intent_id for i in out}) == 2
    assert kept == [{"sleeve": "two_vote", "symbol": "US.QQQ", "lot_id": LOT_A, "shares": 15, "entry_price": 639.784,
                     "limit": 638.68, "net": -18.54, "break_even_quote": 640.24}]   # 15 x -1.104 - 0.99 - 0.99249
    # Without a cost basis no long is sold at all; shorts are still covered.
    kept = []
    out = planner.plan_flatten(st, a, SESSION, "drawdown_halt", CFG, NOW, kept=kept)
    assert [(i.side, i.symbol) for i in out] == [("BUY", "US.SPY")] and kept[0]["why"] == "cost basis unverified"
    # A mismatch with the broker never sells a long either.
    kept = []
    out = planner.plan_flatten(st, acct({"US.QQQ": 25, "US.SPY": -10}, {"US.SPY": 510.0}), SESSION, "x", CFG, NOW,
                               basis=qqq_basis(), kept=kept)
    assert [(i.side, i.symbol) for i in out] == [("BUY", "US.SPY")] and kept[0]["why"] == "attribution mismatch"


def test_every_lot_in_profit_is_sold_each_with_its_own_order():
    out = planner.plan_flatten(qqq_state(), acct({"US.QQQ": 30}, {"US.QQQ": 641.0}), SESSION, "two_vote_scheduled_flatten",
                               CFG, NOW, ("two_vote",), basis=qqq_basis())
    assert [(i.qty, i.take_profit_lot_id) for i in out] == [(15, LOT_A), (15, LOT_B)]
    assert len({i.intent_id for i in out}) == 2


def test_h_exit_at_the_open_waits_for_a_profit():
    st = planner.new_state()
    st["sleeves"]["h_etf"] = {"US.XLP": 200}
    basis = {"h_etf": {"status": "available", "positions": {"US.XLP": {"qty": 200, "cost_basis": 20_000.0}},
                       "entry_lots": {}}}
    plan = {"exits": {"US.XLP": "max_hold"}, "entries": []}
    kept = []
    out, _ = planner.plan_h_open(st, acct({"US.XLP": 200}), SESSION, plan, CFG, NOW, basis=basis, kept=kept)
    assert out == [] and kept[0]["symbol"] == "US.XLP" and kept[0]["break_even_quote"] == 100.07
    out, _ = planner.plan_h_open(st, acct({"US.XLP": 200}, {"US.XLP": 100.07}), SESSION, plan, CFG, NOW, basis=basis)
    assert [(i.side, i.qty, i.reason) for i in out] == [("SELL", 200, "h_max_hold")]


# ------------------------------------------------------------------ the runner (fake broker, real receipts and audits)
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
    planner.mark_risk(r.state, acct(), SESSION, r.cfg)
    r._provenance_id = "p"
    r.broker = CompetitionBroker.__new__(CompetitionBroker)
    r.broker.cfg = r.cfg
    r.quotes = {"US.SPY": 500.0, "US.QQQ": 639.0, "US.XLP": 100.0}
    r.market = SimpleNamespace(quote=lambda s: (r.quotes[s], datetime.now(timezone.utc)))
    r.sent = []
    orders = iter(range(1000))

    def place(i):
        r.sent.append(i)
        return f"o{next(orders)}"

    def wait(order_id, placed, seconds):
        i = r.sent[-1]
        return {"order_id": order_id, "status": "FILLED", "filled": i.qty, "avg_price": r.quotes[i.symbol], "qty": i.qty}

    r.broker.place, r.broker.wait = place, wait

    def account(extra=()):
        held = {}
        for book in r.state["sleeves"].values():
            for s, q in book.items():
                held[s] = held.get(s, 0) + q
        return acct(held, r.quotes)

    monkeypatch.setattr(r, "account", account)
    monkeypatch.setattr(r, "session", lambda now=None: SESSION)
    monkeypatch.setitem(r.monitor.__globals__, "datetime", _clock(NOW))
    return r


def buy(runner, sleeve, symbol, qty, price, tag):
    runner.quotes[symbol] = price
    intent = planner.Intent(f"{SESSION}-{sleeve}-{tag}-{symbol.split('.')[-1]}-BUY", NOW.isoformat(), sleeve, symbol,
                            "BUY", qty, price, price, "test_entry", False)
    runner.send([intent], SESSION)
    runner.sent.clear()


def two_qqq_lots(runner):
    buy(runner, "two_vote", "US.QQQ", 15, 639.784, "e1")
    buy(runner, "two_vote", "US.QQQ", 15, 638.064, "e2")
    lots = sleeve_performance(runner.store, runner.state, asdict(runner.account()), runner.cfg)["sleeves"]["two_vote"]
    return [lot["id"] for lot in lots["entry_lots"]["US.QQQ"]]


def held_events(runner):
    return [r for r in runner.store.outbox(SESSION) if r.get("event") == "long_sale_held"]


def error_events(runner):
    return [r for r in runner.store.outbox(SESSION) if r.get("event") in ("order_refused", "order_error", "runner_error")]


def remaining_lots(runner):
    perf = sleeve_performance(runner.store, runner.state, asdict(runner.account()), runner.cfg)["sleeves"]["two_vote"]
    return [(lot["shares"], lot["entry_price"]) for lot in perf["entry_lots"].get("US.QQQ", [])]


def test_scheduled_flatten_keeps_the_losing_lot_overnight_and_sells_it_once_it_profits(runner):
    old, new = two_qqq_lots(runner)
    runner.quotes["US.QQQ"] = 639.0
    runner.flatten_sleeves(("two_vote",), "two_vote_scheduled_flatten")
    assert [(i.side, i.qty, i.take_profit_lot_id) for i in runner.sent] == [("SELL", 15, new)]
    assert runner.state["sleeves"]["two_vote"] == {"US.QQQ": 15} and remaining_lots(runner) == [(15, 639.784)]
    runner.flatten_sleeves(("two_vote",), "two_vote_scheduled_flatten")          # retried: nothing sold, noted once
    assert len(runner.sent) == 1
    assert [(e["lot_id"], e["break_even_quote"], e["source"]) for e in held_events(runner)] == [
        (old, 640.24, "two_vote_scheduled_flatten")]
    runner.quotes["US.QQQ"] = 640.23                                             # one cent short of the floor
    runner.flatten_sleeves(("two_vote",), "two_vote_scheduled_flatten")
    assert len(runner.sent) == 1
    runner.quotes["US.QQQ"] = 640.24
    runner.flatten_sleeves(("two_vote",), "two_vote_scheduled_flatten")
    assert [(i.qty, i.take_profit_lot_id) for i in runner.sent[1:]] == [(15, old)]
    assert not runner.state["sleeves"].get("two_vote") and error_events(runner) == []
    perf = sleeve_performance(runner.store, runner.state, asdict(runner.account()), runner.cfg)["sleeves"]["two_vote"]
    assert perf["realized_gross"] > 0 and all(f["realized_gross"] > 0 for f in
        sleeve_performance(runner.store, runner.state, asdict(runner.account()), runner.cfg)["exits"]["fills"])


def test_flatten_audit_records_the_basis_and_replays_exactly(runner):
    two_qqq_lots(runner)
    runner.quotes["US.QQQ"] = 639.0                                              # one lot in profit, one kept
    runner.flatten_sleeves(("two_vote",), "two_vote_scheduled_flatten")
    records = [json.loads(p.read_text()) for p in (runner.store.dir / "audit").glob("*/*.json")]
    saved, = [r for r in records if r["kind"] == "flatten"]
    a = saved["account"]
    account = planner.Account(datetime.fromisoformat(a["at"]), a["cash"], a["positions"], a["quotes"],
                              a["quote_age_seconds"], a["sleeve_capital"])
    replay = planner.plan_flatten(saved["state"], account, saved["session"], saved["reason"], runner.cfg,
                                  datetime.fromisoformat(saved["now"]), tuple(saved["sleeves"]), basis=saved["basis"])
    assert [i.to_dict() for i in replay] == saved["intents"] and len(replay) == 1 and saved["kept"][0]["shares"] == 15


@pytest.mark.parametrize("reason_state", [{"day_start_nav": 103_000.0}, {"high_water": 108_000.0}])
def test_loss_lock_and_halt_sell_only_profitable_longs_but_cover_shorts(runner, reason_state):
    two_qqq_lots(runner)
    runner.quotes["US.SPY"] = 500.0
    short = planner.Intent(f"{SESSION}-two_vote-e3-SPY-SELL", NOW.isoformat(), "two_vote", "US.SPY", "SELL", 10, 500.0,
                           500.0, "two_vote_short", False, opens_short=True)
    runner.send([short], SESSION)
    runner.sent.clear()
    runner.quotes.update({"US.SPY": 510.0, "US.QQQ": 639.0})                    # the short and one long lot lose
    runner.state.update(reason_state)
    runner.risk()
    assert runner.state["locked_day"] == SESSION or runner.state["halted"]["kind"] == "drawdown"
    assert [(i.side, i.symbol, i.qty) for i in runner.sent] == [("BUY", "US.SPY", 10), ("SELL", "US.QQQ", 15)]
    assert runner.state["sleeves"]["two_vote"] == {"US.QQQ": 15} and remaining_lots(runner) == [(15, 639.784)]
    runner.risk()                                                                # every later mark: still kept
    assert len(runner.sent) == 2 and len(held_events(runner)) == 1 and error_events(runner) == []


def test_send_never_places_a_losing_long_sale_whatever_planned_it(runner):
    two_qqq_lots(runner)
    sale = planner.Intent(f"{SESSION}-two_vote-manual-QQQ-SELL", NOW.isoformat(), "two_vote", "US.QQQ", "SELL", 30,
                          630.0, 630.0, "manual_flatten", True)
    assert runner.send([sale], SESSION) == [] and runner.sent == []
    held, = held_events(runner)
    assert held["source"] == "send_guard" and "net" in held["why"] and held["intent_id"] == sale.intent_id
    # Not an order_refused/order_error: an unsold loser is a decision, not an execution error, so the next session's
    # scheduler preflight is never blocked by it.
    assert error_events(runner) == [] and runner.state["sleeves"]["two_vote"] == {"US.QQQ": 30}
    assert runner.state["orders_today"] == 2


def test_manual_flatten_command_obeys_the_rule(runner):
    two_qqq_lots(runner)
    runner.quotes["US.QQQ"] = 639.0
    runner.flatten("all", "manual_flatten")
    assert [(i.qty, i.reason) for i in runner.sent] == [(15, "manual_flatten")]
    assert remaining_lots(runner) == [(15, 639.784)]


def _vote_report(opened, k, qqq):
    row = lambda signal: {"buy": 3 if signal > 0 else 0, "sell": 3 if signal < 0 else 0, "rules": [],
                          "entry_window": True, "signal": signal}
    return {"status": "current", "bar_end": (opened + timedelta(minutes=5 * k)).isoformat(),
            "tickers": {"US.SPY": row(0), "US.QQQ": row(qqq)}}


def test_opposite_votes_sell_only_lots_in_profit_and_short_the_twin_against_a_kept_long(runner, monkeypatch):
    old, new = two_qqq_lots(runner)
    runner.quotes.update({"US.QQQ": 639.0, "US.QQQM": 250.0})
    monkeypatch.setattr(runner, "risk", runner.account)
    opened = NOW.replace(hour=9, minute=30)

    def step(k, qqq):
        runner._vote_monitor = SimpleNamespace(observe=lambda now: _vote_report(opened, k, qqq))
        monkeypatch.setitem(runner.monitor.__globals__, "datetime", _clock(opened + timedelta(minutes=5 * k, seconds=20)))
        runner.vote_step()

    step(20, -1)                                    # 2+ sell votes: the lot in profit sells, the loser is kept, and
    assert [(i.side, i.symbol, i.qty, i.take_profit_lot_id, i.reason, i.opens_short) for i in runner.sent] == [
        ("SELL", "US.QQQ", 15, new, "two_vote_opposite_agreement", False),          # the short opens in the twin ETF
        ("SELL", "US.QQQM", 80, None, "two_vote_short", True)]
    kept = [d for d in runner.state["intraday"]["decisions"] if d["action"].startswith("kept")]
    assert [(d["lot"], d["break_even"]) for d in kept] == [(old, 640.24)]
    entry, = [d for d in runner.state["intraday"]["decisions"] if d["action"] == "entry"]
    assert (entry["symbol"], entry["traded"], entry["side"]) == ("US.QQQ", "US.QQQM", -1)
    step(21, -1)                                    # still 2+ sells: the long stays, one more twin lot (within the cap)
    assert [(i.side, i.symbol, i.qty) for i in runner.sent[2:]] == [("SELL", "US.QQQM", 78)]
    assert runner.state["sleeves"]["two_vote"] == {"US.QQQ": 15, "US.QQQM": -158}
    step(22, 1)                                     # buy votes: the twin short is covered, no flip on that bar
    assert [(i.side, i.symbol, i.qty, i.close_only) for i in runner.sent[3:]] == [("BUY", "US.QQQM", 158, True)]
    assert runner.state["sleeves"]["two_vote"] == {"US.QQQ": 15} and remaining_lots(runner) == [(15, 639.784)]
    step(23, 1)                                     # buy votes again: add QQQ up to the cap
    assert runner.sent[-1].side == "BUY" and runner.sent[-1].symbol == "US.QQQ" and not runner.sent[-1].close_only
    assert error_events(runner) == []


def test_short_signal_on_a_flat_ticker_shorts_the_ticker_itself(runner, monkeypatch):
    runner.quotes.update({"US.QQQ": 639.0, "US.QQQM": 250.0})
    monkeypatch.setattr(runner, "risk", runner.account)
    opened = NOW.replace(hour=9, minute=30)
    runner._vote_monitor = SimpleNamespace(observe=lambda now: _vote_report(opened, 20, -1))
    monkeypatch.setitem(runner.monitor.__globals__, "datetime", _clock(opened + timedelta(minutes=100, seconds=20)))
    runner.vote_step()
    assert [(i.side, i.symbol, i.opens_short) for i in runner.sent] == [("SELL", "US.QQQ", True)]


def test_twin_short_is_covered_by_the_flatten_lock_and_halt_while_losing_longs_are_kept(runner):
    two_qqq_lots(runner)
    runner.quotes.update({"US.QQQ": 600.0, "US.QQQM": 250.0})
    twin = planner.Intent(f"{SESSION}-two_vote-e20-QQQM-SELL", NOW.isoformat(), "two_vote", "US.QQQM", "SELL", 80,
                          250.0, 250.0, "two_vote_short", False, opens_short=True)
    runner.send([twin], SESSION)
    runner.sent.clear()
    runner.quotes["US.QQQM"] = 255.0                                             # the twin short is losing too
    runner.flatten_sleeves(("two_vote",), "two_vote_scheduled_flatten")
    assert [(i.side, i.symbol, i.qty, i.close_only) for i in runner.sent] == [("BUY", "US.QQQM", 80, True)]
    assert runner.state["sleeves"]["two_vote"] == {"US.QQQ": 30} and error_events(runner) == []


def test_twin_helpers_and_live_config():
    twins = planner.short_twins(CFG)
    assert twins == {"US.SPY": "US.IVV", "US.QQQ": "US.QQQM"}
    assert planner.twin_for("US.SPY", -1, {"US.SPY": 13}, twins) == "US.IVV"
    assert planner.twin_for("US.SPY", -1, {"US.IVV": -20}, twins) == "US.IVV"       # keeps stacking in the twin
    assert planner.twin_for("US.SPY", -1, {}, twins) == "US.SPY"
    assert planner.twin_for("US.SPY", 1, {"US.SPY": 13, "US.IVV": -20}, twins) == "US.SPY"
    assert planner.twin_for("US.SPY", -1, {"US.SPY": 13}, {}) == "US.SPY"           # no twins configured
    for bad in ({"US.SPY": "US.SPY"}, {"US.SPY": "US.QQQ", "US.QQQ": "US.QQQM"}, {"US.SPY": "IVV"}, ["US.IVV"],
                {"US.SPY": "US.IVV", "US.QQQ": "US.IVV"}):
        assert planner.short_twins({"two_vote": {"short_twins": bad}}) == {}
    assert "US.IVV" not in CFG["h_etf"]["core"] + CFG["h_etf"]["sectors"]
    assert "US.QQQM" not in CFG["h_etf"]["core"] + CFG["h_etf"]["sectors"]


def test_broker_accepts_a_two_vote_twin_short_and_still_refuses_unmarked_sells():
    b = CompetitionBroker.__new__(CompetitionBroker)
    b.cfg, b.acc_id, calls = CFG, "acct", []
    b.call = lambda tool, args, min_interval=1.0: calls.append(args) or {"data": {"order_id": "o1"}}
    short = planner.Intent(f"{SESSION}-two_vote-e20-IVV-SELL", NOW.isoformat(), "two_vote", "US.IVV", "SELL", 20,
                           700.0, 699.65, "two_vote_short", False, opens_short=True)
    assert b.place(short) == "o1" and calls[-1]["symbol"] == "US.IVV" and calls[-1]["order_side"] == 2
    with pytest.raises(Exception, match="close-only"):
        b.place(planner.Intent(f"{SESSION}-two_vote-e21-IVV-SELL", NOW.isoformat(), "two_vote", "US.IVV", "SELL", 20,
                               700.0, 699.65, "two_vote_short", False))


@pytest.mark.parametrize("quote,sold", [(100.0, False), (100.06, False), (100.07, True)])
def test_retired_h_still_exits_a_held_position_only_in_profit(runner, quote, sold):
    live = runner.cfg
    assert not runner.h_live()                     # H retired 2026-11-26: no plan, no open orders with an empty book
    runner.cfg = {**live, "risk": {**live["risk"], "entry_sleeves": ["h_etf", "two_vote"]}}   # a pre-retirement buy
    buy(runner, "h_etf", "US.XLP", 200, 100.0, "open")
    runner.cfg = live
    assert runner.h_live()                         # a held H position still follows its exit plan
    runner.state["h_entry_session"]["US.XLP"] = "2026-11-18"
    runner.state["h_plan"] = {"asof": "2026-11-20", "entries": [], "exits": {"US.XLP": "max_hold"}}
    runner.quotes["US.XLP"] = quote
    runner.h_open()
    assert [(i.side, i.qty) for i in runner.sent] == ([("SELL", 200)] if sold else [])
    assert runner.state["sleeves"]["h_etf"] == ({} if sold else {"US.XLP": 200})
    assert (len(held_events(runner)) == 0) == sold and error_events(runner) == []


def test_scheduler_lets_two_vote_longs_carry_but_never_a_short(tmp_path):
    spec = runpy.run_path(str(PROJECT / "scripts" / "schedule_competition.py"))
    source = (PROJECT / "scripts" / "schedule_competition.py").read_text()
    assert "q < 0 for q in state['sleeves'].get('two_vote', {}).values()" in source
    assert "raise Blocked('invalid_ownership')" in source                       # broker preflight: no short carries
    assert spec["Blocked"]


def test_dashboard_lot_detail_without_quotes_never_unlocks_a_sale(tmp_path):
    """Lots from balanced receipts are shown per lot even before quotes exist (ledger_lots), but the profit-only rule
    reads only fully marked, available lots: without a quote nothing can be proven profitable, so nothing is sold."""
    store = Store(tmp_path, {"state_dir": "state"})
    state = planner.new_state()
    state["initial_nav"] = 100_000.0
    for n, price in ((1, 639.784), (2, 638.064)):
        link = {"audit_id": f"a{n}", "intent_id": f"{SESSION}-two_vote-e{n}-QQQ-BUY"}
        store.append(SESSION, {"event": "intent", "mode": "EXECUTE", **link, "sleeve": "two_vote", "symbol": "US.QQQ",
                               "side": "BUY", "qty": 15})
        store.append(SESSION, {"event": "acknowledged", **link, "order_id": f"O{n}"})
        store.append(SESSION, {"event": "outcome", **link, "order_id": f"O{n}", "status": "FILLED", "filled": 15,
                               "qty": 15, "avg_price": price})
    state["sleeves"]["two_vote"] = {"US.QQQ": 30}
    unmarked = {"at": None, "cash": 0, "positions": {"US.QQQ": 30}, "quotes": {}}
    tv = sleeve_performance(store, state, unmarked, CFG)["sleeves"]["two_vote"]
    assert tv["status"] == "incomplete" and tv["entry_lots"] == {}
    assert [(lot["shares"], lot["entry_price"]) for lot in tv["ledger_lots"]["US.QQQ"]] == [(15, 639.784), (15, 638.064)]
    basis = planner.basis_view(sleeve_performance(store, state, unmarked, CFG)["sleeves"])
    assert planner.long_lots("two_vote", "US.QQQ", basis) is None
    assert planner.long_sale_block(state, "two_vote", "US.QQQ", 15, 700.0, CFG, basis).startswith("cost basis unverified")
    marked = {**unmarked, "quotes": {"US.QQQ": 641.0}}
    basis = planner.basis_view(sleeve_performance(store, state, marked, CFG)["sleeves"])
    assert planner.long_sale_block(state, "two_vote", "US.QQQ", 30, 640.68, CFG, basis) is None


def test_retired_h_20k_lets_a_twin_short_trade_beside_two_full_longs():
    """User decision 2026-11-26: H retired, its $20,000 moved to the Two-vote book ($80,000 -> $100,000). With SPY and
    QQQ longs near their caps, the old $80k book had no room for a twin short; the $100k book fits a full $20k lot."""
    assert CFG["two_vote"]["initial_allocation_usd"] == 100_000 and planner.entries_disabled(CFG, "h_etf")
    held = {"US.SPY": 79, "US.QQQ": 98}                                   # $39,500 + $39,200 long
    for book, expected in ((100_000.0, 40), (80_000.0, None)):
        st = planner.new_state()
        st.update(initial_nav=100_000.0, high_water=100_000.0, day=SESSION, day_start_nav=100_000.0)
        st["sleeves"]["two_vote"] = dict(held)
        a = acct(held, {"US.QQQ": 400.0, "US.IVV": 500.0})
        a.sleeve_capital["two_vote"] = book
        assert planner.mark_risk(st, a, SESSION, CFG) is None
        out, why = planner.plan_vote_entry(st, a, SESSION, "US.IVV", -1, CFG, NOW, "e1")
        if expected:
            assert why == "entry" and [(i.symbol, i.side, i.qty, i.opens_short) for i in out] == [
                ("US.IVV", "SELL", expected, True)]
        else:
            assert out == [] and "below the $5,000 minimum lot" in why              # $1,300 left in an $80k book
