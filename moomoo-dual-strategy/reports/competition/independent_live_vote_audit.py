from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "src"))

from dual_strategy.competition.market import Market  # noqa: E402
from dual_strategy.data.calendar import NY, prior_sessions, session_bounds  # noqa: E402

CFG = yaml.safe_load((PROJECT / "config" / "competition.yaml").read_text(encoding="utf-8"))
SYMBOLS = ("US.SPY", "US.QQQ")
FIRST_ENTRY = 6
REQUIRED_VOTES = int(CFG["two_vote"]["required_votes"])


def ema(values, span):
    return values.ewm(span=span, adjust=False, min_periods=span).mean()


def wilder(values, period):
    clean = values.dropna()
    smoothed = clean.iloc[period - 1 :].copy()
    if not smoothed.empty:
        smoothed.iloc[0] = clean.iloc[:period].mean()
    return smoothed.ewm(alpha=1 / period, adjust=False).mean().reindex(values.index)


def rsi2(values):
    out = [math.nan] * len(values)
    gain = loss = None
    seed = []
    for i in range(1, len(values)):
        change = values[i] - values[i - 1]
        if not math.isfinite(change):
            gain = loss = None
            seed = []
            continue
        up, down = max(change, 0.0), max(-change, 0.0)
        if gain is None:
            seed.append((up, down))
            if len(seed) < 2:
                continue
            gain = sum(item[0] for item in seed) / 2
            loss = sum(item[1] for item in seed) / 2
        else:
            gain, loss = 0.5 * (gain + up), 0.5 * (loss + down)
        out[i] = 100 * gain / (gain + loss) if gain + loss > 0 else 50.0
    return out


def below(values, level):
    delta = values - level
    return (delta.shift() >= 0) & (delta < 0)


def above(values, level):
    return below(-values, -level)


def indicator_masks(frame):
    high, low, close = frame.high, frame.low, frame.close
    prior = close.shift()
    true_range = pd.concat([high - low, (high - prior).abs(), (low - prior).abs()], axis=1).max(axis=1)
    up_move, down_move = high.diff(), -low.diff()
    plus = wilder(up_move.where((up_move > down_move) & (up_move > 0), 0), 14)
    minus = wilder(down_move.where((down_move > up_move) & (down_move > 0), 0), 14)
    dm_sum = plus + minus
    adx = wilder((100 * (plus - minus).abs() / dm_sum).where(dm_sum > 0, 0), 14)
    center = ema(close, 20)
    keltner_low = center - 2 * wilder(true_range, 10)
    rsi = pd.Series(rsi2(close.to_list()), index=close.index)
    macd = ema(close, 12) - ema(close, 26)
    macd_signal = ema(macd, 9)
    low14 = low.rolling(14).min()
    stochastic_k = 100 * (close - low14) / (high.rolling(14).max() - low14)
    stochastic_d = stochastic_k.rolling(3).mean()
    atr22 = wilder(true_range, 22)
    chandelier_long = (high.rolling(22).max() - 3 * atr22).shift()
    chandelier_short = (low.rolling(22).min() + 3 * atr22).shift()
    return {
        "Donchian": (below(close, low.rolling(20).min().shift()), close > high.rolling(10).max().shift()),
        "Keltner": (below(close, keltner_low), close > center),
        "RSI(2)": ((rsi > 90) & (close < close.rolling(200).mean()), close < close.rolling(5).mean()),
        "MACD": (below(macd, macd_signal) & (macd < 0), above(macd, macd_signal)),
        "DMI/ADX": (above(minus, plus) & (adx >= 25), plus > minus),
        "Stochastic": (below(stochastic_k, stochastic_d) & (stochastic_k.shift() >= 80), stochastic_k <= 20),
        "Chandelier": (below(close, chandelier_long), close > chandelier_short),
    }


def last_entry(slots):
    return min(60, slots - 6)


def first_window(entry_mask, exit_mask, slots):
    start, stop = FIRST_ENTRY - 1, last_entry(slots)
    entries = np.flatnonzero(entry_mask[start:stop])
    if not len(entries):
        return None
    entry = start + int(entries[0]) + 1
    flat = slots - 1
    exits = np.flatnonzero(exit_mask[entry : flat - 1])
    return entry, entry + int(exits[0]) + 1 if len(exits) else flat


def turtle_soup_window(frame, section, slots):
    start, stop = FIRST_ENTRY - 1, last_entry(slots)
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
        entry_mask = np.zeros(slots, dtype=bool)
        entry_mask[j] = True
        return first_window(entry_mask, exit_mask, slots)
    return None


def session_windows(frame, section, slots, masks, nr7):
    windows = {}
    for name, (enter, exit_mask) in masks.items():
        window = first_window(enter[section], exit_mask[section], slots)
        if window:
            windows[name] = window
    window = turtle_soup_window(frame, section, slots)
    if window:
        windows["Turtle Soup"] = window
    if nr7 and section.stop - section.start >= 3:
        day = frame.iloc[section]
        entry = day.close.to_numpy() < day.low.iloc[:3].min()
        exit_mask = day.close.to_numpy() >= day.high.iloc[:3].max()
        window = first_window(entry, exit_mask, slots)
        if window:
            windows["NR7"] = window
    return windows


