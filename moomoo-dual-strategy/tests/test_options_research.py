"""Option pricing / fees / Monte Carlo / D-E-F rule tests. Everything here is SYNTHETIC unit-test data; nothing is
historical option performance. Independent references: TradingCalc MCP v2.17.0 (signed results, 2026-11-19)."""

import math

import numpy as np
import pytest

from dual_strategy.research.options import chain, montecarlo, pricing, strat_d, strat_e, strat_f


# ---------------------------------------------------------------- pricing vs independent reference (TradingCalc)
def test_bs_matches_tradingcalc_reference():
    S, K, T, r, q, v = 730, 730, 10 / 365, 0.04, 0.012, 0.20
    assert pricing.bs_price(S, K, T, r, q, v, True) == pytest.approx(9.915958441259818, abs=1e-9)
    assert pricing.bs_price(S, K, T, r, q, v, False) == pytest.approx(9.356357189606342, abs=1e-9)
    g = pricing.bs_greeks(S, K, T, r, q, v, True)
    assert g.delta == pytest.approx(0.5156742831813765, abs=1e-9)
    assert g.gamma == pytest.approx(0.016489906165120232, abs=1e-9)
    assert g.vega / 100 == pytest.approx(0.4815052600215107, abs=1e-9)
    assert g.theta / 365 == pytest.approx(-0.5092963395025435, abs=1e-9)


def test_american_put_matches_tradingcalc_reference():
    args = (730, 760, 30 / 365, 0.04, 0.012, 0.25, False)
    assert pricing.crr_price(*args, american=True, steps=500) == pytest.approx(38.55271117187311, abs=1e-6)
    assert pricing.bs_price(*args) == pytest.approx(38.27709538634576, abs=1e-9)


def test_implied_vol_matches_tradingcalc_reference():
    iv = pricing.implied_vol(7.5, 730, 740, 14 / 365, 0.04, 0.0, True)
    assert iv == pytest.approx(0.19847086209046186, abs=1e-6)


# ---------------------------------------------------------------- pricing identities
@pytest.mark.parametrize("S,K,T,r,q,v", [(100, 90, 0.5, 0.03, 0.01, 0.3), (500, 520, 0.05, 0.05, 0.013, 0.15),
                                         (50, 50, 1.0, 0.0, 0.0, 0.6)])
def test_put_call_parity_and_bounds(S, K, T, r, q, v):
    c, p = pricing.bs_price(S, K, T, r, q, v, True), pricing.bs_price(S, K, T, r, q, v, False)
    assert c - p == pytest.approx(S * math.exp(-q * T) - K * math.exp(-r * T), abs=1e-9)
    assert max(S * math.exp(-q * T) - K * math.exp(-r * T), 0) - 1e-12 <= c <= S * math.exp(-q * T)


def test_greeks_match_finite_differences():
    S, K, T, r, q, v = 100, 105, 0.25, 0.04, 0.01, 0.25
    g = pricing.bs_greeks(S, K, T, r, q, v, False)
    h = 1e-3
    f = lambda s=S, vol=v: pricing.bs_price(s, K, T, r, q, vol, False)
    assert g.delta == pytest.approx((f(S + h) - f(S - h)) / (2 * h), rel=1e-5)
    assert g.gamma == pytest.approx((f(S + h) - 2 * f(S) + f(S - h)) / h ** 2, rel=1e-3)
    assert g.vega == pytest.approx((f(vol=v + h) - f(vol=v - h)) / (2 * h), rel=1e-5)
    assert -1 <= g.delta <= 0 and g.gamma > 0 and g.vega > 0


def test_american_dominates_european_and_call_without_dividend_is_european():
    a = pricing.crr_price(100, 110, 0.5, 0.05, 0.0, 0.3, False, True)
    e = pricing.crr_price(100, 110, 0.5, 0.05, 0.0, 0.3, False, False)
    assert a >= e
    assert pricing.crr_price(100, 95, 0.5, 0.05, 0.0, 0.3, True, True) == pytest.approx(
        pricing.crr_price(100, 95, 0.5, 0.05, 0.0, 0.3, True, False), abs=1e-9)
    assert pricing.crr_price(100, 95, 0.5, 0.05, 0.0, 0.3, True, False, 800) == pytest.approx(
        pricing.bs_price(100, 95, 0.5, 0.05, 0.0, 0.3, True), abs=0.02)


