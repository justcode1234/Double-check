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
CFG["risk"]["legacy_exit_only"] = False  # Exercise the original entry and sizing rules.


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
    return planner.Account(datetime.now(timezone.utc), cash, positions, quotes, {s: 1.0 for s in quotes},
                           {s: CFG["allocation"][s] * 100_000 for s in planner.SLEEVES})


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


@pytest.mark.parametrize('reduced,expected', [(False, 55), (True, 27)])
def test_b3_sizes_only_from_its_own_capital(reduced, expected):
    st = planner.new_state()
    planner.mark_risk(st, acct(), '2026-11-20', CFG)
    st['reduced'] = reduced
    a = acct(nav=150000)
    a.sleeve_capital = {'b3': 28000, 'h_etf': 122000}
    orders, why = planner.plan_b3(st, a, '2026-11-20', Decision(1), CFG, datetime.now(NY))
    assert why == 'entry' and orders[0].qty == expected
    assert orders[0].qty * orders[0].limit_price <= 40000


def test_h_limits_depend_on_h_capital_not_account_nav():
    st = planner.new_state()
    planner.mark_risk(st, acct(), '2026-11-20', CFG)
    symbols = ['US.XLF', 'US.XLV', 'US.XLP', 'US.XLK']
    a = acct(nav=150000, quotes={s: 100 for s in symbols})
    a.sleeve_capital = {'b3': 125000, 'h_etf': 25000}
    orders, why = planner.plan_h_open(st, a, '2026-11-20', {'exits': {}, 'entries': symbols}, CFG, datetime.now(NY))
    assert why == 'planned'
    assert [i.qty for i in orders] == [24, 24, 24, 2]
    assert all(i.qty * i.limit_price <= 2500 for i in orders)
    assert sum(i.qty * i.limit_price for i in orders) <= 7500


@pytest.mark.parametrize('capital', [None, 0, -1, float('nan'), float('inf')])
def test_unverified_capital_blocks_buys_but_not_exits(capital):
    st = planner.new_state()
    planner.mark_risk(st, acct(), '2026-11-20', CFG)
    a = acct(positions={'US.XLV': 2}, quotes={'US.XLV': 100, 'US.XLP': 100})
    a.sleeve_capital = {'b3': capital, 'h_etf': capital}
    st['sleeves']['h_etf'] = {'US.XLV': 2}
    b, why = planner.plan_b3(st, a, '2026-11-20', Decision(1), CFG, datetime.now(NY))
    assert b == [] and 'capital' in why
    h, why = planner.plan_h_open(st, a, '2026-11-20', {'exits': {'US.XLV': 'rsi_recovery'}, 'entries': ['US.XLP']}, CFG, datetime.now(NY))
    assert [(i.side, i.qty) for i in h] == [('SELL', 2)] and 'capital' in why


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


def test_h_plan_dated_before_an_entry_is_blocked_not_a_max_hold_exit():
    days = pd.bdate_range("2026-03-02", periods=130).date.tolist()
    cfg = {**CFG["h_etf"], "core": ["A"], "sectors": ["S1"], "top_sectors": 1}
    up = [100 + 0.5 * k for k in range(130)]
    daily = {s: pd.DataFrame({"close": up, "volume": 1e6, "adjusted": up}, index=days) for s in ("A", "S1")}
    p = h_plan(daily, days[-2], {"S1": days[-1]}, days[:-1], cfg)   # bought on days[-1], plan as of days[-2]
    assert p.exits == {} and p.entries == [] and "entered after" in p.blocked