def technical_votes(history, today, slots, slot):
    previous = [bar for batch in history for bar in batch]
    frame = pd.DataFrame(asdict(bar) for bar in previous + today)
    section = slice(len(previous), len(frame))
    ranges = [max(bar.high for bar in day) - min(bar.low for bar in day) for day in history[-7:]]
    nr7 = len(ranges) == 7 and ranges[-1] < min(ranges[:-1])
    windows = []
    for data in (frame, frame.assign(open=-frame.open, close=-frame.close, high=-frame.low, low=-frame.high)):
        masks = {name: (enter.to_numpy(dtype=bool), exit_mask.to_numpy(dtype=bool))
                 for name, (enter, exit_mask) in indicator_masks(data).items()}
        windows.append(session_windows(data, section, slots, masks, nr7))
    short, long = windows

    def active(signals, name):
        return name in signals and signals[name][0] <= slot < signals[name][1]

    return {name: int(active(long, name)) - int(active(short, name))
            for name in (*masks, "Turtle Soup", "NR7")}


def noise_decision_times(opened, closed, clock_times):
    out = []
    for hhmm in clock_times:
        hour, minute = (int(part) for part in hhmm.split(":"))
        tick = opened.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if opened < tick and tick + timedelta(minutes=1) < closed - timedelta(minutes=5):
            out.append(tick)
    return out


