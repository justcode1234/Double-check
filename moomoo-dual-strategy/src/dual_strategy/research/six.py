"""Six-strategy (A-F) study. SIMULATED, offline, no broker connection.

    PYTHONUTF8=1 .venv/Scripts/python.exe -m dual_strategy.research.six      (~6 min; needs reports/backtest_v3/)

A/B/C: re-run headline variants on OOS and 2025-26, B0->B1 step attribution, same-date trade reconciliation,
10,000-path day-block Monte Carlo. D/E/F: no historical option chains exist, so their option backtests and empirical
Monte Carlo are BLOCKED; the empirical parts that do not need chains are run: E's forecast calibration (Brier),
F's implied-vs-forecast volatility signal (VIX proxy), D's stated-exit economics."""

from __future__ import annotations

import csv
import gzip
import hashlib
import json
import math
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from dual_strategy.research import run, strat_b
from dual_strategy.research.data import sessions
from dual_strategy.research.execution import Costs, ETF_COSTS, ETF_EXEC
from dual_strategy.research.features import daily_on_calendar
from dual_strategy.research.fetch import END, ROOT
from dual_strategy.research.metrics import metrics
from dual_strategy.research.options import chain, montecarlo, strat_d, strat_e, strat_f
from dual_strategy.research.report import svg_lines

OUT = ROOT / "reports"
SIX = OUT / "six_strategy"
V3 = OUT / "backtest_v3"
EXTRA = ROOT / "data-cache" / "history_extra"
HEADLINE = ["A0-R", "A1", "A1-LO", "B0", "B0-100", "B2", "B1", "B1-LO", "C0", "C1", "C1-LO"]
RECENT = (date(2025, 1, 2), END)
BLOCKS = (1, 2, 4, 8)
HORIZON = 8
N_PATHS = 10_000
NA_OPT = "N/A - missing historical option data"


def days_between(lo: date, hi: date) -> list[date]:
    return [d for d in sessions() if lo <= d <= hi]


def daily_cvar(ret: np.ndarray, q: float) -> float | None:
    if len(ret) < 60:
        return None
    s = np.sort(ret)
    k = max(1, int(q * len(s)))
    return float(100 * s[:k].mean())


# ---------------------------------------------------------------- A/B/C runs
def run_headline(engine, frozen) -> tuple[dict, dict]:
    vs = {v.vid: v for v in run.variants(engine, frozen)}
    oos_days, rec_days = run.seg_days("oos"), days_between(*RECENT)
    oos, rec = {}, {}
    for vid in HEADLINE:
        oos[vid] = engine.run(vs[vid], oos_days)
        rec[vid] = engine.run(vs[vid], rec_days)
        print(f"  ran {vid}", flush=True)
    return oos, rec


def b_steps(engine) -> list[dict]:
    """B0 -> B1 one change at a time (cumulative) and each change alone on top of B0."""
    vs = {v.vid: v for v in run.variants(engine, json.loads((ROOT / "config" / "c_frozen.json").read_text(encoding="utf-8")))}
    b0 = vs["B0"].cfg
    fees_only = Costs("competition_fees_no_slip", per_order=0.99, sell_per_share=0.000166, sell_cap=8.30)
    changes = [("sizing: vol-target (median 2.5x) -> fixed 40%", dict(sizing="fixed", fraction=0.40)),
               ("costs: $0.0035/sh -> $0.99/order + sell fee", dict(costs=fees_only)),
               ("slippage: $0.001/sh -> 1 bp/side", dict(costs=ETF_COSTS)),
               ("history: skip gaps -> strict 14 sessions", dict(strict_history=True)),
               ("exit: 16:00 close -> flatten 15:55", dict(flatten_before_close=5)),
               ("risk: none -> daily loss lock 1.25%", dict(loss_lock=0.0125)),
               ("execution: native -> software profile", dict(exe=ETF_EXEC))]
    out = []
    for seg in ("oos", "full"):
        days = run.seg_days(seg)
        cfg = b0
        base = metrics(engine.run(run.Variant("B0", "B", b0, "x"), days))
        out.append({"segment": seg, "mode": "cumulative", "step": "B0 (published rules)", **_bm(base)})
        for i, (label, kw) in enumerate(changes):
            cfg = replace(cfg, name=f"Bstep{i + 1}", **kw)
            m = metrics(engine.run(run.Variant(cfg.name, "B", cfg, "x"), days))
            out.append({"segment": seg, "mode": "cumulative", "step": f"+ {label}", **_bm(m)})
        for i, (label, kw) in enumerate(changes):
            if label.startswith("slippage"):
                kw = dict(costs=replace(ETF_COSTS, per_order=0, sell_per_share=0, sell_cap=0, per_share=0.0035))
            alone = replace(b0, name=f"Balone{i + 1}", **kw)
            m = metrics(engine.run(run.Variant(alone.name, "B", alone, "x"), days))
            out.append({"segment": seg, "mode": "alone (vs B0)", "step": label, **_bm(m)})
    return out