@pytest.mark.parametrize("now, stored_asof, want", [
    (datetime(2026, 11, 20, 16, 16, tzinfo=NY), "2026-11-20", []),                     # evening plan kept
    (datetime(2026, 11, 23, 9, 5, tzinfo=NY), "2026-11-19", [date(2026, 11, 20)]),     # stale plan rebuilt
    (datetime(2026, 11, 23, 9, 5, tzinfo=NY), "2026-11-20", []),                       # current plan kept
])
def test_loop_never_replaces_a_newer_h_plan_with_the_prior_close(status_runner, monkeypatch, now, stored_asof, want):
    runner, _ = status_runner

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz) if tz else now.replace(tzinfo=None)

    monkeypatch.setitem(runner.loop.__globals__, 'datetime', Clock)
    runner.state['h_plan'] = {'asof': stored_asof, 'entries': [], 'exits': {}, 'blocked': None}
    planned = []
    monkeypatch.setattr(runner, 'plan_h', lambda asof=None: planned.append(asof))
    monkeypatch.setattr(runner, 'risk', lambda symbols=(): acct())

    class LoopStopped(BaseException):
        pass

    def stop(seconds):
        raise LoopStopped()

    monkeypatch.setattr(runner.loop.__globals__['time'], 'sleep', stop)
    with pytest.raises(LoopStopped):
        runner.loop()
    assert [d for d in planned if d != now.date()] == want     # the evening re-plan of today itself is fine


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


@pytest.fixture
def status_runner(tmp_path):
    import runpy
    from types import SimpleNamespace
    from dual_strategy.execution.moomoo_cli import CASH_INFO, POSITION_LIST

    runner_class = runpy.run_path(str(PROJECT / "scripts" / "run_competition.py"))["Runner"]
    runner = runner_class.__new__(runner_class)
    runner.cfg = {**CFG, "execution_enabled": True, "state_dir": "state"}
    runner.store = Store(tmp_path, runner.cfg)
    runner.state = runner.store.load()
    runner._provenance_id = None
    runner.broker = CompetitionBroker.__new__(CompetitionBroker)
    runner.broker.acc_id = "TEST"
    runner.market = SimpleNamespace(quote=lambda symbol: (500.0, datetime.now(timezone.utc)))
    responses = {CASH_INFO: {"balance": "97500"}, POSITION_LIST: {"positions": []}}

    def transport(tool, arguments):
        assert arguments == {"acc_id": "TEST", "market": 100}
        if tool not in responses:
            pytest.fail("status attempted an unexpected broker operation")
        return {"data": responses[tool]}

    runner.broker.transport = transport
    return runner, responses


def test_execute_status_reads_broker_nav_without_initializing_state(status_runner, capsys):
    runner, _ = status_runner
    runner.status()
    result = json.loads(capsys.readouterr().out)
    assert result["nav"] == 97500.0
    assert result["broker_positions"] == {} and result["attribution_errors"] == []
    assert result["high_water"] is None and result["halted"] is None
    assert runner.state == planner.new_state()
    assert not runner.store.dir.exists()


def test_execute_status_exposes_unowned_positions_without_resetting_halt(status_runner, capsys):
    from dual_strategy.execution.moomoo_cli import POSITION_LIST
    runner, responses = status_runner
    responses[POSITION_LIST]["positions"] = [{"symbol": "US.SPY", "qty": "5"}]
    runner.state.update(initial_nav=100000.0, high_water=104000.0, halted={"reason": "existing halt"})
    runner.store.save(runner.state)
    before = runner.store.path.read_bytes()
    runner.status()
    result = json.loads(capsys.readouterr().out)
    assert result["nav"] == 100000.0
    assert result["broker_positions"] == {"US.SPY": 5}
    assert result["attribution_errors"] == ["US.SPY: sleeves 0 vs broker 5"]
    assert result["initial_nav"] == 100000.0 and result["high_water"] == 104000.0
    assert result["halted"] == {"reason": "existing halt"}
    assert runner.store.path.read_bytes() == before
    assert runner.state == json.loads(before)
    assert not (runner.store.dir / "outbox").exists()


def test_plan_status_does_not_observe_a_broker_or_write_state(status_runner, capsys):
    runner, _ = status_runner
    runner.broker = None
    runner.status()
    result = json.loads(capsys.readouterr().out)
    assert result["mode"] == "PLAN" and result["nav"] is None
    assert result["broker_positions"] is None and result["attribution_errors"] == []
    assert not runner.store.dir.exists()


