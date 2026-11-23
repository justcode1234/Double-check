"""Two-vote signal rules shared by the quote monitor and historical replay."""

import numpy as np
import pandas as pd

from dual_strategy.competition.signals import rsi2

SYMBOLS = ("US.SPY", "US.QQQ")
FIRST_ENTRY = 6  # 10:00 ET
NORMAL_LAST_ENTRY = 60  # 14:30 ET


def below(x, level):
    delta = x - level
    return (delta.shift() >= 0) & (delta < 0)


def above(x, level):
    return below(-x, -level)


def ema(x, n):
    return x.ewm(span=n, adjust=False, min_periods=n).mean()


def wilder(x, n):
    values = x.dropna()
    smoothed = values.iloc[n - 1 :].copy()
    if not smoothed.empty:
        smoothed.iloc[0] = values.iloc[:n].mean()
    return smoothed.ewm(alpha=1 / n, adjust=False).mean().reindex(x.index)


def indicator_rules(bars):
    h, l, c = bars.high, bars.low, bars.close
    prior = c.shift()
    tr = pd.concat([h - l, (h - prior).abs(), (l - prior).abs()], axis=1).max(axis=1)
    up_move, down_move = h.diff(), -l.diff()
    plus = wilder(up_move.where((up_move > down_move) & (up_move > 0), 0), 14)
    minus = wilder(down_move.where((down_move > up_move) & (down_move > 0), 0), 14)
    dm_sum = plus + minus
    # Both DI values have the same ATR denominator, which cancels in DX.
    adx = wilder((100 * (plus - minus).abs() / dm_sum).where(dm_sum > 0, 0), 14)
    center = ema(c, 20)
    keltner_low = center - 2 * wilder(tr, 10)
    rsi = pd.Series(rsi2(c.to_list()), index=c.index)
    macd = ema(c, 12) - ema(c, 26)
    signal = ema(macd, 9)
    low14 = l.rolling(14).min()
    k = 100 * (c - low14) / (h.rolling(14).max() - low14)
    d = k.rolling(3).mean()
    atr22 = wilder(tr, 22)
    chandelier_long = (h.rolling(22).max() - 3 * atr22).shift()
    chandelier_short = (l.rolling(22).min() + 3 * atr22).shift()
    return {
        "Donchian": (below(c, l.rolling(20).min().shift()), c > h.rolling(10).max().shift()),
        "Keltner": (below(c, keltner_low), c > center),
        "RSI(2)": ((rsi > 90) & (c < c.rolling(200).mean()), c < c.rolling(5).mean()),
        "MACD": (below(macd, signal) & (macd < 0), above(macd, signal)),
        "DMI/ADX": (above(minus, plus) & (adx >= 25), plus > minus),
        "Stochastic": (below(k, d) & (k.shift() >= 80), k <= 20),
        "Chandelier": (below(c, chandelier_long), c > chandelier_short),
    }


def last_entry(n):
    return min(NORMAL_LAST_ENTRY, n - 6)


def first_window(entry_mask, exit_mask, n):
    start, stop = FIRST_ENTRY - 1, last_entry(n)
    signals = np.flatnonzero(entry_mask[start:stop])
    if not len(signals):
        return None
    entry = start + int(signals[0]) + 1
    flat = n - 1
    exits = np.flatnonzero(exit_mask[entry : flat - 1])
    return entry, entry + int(exits[0]) + 1 if len(exits) else flat


def turtle_soup_window(frame, section, n):
    start, stop = FIRST_ENTRY - 1, last_entry(n)
    high, low = frame.high.to_numpy(), frame.low.to_numpy()
    close, opening = frame.close.to_numpy(), frame.open.to_numpy()
    for j in range(start, min(stop, section.stop - section.start - 1)):
        k = section.start + j
        recent_high = high[k - 20 : k]
        ceiling = recent_high.max()
        if not (high[k] > ceiling > close[k] and recent_high[-3:].max() < ceiling):
            continue
        if opening[k + 1] >= high[k]:
            continue
        floor = low[k - 20 : k].min()
        exit_mask = (close[section] >= high[k]) | (close[section] <= (ceiling + floor) / 2)
        entry_mask = np.zeros(n, dtype=bool)
        entry_mask[j] = True
        return first_window(entry_mask, exit_mask, n)
    return None


def session_windows(frame, section, n, masks, nr7):
    windows = {}
    for name, (enter, exit) in masks.items():
        if window := first_window(enter[section], exit[section], n):
            windows[name] = window
    if window := turtle_soup_window(frame, section, n):
        windows["Turtle Soup"] = window
    if nr7 and section.stop - section.start >= 3:
        day = frame.iloc[section]
        entry = day.close.to_numpy() < day.low.iloc[:3].min()
        exit = day.close.to_numpy() >= day.high.iloc[:3].max()
        if window := first_window(entry, exit, n):
            windows["NR7"] = window
    return windows


def supported_side(sell, buy, required_votes):
    if sell >= required_votes and buy < required_votes:
        return -1
    if buy >= required_votes and sell < required_votes:
        return 1
    return 0
