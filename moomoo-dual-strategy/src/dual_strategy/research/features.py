"""Point-in-time features. Every value for session t uses only sessions strictly before t, or bars of t
that have already closed at the decision time (enforced by the callers' bar indexing and by tests)."""

from __future__ import annotations

from datetime import date

import exchange_calendars
import numpy as np
import pandas as pd

from dual_strategy.research.data import Intraday, load_daily, sessions, split_back_factor
from dual_strategy.research.fetch import END

DAILY_START = date(2019, 1, 2)


def daily_on_calendar(symbol: str, autype: int = 0) -> pd.DataFrame:
    """Daily bars reindexed to every XNYS session since the first daily bar (missing days stay NaN),
    so rolling windows always span consecutive exchange sessions."""
    df = load_daily(symbol, autype)
    cal = exchange_calendars.get_calendar("XNYS")
    full = [s.date() for s in cal.sessions_in_range(DAILY_START.isoformat(), END.isoformat())]
    extra = set(df.index) - set(full)
    if extra:
        raise ValueError(f"{symbol} daily bars on non-sessions: {sorted(extra)[:5]}")
    return df.reindex(full)


def stock_daily_features(symbol: str) -> pd.DataFrame:
    """ATR14 and ADV14 expressed in session-t share units (split-adjusted), from the 15/14 prior sessions.
    A feature is NaN unless every required prior session exists."""
    df = daily_on_calendar(symbol, 0)
    days = list(df.index)
    back = split_back_factor(symbol, days)
    adj_h, adj_l, adj_c = df["high"] / back, df["low"] / back, df["close"] / back
    adj_v = df["volume"] * back
    tr = pd.concat([adj_h - adj_l, (adj_h - adj_c.shift(1)).abs(), (adj_l - adj_c.shift(1)).abs()], axis=1).max(axis=1, skipna=False)
    atr_adj = tr.rolling(14, min_periods=14).mean().shift(1)
    adv_adj = adj_v.rolling(14, min_periods=14).mean().shift(1)
    out = pd.DataFrame({
        "atr14": atr_adj * back,
        "adv14": adv_adj / back,
        "prev_close": df["close"].shift(1),
        "open_daily": df["open"],
        "back": back,
    }, index=days)
    return out.loc[[d for d in sessions() if d in out.index]]


def opening_rvol(bars5: Intraday, symbol: str) -> np.ndarray:
    """RVOL_t = first-5-minute volume_t / mean(first-5-minute volume over the 14 prior sessions), split-adjusted."""
    back = split_back_factor(symbol, bars5.days)
    v = bars5.volume[:, 0] * back
    s = pd.Series(v)
    base = s.rolling(14, min_periods=14).mean().shift(1).to_numpy()
    with np.errstate(all="ignore"):
        return v / base


def session_vwap(bars: Intraday, method: str) -> np.ndarray:
    """Cumulative regular-session VWAP through the end of each bar; turnover-based or HLC3 proxy."""
    vol = np.nancumsum(bars.volume, axis=1)
    if method == "turnover":
        num = np.nancumsum(bars.turnover, axis=1)
    elif method == "hlc3":
        num = np.nancumsum((bars.high + bars.low + bars.close) / 3 * bars.volume, axis=1)
    else:
        raise ValueError(method)
    with np.errstate(all="ignore"):
        out = num / vol
    out[np.isnan(bars.close)] = np.nan
    return out


def noise_sigma(bars1: Intraday, slots: list[int], strict: bool) -> dict[int, np.ndarray]:
    """sigma[t, slot] = mean |close_slot / open_0930 - 1| over 14 prior sessions.
    strict: the exact 14 prior XNYS sessions must all have that bar (repo baseline; half-days block late slots).
    not strict: the 14 most recent prior sessions that have the bar (skips half-days/gaps)."""
    open0 = bars1.open[:, 0]
    out = {}
    for j in slots:
        move = np.abs(bars1.close[:, j] / open0 - 1)
        if strict:
            out[j] = pd.Series(move).rolling(14, min_periods=14).mean().shift(1).to_numpy()
            continue
        sig = np.full(len(move), np.nan)
        hist: list[float] = []
        for t in range(len(move)):
            if len(hist) >= 14:
                sig[t] = float(np.mean(hist[-14:]))
            if not np.isnan(move[t]):
                hist.append(float(move[t]))
        out[j] = sig
    return out


def spy_daily_vol(days: list[date]) -> np.ndarray:
    """Sample std (ddof=1) of the 14 prior daily total returns (adjusted closes), for Noise-Area vol targeting."""
    adj = daily_on_calendar("US.SPY", 1)["close"]
    ret = adj.pct_change(fill_method=None)
    vol = ret.rolling(14, min_periods=14).std(ddof=1).shift(1)
    return vol.reindex(days).to_numpy()


def spy_daily_atr(days: list[date]) -> np.ndarray:
    feats = stock_daily_features("US.SPY")
    return feats["atr14"].reindex(days).to_numpy()
