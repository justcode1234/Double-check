"""Monte Carlo utilities: (1) forward distribution of the underlying for Strategy E, from past returns only;
(2) day-block bootstrap of a strategy's realised daily returns for the A-F risk study."""

from __future__ import annotations

import math

import numpy as np


def ewma_vol(returns: np.ndarray, lam: float = 0.94) -> float:
    """RiskMetrics EWMA daily volatility using every return in `returns` (caller passes only past data)."""
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    if len(r) < 20:
        return float("nan")
    w = lam ** np.arange(len(r) - 1, -1, -1)
    return float(math.sqrt(np.sum(w * r * r) / np.sum(w)))


def bootstrap_terminal(past_log_returns: np.ndarray, spot: float, horizon: int, n: int = 10_000, block: int = 5,
                       target_daily_vol: float | None = None, rng: np.random.Generator | None = None) -> np.ndarray:
    """Terminal prices after `horizon` sessions: circular block bootstrap of demeaned past daily log returns,
    rescaled to `target_daily_vol` when given (vol-regime adjustment), zero drift."""
    rng = rng or np.random.default_rng(0)
    r = np.asarray(past_log_returns, dtype=float)
    r = r[np.isfinite(r)]
    r = r - r.mean()
    if target_daily_vol is not None and r.std(ddof=1) > 0:
        r = r * (target_daily_vol / r.std(ddof=1))
    m = len(r)
    ext = np.concatenate([r, r[:block]])
    nb = math.ceil(horizon / block)
    starts = rng.integers(0, m, size=(n, nb))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]).reshape(n, -1)[:, :horizon]
    return spot * np.exp(ext[idx].sum(axis=1))


def spread_stats(net_pnl: np.ndarray) -> dict:
    """Expected value, loss probability and 95% expected shortfall of a simulated P&L vector."""
    x = np.sort(np.asarray(net_pnl, dtype=float))
    k = max(1, int(0.05 * len(x)))
    return {"expected": float(x.mean()), "p_loss": float(np.mean(x < 0)), "cvar95": float(x[:k].mean()),
            "p5": float(np.percentile(x, 5)), "p95": float(np.percentile(x, 95))}


def block_bootstrap_paths(daily_ret: np.ndarray, horizon: int, n: int = 10_000, block: int = 4,
                          seed: int = 20261119) -> np.ndarray:
    """(n, horizon) matrix of daily returns resampled in circular day blocks (keeps intra-block clustering)."""
    r = np.asarray(daily_ret, dtype=float)
    rng = np.random.default_rng(seed)
    ext = np.concatenate([r, r[:block]])
    nb = math.ceil(horizon / block)
    starts = rng.integers(0, len(r), size=(n, nb))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]).reshape(n, -1)[:, :horizon]
    return ext[idx]


def path_risk(paths: np.ndarray) -> dict:
    """Short-horizon outcome distribution of bootstrapped daily-return paths."""
    eq = np.cumprod(1 + paths, axis=1)
    tot = eq[:, -1] - 1
    peak = np.maximum.accumulate(np.concatenate([np.ones((len(eq), 1)), eq], axis=1), axis=1)[:, 1:]
    mdd = (eq / peak - 1).min(axis=1)
    s = np.sort(tot)
    k95, k99 = max(1, int(0.05 * len(s))), max(1, int(0.01 * len(s)))
    loss_day = paths < 0
    streak = np.zeros(len(paths), dtype=int)
    cur = np.zeros(len(paths), dtype=int)
    for j in range(paths.shape[1]):
        cur = np.where(loss_day[:, j], cur + 1, 0)
        streak = np.maximum(streak, cur)
    return {"median_pct": 100 * float(np.median(tot)), "p5_pct": 100 * float(np.percentile(tot, 5)),
            "p95_pct": 100 * float(np.percentile(tot, 95)), "p_profit": float(np.mean(tot > 0)),
            "p_loss_gt_1pct": float(np.mean(tot < -0.01)), "p_loss_gt_3pct": float(np.mean(tot < -0.03)),
            "p_loss_gt_5pct": float(np.mean(tot < -0.05)), "cvar95_pct": 100 * float(s[:k95].mean()),
            "cvar99_pct": 100 * float(s[:k99].mean()), "mdd_median_pct": 100 * float(np.median(mdd)),
            "mdd_p95_pct": 100 * float(np.percentile(mdd, 5)), "p_dd_ge_5pct": float(np.mean(mdd <= -0.05)),
            "loss_streak_median": float(np.median(streak)), "loss_streak_p95": float(np.percentile(streak, 95))}
