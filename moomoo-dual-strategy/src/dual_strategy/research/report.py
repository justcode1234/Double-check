"""Write every backtest artifact into reports/ (CSV, JSON, markdown, hand-built SVG). Never overwrites prior audits:
all names here are new (backtest_*, trades_*, equity_curve_*, walk_forward_results, ablations, final_backtest_audit)."""

from __future__ import annotations

import csv
import hashlib
import json
import math
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from dual_strategy.research.data import (HISTORY, dividends, file_hashes, load_daily, sector_map, split_events,
                                         verify_splits)
from dual_strategy.research.fetch import END, ROOT
from dual_strategy.research.metrics import (block_bootstrap, drawdown_ladder, period_table, regime_table,
                                            spy_benchmark)
from dual_strategy.research.portfolio import START_EQUITY

HEADLINE = {"A": ["A0-R", "A1", "A1-LO"], "B": ["B0", "B0-100", "B1", "B1-LO"], "C": ["C0", "C1", "C1-LO"]}
PRIMARY = {"A": "A1", "B": "B1", "C": "C1"}
COLORS = ["#1f77b4", "#d62728", "#2ca02c", "#9467bd", "#7f7f7f"]
SEG_ORDER = ("dev", "val", "oos", "full")


def _fmt(x, nd=3):
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return "N/A"
    if isinstance(x, float):
        return f"{x:.{nd}f}"
    return str(x)


def _table(rows: list[dict], cols: list[str]) -> str:
    head = "| " + " | ".join(cols) + " |\n|" + "---|" * len(cols) + "\n"
    return head + "".join("| " + " | ".join(_fmt(r.get(c)) for c in cols) + " |\n" for r in rows)


def _sha(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def _row(rows, vid, seg):
    return next((r for r in rows if r["variant"] == vid and r["segment"] == seg), {})


# ---------------------------------------------------------------- SVG
def svg_lines(series: dict[str, pd.Series], title: str, ylabel: str, marks: dict[str, date] | None = None) -> str:
    w, h, ml, mr, mt, mb = 980, 430, 70, 20, 40, 70
    allv = np.concatenate([s.to_numpy(dtype=float) for s in series.values() if len(s)])
    lo, hi = float(np.nanmin(allv)), float(np.nanmax(allv))
    if hi - lo < 1e-9:
        hi, lo = hi + 1, lo - 1
    pad = 0.05 * (hi - lo)
    lo, hi = lo - pad, hi + pad
    days = sorted({d for s in series.values() for d in s.index})
    d0, d1 = days[0].toordinal(), days[-1].toordinal()
    span = max(1, d1 - d0)
    X = lambda d: ml + (d.toordinal() - d0) / span * (w - ml - mr)
    Y = lambda v: mt + (hi - v) / (hi - lo) * (h - mt - mb)
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" font-family="sans-serif" font-size="11">',
           f'<rect width="{w}" height="{h}" fill="white"/>',
           f'<text x="{w / 2}" y="20" text-anchor="middle" font-size="14">{title}</text>',
           f'<text x="14" y="{h / 2}" transform="rotate(-90 14 {h / 2})" text-anchor="middle">{ylabel}</text>']
    for i in range(6):
        v = lo + (hi - lo) * i / 5
        y = Y(v)
        out.append(f'<line x1="{ml}" y1="{y:.1f}" x2="{w - mr}" y2="{y:.1f}" stroke="#eee"/>'
                   f'<text x="{ml - 6}" y="{y + 4:.1f}" text-anchor="end">{v:,.0f}</text>')
    for yr in range(days[0].year, days[-1].year + 1):
        d = date(yr, 1, 1)
        if days[0] <= d <= days[-1]:
            x = X(d)
            out.append(f'<line x1="{x:.1f}" y1="{mt}" x2="{x:.1f}" y2="{h - mb}" stroke="#eee"/>'
                       f'<text x="{x:.1f}" y="{h - mb + 14}" text-anchor="middle">{yr}</text>')
    for name, d in (marks or {}).items():
        if days[0] <= d <= days[-1]:
            x = X(d)
            out.append(f'<line x1="{x:.1f}" y1="{mt}" x2="{x:.1f}" y2="{h - mb}" stroke="#999" stroke-dasharray="4,3"/>'
                       f'<text x="{x + 3:.1f}" y="{mt + 10}" fill="#666">{name}</text>')
    if lo < START_EQUITY < hi and ylabel.startswith("Equity"):
        y = Y(START_EQUITY)
        out.append(f'<line x1="{ml}" y1="{y:.1f}" x2="{w - mr}" y2="{y:.1f}" stroke="#444" stroke-width="0.6"/>')
    for i, (name, s) in enumerate(series.items()):
        pts = " ".join(f"{X(d):.1f},{Y(v):.1f}" for d, v in s.items() if math.isfinite(v))
        col = COLORS[i % len(COLORS)]
        out.append(f'<polyline fill="none" stroke="{col}" stroke-width="1.3" points="{pts}"/>')
        lx = ml + 10 + 190 * i
        out.append(f'<rect x="{lx}" y="{h - 30}" width="12" height="3" fill="{col}"/>'
                   f'<text x="{lx + 16}" y="{h - 25}">{name}</text>')
    out.append(f'<rect x="{ml}" y="{mt}" width="{w - ml - mr}" height="{h - mt - mb}" fill="none" stroke="#333"/>')
    out.append(f'<text x="{w - mr}" y="{h - 8}" text-anchor="end" fill="#666">SIMULATED - offline historical backtest, '
               f'not live results</text></svg>')
    return "\n".join(out)