def _bm(m: dict) -> dict:
    return {k: m.get(k) for k in ("trades", "gross_return_pct", "net_return_pct", "sharpe", "max_drawdown_pct",
                                  "avg_gross_exposure", "fees_usd", "slippage_usd")}


def trade_match(b0: list[dict], b1: list[dict], b2: list[dict]) -> dict:
    def frame(tr):
        df = pd.DataFrame(tr)
        df["key"] = df["date"] + " " + df["entry_time"] + " " + df["side"]
        df["gross_bp"] = 1e4 * np.where(df["side"] == "long", 1, -1) * (df["exit_raw"] / df["entry_raw"] - 1)
        df["cost_bp"] = 1e4 * (df["fees"] + df["slippage_cost"]) / df["notional"]
        return df.set_index("key")
    f0, f1, f2 = frame(b0), frame(b1), frame(b2)
    both = f0.index.intersection(f1.index)
    only0, only1 = f0.index.difference(f1.index), f1.index.difference(f0.index)
    m = f0.loc[both, ["gross_bp", "exit_time", "exit_reason"]].join(f1.loc[both, ["gross_bp", "exit_time", "exit_reason"]],
                                                                   lsuffix="_b0", rsuffix="_b1")
    diff_exit = m[m["exit_time_b0"] != m["exit_time_b1"]]
    r = lambda x: round(float(x), 3)
    return {
        "b0_trades": len(f0), "b1_trades": len(f1), "b2_trades": len(f2),
        "b2_same_signals_as_b0": bool(f2.index.equals(f0.index)),
        "matched_b0_b1": len(both), "b0_only": len(only0), "b1_only": len(only1),
        "b0_only_gross_bp_sum": r(f0.loc[only0, "gross_bp"].sum()), "b1_only_gross_bp_sum": r(f1.loc[only1, "gross_bp"].sum()),
        "matched_gross_bp_mean_b0": r(m["gross_bp_b0"].mean()), "matched_gross_bp_mean_b1": r(m["gross_bp_b1"].mean()),
        "matched_with_different_exit": len(diff_exit),
        "matched_exit_diff_gross_bp_sum": r((diff_exit["gross_bp_b1"] - diff_exit["gross_bp_b0"]).sum()),
        "b0_gross_bp_per_trade": r(f0["gross_bp"].mean()), "b0_cost_bp_per_trade": r(f0["cost_bp"].mean()),
        "b2_cost_bp_per_trade": r(f2["cost_bp"].mean()), "b1_cost_bp_per_trade": r(f1["cost_bp"].mean()),
        "b0_avg_notional": r(f0["notional"].mean()), "b1_avg_notional": r(f1["notional"].mean()),
        "b0_only_reasons_sample": f0.loc[only0].groupby("date").size().sort_values(ascending=False).head(5).to_dict(),
        "b1_exit_reasons": f1["exit_reason"].str.replace(r"flatten_.*", "flatten_15:55", regex=True).value_counts().to_dict(),
        "by_year_net_gross_bp": f0.assign(y=f0["date"].str[:4]).groupby("y")["gross_bp"].agg(["count", "mean"]).round(3).to_dict("index"),
    }


# ---------------------------------------------------------------- Monte Carlo on realised daily returns
def mc_rows(name: str, sample: str, ret: np.ndarray) -> list[dict]:
    rows = []
    for b in BLOCKS:
        risk = montecarlo.path_risk(montecarlo.block_bootstrap_paths(ret, HORIZON, N_PATHS, b))
        rows.append({"strategy": name[0], "variant": name, "sample": sample, "block_days": b, "horizon_sessions": HORIZON,
                     "paths": N_PATHS, "sessions_in_sample": len(ret), **{k: round(v, 4) for k, v in risk.items()}})
    return rows


