"""Inspect original parquet and human-readable manifests independently."""

import hashlib
import json

import exchange_calendars
import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from audit_core import HIST, MIRROR, OUT, ROOT


def digest(path, algorithm, prefix=b""):
    h = hashlib.new(algorithm, prefix)
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def verify_manifest():
    remote = OUT / "mirror_tree.json"
    tree = {r["path"]: r["sha"] for r in json.loads(remote.read_text())["tree"]} if remote.exists() else {}
    rows = []
    for rel, rec in json.loads((MIRROR / "manifest.json").read_text())["files"].items():
        path = MIRROR / rel
        blob = digest(path, "sha1", f"blob {path.stat().st_size}\0".encode())
        sha = digest(path, "sha256")
        rows.append({"path": rel, "sha256": sha, "git_blob": blob, "matches_local_manifest": sha == rec["sha256"],
                     "matches_remote_commit": blob == tree[rel] if rel in tree else None})
    pd.DataFrame(rows).to_csv(OUT / "mirror_verification.csv", index=False)
    history = json.loads((HIST / "manifest.json").read_text())["sha256"]
    check = pd.read_csv(OUT / "input_sha256.csv").set_index("path")["sha256"].to_dict()
    records = []
    for path, expected in history.items():
        actual = check.get("data-cache/history/" + path)
        records.append({"path": path, "matches_local_file": expected == actual, "expected": expected, "actual": actual})
    pd.DataFrame(records).to_csv(OUT / "history_manifest_verification.csv", index=False)
    freeze = json.loads((ROOT / "config/six_strategy_freeze.json").read_text())["sha256"]
    pd.DataFrame([{"path": p, "expected": h, "actual": check.get(p), "matches": check.get(p) == h}
                  for p, h in freeze.items()]).to_csv(OUT / "strategy_freeze_verification.csv", index=False)


def dates(series):
    return pd.to_datetime(series).dt.normalize().astype("datetime64[ns]")


def underlying():
    broker = pd.read_csv(HIST / "daily_a0/US.SPY.csv.gz", dtype={"date": str})
    broker["date"] = dates(broker["date"])
    broker = broker.set_index("date")
    vendor = pd.read_parquet(MIRROR / "spy/underlying_prices.parquet")
    vendor["date"] = dates(vendor["date"])
    return broker, vendor.set_index("date")


def quote_columns(df, expiry, day):
    return {
        "rows": len(df), "first_date": str(day.min().date()), "last_date": str(day.max().date()),
        "date_count": day.nunique(), "first_expiry": str(expiry.min().date()), "last_expiry": str(expiry.max().date()),
        "strike_min": df.strike.min(), "strike_max": df.strike.max(),
        "negative_prices": int(((df.bid < 0) | (df.ask < 0)).sum()),
        "missing_prices": int((df.bid.isna() | df.ask.isna()).sum()),
        "crossed": int((df.bid > df.ask).sum()), "locked_positive": int(((df.bid == df.ask) & (df.bid > 0)).sum()),
        "zero_bid": int((df.bid == 0).sum()), "zero_bid_size": int((df.bid_size == 0).sum()),
        "zero_ask_size": int((df.ask_size == 0).sum()), "negative_oi": int((df.open_interest < 0).sum()),
        "missing_iv": int(df.implied_volatility.isna().sum()), "nonpositive_iv": int((df.implied_volatility <= 0).sum()),
        "expired_rows": int((expiry < day).sum()),
    }


def scan():
    broker, vendor = underlying()
    cal = exchange_calendars.get_calendar("XNYS", start="2020", end="2027")
    sessions = pd.DatetimeIndex(cal.sessions).astype("datetime64[ns]")
    near_path = ROOT / "data-cache/options_history/spy_near_2021_2025.parquet"
    near = pd.read_parquet(near_path)
    near["date"], near["expiration"] = dates(near["date"]), dates(near["expiration"])
    results = []
    for path in sorted((MIRROR / "spy").glob("options_*.parquet")):
        year = int(path.stem[-4:])
        df = pd.read_parquet(path)
        day, expiry = dates(df.date), dates(df.expiration)
        raw = quote_columns(df, expiry, day)
        raw.update(year=year, version="raw", symbols=" ".join(sorted(df.symbol.unique())),
                   negative_volume=int((df.volume < 0).sum()),
                   duplicate_keys=int(df.assign(date=day).duplicated(["date", "contract_id"]).sum()),
                   missing_volume=int(df.volume.isna().sum()),
                   extra_dates=" ".join(d.date().isoformat() for d in sorted(set(day) - set(sessions))))
        occ = df.contract_id.str.extract(r"^SPY(\d{6})([CP])(\d{8})$")
        good = (occ[0] == expiry.dt.strftime("%y%m%d")) & (occ[1] == df.type.str[0].str.upper()) & (
            pd.to_numeric(occ[2], errors="coerce") == np.rint(df.strike * 1000))
        raw["bad_contract_id"] = int((~good).sum())
        close = day.map(broker.close)
        atm = np.where(df.type == "call", close > df.strike, close < df.strike)
        raw["itm_flag_agrees"] = np.mean(atm == df.in_the_money)
        selected = day.isin(sessions) & ((expiry - day).dt.days.between(0, 21)) & ((df.strike / close - 1).abs() <= .08)
        rebuilt = df.loc[selected, ["strike", "bid", "bid_size", "ask", "ask_size", "open_interest", "implied_volatility"]].copy()
        rebuilt = rebuilt.assign(date=day[selected], expiration=expiry[selected], right=df.type[selected].str[0].str.upper())
        processed = near[near.date.dt.year == year]
        keys = ["date", "expiration", "strike", "right"]
        a, b = (table.sort_values(keys).reset_index(drop=True) for table in (rebuilt, processed))
        local = quote_columns(processed, processed.expiration, processed.date)
        local.update(year=year, version="processed", reconstructed_raw_rows=len(rebuilt),
                     same_rows_as_raw_filter=all(np.array_equal(a[col].to_numpy(), b[col].to_numpy()) for col in a),
                     duplicate_keys=int(processed.duplicated(keys).sum()))
        dates_used = pd.DatetimeIndex(day[day.isin(sessions)].unique())
        raw["missing_underlying_day"] = int(vendor.close.reindex(dates_used).isna().sum())
        raw["mismatched_underlying_close"] = int((~np.isclose(vendor.close.reindex(dates_used), broker.close.reindex(dates_used))).sum())
        raw["vendor_dividend_days"] = int((vendor.dividend_amount.reindex(dates_used) > 0).sum())
        raw["vendor_split_days"] = int((vendor.split_coefficient.reindex(dates_used) != 1).sum())
        raw["schema"] = "|".join(pq.ParquetFile(path).schema.names)
        results.extend([raw, local])
        print(year, len(df), len(processed), raw["crossed"], local["crossed"], local["same_rows_as_raw_filter"], flush=True)
    pd.DataFrame(results).to_csv(OUT / "option_data_scan.csv", index=False)
    cheap = near[(near.ask <= .09) & (near.ask > 0) & ((near.expiration - near.date).dt.days.between(1, 10))]
    positive_bid = cheap[cheap.bid > 0]
    metrics = {"cheap_quotes": len(cheap), "cheap_two_sided": len(positive_bid),
               "penny_spread_all": float(np.isclose(cheap.ask - cheap.bid, .01).mean()),
               "penny_spread_two_sided": float(np.isclose(positive_bid.ask - positive_bid.bid, .01).mean())}
    (OUT / "option_data_metrics.json").write_text(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    verify_manifest()
    scan()
