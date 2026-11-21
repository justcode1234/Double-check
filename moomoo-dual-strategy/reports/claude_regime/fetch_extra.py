"""Read-only market-data fetch (Moomoo CLI, quote OAuth) for the regime-strategy study.

Writes data-cache/regime_extra/<kind>/<symbol>/<year>.csv.gz (end_ms,open,high,low,close,volume,turnover) and
data-cache/regime_extra/daily/<symbol>.csv.gz. Existing caches are not touched. No account is addressed.
history-kline returns at most 1000 bars per call and its --end date is exclusive, so requests are chunked by session.
"""

from __future__ import annotations

import gzip
import json
import re
import subprocess
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import exchange_calendars

CLI = str(Path.home() / "AppData/Local/MoomooCLI/bin/moomoo.exe")
OUT = Path("data-cache/regime_extra")
CAL = exchange_calendars.get_calendar("XNYS")
BARS = {"k1": (1, 390), "k6": (6, 78), "k9": (9, 7)}          # ktype, bars per regular session
_last = 0.0


def cli(args):
    global _last
    for attempt in range(10):
        wait = _last + 1.1 - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last = time.monotonic()
        r = subprocess.run([CLI, *args, "--format", "json"], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=120)
        text = (r.stdout or "") + (r.stderr or "")
        if r.returncode == 0:
            return json.loads(r.stdout)
        if "RATE_LIMIT" in text:
            m = re.search(r"retry after (\d+)s", text)
            time.sleep((int(m.group(1)) if m else 6) + 1)
            continue
        raise RuntimeError(f"{args[:2]} failed: {text[:200]}")
    raise RuntimeError("rate limited")


def rows_of(d):
    inner = d.get("data") if isinstance(d, dict) else None
    return (inner or {}).get("kline_list") or (d.get("kline_list") if isinstance(d, dict) else d) or []


def fetch_intraday(symbol, kind, lo, hi):
    ktype, per = BARS[kind]
    days = [s.date() for s in CAL.sessions_in_range(lo.isoformat(), hi.isoformat())]
    step = max(1, 1000 // per - 1)
    rows = {}
    for k in range(0, len(days), step):
        chunk = days[k:k + step]
        for r in rows_of(cli(["history-kline", symbol, "--ktype", str(ktype), "--autype", "0", "--start",
                              chunk[0].isoformat(), "--end", (chunk[-1] + timedelta(days=1)).isoformat()])):
            t = int(r["time_key"])
            rows[t] = (t, r.get("open", r.get("open_price")), r.get("high", r.get("high_price")), r.get("low", r.get("low_price")),
                       r.get("close", r.get("close_price")), r.get("volume"), r.get("turnover"))
        print(f"{symbol} {kind} {chunk[0]}..{chunk[-1]} total {len(rows)}", flush=True)
    by_year = {}
    for t, *v in sorted(rows.values()):
        y = datetime.fromtimestamp(t / 1000, timezone.utc).year
        by_year.setdefault(y, []).append((t, *v))
    for y, rs in by_year.items():
        p = OUT / kind / symbol / f"{y}.csv.gz"
        p.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(p, "wt", newline="") as fh:
            fh.write("end_ms,open,high,low,close,volume,turnover\n")
            for r in rs:
                fh.write(",".join("" if x is None else str(x) for x in r) + "\n")


def fetch_daily(symbol, lo, hi, autype=0):
    rows = {}
    start = lo
    while start <= hi:
        stop = min(start + timedelta(days=700), hi)
        for r in rows_of(cli(["history-kline", symbol, "--ktype", "2", "--autype", str(autype), "--start",
                              start.isoformat(), "--end", (stop + timedelta(days=1)).isoformat()])):
            key = int(r["date"]) if "date" in r else int(datetime.fromtimestamp(int(r["time_key"]) / 1000, timezone.utc).strftime("%Y%m%d"))
            rows[key] = (key, r.get("open", r.get("open_price")), r.get("high", r.get("high_price")), r.get("low", r.get("low_price")),
                         r.get("close", r.get("close_price")), r.get("volume"), r.get("turnover"))
        start = stop + timedelta(days=1)
    p = OUT / f"daily_a{autype}" / f"{symbol}.csv.gz"
    p.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(p, "wt", newline="") as fh:
        fh.write("date,open,high,low,close,volume,turnover\n")
        for k in sorted(rows):
            fh.write(",".join("" if x is None else str(x) for x in rows[k]) + "\n")
    print(f"{symbol} daily a{autype}: {len(rows)}", flush=True)


def main():
    lo, hi = date(2020, 11, 2), date(2026, 11, 20)
    universe = json.loads(Path("config/research_universe.json").read_text(encoding="utf-8"))["stocks"]
    jobs = [("daily", "US..VIX", lo, hi)]
    for s in ("US.SPY", "US.QQQ", "US.IWM"):
        jobs += [("daily", s, lo, hi), ("daily1", s, lo, hi)]
    jobs += [("k6", "US.QQQ", lo, hi), ("k6", "US.IWM", lo, hi), ("k1", "US.SPY", date(2026, 11, 18), hi)]
    for s in ("XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY"):
        jobs.append(("k9", f"US.{s}", lo, hi))
    jobs += [("k9", "US.SPY", lo, hi)]
    for s in universe:
        jobs += [("k6tail", s, date(2026, 11, 18), hi), ("daily", s, date(2026, 10, 1), hi)]
    only = set(sys.argv[1:])
    for kind, sym, a, b in jobs:
        if only and kind not in only:
            continue
        try:
            if kind == "daily":
                fetch_daily(sym, a, b, 0)
            elif kind == "daily1":
                fetch_daily(sym, a, b, 1)
            elif kind == "k6tail":
                fetch_intraday(sym, "k6", a, b)
            else:
                fetch_intraday(sym, kind, a, b)
        except Exception as exc:                          # noqa: BLE001 - record and continue
            print(f"FAILED {kind} {sym}: {exc}", flush=True)


if __name__ == "__main__":
    main()