def test_runner_halts_on_rejection_before_sending_another_intent(status_runner):
    from dual_strategy.execution.moomoo_cli import HISTORY, PLACE
    runner, _ = status_runner
    submissions = []

    def transport(tool, arguments):
        if tool == PLACE:
            submissions.append(arguments)
            return {"data": {"order_id": "REJECTED-TEST"}}
        if tool == HISTORY:
            return {"data": {"orders": [{"order_id": "REJECTED-TEST", "status": 6,
                                         "cum_qty": "0", "avg_fill_price": "0", "qty": "1"}]}}
        pytest.fail("unexpected broker operation")

    runner.broker.transport = transport
    intents = [planner.Intent(str(k), "t", "h_etf", symbol, "BUY", 1, 100.0, 100.0, "r", False)
               for k, symbol in enumerate(["US.XLV", "US.XLP"])]
    with pytest.raises(ExecutionBlocked, match="rejected"):
        runner.send(intents, "2026-11-20")
    assert len(submissions) == 1
    saved = runner.store.load()
    assert saved["halted"] is not None and "rejected" in saved["halted"]["reason"]
    assert saved["orders_today"] == 1 and all(not book for book in saved["sleeves"].values())
    records = runner.store.outbox("2026-11-20")
    assert [r["event"] for r in records] == ["intent", "acknowledged", "outcome", "order_error"]
    assert records[2]["status"] == "REJECTED" and records[2]["filled"] == 0
    assert len({r["audit_id"] for r in records}) == 1


def audit_records(runner, kind):
    records = [json.loads(p.read_text()) for p in (runner.store.dir / "audit").glob("*/*.json")]
    return [r for r in records if r["kind"] == kind]


def restore_account(data):
    return planner.Account(datetime.fromisoformat(data["at"]), data["cash"], data["positions"],
                           data["quotes"], data["quote_age_seconds"], data["sleeve_capital"])


def test_audit_store_retains_distinct_full_precision_snapshots(tmp_path):
    store = Store(tmp_path, {"state_dir": "state"})
    price = 123.12345678901234
    first = store.audit("2026-11-20", {"kind": "test", "price": price})
    second = store.audit("2026-11-20", {"kind": "test", "price": 9.0})
    assert first != second
    a = json.loads((store.dir / "audit" / f"{first}.json").read_text())
    b = json.loads((store.dir / "audit" / f"{second}.json").read_text())
    assert a["price"] == price and b["price"] == 9.0
    assert a["audit_id"] == first and a["logged_at"]


def test_b3_audit_replays_signal_and_sizing_without_market_access(status_runner, monkeypatch):
    from types import SimpleNamespace
    import hashlib
    import dual_strategy.research.data as research_data
    runner, _ = status_runner
    runner.broker = None
    runner.virtual_nav = 100000.0
    day = date(2026, 11, 20)
    previous = date(2026, 11, 19)
    prior = prior_days()
    today = bars(day, [100.0, 100.1, 100.2, 100.3, 100.35, 100.4])
    runner.market = SimpleNamespace(
        b3_inputs=lambda *args, **kwargs: (prior, today),
        daily=lambda *args: pd.DataFrame({"close": [100.123456789]}, index=[previous]),
        quote=lambda symbol: (500.0, datetime.now(timezone.utc)))
    monkeypatch.setattr(research_data, "dividends", lambda symbol: {day: 0.123456789})
    runner.b3("10:00", day)
    saved, = audit_records(runner, "b3")
    inputs = saved["inputs"]
    restore_bar = lambda b: Bar5(**{**b, "end": datetime.fromisoformat(b["end"])})
    decision = b3_decision(
        [restore_bar(b) for b in inputs["today"]],
        {date.fromisoformat(d): [restore_bar(b) for b in rows] for d, rows in inputs["prior"].items()},
        inputs["prev_close"], inputs["dividend_today"], datetime.fromisoformat(inputs["at"]),
        datetime.fromisoformat(inputs["session_open"]), inputs["lookback"], inputs["width"])
    assert decision.target == saved["decision"]["target"] == 1
    assert decision.metrics == saved["decision"]["metrics"]
    assert inputs["prev_close"] == 100.123456789 and inputs["dividend_today"] == 0.123456789
    provenance, = audit_records(runner, "provenance")
    assert saved["provenance_id"] == provenance["audit_id"]
    cfg = provenance["config"]
    assert cfg["risk"]["name_cap_usd"] == 40000 and cfg["allocation"]["b3"] == 0.2
    assert "account_file" not in cfg and "state_dir" not in cfg
    source = provenance["sources"]["src/dual_strategy/competition/signals.py"]
    assert hashlib.sha256(source["text"].encode()).hexdigest() == source["sha256"]
    assert provenance["environment"]["python"] and provenance["environment"]["packages"]["pandas"]
    intents, reason = planner.plan_b3(saved["state"], restore_account(saved["account"]), saved["session"],
                                      decision, cfg, datetime.fromisoformat(saved["now"]))
    assert [i.to_dict() for i in intents] == saved["intents"]
    assert reason == saved["reason"] == "entry" and intents[0].qty == 39          # $20,000 B3 sleeve at $500.25
    assert runner.store.outbox(day.isoformat())[0]["audit_id"] == saved["audit_id"]


