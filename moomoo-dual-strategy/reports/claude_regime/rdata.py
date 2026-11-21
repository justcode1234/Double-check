"""Data layer for the regime-strategy study: existing caches merged with data-cache/regime_extra (fetched 2026-11-21).

All intraday arrays are (n_days, n_slots) on DAYS (XNYS 2020-11-02 .. 2026-11-20); slot j covers
[09:30 + j*w, 09:30 + (j+1)*w). Missing bars are NaN. Prices are raw (unadjusted) unless noted.
"""

from __future__ import annotations

import gzip
import json
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path

import exchange_calendars
import numpy as np
import pandas as pd

ROOT = Path.cwd()
HIST = ROOT / "data-cache" / "history"
EXTRA = ROOT / "data-cache" / "regime_extra"
ETF = ROOT / "data-cache" / "challenge_public_etfs"
NY = "America/New_York"
CAL = exchange_calendars.get_calendar("XNYS")
START, END = date(2020, 11, 2), date(2026, 11, 20)
DAYS = [s.date() for s in CAL.sessions_in_range(START.isoformat(), END.isoformat())]
IDX = {d: i for i, d in enumerate(DAYS)}
MINUTES = np.array([int((CAL.session_close(pd.Timestamp(d)) - CAL.session_open(pd.Timestamp(d))).total_seconds() // 60)
                    for d in DAYS])
UNIVERSE = json.loads((ROOT / "config" / "research_universe.json").read_text(encoding="utf-8"))["stocks"]
SECTORS = ["XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY"]


class Bars:
    def __init__(self, arr, width):
        self.open, self.high, self.low, self.close, self.volume, self.turnover = (arr[k] for k in
                                                                               ("open", "high", "low", "close", "volume", "turnover"))
        self.width = width
        self.expected = MINUTES // width


def _frames(paths):
    fs = [pd.read_csv(p) for p in paths if p.exists()]
    if not fs:
        return None
    return pd.concat(fs, ignore_index=True).drop_duplicates("end_ms", keep="last")


def _grid(df, width):
    nd, ns = len(DAYS), 390 // width
    arr = {f: np.full((nd, ns), np.nan) for f in ("open", "high", "low", "close", "volume", "turnover")}
    if df is None or not len(df):
        return arr
    ts = pd.to_datetime(df.end_ms.astype("int64"), unit="ms", utc=True).dt.tz_convert(NY).dt.tz_localize(None)
    pos = pd.Index(DAYS).get_indexer(ts.dt.date)
    off = (ts.dt.hour * 60 + ts.dt.minute - 570).to_numpy()
    slot = off // width - 1
    exp = np.where(pos >= 0, MINUTES[np.clip(pos, 0, None)] // width, 0)
    ok = (pos >= 0) & (off % width == 0) & (slot >= 0) & (slot < exp)
    for f in arr:
        arr[f][pos[ok], slot[ok]] = pd.to_numeric(df[f], errors="coerce").to_numpy()[ok]
    return arr


@lru_cache(maxsize=None)
def bars1(sym="US.SPY"):
    paths = sorted((HIST / "k1" / sym).glob("*.csv.gz")) + sorted((EXTRA / "k1" / sym).glob("*.csv.gz"))
    return Bars(_grid(_frames(paths), 1), 1)


def _five_from_one(b1):
    n = b1.open.shape[0]
    blk = lambda a: a.reshape(n, 78, 5)
    o, h, l, c, v, t = (blk(x) for x in (b1.open, b1.high, b1.low, b1.close, b1.volume, b1.turnover))
    bad = np.isnan(c).any(axis=2)
    with np.errstate(all="ignore"):
        out = dict(open=o[:, :, 0].copy(), high=h.max(axis=2), low=l.min(axis=2), close=c[:, :, -1].copy(),
                   volume=v.sum(axis=2), turnover=t.sum(axis=2))
    for a in out.values():
        a[bad] = np.nan
    return Bars(out, 5)


@lru_cache(maxsize=None)
def bars5(sym):
    if sym == "US.SPY":
        return _five_from_one(bars1("US.SPY"))
    paths = sorted((HIST / "k6" / sym).glob("*.csv.gz")) + sorted((EXTRA / "k6" / sym).glob("*.csv.gz"))
    return Bars(_grid(_frames(paths), 5), 5)


@lru_cache(maxsize=None)
def bars60(sym):
    """Moomoo 60-minute bars: slots end 10:30, 11:30, ..., 15:30, 16:00 (7 per regular session)."""
    df = _frames(sorted((EXTRA / "k9" / sym).glob("*.csv.gz")))
    nd = len(DAYS)
    arr = {f: np.full((nd, 7), np.nan) for f in ("open", "high", "low", "close", "volume", "turnover")}
    if df is None:
        return Bars(arr, 60)
    ts = pd.to_datetime(df.end_ms.astype("int64"), unit="ms", utc=True).dt.tz_convert(NY).dt.tz_localize(None)
    pos = pd.Index(DAYS).get_indexer(ts.dt.date)
    m = (ts.dt.hour * 60 + ts.dt.minute).to_numpy()
    slot = np.where(m <= 960, np.ceil((m - 570) / 60).astype(int) - 1, -1)
    ok = (pos >= 0) & (slot >= 0) & (slot < 7)
    for f in arr:
        arr[f][pos[ok], slot[ok]] = pd.to_numeric(df[f], errors="coerce").to_numpy()[ok]
    b = Bars(arr, 60)
    b.expected = np.where(MINUTES == 390, 7, 4)
    return b


@lru_cache(maxsize=None)
def daily(sym, a=0):
    """Daily OHLCV on DAYS (from 2019 where cached). Existing Moomoo cache merged with the fresh fetch."""
    frames = []
    for base in (HIST / f"daily_a{a}", EXTRA / f"daily_a{a}"):
        p = base / f"{sym}.csv.gz"
        if p.exists():
            frames.append(pd.read_csv(p))
    if not frames:
        return None
    df = pd.concat(frames, ignore_index=True).drop_duplicates("date", keep="last")
    df.index = [datetime.strptime(str(int(x)), "%Y%m%d").date() for x in df.pop("date")]
    days = [s.date() for s in CAL.sessions_in_range("2019-01-02", END.isoformat())]
    return df.astype(float).sort_index().reindex(days)


@lru_cache(maxsize=None)
def yahoo(sym):
    d = json.loads(gzip.decompress((ETF / f"{sym}_1d.json.gz").read_bytes()))["chart"]["result"][0]
    idx = pd.to_datetime(d["timestamp"], unit="s", utc=True).tz_convert(NY).date
    q = d["indicators"]["quote"][0]
    f = pd.DataFrame({k: q[k] for k in ("open", "high", "low", "close", "volume")}, index=idx, dtype=float)
    f["adjusted"] = d["indicators"]["adjclose"][0]["adjclose"]
    return f


def features(sym, a_sym=None):
    """Daily features on DAYS: prev close, ATR14 (prior 14), 20-day median dollar volume (prior), RSI-ready closes."""
    d = daily(sym, 0)
    h, l, c = d.high, d.low, d.close
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1, skipna=False)
    out = pd.DataFrame({"prev_close": c.shift(), "atr14": tr.rolling(14, min_periods=14).mean().shift(),
                        "dvol20": (c * d.volume).rolling(20, min_periods=15).median().shift(),
                        "high5": h.rolling(5).max(), "close": c, "high": h, "low": l, "open": d.open}, index=d.index)
    return out.reindex(DAYS)


def vix():
    d = daily("US..VIX", 0)
    return d.close.reindex(DAYS) if d is not None else pd.Series(np.nan, index=DAYS)


def cum_vwap(b):
    vol = np.nancumsum(b.volume, axis=1)
    num = np.nancumsum(b.turnover, axis=1)
    with np.errstate(all="ignore"):
        out = num / vol
    out[np.isnan(b.close)] = np.nan
    return out


def rsi2(values):
    out = np.full(len(values), np.nan)
    g = l = None
    seed = []
    for i in range(1, len(values)):
        ch = values[i] - values[i - 1]
        if not np.isfinite(ch):
            g = l = None
            seed = []
            continue
        up, dn = max(ch, 0.0), max(-ch, 0.0)
        if g is None:
            seed.append((up, dn))
            if len(seed) < 2:
                continue
            g, l = sum(x[0] for x in seed) / 2, sum(x[1] for x in seed) / 2
        else:
            g, l = 0.5 * (g + up), 0.5 * (l + dn)
        out[i] = 100 * g / (g + l) if g + l > 0 else 50.0
    return out


@lru_cache(maxsize=None)
def etf_daily(sym):
    """Yahoo daily (session units, adjusted close) through its last date, extended to END from Moomoo daily bars
    (SPY/QQQ/IWM) or from 60-minute bars (sector ETFs). Extension days: adjusted = raw x last adjustment ratio."""
    y = yahoo(sym)[["open", "high", "low", "close", "volume", "adjusted"]].copy()
    last = y.index[-1]
    ratio = y.adjusted.iloc[-1] / y.close.iloc[-1]
    extra = {}
    d = daily(f"US.{sym}", 0)
    if d is not None:
        for day, r in d.loc[[x for x in d.index if x > last and np.isfinite(d.at[x, "close"])]].iterrows():
            extra[day] = (r.open, r.high, r.low, r.close, r.volume)
    else:
        b = bars60(f"US.{sym}")
        for day in [x for x in DAYS if x > last]:
            i = IDX[day]
            n = int(b.expected[i])
            if np.isfinite(b.open[i, 0]) and np.isfinite(b.close[i, n - 1]):
                extra[day] = (b.open[i, 0], np.nanmax(b.high[i, :n]), np.nanmin(b.low[i, :n]), b.close[i, n - 1], np.nansum(b.volume[i, :n]))
    for day, (o, h, l, c, v) in sorted(extra.items()):
        y.loc[day] = [o, h, l, c, v, c * ratio]
    return y.sort_index()


@lru_cache(maxsize=None)
def features_safe(sym):
    """Daily features for symbols without a Moomoo daily cache (sector ETFs), from etf_daily, aligned to DAYS."""
    y = etf_daily(sym.replace("US.", ""))
    h, l, c = y.high, y.low, y.close
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1, skipna=False)
    f = pd.DataFrame({"prev_close": c.shift(), "atr14": tr.rolling(14, min_periods=14).mean().shift(), "close": c}).reindex(DAYS)
    return {k: f[k].to_numpy() for k in f}