def noise_target(today, prior, previous_close, at, opened, config):
    bars = sorted((bar for bar in today if bar.end <= at), key=lambda bar: bar.end)
    expected = int((at - opened).total_seconds() // 300)
    if expected <= 0 or len(bars) != expected or bars[0].end != opened + timedelta(minutes=5) or bars[-1].end != at:
        raise ValueError("incomplete noise bars")
    open0, close_t = bars[0].open, bars[-1].close
    volume = sum(bar.volume for bar in bars)
    turnover = sum(bar.turnover for bar in bars)
    if not all(math.isfinite(value) for value in (open0, close_t, volume, turnover, previous_close)):
        raise ValueError("invalid noise inputs")
    if volume <= 0 or open0 <= 0 or previous_close <= 0:
        raise ValueError("invalid noise inputs")
    vwap = turnover / volume
    clock = at.timetz().replace(tzinfo=None)
    moves = []
    for day in sorted(prior, reverse=True):
        rows = sorted(prior[day], key=lambda bar: bar.end)
        if not rows or day >= at.date():
            continue
        hit = next((bar for bar in rows if bar.end.time() == clock), None)
        if hit is None or not math.isfinite(rows[0].open) or rows[0].open <= 0:
            continue
        moves.append(abs(hit.close / rows[0].open - 1))
        if len(moves) == int(config["lookback_sessions"]):
            break
    if len(moves) < int(config["lookback_sessions"]):
        raise ValueError("insufficient noise history")
    noise = sum(moves) / len(moves) * float(config["band_width"])
    upper = max(open0, previous_close) * (1 + noise)
    lower = min(open0, previous_close) * (1 - noise)
    if close_t > upper and close_t > vwap:
        return 1
    if config.get("shorts") and close_t < lower and close_t < vwap:
        return -1
    return 0


def supported_side(sell, buy):
    if sell >= REQUIRED_VOTES and buy < REQUIRED_VOTES:
        return -1
    if buy >= REQUIRED_VOTES and sell < REQUIRED_VOTES:
        return 1
    return 0


def recorded_votes(audit_dir):
    out = {}
    for path in sorted(audit_dir.glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        if record.get("kind") == "vote_step" and record.get("mode") == "EXECUTE":
            out[int(record["slot"])] = record
    return out


def bars_fingerprint(history, today):
    digest = hashlib.sha256()
    for day in [*history, today]:
        for bar in day:
            digest.update(json.dumps(asdict(bar), default=str, sort_keys=True).encode())
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sessions", nargs="+", default=["2026-11-23", "2026-11-24"])
    parser.add_argument("--output-dir", type=Path, default=PROJECT / "reports" / "competition" / "submission_assets")
    parser.add_argument("--compare-live-implementation", action="store_true")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    market = Market()
    live_indicator_votes = live_noise_decision = live_noise_decision_times = None
    if args.compare_live_implementation:
        from dual_strategy.competition.intraday import noise_decision as live_noise_decision
        from dual_strategy.competition.intraday import noise_decision_times as live_noise_decision_times
        from dual_strategy.competition.vote_live import indicator_votes as live_indicator_votes
    report = {
        "schema_version": 1,
        "generated_at": datetime.now(NY).isoformat(),
        "method": "Independent rule implementation; shared read-only Moomoo market transport; optional identical-input parity check against live signal code.",
        "required_votes": REQUIRED_VOTES,
        "sessions": [],
        "total_bar_checks": 0,
        "total_rule_checks": 0,
        "total_mismatches": 0,
    }
    if args.compare_live_implementation:
        report["implementation_parity"] = {"checks": 0, "mismatches": 0, "first_mismatches": []}
    rows = []
    for session in args.sessions:
        day = datetime.strptime(session, "%Y-%m-%d").date()
        opened, closed = session_bounds(day)
        slots = int((closed - opened).total_seconds() // 300)
        preceding = prior_sessions(day, 20)
        prior_close = float(market.daily("US.SPY", preceding[-1], preceding[-1], 0).at[preceding[-1], "close"])
        audit = recorded_votes(PROJECT / "private" / "competition" / "live" / "audit" / session)
        session_result = {
            "session": session,
            "recorded_bars": len(audit),
            "bar_checks": 0,
            "rule_checks": 0,
            "mismatches": 0,
            "symbols": {},
        }
        for symbol in SYMBOLS:
            prior, today = market.b3_inputs(symbol, day, sessions_needed=20, replay=True)
            history = [prior[d] for d in preceding if d in prior]
            if len(history) != 20:
                raise ValueError(f"{symbol} {session}: expected 20 prior sessions, got {len(history)}")
            today = sorted(today, key=lambda bar: bar.end)
            symbol_result = {"bar_checks": 0, "rule_checks": 0, "mismatches": 0, "input_bars_sha256": bars_fingerprint(history, today)}
            for slot, record in sorted(audit.items()):
                bar_end = datetime.fromisoformat(record["bar_end"])
                today_upto = [bar for bar in today if bar.end <= bar_end]
                votes = technical_votes(history, today_upto, slots, slot)
                if symbol == "US.SPY":
                    target = 0
                    for tick in noise_decision_times(opened, closed, CFG["noise"]["decision_times"]):
                        if tick > bar_end:
                            break
                        target = noise_target(today_upto, prior, prior_close, tick, opened, CFG["noise"])
                    votes["Noise-area"] = target if slot < slots - 1 else 0
                if args.compare_live_implementation:
                    live_votes = live_indicator_votes(history, today_upto, slots, slot)
                    if symbol == "US.SPY":
                        live_target = 0
                        for tick in live_noise_decision_times(opened, closed, CFG["noise"]["decision_times"]):
                            if tick > bar_end:
                                break
                            decision = live_noise_decision(today_upto, prior, prior_close, tick, opened, CFG["noise"])
                            if decision["target"] is None:
                                raise ValueError("live noise replay blocked")
                            live_target = decision["target"]
                        live_votes["Noise-area"] = live_target if slot < slots - 1 else 0
                    parity = report["implementation_parity"]
                    parity["checks"] += 1
                    if votes != live_votes:
                        parity["mismatches"] += 1
                        if len(parity["first_mismatches"]) < 20:
                            parity["first_mismatches"].append({
                                "session": session,
                                "slot": slot,
                                "symbol": symbol,
                                "independent": votes,
                                "live": live_votes,
                            })
                expected = {
                    "buy": sum(value > 0 for value in votes.values()),
                    "sell": sum(value < 0 for value in votes.values()),
                    "rules": votes,
                    "entry_window": slot <= last_entry(slots),
                    "signal": supported_side(sum(value < 0 for value in votes.values()), sum(value > 0 for value in votes.values())),
                }
                actual = record["votes"][symbol]
                mismatched = [key for key in expected if expected[key] != actual.get(key)]
                match = not mismatched
                row = {
                    "session": session,
                    "slot": slot,
                    "bar_end": record["bar_end"],
                    "symbol": symbol,
                    "match": match,
                    "mismatched_fields": ";".join(mismatched),
                    "expected_json": json.dumps(expected, sort_keys=True),
                    "recorded_json": json.dumps(actual, sort_keys=True),
                }
                rows.append(row)
                session_result["bar_checks"] += 1
                session_result["rule_checks"] += len(votes)
                symbol_result["bar_checks"] += 1
                symbol_result["rule_checks"] += len(votes)
                if not match:
                    session_result["mismatches"] += 1
                    symbol_result["mismatches"] += 1
                    report.setdefault("first_mismatches", []).append(row)
            session_result["symbols"][symbol] = symbol_result
        report["sessions"].append(session_result)
        report["total_bar_checks"] += session_result["bar_checks"]
        report["total_rule_checks"] += session_result["rule_checks"]
        report["total_mismatches"] += session_result["mismatches"]
    report["recorded_replay_status"] = "PASS" if report["total_mismatches"] == 0 else "FAIL"
    if args.compare_live_implementation:
        parity = report["implementation_parity"]
        parity["status"] = "PASS" if parity["mismatches"] == 0 else "FAIL"
        report["status"] = "FAIL" if parity["status"] == "FAIL" else ("PASS" if report["total_mismatches"] == 0 else "PASS_WITH_DATA_REVISIONS")
    else:
        report["status"] = report["recorded_replay_status"]
    report["source_script_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    (args.output_dir / "independent_live_vote_audit.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    with (args.output_dir / "independent_live_vote_audit.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps({key: value for key, value in report.items() if key != "first_mismatches"}, indent=2))
    return 0 if report["status"] != "FAIL" else 1


if __name__ == "__main__":
    raise SystemExit(main())