def test_h_audit_retains_full_panel_after_next_plan(status_runner):
    from types import SimpleNamespace
    runner, _ = status_runner
    asof = date(2026, 11, 19)
    days = pd.bdate_range(end=asof, periods=130).date.tolist()
    runner.cfg["h_etf"] = {**CFG["h_etf"], "core": ["US.SPY"], "sectors": [], "top_sectors": 0}
    up = [100 + 0.345678912345 * k for k in range(130)]
    dip = up[:-2] + [up[-3] - 3, up[-3] - 6]
    panel = {"US.SPY": pd.DataFrame({"close": dip, "volume": 1e6, "adjusted": dip}, index=days)}
    runner.market = SimpleNamespace(h_panel=lambda *args: panel)
    runner.plan_h(asof)
    original = runner.state["h_plan_audit_id"]
    path = runner.store.dir / "audit" / f"{original}.json"
    before = path.read_bytes()
    panel["US.SPY"].loc[asof, "adjusted"] += 0.0123456789
    runner.plan_h(asof)
    assert runner.state["h_plan_audit_id"] != original and path.read_bytes() == before
    saved = json.loads(before)
    inputs = saved["inputs"]
    daily = {s: pd.DataFrame(data=f["data"], columns=f["columns"],
                             index=[date.fromisoformat(d) for d in f["index"]])
             for s, f in inputs["daily"].items()}
    provenance, = audit_records(runner, "provenance")
    result = h_plan(daily, date.fromisoformat(inputs["asof"]),
                    {s: date.fromisoformat(d) for s, d in inputs["held"].items()},
                    [date.fromisoformat(d) for d in inputs["sessions"]], provenance["config"]["h_etf"])
    assert result.entries == saved["plan"]["entries"] == ["US.SPY"]
    assert result.metrics == saved["plan"]["metrics"]
    assert daily["US.SPY"].adjusted.iloc[-1] == dip[-1]
    assert result.metrics["US.SPY"]["rsi2"] != round(result.metrics["US.SPY"]["rsi2"], 4)


@pytest.mark.parametrize('mark,capital,per_etf,aggregate', [(110, 21000, 2100, 6300), (90, 19000, 1900, 5700)])
def test_runner_attributes_capital_from_receipts_not_shared_cash(status_runner, mark, capital, per_etf, aggregate):
    from dataclasses import asdict
    runner, _ = status_runner
    runner.state.update(initial_nav=100000, high_water=100000)
    runner.state['sleeves']['h_etf'] = {'US.XLV': 100}
    link = {'audit_id': '2026-11-19/h-buy', 'intent_id': 'h-buy'}
    runner.store.append('2026-11-19', {'event': 'intent', **link, 'mode': 'EXECUTE', 'sleeve': 'h_etf', 'symbol': 'US.XLV', 'side': 'BUY', 'qty': 100})
    runner.store.append('2026-11-19', {'event': 'acknowledged', **link, 'order_id': 'TEST'})
    runner.store.append('2026-11-19', {'event': 'outcome', **link, 'order_id': 'TEST', 'status': 'FILLED', 'qty': 100, 'filled': 100, 'avg_price': 100})
    account = acct(nav=120000, positions={'US.XLV': 100}, quotes={'US.XLV': mark})
    before = runner.store.outbox('2026-11-19')
    performance = runner.attribute_capital(account)
    assert account.sleeve_capital == {'b3': 20000, 'h_etf': capital, 'noise': 20000, 'vwap': 20000, 'orb': 20000}
    from dual_strategy.competition.store import sleeve_performance
    assert performance == sleeve_performance(runner.store, runner.state, asdict(account), runner.cfg)
    assert performance['sleeves']['b3']['limits']['target_before_cap'] == 20000
    assert performance['sleeves']['h_etf']['limits']['per_etf_budget'] == per_etf
    assert performance['sleeves']['h_etf']['limits']['aggregate_limit'] == aggregate
    assert runner.store.outbox('2026-11-19') == before


