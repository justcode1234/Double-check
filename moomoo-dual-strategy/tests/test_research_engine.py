"""Offline research backtester: fees, accounting, fills, look-ahead, warm-up, DST, splits, intrabar ambiguity."""

from __future__ import annotations

import gzip
import json
import math
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pytest

from dual_strategy.data.calendar import NY
from dual_strategy.research import data as rdata
from dual_strategy.research import strat_a, strat_b, strat_c
from dual_strategy.research.book import DayBook
from dual_strategy.research.data import Intraday, resample
from dual_strategy.research.etf import DECISION_MINUTES, EtfContext, decision_slot
from dual_strategy.research.execution import (BASELINE_EXEC, COMPETITION, NATIVE_EXEC, PAPER_NOISE, PAPER_ORB,
                                              Costs, Execution, chance, next_valid, stop_price)
from dual_strategy.research.features import noise_sigma, session_vwap
from dual_strategy.research.metrics import block_bootstrap, drawdown, sharpe
from dual_strategy.research.portfolio import simulate

DAYS = [date(2021, 3, 1) + timedelta(days=i) for i in range(40)]
DAYS = [d for d in DAYS if d.weekday() < 5][:20]


def _bars(o, h, l, c, v, minutes=1, days=None, symbol="US.SPY") -> Intraday:
    days = days or DAYS[:o.shape[0]]
    turnover = (h + l + c) / 3 * v
    expected = np.full(len(days), 390 // minutes)
    return Intraday(symbol, minutes, days, o, h, l, c, v, turnover, expected)


def _walk(n_days=20, seed=1, drift_day=None, drift=0.0006) -> Intraday:
    rng = np.random.default_rng(seed)
    o = np.empty((n_days, 390)); c = np.empty((n_days, 390))
    px = 400.0
    for t in range(n_days):
        for j in range(390):
            r = rng.normal(0, 0.0004) + (drift if t == drift_day and j >= 15 else 0.0)
            o[t, j] = px
            px *= 1 + r
            c[t, j] = px
    h = np.maximum(o, c) * 1.0001
    l = np.minimum(o, c) * 0.9999
    return _bars(o, h, l, c, np.full((n_days, 390), 50_000.0))


def _ctx(b1: Intraday, atr: float = 4.0) -> EtfContext:
    b5 = resample(b1, 5)
    slots = [decision_slot(m) for m in DECISION_MINUTES]
    n = len(b1.days)
    prev = np.concatenate([[b1.close[0, -1]], b1.close[:-1, -1]])
    return EtfContext("US.SPY", b1.days, b1, b5, b1.complete(), session_vwap(b1, "turnover"), session_vwap(b1, "hlc3"),
                      session_vwap(b5, "hlc3"), session_vwap(b5, "turnover"), noise_sigma(b1, slots, True),
                      noise_sigma(b1, slots, False), np.full(n, 0.01), np.full(n, atr), prev, np.zeros(n),
                      np.full(n, 960))


def _book(cfg, day=DAYS[0], equity=50_000.0, minutes=1):
    return DayBook("T", cfg.name, day, equity, cfg.costs, cfg.exe, minutes)


# ---------------------------------------------------------------- fees, slippage, fills, accounting

def test_competition_fees_platform_plus_sell_side_capped():
    assert COMPETITION.fee(100, sell=False) == pytest.approx(0.99)
    assert COMPETITION.fee(100, sell=True) == pytest.approx(0.99 + 0.0166)
    assert COMPETITION.fee(1_000_000, sell=True) == pytest.approx(0.99 + 8.30)
    assert COMPETITION.fee(0, sell=True) == 0.0
    assert PAPER_ORB.fee(1000, sell=False) == pytest.approx(3.5)
    assert PAPER_NOISE.slipped(100.0, buy=True) == pytest.approx(100.001)


def test_slippage_is_always_adverse():
    c = COMPETITION.with_slippage(10)
    assert c.slipped(100, buy=True) == pytest.approx(100.10)
    assert c.slipped(100, buy=False) == pytest.approx(99.90)


def test_round_trip_accounting_identity():
    cfg = strat_b.BConfig("x", costs=COMPETITION.with_slippage(5))
    book = _book(cfg)
    book.enter("S", 1, 100, 100.0, 1e6, 600, signal_minute=600, signal="s", order_type="market", stop=99.0)
    rec = book.exit("S", [(101.0, 700, 1e6)], "r", 700)
    assert rec["gross_pnl"] == pytest.approx(100.0)
    assert rec["net_pnl"] == pytest.approx(rec["gross_pnl"] - rec["slippage_cost"] - rec["fees"], abs=0.02)
    assert book.realized == pytest.approx(rec["net_pnl"], abs=0.01)
    assert rec["label"] == "SIMULATED"
    short = _book(cfg)
    short.enter("S", -1, 100, 100.0, 1e6, 600, signal_minute=600, signal="s", order_type="market", stop=101.0)
    assert short.fees == pytest.approx(0.99 + 0.0166)  # a short entry is a sale
    rec = short.exit("S", [(99.0, 700, 1e6)], "r", 700)
    assert rec["gross_pnl"] == pytest.approx(100.0) and rec["side"] == "short"


def test_partial_fill_capacity_and_exit_spill():
    cfg = strat_b.BConfig("x", exe=Execution(participation=20))
    book = _book(cfg)
    leg = book.enter("S", 1, 1000, 100.0, 10, 600, signal_minute=600, signal="s", order_type="market", stop=None)
    assert leg.qty == 200 and leg.requested == 1000 and book.events["partial"] == 1
    rec = book.exit("S", [(101.0, 601, 5), (102.0, 602, 100)], "r", 600)
    assert rec["qty"] == 200
    assert rec["exit_raw"] == pytest.approx((100 * 101 + 100 * 102) / 200)
    assert book.enter("Z", 1, 10, 100.0, 0, 600, signal_minute=600, signal="s", order_type="market", stop=None) is None
    assert book.events["no_capacity"] == 1


def test_duplicate_entry_rejected_and_rejects_deterministic():
    cfg = strat_b.BConfig("x")
    book = _book(cfg)
    assert book.enter("S", 1, 10, 100.0, 1e6, 600, signal_minute=600, signal="s", order_type="m", stop=None)
    assert book.enter("S", 1, 10, 100.0, 1e6, 601, signal_minute=601, signal="s", order_type="m", stop=None) is None
    draws = [chance(0.3, 1, "k", i) for i in range(2000)]
    assert draws == [chance(0.3, 1, "k", i) for i in range(2000)]
    assert 0.25 < np.mean(draws) < 0.35
    all_reject = strat_b.BConfig("r", exe=Execution(reject_rate=1.0))
    book = _book(all_reject)
    assert book.enter("S", 1, 10, 100.0, 1e6, 600, signal_minute=600, signal="s", order_type="m", stop=None) is None


def test_stop_price_modes():
    assert stop_price(100.0, 99.0, 99.5, True, NATIVE_EXEC) == 99.5
    assert stop_price(99.0, 99.2, 99.5, True, NATIVE_EXEC) == 99.0  # gap through the stop
    assert stop_price(100.0, 99.0, 99.5, True, BASELINE_EXEC) == pytest.approx(99.5 * (1 - 5e-4))
    stressed = Execution(stop_mode="stressed", stop_latency_bps=0)
    assert stop_price(100.0, 99.0, 99.5, True, stressed) == 99.0
    assert stop_price(100.0, 101.0, 100.5, False, stressed) == 101.0


def test_stale_bars_skipped_never_interpolated():
    row = np.array([1.0, np.nan, np.nan, 4.0])
    assert next_valid(row, 1, 4) == 3 and next_valid(row, 1, 3) is None
    b = _walk(2)
    b.close[0, 7] = np.nan
    r = resample(b, 5)
    assert np.isnan(r.close[0, 1]) and not np.isnan(r.close[0, 0]) and not np.isnan(r.close[1, 1])
    assert not b.complete()[0] and b.complete()[1]


# ---------------------------------------------------------------- look-ahead / warm-up

def test_noise_sigma_uses_only_prior_sessions_and_needs_14_day_warmup():
    b = _walk(20)
    j = decision_slot(600)
    base = noise_sigma(b, [j], True)[j]
    assert np.isnan(base[:14]).all() and np.isfinite(base[14:]).all()
    move = np.abs(b.close[:, j] / b.open[:, 0] - 1)
    assert base[14] == pytest.approx(move[:14].mean())
    b.close[15, :] *= 1.05  # today's own bars must not enter today's sigma
    assert noise_sigma(b, [j], True)[j][15] == pytest.approx(base[15])
    b.close[14, j] = np.nan  # strict: a missing prior slot blocks; skip mode falls back to older sessions
    assert np.isnan(noise_sigma(b, [j], True)[j][15]) and np.isfinite(noise_sigma(b, [j], False)[j][15])


def test_vwap_is_cumulative_through_the_bar_only():
    b = _walk(2)
    v = session_vwap(b, "hlc3")
    b.high[0, 100:] *= 1.1
    assert np.allclose(session_vwap(b, "hlc3")[0, :100], v[0, :100])


def test_b_no_trades_during_warmup_and_fills_after_decision():
    ctx = _ctx(_walk(20, drift_day=16))
    cfg = strat_b.BConfig("b", shorts=True)
    res = simulate("B", cfg, strat_b.run_day, ctx, ctx.days, {d: i for i, d in enumerate(ctx.days)})
    assert all(date.fromisoformat(t["date"]) >= ctx.days[14] for t in res.trades)
    day16 = [t for t in res.trades if t["date"] == ctx.days[16].isoformat()]
    assert day16 and day16[0]["side"] == "long"
    for t in res.trades:
        assert t["entry_time"] >= t["signal_time"] >= "10:00"
        assert t["exit_time"] <= "15:55"
    # equity is carried day to day exactly by realized net P&L
    assert res.daily["equity_eod"].iloc[-1] == pytest.approx(50_000 + sum(t["net_pnl"] for t in res.trades), abs=0.5)


def test_b_trades_unchanged_when_future_bars_change():
    b = _walk(20, drift_day=16)
    ctx = _ctx(b)
    cfg = strat_b.BConfig("b")
    first = strat_b.run_day(ctx, 16, 50_000, cfg, (bk := _book(cfg, ctx.days[16]))) and bk.trades
    cut = decision_slot(660)  # bars after 11:00 are rewritten
    b2 = _walk(20, drift_day=16)
    for a in (b2.open, b2.high, b2.low, b2.close):
        a[16, cut + 2:] *= 0.97
    ctx2 = _ctx(b2)
    bk2 = _book(cfg, ctx.days[16])
    strat_b.run_day(ctx2, 16, 50_000, cfg, bk2)
    assert first and first[0]["entry_time"] < "11:00"
    assert bk2.trades[0]["entry_fill"] == first[0]["entry_fill"]
    assert bk2.trades[0]["signal"] == first[0]["signal"]


def test_b_missing_decision_bar_is_a_missed_decision():
    b = _walk(20, drift_day=16)
    for m in DECISION_MINUTES:
        b.close[16, decision_slot(m)] = np.nan
    ctx = _ctx(b)
    cfg = strat_b.BConfig("b")
    book = _book(cfg, ctx.days[16])
    strat_b.run_day(ctx, 16, 50_000, cfg, book)
    assert book.trades == []


def test_b_long_only_never_shorts_and_sizing_cap():
    ctx = _ctx(_walk(20, seed=3))
    cfg = strat_b.BConfig("b", shorts=False)
    res = simulate("B", cfg, strat_b.run_day, ctx, ctx.days, {d: i for i, d in enumerate(ctx.days)})
    assert all(t["side"] == "long" for t in res.trades)
    assert all(t["notional"] <= 0.40 * t["equity_sod"] * 1.001 for t in res.trades)


# ---------------------------------------------------------------- strategy C

def _c_day(stop_and_target_same_bar: bool) -> EtfContext:
    n = 390
    c = np.full((1, n), 100.0)
    c[0, 30:35] = 99.5            # 5m bar ending 10:05: d ~ -0.5 (arms)
    c[0, 35:40] = 99.95           # bar ending 10:10 back inside: trigger; entry at the 10:10 open
    c[0, 40] = 99.90              # entry bar closes below VWAP: no target yet
    if stop_and_target_same_bar:
        c[0, 41] = 100.2          # next bar closes above VWAP ...
    o = np.concatenate([[100.0], c[0, :-1]])[None, :]
    h, l = np.maximum(o, c) + 0.01, np.minimum(o, c) - 0.01
    if stop_and_target_same_bar:
        l[0, 41] = 99.0           # ... but also traded through the stop
    b = Intraday("US.SPY", 1, [DAYS[0]], o, h, l, c, np.full((1, n), 1e5), (h + l + c) / 3 * 1e5, np.array([390]))
    return _ctx(b, atr=1.0)


def test_c_stop_assumed_before_target_on_same_bar():
    ctx = _c_day(True)
    cfg = strat_c.CConfig("c", exe=NATIVE_EXEC)
    book = _book(cfg, ctx.days[0])
    strat_c.run_day(ctx, 0, 50_000, cfg, book)
    assert len(book.trades) == 1
    t = book.trades[0]
    assert t["side"] == "long" and t["entry_time"] == "10:10" and t["signal_time"] == "10:10"
    assert "armed_bar_end=10:05" in t["signal"]
    assert t["exit_reason"] == "stop" and t["exit_raw"] == pytest.approx(99.95 - 0.25)


def test_c_target_and_sizing():
    ctx = _c_day(False)
    cfg = strat_c.CConfig("c")
    book = _book(cfg, ctx.days[0])
    strat_c.run_day(ctx, 0, 50_000, cfg, book)
    t = book.trades[0]
    assert t["exit_reason"] in ("vwap_target", "max_hold")
    # min(0.25% risk / 0.25 ATR = 500 sh, 40% notional = 200 sh)
    assert t["qty"] == math.floor(0.40 * 50_000 / t["entry_fill"]) or t["qty"] == 200


def test_c_no_entry_after_1430():
    ctx = _c_day(False)
    for a in (ctx.b1.open, ctx.b1.high, ctx.b1.low, ctx.b1.close):
        a[0, :] = np.roll(a[0, :], 290)  # move the setup to ~14:50
    ctx = _ctx(ctx.b1, atr=1.0)
    cfg = strat_c.CConfig("c")
    book = _book(cfg, ctx.days[0])
    strat_c.run_day(ctx, 0, 50_000, cfg, book)
    assert all(t["entry_time"] <= "14:31" for t in book.trades)


# ---------------------------------------------------------------- strategy A

def _a_universe(stop_same_bar: bool) -> dict:
    n = 78
    o = np.full((1, n), 50.0); c = np.full((1, n), 50.0)
    h = np.full((1, n), 50.1); l = np.full((1, n), 49.9)
    o[0, 0], c[0, 0], h[0, 0], l[0, 0] = 50.0, 50.5, 50.6, 49.9    # green opening candle, ORH 50.6
    o[0, 1], h[0, 1], c[0, 1], l[0, 1] = 50.5, 51.0, 50.9, 50.45   # breakout bar
    if stop_same_bar:
        l[0, 1] = 50.3                                             # 50.6 - 0.1*ATR(2.0) = 50.4 -> stop touched
    o[0, 2:], c[0, 2:], h[0, 2:], l[0, 2:] = 51.0, 51.2, 51.3, 50.9
    b5 = Intraday("US.X", 5, [DAYS[0]], o, h, l, c, np.full((1, n), 1e5), np.full((1, n), 5e6), np.array([78]))
    return {"US.X": strat_a.StockData("US.X", b5, np.array([True]), np.array([2.0]), np.array([5e6]),
                                      np.array([3.0]), "Tech")}


@pytest.mark.parametrize("mode,expected", [("pessimistic", "stop_same_bar"), ("optimistic", "session_close")])
def test_a_resting_stop_same_bar_ambiguity(mode, expected):
    uni = _a_universe(True)
    cfg = strat_a.AConfig("a", entry="resting_stop", same_bar_stop=mode, flatten_before_close=0, exe=NATIVE_EXEC,
                          loss_lock=None)
    book = _book(cfg, minutes=5)
    strat_a.run_day(uni, 0, 50_000, cfg, book)
    assert len(book.trades) == 1
    t = book.trades[0]
    assert t["entry_raw"] == pytest.approx(50.6) and t["exit_reason"] == expected


def test_a_bar_close_market_enters_next_open_and_paper_sizing():
    uni = _a_universe(False)
    cfg = strat_a.AConfig("a", entry="bar_close_market")
    book = _book(cfg, minutes=5)
    strat_a.run_day(uni, 0, 50_000, cfg, book)
    t = book.trades[0]
    assert t["signal_time"] == "09:40" and t["entry_time"] == "09:40" and t["entry_raw"] == 51.0
    assert t["exit_reason"].startswith("flatten")
    assert t["notional"] <= 0.15 * 50_000 * 1.001
    paper = strat_a.AConfig("p", entry="resting_stop", sizing="paper", top_n=20, costs=PAPER_ORB, exe=NATIVE_EXEC)
    book = _book(paper, equity=25_000, minutes=5)
    strat_a.run_day(_a_universe(False), 0, 25_000, paper, book)
    t = book.trades[0]
    assert t["qty"] == math.floor(min(0.01 * 25_000 / 0.2, 4 * 25_000 / (20 * 50.6)))


def test_a_filters_and_doji():
    uni = _a_universe(False)
    s = uni["US.X"]
    assert strat_a.candidates(uni, 0, strat_a.AConfig("a"))
    assert not strat_a.candidates(uni, 0, strat_a.AConfig("a", rvol_min=5.0))
    s.b5.close[0, 0] = s.b5.open[0, 0]
    assert not strat_a.candidates(uni, 0, strat_a.AConfig("a"))
    s.b5.close[0, 0] = 49.0
    assert not strat_a.candidates(uni, 0, strat_a.AConfig("a", shorts=False))
    assert strat_a.candidates(uni, 0, strat_a.AConfig("a"))[0]["side"] == -1


# ---------------------------------------------------------------- loader: DST, duplicates, out-of-RTH, splits

def _write_k(tmp, symbol, rows):
    path = tmp / "k1" / symbol / "2021.csv.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt") as fh:
        fh.write("end_ms,open,high,low,close,volume,turnover\n")
        for r in rows:
            fh.write(",".join(map(str, r)) + "\n")


