"""Validate the free end-of-day SPY option-chain mirror (github.com/anahatsingh-ui/options-dataset-hist, commit pinned in
data-cache/options_history_raw/dubach_mirror/SOURCE_COMMIT) before any backtest uses it.

    PYTHONUTF8=1 .venv/Scripts/python.exe -m dual_strategy.research.options.validate_chains
"""

from __future__ import annotations

import gzip
import json
import math
from datetime import date, datetime

import numpy as np
import pandas as pd

from dual_strategy.research.data import sessions
from dual_strategy.research.features import daily_on_calendar
from dual_strategy.research.fetch import ROOT
from dual_strategy.research.options.pricing import bs_greeks, implied_vol

RAW = ROOT / "data-cache" / "options_history_raw" / "dubach_mirror"
COLS = ["contract_id", "expiration", "strike", "type", "mark", "bid", "bid_size", "ask", "ask_size", "volume",
        "open_interest", "date", "implied_volatility", "delta", "in_the_money"]


def load_year(year: int, columns=COLS) -> pd.DataFrame:
    df = pd.read_parquet(RAW / "spy" / f"options_{year}.parquet", columns=columns)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df["expiration"] = pd.to_datetime(df["expiration"]).dt.date
    return df


def underlying() -> pd.Series:
    u = pd.read_parquet(RAW / "spy" / "underlying_prices.parquet")
    return pd.Series(u["close"].to_numpy(float), index=pd.to_datetime(u["date"]).dt.date).sort_index()


def parity_rates(day: pd.DataFrame, spot: float, d: date) -> list[dict]:
    """Per expiry 7-60 DTE: regress (C_mid - P_mid) on K over strikes within 3% of spot:
    C - P = S e^{-qT} - K e^{-rT}  ->  slope = -e^{-rT}, intercept = S e^{-qT}. Returns implied r, q and residuals."""
    out = []
    for exp, g in day.groupby("expiration"):
        T = (exp - d).days / 365.0
        if not 7 / 365 <= T <= 60 / 365:
            continue
        g = g[(g["strike"] / spot - 1).abs() <= 0.03]
        c = g[g["type"] == "call"].set_index("strike")
        p = g[g["type"] == "put"].set_index("strike")
        ks = c.index.intersection(p.index)
        ok = [k for k in ks if c.at[k, "bid"] > 0 and p.at[k, "bid"] > 0]
        if len(ok) < 5:
            continue
        cp = np.array([(c.at[k, "bid"] + c.at[k, "ask"]) / 2 - (p.at[k, "bid"] + p.at[k, "ask"]) / 2 for k in ok])
        half = np.array([((c.at[k, "ask"] - c.at[k, "bid"]) + (p.at[k, "ask"] - p.at[k, "bid"])) / 2 for k in ok])
        K = np.array(ok, dtype=float)
        slope, icpt = np.polyfit(K, cp, 1)
        if slope >= 0 or icpt <= 0:
            continue
        resid = cp - (icpt + slope * K)
        out.append({"date": d, "expiration": exp, "T": T, "r": -math.log(-slope) / T, "q": -math.log(icpt / spot) / T,
                    "resid_abs_mean": float(np.abs(resid).mean()), "half_spread_mean": float(half.mean()), "n": len(ok)})
    return out


