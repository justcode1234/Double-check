"""The ten current-regime strategies from deep-research-report (8).md, implemented on local bars.

Every strategy is an independent USD 100,000 book, NOT compounded (risk and notional caps use a fixed $100,000), so
weekly P&L reads directly in dollars. Fills: next bar open (or the stop/target price when touched; if both are touched
in one bar the stop is assumed first). Costs per side: 1 bp slippage + 0.12 bp half-spread; $0.99 per order plus
$0.000166/share on sells (cap $8.30). Capacity: at most 1% of the prior bar's volume.
Data deviations from the report (no data available): 5-minute (not 1-minute) triggers; no pre-market volume, news
catalysts, spreads or breadth; sector ETFs on 60-minute bars; ETF pairs on daily bars; stocks are the 40-name research
universe chosen in 2026 (survivorship bias).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

import rdata as R

EQUITY = 100_000.0
FR = 1.12e-4


def fee(q, sell):
    return 0.99 + (min(0.000166 * q, 8.30) if sell else 0.0)


@dataclass
class Trade:
    sym: str
    day: object          # exit day
    entry_day: object
    side: int
    qty: int
    entry: float
    exit: float
    pnl: float
    reason: str


def settle(sym, entry_day, side, qty, entry_raw, fills):
    """fills: [(fraction, raw_price, day, reason)] -> Trade list (one per fill)."""
    out, left = [], qty
    efill = entry_raw * (1 + side * FR)
    for k, (frac, px, day, why) in enumerate(fills):
        q = left if k == len(fills) - 1 else min(left, int(round(qty * frac)))
        if q <= 0:
            continue
        left -= q
        xfill = px * (1 - side * FR)
        entry_fee = fee(q, side < 0)
        pnl = side * q * (xfill - efill) - entry_fee - fee(q, side > 0)
        out.append(Trade(sym, day, entry_day, side, q, entry_raw, px, pnl, why))
    return out


def walk(b, i, j0, side, entry, stop, target, j_end, mode="fixed", gap_first=False):
    """Intraday exit path from entry bar j0 (entered at its open). Returns ([(frac, px, reason)], exit_j)."""
    o, h, l, c = b.open[i], b.high[i], b.low[i], b.close[i]
    fills, frac_left, trailing = [], 1.0, False
    for j in range(j0, j_end):
        if not math.isfinite(o[j]):
            continue
        if j > j0 or gap_first:
            if side > 0 and o[j] <= stop:
                return fills + [(frac_left, o[j], "stop_gap")], j
            if side < 0 and o[j] >= stop:
                return fills + [(frac_left, o[j], "stop_gap")], j
        hit_stop = (l[j] <= stop) if side > 0 else (h[j] >= stop)
        if hit_stop:
            return fills + [(frac_left, stop, "stop")], j
        if not trailing and target is not None:
            hit_t = (h[j] >= target) if side > 0 else (l[j] <= target)
            if hit_t:
                if mode == "half_trail":
                    fills.append((0.5, target, "target_half"))
                    frac_left, trailing, stop = 0.5, True, entry      # remainder: breakeven stop + 5-min trail
                else:
                    return fills + [(frac_left, target, "target")], j
        if trailing and j > j0 and j + 1 < j_end:
            broke = (c[j] < l[j - 1]) if side > 0 else (c[j] > h[j - 1])
            if broke and math.isfinite(o[j + 1]):
                return fills + [(frac_left, o[j + 1], "trail")], j + 1
    j = j_end
    while j < len(o) and not math.isfinite(o[j]):
        j += 1
    px = o[j] if j < len(o) and math.isfinite(o[j]) else c[j_end - 1]
    return fills + [(frac_left, px, "time")], j_end


def size(risk_frac, dist, price, cap_frac, prior_vol):
    if not (dist > 0 and price > 0):
        return 0
    q = min(int(risk_frac * EQUITY / dist), int(cap_frac * EQUITY / price))
    if math.isfinite(prior_vol):
        q = min(q, int(0.01 * prior_vol))
    return max(q, 0)


class Sym:
    """Cached per-symbol 5-minute arrays and daily features."""
    _cache = {}

    def __new__(cls, sym):
        if sym not in cls._cache:
            obj = super().__new__(cls)
            obj.sym = sym
            obj.b = R.bars5(sym)
            obj.vwap = R.cum_vwap(obj.b)
            dsym = sym if R.daily(sym) is not None else None
            f = R.features(dsym) if dsym else None
            obj.atr = f.atr14.to_numpy() if f is not None else np.full(len(R.DAYS), np.nan)
            obj.prev = f.prev_close.to_numpy() if f is not None else np.full(len(R.DAYS), np.nan)
            obj.dvol = f.dvol20.to_numpy() if f is not None else np.full(len(R.DAYS), np.nan)
            obj.f = f
            obj._rv = {}
            cls._cache[sym] = obj
        return cls._cache[sym]

    def rvol(self, k):
        """volume of the first k bars vs the mean of the prior 20 sessions' first k bars."""
        if k not in self._rv:
            v = np.nansum(self.b.volume[:, :k], axis=1)
            v[np.isnan(self.b.volume[:, :k]).any(axis=1)] = np.nan
            base = pd.Series(v).rolling(20, min_periods=15).mean().shift().to_numpy()
            with np.errstate(all="ignore"):
                self._rv[k] = v / base
        return self._rv[k]


