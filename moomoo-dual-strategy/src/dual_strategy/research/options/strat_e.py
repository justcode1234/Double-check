"""Strategy E - Monte Carlo-driven defined-risk SPY debit verticals (pre-registered 2026-11-19, never run on
historical chains because none exist).

Rule (decision 10:00 ET each session):
1. Forward distribution: 10,000 paths of the SPY terminal price at the chosen expiry from a circular 5-day block
   bootstrap of the trailing 500 daily log returns (past only, demeaned), rescaled to the EWMA(0.94) daily vol known
   at the prior close.
2. Expiry: the nearest listed expiry 5-10 sessions ahead. Candidates: call and put debit verticals, long strike =
   listed strike nearest spot, short strike nearest spot x (1 +/- w), w in {1%, 2%}.
3. Executable cost: long ASK - short BID; exit value at expiry = clipped spread intrinsic (closed 15:30 on expiry day
   at long BID - short ASK if still open); fees = handbook option fees on 4 orders.
4. Enter the single best candidate only if E[net]/max_loss >= MIN_EDGE, P(loss) <= MAX_P_LOSS, both legs quoted
   (bid > 0), quoted spread <= 10% of mid per leg, open interest >= 500 per leg. Max loss per trade <= 0.5% of equity;
   at most 3 open E trades. MIN_EDGE/MAX_P_LOSS are pre-registered and must be confirmed on 2021-22 chains only.
5. Exit: take profit at 50% of max gain (bid/ask), else close 15:30 on expiry day. No other stop: the debit is the
   maximum loss.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from dual_strategy.research.options.montecarlo import bootstrap_terminal, ewma_vol, spread_stats
from dual_strategy.research.options.pricing import MULTIPLIER, option_order_fee, vertical_payoff


@dataclass(frozen=True)
class EConfig:
    lookback: int = 500
    block: int = 5
    n_paths: int = 10_000
    ewma_lambda: float = 0.94
    min_sessions: int = 5
    max_sessions: int = 10
    widths: tuple = (0.01, 0.02)
    min_edge: float = 0.10          # pre-registered, NOT fitted (no development chains exist)
    max_p_loss: float = 0.60        # pre-registered
    max_leg_spread: float = 0.10
    min_oi: int = 500
    risk_frac: float = 0.005
    take_profit: float = 0.50


def candidates(chain: list[dict], spot: float, expiry, cfg: EConfig) -> list[dict]:
    """chain rows: {expiry, strike, right, bid, ask, open_interest}. Returns executable debit verticals."""
    legs = {(r["strike"], r["right"]): r for r in chain if r["expiry"] == expiry}
    strikes = sorted({k for k, _ in legs})
    if not strikes:
        return []
    nearest = lambda x: min(strikes, key=lambda k: abs(k - x))
    out = []
    for right, sign in (("C", 1), ("P", -1)):
        k_long = nearest(spot)
        for w in cfg.widths:
            k_short = nearest(spot * (1 + sign * w))
            if (k_short - k_long) * sign <= 0:
                continue
            a, b = legs.get((k_long, right)), legs.get((k_short, right))
            if not a or not b or a["bid"] <= 0 or b["bid"] <= 0:
                continue
            ok = all((x["ask"] - x["bid"]) / (0.5 * (x["ask"] + x["bid"])) <= cfg.max_leg_spread and
                     x["open_interest"] >= cfg.min_oi for x in (a, b))
            debit = a["ask"] - b["bid"]
            width = abs(k_short - k_long)
            if ok and 0 < debit < width:
                out.append({"right": right, "k_long": k_long, "k_short": k_short, "debit": debit, "width": width,
                            "exit_bid_ask": (a["bid"], b["ask"])})
    return out


def evaluate(cand: dict, terminal: np.ndarray, contracts: int) -> dict:
    payoff = vertical_payoff(terminal, cand["k_long"], cand["k_short"], cand["right"] == "C") * MULTIPLIER * contracts
    fees = 2 * option_order_fee(contracts, cand["debit"], False) + 2 * option_order_fee(contracts, cand["debit"], True)
    cost = cand["debit"] * MULTIPLIER * contracts
    net = payoff - cost - fees
    stats = spread_stats(net)
    stats["max_loss"] = cost + fees
    stats["edge_ratio"] = stats["expected"] / stats["max_loss"] if stats["max_loss"] > 0 else float("nan")
    return stats


def select(chain: list[dict], spot: float, expiry, sessions_to_expiry: int, past_log_returns: np.ndarray,
           equity: float, cfg: EConfig = EConfig(), seed: int = 0) -> dict | None:
    if not cfg.min_sessions <= sessions_to_expiry <= cfg.max_sessions:
        return None
    past = np.asarray(past_log_returns, dtype=float)[-cfg.lookback:]
    vol = ewma_vol(past, cfg.ewma_lambda)
    if not math.isfinite(vol):
        return None
    terminal = bootstrap_terminal(past, spot, sessions_to_expiry, cfg.n_paths, cfg.block, vol, np.random.default_rng(seed))
    best = None
    for cand in candidates(chain, spot, expiry, cfg):
        per = cand["debit"] * MULTIPLIER
        contracts = int(cfg.risk_frac * equity // per) if per > 0 else 0
        if contracts < 1:
            continue
        st = evaluate(cand, terminal, contracts)
        if st["edge_ratio"] >= cfg.min_edge and st["p_loss"] <= cfg.max_p_loss:
            if best is None or st["edge_ratio"] > best["stats"]["edge_ratio"]:
                best = {**cand, "contracts": contracts, "stats": st}
    return best


# ---------------------------------------------------------------- empirical part: forecast calibration on SPY
def calibration_study(log_ret: np.ndarray, days: list, test_idx: list[int], horizon: int = 5,
                      ks=(-1.0, -0.5, 0.0, 0.5, 1.0), cfg: EConfig = EConfig(), seed: int = 7) -> list[dict]:
    """For each decision index t (spot = close t-1), predict P(close_{t-1+H} > spot*exp(k*sigma*sqrt(H))) with
    (a) the E bootstrap, (b) a zero-drift lognormal with the same EWMA sigma, (c) unscaled historical frequency.
    Uses only returns up to t-1. Returns one row per (t, k) with predictions and the realised outcome."""
    rng = np.random.default_rng(seed)
    rows = []
    for t in test_idx:
        past = log_ret[max(0, t - cfg.lookback):t]
        if len(past) < cfg.lookback or t - 1 + horizon >= len(log_ret):
            continue
        vol = ewma_vol(past, cfg.ewma_lambda)
        realized = float(np.sum(log_ret[t:t + horizon]))
        sims = np.log(bootstrap_terminal(past, 1.0, horizon, cfg.n_paths, cfg.block, vol, rng))
        hist_h = np.array([past[i:i + horizon].sum() for i in range(len(past) - horizon + 1)])
        for k in ks:
            thr = k * vol * math.sqrt(horizon)
            p_normal = 0.5 * (1 - math.erf((thr + 0.5 * vol * vol * horizon) / (vol * math.sqrt(2 * horizon))))
            rows.append({"day": days[t], "k": k, "p_mc": float(np.mean(sims > thr)), "p_normal": p_normal,
                         "p_hist": float(np.mean(hist_h > thr)), "y": int(realized > thr)})
    return rows
