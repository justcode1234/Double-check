"""Backtest preview of the competition portfolio (research only, hypothetical fills).

Combines the Claude-rules B3 and H daily books from the research worktree (reports/claude_a_j/results) as
NAV_t = NAV_{t-1} x (1 + 0.80 x r_B3,t + 0.20 x r_H,t), i.e. sleeves re-weighted to 80/20 of account NAV each day as
the live planner does, then applies the account 3.5% close-drawdown halt (flat for the rest of the window once hit).
Also bootstraps 5- and 8-session outcomes (10,000 paths, 4-session circular blocks) for the remaining challenge days.

    .venv/Scripts/python.exe scripts/competition_preview.py "<research worktree>/moomoo-dual-strategy"
Writes reports/competition/backtest_preview.json (read by the dashboard's /competition page).
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
W = {"b3": 0.80, "h": 0.20}
HALT = 0.035


def combine(res: Path, window: str, rules: str = "claude"):
    b = pd.read_csv(res / window / rules / "B_daily.csv")
    h = pd.read_csv(res / window / rules / "H_daily.csv")
    assert b.date.tolist() == h.date.tolist()
    r = W["b3"] * b.daily_return.to_numpy() + W["h"] * h.daily_return.to_numpy()
    nav, peak, halted_on, out = 100_000.0, 100_000.0, None, []
    for d, x in zip(b.date, r):
        if halted_on is None:
            nav *= 1 + x
            peak = max(peak, nav)
            if nav <= peak * (1 - HALT):
                halted_on = d
        out.append(nav)
    eq = np.r_[100_000.0, out]
    dd = eq / np.maximum.accumulate(eq) - 1
    raw = np.cumprod(1 + r) * 100_000
    return {"window": window, "first_session": b.date.iloc[0], "last_session": b.date.iloc[-1], "sessions": len(b),
            "net_return_pct": round(100 * (out[-1] / 1e5 - 1), 3), "max_drawdown_pct": round(100 * dd.min(), 3),
            "halted_on": halted_on, "without_halt_return_pct": round(100 * (raw[-1] / 1e5 - 1), 3),
            "b3_return_pct": round(100 * (b.nav.iloc[-1] / 1e5 - 1), 3), "h_return_pct": round(100 * (h.nav.iloc[-1] / 1e5 - 1), 3),
            "sharpe": round(float(r.mean() / r.std(ddof=1) * np.sqrt(252)), 3),
            "positive_days_pct": round(100 * float(np.mean(r > 0)), 1),
            "equity": [{"date": d, "nav": round(v, 2)} for d, v in zip(b.date, out)]}, r


def short_horizon(r, horizons=(5, 8), paths=10_000, block=4, seed=20261120):
    rng = np.random.default_rng(seed)
    ext = np.concatenate([r, r[:block]])
    out = {}
    for hz in horizons:
        starts = rng.integers(0, len(r), size=(paths, -(-hz // block)))
        idx = (starts[:, :, None] + np.arange(block)).reshape(paths, -1)[:, :hz]
        tot = np.prod(1 + ext[idx], axis=1) - 1
        out[str(hz)] = {"median_pct": round(100 * float(np.median(tot)), 3), "p5_pct": round(100 * float(np.percentile(tot, 5)), 3),
                        "p95_pct": round(100 * float(np.percentile(tot, 95)), 3), "loss_probability_pct": round(100 * float(np.mean(tot < 0)), 1)}
    return out


def main():
    research = Path(sys.argv[1]) if len(sys.argv) > 1 else PROJECT.parents[1] / "Moo Moo - Codex Research" / "moomoo-dual-strategy"
    res = research / "reports" / "claude_a_j" / "results"
    windows, tapes = {}, {}
    for w in ("long", "2024_25", "2021_22", "2023"):
        windows[w], tapes[w] = combine(res, w)
    stress = pd.read_csv(res / "stress.csv")
    st = stress[(stress.rules == "claude") & stress.letter.isin(["B", "H"])]
    costs = {}
    for w in ("long", "2024_25"):
        for bp in ("0bp", "1bp", "3bp", "5bp", "10bp"):
            g = st[(st.window == w) & (st.stress == bp)].set_index("letter").net_return_pct
            costs.setdefault(w, {})[bp] = round(W["b3"] * g["B"] + W["h"] * g["H"], 3)   # sleeve-weighted, no halt
    out = {"generated_at": datetime.now(timezone.utc).isoformat(), "kind": "RESEARCH_BACKTEST_NOT_FILLS",
           "allocation": W, "halt_drawdown_fraction": HALT,
           "source": "Claude independent A-J engine, Claude rules (reports/claude_a_j in the research worktree)",
           "method": "daily returns weighted 80% B3 + 20% H, account halt at 3.5% below the high-water close",
           "windows": windows, "cost_stress_return_pct": costs,
           "remaining_challenge_sessions_bootstrap": {"long": short_horizon(tapes["long"]), "2024_25": short_horizon(tapes["2024_25"])}}
    dest = PROJECT / "reports" / "competition" / "backtest_preview.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    print(json.dumps({k: {x: v[x] for x in v if x != "equity"} for k, v in windows.items()}, indent=1))
    print(json.dumps({"costs": costs, "bootstrap": out["remaining_challenge_sessions_bootstrap"]}, indent=1))


if __name__ == "__main__":
    main()