def svg_heatmap(mat: pd.DataFrame, title: str) -> str:
    n = len(mat)
    cell, ml, mt = 70, 110, 50
    w, h = ml + cell * n + 20, mt + cell * n + 40
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" font-family="sans-serif" font-size="12">',
           f'<rect width="{w}" height="{h}" fill="white"/>',
           f'<text x="{w / 2}" y="22" text-anchor="middle" font-size="14">{title}</text>']
    for i, a in enumerate(mat.index):
        out.append(f'<text x="{ml - 8}" y="{mt + cell * i + cell / 2 + 4}" text-anchor="end">{a}</text>')
        out.append(f'<text x="{ml + cell * i + cell / 2}" y="{mt - 8}" text-anchor="middle">{a}</text>')
        for j, b in enumerate(mat.columns):
            v = float(mat.iloc[i, j])
            r, g, bl = (255, int(255 * (1 - v)), int(255 * (1 - v))) if v >= 0 else (int(255 * (1 + v)), int(255 * (1 + v)), 255)
            out.append(f'<rect x="{ml + cell * j}" y="{mt + cell * i}" width="{cell}" height="{cell}" '
                       f'fill="rgb({r},{g},{bl})" stroke="white"/>'
                       f'<text x="{ml + cell * j + cell / 2}" y="{mt + cell * i + cell / 2 + 4}" text-anchor="middle">{v:.2f}</text>')
    out.append(f'<text x="{w / 2}" y="{h - 12}" text-anchor="middle" fill="#666">Pearson correlation of SIMULATED daily '
               f'net returns</text></svg>')
    return "\n".join(out)


