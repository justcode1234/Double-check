"""Parity check: the live competition signal code vs the research engine that reproduced the frozen backtests.

Run from the RESEARCH worktree project directory (it owns the 2021-2025 caches and reports/claude_a_j/engine):
    "<venv python>" "<this repo>/scripts/competition_parity.py"
B3: every hourly decision 2021-01-04..2025-12-12 from 5-minute bars (live code) vs the engine's 1-minute targets.
H: every session's entry candidates (nothing held) and universe from the live code vs the engine.
Writes reports/claude_a_j/results/competition_parity.json in the research worktree.
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / "src"))
sys.path.insert(0, str(Path.cwd() / "reports" / "claude_a_j" / "engine"))

import cdata  # noqa: E402  (research engine, reads ./data-cache)
import cetf  # noqa: E402
import cspy  # noqa: E402
from dual_strategy.competition.signals import Bar5, b3_decision, b3_decision_times, h_plan  # noqa: E402

NY = "America/New_York"
LO, HI = date(2021, 1, 4), date(2025, 12, 12)
cfg = {"core": ["SPY", "QQQ", "IWM", "DIA"], "sectors": list(cetf.SECTORS), "top_sectors": 5, "dollar_volume_sessions": 20,
       "rsi_entry_max": 10, "rsi_exit_min": 70, "max_hold_sessions": 3, "trend_sma_sessions": 100}

# ------------------------------------------------------------------ B3
spy = cspy.Spy()
targets = spy.b_targets(14, 1.0, 60, False, "skip")
b5 = cdata.to_five(cdata.intraday("US.SPY", 1))      # no cached Moomoo 5-minute SPY file; aggregate the 1-minute bars
prev_close = cdata.daily_features("US.SPY").prev_close.to_numpy()
div = cdata.dividends("US.SPY")
bars_by_day = {}
for i, d in enumerate(cdata.DAYS):
    n = int(b5.expected[i])
    o = pd.Timestamp(cdata.CAL.session_open(pd.Timestamp(d))).tz_convert(NY).to_pydatetime()
    rows = []
    for j in range(n):
        if np.isfinite(b5.close[i, j]):
            rows.append(Bar5(o + timedelta(minutes=5 * (j + 1)), b5.open[i, j], b5.high[i, j], b5.low[i, j], b5.close[i, j],
                             b5.volume[i, j], b5.turnover[i, j]))
    bars_by_day[d] = (o, rows)
agree = disagree = blocked_live = blocked_engine = 0
examples = []
for i, d in enumerate(cdata.DAYS):
    if not LO <= d <= HI:
        continue
    o, rows = bars_by_day[d]
    c = pd.Timestamp(cdata.CAL.session_close(pd.Timestamp(d))).tz_convert(NY).to_pydatetime()
    prior = {x: bars_by_day[x][1] for x in cdata.DAYS[max(0, i - 40):i]}
    for t in b3_decision_times(o, c, ["10:00", "11:00", "12:00", "13:00", "14:00", "15:00"]):
        j = int((t - o).total_seconds() // 60) - 1                 # 1-minute slot ending at t
        eng = int(targets[i, j])
        live = b3_decision([b for b in rows if b.end <= t], prior, float(prev_close[i]), div.get(d, 0.0), t, o)
        if eng == -9 or live.target is None:
            blocked_engine += eng == -9
            blocked_live += live.target is None
            if (eng == -9) != (live.target is None) and len(examples) < 10:
                examples.append({"day": str(d), "at": t.strftime("%H:%M"), "engine": eng, "live": live.target, "why": live.reason})
            continue
        if eng == live.target:
            agree += 1
        else:
            disagree += 1
            if len(examples) < 10:
                examples.append({"day": str(d), "at": t.strftime("%H:%M"), "engine": eng, "live": live.target, **live.metrics})
b3 = {"decisions_compared": agree + disagree, "agree": agree, "disagree": disagree, "blocked_engine": blocked_engine,
      "blocked_live": blocked_live, "examples": examples}
print("B3", {k: v for k, v in b3.items() if k != "examples"}, flush=True)

# ------------------------------------------------------------------ H (Yahoo snapshot used by the H backtest)
P = cetf.EtfPanel()
panel = {}
for s in cetf.CORE + cetf.SECTORS:
    f = cdata.yahoo_daily(s)[0]
    panel[s] = pd.DataFrame({"close": f.close, "volume": f.volume, "adjusted": f.adjusted})
sess = list(cdata.yahoo_daily("SPY")[0].index)
same_u = same_e = total = 0
h_examples = []
for i, d in enumerate(cdata.DAYS):
    if not LO <= d <= HI:
        continue
    eng_u = list(P.universe[i])
    eng_e = sorted((s for s in eng_u if P.rsi[s][i] <= cetf.H_SPEC["entry"] and P.trend[s][i]), key=lambda s: (P.rsi[s][i], s))
    p = h_plan(panel, d, {}, [x for x in sess if x <= d], cfg)
    total += 1
    same_u += p.universe == eng_u
    same_e += p.entries == eng_e
    if (p.universe != eng_u or p.entries != eng_e) and len(h_examples) < 10:
        h_examples.append({"day": str(d), "engine_entries": eng_e, "live_entries": p.entries, "engine_u": eng_u, "live_u": p.universe})
h = {"sessions": total, "universe_identical": same_u, "entries_identical": same_e, "examples": h_examples}
print("H", {k: v for k, v in h.items() if k != "examples"}, flush=True)
out = Path.cwd() / "reports" / "claude_a_j" / "results" / "competition_parity.json"
out.write_text(json.dumps({"b3": b3, "h": h, "generated": datetime.now().isoformat()}, indent=1, default=str), encoding="utf-8")
print("->", out)
