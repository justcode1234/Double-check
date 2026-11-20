"""Strategy F - Black-Scholes / implied-vs-forecast volatility, defined-risk SPY options (pre-registered 2026-11-19;
never run on historical chains because none exist).

Rule (decision 10:00 ET each session):
1. Forecast: HAR-RV (Corsi 2009) 21-session realised volatility, refit daily by OLS on the trailing 756 sessions using
   only targets fully observed before the decision (RV = 5-minute RTH log-return variance + overnight return^2).
2. Market vol: ATM implied vol of the expiry 7-14 calendar days out, inverted from the bid/ask MID with a CRR
   American model, continuous dividend yield (trailing 12m SPY dividends / spot) and the 3-month T-bill rate.
   Reject the day if either leg is unquoted, quoted spread > 10% of mid, or OI < 500.
3. spread = IV - forecast (vol points). If spread >= RICH: SELL an ATM iron butterfly with wings at +/- 1 expected
   move (max loss = wing - credit). If spread <= -CHEAP: BUY the ATM straddle (max loss = debit). Else no trade.
   RICH/CHEAP are chosen on 2021-22 only (here from the VIX proxy study), never on the holdout.
4. Size: max loss <= 0.5% equity; 1 open F trade at a time. Exit: 50% of max profit (credit) / +50% of debit
   (straddle) at bid/ask, else close 15:30 one session before expiry (no exercise or assignment risk is carried).
5. Naive baseline for the incremental-value test: the same structure on the same days regardless of the spread.

What CAN be measured without option chains: forecast accuracy, and whether (VIX - forecast) predicts the forward
variance risk premium. That is signal evidence, NOT executable option P&L (no bid/ask, skew, or SPY-vs-SPX basis)."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from dual_strategy.research.data import Intraday, resample


@dataclass(frozen=True)
class FConfig:
    horizon: int = 21
    train: int = 756
    min_dte_days: int = 7
    max_dte_days: int = 14
    rich_grid: tuple = (0.0, 2.0, 4.0, 6.0)
    risk_frac: float = 0.005
    take_profit: float = 0.50


def daily_realized_variance(b1: Intraday, adj_open: np.ndarray, adj_close: np.ndarray) -> np.ndarray:
    """Per-session variance: sum of squared 5-min RTH log returns + squared overnight log return (adjusted)."""
    b5 = resample(b1, 5)
    with np.errstate(all="ignore"):
        lr = np.diff(np.log(b5.close), axis=1)
        first = np.log(b5.close[:, 0] / b5.open[:, 0])
    rv_in = np.nansum(lr ** 2, axis=1) + np.nan_to_num(first ** 2)
    ok = b5.complete()
    over = np.full(len(adj_close), np.nan)
    over[1:] = np.log(adj_open[1:] / adj_close[:-1])
    rv = rv_in + np.nan_to_num(over ** 2)
    rv[~ok] = np.nan
    return rv


def har_forecasts(rv: np.ndarray, cfg: FConfig = FConfig()) -> np.ndarray:
    """f[t] = forecast of annualised vol over sessions t..t+H-1, made at the close of t-1 with data < t only."""
    n, H = len(rv), cfg.horizon
    s = pd.Series(rv)
    d = s.shift(1).to_numpy()                                   # RV_{t-1}
    w = s.rolling(5, min_periods=4).mean().shift(1).to_numpy()  # mean RV_{t-5..t-1} (tolerates 1 missing day)
    m = s.rolling(22, min_periods=20).mean().shift(1).to_numpy()  # mean RV_{t-22..t-1}
    y = s[::-1].rolling(H, min_periods=H - 2).mean()[::-1].to_numpy()  # mean RV_{t..t+H-1}
    X = np.column_stack([np.ones(n), d, w, m])
    out = np.full(n, np.nan)
    for t in range(n):
        lo, hi = max(0, t - cfg.train), t - H + 1               # target window of sample s ends at s+H-1 <= t-1
        if hi - lo < 250:
            continue
        Xs, ys = X[lo:hi], y[lo:hi]
        ok = np.isfinite(Xs).all(axis=1) & np.isfinite(ys)
        if ok.sum() < 250 or not np.isfinite(X[t]).all():
            continue
        beta, *_ = np.linalg.lstsq(Xs[ok], ys[ok], rcond=None)
        out[t] = math.sqrt(252 * max(float(X[t] @ beta), 1e-8))
    return out


def forward_realized_vol(rv: np.ndarray, horizon: int = 21) -> np.ndarray:
    """Annualised realised vol over sessions t..t+H-1 (the outcome; never used as an input)."""
    y = pd.Series(rv)[::-1].rolling(horizon, min_periods=horizon - 2).mean()[::-1].to_numpy()
    return np.sqrt(252 * y)


def decide(iv: float, forecast: float, rich: float, cheap: float) -> str | None:
    spread = 100 * (iv - forecast)
    if spread >= rich:
        return "sell_iron_butterfly"
    if spread <= -cheap:
        return "buy_straddle"
    return None