def in_window(lo, hi):
    return [i for i, d in enumerate(R.DAYS) if lo <= d.isoformat() <= hi]


def concurrency(cands, max_open):
    """cands: [(entry_j, exit_j, trades)] for one day -> trades of accepted candidates (first come, max_open open)."""
    out, open_ends = [], []
    for ej, xj, tr in sorted(cands, key=lambda x: x[0]):
        open_ends = [e for e in open_ends if e > ej]
        if len(open_ends) >= max_open:
            continue
        open_ends.append(xj)
        out.extend(tr)
    return out


ETFS = ["US.SPY", "US.QQQ", "US.IWM"]


# ------------------------------------------------------------------ 1. opening range breakout / breakdown
def s1_orb(p, days):
    syms = ETFS + (R.UNIVERSE if p["universe"] == "etf+stocks" else [])
    k = p["or_min"] // 5
    trades = []
    for i in days:
        cands = []
        for s in syms:
            S = Sym(s)
            b, n = S.b, int(S.b.expected[i])
            if n < k + 8 or np.isnan(b.high[i, :k]).any():
                continue
            orh, orl, atr = np.max(b.high[i, :k]), np.min(b.low[i, :k]), S.atr[i]
            if not (atr > 0) or orh - orl > 1.25 * atr or b.open[i, 0] < 5:
                continue
            if s not in ETFS and not (S.dvol[i] >= 1e8):
                continue
            if not (S.rvol(k)[i] >= p["rvol"]):
                continue
            c, vw = b.close[i, k:n - 6], S.vwap[i, k:n - 6]
            longs = (c > orh) & (c > vw) if p["dir"] in ("both", "long") else np.zeros_like(c, bool)
            shorts = (c < orl) & (c < vw) if p["dir"] in ("both", "short") else np.zeros_like(c, bool)
            hit = np.flatnonzero(longs | shorts)
            if not len(hit):
                continue
            j = k + hit[0]
            side = 1 if longs[hit[0]] else -1
            je = j + 1
            entry = b.open[i, je]
            if not math.isfinite(entry):
                continue
            candle = entry - b.low[i, j] if side > 0 else b.high[i, j] - entry
            dist = min(candle, p["stop_atr"] * atr) if candle > 0 else p["stop_atr"] * atr
            stop, target = entry - side * dist, entry + side * p["target_r"] * dist
            q = size(0.0015, dist, entry, 0.10, b.volume[i, j])
            if q <= 0:
                continue
            fills, xj = walk(b, i, je, side, entry, stop, target, n - 1, "half_trail")
            cands.append((je, xj, settle(s, R.DAYS[i], side, q, entry, [(f, px, R.DAYS[i], w) for f, px, w in fills])))
        trades += concurrency(cands, 3)
    return trades