def test_h_open_audit_links_signal_account_intent_and_fill(status_runner, monkeypatch):
    from dual_strategy.execution.moomoo_cli import HISTORY, PLACE
    runner, _ = status_runner
    now = datetime(2026, 11, 20, 9, 30, 10, tzinfo=NY)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz) if tz else now.replace(tzinfo=None)

    monkeypatch.setitem(runner.h_open.__globals__, "datetime", Clock)
    runner.market.quote = lambda symbol: (500.0, now.astimezone(timezone.utc))
    runner.state["h_plan"] = {"asof": "2026-11-19", "entries": ["US.SPY"], "exits": {}, "blocked": None}
    signal_id = runner.store.audit("2026-11-19", {"kind": "h_plan", "plan": runner.state["h_plan"]})
    runner.state["h_plan_audit_id"] = signal_id
    original_transport = runner.broker.transport

    def transport(tool, args):
        if tool == PLACE:
            return {"data": {"order_id": "FILL-TEST"}}
        if tool == HISTORY:
            return {"data": {"orders": [{"order_id": "FILL-TEST", "status": 4, "qty": "3",
                                         "cum_qty": "3", "avg_fill_price": "500.123456789"}]}}
        return original_transport(tool, args)

    runner.broker.transport = transport
    runner.h_open()
    saved, = audit_records(runner, "h_open")
    provenance, = audit_records(runner, "provenance")
    assert saved["signal_audit_id"] == signal_id
    assert saved['account']['sleeve_capital'] == {s: 19500 for s in planner.SLEEVES}
    assert saved['capital']['sleeves']['h_etf']['initial_capital'] == 19500
    assert saved['capital']['sleeves']['h_etf']['limits']['per_etf_budget'] == 1950
    intents, reason = planner.plan_h_open(saved["state"], restore_account(saved["account"]), saved["session"],
                                          saved["plan"], provenance["config"], datetime.fromisoformat(saved["now"]))
    assert [i.to_dict() for i in intents] == saved["intents"] and reason == saved["reason"]
    records = runner.store.outbox("2026-11-20")
    assert [r["event"] for r in records] == ["intent", "acknowledged", "outcome"]
    assert all(r["audit_id"] == saved["audit_id"] for r in records)
    assert records[-1]["filled"] == 3 and records[-1]["avg_price"] == 500.123456789
    assert runner.state["sleeves"]["h_etf"] == {"US.SPY": 3}


