from decimal import Decimal
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from audit_core import OUT, block_paths, european_price, intrinsic, invert_iv, option_fee, prob_above
from option_replay import Spec, execute


@pytest.mark.parametrize("activity_both,expected", [(False, "4.61279"), (True, "4.61558")])
def test_penny_option_one_lot_fee(activity_both, expected):
    assert option_fee(1, ".09", False, activity_both) + option_fee(1, ".10", True, activity_both) == Decimal(expected)


@pytest.mark.parametrize("activity_both", [False, True])
def test_positive_target_threshold_and_crossed_premium_tier(activity_both):
    win = lambda n, a, b: n - option_fee(n, a, False, activity_both) - option_fee(n, b, True, activity_both)
    assert win(10, ".09", ".10") < 0 < win(11, ".09", ".10")
    assert all(win(n, ".10", ".11") < 0 for n in range(1, 1001))


@pytest.mark.parametrize("sign", [1, -1])
def test_european_iv_round_trip(sign):
    p = european_price(600, 605, 10 / 365, .04, .013, .183, sign)
    assert invert_iv(p, 600, 605, 10 / 365, .04, .013, sign) == pytest.approx(.183)


def test_put_probability_and_vertical_bounds():
    assert prob_above(1.2, 1.7, 1, -1) == pytest.approx(.5)
    assert prob_above(1.7, 1.2, 1, 1) == pytest.approx(.5)
    assert np.isnan(prob_above(1, 2.2, 1, -1))
    put = [(100., "P", 1), (95., "P", -1)]
    assert [intrinsic(s, put) for s in (90, 98, 110)] == [5., 2., 0.]


def test_credit_max_loss_includes_entry_fees_and_adverse_ticks():
    legs = ((105, "C", -1), (95, "P", -1), (110, "C", 1), (90, "P", 1))
    prices = {105: 1.3, 95: 1.2, 110: .5, 90: .4}
    chain = SimpleNamespace(get=lambda d, e, k, r: (prices[k], prices[k]+.02, 3, 3, 1000, .20))
    order = {"expiry": "2025-02-10", "legs": legs, "width": 5, "contracts": 1, "budget": 360}
    spec = Spec("x", "credit", "har", 1.2)
    fill = execute(chain, "2025-02-04", spec, order)
    stress = execute(chain, "2025-02-04", Spec("x", "credit", "har", 1.2, ticks=2), order)
    assert fill["max_loss"] == pytest.approx(500 - fill["entry_cash"])
    assert fill["entry_fee"] > 0
    assert stress["max_loss"] - fill["max_loss"] == pytest.approx(8.)
    assert execute(chain, "2025-02-04", Spec("x", "credit", "har", 1.2, ticks=2, strict=True), order) is None


def test_strict_market_order_requires_executable_size():
    quote = SimpleNamespace(get=lambda *args: (.9, 1., 0, 0, 1000, .20))
    order = {"expiry": "2025-02-10", "legs": ((100, "C", 1),), "width": 0, "contracts": 1, "budget": 200}
    assert execute(quote, "2025-02-04", Spec("x", "long", "har", 0, strict=True), order) is None


def test_block_bootstrap_constant_path():
    assert np.allclose(block_paths(np.full(8, -.001), 8, paths=20), .999**8 - 1)


def test_audit_trade_reconciliation_and_frozen_inputs():
    stock = pd.read_csv(OUT / "stock_BC_trade_comparison.csv", low_memory=False)
    assert len(stock) == 4297
    assert stock["_merge"].eq("both").all()
    assert stock.net_delta.abs().max() <= .005001
    option = pd.read_csv(OUT / "option_trade_comparison.csv", low_memory=False)
    frozen = option[option.net_pnl_original.notna()]
    assert len(frozen) == 1171
    assert frozen["_merge"].eq("both").all()
    assert frozen.net_pnl_delta.abs().max() <= .005001