# ------------------------------------------------------------------ 2. VWAP trend continuation
def s2_vwap(p, days):
    trades = []
    for i in days:
        cands = []
        for s in ("US.SPY", "US.QQQ"):
            S = Sym(s)
            b, n, atr = S.b, int(S.b.expected[i]), S.atr[i]
            if not atr > 0:
                continue
            o, h, l, c, vw = b.open[i], b.high[i], b.low[i], b.close[i], S.vwap[i]
            j, flat_at, done = 7, n - 3, 0                          # 10:05 bar onward; flatten 15:45 (n-3)
            while j < flat_at - 2 and done < 3:
                side = 1 if c[j] > vw[j] else -1 if c[j] < vw[j] else 0
                rising = vw[j] - vw[j - 3]
                window = np.sign(c[max(0, j - 12):j + 1] - vw[max(0, j - 12):j + 1])
                crosses = int(np.sum(window[1:] != window[:-1]))
                trend_ok = side != 0 and np.sign(rising) == side and crosses < 3
                struct = (h[j] > h[j - 1] and l[j] > l[j - 1]) if side > 0 else (h[j] < h[j - 1] and l[j] < l[j - 1])
                near = (l[j] - vw[j] <= p["pullback_atr"] * atr and c[j] > vw[j]) if side > 0 else \
                       (vw[j] - h[j] <= p["pullback_atr"] * atr and c[j] < vw[j])
                if trend_ok and near and (struct or p["need_struct"] is False):
                    trig = h[j] if side > 0 else l[j]
                    jn = j + 1
                    touched = (h[jn] >= trig) if side > 0 else (l[jn] <= trig)
                    if touched and math.isfinite(o[jn]):
                        entry = max(o[jn], trig) if side > 0 else min(o[jn], trig)
                        stop = vw[j] - side * p["stop_atr"] * atr
                        dist = side * (entry - stop)
                        q = size(0.0015, dist, entry, 0.20, b.volume[i, j])
                        if q > 0 and dist > 0:
                            fills, xj = walk(b, i, jn, side, entry, stop, entry + side * p["target_r"] * dist, flat_at)
                            cands.append((jn, xj, settle(s, R.DAYS[i], side, q, entry, [(f, px, R.DAYS[i], w) for f, px, w in fills])))
                            done += 1
                            j = xj + 1
                            continue
                j += 1
        trades += concurrency(cands, 2)
    return trades


# ------------------------------------------------------------------ 3. failed breakdown reversal (long)
def s3_failed_breakdown(p, days):
    trades = []
    for i in days:
        cands = []
        for s in ETFS:
            S = Sym(s)
            b, n = S.b, int(S.b.expected[i])
            o, h, l, c, vw = b.open[i], b.high[i], b.low[i], b.close[i], S.vwap[i]
            if not math.isfinite(o[0]):
                continue
            low_j, stage, entry_j = None, 0, None
            for j in range(3, n - 12):
                if not math.isfinite(c[j]):
                    continue
                if l[j] <= np.nanmin(l[:j]) and l[j] <= o[0] * (1 - p["drop"]):
                    low_j, stage = j, 1                                # fresh session low beyond the threshold
                    prior15 = np.nanmin(l[max(0, j - 3):j])
                    continue
                if stage == 1 and c[j] > prior15:
                    stage = 2                                          # reclaimed the prior 15-minute low
                if stage == 2:
                    ok = c[j] > vw[j] if p["confirm"] == "vwap" else (l[j] > l[low_j] and l[j] > l[j - 1])
                    if ok:
                        stage = 3
                        continue
                if stage == 3 and h[j] > h[j - 1] and math.isfinite(o[j + 1]):
                    entry_j = j + 1
                    break
            if entry_j is None or entry_j >= n - 6:
                continue
            entry, stop = o[entry_j], l[low_j] - 0.01
            dist = entry - stop
            if not dist > 0:
                continue
            if p["target"] == "vwap":
                target = max(vw[entry_j - 1], entry + 0.5 * dist)        # first target VWAP (at least 0.5R)
            elif p["target"] == "open":
                target = max(o[0], entry + 1.0 * dist)                   # back to the session open (at least 1R)
            else:
                target = entry + float(p["target"][:-1]) * dist
            q = size(0.0010, dist, entry, 0.20, b.volume[i, entry_j - 1])
            if q <= 0:
                continue
            fills, xj = walk(b, i, entry_j, 1, entry, stop, target, n - 1)
            cands.append((entry_j, xj, settle(s, R.DAYS[i], 1, q, entry, [(f, px, R.DAYS[i], w) for f, px, w in fills])))
        trades += concurrency(cands, 3)
    return trades


