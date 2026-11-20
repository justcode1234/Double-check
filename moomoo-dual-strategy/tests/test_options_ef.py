"""Required option-engine checks (a)-(h) on SYNTHETIC chains. Unit tests only; not historical performance."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from dual_strategy.research.metrics import block_bootstrap
from dual_strategy.research.options import eod_backtest as eb
from dual_strategy.research.options import ef_study as ef
from dual_strategy.research.options.pricing import option_order_fee, round_trip_fee_per_contract

D = [date(2025, 3, 3), date(2025, 3, 4), date(2025, 3, 5), date(2025, 3, 6), date(2025, 3, 7)]
EXP = date(2025, 3, 7)
COLS = ["date", "expiration", "strike", "right", "bid", "ask", "bid_size", "ask_size", "open_interest", "implied_volatility"]


def chain(rows):
    return eb.Chain(pd.DataFrame(rows, columns=COLS))


def spot(v=500.0):
    return pd.Series({d: v for d in D})


# (a) fee minimums and tiers
def test_a_fee_minimums_one_contract_round_trip_exceeds_one_dollar():
    assert round_trip_fee_per_contract(1, 2.0) == pytest.approx(4.61279)
    assert round_trip_fee_per_contract(1, 2.0) * 1 > 1.0                       # +$0.01 earns only $1.00 gross
    assert option_order_fee(3, 2.0, False) == pytest.approx(1.99 + 0.9 + 0.045)  # min commission still binds at 3
    assert option_order_fee(4, 2.0, False) == pytest.approx(2.60 + 1.2 + 0.06)
    assert 11 * 1.0 - (option_order_fee(11, 0.05, False) + option_order_fee(11, 0.06, True)) > 0   # only <= $0.10, >= 11 lots
    assert 10 * 1.0 - (option_order_fee(10, 0.05, False) + option_order_fee(10, 0.06, True)) < 0


# (b) + (c) every leg at its own bid/ask, x100 multiplier, one order fee per leg
def test_b_c_four_leg_condor_fills_each_leg_at_bid_or_ask():
    legs = [(510.0, "C", -1), (490.0, "P", -1), (520.0, "C", 1), (480.0, "P", 1)]
    q = {510.0: (2.00, 2.04), 490.0: (1.90, 1.95), 520.0: (0.50, 0.53), 480.0: (0.45, 0.48)}
    rows = []
    for d in D[:2]:
        for k, r, _ in legs:
            rows.append((d, EXP, k, r, q[k][0], q[k][1], 100, 100, 1000, .2))
    book = eb.Book(chain(rows), spot())
    p = book.enter(D[1], "F_credit", EXP, legs, 2, {})
    credit = (2.00 + 1.90 - 0.53 - 0.48) * 100 * 2
    fees = sum(option_order_fee(2, px, sell=s < 0) for px, s in ((2.00, -1), (1.90, -1), (0.53, 1), (0.48, 1)))
    assert p.entry_cash == pytest.approx(credit - fees)
    assert book.stats["legs_filled"] == 4


# (d) never enter on or after the expiry date
def test_d_never_enters_on_expiry_day():
    rows = [(d, D[1], 500.0, "C", 1.0, 1.1, 100, 100, 1000, .2) for d in D[:3]]
    b, eq = eb.run_book(D[:3], chain(rows), spot(), lambda d, book: [{"kind": "E_t", "expiry": D[1], "legs": [(500.0, "C", 1)],
                                                                    "n": 1, "meta": {}}] if d == D[0] else [],
                        lambda d, book, p: False)
    assert b.trades == []                                       # decided D0, would fill D1 == expiry -> rejected


# (e) zero look-ahead: decision-time fields for day d do not depend on anything after d
def test_e_forecast_inputs_ignore_future_data():
    rng = np.random.default_rng(0)
    from dual_strategy.research.data import sessions
    days = [d for d in sessions() if d >= date(2023, 1, 3)][:520]
    lr = pd.Series(rng.normal(0, 0.01, len(days)), index=days)
    px = pd.Series(500 * np.exp(lr.cumsum()), index=days)
    d0, exp = days[510], days[517]
    rows = [(d0, exp, float(k), r, 2.0 + (500 - k) * (0.5 if r == "C" else -0.5) * 0.1, 2.05 + (500 - k) * (0.5 if r == "C" else -0.5) * 0.1,
             100, 100, 1000, .2) for k in range(490, 511) for r in ("C", "P")]
    s1 = px.copy(); s1[d0] = 500.0
    c1, _ = ef.e_eval(chain(rows), s1, lr, [d0])
    lr2, s2 = lr.copy(), s1.copy()
    lr2[lr2.index > d0] = 0.05                                  # rewrite the future
    s2[s2.index > d0] = 900.0
    c2, _ = ef.e_eval(chain(rows), s2, lr2, [d0])
    keys = ("edge_boot", "p_loss_boot", "edge_hv", "p_loss_hv", "debit", "k_long", "k_short")
    assert [tuple(c[k] for k in keys) for c in c1[d0]] == [tuple(c[k] for k in keys) for c in c2[d0]]


# (f) expiry: ITM legs closed at quotes (or intrinsic if unquoted), OTM legs expire without an order or fee
def test_f_expiry_exercise_handling():
    legs = [(495.0, "C", 1), (505.0, "C", -1)]
    rows = [(D[0], EXP, 495.0, "C", 6.0, 6.1, 100, 100, 1000, .2), (D[0], EXP, 505.0, "C", 1.0, 1.1, 100, 100, 1000, .2)]
    book = eb.Book(chain(rows), pd.Series({D[0]: 500.0, EXP: 500.0}))
    p = book.enter(D[0], "E_vertical", EXP, legs, 1, {})
    book.close(EXP, p, "expiry")                                # no EXP quotes: long 495C ITM -> intrinsic 5.00; 505C OTM -> no fee
    t = book.trades[0]
    assert t["exit_cash"] == pytest.approx(5.00 * 100 - option_order_fee(1, 5.0, True), abs=0.01)
    assert book.stats["intrinsic_exit_legs"] == 1


# (g) max loss: realised loss never exceeds defined max loss + exit fees
@pytest.mark.parametrize("S_T", [400.0, 480.0, 489.0, 495.0, 500.0, 505.0, 511.0, 520.0, 600.0])
def test_g_condor_and_vertical_loss_bounded(S_T):
    condor = [(510.0, "C", -1), (490.0, "P", -1), (520.0, "C", 1), (480.0, "P", 1)]
    q = {510.0: (2.00, 2.04), 490.0: (1.90, 1.95), 520.0: (0.50, 0.53), 480.0: (0.45, 0.48)}
    rows = [(D[0], EXP, k, r, q[k][0], q[k][1], 100, 100, 1000, .2) for k, r, _ in condor]
    book = eb.Book(chain(rows), pd.Series({D[0]: 500.0, EXP: S_T}))
    credit = 2.00 + 1.90 - 0.53 - 0.48
    p = book.enter(D[0], "F_credit", EXP, condor, 1, {"max_loss_per_contract_fn": lambda qs: (10 - credit) * 100})
    book.close(EXP, p, "expiry")
    exit_fees = 4 * option_order_fee(1, 10.0, True)
    assert book.trades[0]["net_pnl"] >= -(p.max_loss + exit_fees) - 1e-6
    entry_fees = sum(option_order_fee(1, px, sell=s < 0) for px, s in ((2.00, -1), (1.90, -1), (0.53, 1), (0.48, 1)))
    assert p.max_loss == pytest.approx((10 - credit) * 100 + entry_fees)


# (h) resampling confidence intervals: deterministic, contain the point estimate, widen when n shrinks
def test_h_block_bootstrap_ci_properties():
    rng = np.random.default_rng(3)
    r = rng.normal(0.0005, 0.01, 1000)
    a, b = block_bootstrap(r), block_bootstrap(r)
    assert a == b
    sh = r.mean() / r.std(ddof=1) * np.sqrt(252)
    assert a["sharpe_ci"][0] < sh < a["sharpe_ci"][1]
    small = block_bootstrap(r[:120])
    assert (small["sharpe_ci"][1] - small["sharpe_ci"][0]) > (a["sharpe_ci"][1] - a["sharpe_ci"][0])
    mc = ef.two_level_mc(r[:300], outer=20, inner=500)
    assert mc["p_profit_ci90"][0] <= mc["p_profit"] + 0.05 and mc["p_profit_ci90"][1] >= mc["p_profit"] - 0.05
