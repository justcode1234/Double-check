"""Compare reproduction and independent runs, then build the audit deliverables."""

import ast
import hashlib
import json

import numpy as np
import pandas as pd

from audit_core import OUT, REPRO, ROOT, minute, risk_rows
from stock_replay import OOS, daily


def load(path, **kwargs):
    return pd.read_csv(path, low_memory=False, **kwargs)


def check_inputs():
    manifest = load(OUT / "input_sha256.csv")
    changed = []
    for row in manifest.itertuples(index=False):
        src = ROOT / row.path
        if not src.is_file():
            changed.append({"path": row.path, "status": "missing"})
            continue
        with src.open("rb") as file:
            actual = hashlib.file_digest(file, "sha256").hexdigest()
        if row.sha256 != actual:
            changed.append({"path": row.path, "status": "changed"})
    (OUT / "input_verification_after_run.json").write_text(
        json.dumps({"checked": len(manifest), "changed": changed}, indent=2), encoding="utf-8")


def check_reproduction(stock, option):
    parent = ROOT / "reports"
    routes = ("backtest_v3/backtest_results.csv", "backtest_v3/trades_A.csv", "backtest_v3/trades_B.csv",
              "backtest_v3/trades_C.csv", "backtest_v3/equity_curve_A.csv", "backtest_v3/equity_curve_B.csv",
              "backtest_v3/equity_curve_C.csv", "options_eod/eod_results.csv", "options_eod/eod_trades.csv",
              "options_ef/ef_results.csv", "options_ef/ef_trades.csv", "options_ef/ef_oos_daily_returns.csv",
              "options_ef/ef_calibration_scores.csv", "options_ef/ef_random_baseline.csv")
    comparisons = []
    for path in routes:
        x, y = load(parent / path, comment="#"), load(REPRO / path, comment="#")
        comparisons.append({"comparison": path, "original_rows": len(x), "reproduced_rows": len(y),
                            "same_schema": x.columns.equals(y.columns), "values_match": x.equals(y)})
    a, b = parent / "options_ef/d_sentiment.json", REPRO / "d_sentiment.json"
    comparisons.append({"comparison": "D sentiment JSON", "values_match": json.loads(a.read_text()) == json.loads(b.read_text())})
    pd.DataFrame(comparisons).to_csv(OUT / "reproduction_comparisons.csv", index=False)
    base = load(REPRO / "trade_level_ledger_A_to_F.csv", skiprows=1)
    stock = stock.query("segment=='oos'")
    st_rows = []
    for name in ("B0", "B0-100", "B1", "B1-LO", "B2", "C0", "C1", "C1-LO"):
        x, y = base[base.variant == name].copy(), stock[stock.variant == name].copy()
        for side in ("entry", "exit"):
            x[side + "_slot"] = x[side + "_time"].map(minute)
        keys = ["variant", "date", "side", "entry_slot", "exit_slot", "qty"]
        st_rows.append(x.merge(y, on=keys, how="outer", suffixes=("_original", "_independent"), indicator=True))
    checked = pd.concat(st_rows)
    checked["net_delta"] = checked.net_pnl_independent - checked.net_pnl_original
    checked.to_csv(OUT / "stock_BC_trade_comparison.csv", index=False)

    x, y = load(REPRO / "options_ef/ef_trades.csv"), option.copy()
    x["key_legs"] = x.legs.map(lambda v: str(tuple(tuple(leg) for leg in json.loads(v))))
    y["key_legs"] = y.legs.map(lambda v: str(ast.literal_eval(v)))
    keys = ["strategy", "variant", "segment", "entry_date", "exit_date", "expiry", "key_legs", "contracts"]
    checks = x.merge(y, how="outer", on=keys, suffixes=("_original", "_independent"), indicator=True)
    for field in ("net_pnl", "max_loss", "entry_cash", "exit_cash"):
        checks[field + "_delta"] = checks[field + "_independent"] - checks[field + "_original"]
    checks.to_csv(OUT / "option_trade_comparison.csv", index=False)
    return base


