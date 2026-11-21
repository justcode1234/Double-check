"""Summarize results/grid.csv into results/summary.csv and summary.md (dollars per week on a $100,000 book)."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

OUT = Path("reports/claude_regime/results")


def main():
    g = pd.concat([pd.read_csv(f) for f in sorted(OUT.glob("grid_*.csv"))], ignore_index=True)
    rows = []
    for name, d in g.groupby("strategy", sort=False):
        d = d.copy()
        best_train = d.sort_values("train_mean", ascending=False).iloc[0]
        best_vix = d.sort_values("vix25_mean", ascending=False).iloc[0]
        rows.append({
            "strategy": name, "settings": len(d),
            "settings_positive_pct": round(100 * (d.all_mean > 0).mean()),
            "median_setting_week": round(d.all_mean.median(), 1),
            "picked_params": best_train.params,
            "picked_train_week": best_train.train_mean, "picked_test_week": best_train.test_mean,
            "picked_test_pos_weeks_pct": best_train.test_pos_pct, "picked_test_worst_week": best_train.test_worst,
            "picked_test_best_week": best_train.test_best,
            "picked_vix25_week": best_train.vix25_mean, "vix25_weeks": best_train.vix25_weeks,
            "picked_vix30_week": best_train.vix30_mean, "vix30_weeks": best_train.vix30_weeks,
            "picked_last_week": best_train.lastweek_total, "picked_trades_per_week": best_train.trades_per_week,
            "best_vix25_params": best_vix.params, "best_vix25_week": best_vix.vix25_mean,
            "best_vix25_test_week": best_vix.test_mean,
        })
    s = pd.DataFrame(rows)
    s.to_csv(OUT / "summary.csv", index=False)
    lines = ["| Strategy | Settings tried | % settings profitable | Typical setting $/wk | Picked on 2021-24: $/wk then | "
             "→ 2025-26 $/wk | 2025-26 winning weeks | Worst week 2025-26 | VIX≥25 weeks $/wk | VIX≥30 weeks $/wk | Last week (Nov 16-20) |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for _, r in s.sort_values("picked_test_week", ascending=False).iterrows():
        lines.append(f"| {r.strategy} | {r.settings} | {r.settings_positive_pct}% | {r.median_setting_week:+,.0f} | "
                     f"{r.picked_train_week:+,.0f} | **{r.picked_test_week:+,.0f}** | {r.picked_test_pos_weeks_pct:.0f}% | "
                     f"{r.picked_test_worst_week:+,.0f} | {r.picked_vix25_week:+,.0f} ({r.vix25_weeks:.0f} wks) | "
                     f"{r.picked_vix30_week:+,.0f} ({r.vix30_weeks:.0f} wks) | {r.picked_last_week:+,.0f} |")
    lines.append("")
    lines.append("Picked settings (chosen on 2021-24 weekly P&L only):")
    for _, r in s.iterrows():
        lines.append(f"- {r.strategy}: {json.loads(r.picked_params)} | {r.picked_trades_per_week} trades/week")
    (OUT / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