# ---------------------------------------------------------------- E calibration and F signal studies
def spy_daily_log_returns() -> tuple[list[date], np.ndarray]:
    adj = daily_on_calendar("US.SPY", 1)["close"]
    days = list(adj.index)
    lr = np.log(adj / adj.shift(1)).to_numpy()
    return days, lr


def e_study() -> dict:
    days, lr = spy_daily_log_returns()
    out = {}
    for seg, (lo, hi) in {k: run.SEGMENTS[k] for k in ("dev", "val", "oos")}.items():
        idx = [i for i, d in enumerate(days) if lo <= d <= hi]
        rows = strat_e.calibration_study(lr, days, idx)
        df = pd.DataFrame(rows)
        if df.empty:
            continue
        res = {"decisions": int(df["day"].nunique()), "events": len(df)}
        for model in ("p_mc", "p_normal", "p_hist"):
            res[f"brier_{model[2:]}"] = round(float(((df[model] - df["y"]) ** 2).mean()), 5)
        res["brier_skill_mc_vs_hist"] = round(1 - res["brier_mc"] / res["brier_hist"], 4)
        res["brier_skill_mc_vs_normal"] = round(1 - res["brier_mc"] / res["brier_normal"], 4)
        df["bin"] = pd.cut(df["p_mc"], [0, .2, .4, .6, .8, 1.0], include_lowest=True)
        res["reliability"] = [{"bin": str(k), "n": int(len(g)), "mean_pred": round(float(g["p_mc"].mean()), 3),
                               "realised": round(float(g["y"].mean()), 3)} for k, g in df.groupby("bin", observed=True)]
        res["by_k"] = [{"k": k, "mean_pred": round(float(g["p_mc"].mean()), 3), "realised": round(float(g["y"].mean()), 3)}
                       for k, g in df.groupby("k")]
        out[seg] = res
    return out