def describe(name, strategy, quality, t, g, capital, sample="oos",
             return_col="return", exposure_col="exposure", peak_col="peak_exposure"):
    r = g[return_col].to_numpy()
    eq = np.r_[1., np.cumprod(1 + r)]
    dd = eq / np.maximum.accumulate(eq) - 1
    win, lose = t.net_pnl[t.net_pnl > 0], t.net_pnl[t.net_pnl < 0]
    spy = daily(adjusted=True).close.pct_change().reindex(g.date).fillna(0).to_numpy()
    exposure = g[exposure_col].mean() if exposure_col in g else np.nan
    peak = g[peak_col].max() if peak_col in g else np.nan
    scenarios = risk_rows(name, r, sample)
    c = next(v for v in scenarios if v["block"] == 4)
    sd = np.std(r, ddof=1)
    dn = np.sqrt(np.mean(np.minimum(r, 0) ** 2))
    row = {"strategy": strategy, "variant": name, "sample": sample, "start": g.date.iloc[0], "end": g.date.iloc[-1],
           "sessions": len(r), "data_quality": quality, "net_return_pct": 100 * (eq[-1] - 1),
           "ledger_net_pnl_usd": t.net_pnl.sum(), "fees_usd": t.fees.sum() if "fees" in t else np.nan,
           "slippage_usd": t.slippage.sum() if "slippage" in t else np.nan,
           "max_drawdown_pct": 100 * dd.min(), "expectancy_usd": t.net_pnl.mean(),
           "expectancy_R": t.r_multiple.mean() if "r_multiple" in t else np.nan,
           "trades": len(t), "win_pct": 100 * len(win) / len(t) if len(t) else np.nan, "avg_win": win.mean(),
           "avg_loss": lose.mean(), "profit_factor": win.sum() / -lose.sum() if len(lose) else np.nan,
           "sharpe": np.sqrt(252) * r.mean() / sd if sd > 0 and len(t) >= 30 else np.nan,
           "sortino": np.sqrt(252) * r.mean() / dn if dn > 0 and len(t) >= 30 else np.nan,
           "worst_day_pct": 100 * r.min(), "mean_exposure_fraction": exposure, "peak_exposure_fraction": peak,
           "cash_return_pct": 0., "spy_buy_hold_pct": (np.prod(1 + spy) - 1) * 100,
           "spy_exposure_matched_pct": (np.prod(1 + spy * exposure) - 1) * 100 if np.isfinite(exposure) else np.nan,
           "capital_usd": capital, "ci95_return_low_pct": c["full_return_ci95_low_pct"],
           "ci95_return_high_pct": c["full_return_ci95_high_pct"],
           "p_negative_8_sessions": c["probability_negative"], "verdict": "REPRODUCED"}
    return row, scenarios


def blocked(strategy, variant, reason, lo=None, hi=None):
    return {"strategy": strategy, "variant": variant, "sample": "oos", "start": lo, "end": hi, "data_quality": reason,
            "verdict": "BLOCKED (DATA)"}


def earlier_eod_rows():
    files = REPRO / "options_eod"
    summary = load(files / "eod_results.csv").query("segment=='oos' and stress=='baseline'")
    equity = load(files / "eod_equity_oos.csv").set_index("date")
    trades = load(files / "eod_trades.csv").query("segment=='oos'")
    rows, risk = [], []
    for original in summary.itertuples():
        variant = f"Earlier EOD {original.variant}"
        curve = equity[original.variant]
        rets = curve.pct_change().fillna(curve.iloc[0] / 50_000 - 1)
        daily_equity = pd.DataFrame({"date": curve.index, "return": rets.to_numpy()})
        sample = trades[trades.variant == original.variant]
        row, scenarios = describe(variant, original.variant[0], "Earlier SPY EOD recipe, daily quote proxy",
                                  sample, daily_equity, 50_000)
        rows.append(row)
        risk.extend(scenarios)
    return rows, risk