# ------------------------------------------------------------------ 4. relative-strength sector momentum (60-minute bars)
def s4_sector_rs(p, days):
    secs = [f"US.{x}" for x in R.SECTORS]
    B = {s: R.bars60(s) for s in secs + ["US.SPY"]}
    F = {s: R.features_safe(s) for s in secs + ["US.SPY"]}
    trades = []
    for i in days:
        spy = B["US.SPY"]
        if not math.isfinite(spy.close[i, 0]) or not math.isfinite(F["US.SPY"]["prev_close"][i]):
            continue
        rows = []
        for s in secs:
            b, f = B[s], F[s]
            if not (math.isfinite(b.close[i, 0]) and f["prev_close"][i] > 0 and f["atr14"][i] > 0 and i >= 25):
                continue
            intraday = b.close[i, 0] / b.open[i, 0] - 1
            rel5 = (b.close[i, 0] / f["close"][i - 5]) - (spy.close[i, 0] / F["US.SPY"]["close"][i - 5])
            base = np.nanmean(b.volume[i - 20:i, 0])
            rvol = b.volume[i, 0] / base if base > 0 else np.nan
            vwap = b.turnover[i, 0] / b.volume[i, 0] if b.volume[i, 0] > 0 else np.nan
            rs_up = b.close[i, 0] / spy.close[i, 0] > f["prev_close"][i] / F["US.SPY"]["prev_close"][i]
            rows.append((s, intraday, rel5, rvol, vwap, rs_up))
        if len(rows) < 5:
            continue
        df = pd.DataFrame(rows, columns=["s", "intra", "rel5", "rvol", "vwap", "rs_up"]).dropna()
        z = lambda x: (x - x.mean()) / (x.std(ddof=0) or 1)
        w = p["weights"]
        df["score"] = w[0] * z(df.intra) + w[1] * z(df.rel5) + w[2] * z(df.rvol)
        spy_ok = spy.close[i, 0] > spy.low[i, 0] + 0.25 * (spy.high[i, 0] - spy.low[i, 0])
        if not spy_ok:
            continue
        for _, r in df.sort_values("score", ascending=False).head(p["top"]).iterrows():
            b = B[r.s]
            if not (b.close[i, 0] > r.vwap and r.rs_up and math.isfinite(b.open[i, 1])):
                continue
            entry, atr = b.open[i, 1], F[r.s]["atr14"][i]
            stop = entry - p["stop_atr"] * atr
            q = int(0.10 * EQUITY / entry)
            last = int(b.expected[i]) - 1                              # 15:30 bar open (12:00 bar on half days)
            fills, _ = walk(b, i, 1, 1, entry, stop, None, last)
            trades += settle(r.s, R.DAYS[i], 1, q, entry, [(f, px, R.DAYS[i], why) for f, px, why in fills])
    return trades