def test_iv_round_trip_and_rejects_arbitrage_prices():
    p = pricing.bs_price(100, 100, 0.1, 0.02, 0.0, 0.33, True)
    assert pricing.implied_vol(p, 100, 100, 0.1, 0.02, 0.0, True) == pytest.approx(0.33, abs=1e-6)
    assert pricing.implied_vol(0.0, 100, 50, 0.1, 0.02, 0.0, True) is None     # below intrinsic


def test_expiry_payoffs():
    assert pricing.vertical_payoff(np.array([90, 100, 105, 120]), 100, 110, True).tolist() == [0, 0, 5, 10]
    assert pricing.vertical_payoff(np.array([80, 95, 100, 120]), 100, 90, False).tolist() == [10, 5, 0, 0]
    assert pricing.bs_price(100, 90, 0, 0.05, 0, 0.2, True) == 10


# ---------------------------------------------------------------- handbook option fees
def test_option_fee_schedule():
    assert pricing.option_order_fee(1, 2.0, False) == pytest.approx(1.99 + 0.30 + 0.015)          # min commission
    assert pricing.option_order_fee(10, 2.0, True) == pytest.approx(6.5 + 3.0 + 0.15 + 0.0279)
    assert pricing.option_order_fee(100, 0.05, False) == pytest.approx(15 + 30 + 1.5)              # $0.15 tier
    assert pricing.option_order_fee(0, 2.0, True) == 0.0
    assert pricing.round_trip_fee_per_contract(100, 2.0) == pytest.approx(1.93279)


# ---------------------------------------------------------------- D0 economics
def test_d0_one_cent_target_loses_on_every_win_above_ten_cents():
    e = strat_d.target_economics(premium=2.0, contracts=10, target="cent")
    assert e["gross_win_usd"] == 1.0 and e["net_win_usd"] < 0 and e["breakeven_win_rate"] is None
    cheap = strat_d.target_economics(premium=0.05, contracts=100, target="cent")
    assert 0 < cheap["net_win_usd"] < 0.1 and cheap["breakeven_win_rate"] > 0.98
    pct = strat_d.target_economics(premium=5.0, contracts=10, target="pct")
    assert pct["gross_win_usd"] == 5.0 and pct["mid_move_needed_per_share"] == pytest.approx(0.07)


def test_d0_is_marked_blocked_with_conflicting_sources():
    assert strat_d.D0_STATED["status"].startswith("BLOCKED")
    assert len(strat_d.D0_STATED["sources"]) == 2 and "conflict" in strat_d.D0_STATED


# ---------------------------------------------------------------- chains: blocked + no look-ahead
def test_missing_chains_raise_blocked(tmp_path):
    with pytest.raises(chain.OptionsDataBlocked, match="HISTORICAL OPTIONS CHAINS MISSING"):
        chain.load_chains("US.SPY", tmp_path)


def test_snapshot_excludes_future_and_stale_quotes():
    import pandas as pd
    t0 = pd.Timestamp("2025-03-03 15:00", tz="UTC")
    df = pd.DataFrame({"quote_time_utc": [t0 - pd.Timedelta(seconds=s) for s in (120, 30, -5)],
                       "expiry": ["2025-03-07"] * 3, "strike": [500.0] * 3, "right": ["C"] * 3,
                       "bid": [1.0, 2.0, 3.0], "ask": [1.1, 2.1, 3.1]})
    snap = chain.snapshot(df, t0, max_age_s=60)
    assert snap["bid"].tolist() == [2.0]