def f_study(engine) -> dict:
    spy = engine.spy
    daily = daily_on_calendar("US.SPY", 1).reindex(spy.days)
    rv = strat_f.daily_realized_variance(spy.b1, daily["open"].to_numpy(), daily["close"].to_numpy())
    fc = strat_f.har_forecasts(rv)
    fwd = strat_f.forward_realized_vol(rv)
    naive = np.sqrt(252 * pd.Series(rv).rolling(22).mean().shift(1).to_numpy())
    with gzip.open(EXTRA / "US..VIX_daily.csv.gz", "rt") as fh:
        vix = pd.read_csv(fh)
    vix.index = [datetime.strptime(str(d), "%Y%m%d").date() for d in vix["date"]]
    # VIX known at the close of t-1 -> decision for t
    vix_prev = vix["close"].reindex(spy.days).shift(1).to_numpy() / 100
    df = pd.DataFrame({"day": spy.days, "vix": vix_prev, "har": fc, "naive": naive, "fwd": fwd}).dropna()
    df["spread"] = 100 * (df["vix"] - df["har"])
    df["vrp"] = 100 * (df["vix"] - df["fwd"])          # >0: implied exceeded realised (short vol would have earned it)
    seg = {k: run.SEGMENTS[k] for k in ("dev", "val", "oos")}
    pick = lambda s: df[(df["day"] >= seg[s][0]) & (df["day"] <= seg[s][1])]
    out = {"note": "Signal study only: VIX (30-day SPX implied vol) vs HAR forecast of SPY 21-session realised vol. "
                   "VRP in vol points is NOT option P&L (no bid/ask, skew, SPY-SPX basis, gamma path)."}
    for s in seg:
        x = pick(s)
        rmse = lambda a: round(float(np.sqrt(np.mean((100 * (x[a] - x["fwd"])) ** 2))), 3)
        out[s] = {"days": len(x), "har_rmse_volpts": rmse("har"), "naive22_rmse_volpts": rmse("naive"),
                  "vix_rmse_volpts": rmse("vix"), "mean_vrp_volpts": round(float(x["vrp"].mean()), 3),
                  "share_vrp_positive": round(float((x["vrp"] > 0).mean()), 3),
                  "corr_spread_vrp": round(float(x[["spread", "vrp"]].corr().iloc[0, 1]), 3)}
    dev = pick("dev")
    grid = []
    for rich in strat_f.FConfig().rich_grid:
        sel = dev[dev["spread"] >= rich]
        grid.append({"rich": rich, "days": len(sel), "mean_vrp": round(float(sel["vrp"].mean()), 3) if len(sel) else None})
    elig = [g for g in grid if g["days"] >= 60 and g["mean_vrp"] is not None]
    rich = max(elig, key=lambda g: g["mean_vrp"])["rich"] if elig else None
    cheap_grid = []
    for cheap in strat_f.FConfig().rich_grid:
        sel = dev[dev["spread"] <= -cheap]
        cheap_grid.append({"cheap": cheap, "days": len(sel), "mean_vrp": round(float(sel["vrp"].mean()), 3) if len(sel) else None})
    out["dev_grid_rich"], out["dev_grid_cheap"], out["selected_rich"] = grid, cheap_grid, rich
    for s in ("val", "oos"):
        x = pick(s)
        rs = x[x["spread"] >= rich] if rich is not None else x.iloc[0:0]
        cs = x[x["spread"] <= -2.0]
        out[s].update({"rich_days": len(rs), "rich_mean_vrp": round(float(rs["vrp"].mean()), 3) if len(rs) else None,
                       "naive_always_short_mean_vrp": round(float(x["vrp"].mean()), 3),
                       "incremental_vs_naive": round(float(rs["vrp"].mean() - x["vrp"].mean()), 3) if len(rs) else None,
                       "cheap2_days": len(cs), "cheap2_mean_vrp": round(float(cs["vrp"].mean()), 3) if len(cs) else None})
    hv = json.loads((EXTRA / "US.SPY_moomoo_iv_hv_260.json").read_text(encoding="utf-8"))["volatilities"]
    iv = pd.Series({date.fromisoformat(v["time"]): float(v["iv"]) for v in hv})
    joined = pd.concat([iv.rename("moomoo_spy_iv"), vix["close"].rename("vix")], axis=1).dropna()
    out["vix_vs_moomoo_spy_iv"] = {"overlap_days": len(joined), "corr": round(float(joined.corr().iloc[0, 1]), 3),
                                   "mean_vix_minus_spy_iv": round(float((joined["vix"] - joined["moomoo_spy_iv"]).mean()), 3),
                                   "window": [str(joined.index.min()), str(joined.index.max())]}
    return out


def d_study() -> list[dict]:
    rows = []
    for target in ("cent", "pct"):
        for premium in (0.05, 0.10, 0.50, 2.00, 5.00):
            for n in (1, 10, 100):
                rows.append(strat_d.target_economics(premium, n, target))
    return rows


# ---------------------------------------------------------------- writers
def write_csv(path: Path, rows: list[dict], header_note: str | None = None) -> None:
    cols = list(dict.fromkeys(k for r in rows for k in r))
    with open(path, "w", newline="", encoding="utf-8") as fh:
        if header_note:
            fh.write(f"# {header_note}\n")
        w = csv.DictWriter(fh, cols)
        w.writeheader()
        w.writerows({k: (json.dumps(v) if isinstance(v, (list, dict)) else v) for k, v in r.items()} for r in rows)