# ------------------------------------------------------------------ 5. gap-and-go momentum (stocks)
def s5_gap_go(p, days):
    k = p["or_min"] // 5
    trades = []
    for i in days:
        cands = []
        for s in R.UNIVERSE:
            S = Sym(s)
            b, n, atr, prev = S.b, int(S.b.expected[i]), S.atr[i], S.prev[i]
            if not (atr > 0 and prev > 0) or np.isnan(b.high[i, :k]).any() or b.open[i, 0] < 5:
                continue
            gap = b.open[i, 0] / prev - 1
            if abs(gap) < p["gap"] or abs(b.open[i, 0] - prev) > 2 * atr or not (S.rvol(1)[i] >= p["rvol"]):
                continue
            side = 1 if gap > 0 else -1
            orh, orl = np.max(b.high[i, :k]), np.min(b.low[i, :k])
            c, vw = b.close[i, k:n - 12], S.vwap[i, k:n - 12]
            held = (b.low[i, k:n - 12] >= orl) if side > 0 else (b.high[i, k:n - 12] <= orh)
            cond = ((c > orh) & (c > vw)) if side > 0 else ((c < orl) & (c < vw))
            hold_ok = np.minimum.accumulate(held.astype(int)).astype(bool)
            hit = np.flatnonzero(cond & hold_ok)
            if not len(hit):
                continue
            j = k + hit[0]
            je = j + 1
            entry = b.open[i, je]
            stop = orl if side > 0 else orh
            dist = side * (entry - stop)
            q = size(0.00125, dist, entry, 0.10, b.volume[i, j])
            if q <= 0:
                continue
            target = None if p["target"] == "eod" else entry + side * 2 * dist
            fills, xj = walk(b, i, je, side, entry, stop, target, n - 1)
            cands.append((je, xj, settle(s, R.DAYS[i], side, q, entry, [(f, px, R.DAYS[i], w) for f, px, w in fills])))
        trades += concurrency(cands, 4)
    return trades


# ------------------------------------------------------------------ 6. volume-conditioned gap reversal (stocks)
def s6_gap_reversal(p, days):
    trades = []
    for i in days:
        cands = []
        for s in R.UNIVERSE:
            S = Sym(s)
            b, n, prev = S.b, int(S.b.expected[i]), S.prev[i]
            if not prev > 0 or not math.isfinite(b.high[i, 0]) or b.open[i, 0] < 5:
                continue
            gap = b.open[i, 0] / prev - 1
            if abs(gap) < p["gap"] or not (S.rvol(1)[i] >= p["rvol"]):
                continue
            side = -1 if gap > 0 else 1
            h0, l0 = b.high[i, 0], b.low[i, 0]
            last = min(n - 3, 30) if p["exit"] == "12:00" else n - 3
            entry_j = None
            for j in range(2, min(last - 1, 36)):                      # confirmation from 09:40, entries until 12:30
                c, vw = b.close[i, j], S.vwap[i, j]
                if side < 0 and c < l0 and c < vw and np.nanmax(b.high[i, 1:j + 1]) <= np.nanmax(b.high[i, :j + 1]):
                    entry_j = j + 1
                    break
                if side > 0 and c > h0 and c > vw:
                    entry_j = j + 1
                    break
            if entry_j is None or not math.isfinite(b.open[i, entry_j]):
                continue
            entry = b.open[i, entry_j]
            stop = np.nanmax(b.high[i, :entry_j]) if side < 0 else np.nanmin(b.low[i, :entry_j])
            dist = side * (entry - stop)
            q = size(0.0010, dist, entry, 0.10, b.volume[i, entry_j - 1])
            if q <= 0:
                continue
            fills, xj = walk(b, i, entry_j, side, entry, stop, None, last)
            cands.append((entry_j, xj, settle(s, R.DAYS[i], side, q, entry, [(f, px, R.DAYS[i], w) for f, px, w in fills])))
        trades += concurrency(cands, 4)
    return trades