def verdict_rows(base, st, op, proxy):
    stock, options = (load(OUT / f"{group}_independent_daily.csv") for group in ("stock", "option"))
    a_daily = load(OUT / "stock_reproduction_daily.csv")
    a_peak = OUT / "stock_A_peak_exposure.csv"
    if a_peak.is_file():
        a_daily = a_daily.merge(load(a_peak), on=["variant", "date"], how="left")
    report, risk = [], []

    def add(*args, **kwargs):
        row, measures = describe(*args, **kwargs)
        report.append(row)
        risk.extend(measures)

    report.append(blocked("A", "A0 faithful published ORB", "Point-in-time universe, delistings and intrabar sequencing absent", *OOS))
    for name, g in a_daily[a_daily.variant.str.startswith("A")].groupby("variant"):
        t = base[base.variant == name]
        add(name, "A", "40 current names, 5-min OHLCV only", t, g, 100_000 if "100k" in name else 50_000, return_col="ret")
        report[-1].update(ledger_net_pnl_usd=g.pnl.sum(), fees_usd=g.fees.sum(), slippage_usd=g.slippage.sum())
        if not len(t):
            report[-1].update(trades=int(g.trades.sum()), expectancy_usd=g.pnl.sum() / g.trades.sum())
    track = load(REPRO / "backtest_v3/backtest_results.csv")
    alt = track.query("variant=='A0-R-optimistic' and segment=='oos'").iloc[0]
    report.append({"strategy": "A", "variant": alt.variant, "sample": "oos", "start": alt.start, "end": alt.end,
                   "data_quality": "40 current names, optimistic intrabar sequence", "net_return_pct": alt.net_return_pct,
                   "trades": alt.trades, "fees_usd": alt.fees_usd, "slippage_usd": alt.slippage_usd,
                   "max_drawdown_pct": alt.max_drawdown_pct, "expectancy_usd": alt.expectancy_usd,
                   "expectancy_R": alt.expectancy_r, "verdict": "REPRODUCED"})
    for (name, seg), g in stock.groupby(["variant", "segment"]):
        t = st[(st.variant == name) & (st.segment == seg)].copy()
        t["r_multiple"] = t.net_pnl / ((t.entry_fill - t.stop).abs() * t.qty)
        add(name, name[0], "SPY 1-min OHLCV and turnover, no historical NBBO", t, g,
            100_000 if "100k" in name else 50_000, sample=seg)
    report.append(blocked("D", "D original +$0.01 and no stop", "Intraday bid/ask, point-in-time news and complete rules required"))
    for seg, trades in proxy.groupby("segment"):
        report.append({"strategy": "D", "variant": "D GDELT 1-day option proxy", "sample": seg,
                       "start": trades.entry_date.min(), "end": trades.entry_date.max(),
                       "data_quality": "EOD spread crossing, one lot, separate timing test", "trades": len(trades),
                       "fees_usd": trades.fees.sum(), "ledger_net_pnl_usd": trades.net_pnl.sum(),
                       "expectancy_usd": trades.net_pnl.mean(), "verdict": "REPRODUCED"})
    for (name, seg), g in options.groupby(["variant", "segment"]):
        t = op[(op.variant == name) & (op.segment == seg)]
        add(name, name[0], "SPY EOD snapshots, reported time only, reused holdout", t, g, 50_000,
            sample=seg, exposure_col="risk_fraction", peak_col="risk_fraction")
    earlier, earlier_risk = earlier_eod_rows()
    report.extend(earlier)
    risk.extend(earlier_risk)
    report.append(blocked("E", "E original intraday specification", "Intraday option snapshots and untouched post-2025 holdout absent"))
    report.append(blocked("F", "F original intraday specification", "Intraday option snapshots and untouched post-2025 holdout absent"))
    report.extend([
        {"strategy": "B", "variant": "B0 exact published data and fills", "data_quality": "Different sample, VWAP and fill convention", "verdict": "NOT REPRODUCIBLE"},
        {"strategy": "D", "variant": "D −53.7% applied to original", "data_quality": "Earlier ATM call EOD proxy", "verdict": "DISPUTED"},
        {"strategy": "F", "variant": "F-long screenshot fee split", "data_quality": "Gross and fee amounts transposed", "verdict": "DISPUTED"},
    ])
    result = pd.DataFrame(report)
    result.to_csv(OUT / "six_strategy_verdict.csv", index=False, float_format="%.8g")
    pd.DataFrame(risk).to_csv(OUT / "monte_carlo_risk.csv", index=False, float_format="%.8g")
    return result