def _ms(d: date, hh: int, mm: int) -> int:
    return int(datetime(d.year, d.month, d.day, hh, mm, tzinfo=NY).astimezone(timezone.utc).timestamp() * 1000)


def test_loader_dst_duplicates_out_of_rth(tmp_path, monkeypatch):
    monkeypatch.setattr(rdata, "HISTORY", tmp_path)
    before, after = date(2021, 3, 12), date(2021, 3, 15)  # DST began 2021-03-14
    rows = [(_ms(before, 9, 31), 1, 1, 1, 1, 10, 10), (_ms(after, 9, 31), 2, 2, 2, 2, 10, 20),
            (_ms(after, 9, 31), 3, 3, 3, 3, 10, 30),          # duplicate: keep the last
            (_ms(after, 9, 30), 9, 9, 9, 9, 10, 90),          # pre-open bar end: out of RTH
            (_ms(after, 16, 1), 9, 9, 9, 9, 10, 90)]          # after close
    _write_k(tmp_path, "US.T", rows)
    b = rdata.load_intraday("US.T", 1)
    i0, i1 = b.index[before], b.index[after]
    assert b.close[i0, 0] == 1 and b.close[i1, 0] == 3
    assert b.duplicates == 1 and b.out_of_rth == 2
    assert np.isnan(b.close[i1, 1:]).all()
    half = date(2021, 11, 26)
    assert rdata.calendar_frame().loc[half, "minutes"] == 210 and b.expected[b.index[half]] == 210