# ------------------------------------------------------------------ 7. ETF RSI(2) exhaustion reversal (confirmed, multi-day)
def s7_rsi(p, days):
    trades = []
    dayset = set(days)
    for s in ETFS:
        S = Sym(s)
        adj = R.daily(s, 1)
        if adj is None:
            continue
        rsi = pd.Series(R.rsi2(adj.close.to_numpy()), index=adj.index).reindex(R.DAYS).to_numpy()
        f = S.f
        b = S.b
        i = 1
        while i < len(R.DAYS) - 1:
            sig = i - 1                                                # signal from the prior close
            if i not in dayset or not (rsi[sig] <= p["rsi"]) or not (f.atr14.iloc[sig] > 0):
                i += 1
                continue
            if not (f.close.iloc[sig] <= f.high5.iloc[sig] - p["atr_below"] * f.atr14.iloc[sig]):
                i += 1
                continue
            n = int(b.expected[i])
            o, h, l, c, vw = b.open[i], b.high[i], b.low[i], b.close[i], S.vwap[i]
            entry_j = None
            if np.isnan(h[:3]).any():
                i += 1
                continue
            orh = np.max(h[:3])
            for j in range(3, n - 6):
                if p["trigger"] == "orh" and c[j] > orh:
                    entry_j = j + 1
                    break
                if p["trigger"] == "vwap" and j >= 4 and c[j] > vw[j] and c[j - 1] <= vw[j - 1] and l[j] > l[j - 1]:
                    entry_j = j + 1
                    break
            if entry_j is None or not math.isfinite(o[entry_j]):
                i += 1
                continue
            entry = o[entry_j]
            stop = np.nanmin(l[:entry_j])
            dist = entry - stop
            if not dist > 0:
                i += 1
                continue
            q = min(int(0.10 * EQUITY / entry), int(0.0015 * EQUITY / dist) if p["risk_size"] else 10**9)
            target = entry + p["target_r"] * dist
            fills, exit_day, held = None, None, 0
            ii, jj = i, entry_j
            while ii < len(R.DAYS) and fills is None:
                nn = int(b.expected[ii])
                bars_end = nn - 1
                fl, xj = walk(b, ii, jj, 1, entry, stop, target, bars_end, gap_first=ii > i)
                if fl[-1][2] != "time":
                    fills, exit_day = fl, R.DAYS[ii]
                    break
                held += 1
                close_rsi = rsi[ii]
                if held >= 2 or close_rsi >= 50:
                    fills, exit_day = [(1.0, c_next_open(b, ii), "time_or_rsi")], R.DAYS[min(ii + 1, len(R.DAYS) - 1)]
                    break
                ii, jj = ii + 1, 0
            if fills is None:
                break
            trades += settle(s, exit_day, 1, q, entry, [(f_, px, exit_day, w) for f_, px, w in fills])
            i = R.IDX[exit_day] + 1
    return trades


def c_next_open(b, i):
    j = i + 1
    if j < b.open.shape[0] and math.isfinite(b.open[j, 0]):
        return b.open[j, 0]
    return b.close[i, int(b.expected[i]) - 1]