# ---------------------------------------------------------------- Monte Carlo
def test_bootstrap_terminal_is_reproducible_zero_drift_and_vol_scaled():
    rng = np.random.default_rng(1)
    past = rng.normal(0.001, 0.02, 500)
    a = montecarlo.bootstrap_terminal(past, 100.0, 5, 20_000, 5, 0.01, np.random.default_rng(3))
    b = montecarlo.bootstrap_terminal(past, 100.0, 5, 20_000, 5, 0.01, np.random.default_rng(3))
    assert np.array_equal(a, b)
    lr = np.log(a / 100)
    assert abs(lr.mean()) < 0.002                                   # demeaned: zero drift
    iid = np.log(montecarlo.bootstrap_terminal(past, 100.0, 5, 40_000, 1, 0.01, np.random.default_rng(4)) / 100)
    assert iid.std() == pytest.approx(0.01 * math.sqrt(5), rel=0.03)  # 1-day blocks: sqrt(H) scaling of target vol


def test_block_bootstrap_paths_and_path_risk():
    r = np.array([0.01, -0.02, 0.005, 0.0, -0.01] * 40)
    paths = montecarlo.block_bootstrap_paths(r, 8, 5_000, 4, seed=1)
    assert paths.shape == (5_000, 8) and set(np.unique(paths)) <= set(np.unique(r))
    risk = montecarlo.path_risk(paths)
    assert 0 <= risk["p_profit"] <= 1 and risk["cvar99_pct"] <= risk["cvar95_pct"] <= risk["p5_pct"]


def test_ewma_vol_uses_only_given_past():
    r = np.r_[np.full(100, 0.01), np.full(5, 0.05)]
    assert montecarlo.ewma_vol(r[:100]) == pytest.approx(0.01)
    assert montecarlo.ewma_vol(r) > 0.01


# ---------------------------------------------------------------- E rule mechanics on a SYNTHETIC chain
def _synthetic_chain(spot=500.0, iv=0.18, spread=0.02, oi=1000, expiry="E1", T=7 / 365):
    rows = []
    for k in np.arange(480, 521, 1.0):
        for right in ("C", "P"):
            mid = pricing.bs_price(spot, k, T, 0.04, 0.013, iv, right == "C")
            rows.append({"expiry": expiry, "strike": float(k), "right": right, "bid": max(mid - spread / 2, 0.0),
                         "ask": mid + spread / 2, "open_interest": oi})
    return rows


def test_e_candidates_are_defined_risk_debit_verticals():
    cands = strat_e.candidates(_synthetic_chain(), 500.0, "E1", strat_e.EConfig())
    assert {c["right"] for c in cands} == {"C", "P"}
    assert all(0 < c["debit"] < c["width"] for c in cands)


def test_e_rejects_illiquid_and_respects_risk_cap():
    assert strat_e.candidates(_synthetic_chain(oi=10), 500.0, "E1", strat_e.EConfig()) == []
    rng = np.random.default_rng(0)
    past = rng.normal(0, 0.012, 600)
    pick = strat_e.select(_synthetic_chain(iv=0.30), 500.0, "E1", 5, past, 50_000, strat_e.EConfig(min_edge=-1, max_p_loss=1))
    assert pick is not None and pick["stats"]["max_loss"] <= 0.005 * 50_000 + 20


def test_e_never_buys_overpriced_spreads():
    rng = np.random.default_rng(0)
    past = rng.normal(0, 0.005, 600)                      # realised vol ~8% vs quoted 40%: spreads are rich
    assert strat_e.select(_synthetic_chain(iv=0.40), 500.0, "E1", 5, past, 50_000) is None


# ---------------------------------------------------------------- F mechanics
def test_har_forecast_has_no_lookahead():
    rng = np.random.default_rng(2)
    rv = (rng.lognormal(-9.5, 0.4, 1200))
    f1 = strat_f.har_forecasts(rv)
    rv2 = rv.copy()
    rv2[1000:] *= 50                                     # change the future only
    f2 = strat_f.har_forecasts(rv2)
    assert np.allclose(f1[:1000], f2[:1000], equal_nan=True)
    assert np.isfinite(f1[900:1000]).all()


def test_f_decision_thresholds():
    assert strat_f.decide(0.25, 0.18, rich=4, cheap=4) == "sell_iron_butterfly"
    assert strat_f.decide(0.12, 0.18, rich=4, cheap=4) == "buy_straddle"
    assert strat_f.decide(0.19, 0.18, rich=4, cheap=4) is None
