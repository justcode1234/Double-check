"""Strategy D, sentiment-quality test (separate from D's +$0.01 exit, which EOD data cannot evaluate).

Signal: GDELT ECON_STOCKMARKET tone in the 15:30 ET batch, z-scored against its own trailing 20 sessions (past only).
Questions: (1) does sign(z_t) predict SPY's close(t) -> close(t+1) direction? (2) does buying the ATM call (z>0) or put
(z<0) at day t's 16:00 ASK and selling at day t+1's 16:00 BID have positive expectancy vs no-sentiment baselines?
(2) is a 1-day EOD round trip, NOT D's exit rule. The news batch (15:30) precedes the 16:00 quote used for entry.

    PYTHONUTF8=1 .venv/Scripts/python.exe -m dual_strategy.research.options.d_sentiment
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from dual_strategy.research.features import daily_on_calendar
from dual_strategy.research.fetch import ROOT
from dual_strategy.research.options.eod_backtest import SEG, Chain, build_near, session_index, sessions_between
from dual_strategy.research.options.gdelt_tone import OUT as TONE
from dual_strategy.research.options.pricing import option_order_fee

OUT = ROOT / "reports" / "options_ef" / "d_sentiment.json"


def segment(d):
    return "dev" if d < SEG["val"][0] else "val" if d < SEG["oos"][0] else "oos"


def main() -> None:
    tone = pd.read_csv(TONE)
    tone = tone[tone["status"] == "ok"].copy()
    tone["date"] = pd.to_datetime(tone["date"]).dt.date
    tone = tone.set_index("date")["tone_mean"].astype(float).sort_index()
    mu = tone.rolling(20, min_periods=15).mean().shift(1)
    sd = tone.rolling(20, min_periods=15).std().shift(1)
    z = ((tone - mu) / sd).dropna()
    adj = daily_on_calendar("US.SPY", 1)["close"]
    nxt = (adj.shift(-1) / adj - 1)
    chain = Chain(build_near())
    spot = daily_on_calendar("US.SPY", 0)["close"]
    allsess, idx = session_index()
    rows = []
    for d, zt in z.items():
        if d not in nxt.index or pd.isna(nxt[d]) or d not in chain.exp_by_day:
            continue
        i = idx.get(d)
        d1 = allsess[i + 1] if i is not None and i + 1 < len(allsess) else None
        exps = [e for e in chain.exp_by_day[d] if sessions_between(idx, d, e) >= 2]
        rec = {"date": d, "segment": segment(d), "z": float(zt), "ret_next": float(nxt[d])}
        if exps and d1 is not None:
            e = exps[0]
            k = chain.nearest(d, e, spot[d])
            for right in ("C", "P"):
                q0, q1 = chain.get(d, e, k, right), chain.get(d1, e, k, right)
                if q0 and q1 and q0[1] > 0:
                    fees = option_order_fee(1, q0[1], False) + option_order_fee(1, q1[0], True)
                    rec[f"{right}_net_1c"] = (q1[0] - q0[1]) * 100 - fees            # 1 contract, ask in / bid out
                    rec[f"{right}_mid_chg"] = ((q1[0] + q1[1]) - (q0[0] + q0[1])) / 2 * 100
        rows.append(rec)
    df = pd.DataFrame(rows)
    rng = np.random.default_rng(5)
    res = {"signal": "GDELT ECON_STOCKMARKET mean tone, 15:30 ET batch, z vs trailing 20 sessions",
           "days_with_signal": len(df)}
    raw = pd.read_csv(TONE)
    okd = set(raw.loc[raw["status"] == "ok", "date"])
    res["gdelt_missing_days"] = sorted(set(raw["date"]) - okd)
    for seg, g in df.groupby("segment"):
        y = np.sign(g["ret_next"].to_numpy())
        s = np.sign(g["z"].to_numpy())
        hit = float(np.mean(s == y))
        corr = float(np.corrcoef(g["z"], g["ret_next"])[0, 1])
        perm = np.array([np.mean(rng.permutation(s) == y) for _ in range(2000)])
        side = np.where(g["z"] > 0, g["C_net_1c"], g["P_net_1c"])
        side_mid = np.where(g["z"] > 0, g["C_mid_chg"], g["P_mid_chg"])
        res[seg] = {"days": len(g), "up_day_rate": round(float(np.mean(y > 0)), 4),
                    "direction_hit_rate": round(hit, 4), "permutation_p_value": round(float(np.mean(perm >= hit)), 4),
                    "corr_z_next_return": round(corr, 4),
                    "option_1day_net_per_trade_sentiment_side": round(float(np.nanmean(side)), 2),
                    "option_1day_mid_change_sentiment_side": round(float(np.nanmean(side_mid)), 2),
                    "baseline_always_call_net": round(float(np.nanmean(g["C_net_1c"])), 2),
                    "baseline_always_put_net": round(float(np.nanmean(g["P_net_1c"])), 2),
                    "baseline_random_side_expected_net": round(float((np.nanmean(g["C_net_1c"]) + np.nanmean(g["P_net_1c"])) / 2), 2),
                    "share_sentiment_side_net_positive": round(float(np.nanmean(side > 0)), 4)}
    OUT.write_text(json.dumps(res, indent=1), encoding="utf-8")
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