# ---------------------------------------------------------------- data inventory
def data_inventory(engine, hashes: dict) -> str:
    lines = ["# Data inventory (offline backtest, generated)\n",
             f"Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')}. Source: Moomoo OpenAPI via `moomoo` "
             f"CLI v0.5.0 `history-kline` (quote OAuth), cached under `data-cache/history/` (not in Git). "
             f"Window {hashes.get('start')} .. {hashes.get('end')} (first 2 months = indicator warm-up only).\n",
             "Intraday bars are RAW (autype 0), regular session only (extended_time 0); Moomoo `time_key` = bar END. "
             "Daily bars fetched raw (autype 0) and forward-adjusted (autype 1). No paid data was bought; nothing was "
             "interpolated or filled: a missing bar stays missing (stale data) and a session with any missing bar is "
             "flagged incomplete.\n"]
    rows = []
    syms = ["US.SPY", *engine.ranked]
    for sym in syms:
        is_etf = sym == "US.SPY"
        bars = engine.spy.b1 if is_etf else engine.stocks[sym].b5
        exp = bars.expected > 0
        has = bars.has_any()
        comp = bars.complete()
        daily = load_daily(sym, 0)
        first = next((d for d, x in zip(bars.days, has) if x), None)
        live = np.array([first is not None and d >= first for d in bars.days])
        rows.append({"symbol": sym.replace("US.", ""), "bars": "1m" if is_etf else "5m",
                     "first_intraday": first.isoformat() if first else None,
                     "sessions_with_data": int(has.sum()), "complete_sessions": int(comp.sum()),
                     "missing_sessions_after_first": int((live & exp & ~has).sum()),
                     "incomplete_sessions": int((has & ~comp).sum()),
                     "bars_out_of_rth_dropped": bars.out_of_rth, "duplicate_bars_dropped": bars.duplicates,
                     "daily_rows": len(daily), "sector": "ETF" if is_etf else engine.stocks[sym].sector})
    lines.append("## Coverage\n")
    lines.append(_table(rows, list(rows[0])))
    if engine.missing_stocks:
        lines.append(f"\nUniverse names with NO 5m history (excluded): {', '.join(engine.missing_stocks)}\n")
    lines.append("\n## Corporate actions\n")
    lines.append("Splits come from `moomoo corporate-actions` and are verified against every >3% jump of the raw/"
                 "adjusted daily close ratio (`verify_splits`). Intraday prices stay raw; only cross-session features "
                 "(ATR14, ADV14, RVOL baseline) are split-adjusted, using splits between the two sessions only.\n")
    srows = []
    for sym in syms:
        v = verify_splits(sym)
        ev = [f"{d} x{f:.4g}" for d, f in split_events(sym) if d <= END]
        srows.append({"symbol": sym.replace("US.", ""), "splits_listed": "; ".join(ev) or "-",
                      "unmatched_observed": json.dumps(v["unmatched_observed"]) if v["unmatched_observed"] else "-",
                      "unmatched_listed": json.dumps(v["unmatched_listed"]) if v["unmatched_listed"] else "-",
                      "verified": "PASS" if v["ok"] else "CHECK"})
    lines.append(_table(srows, list(srows[0])))
    div = dividends("US.SPY")
    lines.append(f"\nSPY dividends in window: {sum(1 for d in div if d <= END)} ex-dates "
                 "(subtracted from the prior close for the Noise-Area gap, as in the paper).\n")
    lines.append("\n## Known data limitations\n")
    lines.append("- **No point-in-time stock universe.** The 40 stocks are today's top Moomoo turnover names "
                 "(`config/research_universe.json`), i.e. survivorship- and look-ahead-biased. Faithful A0 is "
                 "BLOCKED - DATA; every A result is RESTRICTED-UNIVERSE EXPLORATORY.\n"
                 "- Market-cap >= $1B (spec) is not checked historically (no point-in-time market caps).\n"
                 "- Sector labels are today's Moomoo INDUSTRY plate, not point-in-time.\n"
                 "- Stock bars are 5-minute only: intrabar order of entry vs stop is unknown (handled pessimistically, "
                 "with an optimistic ablation).\n"
                 "- QQQ intraday was not fetched (optional; skipped to save API budget). QQQ variants NOT RUN.\n"
                 "- Quote bars, not trades/quotes: no bid/ask, so spread is modelled through bps slippage.\n")
    entries = hashes.get("sha256", {})
    lines.append(f"\n## Hashes\n\n`data-cache/history/manifest.json`: {len(entries)} files, content hash "
                 f"(sha256 of the per-file sha256 map) `{_sha(entries)}`.\n")
    return "\n".join(lines)