def backtest_table(oos: dict, rec: dict) -> list[dict]:
    v3 = pd.read_csv(V3 / "backtest_results.csv")
    keep = ["start", "end", "sessions", "trades", "net_pnl", "net_return_pct", "expectancy_usd", "expectancy_r",
            "win_rate_pct", "avg_win", "avg_loss", "profit_factor", "sharpe", "sharpe_ci95", "sortino", "max_drawdown_pct",
            "avg_gross_exposure", "fees_usd", "slippage_usd", "spy_exposure_matched_pct", "excess_vs_matched_pct",
            "statistically_meaningful"]
    eq = {s: pd.read_csv(V3 / f"equity_curve_{s}.csv", comment="#", index_col="date", parse_dates=True) for s in "ABC"}
    rows = []
    for vid in HEADLINE:
        for seg in ("dev", "val", "oos", "full"):
            r = v3[(v3["variant"] == vid) & (v3["segment"] == seg)]
            if r.empty:
                continue
            row = {"strategy": vid[0], "variant": vid, "period": seg, **{k: r.iloc[0][k] for k in keep}}
            if seg in ("oos",):
                ret = oos[vid].daily["ret"].to_numpy()
            else:
                e = eq[vid[0]][vid]
                lo, hi = run.SEGMENTS[seg]
                ret = (e.pct_change().fillna(e.iloc[0] / 50_000 - 1))[(e.index.date >= lo) & (e.index.date <= hi)].to_numpy()
            row["cvar95_daily_pct"], row["cvar99_daily_pct"] = daily_cvar(ret, .05), daily_cvar(ret, .01)
            rows.append(row)
        m = metrics(rec[vid])
        ret = rec[vid].daily["ret"].to_numpy()
        rows.append({"strategy": vid[0], "variant": vid, "period": "recent_2025_2026", **{k: m.get(k) for k in keep},
                     "cvar95_daily_pct": daily_cvar(ret, .05), "cvar99_daily_pct": daily_cvar(ret, .01)})
    for s, name in (("D", "D0 news-sentiment options (as stated)"), ("E", "E Monte Carlo debit verticals"),
                    ("F", "F IV-vs-forecast defined-risk options")):
        rows.append({"strategy": s, "variant": name, "period": "all",
                     **{k: NA_OPT for k in keep if k not in ("start", "end")}, "start": NA_OPT, "end": NA_OPT,
                     "cvar95_daily_pct": NA_OPT, "cvar99_daily_pct": NA_OPT})
    return rows