def test_split_back_factor_and_split_adjusted_atr(tmp_path, monkeypatch):
    from dual_strategy.research import features
    monkeypatch.setattr(rdata, "HISTORY", tmp_path)
    ex = date(2021, 3, 15)
    (tmp_path / "actions").mkdir()
    (tmp_path / "actions" / "US.T.json").write_text(json.dumps({
        "splits": {"split_list": [{"ex_date": "2021/03/15", "rate": "1→4"}, {"ex_date": "2021/01/04", "type": "CONSOLIDATION"}]},
        "dividends": {"dividend_list": []}}))
    days = [d for d in rdata.sessions() if date(2021, 1, 4) <= d <= date(2021, 4, 30)]
    back = rdata.split_back_factor("US.T", days)
    assert back[days.index(date(2021, 3, 12))] == 4 and back[days.index(ex)] == 1
    (tmp_path / "daily_a0").mkdir()
    with gzip.open(tmp_path / "daily_a0" / "US.T.csv.gz", "wt") as fh:
        fh.write("date,open,high,low,close,volume,turnover\n")
        for d in days:
            k = 4 if d < ex else 1
            px = 100 * k
            fh.write(f"{d:%Y%m%d},{px},{px + 0.5 * k},{px - 0.5 * k},{px},{1e6 / k},0\n")
    f = features.stock_daily_features("US.T")
    day_after = days[days.index(ex) + 1]
    assert f.loc[ex, "atr14"] == pytest.approx(1.0)        # all-prior-session ATR in post-split units
    assert f.loc[day_after, "atr14"] == pytest.approx(1.0)
    assert f.loc[date(2021, 3, 12), "atr14"] == pytest.approx(4.0)
    assert f.loc[ex, "prev_close"] == pytest.approx(400)   # raw previous close is NOT adjusted (gap logic)


