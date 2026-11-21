"""Parameter-grid backtest of the ten regime strategies; weekly P&L on a fixed USD 100,000 book per strategy.

    python reports/claude_regime/run_grid.py [strategy-number ...]
Outputs reports/claude_regime/results/grid.csv (one row per strategy x parameter set) and summary.csv / summary.md.
Weeks are Monday-Friday calendar weeks. A week's VIX regime is the VIX close of the last session before it.
Selection is honest: the best parameter set is chosen on 2021-2024 weekly P&L and reported on 2025-01-01..2026-11-20.
"""

from __future__ import annotations

import itertools
import json
import sys
import time
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rdata as R  # noqa: E402
import rstrats as S  # noqa: E402

OUT = Path("reports/claude_regime/results")
LO, HI = "2021-01-04", R.END.isoformat()
TRAIN_END, TEST_START = date(2024, 12, 31), date(2025, 1, 1)
LAST_WEEK = (date(2026, 11, 16), date(2026, 11, 20))


def week_table():
    days = [d for d in R.DAYS if LO <= d.isoformat() <= HI]
    vix = R.vix()
    rows, prev_close = {}, None
    wk_of = {}
    for d in days:
        key = d.isocalendar()[:2]
        wk_of[d] = key
        if key not in rows:
            k = R.IDX[d] - 1
            rows[key] = {"week": key, "start": d, "vix_prior": float(vix.iloc[k]) if k >= 0 else np.nan}
        rows[key]["end"] = d
    return days, wk_of, pd.DataFrame(rows.values()).set_index("week")


DAYS, WK_OF, WEEKS = week_table()
DAY_IDX = [R.IDX[d] for d in DAYS]


def weekly(trades):
    pnl = pd.Series(0.0, index=WEEKS.index)
    n = pd.Series(0, index=WEEKS.index)
    for t in trades:
        k = WK_OF.get(t.day)
        if k is not None:
            pnl[k] += t.pnl
            n[k] += 1
    return pnl, n


def stats(pnl, mask):
    x = pnl[mask]
    if not len(x):
        return {}
    return {"weeks": int(len(x)), "mean": round(float(x.mean()), 1), "median": round(float(x.median()), 1),
            "pos_pct": round(100 * float((x > 0).mean()), 1), "p5": round(float(x.quantile(0.05)), 1),
            "worst": round(float(x.min()), 1), "best": round(float(x.max()), 1), "total": round(float(x.sum()), 1)}


def run_strategy(name, fn, grid):
    keys = list(grid)
    rows = []
    start = WEEKS.start
    train = (start <= pd.Timestamp(TRAIN_END).date()).to_numpy()
    test = (start >= TEST_START).to_numpy()
    v = WEEKS.vix_prior.to_numpy()
    last = (WEEKS.start >= LAST_WEEK[0]).to_numpy() & (WEEKS.start <= LAST_WEEK[1]).to_numpy()
    for combo in itertools.product(*grid.values()):
        p = dict(zip(keys, combo))
        t0 = time.time()
        trades = fn(p, DAY_IDX)
        pnl, n = weekly(trades)
        row = {"strategy": name, "params": json.dumps(p, default=str), "trades": len(trades),
               "trades_per_week": round(len(trades) / len(WEEKS), 2), "secs": round(time.time() - t0, 1)}
        for label, m in (("all", np.ones(len(WEEKS), bool)), ("train", train), ("test", test),
                         ("vix25", v >= 25), ("vix30", v >= 30), ("lastweek", last)):
            for k, val in stats(pnl, m).items():
                row[f"{label}_{k}"] = val
        rows.append(row)
        print(f"{name:28s} {row['params'][:90]:90s} all {row.get('all_mean', 0):+8.1f}/wk  test {row.get('test_mean', 0):+8.1f}  "
              f"vix30 {row.get('vix30_mean', 0):+8.1f}  last {row.get('lastweek_total', 0):+8.1f}  trades {row['trades']:5d} {row['secs']}s", flush=True)
    return rows


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    pick = set(sys.argv[1:])
    path = OUT / f"grid_{'_'.join(sorted(pick)) or 'all'}.csv"
    old = pd.read_csv(path) if path.exists() else pd.DataFrame()
    new = []
    for name, (fn, grid) in S.STRATS.items():
        if pick and name.split()[0] not in pick:
            continue
        new += run_strategy(name, fn, grid)
    new = pd.DataFrame(new)
    if len(old):
        old = old[~old.strategy.isin(new.strategy.unique())]
    allrows = pd.concat([old, new], ignore_index=True)
    allrows.to_csv(path, index=False)
    print("weeks:", len(WEEKS), "| VIX>=25 weeks:", int((WEEKS.vix_prior >= 25).sum()), "| VIX>=30 weeks:", int((WEEKS.vix_prior >= 30).sum()))


if __name__ == "__main__":
    main()
