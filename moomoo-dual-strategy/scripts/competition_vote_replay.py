"""Replay a two-vote SPY/QQQ strategy on cached research bars.

Run from moomoo-dual-strategy:
    .venv/Scripts/python.exe scripts/competition_vote_replay.py

The nine technical rules vote on each symbol independently. Noise-area is
SPY-only. Each technical rule votes from its first eligible setup per
symbol and direction until its own exit. The long/short replay mirrors
those rules to generate bullish votes. All fills use bar opens and the
existing research cost model. The default exit waits for opposite
agreement or the scheduled flatten. SPY and QQQ may be open concurrently,
with one position per ticker and a shared $100,000 exposure budget.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile

import numpy as np
import pandas as pd

from competition_parity_regime import CFG, I, R, RS, spy_decision_bars
from dual_strategy.competition.signals import rsi2

SYMBOLS = ("US.SPY", "US.QQQ")
DEFAULT_VOTES = 2
FIRST_ENTRY = 6  # 10:00 ET
NORMAL_LAST_ENTRY = 60  # 14:30 ET
EARLIER = ("2025-01-01", "2026-11-06")
RECENT = ("2026-11-09", "2026-11-20")


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


def technical_windows(bars, warm_days, score_days, mirror=False):
    sections, frames, ranges = {}, [], []
    offset = 0
    for i in warm_days:
        n = int(bars.expected[i])
        data = {name: getattr(bars, name)[i, :n] for name in ("open", "high", "low", "close")}
        if any(not np.isfinite(values).all() for values in data.values()):
            raise ValueError(f"Missing intraday bars on {R.DAYS[i]}")
        ranges.append(data["high"].max() - data["low"].min())
        if mirror:
            data = {"open": -data["open"], "close": -data["close"],
                    "high": -data["low"], "low": -data["high"]}
        sections[i] = slice(offset, offset + n)
        frames.append(pd.DataFrame(data))
        offset += n

    frame = pd.concat(frames, ignore_index=True)
    masks = {name: (enter.to_numpy(dtype=bool), exit.to_numpy(dtype=bool))
             for name, (enter, exit) in indicator_rules(frame).items()}
    score_days = set(score_days)
    result = {}
    for pos, i in enumerate(warm_days):
        if i not in score_days:
            continue
        section = sections[i]
        n = section.stop - section.start
        nr7 = pos >= 7 and ranges[pos - 1] < min(ranges[pos - 7 : pos - 1])
        result[i] = session_windows(frame, section, n, masks, nr7)
    return result


def noise_votes(days):
    """Hold the last valid target until the next decision or scheduled exit."""
    _, bars = spy_decision_bars()
    prev = R.features("US.SPY").prev_close.to_numpy()
    config = CFG["noise"]
    by_day = {}
    for i in days:
        opened, today = bars[i]
        minutes = int(R.MINUTES[i])
        closed = opened + timedelta(minutes=minutes)
        prior = {R.DAYS[k]: bars[k][1] for k in range(max(0, i - 40), i)}
        votes = np.zeros(minutes // 5, dtype=np.int8)
        for tick in I.noise_decision_times(opened, closed, config["decision_times"]):
            target = I.noise_decision(today, prior, float(prev[i]), tick, opened, config)["target"]
            if target is not None:
                slot = int((tick - opened).total_seconds() // 300)
                votes[slot:-1] = target
        by_day[i] = votes
    return by_day


def counts(i, bars, shorts, longs, noise):
    """A rule that has an active signal in both directions abstains."""
    n = int(bars.expected[i])
    sell, buy = np.zeros(n, dtype=np.int8), np.zeros(n, dtype=np.int8)
    for rule in shorts.keys() | longs.keys():
        vote = np.zeros(n, dtype=np.int8)
        if rule in shorts:
            start, stop = shorts[rule]
            vote[start:stop] -= 1
        if rule in longs:
            start, stop = longs[rule]
            vote[start:stop] += 1
        sell += vote < 0
        buy += vote > 0
    if noise is not None:
        sell += noise < 0
        buy += noise > 0
    return sell, buy


def supported_side(sell, buy, required_votes):
    if sell >= required_votes and buy < required_votes:
        return -1
    if buy >= required_votes and sell < required_votes:
        return 1
    return 0


@dataclass(frozen=True)
class Trade:
    day: date
    symbol: str
    side: int
    shares: int
    entry: float
    exit: float
    entry_slot: int
    exit_slot: int

    @property
    def pnl(self):
        return I.replay_pnl(self.side, self.shares, self.entry, self.exit)


@dataclass(frozen=True)
class Position:
    side: int
    shares: int
    entry: float
    slot: int


def replay_day(i, prices, votes, budget, required_votes, close_on_lost_support=False,
               max_positions=len(SYMBOLS)):
    n = int(R.MINUTES[i] // 5)
    positions = {}
    trades = []
    for j in range(FIRST_ENTRY, n):
        opens = {sym: float(bars.open[i, j]) for sym, bars in prices.items()}
        sides = {sym: supported_side(sell[j], buy[j], required_votes)
                 for sym, (sell, buy) in votes.items()}
        closed = set()
        for sym, pos in list(positions.items()):
            exit_signal = sides[sym] == -pos.side or (close_on_lost_support and sides[sym] != pos.side)
            if j == n - 1 or exit_signal:
                trades.append(Trade(R.DAYS[i], sym, pos.side, pos.shares, pos.entry, opens[sym], pos.slot, j))
                del positions[sym]
                closed.add(sym)
        if j > last_entry(n):
            continue
        eligible = [sym for sym in SYMBOLS if sides[sym] and sym not in positions and sym not in closed]
        eligible.sort(key=lambda sym: -int(votes[sym][0 if sides[sym] < 0 else 1][j]))
        gross = math.fsum(pos.shares * opens[sym] for sym, pos in positions.items())
        remaining = max(0.0, RS.EQUITY - gross)
        for sym in eligible:
            # A closing position frees a slot for the next bar.
            if len(positions) + len(closed) >= max_positions:
                break
            entry = opens[sym]
            shares = int(min(budget, remaining) / entry)
            if shares:
                positions[sym] = Position(sides[sym], shares, entry, j)
                remaining -= shares * entry
    return trades


def summarize(trades, dates):
    selected = [t for t in trades if t.day in dates]
    outcomes = [t.pnl for t in selected]
    open_by_bar = {}
    for t in selected:
        for slot in range(t.entry_slot, t.exit_slot):
            key = (t.day, slot)
            open_by_bar[key] = open_by_bar.get(key, 0) + 1
    return {
        "sessions": len(dates),
        "trades": len(outcomes),
        "long_entries": sum(t.side > 0 for t in selected),
        "short_entries": sum(t.side < 0 for t in selected),
        "active_sessions": len({t.day for t in selected}),
        "peak_open_positions": max(open_by_bar.values(), default=0),
        "winners": sum(p > 0 for p in outcomes),
        "net_pnl_usd": round(math.fsum(outcomes), 2),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--budget", type=float, default=10_000,
                        help="Maximum USD notional for a single position (default: 10000)")
    parser.add_argument("--votes", type=int, choices=range(1, 11), default=DEFAULT_VOTES)
    parser.add_argument("--max-positions", type=int, choices=range(1, len(SYMBOLS) + 1), default=len(SYMBOLS))
    parser.add_argument("--exit-rule", choices=("opposing-vote", "loss-of-support"), default="opposing-vote")
    parser.add_argument("--output", type=Path, help="Write the replay report to this JSON file")
    args = parser.parse_args()
    if not math.isfinite(args.budget) or not 0 < args.budget <= CFG["risk"]["name_cap_usd"]:
        parser.error("Budget must be positive and within the per-symbol cap")

    days = RS.in_window(EARLIER[0], RECENT[1])
    warm_days = RS.in_window("2024-12-02", RECENT[1])
    earlier = {R.DAYS[i] for i in RS.in_window(*EARLIER)}
    recent = {R.DAYS[i] for i in RS.in_window(*RECENT)}
    prices = {sym: R.bars5(sym) for sym in SYMBOLS}
    shorts = {sym: technical_windows(bars, warm_days, days) for sym, bars in prices.items()}
    longs = {sym: technical_windows(bars, warm_days, days, mirror=True) for sym, bars in prices.items()}
    noise = noise_votes(days)
    close_on_lost_support = args.exit_rule == "loss-of-support"
    out = {"schema_version": 1, "budget_usd": args.budget, "account_budget_usd": RS.EQUITY,
           "required_votes": args.votes, "max_open_positions": args.max_positions,
           "symbols": SYMBOLS, "exit_rule": args.exit_rule, "models": {}}
    for balanced in (False, True):
        trades = []
        recent_detail = []
        for i in days:
            vote_counts = {}
            for sym, bars in prices.items():
                noise_vote = noise[i] if sym == "US.SPY" else None
                if not balanced and noise_vote is not None:
                    noise_vote = np.minimum(noise_vote, 0)
                vote_counts[sym] = counts(i, bars, shorts[sym][i], longs[sym][i] if balanced else {}, noise_vote)
            filled = replay_day(i, prices, vote_counts, args.budget, args.votes,
                                close_on_lost_support, args.max_positions)
            trades.extend(filled)
            if R.DAYS[i] in recent:
                recent_detail.append({
                    "session": R.DAYS[i].isoformat(),
                    "max_sell_votes": max(int(sell.max()) for sell, _ in vote_counts.values()),
                    "max_buy_votes": max(int(buy.max()) for _, buy in vote_counts.values()),
                    "trades": len(filled),
                    "net_pnl_usd": round(math.fsum(t.pnl for t in filled), 2),
                })
        out["models"]["long_short" if balanced else "short_only"] = {
            "earlier": {"from": EARLIER[0], "through": EARLIER[1], **summarize(trades, earlier)},
            "recent": {"from": RECENT[0], "through": RECENT[1], **summarize(trades, recent)},
            "recent_sessions": recent_detail,
        }
    report = json.dumps(out, indent=2, allow_nan=False)
    if args.output is None:
        print(report)
    else:
        handle = NamedTemporaryFile(mode="w", encoding="utf-8", dir=args.output.parent, delete=False)
        temp = Path(handle.name)
        try:
            with handle:
                handle.write(report + "\n")
            temp.replace(args.output)
        finally:
            temp.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