def test_risk_audit_replays_mark_and_emergency_exit(status_runner, monkeypatch):
    from copy import deepcopy
    from dual_strategy.execution.moomoo_cli import CASH_INFO, POSITION_LIST
    runner, responses = status_runner
    session = runner.session()
    runner.state.update(initial_nav=100000.0, high_water=100000.0, day=session, day_start_nav=100000.0)
    runner.state["sleeves"]["b3"] = {"US.SPY": 5}
    responses[CASH_INFO]["balance"] = "96000"
    responses[POSITION_LIST]["positions"] = [{"symbol": "US.SPY", "qty": "5"}]
    monkeypatch.setattr(runner.broker, "place", lambda intent: "RISK-TEST")
    monkeypatch.setattr(runner.broker, "wait", lambda *args: {"order_id": "RISK-TEST", "status": "FILLED",
                                                            "filled": 5, "avg_price": 499.75, "qty": 5})
    runner.risk()
    saved, = audit_records(runner, "risk")
    provenance, = audit_records(runner, "provenance")
    state = deepcopy(saved["state_before"])
    account = restore_account(saved["account"])
    reason = planner.mark_risk(state, account, saved["session"], provenance["config"])
    assert reason == saved["reason"] == "daily_loss_lock"
    assert state == saved["state_after"] and saved["state_before"]["locked_day"] is None
    intents = planner.plan_flatten(state, account, saved["session"], reason, provenance["config"],
                                    datetime.fromisoformat(saved["now"]))
    assert [i.to_dict() for i in intents] == saved["intents"] and intents[0].qty == 5
    assert runner.store.outbox(session)[0]["audit_id"] == saved["audit_id"]


@pytest.mark.parametrize("last_close,missing_bar,target", [(100.1, False, 0), (100.4, True, None)])
def test_b3_audit_retains_no_trade_and_incomplete_inputs(status_runner, monkeypatch, last_close, missing_bar, target):
    from types import SimpleNamespace
    import dual_strategy.research.data as research_data
    runner, _ = status_runner
    runner.broker = None
    runner.virtual_nav = 100000.0
    day = date(2026, 11, 20)
    today = bars(day, [100.0] * 5 + [last_close])
    if missing_bar:
        today.pop(2)
    runner.market = SimpleNamespace(
        b3_inputs=lambda *args, **kwargs: (prior_days(), today),
        daily=lambda *args: pd.DataFrame({"close": [100.0]}, index=[date(2026, 11, 19)]),
        quote=lambda symbol: (500.0, datetime.now(timezone.utc)))
    monkeypatch.setattr(research_data, "dividends", lambda symbol: {})
    runner.b3("10:00", day)
    saved, = audit_records(runner, "b3")
    assert saved["decision"]["target"] == target and saved["intents"] == []
    assert len(saved["inputs"]["today"]) == (5 if missing_bar else 6)
    assert runner.store.outbox(day.isoformat()) == []


def test_flatten_audit_replays_only_selected_sleeve(status_runner, monkeypatch):
    from dual_strategy.execution.moomoo_cli import POSITION_LIST
    runner, responses = status_runner
    runner.state["sleeves"] = {"b3": {"US.SPY": 5}, "h_etf": {"US.SPY": 2}}
    responses[POSITION_LIST]["positions"] = [{"symbol": "US.SPY", "qty": "7"}]
    monkeypatch.setattr(runner.broker, "place", lambda intent: "FLAT-TEST")
    monkeypatch.setattr(runner.broker, "wait", lambda *args: {"order_id": "FLAT-TEST", "status": "FILLED",
                                                            "filled": 5, "avg_price": 499.75, "qty": 5})
    runner.flatten("b3", "b3_scheduled_flatten")
    saved, = audit_records(runner, "flatten")
    provenance, = audit_records(runner, "provenance")
    replay = planner.plan_flatten(saved["state"], restore_account(saved["account"]), saved["session"],
                                  saved["reason"], provenance["config"], datetime.fromisoformat(saved["now"]),
                                  saved["sleeves"])
    assert [i.to_dict() for i in replay] == saved["intents"] and replay[0].qty == 5
    assert runner.state["sleeves"] == {"b3": {}, "h_etf": {"US.SPY": 2}}
    assert all(r["audit_id"] == saved["audit_id"] for r in runner.store.outbox(saved["session"]))


def test_missing_audit_write_prevents_order_submission(status_runner, monkeypatch):
    runner, _ = status_runner
    submissions = []
    monkeypatch.setattr(runner.broker, "place", lambda intent: submissions.append(intent) or "TEST")
    monkeypatch.setattr(runner.broker, "wait", lambda *args: {"status": "FILLED", "filled": 1, "avg_price": 100})

    def disk_full(*args):
        raise OSError("audit disk full")

    monkeypatch.setattr(runner.store, "audit", disk_full, raising=False)
    intent = planner.Intent("audit-test", "t", "b3", "US.SPY", "BUY", 1, 100, 100, "r", False)
    with pytest.raises(OSError, match="audit disk full"):
        runner.send([intent], "2026-11-20")
    assert submissions == [] and runner.store.outbox("2026-11-20") == []