# ---------------------------------------------------------------- metrics

def test_metrics_guards():
    assert sharpe(np.full(30, 0.001)) is None
    assert block_bootstrap(np.zeros(10))["sharpe_ci"] is None
    a = block_bootstrap(np.random.default_rng(0).normal(0.001, 0.01, 300))
    assert a == block_bootstrap(np.random.default_rng(0).normal(0.001, 0.01, 300))
    dd_abs, dd_pct = drawdown(np.array([51_000, 45_900, 52_000]), 50_000)
    assert dd_abs == pytest.approx(-5_100) and dd_pct == pytest.approx(-0.1)


# ---------------------------------------------------------------- cross-check with the repo baseline on real data

def test_noise_area_matches_repo_baseline_on_cached_spy():
    from dual_strategy.models import Bar
    from dual_strategy.research.etf import etf_context
    from dual_strategy.strategies.noise_area import compute_noise_area
    if not (rdata.HISTORY / "k1" / "US.SPY" / "2021.csv.gz").exists():
        pytest.skip("SPY 1m history cache not present")
    ctx = etf_context("US.SPY")
    b = ctx.b1

    def bars_of(t, through=None):
        out = []
        for j in range(int(b.expected[t]) if through is None else through + 1):
            end = datetime.combine(ctx.days[t], datetime.min.time(), NY) + timedelta(minutes=571 + j)
            out.append(Bar("SPY", end, b.open[t, j], b.high[t, j], b.low[t, j], b.close[t, j], b.volume[t, j],
                           b.turnover[t, j]))
        return out

    checked = 0
    for day in (date(2021, 3, 15), date(2021, 6, 18), date(2021, 9, 17)):
        t = ctx.days.index(day)
        if not all(ctx.complete1[t - 14:t + 1]):
            continue
        history = {ctx.days[s]: bars_of(s) for s in range(t - 14, t)}
        for m in (600, 780, 930):
            j = decision_slot(m)
            at = datetime.combine(day, datetime.min.time(), NY) + timedelta(minutes=m)
            ref = compute_noise_area(bars_of(t, j), history, at=at, cash_dividend=float(ctx.dividend[t]))
            assert ctx.sigma_strict[j][t] == pytest.approx(ref.sigma, rel=1e-9)
            assert ctx.vwap_turnover[t, j] == pytest.approx(ref.vwap, rel=1e-9)
            prev = ctx.prev_close[t] - ctx.dividend[t]
            assert prev == pytest.approx(ref.previous_close, rel=1e-9)
            assert max(b.open[t, 0], prev) * (1 + ctx.sigma_strict[j][t]) == pytest.approx(ref.upper, rel=1e-9)
            checked += 1
    assert checked >= 3


