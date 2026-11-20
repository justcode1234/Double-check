"""Deterministic B3 and H signals for the competition portfolio. Pure functions; no I/O, no broker access.

B3 (SPY Noise-Area, frozen B3: 14-session lookback, width 1.0, hourly cadence, long-only)
    At a decision time T (a completed 5-minute bar ending at T, 10:00..15:00 ET):
      noise  = mean |close_T / open_0930 - 1| over the 14 most recent prior sessions that have a bar ending at T
      upper  = max(today's 09:30 open, prior close - today's ex-dividend) x (1 + width x noise)
      vwap   = cumulative turnover / cumulative volume from 09:30 through T
      target = LONG if close_T > upper and close_T > vwap, else FLAT
    On 5-minute bars these inputs equal the 1-minute research inputs exactly (same open, same bar close at T,
    same cumulative turnover and volume).

H (ETF RSI-2 reversal panel, frozen: entry RSI2 <= 10, exit RSI2 >= 70, 3-session hold, 100-session trend)
    At a completed close: universe = SPY, QQQ, IWM, DIA + the 5 sector SPDRs with the highest 20-session median dollar
    volume. Entries (next open): not held, RSI2(adjusted) <= 10 and adjusted close > its 100-session mean, ranked by
    RSI2. Exits (next open): held and RSI2 >= 70, or held for 3 sessions (entry session counts as 1).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

import pandas as pd


@dataclass(frozen=True)
class Bar5:
    end: datetime          # bar END time, America/New_York
    open: float
    high: float
    low: float
    close: float
    volume: float
    turnover: float


@dataclass
class B3Decision:
    at: datetime
    target: int | None             # 1 long, 0 flat, None = blocked (missing/invalid data)
    reason: str
    metrics: dict = field(default_factory=dict)


def _finite(*xs):
    return all(x is not None and isinstance(x, (int, float)) and math.isfinite(x) for x in xs)


def b3_decision(today: list[Bar5], prior: dict[date, list[Bar5]], prev_close: float, dividend_today: float,
                at: datetime, session_open: datetime, lookback: int = 14, width: float = 1.0) -> B3Decision:
    bars = sorted((b for b in today if b.end <= at), key=lambda b: b.end)
    expected = int((at - session_open).total_seconds() // 300)
    if expected <= 0 or len(bars) != expected or bars[0].end != session_open + timedelta(minutes=5) or bars[-1].end != at:
        return B3Decision(at, None, "incomplete_bars", {"bars": len(bars), "expected": expected})
    if len({b.end for b in bars}) != len(bars):
        return B3Decision(at, None, "duplicate_bars")
    open0, close_t = bars[0].open, bars[-1].close
    vol = sum(b.volume for b in bars)
    turn = sum(b.turnover for b in bars)
    if not (_finite(open0, close_t, vol, turn, prev_close) and vol > 0 and open0 > 0 and prev_close > 0):
        return B3Decision(at, None, "invalid_inputs")
    vwap = turn / vol
    clock = at.timetz().replace(tzinfo=None) if at.tzinfo else at.time()
    moves = []
    for d in sorted(prior, reverse=True):
        rows = sorted(prior[d], key=lambda b: b.end)
        if not rows or d >= at.date():
            continue
        first = rows[0]
        hit = next((b for b in rows if b.end.time() == clock), None)
        if hit is None or not _finite(first.open, hit.close) or first.open <= 0:
            continue                                   # e.g. a 13:00 half-day has no 14:00 bar: skip it
        moves.append(abs(hit.close / first.open - 1))
        if len(moves) == lookback:
            break
    if len(moves) < lookback:
        return B3Decision(at, None, "insufficient_noise_history", {"sessions_with_slot": len(moves)})
    noise = sum(moves) / lookback * width
    anchor = prev_close - (dividend_today or 0.0)
    upper = max(open0, anchor) * (1 + noise)
    long = close_t > upper and close_t > vwap
    return B3Decision(at, 1 if long else 0, "above_band_and_vwap" if long else "inside_band_or_below_vwap",
                      {"open_0930": open0, "close": close_t, "vwap": vwap, "noise": noise, "upper": upper,
                       "anchor": anchor, "prev_close": prev_close, "dividend": dividend_today or 0.0})


def b3_decision_times(session_open: datetime, session_close: datetime, clock_times: list[str]) -> list[datetime]:
    """Decision ticks strictly before the last tradable minute (research rule: decision minute + 1 < flatten)."""
    out = []
    flatten = session_close - timedelta(minutes=5)
    for hhmm in clock_times:
        h, m = (int(x) for x in hhmm.split(":"))
        t = session_open.replace(hour=h, minute=m, second=0, microsecond=0)
        if session_open < t and t + timedelta(minutes=1) < flatten:
            out.append(t)
    return out


# ------------------------------------------------------------------ H
def rsi2(values: list[float]) -> list[float]:
    """Wilder RSI(2): seed with the mean of the first two changes, then alpha 1/2; NaN until seeded."""
    out = [math.nan] * len(values)
    g = l = None
    seed = []
    for i in range(1, len(values)):
        ch = values[i] - values[i - 1]
        if not math.isfinite(ch):
            g = l = None
            seed = []
            continue
        up, dn = max(ch, 0.0), max(-ch, 0.0)
        if g is None:
            seed.append((up, dn))
            if len(seed) < 2:
                continue
            g, l = sum(s[0] for s in seed) / 2, sum(s[1] for s in seed) / 2
        else:
            g, l = 0.5 * (g + up), 0.5 * (l + dn)
        out[i] = 100 * g / (g + l) if g + l > 0 else 50.0
    return out


@dataclass
class HPlan:
    asof: date
    universe: list[str]
    entries: list[str]                    # buy at the next open, in this order
    exits: dict[str, str]                 # symbol -> reason, sell at the next open
    metrics: dict
    blocked: str | None = None


def h_plan(daily: dict[str, pd.DataFrame], asof: date, held: dict[str, date], sessions: list[date], cfg: dict) -> HPlan:
    """daily[symbol]: DataFrame indexed by session date with columns close (raw), volume (raw), adjusted (adj close),
    complete through `asof`. held: symbol -> entry session. sessions: XNYS sessions up to asof."""
    core, sectors = list(cfg["core"]), list(cfg["sectors"])
    need = cfg["trend_sma_sessions"] + 5
    metrics, missing = {}, []
    for s in core + sectors:
        f = daily.get(s)
        if f is None or asof not in f.index or len(f.loc[:asof]) < need:
            missing.append(s)
            continue
        f = f.loc[:asof]
        adj = f.adjusted.astype(float).tolist()
        r = rsi2(adj)[-1]
        sma = sum(adj[-cfg["trend_sma_sessions"]:]) / cfg["trend_sma_sessions"]
        dv = (f.close * f.volume).iloc[-cfg["dollar_volume_sessions"]:]
        metrics[s] = {"rsi2": r, "adjusted": adj[-1], "sma": sma, "above_trend": adj[-1] > sma,
                      "median_dollar_volume": float(dv.median()) if len(dv) == cfg["dollar_volume_sessions"] else math.nan}
    if any(s in missing for s in core) or any(s in missing for s in held):
        return HPlan(asof, [], [], {}, metrics, blocked=f"missing daily data: {missing}")
    ranked = sorted((s for s in sectors if s in metrics and math.isfinite(metrics[s]["median_dollar_volume"])),
                    key=lambda s: (-metrics[s]["median_dollar_volume"], s))
    universe = core + ranked[:cfg["top_sectors"]]
    exits = {}
    pos = {d: i for i, d in enumerate(sessions)}
    for s, entry in held.items():
        up = metrics[s]["rsi2"] >= cfg["rsi_exit_min"]
        age = pos[asof] - pos[entry] + 1 if entry in pos and asof in pos else 10_000
        if up or age >= cfg["max_hold_sessions"]:
            exits[s] = "rsi_recovery" if up else "max_hold"
    entries = sorted((s for s in universe if s not in held and metrics[s]["rsi2"] <= cfg["rsi_entry_max"]
                      and metrics[s]["above_trend"]), key=lambda s: (metrics[s]["rsi2"], s))
    return HPlan(asof, universe, entries, exits, metrics)