def test_config_five_sleeves_at_20pct_each():
    # execution_enabled is the user's decision (true on the competition Mac since 2026-11-20), not asserted here
    assert CFG["allocation"] == {"b3": 0.20, "h_etf": 0.20, "noise": 0.20, "vwap": 0.20, "orb": 0.20}
    assert set(CFG["allocation"]) == set(planner.SLEEVES)
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


def test_spy_cap_is_per_symbol_across_sleeves_and_b3_is_20k():
    assert CFG["allocation"]["b3"] * CFG["b3"]["position_fraction_of_sleeve"] == pytest.approx(0.20)
    st = planner.new_state()
    planner.mark_risk(st, acct(), "2026-11-20", CFG)
    i = planner.plan_b3(st, acct(), "2026-11-20", Decision(1), CFG, datetime.now(NY))[0][0]
    assert i.qty == int(20_000 // i.limit_price)                       # B3 now trades $20,000 of SPY
    st["sleeves"]["b3"]["US.SPY"] = 79
    a = acct(positions={"US.SPY": 79})
    out, _ = planner.plan_h_open(st, a, "2026-11-20", {"exits": {}, "entries": ["US.SPY"]}, CFG, datetime.now(NY))
    assert out == [] or out[0].qty * out[0].limit_price + 79 * 500 <= CFG["risk"]["name_cap_usd"] + 1e-6


def test_order_error_halt_blocks_entries_without_forced_exit_loop():
    st = planner.new_state()
    a = acct(positions={"US.SPY": 10})
    st["sleeves"]["b3"]["US.SPY"] = 10
    planner.mark_risk(st, a, "2026-11-20", CFG)
    st["halted"] = {"kind": "order_error", "reason": "order error: rejected"}
    assert planner.mark_risk(st, a, "2026-11-20", CFG) is None
    assert "drawdown_halt" in planner.entry_block(st, a, "2026-11-20", CFG)
    st["halted"] = {"kind": "drawdown", "reason": "dd"}
    assert planner.mark_risk(st, a, "2026-11-20", CFG) == "drawdown_halt"
    st["halted"] = {"reason": "legacy record without kind"}
    assert planner.mark_risk(st, a, "2026-11-20", CFG) == "drawdown_halt"


def test_flatten_never_sells_more_than_broker_shows():
    st = planner.new_state()
    st["sleeves"]["b3"]["US.SPY"] = 50
    out = planner.plan_flatten(st, acct(positions={"US.SPY": 30}), "2026-11-20", "x", CFG, datetime.now(NY))
    assert [(i.symbol, i.qty) for i in out] == [("US.SPY", 30)]
    assert planner.plan_flatten(st, acct(), "2026-11-20", "x", CFG, datetime.now(NY)) == []


def test_order_error_budget_stops_sending_after_three(status_runner):
    runner, _ = status_runner
    placed = []
    runner.broker.place = lambda intent: placed.append(intent) or (_ for _ in ()).throw(ExecutionBlocked("cli down"))
    intent = planner.Intent("x", "t", "b3", "US.SPY", "SELL", 1, 500.0, 500.0, "r", True)
    for _ in range(3):
        with pytest.raises(ExecutionBlocked):
            runner.send([intent], "2026-11-20")
    with pytest.raises(ExecutionBlocked, match="budget"):
        runner.send([intent], "2026-11-20")
    assert len(placed) == 3 and runner.state["order_errors_today"] == 3
    assert runner.state["halted"]["kind"] == "order_error"
    assert "order_refused" in [r["event"] for r in runner.store.outbox("2026-11-20")]


@pytest.mark.parametrize('recorded', ['h_open', 'h_open_skipped', 'h_open_started', 'runner_error', 'PLAN', 'prior_day', 'none'])
def test_restarted_loop_does_not_replay_h_open_but_keeps_risk_checks(status_runner, monkeypatch, recorded):
    runner, _ = status_runner
    now = datetime(2026, 11, 20, 11, 45, tzinfo=NY)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz) if tz else now.replace(tzinfo=None)

    monkeypatch.setitem(runner.loop.__globals__, 'datetime', Clock)
    runner.state['h_plan'] = {'asof': '2026-11-19', 'entries': [], 'exits': {}, 'blocked': None}
    if recorded == 'runner_error':
        runner.store.append('2026-11-20', {'event': 'runner_error', 'step': 'h-open', 'error': 'timeout'})
    elif recorded != 'none':
        runner.store.audit('2026-11-19' if recorded == 'prior_day' else '2026-11-20', {
            'kind': 'h_open' if recorded in ('PLAN', 'prior_day') else recorded,
            'mode': 'PLAN' if recorded == 'PLAN' else 'EXECUTE'})
    sent, risks = [], []
    monkeypatch.setattr(runner, 'send', lambda intents, session, audit_id: sent.append(intents))

    def risk(symbols=()):
        risks.append(list(symbols))
        return acct()

    monkeypatch.setattr(runner, 'risk', risk)

    class LoopStopped(BaseException):
        pass

    def stop(seconds):
        raise LoopStopped()

    monkeypatch.setattr(runner.loop.__globals__['time'], 'sleep', stop)
    with pytest.raises(LoopStopped):
        runner.loop()
    assert len(sent) == (1 if recorded in ('PLAN', 'prior_day', 'none') else 0)
    assert len(risks) == (2 if recorded in ('PLAN', 'prior_day', 'none') else 1)