def main() -> None:
    sess = set(sessions())
    spot = underlying()
    moomoo = daily_on_calendar("US.SPY", 0)["close"]
    with gzip.open(ROOT / "data-cache" / "history_extra" / "US..VIX_daily.csv.gz", "rt") as fh:
        vix = pd.read_csv(fh)
    vix = pd.Series(vix["close"].to_numpy(float), index=[datetime.strptime(str(x), "%Y%m%d").date() for x in vix["date"]])
    report = {"source": json.loads((RAW / "manifest.json").read_text())["source"],
              "commit": (RAW / "SOURCE_COMMIT").read_text().strip(), "years": {}}
    parity, ivcheck, atm_rows = [], [], []
    for year in range(2021, 2026):
        df = load_year(year)
        days = sorted(df["date"].unique())
        cal = sorted(d for d in sess if d.year == year)
        mid = (df["bid"] + df["ask"]) / 2
        live = df[(df["bid"] > 0) & (df["ask"] > 0)]
        dte = pd.Series([(e - d).days for e, d in zip(df["expiration"], df["date"])])
        sp = df["date"].map(spot)
        itm_calc = np.where(df["type"] == "call", df["strike"] < sp, df["strike"] > sp)
        y = {
            "rows": len(df), "trading_days": len(days),
            "missing_sessions": [str(d) for d in cal if d not in set(days)],
            "extra_non_sessions": [str(d) for d in days if d not in sess],
            "duplicate_contract_days": int(df.duplicated(["contract_id", "date"]).sum()),
            "expired_rows (expiration<date)": int((dte < 0).sum()),
            "expirations_per_day_median": float(df.groupby("date")["expiration"].nunique().median()),
            "zero_dte_rows_pct": round(100 * float((dte == 0).mean()), 2),
            "nan_bid_or_ask": int(df["bid"].isna().sum() + df["ask"].isna().sum()),
            "negative_prices": int(((df["bid"] < 0) | (df["ask"] < 0)).sum()),
            "crossed (ask<bid)": int((df["ask"] < df["bid"]).sum()),
            "locked (ask==bid>0)": int(((df["ask"] == df["bid"]) & (df["bid"] > 0)).sum()),
            "zero_bid_pct": round(100 * float((df["bid"] == 0).mean()), 2),
            "mark_vs_mid_max_abs": round(float((df["mark"] - mid).abs().max()), 4),
            "mark_equals_mid_pct": round(100 * float(((df["mark"] - mid).abs() < 0.0051).mean()), 2),
            "negative_volume_or_oi": int(((df["volume"] < 0) | (df["open_interest"] < 0)).sum()),
            "in_the_money_flag_agrees_pct": round(100 * float((df["in_the_money"].astype(bool) == itm_calc).mean()), 2),
            "iv_nonpositive_pct": round(100 * float((df["implied_volatility"] <= 0).mean()), 2),
            "call_delta_outside_0_1": int(((df["type"] == "call") & ((df["delta"] < 0) | (df["delta"] > 1))).sum()),
            "put_delta_outside_-1_0": int(((df["type"] == "put") & ((df["delta"] > 0) | (df["delta"] < -1))).sum()),
        }
        # ATM 5-15 DTE spreads (what E/F actually trade)
        dd = pd.Series([(e - d).days for e, d in zip(live["expiration"], live["date"])], index=live.index)
        sp_l = live["date"].map(spot)
        atm = live[(dd.between(5, 15)) & ((live["strike"] / sp_l - 1).abs() <= 0.01)]
        spread = atm["ask"] - atm["bid"]
        y["atm_5_15dte_spread_usd_median"] = round(float(spread.median()), 3)
        y["atm_5_15dte_spread_pct_mid_median"] = round(float((spread / ((atm["ask"] + atm["bid"]) / 2)).median() * 100), 2)
        y["atm_5_15dte_quote_size_median"] = float(atm["bid_size"].median())
        report["years"][year] = y
        # parity + IV checks on every 5th trading day
        for d in days[::5]:
            day = live[live["date"] == d]
            s = spot.get(d)
            if s is None or day.empty:
                continue
            rows = parity_rates(day, s, d)
            parity += rows
            for r in rows:
                if not 20 / 365 <= r["T"] <= 45 / 365:
                    continue
                g = day[(day["expiration"] == r["expiration"])]
                k = g.iloc[(g["strike"] - s).abs().argsort()[:2]]
                for _, o in k.iterrows():
                    m = (o["bid"] + o["ask"]) / 2
                    iv = implied_vol(m, s, o["strike"], r["T"], max(r["r"], 0), max(r["q"], 0), o["type"] == "call")
                    if iv:
                        ivcheck.append({"date": d, "iv_dataset": o["implied_volatility"], "iv_recomputed": iv,
                                        "delta_dataset": o["delta"],
                                        "delta_bs": bs_greeks(s, o["strike"], r["T"], max(r["r"], 0), max(r["q"], 0),
                                                              o["implied_volatility"], o["type"] == "call").delta})
                atm_rows.append({"date": d, "atm_iv": float(k["implied_volatility"].mean())})
    par = pd.DataFrame(parity)
    ivc = pd.DataFrame(ivcheck)
    atm = pd.DataFrame(atm_rows).groupby("date")["atm_iv"].mean()
    common = [d for d in atm.index if d in vix.index]
    both = [d for d in spot.index if d in moomoo.index and d.year >= 2021 and not math.isnan(moomoo[d])]
    report["underlying_vs_moomoo_close"] = {"days": len(both),
                                            "max_abs_diff": round(float(np.max(np.abs(spot[both].to_numpy() - moomoo[both].to_numpy()))), 4),
                                            "days_diff_gt_1c": int(np.sum(np.abs(spot[both].to_numpy() - moomoo[both].to_numpy()) > 0.011))}
    report["put_call_parity"] = {"expiry_snapshots": len(par),
                                 "implied_carry_r_minus_q_median_by_year": (par["r"] - par["q"]).groupby(par["date"].map(lambda d: d.year)).median().round(4).to_dict(),
                                 "note": "r and q are not separately identified from +/-3% strikes (collinear slope/intercept); only the forward carry r-q is reported",
                                 "resid_over_half_spread_median": round(float((par["resid_abs_mean"] / par["half_spread_mean"]).median()), 3),
                                 "share_resid_within_half_spread": round(float((par["resid_abs_mean"] <= par["half_spread_mean"]).mean()), 3)}
    report["iv_and_delta_recompute_atm_20_45dte"] = {
        "n": len(ivc), "iv_median_abs_diff_volpts": round(float(100 * (ivc["iv_dataset"] - ivc["iv_recomputed"]).abs().median()), 3),
        "iv_corr": round(float(ivc[["iv_dataset", "iv_recomputed"]].corr().iloc[0, 1]), 4),
        "delta_median_abs_diff": round(float((ivc["delta_dataset"] - ivc["delta_bs"]).abs().median()), 4)}
    report["atm_iv_vs_vix"] = {"days": len(common), "corr": round(float(np.corrcoef(atm[common], vix[common] / 100)[0, 1]), 4),
                               "mean_vix_minus_atm_iv_volpts": round(float((vix[common] / 100 - atm[common]).mean() * 100), 3)}
    out = ROOT / "reports" / "options_data_validation.json"
    out.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    print(json.dumps(report, indent=1, default=str))


if __name__ == "__main__":
    main()
