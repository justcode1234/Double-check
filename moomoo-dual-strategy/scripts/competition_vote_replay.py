"""Replay a two-vote SPY/QQQ strategy on cached research bars.

Run from moomoo-dual-strategy:
    .venv/Scripts/python.exe scripts/competition_vote_replay.py

The nine technical rules vote on each symbol independently. Noise-area is
SPY-only. Each technical rule votes from its first eligible setup per
symbol and direction until its own exit. The long/short replay mirrors
those rules to generate bullish votes. All fills use bar opens and the
existing research cost model. The default exit waits for opposite
agreement or the scheduled flatten. SPY and QQQ may be open concurrently,
with multiple lots per ticker and separate per-ticker exposure limits.
Positive realized net P&L is reserved outside the entry budget.
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
from dual_strategy.competition.vote_rules import (
    FIRST_ENTRY, SYMBOLS, indicator_rules, last_entry, session_windows, supported_side,
)

DEFAULT_VOTES = CFG["two_vote"]["required_votes"]
LOT_USD = CFG["two_vote"]["entry_usd"]
SYMBOL_CAP = CFG["risk"]["name_cap_usd"]
# Live null = no lot count, dollar cap only; the research replay models that as whole entry lots within the cap.
LOTS_PER_SYMBOL = CFG["two_vote"]["max_lots_per_symbol"] or max(1, int(SYMBOL_CAP // LOT_USD))
PORTFOLIO_CAP = len(SYMBOLS) * SYMBOL_CAP
BANK_GAINS = CFG["two_vote"]["bank_realized_gains"]
EARLIER = ("2025-01-01", "2026-11-06")
RECENT = ("2026-11-09", "2026-11-20")


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


@dataclass
class VoteCash:
    sizing_capital: float = PORTFOLIO_CAP
    reserved_profit: float = 0.0

    def settle(self, pnl: float) -> None:
        if BANK_GAINS:
            self.reserved_profit += max(0.0, pnl)
            self.sizing_capital += min(0.0, pnl)
        else:
            self.sizing_capital += pnl


def replay_day(i, prices, votes, budget, required_votes, close_on_lost_support=False,
               max_positions=LOTS_PER_SYMBOL * len(SYMBOLS), capital=None):
    n = int(R.MINUTES[i] // 5)
    capital = capital if capital is not None else VoteCash()
    positions: dict[str, list[Position]] = {}
    trades = []
    prior_sides = {}
    for j in range(FIRST_ENTRY, n):
        opens = {sym: float(bars.open[i, j]) for sym, bars in prices.items()}
        sides = {sym: supported_side(sell[j], buy[j], required_votes)
                 for sym, (sell, buy) in votes.items()}
        needed = positions.keys() | {sym for sym, side in sides.items() if side and j <= last_entry(n)}
        if any(not math.isfinite(opens[sym]) or opens[sym] <= 0 for sym in needed):
            raise ValueError(f"Missing or invalid two-vote fill price on {R.DAYS[i]}, slot {j}")
        closed = set()
        for sym, lots in list(positions.items()):
            side = lots[0].side
            exit_signal = sides[sym] == -side or (close_on_lost_support and sides[sym] != side)
            if j == n - 1 or exit_signal:
                for lot in lots:
                    trade = Trade(R.DAYS[i], sym, side, lot.shares, lot.entry, opens[sym], lot.slot, j)
                    trades.append(trade)
                    capital.settle(trade.pnl)
                del positions[sym]
                closed.add(sym)
        if j > last_entry(n):
            continue
        # Repeated bars from one agreement don't authorize another lot.
        eligible = [sym for sym in SYMBOLS if sides[sym] and sym not in closed
                    and len(positions.get(sym, ())) < LOTS_PER_SYMBOL
                    and (sym not in positions or sides[sym] != prior_sides.get(sym))]
        eligible.sort(key=lambda sym: -int(votes[sym][0 if sides[sym] < 0 else 1][j]))
        prior_sides = sides
        entry_prices = {sym: price * (1 + I.FR) for sym, price in opens.items()}
        gross_by_symbol = {sym: sum(lot.shares for lot in lots) * entry_prices[sym]
                           for sym, lots in positions.items()}
        remaining = min(PORTFOLIO_CAP, capital.sizing_capital) - math.fsum(gross_by_symbol.values())
        slots = max_positions - sum(map(len, positions.values()))
        for sym in eligible:
            if slots <= 0:
                break
            entry = opens[sym]
            sizing_price = entry_prices[sym]
            symbol_room = SYMBOL_CAP - gross_by_symbol.get(sym, 0.0)
            room = max(0.0, min(budget, LOT_USD, remaining, symbol_room))
            shares = math.floor(room / sizing_price)
            if shares * sizing_price > room:
                shares -= 1
            if shares:
                positions.setdefault(sym, []).append(Position(sides[sym], shares, entry, j))
                remaining -= shares * sizing_price
                slots -= 1
    return trades


def summarize(trades, dates):
    selected = [t for t in trades if t.day in dates]
    outcomes = [t.pnl for t in selected]
    open_by_bar = {}
    open_by_symbol_bar = {}
    for t in selected:
        for slot in range(t.entry_slot, t.exit_slot):
            key = (t.day, slot)
            open_by_bar[key] = open_by_bar.get(key, 0) + 1
            symbol_key = (t.symbol, *key)
            open_by_symbol_bar[symbol_key] = open_by_symbol_bar.get(symbol_key, 0) + 1
    return {
        "sessions": len(dates),
        "trades": len(outcomes),
        "long_entries": sum(t.side > 0 for t in selected),
        "short_entries": sum(t.side < 0 for t in selected),
        "active_sessions": len({t.day for t in selected}),
        "peak_open_positions": max(open_by_bar.values(), default=0),
        "peak_open_by_symbol": {sym: max((count for key, count in open_by_symbol_bar.items() if key[0] == sym),
                                        default=0) for sym in SYMBOLS},
        "winners": sum(p > 0 for p in outcomes),
        "net_pnl_usd": round(math.fsum(outcomes), 2),
        "reserved_profit_usd": round(math.fsum(max(p, 0) for p in outcomes), 2) if BANK_GAINS else 0,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--budget", type=float, default=LOT_USD,
                        help="Maximum USD notional for a single position (default: 10000)")
    parser.add_argument("--votes", type=int, choices=range(1, 11), default=DEFAULT_VOTES)
    max_lots = len(SYMBOLS) * LOTS_PER_SYMBOL
    parser.add_argument("--max-positions", type=int, choices=range(1, max_lots + 1), default=max_lots)
    parser.add_argument("--exit-rule", choices=("opposing-vote", "loss-of-support"), default="opposing-vote")
    parser.add_argument("--output", type=Path, help="Write the replay report to this JSON file")
    args = parser.parse_args()
    if not math.isfinite(args.budget) or not 0 < args.budget <= min(LOT_USD, SYMBOL_CAP):
        parser.error("Budget must be positive and within the per-lot and per-symbol caps")

    days = RS.in_window(EARLIER[0], RECENT[1])
    warm_days = RS.in_window("2024-12-02", RECENT[1])
    earlier = {R.DAYS[i] for i in RS.in_window(*EARLIER)}
    recent = {R.DAYS[i] for i in RS.in_window(*RECENT)}
    prices = {sym: R.bars5(sym) for sym in SYMBOLS}
    shorts = {sym: technical_windows(bars, warm_days, days) for sym, bars in prices.items()}
    longs = {sym: technical_windows(bars, warm_days, days, mirror=True) for sym, bars in prices.items()}
    noise = noise_votes(days)
    close_on_lost_support = args.exit_rule == "loss-of-support"
    out = {"schema_version": 1, "budget_usd": args.budget, "account_budget_usd": PORTFOLIO_CAP,
           "per_symbol_cap_usd": SYMBOL_CAP, "max_lots_per_symbol": LOTS_PER_SYMBOL, "bank_realized_gains": BANK_GAINS,
           "required_votes": args.votes, "max_open_positions": args.max_positions,
           "symbols": SYMBOLS, "exit_rule": args.exit_rule, "models": {}}
    for balanced in (False, True):
        capital = VoteCash()
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
                                close_on_lost_support, args.max_positions, capital)
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
            "ending_sizing_capital_usd": round(capital.sizing_capital, 2),
            "ending_reserved_profit_usd": round(capital.reserved_profit, 2),
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
