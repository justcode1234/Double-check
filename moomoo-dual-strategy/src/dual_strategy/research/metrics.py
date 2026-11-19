"""Performance metrics, day-block bootstrap, benchmarks and regime labels. N/A (None) when not meaningful."""

from __future__ import annotations

import math
from datetime import date

import numpy as np
import pandas as pd

from dual_strategy.research.features import daily_on_calendar
from dual_strategy.research.portfolio import START_EQUITY, Result

MIN_DAYS_FOR_RATIOS = 60
MIN_DAYS_FOR_ANNUAL = 252
MIN_TRADES = 30


def _r(x, nd=4):
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return None
    return round(float(x), nd)


def sharpe(ret: np.ndarray) -> float | None:
    if len(ret) < MIN_DAYS_FOR_RATIOS or np.std(ret, ddof=1) == 0:
        return None
    return float(np.mean(ret) / np.std(ret, ddof=1) * math.sqrt(252))


def sortino(ret: np.ndarray) -> float | None:
    if len(ret) < MIN_DAYS_FOR_RATIOS:
        return None
    down = np.minimum(ret, 0)
    dd = math.sqrt(np.mean(down ** 2))
    return None if dd == 0 else float(np.mean(ret) / dd * math.sqrt(252))


def drawdown(equity: np.ndarray, start: float = START_EQUITY) -> tuple[float, float]:
    path = np.concatenate([[start], equity])
    peak = np.maximum.accumulate(path)
    dd = path - peak
    i = int(np.argmin(dd))
    return float(dd[i]), float(dd[i] / peak[i])


def block_bootstrap(ret: np.ndarray, block: int = 5, reps: int = 2000, seed: int = 7) -> dict:
    """Circular day-block bootstrap of daily returns: 95% CI for annualized Sharpe and total return."""
    n = len(ret)
    if n < MIN_DAYS_FOR_RATIOS:
        return {"sharpe_ci": None, "total_return_ci": None, "p_sharpe_le_0": None}
    rng = np.random.default_rng(seed)
    nb = math.ceil(n / block)
    sh, tot = np.empty(reps), np.empty(reps)
    ext = np.concatenate([ret, ret[:block]])
    for r in range(reps):
        starts = rng.integers(0, n, nb)
        sample = np.concatenate([ext[s:s + block] for s in starts])[:n]
        sd = sample.std(ddof=1)
        sh[r] = sample.mean() / sd * math.sqrt(252) if sd > 0 else 0.0
        tot[r] = np.prod(1 + sample) - 1
    return {"sharpe_ci": [_r(np.percentile(sh, 2.5), 3), _r(np.percentile(sh, 97.5), 3)],
            "total_return_ci": [_r(np.percentile(tot, 2.5)), _r(np.percentile(tot, 97.5))],
            "p_sharpe_le_0": _r(float(np.mean(sh <= 0)), 4)}


def spy_benchmark(days: list[date]) -> pd.Series:
    """SPY total-return close-to-close daily returns (dividend-adjusted closes) on the given sessions."""
    adj = daily_on_calendar("US.SPY", 1)["close"]
    return adj.pct_change(fill_method=None).reindex(days).fillna(0.0)


def regimes(days: list[date]) -> pd.DataFrame:
    """Ex-post day labels for reporting only (never used by any signal)."""
    raw = daily_on_calendar("US.SPY", 0)
    adj = daily_on_calendar("US.SPY", 1)["close"]
    rng = (raw["high"] - raw["low"]).replace(0, np.nan)
    trend = ((raw["close"] - raw["open"]).abs() / rng) >= 0.5
    vol14 = adj.pct_change(fill_method=None).rolling(14).std().shift(1)
    sma200 = adj.rolling(200).mean().shift(1)
    out = pd.DataFrame({
        "trend_day": trend, "vol14": vol14, "bull": adj.shift(1) > sma200,
    }).reindex(days)
    med = out["vol14"].median()
    out["high_vol"] = out["vol14"] > med
    return out


