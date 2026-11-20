"""Independent audit math and shared output paths. No strategy package imports."""

from decimal import Decimal as D
from pathlib import Path
from statistics import NormalDist
import math

import numpy as np
import pandas as pd

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
HIST = ROOT / "data-cache/history"
REPRO = OUT / "reproduction"
MIRROR = ROOT / "data-cache/options_history_raw/dubach_mirror"
SEED = 20261119


def minute(value):
    hour, minute = map(int, value.split(":"))
    return hour * 60 + minute - 570


def option_fee(n, price, is_sell, activity_both=False):
    rate = D(".65") if D(str(price)) > D(".10") else D(".15")
    return max(D("1.99"), n * rate) + n * (
        D(".315") + D(".00279") * (is_sell or activity_both)
    )


def stock_cost(n, price, buy, schedule, bps):
    sign = 1 if buy else -1
    if schedule == "paper":
        return price + sign * (price * bps / 1e4 + .001), .0035 * n
    fee = 0 if schedule == "zero" else .99 + (0 if buy else min(.000166 * n, 8.30))
    return price * (1 + sign * bps / 1e4), fee


def fee_table():
    rows = []
    for entry in (D(".02"), D(".05"), D(".09"), D(".10"), D(".11"), D("1.00")):
        for n in tuple(range(1, 22)) + (100, 1000):
            for both in (False, True):
                exit_px = entry + D(".01")
                buy, sell = option_fee(n, entry, False, both), option_fee(n, exit_px, True, both)
                win = D(n) - buy - sell
                loss = entry * 100 * n + buy
                rows.append({"entry_ask": entry, "exit_bid": exit_px, "contracts": n,
                             "activity": "both_legs" if both else "sell_only",
                             "buy_fee": buy, "sell_fee": sell, "total_fee": buy + sell,
                             "gross_target": n, "net_target": win, "worthless_loss": loss,
                             "break_even_move": (buy + sell) / (100 * n),
                             "win_rate_if_other_trades_expire_worthless": loss / (loss + win) if win > 0 else None})
    pd.DataFrame(rows).to_csv(OUT / "fee_audit.csv", index=False)


NORM = NormalDist()


def european_price(s, k, t, r, q, vol, cp):
    width = vol * math.sqrt(t)
    a = (math.log(s / k) + (r - q) * t) / width + width / 2
    return cp * (s * math.exp(-q * t) * NORM.cdf(cp * a)
                 - k * math.exp(-r * t) * NORM.cdf(cp * (a - width)))


def invert_iv(price, s, k, t, r, q, cp):
    lo, hi = 1e-5, 5.
    if not european_price(s, k, t, r, q, lo, cp) <= price <= european_price(s, k, t, r, q, hi, cp):
        return float("nan")
    for _ in range(46):
        mid = (lo + hi) / 2
        if european_price(s, k, t, r, q, mid, cp) < price:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def prob_above(low_price, high_price, strike_width, cp, rate=0., years=0.):
    slope = (high_price - low_price) * math.exp(rate * years) / strike_width
    result = -slope if cp == 1 else 1 - slope
    return result if 0 <= result <= 1 else float("nan")


def intrinsic(s, legs):
    return sum(side * max((s - strike) * (1 if right == "C" else -1), 0)
               for strike, right, side in legs)


def block_paths(returns, horizon, block=4, paths=10000, seed=SEED):
    rng = np.random.default_rng(seed)
    starts = rng.integers(0, len(returns), size=(paths, math.ceil(horizon / block), 1))
    idx = (starts + np.arange(block)) % len(returns)
    idx = idx.reshape(paths, -1)[:, :horizon]
    return np.prod(1 + returns[idx], axis=1) - 1


def risk_rows(variant, returns, sample="holdout", paths=10000):
    returns = np.asarray(returns, dtype=float)
    full = block_paths(returns, len(returns), paths=paths)
    lower, upper = np.quantile(full, [.025, .975])
    rows = []
    for block in (1, 2, 4, 8):
        outcomes = block_paths(returns, 8, block=block, paths=paths)
        floor = np.quantile(outcomes, .05)
        rows.append({"variant": variant, "sample": sample, "sessions": len(returns), "block": block, "paths": paths,
                     "probability_negative": np.mean(outcomes < -1e-12), "median_8_session_pct": 100 * np.median(outcomes),
                     "var_95_return_pct": 100 * floor, "expected_shortfall_pct": 100 * outcomes[outcomes <= floor].mean(),
                     "full_return_ci95_low_pct": 100 * lower, "full_return_ci95_high_pct": 100 * upper})
    return rows


if __name__ == "__main__":
    fee_table()