# ---------------------------------------------------------------- gates
def gates(strategy: str, vid: str, rows: list[dict], wf_summary: dict, frozen: dict) -> list[tuple[str, str, str]]:
    oos, val = _row(rows, vid, "oos"), _row(rows, vid, "val")
    g = []
    if strategy == "A":
        g.append(("data", "BLOCKED" if vid.startswith("A0") else "EXPLORATORY",
                  "no point-in-time universe; 40 present-day names (survivorship bias)"))
    else:
        gap = oos.get("data_gap_sessions")
        g.append(("data", "PASS" if gap == 0 else "FAIL", f"SPY 1m, OOS gap sessions = {gap}"))
    n_ok = (oos.get("sessions") or 0) >= 252 and (oos.get("trades") or 0) >= 30
    g.append(("sample", "PASS" if n_ok else "FAIL", f"OOS sessions {oos.get('sessions')}, trades {oos.get('trades')}"))
    sh, p = oos.get("sharpe"), oos.get("p_bootstrap_sharpe_le_0")
    g.append(("oos_sharpe", "PASS" if sh is not None and sh > 0 and p is not None and p < 0.05 else "FAIL",
              f"OOS Sharpe {_fmt(sh)}, CI95 {oos.get('sharpe_ci95')}, bootstrap P(Sharpe<=0) {_fmt(p, 4)}"))
    vr = val.get("net_return_pct")
    g.append(("validation_2023", "PASS" if vr is not None and vr > 0 else "FAIL", f"2023 net {_fmt(vr)}%"))
    if vid == PRIMARY[strategy]:
        s10 = _row(rows, f"{vid}-slip10", "oos").get("net_return_pct")
        g.append(("cost_10bps", "PASS" if s10 is not None and s10 > 0 else "FAIL", f"OOS net at 10 bps {_fmt(s10)}%"))
        st = _row(rows, f"{vid}-combined-slip10", "oos").get("net_return_pct")
        g.append(("stress", "PASS" if st is not None and st > 0 else "FAIL",
                  f"OOS net, stressed stops + 1-bar delay + 2% rejects + 2% misses + 10 bps: {_fmt(st)}%"))
    ex = oos.get("excess_vs_matched_pct")
    g.append(("vs_exposure_matched_spy", "PASS" if ex is not None and ex > 0 else "FAIL",
              f"OOS excess vs exposure-matched SPY {_fmt(ex)} pp"))
    dd = oos.get("max_drawdown_pct")
    g.append(("oos_drawdown_<=5%", "PASS" if dd is not None and dd >= -5 else "FAIL", f"OOS max DD {_fmt(dd)}%"))
    if vid in wf_summary:
        w = wf_summary[vid]
        ok = w["stitched_return_pct"] > 0 and w["positive_share"] >= 0.6
        g.append(("walk_forward", "PASS" if ok else "FAIL",
                  f"{w['windows']} half-year test windows 2023+, {100 * w['positive_share']:.0f}% positive, "
                  f"stitched {w['stitched_return_pct']:.2f}%" + (" (C re-selected per fold)" if w.get("reselected") else "")))
    if strategy == "C":
        g.append(("development_2021_22", "PASS" if frozen["development_pass"] else "FAIL",
                  f"best eligible DEV grid Sharpe > 0: {frozen['development_pass']}; frozen {frozen['selected']}"))
    return g


def verdict(g: list[tuple[str, str, str]]) -> str:
    states = {s for _, s, _ in g}
    if "FAIL" in states:
        return "NO-GO"
    if "BLOCKED" in states or "EXPLORATORY" in states:
        return "NO-GO (exploratory only)"
    return "GO (paper-trading candidate only)"


# ---------------------------------------------------------------- walk-forward
def half_year_windows(res, start=date(2023, 1, 1)) -> list[dict]:
    d = res.daily
    out = []
    for yr in range(start.year, END.year + 1):
        for h in (1, 2):
            lo, hi = date(yr, 1 if h == 1 else 7, 1), date(yr, 6, 30) if h == 1 else date(yr, 12, 31)
            sub = d[(d.index >= lo) & (d.index <= hi)]
            if not len(sub):
                continue
            r = sub["ret"].to_numpy()
            sd = r.std(ddof=1) if len(r) > 1 else 0
            out.append({"window": f"{yr}H{h}", "sessions": len(r), "trades": int(sub["trades"].sum()),
                        "net_return_pct": round(100 * (float(np.prod(1 + r)) - 1), 3),
                        "sharpe": round(float(r.mean() / sd * math.sqrt(252)), 3) if sd > 0 and len(r) > 20 else None})
    return out