def markdown(result):
    headline = ["A0 faithful published ORB", "A0-R", "A0-R-optimistic", "A1", "A1-LO",
                "B0", "B0-100", "B1", "B1-LO", "B2", "C0", "C1", "C1-LO",
                "D original +$0.01 and no stop", "D GDELT 1-day option proxy",
                "E_boot", "E_drift", "E_hv", "E_always_same_structure",
                "F_long_har", "F_long_hv", "F_long_naive", "F_credit_har", "F_credit_hv", "F_credit_naive"]
    headline += [name for name in result.variant if name.startswith("Earlier EOD ")]
    oos = result[result["sample"].eq("oos") | result["sample"].isna()]
    keys = oos.set_index("variant").loc[headline].reset_index()
    extra = oos[~oos.variant.isin(headline)].sort_values(["strategy", "variant"])
    def f(v, fmt):
        return format(v, fmt) if pd.notna(v) else "—"
    def table(frame):
        rows = ["| ID | Variant | Holdout dates | Data quality | Trades | Net | Fees / slip | Drawdown | Expectancy | Return 95% CI | Verdict |",
                "|---|---|---|---|---:|---:|---:|---:|---:|---|---|"]
        for row in frame.itertuples():
            span = f"{row.start} to {row.end}" if pd.notna(row.start) else "—"
            band = f"[{row.ci95_return_low_pct:+.2f}%, {row.ci95_return_high_pct:+.2f}%]" if pd.notna(row.ci95_return_low_pct) else "—"
            rows.append(f"| {row.strategy} | {row.variant} | {span} | {row.data_quality} | {f(row.trades, '.0f')} | "
                        f"{f(row.net_return_pct, '+.2f')} | {f(row.fees_usd, '.2f')} / {f(row.slippage_usd, '.2f')} | "
                        f"{f(row.max_drawdown_pct, '.2f')} | {f(row.expectancy_usd, '+.2f')} | {band} | {row.verdict} |")
        return rows
    lines = ["# Six-strategy verdict", "", "All returns are simulated. The verdict describes reproducibility of each result.",
             "The CSV also includes development, validation and recent-period results.", "", *table(keys),
             "", "## Sensitivities and scope checks", "", *table(extra)]
    lines.extend(["", "Returns, drawdowns and confidence bounds are percent. Fees, slippage and expectancy are USD. "
                  "The option rows embed bid/ask spreads in gross P&L. A0-R uses a retrospective 40-stock universe. "
                  "D's EOD proxy expectancy is per one-contract round trip. The E/F holdout was previously inspected.",
                  "", "The E/F capital-at-risk comparison weights daily SPY returns by average modeled maximum loss. "
                  "SPY and options have different payoff distributions."])
    (OUT / "six_strategy_verdict.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    check_inputs()
    stock = load(OUT / "stock_independent_trades.csv")
    option = load(OUT / "option_independent_trades.csv")
    proxy = load(OUT / "sentiment_proxy_trades.csv")
    abc = check_reproduction(stock, option)
    earlier = load(REPRO / "options_eod/eod_trades.csv")
    earlier["variant"] = "Earlier EOD " + earlier["variant"]
    ledger = pd.concat([
        abc[abc.strategy == "A"].assign(segment="oos", evidence="frozen_simulator"),
        stock.assign(evidence="independent_raw_bar_replay"),
        option.assign(evidence="independent_EOD_quote_replay"),
        proxy.assign(evidence="independent_D_proxy_only"),
        earlier.assign(evidence="frozen_earlier_EOD_proxy"),
    ], ignore_index=True)
    ledger.insert(0, "audit_trade_id", np.arange(1, len(ledger) + 1))
    ledger.to_csv(OUT / "trade_ledger.csv", index=False)
    with (OUT / "trade_ledger.csv").open("rb") as file:
        checksum = hashlib.file_digest(file, "sha256").hexdigest()
    (OUT / "trade_ledger.sha256").write_text(f"{checksum}  trade_ledger.csv\n", encoding="ascii")
    markdown(verdict_rows(abc, stock, option, proxy))


if __name__ == "__main__":
    main()
