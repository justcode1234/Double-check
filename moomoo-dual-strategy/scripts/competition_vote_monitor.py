"""Publish current SPY/QQQ votes from completed Moomoo market bars. Quote access only."""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile

import pandas as pd

from competition_vote_replay import (
    CFG, DEFAULT_VOTES, I, SYMBOLS, indicator_rules, last_entry, session_windows, supported_side,
)
from dual_strategy.competition.market import Market, MarketDataError
from dual_strategy.data.calendar import NY, prior_sessions, session_bounds


def regular_bars(rows, opened, through):
    step = timedelta(minutes=5)
    ends = [opened + step * j for j in range(1, (through - opened) // step + 1)]
    by_end = {row.end: row for row in rows}
    if any(end not in by_end for end in ends):
        raise MarketDataError("Incomplete completed bars")
    selected = [by_end[end] for end in ends]
    if any(not all(math.isfinite(x) for x in (b.open, b.high, b.low, b.close, b.volume, b.turnover))
           or min(b.open, b.high, b.low, b.close) <= 0 or b.volume < 0 for b in selected):
        raise MarketDataError("Invalid completed bars")
    return selected


def indicator_votes(history, bars, n, slot):
    previous = [row for batch in history for row in batch]
    frame = pd.DataFrame(asdict(b) for b in previous + bars)
    part = slice(len(previous), len(frame))
    ranges = [max(b.high for b in day) - min(b.low for b in day) for day in history[-7:]]
    nr7 = ranges[-1] < min(ranges[:-1])
    windows = []
    for data in (frame, frame.assign(open=-frame.open, close=-frame.close, high=-frame.low, low=-frame.high)):
        masks = {name: (enter.to_numpy(dtype=bool), exit.to_numpy(dtype=bool))
                 for name, (enter, exit) in indicator_rules(data).items()}
        windows.append(session_windows(data, part, n, masks, nr7))
    short, long = windows
    active = lambda signals, name: name in signals and signals[name][0] <= slot < signals[name][1]
    return {name: int(active(long, name)) - int(active(short, name))
            for name in (*masks, "Turtle Soup", "NR7")}


class Monitor:
    def __init__(self, market):
        self.market = market
        self.history_day = None
        self.last = None

    def prepare(self, day):
        preceding = prior_sessions(day, 20)
        history = {}
        for symbol in SYMBOLS:
            quotes = []
            for k in range(0, len(preceding), 4):
                chunk = preceding[k:k + 4]
                quotes.extend(self.market.five_minute(symbol, chunk[0], chunk[-1]))
            history[symbol] = {d: regular_bars(quotes, *session_bounds(d)) for d in preceding}
        prior = preceding[-1]
        self.previous_close = float(self.market.daily("US.SPY", prior, prior, 0).at[prior, "close"])
        if not math.isfinite(self.previous_close) or self.previous_close <= 0:
            raise MarketDataError("Missing previous SPY close")
        self.history = history
        self.history_day = day

    def observe(self, now):
        day = now.astimezone(NY).date()
        base = {"schema_version": 1, "observed_at": now.isoformat(), "symbols": SYMBOLS,
                "required_votes": DEFAULT_VOTES, "entry_usd": 10_000, "max_open_positions": len(SYMBOLS)}
        bounds = session_bounds(day)
        if not bounds or now >= bounds[1]:
            return {**base, "status": "closed"}
        opened, closed = bounds
        if now < opened:
            return {**base, "status": "preopen"}
        slot = int((now - timedelta(seconds=15) - opened).total_seconds() // 300)
        if slot < 1:
            return {**base, "status": "waiting"}
        end = opened + timedelta(minutes=5 * slot)
        if self.last and self.last["bar_end"] == end.isoformat():
            return {**self.last, **base}
        if self.history_day != day:
            self.prepare(day)
        n = I.expected_slots(opened, closed)
        per_symbol = {}
        for symbol in SYMBOLS:
            today = regular_bars(self.market.five_minute_today(symbol, day), opened, end)
            votes = indicator_votes(list(self.history[symbol].values()), today, n, slot)
            if symbol == "US.SPY":
                config = CFG["noise"]
                target = 0
                for tick in I.noise_decision_times(opened, closed, config["decision_times"]):
                    if tick > end:
                        break
                    decision = I.noise_decision(today, self.history[symbol], self.previous_close, tick, opened, config)
                    if decision["target"] is None:
                        raise MarketDataError("Incomplete SPY decision inputs")
                    target = decision["target"]
                votes["Noise-area"] = target if slot < n - 1 else 0
            buy = sum(side > 0 for side in votes.values())
            sell = sum(side < 0 for side in votes.values())
            per_symbol[symbol] = {"buy": buy, "sell": sell, "rules": votes, "entry_window": slot <= last_entry(n),
                                  "signal": supported_side(sell, buy, DEFAULT_VOTES)}
        self.last = {**base, "status": "current", "bar_end": end.isoformat(), "tickers": per_symbol}
        return self.last


def publish(path, report):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False)
    temp = Path(handle.name)
    try:
        with handle:
            json.dump(report, handle, allow_nan=False)
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    monitor = Monitor(Market())
    while True:
        now = datetime.now(timezone.utc)
        try:
            status = monitor.observe(now)
        except (MarketDataError, subprocess.TimeoutExpired, KeyError, TypeError, ValueError, OSError):
            status = {"schema_version": 1, "status": "unavailable", "observed_at": now.isoformat()}
        publish(args.output, status)
        time.sleep(10)


if __name__ == "__main__":
    main()