def write_all(out: Path, *, engine, variants, rows, full, frozen, wf, segments, command, commit, python, elapsed,
              partial) -> None:
    out.mkdir(parents=True, exist_ok=True)
    strategies = sorted({v.strategy for v in variants})
    hashes = file_hashes()
    cfg_hash = _sha({v.vid: full[v.vid].config for v in variants if v.vid in full})
    # results
    cols = list(dict.fromkeys(k for r in rows for k in r))
    with open(out / "backtest_results.csv", "w", newline="") as fh:
        wr = csv.DictWriter(fh, cols)
        wr.writeheader()
        for r in rows:
            wr.writerow({k: (json.dumps(v) if isinstance(v, list) else v) for k, v in r.items()})
    breakdowns = {}
    for vid, res in full.items():
        if any(vid in HEADLINE[s] for s in HEADLINE):
            breakdowns[vid] = {"by_year": period_table(res, "Y").reset_index().to_dict("records"),
                               "by_quarter": period_table(res, "Q").reset_index().to_dict("records"),
                               "by_regime": regime_table(res).to_dict("records"),
                               "competition_drawdown_ladder_not_applied": drawdown_ladder(res)}
    # walk-forward
    wf_summary, wf_tables = {}, {}
    for s in strategies:
        for vid in HEADLINE[s]:
            if vid not in full:
                continue
            win = half_year_windows(full[vid])
            wf_tables[vid] = win
            r = full[vid].daily
            r = r[r.index >= date(2023, 1, 1)]["ret"].to_numpy()
            wf_summary[vid] = {"windows": len(win), "positive_share": float(np.mean([w["net_return_pct"] > 0 for w in win])) if win else 0.0,
                               "stitched_return_pct": 100 * (float(np.prod(1 + r)) - 1)}
    if wf:
        r = np.concatenate([np.array(f["returns"]) for f in wf])
        wf_summary["C1"] = {"windows": len(wf), "positive_share": float(np.mean([f["test_net_return_pct"] > 0 for f in wf])),
                            "stitched_return_pct": 100 * (float(np.prod(1 + r)) - 1), "reselected": True,
                            "bootstrap": block_bootstrap(r)}
    # correlations
    prim = [PRIMARY[s] for s in strategies if PRIMARY[s] in full]
    corr = {}
    if prim:
        rets = pd.DataFrame({vid: full[vid].daily["ret"] for vid in prim})
        rets["SPY"] = spy_benchmark(list(rets.index)).to_numpy()
        for seg in ("full", "oos"):
            lo, hi = (date(2021, 1, 4), END) if seg == "full" else (date(2024, 1, 2), END)
            sub = rets[(rets.index >= lo) & (rets.index <= hi)]
            corr[seg] = sub.corr().round(3)
        if "B1" in rets and "C1" in rets:
            both = rets[rets.index >= date(2024, 1, 2)]
            corr["oos_active_days_BC"] = int(((full["B1"].daily["trades"] > 0) & (full["C1"].daily["trades"] > 0))
                                             .reindex(both.index).sum())
        (out / "backtest_correlation.svg").write_text(svg_heatmap(corr["oos"], "OOS 2024-2026 daily-return correlation"))
    # json
    payload = {"label": "SIMULATED offline historical backtest - not live or paper-traded results",
               "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "partial_run": partial, "segments": {k: [a.isoformat(), b.isoformat()] for k, (a, b) in segments.items()},
               "results": rows, "breakdowns": breakdowns, "walk_forward": {"c_reselected_folds": [
                   {k: v for k, v in f.items() if k not in ("returns", "days")} for f in wf],
                   "half_year_windows": wf_tables, "summary": wf_summary},
               "correlation": {k: (v.to_dict() if isinstance(v, pd.DataFrame) else v) for k, v in corr.items()},
               "c_frozen": {k: v for k, v in frozen.items() if k != "grid_development_results"}}
    (out / "backtest_results.json").write_text(json.dumps(payload, indent=1, default=str))
    # trades + equity curves
    for s in strategies:
        heads = [v for v in HEADLINE[s] if v in full]
        trades = [t for vid in heads for t in full[vid].trades]
        tcols = list(dict.fromkeys(k for t in trades for k in t)) or ["label"]
        with open(out / f"trades_{s}.csv", "w", newline="") as fh:
            fh.write("# SIMULATED trades from an offline historical backtest (full window 2021-01-04..2026-11-17). "
                     "Not real or paper fills.\n")
            wr = csv.DictWriter(fh, tcols)
            wr.writeheader()
            wr.writerows(trades)
        eq = pd.DataFrame({vid: START_EQUITY * np.cumprod(1 + full[vid].daily["ret"].to_numpy()) for vid in heads},
                          index=full[heads[0]].daily.index)
        spy = spy_benchmark(list(eq.index)).to_numpy()
        eq["SPY_buy_hold"] = START_EQUITY * np.cumprod(1 + spy)
        w = float(full[PRIMARY[s]].daily["exposure"].mean()) if PRIMARY[s] in full else 1.0
        eq[f"SPY_matched_x{w:.3f}"] = START_EQUITY * np.cumprod(1 + w * spy)
        with open(out / f"equity_curve_{s}.csv", "w") as fh:
            fh.write(f"# SIMULATED equity, $50,000 start, independent portfolio, net of modelled fees/slippage. "
                     f"SPY_matched = SPY total return scaled by {PRIMARY[s]}'s mean gross exposure\n")
            eq.round(2).to_csv(fh, index_label="date")
        series = {c: eq[c] for c in eq.columns if c in heads[:3] or c.startswith("SPY_matched")}
        (out / f"equity_curve_{s}.svg").write_text(svg_lines(series, f"Strategy {s} - SIMULATED equity (full window)",
                                                             "Equity ($)", {"VAL": date(2023, 1, 3), "OOS": date(2024, 1, 2)}))
    if prim:
        cmp = {vid: pd.Series(START_EQUITY * np.cumprod(1 + full[vid].daily["ret"].to_numpy()), index=full[vid].daily.index)
               for vid in prim}
        (out / "equity_curve_comparison.svg").write_text(svg_lines(cmp, "A1 vs B1 vs C1 - SIMULATED equity", "Equity ($)",
                                                                   {"VAL": date(2023, 1, 3), "OOS": date(2024, 1, 2)}))
    # walk-forward md
    md = ["# Walk-forward results (SIMULATED)\n",
          "A and B have no fitted parameters (published rules), so their walk-forward is a stability check: the same "
          "frozen rule on consecutive half-year windows from 2023 (validation + OOS). C is re-selected on each trailing "
          "2-year window with the frozen selection rule and tested on the following 6 months, never seeing its test data.\n"]
    if wf:
        md.append("## C - rolling re-selection (2y train / 6m test)\n")
        md.append(_table([{**{k: v for k, v in f.items() if k not in ("returns", "days", "selected")},
                           "band/stop/hold": "/".join(str(x) for x in f["selected"].values())} for f in wf],
                         ["train", "test", "band/stop/hold", "test_trades", "test_net_return_pct", "test_sharpe"]))
        cs = wf_summary["C1"]
        md.append(f"\nStitched fold OOS: {cs['stitched_return_pct']:.2f}%, {100 * cs['positive_share']:.0f}% of folds "
                  f"positive, bootstrap Sharpe CI95 {cs['bootstrap']['sharpe_ci']}.\n")
    for vid, win in wf_tables.items():
        md.append(f"\n## {vid} - half-year windows\n")
        md.append(_table(win, ["window", "sessions", "trades", "net_return_pct", "sharpe"]))
    (out / "walk_forward_results.md").write_text("\n".join(md))
    # ablations md
    keep = ["variant", "segment", "trades", "net_return_pct", "expectancy_usd", "expectancy_r", "sharpe",
            "max_drawdown_pct", "win_rate_pct", "fees_usd", "slippage_usd", "rejected_orders", "missed_signals",
            "partial_fills"]
    md = ["# Ablations, slippage and latency stress (SIMULATED)\n",
          "Every row is an independent $50,000 portfolio. Ablations are diagnostics, not candidates: none was used to "
          "pick a rule, except the C development grid (2021-2022 only), listed last.\n"]
    for grp, title in (("headline", "Headline variants"), ("ablation", "Ablations"), ("slippage", "Slippage 0/2/5/10/15 bps"),
                       ("stress", "Execution stress")):
        sub = [r for r in rows if r["group"] == grp and r["segment"] in ("oos", "full")]
        if sub:
            md.append(f"\n## {title} (OOS 2024-01-02..{END} and full 2021-{END.year})\n")
            md.append(_table(sub, keep))
    md.append("\n## C development grid (2021-2022 only, competition sizing, 5 bps)\n")
    md.append(_table(frozen["grid_development_results"], ["band", "stop_atr", "hold", "trades", "sharpe", "net_return_pct"]))
    md.append(f"\nSelection rule: {frozen['selection_rule']}\n\nFrozen: {frozen['selected']} "
              f"(sha256 `{frozen['sha256']}`, `config/c_frozen.json`).\n")
    (out / "ablations.md").write_text("\n".join(md))
    (out / "backtest_breakdowns.md").write_text(_breakdowns_md(breakdowns))
    if not partial:
        (out / "data_inventory.md").write_text(data_inventory(engine, hashes))
    manifest = {"command": command, "git_commit": commit, "python": python, "elapsed_seconds": round(elapsed, 1),
                "data_manifest_content_sha256": _sha(hashes.get("sha256", {})), "data_files": len(hashes.get("sha256", {})),
                "config_sha256": cfg_hash, "c_frozen_sha256": frozen["sha256"],
                "universe_sha256": hashlib.sha256((ROOT / "config" / "research_universe.json").read_bytes()).hexdigest(),
                "numpy": np.__version__, "pandas": pd.__version__, "variants": len(variants)}
    (out / "backtest_run_manifest.json").write_text(json.dumps(manifest, indent=1))
    if not partial:
        (out / "final_backtest_audit.md").write_text(_audit_md(rows, wf_summary, frozen, corr, manifest, engine))