def test_h_open_attempt_is_persisted_before_account_read(status_runner, monkeypatch):
    runner, _ = status_runner
    runner.state['h_plan'] = {'asof': '2026-11-19', 'entries': ['US.SPY'], 'exits': {}, 'blocked': None}
    monkeypatch.setattr(runner, 'session', lambda: '2026-11-20')
    calls = []

    def failed_read(symbols):
        calls.append(symbols)
        raise RuntimeError('read interrupted')

    monkeypatch.setattr(runner, 'risk', failed_read)
    with pytest.raises(RuntimeError, match='read interrupted'):
        runner.h_open()
    runner.h_open()
    assert calls == [['US.SPY']]
    assert runner.store.outbox('2026-11-20') == []


def test_loop_step_failure_is_recorded_not_fatal(status_runner):
    runner, _ = status_runner

    def boom():
        raise RuntimeError("quote feed timeout")
    assert runner.guarded("risk", boom) is False
    assert runner.guarded("ok", lambda: None) is True
    errors = [r for r in runner.store.outbox(runner.session()) if r["event"] == "runner_error"]
    assert errors and errors[0]["step"] == "risk" and "timeout" in errors[0]["error"]


def test_broker_retries_reads_but_never_order_placement(tmp_path, monkeypatch):
    import dual_strategy.competition.broker as broker_mod
    monkeypatch.setattr(broker_mod.time, "sleep", lambda s: None)
    b = CompetitionBroker.__new__(CompetitionBroker)
    b.acc_id, b._last = "T", 0.0
    calls = {"funds": 0, "place": 0}

    def flaky(tool, args):
        if tool.endswith("cash_info"):
            calls["funds"] += 1
            if calls["funds"] < 3:
                raise ExecutionBlocked("Moomoo CLI command failed; reconcile before retry")
            return {"data": {"balance": "1"}}
        if tool.endswith("input_order"):
            calls["place"] += 1
            raise ExecutionBlocked("Moomoo CLI command failed; reconcile before retry")
        return {"data": {"positions": []}}
    b.transport = flaky
    assert b.call(broker_mod.CASH_INFO, {})["data"]["balance"] == "1" and calls["funds"] == 3
    with pytest.raises(ExecutionBlocked):
        b.place(planner.Intent("i", "t", "b3", "US.SPY", "BUY", 1, 1.0, 1.0, "r", False))
    assert calls["place"] == 1