def test_c_freeze_refuses_tampered_file(tmp_path, monkeypatch):
    from dual_strategy.research import run
    path = tmp_path / "c_frozen.json"
    monkeypatch.setattr(run, "FREEZE_PATH", path)
    monkeypatch.setattr(run, "select_c", lambda engine, days: ((0.3, 0.25, 60), [{"band": 0.3, "stop_atr": 0.25,
                                                                                  "hold": 60, "trades": 150, "sharpe": -1.0}]))
    rec = run.freeze_c(None)
    assert rec["selected"] == {"band": 0.3, "stop_atr": 0.25, "hold": 60} and rec["development_pass"] is False
    assert run.freeze_c(None)["sha256"] == rec["sha256"]
    body = json.loads(path.read_text())
    body["selected"]["band"] = 0.2
    path.write_text(json.dumps(body))
    with pytest.raises(SystemExit):
        run.freeze_c(None)


def test_gates_and_verdict():
    from dual_strategy.research import report
    good = {"sessions": 700, "trades": 300, "sharpe": 1.2, "p_bootstrap_sharpe_le_0": 0.01, "data_gap_sessions": 0,
            "excess_vs_matched_pct": 3.0, "max_drawdown_pct": -2.0, "net_return_pct": 4.0, "sharpe_ci95": [0.2, 2.0]}
    rows = [{"variant": v, "segment": s, **good} for v in ("B1", "B1-slip10", "B1-combined-slip10") for s in ("val", "oos")]
    wf = {"B1": {"windows": 6, "positive_share": 0.67, "stitched_return_pct": 5.0}}
    g = report.gates("B", "B1", rows, wf, {})
    assert {s for _, s, _ in g} == {"PASS"} and report.verdict(g).startswith("GO")
    rows[1]["p_bootstrap_sharpe_le_0"] = 0.2
    assert report.verdict(report.gates("B", "B1", rows, wf, {})) == "NO-GO"
    ga = report.gates("A", "A1", [{**r, "variant": r["variant"].replace("B", "A")} for r in rows[:0]] +
                      [{"variant": "A1", "segment": s, **good} for s in ("val", "oos")], {}, {})
    assert ("data", "EXPLORATORY") in [(a, b) for a, b, _ in ga]
    assert report.verdict([("data", "EXPLORATORY", ""), ("x", "PASS", "")]).startswith("NO-GO (exploratory")