def metrics(res: Result, days: list[date] | None = None) -> dict:
    daily = res.daily if days is None else res.daily.loc[[d for d in days if d in res.daily.index]]
    if daily.empty:
        return {}
    dset = set(daily.index)
    trades = [t for t in res.trades if date.fromisoformat(t["date"]) in dset]
    ret = daily["ret"].to_numpy()
    start_eq = float(daily["equity_sod"].iloc[0])
    eq = start_eq * np.cumprod(1 + ret)
    net = float(eq[-1] - start_eq)
    gross = float(sum(t["gross_pnl"] for t in trades))
    pnl = np.array([t["net_pnl"] for t in trades])
    rs = np.array([t["r_multiple"] for t in trades if t["r_multiple"] is not None])
    wins, losses = pnl[pnl > 0], pnl[pnl <= 0]
    n = len(ret)
    years = n / 252
    dd_abs, dd_pct = drawdown(eq, start_eq)
    spy = spy_benchmark(list(daily.index)).to_numpy()
    w = float(daily["exposure"].mean())
    bm = np.prod(1 + spy) - 1
    bm_matched = np.prod(1 + w * spy) - 1
    boot = block_bootstrap(ret)
    symbols = sorted({t["symbol"] for t in trades})
    total_ret = eq[-1] / start_eq - 1
    return {
        "strategy": res.strategy, "variant": res.variant, "label": res.label,
        "start": daily.index[0].isoformat(), "end": daily.index[-1].isoformat(), "sessions": n,
        "data_gap_sessions": int(daily["data_gap"].sum()),
        "symbols_traded": len(symbols), "symbols": " ".join(s.replace("US.", "") for s in symbols[:60]),
        "trades": len(trades), "long_trades": sum(t["side"] == "long" for t in trades),
        "short_trades": sum(t["side"] == "short" for t in trades),
        "gross_pnl": _r(gross, 2), "gross_return_pct": _r(100 * gross / start_eq, 3),
        "net_pnl": _r(net, 2), "net_return_pct": _r(100 * total_ret, 3),
        "annualized_return_pct": _r(100 * ((1 + total_ret) ** (1 / years) - 1), 3) if n >= MIN_DAYS_FOR_ANNUAL else None,
        "win_rate_pct": _r(100 * len(wins) / len(pnl), 2) if len(pnl) else None,
        "avg_win": _r(wins.mean(), 2) if len(wins) else None, "avg_loss": _r(losses.mean(), 2) if len(losses) else None,
        "expectancy_usd": _r(pnl.mean(), 2) if len(pnl) else None,
        "expectancy_r": _r(rs.mean(), 4) if len(rs) else None,
        "profit_factor": _r(wins.sum() / -losses.sum(), 3) if len(losses) and losses.sum() < 0 else None,
        "max_drawdown_usd": _r(dd_abs, 2), "max_drawdown_pct": _r(100 * dd_pct, 3),
        "sharpe": _r(sharpe(ret), 3), "sortino": _r(sortino(ret), 3),
        "sharpe_ci95": boot["sharpe_ci"], "total_return_ci95": boot["total_return_ci"],
        "p_bootstrap_sharpe_le_0": boot["p_sharpe_le_0"],
        "worst_trade_usd": _r(pnl.min(), 2) if len(pnl) else None, "worst_day_pct": _r(100 * ret.min(), 3),
        "worst_intraday_mark_pct": _r(100 * daily["worst_intraday_pct"].min(), 3),
        "avg_gross_exposure": _r(w, 4), "time_in_market": _r(daily["in_market"].mean(), 4),
        "turnover_x_per_year": _r(daily["turnover"].sum() / daily["equity_sod"].mean() / years, 2) if years > 0 else None,
        "fees_usd": _r(daily["fees"].sum(), 2), "slippage_usd": _r(daily["slippage"].sum(), 2),
        "rejected_orders": int(daily["rejected"].sum()), "missed_signals": int(daily["missed"].sum()),
        "partial_fills": int(daily["partial"].sum()), "no_capacity": int(daily["no_capacity"].sum()),
        "spy_buy_hold_pct": _r(100 * bm, 3), "spy_exposure_matched_pct": _r(100 * bm_matched, 3),
        "excess_vs_matched_pct": _r(100 * (total_ret - bm_matched), 3),
        "statistically_meaningful": bool(n >= MIN_DAYS_FOR_ANNUAL and len(pnl) >= MIN_TRADES),
    }


def period_table(res: Result, freq: str) -> pd.DataFrame:
    d = res.daily.copy()
    idx = pd.to_datetime(pd.Index(d.index))
    key = idx.year.astype(str) if freq == "Y" else idx.year.astype(str) + "Q" + idx.quarter.astype(str)
    d["key"] = list(key)
    out = d.groupby("key").agg(sessions=("ret", "size"), trades=("trades", "sum"),
                               net_return_pct=("ret", lambda r: 100 * (np.prod(1 + r) - 1)),
                               worst_day_pct=("ret", lambda r: 100 * r.min()))
    return out.round(3)


def regime_table(res: Result) -> pd.DataFrame:
    days = list(res.daily.index)
    reg = regimes(days)
    d = res.daily.join(reg)
    rows = []
    for name, mask in (("trend_day", d["trend_day"] == True), ("choppy_day", d["trend_day"] == False),
                       ("high_vol", d["high_vol"] == True), ("low_vol", d["high_vol"] == False),
                       ("bull_above_200d", d["bull"] == True), ("bear_below_200d", d["bull"] == False)):
        sub = d[mask]
        r = sub["ret"].to_numpy()
        rows.append({"regime": name, "sessions": len(sub), "trades": int(sub["trades"].sum()),
                     "mean_daily_bp": _r(1e4 * r.mean(), 2) if len(r) else None,
                     "net_return_pct": _r(100 * (np.prod(1 + r) - 1), 3) if len(r) else None,
                     "hit_days_pct": _r(100 * np.mean(r[sub["trades"].to_numpy() > 0] > 0), 1) if (sub["trades"] > 0).any() else None})
    return pd.DataFrame(rows)


def drawdown_ladder(res: Result, levels=(0.02, 0.035, 0.05), windows=(8, 20)) -> dict:
    """The competition ladder is NOT applied to multi-year results; report when it would have fired."""
    ret = res.daily["ret"].to_numpy()
    days = list(res.daily.index)
    eq = START_EQUITY * np.cumprod(1 + ret)
    peak = np.maximum.accumulate(np.concatenate([[START_EQUITY], eq]))[1:]
    dd = eq / peak - 1
    out = {}
    for lv in levels:
        hit = np.where(dd <= -lv)[0]
        out[f"first_breach_{lv:.3f}"] = days[hit[0]].isoformat() if len(hit) else None
        for w in windows:
            fires = 0
            total = max(0, len(ret) - w + 1)
            for s in range(total):
                e = np.cumprod(1 + ret[s:s + w])
                p = np.maximum.accumulate(np.concatenate([[1.0], e]))[1:]
                fires += bool((e / p - 1 <= -lv).any())
            out[f"share_{w}d_windows_breach_{lv:.3f}"] = _r(fires / total, 4) if total else None
    return out
