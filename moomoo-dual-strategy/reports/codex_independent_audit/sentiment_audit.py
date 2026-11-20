"""Independent D proxy, public batch integrity and early-close alignment checks."""

from datetime import datetime
from hashlib import sha256
import json
from zipfile import ZipFile
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from audit_core import OUT, ROOT, SEED, option_fee
from option_replay import Quotes, WINDOWS, segment
from stock_replay import daily, session_minutes

EXTRA = ROOT / "data-cache/history_extra"
TONE = EXTRA / "gdelt_econ_stockmarket_tone_1530ET.csv"
NY = ZoneInfo("America/New_York")


def check_batches():
    expected = json.loads((EXTRA / "gdelt_files_manifest.json").read_text())
    cached = pd.read_csv(TONE, dtype={"gkg_file_utc": str}).set_index("gkg_file_utc")
    rows = []
    for file in sorted((OUT / "gdelt_samples").glob("*.zip")):
        stamp = file.stem
        with ZipFile(file) as archive:
            lines = archive.read(archive.namelist()[0]).decode("utf-8", "replace").splitlines()
        seen_urls, seen_vectors, picked = set(), set(), []
        themed, max_record_stamp = 0, ""
        for line in lines:
            f = line.split("\t")
            if len(f) < 16 or all("ECON_STOCKMARKET" not in f[i] for i in (7, 8)):
                continue
            themed += 1
            max_record_stamp = max(max_record_stamp, f[1])
            if f[4] in seen_urls or f[15] in seen_vectors:
                continue
            seen_urls.add(f[4])
            seen_vectors.add(f[15])
            try:
                picked.append(float(f[15].split(",")[0]))
            except ValueError:
                continue
        checksum = sha256(file.read_bytes()).hexdigest()
        record = cached.loc[stamp] if stamp in cached.index else {}
        rows.append({"stamp": stamp, "on_manifest": stamp in expected, "sha256": checksum,
                     "sha_match": expected.get(stamp, {}).get("sha256") == checksum,
                     "source_rows": len(lines), "themed_rows": themed, "deduped_rows": len(picked),
                     "cached_deduped_rows": record.get("rows_dedup"), "mean_tone": np.mean(picked),
                     "cached_mean_tone": record.get("tone_mean"), "latest_gkg_record_stamp": max_record_stamp})
    pd.DataFrame(rows).to_csv(OUT / "gdelt_sample_verification.csv", index=False)


def run():
    q = Quotes()
    tone = pd.read_csv(TONE, dtype={"gkg_file_utc": str}).set_index("date").sort_index()
    tone = tone.loc[tone.status == "ok"]
    if not tone.index.is_unique:
        raise ValueError("Multiple successful GDELT batches for one session")
    spot = daily().close
    adj = daily(adjusted=True).close
    nxt = adj.shift(-1) / adj - 1
    calendar = list(spot.index)
    index = {d: i for i, d in enumerate(calendar)}
    tone_val = tone.tone_mean
    prior = tone_val.shift(1).rolling(20, min_periods=15)
    z = ((tone_val - prior.mean()) / prior.std()).dropna()
    trades, days = [], []
    for d, score in z.items():
        if d not in q.chain or not np.isfinite(nxt[d]):
            continue
        d1 = calendar[index[d] + 1]
        stamp = datetime.strptime(tone.loc[d, "gkg_file_utc"] + "+0000", "%Y%m%d%H%M%S%z").astimezone(NY)
        before_session_close = stamp.hour * 60 + stamp.minute < 570 + session_minutes(d)
        seg = segment(d)
        timing_clean = before_session_close and d1 <= WINDOWS[seg][1]
        rec = {"segment": seg, "date": d, "option_exit_date": d1, "z": score, "return_next": nxt[d],
               "batch_et": stamp.isoformat(), "early_close": not before_session_close,
               "target_crosses_split": d1 > WINDOWS[seg][1], "timing_clean": timing_clean}
        expiries = [e for e in q.chain[d] if (index[e] - index[d] if e in index else 10_000) >= 2]
        rec["non_session_expiry"] = bool(expiries and expiries[0] not in index)
        for right in ("C", "P"):
            rec[right] = np.nan
            if not expiries:
                continue
            e = expiries[0]
            k = q.nearest(d, e, spot[d])
            a, b = q.get(d, e, k, right), q.get(d1, e, k, right)
            if not a or not b or a[1] <= 0:
                continue
            fees = float(option_fee(1, a[1], False) + option_fee(1, b[0], True))
            rec[right] = (b[0] - a[1]) * 100 - fees
            if (right == "C") == (score > 0):
                trades.append({"strategy": "D", "variant": "D_one_day_EOD_proxy", "segment": seg, "entry_date": d,
                               "exit_date": d1, "option_type": right, "expiry": e, "strike": k, "entry_ask": a[1],
                               "exit_bid": b[0], "fees": fees, "net_pnl": rec[right], "timing_clean": timing_clean,
                               "executable_quote_sanity": a[3] > 0 and b[2] > 0 and b[0] > 0 and a[1] >= a[0] and b[1] >= b[0]})
        days.append(rec)
    days = pd.DataFrame(days)
    stats = []
    for seg, group in days.groupby("segment"):
        for mode, g in (("as_run", group), ("timing_clean", group[group.timing_clean])):
            y, signal = np.sign(g.return_next.to_numpy()), np.sign(g.z.to_numpy())
            hit = (signal == y)
            base = y > 0
            paired = hit.astype(float) - base
            shifted = [np.mean(np.roll(signal, n) == y) for n in range(1, len(y))]
            groups = np.random.default_rng(SEED).integers(0, len(y), (4000, (len(y) + 3) // 4, 1))
            indexes = ((groups + np.arange(4)) % len(y)).reshape(4000, -1)[:, :len(y)]
            ci = np.quantile(paired[indexes].mean(axis=1), [.025, .975])
            option_days = g[~g.non_session_expiry] if mode == "timing_clean" else g
            pay = np.where(option_days.z > 0, option_days.C, option_days.P)
            stats.append({"segment": seg, "mode": mode, "days": len(g), "accuracy": hit.mean(),
                          "up_baseline": base.mean(), "circulant_p_one_sided": (1 + np.sum(np.array(shifted) >= hit.mean())) / len(y),
                          "accuracy_minus_up_ci_low": ci[0], "accuracy_minus_up_ci_high": ci[1],
                          "paired_option_available": int(option_days[["C", "P"]].notna().all(axis=1).sum()),
                          "chosen_option_available": int(np.isfinite(pay).sum()),
                          "option_pnl_mean": np.nanmean(pay), "always_call": option_days.C.mean(), "always_put": option_days.P.mean(),
                          "early_close_rows": int(group.early_close.sum()),
                          "cross_split_rows": int(group.target_crosses_split.sum()),
                          "non_session_expiry_rows": int(group.non_session_expiry.sum())})
    pd.DataFrame(stats).to_csv(OUT / "sentiment_independent.csv", index=False)
    days.to_csv(OUT / "sentiment_signal_daily.csv", index=False)
    pd.DataFrame(trades).to_csv(OUT / "sentiment_proxy_trades.csv", index=False)


if __name__ == "__main__":
    check_batches()
    run()
