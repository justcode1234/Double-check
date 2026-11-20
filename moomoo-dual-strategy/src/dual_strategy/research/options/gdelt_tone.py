"""Daily U.S. stock-market news tone from free GDELT 2.0 GKG raw files (no account; cite the GDELT Project).

For each XNYS session, the GKG 15-minute batch stamped 15:30 America/New_York (articles GDELT processed in the
preceding 15 minutes) is downloaded, filtered to the ECON_STOCKMARKET theme, de-duplicated (same URL, then identical
7-value tone vector = syndicated copy) and reduced to one row. The batch timestamp precedes the 16:00 ET option
snapshot, so a signal built from it is available before any EOD fill. Raw zips are hashed then discarded.

    PYTHONUTF8=1 .venv/Scripts/python.exe -m dual_strategy.research.options.gdelt_tone      (~1 h, ~11 GB streamed)
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import time
import urllib.request
import zipfile
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from dual_strategy.research.data import sessions
from dual_strategy.research.fetch import ROOT

OUT = ROOT / "data-cache" / "history_extra" / "gdelt_econ_stockmarket_tone_1530ET.csv"
MAN = ROOT / "data-cache" / "history_extra" / "gdelt_files_manifest.json"
NY = ZoneInfo("America/New_York")
UA = "research-backtest/1.0 (offline study)"
FIELDS = ["date", "gkg_file_utc", "status", "rows_total", "rows_stockmarket", "rows_dedup", "tone_mean", "tone_median",
          "pos_mean", "neg_mean", "polarity_mean"]


def file_stamp(d: date) -> str:
    local = datetime(d.year, d.month, d.day, 15, 30, tzinfo=NY)
    return local.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S")


def parse(blob: bytes) -> dict:
    z = zipfile.ZipFile(io.BytesIO(blob))
    text = z.read(z.namelist()[0]).decode("utf-8", "replace")
    total, seen_url, seen_tone, tones = 0, set(), set(), []
    for line in text.splitlines():
        total += 1
        if "ECON_STOCKMARKET" not in line:
            continue
        f = line.split("\t")
        if len(f) < 16 or ("ECON_STOCKMARKET" not in f[7] and "ECON_STOCKMARKET" not in f[8]):
            continue
        url, tone = f[4], f[15]
        if url in seen_url or tone in seen_tone:
            continue
        seen_url.add(url)
        seen_tone.add(tone)
        try:
            v = [float(x) for x in tone.split(",")[:4]]
        except ValueError:
            continue
        tones.append(v)
    n = len(tones)
    srt = sorted(t[0] for t in tones)
    mean = lambda i: round(sum(t[i] for t in tones) / n, 5) if n else None
    return {"rows_total": total, "rows_stockmarket": n, "rows_dedup": n, "tone_mean": mean(0),
            "tone_median": round(srt[n // 2], 5) if n else None, "pos_mean": mean(1), "neg_mean": mean(2),
            "polarity_mean": mean(3)}


def fetch_day(d: date) -> tuple[dict, dict]:
    stamp = file_stamp(d)
    url = f"https://data.gdeltproject.org/gdeltv2/{stamp}.gkg.csv.zip"
    row, man = {"date": d.isoformat(), "gkg_file_utc": stamp, "status": "missing"}, {}
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                blob = r.read()
            man[stamp] = {"sha256": hashlib.sha256(blob).hexdigest(), "bytes": len(blob)}
            row.update(parse(blob), status="ok")
            break
        except urllib.error.HTTPError as e:
            row["status"] = f"http_{e.code}"
            if e.code == 404:
                break
        except Exception as e:
            row["status"] = f"error_{type(e).__name__}"
            time.sleep(3 * (attempt + 1))
    return row, man


def main_parallel(start: date = date(2021, 1, 4), end: date = date(2025, 12, 12), workers: int = 4) -> None:
    """Same output as main(), 4 polite concurrent downloads, resumes from rows already marked ok."""
    from concurrent.futures import ThreadPoolExecutor
    done = set()
    if OUT.exists():
        with open(OUT, encoding="utf-8") as fh:
            done = {r["date"] for r in csv.DictReader(fh) if r["status"] == "ok"}
    man = json.loads(MAN.read_text(encoding="utf-8")) if MAN.exists() else {}
    todo = [s for s in sessions() if start <= s <= end and s.isoformat() not in done]
    new = not OUT.exists()
    with open(OUT, "a", newline="", encoding="utf-8") as fh, ThreadPoolExecutor(workers) as ex:
        w = csv.DictWriter(fh, FIELDS)
        if new:
            w.writeheader()
        for i, (row, m) in enumerate(ex.map(fetch_day, todo)):
            w.writerow(row)
            fh.flush()
            man.update(m)
            if i % 50 == 0:
                MAN.write_text(json.dumps(man, indent=0), encoding="utf-8")
    MAN.write_text(json.dumps(man, indent=0), encoding="utf-8")
    print("done", OUT)


def main(start: date = date(2021, 1, 4), end: date = date(2025, 12, 12)) -> None:
    done = set()
    if OUT.exists():
        with open(OUT, encoding="utf-8") as fh:
            done = {r["date"] for r in csv.DictReader(fh) if r["status"] == "ok"}
    man = json.loads(MAN.read_text(encoding="utf-8")) if MAN.exists() else {}
    new = not OUT.exists()
    with open(OUT, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, FIELDS)
        if new:
            w.writeheader()
        for d in [s for s in sessions() if start <= s <= end]:
            if d.isoformat() in done:
                continue
            stamp = file_stamp(d)
            url = f"https://data.gdeltproject.org/gdeltv2/{stamp}.gkg.csv.zip"
            row = {"date": d.isoformat(), "gkg_file_utc": stamp, "status": "missing"}
            for attempt in range(4):
                try:
                    req = urllib.request.Request(url, headers={"User-Agent": UA})
                    with urllib.request.urlopen(req, timeout=60) as r:
                        blob = r.read()
                    man[stamp] = {"sha256": hashlib.sha256(blob).hexdigest(), "bytes": len(blob)}
                    row.update(parse(blob), status="ok")
                    break
                except urllib.error.HTTPError as e:
                    row["status"] = f"http_{e.code}"
                    if e.code == 404:
                        break
                except Exception as e:  # network hiccup
                    row["status"] = f"error_{type(e).__name__}"
                    time.sleep(3 * (attempt + 1))
            w.writerow(row)
            fh.flush()
            if len(man) % 50 == 0:
                MAN.write_text(json.dumps(man, indent=0), encoding="utf-8")
    MAN.write_text(json.dumps(man, indent=0), encoding="utf-8")
    print("done", OUT)


if __name__ == "__main__":
    main_parallel()