# ------------------------------------------------------------------ 8. last-hour cross-sectional momentum (stocks)
def s8_last_hour(p, days):
    trades = []
    spy = Sym("US.SPY")
    for i in days:
        n = int(spy.b.expected[i])
        if n != 78:
            continue                                                   # skip half days
        rank_j = 71                                                    # bar ending 15:30 completes the window
        start_j = rank_j - p["window"] // 5 + 1
        if not math.isfinite(spy.b.close[i, rank_j]) or not math.isfinite(spy.b.open[i, start_j]):
            continue
        spy_r = spy.b.close[i, rank_j] / spy.b.open[i, start_j] - 1
        liquid = sorted((s for s in R.UNIVERSE if Sym(s).dvol[i] > 0), key=lambda s: -Sym(s).dvol[i])[:30]
        rows = []
        for s in liquid:
            b = Sym(s).b
            if np.isnan(b.close[i, start_j:rank_j + 1]).any() or not math.isfinite(b.open[i, 72]):
                continue
            vol = np.nansum(b.volume[i, start_j:rank_j + 1])
            base = np.nanmean(np.nansum(b.volume[max(0, i - 20):i, start_j:rank_j + 1], axis=1))
            if not (base > 0 and vol / base >= p["rvol"]):
                continue
            rows.append((s, b.close[i, rank_j] / b.open[i, start_j] - 1 - spy_r))
        if len(rows) < 10:
            continue
        rows.sort(key=lambda x: -x[1])
        m = max(1, int(round(len(rows) * p["tail"])))
        picks = [(s, 1) for s, _ in rows[:m]] + ([(s, -1) for s, _ in rows[-m:]] if p["shorts"] else [])
        per = min(0.05, 0.20 / m) * EQUITY
        for s, side in picks:
            b = Sym(s).b
            entry = b.open[i, 72]
            q = min(int(per / entry), int(0.01 * b.volume[i, 71]) if math.isfinite(b.volume[i, 71]) else 0)
            if q <= 0:
                continue
            exit_px = b.open[i, 77] if math.isfinite(b.open[i, 77]) else b.close[i, 76]
            trades += settle(s, R.DAYS[i], side, q, entry, [(1.0, exit_px, R.DAYS[i], "time")])
    return trades


# ------------------------------------------------------------------ 9. ETF pairs mean reversion (daily)
PAIRS = [("SPY", "QQQ"), ("SPY", "IWM"), ("XLK", "SPY"), ("XLF", "SPY")]


def s9_pairs(p, days):
    trades = []
    dayset = set(days)
    px = {s: R.etf_daily(s) for s in {x for pr in PAIRS for x in pr}}
    for a, bsym in PAIRS:
        ya, yb = np.log(px[a]["adjusted"]), np.log(px[bsym]["adjusted"])
        L = p["lookback"]
        beta = ya.rolling(L).cov(yb).shift() / yb.rolling(L).var().shift()
        alpha = ya.rolling(L).mean().shift() - beta * yb.rolling(L).mean().shift()
        sd = np.sqrt((ya.rolling(L).var().shift() - beta ** 2 * yb.rolling(L).var().shift()).clip(lower=0))
        z = ((ya - alpha - beta * yb) / sd).to_numpy()
        bt = beta.to_numpy()
        idx = list(px[a].index)
        for k in range(len(idx) - 1):
            d1 = idx[k + 1]
            if d1 not in R.IDX or R.IDX[d1] not in dayset or not (bt[k] > 0) or not (abs(z[k]) >= p["entry"]):
                continue
            side = -1 if z[k] > 0 else 1                               # short the rich leg a, long b (x beta)
            g = 0.20 * EQUITY / 4                                      # gross per pair (4 pairs share 20%)
            na = g / (1 + bt[k]) / px[a]["open"].iloc[k + 1]
            nb = g * bt[k] / (1 + bt[k]) / px[bsym]["open"].iloc[k + 1]
            for sym, sd_, q in ((a, side, int(na)), (bsym, -side, int(nb))):
                if q <= 0:
                    continue
                e, x = px[sym]["open"].iloc[k + 1], px[sym]["close"].iloc[k + 1]
                trades += settle(sym, d1, sd_, q, e, [(1.0, x, d1, "one_session")])
            _ = p["exit"]
    return trades