def _breakdowns_md(b: dict) -> str:
    md = ["# Year / quarter / regime breakdowns (SIMULATED, full window)\n",
          "Regimes are ex-post SPY labels for reporting only: trend day = |close-open| >= 50% of the day range; "
          "high vol = prior 14-day SPY vol above its sample median; bull = prior close above prior 200-day SMA.\n"]
    for vid, x in b.items():
        md.append(f"\n## {vid}\n\n### By year\n")
        md.append(_table(x["by_year"], list(x["by_year"][0]) if x["by_year"] else []))
        md.append("\n### By regime\n")
        md.append(_table(x["by_regime"], list(x["by_regime"][0]) if x["by_regime"] else []))
        md.append("\n### Competition drawdown ladder (NOT applied to multi-year runs; when it would fire)\n")
        md.append(_table([{"metric": k, "value": v} for k, v in x["competition_drawdown_ladder_not_applied"].items()],
                         ["metric", "value"]))
        md.append("\n### By quarter\n")
        md.append(_table(x["by_quarter"], list(x["by_quarter"][0]) if x["by_quarter"] else []))
    return "\n".join(md)


def _audit_md(rows, wf_summary, frozen, corr, manifest, engine) -> str:
    md = ["# Final backtest audit - strategies A, B, C (SIMULATED, offline)\n",
          f"Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')}. This is a historical simulation on "
          "Moomoo bars. No orders were sent and no account was touched. It does not replace `final_audit.md`.\n",
          f"Segments: development 2021-01-04..2022-12-30, validation 2023, untouched OOS 2024-01-02..{END}. "
          "C rules were frozen from development data before any validation/OOS simulation of C.\n",
          "Gate legend: PASS / FAIL / BLOCKED (missing data makes the faithful test impossible) / EXPLORATORY "
          "(runs, but on biased data, so it cannot support a GO).\n"]
    head_cols = ["variant", "trades", "net_return_pct", "expectancy_usd", "expectancy_r", "sharpe", "sharpe_ci95",
                 "max_drawdown_pct", "avg_gross_exposure", "excess_vs_matched_pct", "start", "end"]
    heads = [r for r in rows if r["group"] == "headline" and r["segment"] == "oos"]
    md.append("## Headline OOS results\n")
    md.append(_table(heads, head_cols))
    md.append("\n## Gates\n")
    verdicts = {}
    for s in ("A", "B", "C"):
        for vid in HEADLINE[s]:
            if not _row(rows, vid, "oos"):
                continue
            g = gates(s, vid, rows, wf_summary, frozen)
            verdicts[vid] = verdict(g)
            md.append(f"\n### {vid} - {verdicts[vid]}\n")
            md.append(_table([{"gate": a, "status": b, "evidence": c} for a, b, c in g], ["gate", "status", "evidence"]))
    if "oos" in corr:
        md.append("\n## Diversification (OOS daily net returns)\n")
        c = corr["oos"]
        md.append(_table([{"": i, **c.loc[i].to_dict()} for i in c.index], ["", *c.columns]))
    md.append("\n## Verdict summary\n")
    md.append(_table([{"variant": k, "verdict": v} for k, v in verdicts.items()], ["variant", "verdict"]))
    md.append("\nA GO here would only mean 'eligible for supervised paper trading under the existing authorization'; "
              "it is never a promise of future returns.\n")
    md.append("\n## Not done / blocked\n\n- A0 faithful replication: BLOCKED - DATA (no point-in-time universe, "
              "no market-cap history, no 1-minute stock bars).\n- QQQ variants: NOT RUN (optional; intraday not fetched).\n"
              f"- Stocks without history: {', '.join(engine.missing_stocks) or 'none'}.\n")
    md.append(f"\n## Reproduce\n\n```\n{manifest['command']}\n```\n\nGit `{manifest['git_commit']}`, data content "
              f"sha256 `{manifest['data_manifest_content_sha256']}` ({manifest['data_files']} files), config sha256 "
              f"`{manifest['config_sha256']}`, C freeze sha256 `{manifest['c_frozen_sha256']}`.\n")
    return "\n".join(md)