def main() -> None:
    SIX.mkdir(parents=True, exist_ok=True)
    if not (V3 / "backtest_results.csv").exists():
        raise SystemExit("run `python -m dual_strategy.research.run` first (reports/backtest_v3/)")
    t0 = datetime.now(timezone.utc)
    engine = run.Engine()
    frozen = run.freeze_c(engine)
    print("A/B/C headline runs (OOS, 2025-26)...", flush=True)
    oos, rec = run_headline(engine, frozen)
    print("B attribution...", flush=True)
    steps = b_steps(engine)
    recon = trade_match(oos["B0"].trades, oos["B1"].trades, oos["B2"].trades)
    print("Monte Carlo...", flush=True)
    mc = []
    for vid in HEADLINE:
        mc += mc_rows(vid, "oos_2024_2026", oos[vid].daily["ret"].to_numpy())
        mc += mc_rows(vid, "recent_2025_2026", rec[vid].daily["ret"].to_numpy())
    vs = {v.vid: v for v in run.variants(engine, frozen)}
    for vid in ("A1-slip0", "A1-slip10", "B1-slip0", "B1-slip5", "C1-slip0", "C1-slip5",
                "A1-stop0.05", "A1-stop0.20", "A1-top4", "A1-top20", "B1-stateful-trail", "B1-hlc3-vwap",
                "C1-expiry3", "C1-expiry12", "C1-turnover-vwap"):
        res = engine.run(vs[vid], run.seg_days("oos"))
        mc += [{**r, "group": "cost" if "slip" in vid else "parameter"} for r in mc_rows(vid, "oos_2024_2026", res.daily["ret"].to_numpy()) if r["block_days"] == 4]
    for s in "DEF":
        mc.append({"strategy": s, "variant": s, "sample": "BLOCKED", "block_days": None, "horizon_sessions": HORIZON,
                   "paths": None, "sessions_in_sample": NA_OPT})
    print("E calibration, F signal, D economics...", flush=True)
    e_res, f_res, d_res = e_study(), f_study(engine), d_study()
    # ---- artifacts
    write_csv(OUT / "backtest_A_to_F.csv", backtest_table(oos, rec),
              "SIMULATED offline backtest. A-C from reports/backtest_v3 (+ 2025-26 re-run here); D-F blocked: no historical option chains.")
    write_csv(OUT / "monte_carlo_A_to_F.csv", mc,
              f"Day-block bootstrap of SIMULATED daily net returns, {N_PATHS} paths x {HORIZON} sessions. D-F BLOCKED (no empirical return series).")
    ledger = []
    for vid in HEADLINE:
        for t in oos[vid].trades:
            ledger.append({"label": "SIMULATED - not a broker order", **t})
    write_csv(OUT / "trade_level_ledger_A_to_F.csv", ledger,
              "OOS 2024-01-02..2026-11-17, every SIMULATED fill of A/B/C headline variants. D/E/F: no rows (no historical option data). "
              "Rejected/missed signals are counted per day in backtest_v3, not logged per signal.")
    write_csv(SIX / "B_step_attribution.csv", steps)
    write_csv(SIX / "D0_exit_economics.csv", d_res)
    (SIX / "B_trade_reconciliation.json").write_text(json.dumps(recon, indent=1, default=str), encoding="utf-8")
    (SIX / "E_calibration.json").write_text(json.dumps(e_res, indent=1, default=str), encoding="utf-8")
    (SIX / "F_signal_study.json").write_text(json.dumps(f_res, indent=1, default=str), encoding="utf-8")
    # charts: OOS drawdown and MC distribution
    dd = {}
    for vid in ("A1", "B0", "B2", "B1", "C1"):
        d = oos[vid].daily
        e = np.cumprod(1 + d["ret"].to_numpy())
        dd[vid] = pd.Series(100 * (e / np.maximum.accumulate(e) - 1), index=d.index)
    (SIX / "drawdown_oos.svg").write_text(svg_lines(dd, "OOS drawdown from peak - SIMULATED", "Drawdown (%)"), encoding="utf-8")
    mc_svg(SIX / "mc_8session_distribution.svg", [r for r in mc if r.get("block_days") == 4 and r["sample"] == "oos_2024_2026"
                                                 and r["variant"] in ("A1", "B0", "B2", "B1", "C1")])
    manifest = {"generated_at_utc": t0.isoformat(timespec="seconds"), "seed": 20261119, "paths": N_PATHS,
                "horizon": HORIZON, "blocks": BLOCKS, "freeze": json.loads((ROOT / "config" / "six_strategy_freeze.json").read_text(encoding="utf-8"))["sha256"],
                "extra_data": json.loads((EXTRA / "manifest.json").read_text(encoding="utf-8"))["sha256"],
                "chain_status": _chain_status(), "command": "PYTHONUTF8=1 .venv/Scripts/python.exe -m dual_strategy.research.six"}
    (SIX / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    print(f"done in {(datetime.now(timezone.utc) - t0).seconds}s", flush=True)


def _chain_status() -> str:
    try:
        chain.load_chains("US.SPY")
        return "present"
    except chain.OptionsDataBlocked as exc:
        return str(exc)


def mc_svg(path: Path, rows: list[dict]) -> None:
    w, h, ml, mt, rowh = 760, 60 + 46 * len(rows), 90, 40, 46
    lo = min(r["cvar99_pct"] for r in rows) - 0.5
    hi = max(r["p95_pct"] for r in rows) + 0.5
    X = lambda v: ml + (v - lo) / (hi - lo) * (w - ml - 30)
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" font-family="sans-serif" font-size="11">',
           f'<rect width="{w}" height="{h}" fill="white"/>',
           f'<text x="{w / 2}" y="20" text-anchor="middle" font-size="13">8-session return, 10,000 block-bootstrap paths (OOS) - SIMULATED</text>',
           f'<line x1="{X(0):.1f}" y1="{mt - 6}" x2="{X(0):.1f}" y2="{h - 24}" stroke="#555"/>']
    for i, r in enumerate(rows):
        y = mt + i * rowh + 14
        out.append(f'<text x="{ml - 10}" y="{y + 4}" text-anchor="end">{r["variant"]}</text>')
        out.append(f'<line x1="{X(r["p5_pct"]):.1f}" y1="{y}" x2="{X(r["p95_pct"]):.1f}" y2="{y}" stroke="#2a78d6" stroke-width="6"/>')
        out.append(f'<circle cx="{X(r["median_pct"]):.1f}" cy="{y}" r="5" fill="#111"/>')
        out.append(f'<text x="{X(r["p95_pct"]) + 6:.1f}" y="{y + 4}" fill="#444">median {r["median_pct"]:+.2f}%  '
                   f'P(profit) {100 * r["p_profit"]:.0f}%  P(&lt;-3%) {100 * r["p_loss_gt_3pct"]:.1f}%</text>')
    out.append(f'<text x="{ml}" y="{h - 8}" fill="#666">bar = 5th-95th percentile, dot = median; vertical line = 0%</text></svg>')
    path.write_text("\n".join(out), encoding="utf-8")


if __name__ == "__main__":
    main()