# ------------------------------------------------------------------ 10. noise-area intraday momentum (SPY 1-minute)
def s10_noise(p, days):
    b = R.bars1("US.SPY")
    f = R.features("US.SPY")
    prev = f.prev_close.to_numpy()
    vw = R.cum_vwap(b)
    slots = {"report": [59, 119, 239, 299], "hourly": [29, 89, 149, 209, 269, 329],
             "halfhourly": list(range(29, 360, 30))}[p["times"]]
    noise = {}
    for j in slots:
        with np.errstate(all="ignore"):
            mv = np.abs(b.close[:, j] / b.open[:, 0] - 1)
        s = pd.Series(mv)
        obs = s.dropna().rolling(14, min_periods=14).mean()
        noise[j] = obs.reindex(range(len(s))).ffill().shift().to_numpy() * p["band"]
    trades = []
    for i in days:
        n = int(b.expected[i])
        pos, entry, ej = 0, None, None
        o, c = b.open[i], b.close[i]
        if not math.isfinite(o[0]) or not prev[i] > 0:
            continue
        for j in [s for s in slots if s + 1 < n - 5]:
            nz = noise[j][i]
            if not (nz > 0) or not math.isfinite(c[j]):
                continue
            up, dn = max(o[0], prev[i]) * (1 + nz), min(o[0], prev[i]) * (1 - nz)
            tgt = 1 if (c[j] > up and c[j] > vw[i, j]) else -1 if (p["shorts"] and c[j] < dn and c[j] < vw[i, j]) else 0
            if tgt == pos:
                continue
            px_ = o[j + 1]
            if not math.isfinite(px_):
                continue
            if pos != 0:
                trades += settle("US.SPY", R.DAYS[i], pos, qty, entry, [(1.0, px_, R.DAYS[i], "target_change")])
            pos = tgt
            if pos != 0:
                entry, qty = px_, int(p["alloc"] * EQUITY / max(px_, o[0]))
        if pos != 0:
            xj = n - 5
            trades += settle("US.SPY", R.DAYS[i], pos, qty, entry, [(1.0, o[xj] if math.isfinite(o[xj]) else c[xj - 1], R.DAYS[i], "flatten")])
    return trades


STRATS = {
    "1 ORB breakout/breakdown": (s1_orb, {"or_min": [5, 15, 30], "rvol": [1.0, 1.5, 2.0], "stop_atr": [0.25, 0.35, 0.5],
                                          "target_r": [1.5, 2.0], "dir": ["both", "short"], "universe": ["etf", "etf+stocks"]}),
    "2 VWAP trend continuation": (s2_vwap, {"pullback_atr": [0.10, 0.15, 0.25], "stop_atr": [0.15, 0.25, 0.35],
                                            "target_r": [1.5, 2.0, 3.0], "need_struct": [True, False]}),
    "3 Failed breakdown reversal": (s3_failed_breakdown, {"drop": [0.005, 0.01, 0.015], "confirm": ["vwap", "higher_low"],
                                                          "target": ["vwap", "open", "2R"]}),
    "4 Sector relative strength": (s4_sector_rs, {"top": [1, 2, 3], "stop_atr": [0.35, 0.5, 0.75],
                                                  "weights": [(0.5, 0.3, 0.2), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)]}),
    "5 Gap-and-go momentum": (s5_gap_go, {"gap": [0.015, 0.02, 0.03], "rvol": [1.5, 2.0, 3.0], "or_min": [5, 15],
                                          "target": ["eod", "2R"]}),
    "6 Volume gap reversal": (s6_gap_reversal, {"gap": [0.0075, 0.0125, 0.02], "rvol": [1.5, 1.75, 2.5], "exit": ["12:00", "15:45"]}),
    "7 ETF RSI-2 exhaustion": (s7_rsi, {"rsi": [3, 5, 10], "atr_below": [1.0, 2.0], "trigger": ["orh", "vwap"],
                                        "target_r": [1.5, 2.0], "risk_size": [False]}),
    "8 Last-hour momentum": (s8_last_hour, {"window": [30, 60], "tail": [0.10, 0.20], "rvol": [1.0, 1.25, 1.5], "shorts": [True, False]}),
    "9 ETF pairs reversion": (s9_pairs, {"lookback": [40, 60, 90], "entry": [1.75, 2.25, 2.75], "exit": [0.75]}),
    "10 Noise-area momentum": (s10_noise, {"band": [1.0, 1.25, 1.5], "times": ["report", "hourly", "halfhourly"],
                                            "alloc": [0.2, 0.4], "shorts": [True, False]}),
}
