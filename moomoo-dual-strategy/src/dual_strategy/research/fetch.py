"""Resumable, throttled historical download through the authenticated Moomoo CLI (quote endpoints only).

history-kline silently truncates at 1000 bars with has_more=false, so every request covers a fixed
number of XNYS sessions and every returned session is checked against the exchange calendar.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import subprocess
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

import exchange_calendars

from dual_strategy.data.calendar import NY

MOOMOO = str(Path.home() / ".moomoo/bin/moomoo")
ROOT = Path(__file__).resolve().parents[3]
HISTORY = ROOT / "data-cache" / "history"
START = date(2020, 11, 2)
END = date(2026, 11, 17)
SESSIONS_PER_REQUEST = {1: 2, 6: 12}
EXPECTED_BARS = {1: 1, 6: 5}
RTH_INTERVAL = 2.6
OFF_HOURS_INTERVAL = 1.15

_last_call = 0.0


def _rth_now() -> bool:
    now = datetime.now(NY)
    cal = exchange_calendars.get_calendar("XNYS")
    label = now.date().isoformat()
    if not cal.is_session(label):
        return False
    opened = cal.session_open(label).to_pydatetime().astimezone(NY)
    closed = cal.session_close(label).to_pydatetime().astimezone(NY)
    return (opened.timestamp() - 600) <= now.timestamp() <= (closed.timestamp() + 600)


def cli(args: list[str], log) -> dict | list:
    """Pace below the shared rate limit (tighter during RTH so a live runner keeps headroom)."""
    global _last_call
    for attempt in range(12):
        interval = RTH_INTERVAL if _rth_now() else OFF_HOURS_INTERVAL
        wait = _last_call + interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_call = time.monotonic()
        proc = subprocess.run([MOOMOO, *args, "--format", "json"], capture_output=True, text=True, timeout=120)
        out = proc.stdout.strip() or proc.stderr.strip()
        if proc.returncode == 0:
            return json.loads(out)
        match = re.search(r"retry after (\d+)s", out)
        if "RATE_LIMIT" in out:
            delay = int(match.group(1)) + 2 if match else 32
            log(f"rate limited; sleeping {delay}s")
            time.sleep(delay)
            continue
        log(f"error attempt {attempt}: {out[:200]}")
        time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"CLI failed repeatedly: {args}")


def sessions(start: date, end: date) -> list[date]:
    cal = exchange_calendars.get_calendar("XNYS")
    return [s.date() for s in cal.sessions_in_range(start.isoformat(), end.isoformat())]


def expected_count(day: date, ktype: int) -> int:
    cal = exchange_calendars.get_calendar("XNYS")
    o = cal.session_open(day.isoformat())
    c = cal.session_close(day.isoformat())
    return int((c - o).total_seconds() // 60) // EXPECTED_BARS[ktype]


def fetch_intraday(symbol: str, ktype: int, log) -> None:
    """One gzip CSV per (symbol, ktype, year); a year file is written only after all its windows succeed."""
    days = sessions(START, END)
    all_days = sessions(START, date(2026, 12, 31))
    step = SESSIONS_PER_REQUEST[ktype]
    for year in sorted({d.year for d in days}):
        path = HISTORY / f"k{ktype}" / symbol / f"{year}.csv.gz"
        if path.exists():
            continue
        year_days = [d for d in days if d.year == year]
        rows: dict[int, tuple] = {}
        for i in range(0, len(year_days), step):
            chunk = year_days[i:i + step]
            nxt = all_days[all_days.index(chunk[-1]) + 1]
            data = cli(["history-kline", symbol, "--ktype", str(ktype), "--autype", "0",
                        "--start", chunk[0].isoformat(), "--end", nxt.isoformat()], log)
            bars = (data.get("data") or {}).get("kline_list") or []
            if len(bars) >= 1000:
                raise RuntimeError(f"{symbol} {chunk[0]} window hit the 1000-bar cap; reduce window")
            for b in bars:
                if "close" not in b or b.get("volume") is None:
                    continue
                rows[int(b["time_key"])] = (int(b["time_key"]), b["open"], b["high"], b["low"], b["close"],
                                            b["volume"], b.get("turnover"))
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with gzip.open(tmp, "wt", newline="") as fh:
            fh.write("end_ms,open,high,low,close,volume,turnover\n")
            for key in sorted(rows):
                fh.write(",".join("" if v is None else str(v) for v in rows[key]) + "\n")
        tmp.replace(path)
        log(f"{symbol} k{ktype} {year}: {len(rows)} bars / {len(year_days)} sessions")


def fetch_daily(symbol: str, autype: int, log) -> None:
    path = HISTORY / f"daily_a{autype}" / f"{symbol}.csv.gz"
    if path.exists():
        return
    rows: dict[int, tuple] = {}
    start = date(2019, 1, 2)
    while start <= END:
        stop = min(date(start.year + 2, start.month, start.day), END)
        data = cli(["history-kline", symbol, "--ktype", "2", "--autype", str(autype),
                    "--start", start.isoformat(), "--end", stop.isoformat()], log)
        for b in (data.get("data") or {}).get("kline_list") or []:
            if "close" in b:
                rows[int(b["date"])] = (int(b["date"]), b["open"], b["high"], b["low"], b["close"], b["volume"], b.get("turnover"))
        start = date(stop.year, stop.month, stop.day)
        if stop == END:
            break
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", newline="") as fh:
        fh.write("date,open,high,low,close,volume,turnover\n")
        for key in sorted(rows):
            fh.write(",".join("" if v is None else str(v) for v in rows[key]) + "\n")
    log(f"{symbol} daily autype {autype}: {len(rows)} days")


def fetch_actions(symbol: str, log) -> None:
    path = HISTORY / "actions" / f"{symbol}.json"
    if path.exists():
        return
    out = {}
    for kind in ("splits", "dividends"):
        out[kind] = cli(["corporate-actions", symbol, "--type", kind, "--limit", "200"], log)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=1, ensure_ascii=False))
    log(f"{symbol} corporate actions saved")


def write_manifest(log) -> None:
    files = sorted(p for p in HISTORY.rglob("*") if p.is_file() and p.suffix in {".gz", ".json"} and p.name != "manifest.json")
    entries = {str(p.relative_to(HISTORY)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    manifest = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": "moomoo CLI v0.5.0 history-kline / corporate-actions (Global API, quote OAuth)",
        "intraday_autype": 0, "extended_time": 0, "timestamp": "end_ms = bar END, epoch ms UTC",
        "start": START.isoformat(), "end": END.isoformat(), "sha256": entries,
    }
    (HISTORY / "manifest.json").write_text(json.dumps(manifest, indent=1))
    log(f"manifest: {len(entries)} files")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--universe", default=str(ROOT / "config" / "research_universe.json"))
    args = parser.parse_args()
    HISTORY.mkdir(parents=True, exist_ok=True)
    logfile = (HISTORY / "fetch.log").open("a")

    def log(msg: str) -> None:
        line = f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {msg}"
        print(line, flush=True)
        logfile.write(line + "\n")
        logfile.flush()

    universe = json.loads(Path(args.universe).read_text())
    etfs, stocks = universe["etfs"], universe["stocks"]
    for sym in etfs + stocks:
        for autype in (0, 1):
            fetch_daily(sym, autype, log)
        fetch_actions(sym, log)
    for sym in etfs:
        fetch_intraday(sym, 1, log)
    for sym in stocks:
        fetch_intraday(sym, 6, log)
    write_manifest(log)
    log("DONE")


if __name__ == "__main__":
    sys.exit(main())
