"""Noise-area momentum, VWAP trend continuation and ORB breakout/breakdown. Pure functions; no I/O.

Ported line for line from reports/claude_regime/rstrats.py (s10_noise, s2_vwap, s1_orb, walk, size, concurrency) and
rdata.py (cum_vwap, the 5-minute slot grid). Parity is checked by scripts/competition_parity_regime.py.

Slot grid: slot j covers [09:30 + 5j, 09:30 + 5(j+1)) and is usable only after it ends. Arrays have 78 slots; slots that
are missing or not completed yet are NaN. `upto` is the index of the last COMPLETED slot, so the live runner and the
full-day research replay run the same code: with upto = 77 the scan reproduces the research backtest, with a smaller
`upto` it returns the trades so far plus what to do at the next slot (`pending`).
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

SLOTS = 78
FR = 1.12e-4                    # research cost model: 1 bp slippage + 0.12 bp half-spread per side (replays only)


# ------------------------------------------------------------------ bars
def day_arrays(bars, session_open: datetime) -> dict[str, np.ndarray]:
    """list[Bar5] (bar END times) -> slot arrays. Bars not on the 5-minute grid are ignored, as in rdata._grid."""
    arr = {k: np.full(SLOTS, np.nan) for k in ("open", "high", "low", "close", "volume", "turnover")}
    for b in bars:
        off = (b.end - session_open).total_seconds() / 60
        if off < 5 or off % 5:
            continue
        j = int(off // 5) - 1
        if 0 <= j < SLOTS:
            for k in arr:
                arr[k][j] = float(getattr(b, k))
    return arr


def expected_slots(session_open: datetime, session_close: datetime) -> int:
    return int((session_close - session_open).total_seconds() // 300)


def last_completed_slot(session_open: datetime, now: datetime, n: int) -> int:
    return min(n - 1, int((now - session_open).total_seconds() // 300) - 1)


def slot_start(session_open: datetime, j: int) -> datetime:
    return session_open + timedelta(minutes=5 * j)


def cum_vwap(volume: np.ndarray, turnover: np.ndarray, close: np.ndarray) -> np.ndarray:
    vol = np.nancumsum(volume)
    num = np.nancumsum(turnover)
    with np.errstate(all="ignore"):
        out = num / vol
    out[np.isnan(close)] = np.nan
    return out


def atr14(daily: pd.DataFrame, day) -> float:
    """Mean true range of the 14 daily bars BEFORE `day` (rdata.features: rolling(14).mean().shift())."""
    d = daily.sort_index()
    h, l, c = d.high.astype(float), d.low.astype(float), d.close.astype(float)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1, skipna=False)
    prior = tr[[x < day for x in tr.index]]
    if len(prior) < 15:                                   # 14 true ranges need 15 closes
        return math.nan
    return float(prior.iloc[-14:].mean())


# ------------------------------------------------------------------ shared mechanics (rstrats.size / walk / concurrency)
def size(risk_frac, dist, price, cap_notional, prior_vol, equity, vol_frac=0.01) -> int:
    if not (dist > 0 and price > 0):
        return 0
    q = min(int(risk_frac * equity / dist), int(cap_notional / price))
    if math.isfinite(prior_vol):
        q = min(q, int(vol_frac * prior_vol))
    return max(q, 0)


def walk(o, h, l, c, j0, side, entry, stop, target, j_end, upto=SLOTS - 1, mode="fixed"):
    """rstrats.walk (no gap check on the entry slot) on completed slots <= upto. mode "half_trail" (ORB): half exits at
    the target, the rest gets a breakeven stop and exits at the next open after a slot closes beyond the previous slot's
    low (long) / high (short). Returns ([(fraction, price, reason)], exit_slot); exit_slot is None while still open at
    `upto`, and the fills so far (e.g. the target half) are returned with it."""
    fills, frac_left, trailing = [], 1.0, False
    for j in range(j0, j_end):
        if j > upto:
            return fills, None
        if not math.isfinite(o[j]):
            continue
        if j > j0:
            if side > 0 and o[j] <= stop:
                return fills + [(frac_left, o[j], "stop_gap")], j
            if side < 0 and o[j] >= stop:
                return fills + [(frac_left, o[j], "stop_gap")], j
        if (l[j] <= stop) if side > 0 else (h[j] >= stop):
            return fills + [(frac_left, stop, "stop")], j
        if not trailing and target is not None and ((h[j] >= target) if side > 0 else (l[j] <= target)):
            if mode == "half_trail":
                fills.append((0.5, target, "target_half"))
                frac_left, trailing, stop = 0.5, True, entry
            else:
                return fills + [(frac_left, target, "target")], j
        if trailing and j > j0 and j + 1 < j_end:
            broke = (c[j] < l[j - 1]) if side > 0 else (c[j] > h[j - 1])
            if broke and math.isfinite(o[j + 1]):
                if j + 1 > upto:
                    return fills, None                    # live: the trail exit is sent now, at the next slot's open
                return fills + [(frac_left, o[j + 1], "trail")], j + 1
    if upto < j_end:
        return fills, None                                # the scheduled flatten slot has not started yet
    j = j_end
    while j < len(o) and not math.isfinite(o[j]):
        j += 1
    px = o[j] if j < len(o) and math.isfinite(o[j]) else c[j_end - 1]
    return fills + [(frac_left, px, "time")], j_end


def concurrency(cands: list[dict], max_open: int) -> list[dict]:
    """rstrats.concurrency: first come, at most max_open open (a trade exiting on slot e is closed for an entry on e)."""
    out, open_ends = [], []
    for cand in sorted(cands, key=lambda x: x["entry_j"]):
        open_ends = [e for e in open_ends if e > cand["entry_j"]]
        if len(open_ends) >= max_open:
            continue
        open_ends.append(cand["exit_j"] if cand["exit_j"] is not None else 10 ** 6)
        out.append(cand)
    return out


def replay_pnl(side: int, qty: int, entry: float, fills) -> float:
    """rstrats.settle: research cost model, HYPOTHETICAL replay P&L (never a broker result). `fills` is a price or
    [(fraction, price, reason)]; partial quantities are round(qty x fraction), the last fill takes the rest."""
    if not isinstance(fills, (list, tuple)):
        fills = [(1.0, fills, "")]
    fee = lambda q, sell: 0.99 + (min(0.000166 * q, 8.30) if sell else 0.0)
    out, left = 0.0, qty
    efill = entry * (1 + side * FR)
    for k, (frac, px, _) in enumerate(fills):
        q = left if k == len(fills) - 1 else min(left, int(round(qty * frac)))
        if q <= 0:
            continue
        left -= q
        out += side * q * (px * (1 - side * FR) - efill) - fee(q, side < 0) - fee(q, side > 0)
    return out


# ------------------------------------------------------------------ VWAP trend continuation (rstrats.s2_vwap)
def s2_scan(a: dict, vw: np.ndarray, atr: float, n: int, p: dict, upto: int, equity: float, cap_notional: float):
    """VWAP trend continuation for one symbol-session. Returns (candidates, pending).
    candidates: trades in slot order, each {setup_j, entry_j, side, trigger, entry, stop, target, qty, exit_j, fills}
    (exit_j None = still open at `upto`). pending: a setup on slot `upto` whose stop trigger is armed for slot upto+1."""
    cands, pending = [], None
    if not atr > 0:
        return cands, pending
    o, h, l, c, v = a["open"], a["high"], a["low"], a["close"], a["volume"]
    j, flat_at, done = p["first_setup_slot"], n - p["flatten_slots_before_close"], 0
    while j < flat_at - 2 and done < p["max_trades_per_symbol"]:
        if j > upto:
            break
        side = 1 if c[j] > vw[j] else -1 if c[j] < vw[j] else 0
        rising = vw[j] - vw[j - 3]
        window = np.sign(c[max(0, j - 12):j + 1] - vw[max(0, j - 12):j + 1])
        crosses = int(np.sum(window[1:] != window[:-1]))
        trend_ok = side != 0 and np.sign(rising) == side and crosses < 3
        struct = (h[j] > h[j - 1] and l[j] > l[j - 1]) if side > 0 else (h[j] < h[j - 1] and l[j] < l[j - 1])
        near = ((l[j] - vw[j] <= p["pullback_atr"] * atr and c[j] > vw[j]) if side > 0 else
                (vw[j] - h[j] <= p["pullback_atr"] * atr and c[j] < vw[j]))
        if trend_ok and near and (struct or p["need_struct"] is False):
            trig = h[j] if side > 0 else l[j]
            stop = vw[j] - side * p["stop_atr"] * atr
            jn = j + 1
            if jn > upto:
                pending = {"setup_j": j, "entry_j": jn, "side": side, "trigger": float(trig), "stop": float(stop),
                           "setup_volume": float(v[j])}
                break
            touched = (h[jn] >= trig) if side > 0 else (l[jn] <= trig)
            if touched and math.isfinite(o[jn]):
                entry = max(o[jn], trig) if side > 0 else min(o[jn], trig)
                dist = side * (entry - stop)
                q = size(p["risk_fraction_of_nav"], dist, entry, cap_notional, v[j], equity, p["volume_fraction"])
                if q > 0 and dist > 0:
                    target = entry + side * p["target_r"] * dist
                    fills, xj = walk(o, h, l, c, jn, side, entry, stop, target, flat_at, upto)
                    cands.append({"setup_j": j, "entry_j": jn, "side": side, "trigger": float(trig), "entry": float(entry),
                                  "stop": float(stop), "target": float(target), "qty": q, "exit_j": xj, "fills": fills})
                    done += 1
                    if xj is None:
                        break
                    j = xj + 1
                    continue
        j += 1
    return cands, pending


def s2_entry(pending: dict, entry_open: float, p: dict, atr: float, equity: float, cap_notional: float):
    """Price a triggered VWAP setup: entry = max(slot open, trigger) for longs (min for shorts), research sizing."""
    side, trig, stop = pending["side"], pending["trigger"], pending["stop"]
    entry = max(entry_open, trig) if side > 0 else min(entry_open, trig)
    dist = side * (entry - stop)
    q = size(p["risk_fraction_of_nav"], dist, entry, cap_notional, pending["setup_volume"], equity, p["volume_fraction"])
    if not (q > 0 and dist > 0):
        return None
    return {"entry": float(entry), "stop": float(stop), "target": float(entry + side * p["target_r"] * dist), "qty": q}


# ------------------------------------------------------------------ ORB breakout/breakdown (rstrats.s1_orb, universe etf)
def orb_rvol(first_volume: float, prior_first_volumes: list[float], min_valid: int = 15) -> float:
    """First-range volume vs the mean of the prior 20 sessions' first-range volume (rolling(20, min_periods=15)):
    sessions with any missing first-range bar count as missing."""
    vals = [x for x in prior_first_volumes if math.isfinite(x)]
    if len(vals) < min_valid or not math.isfinite(first_volume):
        return math.nan
    base = sum(vals) / len(vals)
    return first_volume / base if base else math.nan


def first_range_volume(volume: np.ndarray, k: int) -> float:
    v = volume[:k]
    return float(np.nansum(v)) if not np.isnan(v).any() else math.nan


def orb_scan(a: dict, vw: np.ndarray, atr: float, rvol: float, n: int, p: dict, upto: int, equity: float,
             cap_notional: float):
    """ORB breakout/breakdown for one symbol-session. Returns (trade, pending, skip).
    trade: {entry_j, side, entry, stop, target, qty, dist, exit_j, fills}; pending: the breakout closed on slot `upto`
    and the entry is the open of slot upto+1 (now); skip: why the symbol is out for the day (None if still live)."""
    k = p["range_minutes"] // 5
    o, h, l, c, v = a["open"], a["high"], a["low"], a["close"], a["volume"]
    if upto < k - 1:
        return None, None, None
    if n < k + 8 or np.isnan(h[:k]).any():
        return None, None, "incomplete opening range"
    orh, orl = float(np.max(h[:k])), float(np.min(l[:k]))
    if not (atr > 0) or orh - orl > p["max_range_atr"] * atr or o[0] < 5:
        return None, None, "range wider than ATR limit" if atr > 0 else "no ATR"
    if not (rvol >= p["rvol"]):
        return None, None, "opening volume below threshold"
    longs_ok, shorts_ok = p["dir"] in ("both", "long"), p["dir"] in ("both", "short")
    hit = None
    for j in range(k, min(upto, n - 7) + 1):
        if longs_ok and c[j] > orh and c[j] > vw[j]:
            hit = (j, 1)
            break
        if shorts_ok and c[j] < orl and c[j] < vw[j]:
            hit = (j, -1)
            break
    if hit is None:
        return None, None, ("no breakout" if upto >= n - 7 else None)
    j, side = hit
    je = j + 1
    if je > upto:
        return None, {"entry_j": je, "side": side, "breakout_low": float(l[j]), "breakout_high": float(h[j]),
                      "volume": float(v[j]), "orh": orh, "orl": orl}, None
    if not math.isfinite(o[je]):
        return None, None, "no entry price"
    t = orb_entry({"side": side, "breakout_low": float(l[j]), "breakout_high": float(h[j]), "volume": float(v[j])},
                  float(o[je]), p, atr, equity, cap_notional)
    if t is None:
        return None, None, "size zero"
    fills, xj = walk(o, h, l, c, je, side, t["entry"], t["stop"], t["target"], n - 1, upto, "half_trail")
    return {**t, "entry_j": je, "side": side, "exit_j": xj, "fills": fills}, None, None


def orb_entry(pending: dict, entry: float, p: dict, atr: float, equity: float, cap_notional: float):
    side = pending["side"]
    candle = entry - pending["breakout_low"] if side > 0 else pending["breakout_high"] - entry
    dist = min(candle, p["stop_atr"] * atr) if candle > 0 else p["stop_atr"] * atr
    q = size(p["risk_fraction_of_nav"], dist, entry, cap_notional, pending["volume"], equity, p["volume_fraction"])
    if q <= 0:
        return None
    return {"entry": float(entry), "stop": float(entry - side * dist), "target": float(entry + side * p["target_r"] * dist),
            "dist": float(dist), "qty": q}


# ------------------------------------------------------------------ Noise-area momentum (rstrats.s10_noise)
def noise_decision(today, prior: dict, prev_close: float, at: datetime, session_open: datetime, p: dict) -> dict:
    """Target at the completed 5-minute bar ending `at`: +1 long if close > upper and > VWAP, -1 short if close < lower
    and < VWAP (shorts enabled), else 0; None if inputs are missing (fail closed). Same inputs as b3_decision (complete
    bars, 14 prior sessions that have this slot), but no dividend adjustment and with the lower band, as s10_noise."""
    from dual_strategy.competition.signals import b3_decision
    d = b3_decision(today, prior, prev_close, 0.0, at, session_open, p["lookback_sessions"], p["band_width"])
    if d.target is None:
        return {"at": at, "target": None, "reason": d.reason, "metrics": d.metrics}
    m = d.metrics
    open0, close_t, vwap, noise = m["open_0930"], m["close"], m["vwap"], m["noise"]
    upper = max(open0, prev_close) * (1 + noise)
    lower = min(open0, prev_close) * (1 - noise)
    if close_t > upper and close_t > vwap:
        target, why = 1, "above_upper_and_vwap"
    elif p.get("shorts") and close_t < lower and close_t < vwap:
        target, why = -1, "below_lower_and_vwap"
    else:
        target, why = 0, "inside_band_or_vwap_filter"
    return {"at": at, "target": target, "reason": why,
            "metrics": {"open_0930": open0, "close": close_t, "vwap": vwap, "noise": noise, "upper": upper, "lower": lower,
                        "prev_close": prev_close}}


def noise_decision_times(session_open: datetime, session_close: datetime, clock_times: list[str]) -> list[datetime]:
    """s10_noise acts at slot T only if the next minute is before the 15:55 flatten (half-days: 10:30, 11:30)."""
    out = []
    for hhmm in clock_times:
        hh, mm = (int(x) for x in hhmm.split(":"))
        t = session_open.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if session_open < t and t + timedelta(minutes=1) < session_close - timedelta(minutes=5):
            out.append(t)
    return out


def noise_qty(capital: float, price: float, open0: float) -> int:
    return int(capital / max(price, open0)) if price > 0 and open0 > 0 else 0
