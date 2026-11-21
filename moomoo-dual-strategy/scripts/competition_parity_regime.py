"""Parity: live Noise-area, VWAP and ORB signal code (src/dual_strategy/competition/intraday.py) vs the research
reference (reports/claude_regime/rstrats.py s10_noise, s2_vwap, s1_orb universe etf) on the same cached bars, every
session 2021-01-04..2026-11-20.

Run from the moomoo-dual-strategy folder:   .venv/bin/python scripts/competition_parity_regime.py [--lo D] [--hi D]
VWAP / ORB: every candidate's entry slot, side, entry, stop, target, quantity and exit path (captured inside the
reference by wrapping walk() and settle()), plus each day's hypothetical P&L. Noise-area: every trade's side, quantity,
entry, exit and reason, from the live decision function fed the reference's 1-minute SPY data (close at the slot,
cumulative turnover/volume, the next minute's open). Writes reports/competition/parity_regime.json.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timedelta
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))
sys.path.insert(0, str(PROJECT / "reports" / "claude_regime"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import rdata as R  # noqa: E402  (reads ./data-cache relative to the working directory)
import rstrats as RS  # noqa: E402
import yaml  # noqa: E402

from dual_strategy.competition import intraday as I  # noqa: E402
from dual_strategy.competition.signals import Bar5  # noqa: E402
from dual_strategy.data.calendar import NY  # noqa: E402

CFG = yaml.safe_load((PROJECT / "config" / "competition.yaml").read_text(encoding="utf-8"))
REF = {"vwap": {"pullback_atr": 0.10, "stop_atr": 0.25, "target_r": 3.0, "need_struct": True},
       "orb": {"or_min": 15, "rvol": 2.0, "stop_atr": 0.35, "target_r": 2.0, "dir": "both", "universe": "etf"},
       "noise": {"band": 1.0, "times": "report", "alloc": 0.4, "shorts": True}}
EQUITY = RS.EQUITY


def close(x, y):
    return math.isclose(float(x), float(y), rel_tol=0, abs_tol=1e-9)


def same_fills(a, b):
    return len(a) == len(b) and all(fa[2] == fb[2] and close(fa[0], fb[0]) and close(fa[1], fb[1]) for fa, fb in zip(a, b))


def capture():
    """Wrap rstrats.walk/settle: every reference candidate as (day, symbol, entry slot, side, entry, stop, target,
    exit slot, fills, qty). Restored by the returned function."""
    rows, last = [], {}
    real_walk, real_settle = RS.walk, RS.settle

    def walk(b, i, j0, side, entry, stop, target, j_end, mode="fixed", gap_first=False):
        out = real_walk(b, i, j0, side, entry, stop, target, j_end, mode, gap_first)
        last.update(j0=j0, side=side, entry=float(entry), stop=float(stop), target=float(target), xj=out[1],
                    fills=[(float(f), float(px), w) for f, px, w in out[0]])
        return out

    def settle(sym, entry_day, side, qty, entry_raw, fills):
        if last:
            rows.append({**last, "sym": sym, "day": entry_day.isoformat(), "qty": int(qty)})
            last.clear()
        return real_settle(sym, entry_day, side, qty, entry_raw, fills)

    RS.walk, RS.settle = walk, settle

    def restore():
        RS.walk, RS.settle = real_walk, real_settle
    return rows, restore


def ref_pnl_by_day(trades):
    out = {}
    for t in trades:
        out[t.day.isoformat()] = out.get(t.day.isoformat(), 0.0) + t.pnl
    return out


def pnl_diff(ref, mine):
    return sorted(d for d in set(ref) | set(mine) if not math.isclose(ref.get(d, 0.0), mine.get(d, 0.0), abs_tol=1e-6))


def orb_rvol_at(S, i, k):
    first = lambda r: I.first_range_volume(S.b.volume[r], k)
    return I.orb_rvol(first(i), [first(r) for r in range(max(0, i - 20), i)])


def vwap_orb_parity(name, days):
    rows, restore = capture()
    try:
        ref_trades = (RS.s2_vwap if name == "vwap" else RS.s1_orb)(REF[name], days)
    finally:
        restore()
    ref = {}
    for r in rows:
        ref.setdefault((r["day"], r["sym"]), []).append(r)
    p = CFG[name]
    symbols = p["symbols"]
    mismatches, mine_pnl, n_cands = [], {}, 0
    for i in days:
        d = R.DAYS[i].isoformat()
        kept = []
        for s in symbols:
            S = RS.Sym(s)
            n = int(S.b.expected[i])
            a = {k: getattr(S.b, k)[i] for k in ("open", "high", "low", "close", "volume", "turnover")}
            if name == "vwap":
                cands, _ = I.s2_scan(a, S.vwap[i], S.atr[i], n, p, I.SLOTS - 1, EQUITY, 0.20 * EQUITY)
            else:
                t, _, _ = I.orb_scan(a, S.vwap[i], S.atr[i], orb_rvol_at(S, i, p["range_minutes"] // 5), n, p,
                                     I.SLOTS - 1, EQUITY, float(p["max_notional"]))
                cands = [t] if t else []
            want = ref.get((d, s), [])
            n_cands += len(want)
            ok = len(cands) == len(want) and all(
                c["entry_j"] == r["j0"] and c["side"] == r["side"] and c["qty"] == r["qty"] and c["exit_j"] == r["xj"]
                and close(c["entry"], r["entry"]) and close(c["stop"], r["stop"]) and close(c["target"], r["target"])
                and same_fills(c["fills"], r["fills"]) for c, r in zip(cands, want))
            if not ok:
                mismatches.append({"day": d, "symbol": s,
                                   "live": [(c["entry_j"], c["side"], c["entry"], c["stop"], c["target"], c["qty"], c["exit_j"]) for c in cands],
                                   "reference": [(r["j0"], r["side"], r["entry"], r["stop"], r["target"], r["qty"], r["xj"]) for r in want]})
            kept += [{**c, "symbol": s} for c in cands]
        kept = I.concurrency(kept, p["max_open"])
        mine_pnl[d] = sum(I.replay_pnl(c["side"], c["qty"], c["entry"], c["fills"]) for c in kept)
    refd = ref_pnl_by_day(ref_trades)
    diff = pnl_diff(refd, mine_pnl)
    return {"sessions": len(days), "reference_candidates": n_cands, "candidate_mismatches": len(mismatches),
            "first_mismatches": mismatches[:10], "reference_trades": len(ref_trades),
            "pnl_reference": round(sum(refd.values()), 2), "pnl_live": round(sum(mine_pnl.values()), 2),
            "pnl_mismatch_count": len(diff), "pnl_mismatch_days": diff[:20]}


def spy_decision_bars():
    """5-minute Bar5 lists per session built from the reference's 1-minute SPY bars (open of the first minute, close
    of the last minute, summed turnover/volume), so the live decision sees exactly the reference's inputs."""
    b1 = R.bars1("US.SPY")
    out = {}
    for i, d in enumerate(R.DAYS):
        n5 = int(R.MINUTES[i] // 5)
        opened = datetime(d.year, d.month, d.day, 9, 30, tzinfo=NY)
        rows = []
        for s in range(n5):
            m = slice(5 * s, 5 * s + 5)
            with np.errstate(all="ignore"):
                rows.append(Bar5(opened + timedelta(minutes=5 * (s + 1)), float(b1.open[i, 5 * s]), float(np.nanmax(b1.high[i, m])) if np.isfinite(b1.high[i, m]).any() else math.nan,
                                 float(np.nanmin(b1.low[i, m])) if np.isfinite(b1.low[i, m]).any() else math.nan,
                                 float(b1.close[i, 5 * s + 4]), float(np.nansum(b1.volume[i, m])), float(np.nansum(b1.turnover[i, m]))))
        out[i] = (opened, rows)
    return b1, out


def noise_parity(days):
    rows, restore = capture()
    trades = []
    real_settle = RS.settle

    def settle(sym, entry_day, side, qty, entry_raw, fills):
        trades.append((entry_day.isoformat(), side, int(qty), float(entry_raw), float(fills[0][1]), fills[0][3]))
        return real_settle(sym, entry_day, side, qty, entry_raw, fills)
    RS.settle = settle
    try:
        ref_trades = RS.s10_noise(REF["noise"], days)
    finally:
        restore()
    ref = {}
    for t in trades:
        ref.setdefault(t[0], []).append(t[1:])
    p = {**CFG["noise"], "shorts": True}
    b1, bars = spy_decision_bars()
    prev = R.features("US.SPY").prev_close.to_numpy()
    mine, mism, blocked = {}, [], 0
    for i in days:
        d = R.DAYS[i]
        opened, today = bars[i]
        n = int(R.MINUTES[i])
        o1, c1 = b1.open[i], b1.close[i]
        if not math.isfinite(o1[0]) or not prev[i] > 0:
            continue
        prior = {R.DAYS[r]: bars[r][1] for r in range(max(0, i - 30), i)}
        closed = opened + timedelta(minutes=n)
        pos, entry, qty, out = 0, None, 0, []
        for tick in I.noise_decision_times(opened, closed, p["decision_times"]):
            dec = I.noise_decision(today, prior, float(prev[i]), tick, opened, p)
            if dec["target"] is None:
                blocked += 1
                continue
            jm = int((tick - opened).total_seconds() // 60) - 1                      # the 1-minute slot ending at tick
            if dec["target"] == pos or not math.isfinite(o1[jm + 1]):
                continue
            px = float(o1[jm + 1])
            if pos:
                out.append((pos, qty, entry, px, "target_change"))
            pos = dec["target"]
            if pos:
                entry, qty = px, int(REF["noise"]["alloc"] * EQUITY / max(px, o1[0]))
        if pos:
            xj = n - 5
            out.append((pos, qty, entry, float(o1[xj]) if math.isfinite(o1[xj]) else float(c1[xj - 1]), "flatten"))
        if out:
            mine[d.isoformat()] = out
    for day in sorted(set(ref) | set(mine)):
        a, b = mine.get(day, []), ref.get(day, [])
        if len(a) != len(b) or not all(x[0] == y[0] and x[1] == y[1] and x[4] == y[4] and close(x[2], y[2]) and close(x[3], y[3])
                                       for x, y in zip(a, b)):
            mism.append({"day": day, "live": a, "reference": b})
    return {"sessions": len(days), "reference_trades": len(ref_trades), "reference_trade_days": len(ref),
            "live_trade_days": len(mine), "mismatch_days": len(mism), "first_mismatches": mism[:10],
            "live_blocked_decisions": blocked}


def incremental_parity(days):
    """The live runner sees one completed slot at a time. For every session, symbol and slot, the scan on bars
    0..upto must agree with the full-day scan: started trades identical (entry, stop, target, quantity, fills so far),
    and a pending VWAP trigger / ORB entry exactly where the full-day scan enters on the next slot."""
    bad, checks = [], 0
    for name in ("vwap", "orb"):
        p = CFG[name]
        for i in days:
            for s in p["symbols"]:
                S = RS.Sym(s)
                n = int(S.b.expected[i])
                full = {k: getattr(S.b, k)[i] for k in ("open", "high", "low", "close", "volume", "turnover")}
                rv = orb_rvol_at(S, i, p["range_minutes"] // 5) if name == "orb" else None

                def scan(a, vw, upto):
                    if name == "vwap":
                        return I.s2_scan(a, vw, S.atr[i], n, p, upto, EQUITY, 0.20 * EQUITY)
                    t, pend, _ = I.orb_scan(a, vw, S.atr[i], rv, n, p, upto, EQUITY, float(p["max_notional"]))
                    return ([t] if t else []), pend
                ref, _ = scan(full, S.vwap[i], I.SLOTS - 1)
                for upto in range(n):
                    mask = np.arange(I.SLOTS) <= upto
                    part = {k: np.where(mask, v, np.nan) for k, v in full.items()}
                    got, pend = scan(part, np.where(mask, S.vwap[i], np.nan), upto)
                    checks += 1
                    started = [c for c in ref if c["entry_j"] <= upto]
                    ok = len(got) == len(started)
                    for g, r in zip(got, started):
                        ok = ok and g["entry_j"] == r["entry_j"] and g["qty"] == r["qty"] and all(close(g[k], r[k]) for k in ("entry", "stop", "target"))
                        if g["exit_j"] is not None:
                            ok = ok and g["exit_j"] == r["exit_j"] and same_fills(g["fills"], r["fills"])
                        else:
                            ok = ok and same_fills(g["fills"], r["fills"][:len(g["fills"])])
                    nxt = [c for c in ref if c["entry_j"] == upto + 1]
                    if pend is not None:
                        key = "setup_j" if name == "vwap" else "entry_j"
                        ok = ok and (not nxt or nxt[0][key] == pend[key])
                    elif nxt:
                        ok = False
                    if not ok:
                        bad.append({"strategy": name, "day": R.DAYS[i].isoformat(), "symbol": s, "upto": upto})
    return {"slot_checks": checks, "mismatches": len(bad), "first_mismatches": bad[:10]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lo", default="2021-01-04")
    ap.add_argument("--hi", default="2026-11-20")
    ap.add_argument("--skip-incremental", action="store_true")
    a = ap.parse_args()
    days = RS.in_window(a.lo, a.hi)
    out = {"window": [a.lo, a.hi], "reference": "reports/claude_regime/rstrats.py",
           "noise": noise_parity(days), "vwap": vwap_orb_parity("vwap", days), "orb": vwap_orb_parity("orb", days)}
    if not a.skip_incremental:
        out["vwap_orb_incremental"] = incremental_parity(days)
    path = PROJECT / "reports" / "competition" / "parity_regime.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    print(json.dumps({k: ({kk: vv for kk, vv in v.items() if not kk.startswith("first")} if isinstance(v, dict) else v)
                      for k, v in out.items()}, indent=1, default=str))
    ok = (out["noise"]["mismatch_days"] == 0 and all(out[k]["candidate_mismatches"] == 0 and out[k]["pnl_mismatch_count"] == 0
                                                     for k in ("vwap", "orb"))
          and out.get("vwap_orb_incremental", {"mismatches": 0})["mismatches"] == 0)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
