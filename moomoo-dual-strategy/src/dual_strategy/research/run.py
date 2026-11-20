"""Reproducible offline backtest of strategies A (stock ORB), B (SPY Noise-Area), C (SPY VWAP reversion).

    .venv/bin/python -m dual_strategy.research.run            # full run, writes reports/
    .venv/bin/python -m dual_strategy.research.run --only B   # subset (no final audit)

Historical simulation only: no broker connection, no orders. Every trade row is labelled SIMULATED.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
import time
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np

from dual_strategy.research import strat_a, strat_b, strat_c
from dual_strategy.research.data import HISTORY, sessions
from dual_strategy.research.etf import etf_context
from dual_strategy.research.execution import (BASELINE_EXEC, ETF_COSTS, ETF_EXEC, NATIVE_EXEC, PAPER_NOISE, PAPER_ORB,
                                              STOCK_COSTS)
from dual_strategy.research.fetch import END, ROOT
from dual_strategy.research.metrics import metrics
from dual_strategy.research.portfolio import Result, simulate

SEGMENTS = {
    "dev": (date(2021, 1, 4), date(2022, 12, 30)),
    "val": (date(2023, 1, 3), date(2023, 12, 29)),
    "oos": (date(2024, 1, 2), END),
    "full": (date(2021, 1, 4), END),
}
FREEZE_PATH = ROOT / "config" / "c_frozen.json"
C_GRID = [(b, s, h) for b in (0.20, 0.30, 0.40) for s in (0.20, 0.25, 0.35) for h in (30, 60, 90)]
C_PREREGISTERED = (0.30, 0.25, 60)
C_MIN_DEV_TRADES = 100
SLIP_GRID = (0, 1, 2, 3, 5, 10, 15)
SELECTION_RULE = (f"Highest development-period (2021-2022) daily Sharpe of the competition-sized long/short C1 at "
                  f"the baseline cost (competition fees + 1 bp/side), among grid cells with >= {C_MIN_DEV_TRADES} trades; ties -> closest to the preregistered "
                  f"{C_PREREGISTERED}. If no cell has a positive development Sharpe, freeze the preregistered cell "
                  f"and record a development FAIL. Validation (2023) is reported, never used to re-select.")


def seg_days(seg: str, lo: date | None = None, hi: date | None = None) -> list[date]:
    a, b = (lo, hi) if lo else SEGMENTS[seg]
    return [d for d in sessions() if a <= d <= b]


@dataclass
class Variant:
    vid: str
    strategy: str
    cfg: object
    group: str          # headline | ablation | slippage | stress
    note: str = ""
    universe: tuple[str, ...] | None = None


class Engine:
    def __init__(self) -> None:
        self.spy = etf_context("US.SPY")
        self.spy_ok = self.spy.b1.has_any()
        universe = json.loads((ROOT / "config" / "research_universe.json").read_text(encoding="utf-8"))
        self.ranked = [s for s in universe["stocks"] if list((HISTORY / "k6" / s).glob("*.csv.gz"))]
        self.missing_stocks = [s for s in universe["stocks"] if s not in self.ranked]
        self.stocks = strat_a.stock_universe(tuple(self.ranked)) if self.ranked else {}

    def run(self, v: Variant, days: list[date]) -> Result:
        if v.strategy == "A":
            uni = {s: self.stocks[s] for s in (v.universe or self.ranked) if s in self.stocks}
            any_s = next(iter(uni.values()))
            ok = np.any([s.b5.has_any() for s in uni.values()], axis=0)
            return simulate("A", v.cfg, strat_a.run_day, uni, days, any_s.b5.index, lambda t: bool(ok[t]))
        fn = strat_b.run_day if v.strategy == "B" else strat_c.run_day
        return simulate(v.strategy, v.cfg, fn, self.spy, days, self.spy.b1.index, lambda t: bool(self.spy_ok[t]))


def _sharpe_of(res: Result) -> float:
    r = res.daily["ret"].to_numpy()
    sd = r.std(ddof=1)
    return float(r.mean() / sd * np.sqrt(252)) if sd > 0 else 0.0


def select_c(engine: Engine, days: list[date]) -> tuple[tuple, list[dict]]:
    rows = []
    for band, stop, hold in C_GRID:
        cfg = strat_c.CConfig(f"C1-grid-{band}-{stop}-{hold}", band=band, stop_atr=stop, hold=hold)
        res = engine.run(Variant(cfg.name, "C", cfg, "grid"), days)
        rows.append({"band": band, "stop_atr": stop, "hold": hold, "trades": len(res.trades),
                     "sharpe": round(_sharpe_of(res), 4),
                     "net_return_pct": round(100 * (float(np.prod(1 + res.daily["ret"].to_numpy())) - 1), 3)})
    eligible = [r for r in rows if r["trades"] >= C_MIN_DEV_TRADES and r["sharpe"] > 0]
    if not eligible:
        return C_PREREGISTERED, rows
    dist = lambda r: abs(r["band"] - 0.30) + abs(r["stop_atr"] - 0.25) + abs(r["hold"] - 60) / 100
    best = max(eligible, key=lambda r: (r["sharpe"], -dist(r)))
    return (best["band"], best["stop_atr"], best["hold"]), rows


def freeze_c(engine: Engine) -> dict:
    """Write the frozen C rule BEFORE any validation/OOS simulation of C; reuse and verify it on reruns."""
    if FREEZE_PATH.exists():
        rec = json.loads(FREEZE_PATH.read_text(encoding="utf-8"))
        body = {k: v for k, v in rec.items() if k != "sha256"}
        if hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest() != rec["sha256"]:
            raise SystemExit("config/c_frozen.json was edited after freezing; refusing to run")
        return rec
    chosen, grid = select_c(engine, seg_days("dev"))
    body = {
        "frozen_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "development_window": [d.isoformat() for d in SEGMENTS["dev"]],
        "selection_rule": SELECTION_RULE, "preregistered": dict(zip(("band", "stop_atr", "hold"), C_PREREGISTERED)),
        "selected": dict(zip(("band", "stop_atr", "hold"), chosen)),
        "development_pass": any(r["trades"] >= C_MIN_DEV_TRADES and r["sharpe"] > 0 for r in grid),
        "fixed": {"expiry_bars": 6, "last_entry": "14:30", "hard_exit": "15:50", "vwap": "hlc3 5m session",
                  "atr": "ATR14 daily, prior sessions", "stop_first_on_ambiguous_bar": True},
        "grid_development_results": grid,
    }
    rec = {**body, "sha256": hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()}
    FREEZE_PATH.write_text(json.dumps(rec, indent=1))
    return rec


def variants(engine: Engine, frozen: dict) -> list[Variant]:
    out: list[Variant] = []
    if engine.stocks:
        a0 = strat_a.AConfig("A0-R", entry="resting_stop", top_n=20, sizing="paper", costs=PAPER_ORB, exe=NATIVE_EXEC,
                             loss_lock=None, flatten_before_close=0, max_concurrent=None, sector_cap=None,
                             entry_deadline=None, liquidity_frac=None,
                             label="A0 BLOCKED-DATA; restricted 40-stock universe run = EXPLORATORY")
        a1 = strat_a.AConfig("A1", label="RESTRICTED-UNIVERSE EXPLORATORY")
        out += [Variant("A0-R", "A", a0, "headline", "paper rules on a survivorship-biased 40-stock universe"),
                Variant("A1", "A", a1, "headline", "competition L/S"),
                Variant("A1-LO", "A", replace(a1, name="A1-LO", shorts=False), "headline", "competition long-only")]
        abl = {
            "A0-R-QCstop": replace(a0, name="A0-R-QCstop", stop_anchor="range"),
            "A0-R-optimistic": replace(a0, name="A0-R-optimistic", same_bar_stop="optimistic"),
            "A0-R-competition-fees": replace(a0, name="A0-R-competition-fees", costs=STOCK_COSTS),
            "A1-QCstop": replace(a1, name="A1-QCstop", stop_anchor="range"),
            "A1-resting-stop": replace(a1, name="A1-resting-stop", entry="resting_stop", exe=NATIVE_EXEC),
            "A1-touch-market": replace(a1, name="A1-touch-market", entry="touch_market"),
            "A1-rvol0": replace(a1, name="A1-rvol0", rvol_min=0.0),
            "A1-rvol1.5": replace(a1, name="A1-rvol1.5", rvol_min=1.5),
            "A1-rvol2": replace(a1, name="A1-rvol2", rvol_min=2.0),
            "A1-top4": replace(a1, name="A1-top4", top_n=4),
            "A1-top20": replace(a1, name="A1-top20", top_n=20),
            "A1-stop0.05": replace(a1, name="A1-stop0.05", stop_atr=0.05),
            "A1-stop0.20": replace(a1, name="A1-stop0.20", stop_atr=0.20),
            "A1-stop0.25": replace(a1, name="A1-stop0.25", stop_atr=0.25),
            "A1-no-deadline": replace(a1, name="A1-no-deadline", entry_deadline=None),
            "A1-no-losslock": replace(a1, name="A1-no-losslock", loss_lock=None),
        }
        out += [Variant(k, "A", c, "ablation") for k, c in abl.items()]
        out.append(Variant("A1-top20-universe", "A", replace(a1, name="A1-top20-universe"), "ablation",
                           "universe breadth: 20 highest-turnover names only", tuple(engine.ranked[:20])))
    b0 = strat_b.BConfig("B0", sizing="vol_target", strict_history=False, flatten_before_close=0, loss_lock=None,
                         costs=PAPER_NOISE, exe=NATIVE_EXEC, label="PUBLISHED-SPEC REPLICATION (next-bar fills)")
    b1 = strat_b.BConfig("B1", label="COMPETITION")
    out += [Variant("B0", "B", b0, "headline", "paper: L/S, min(4, 2%/vol14) leverage"),
            Variant("B0-100", "B", replace(b0, name="B0-100", sizing="fixed", fraction=1.0), "headline", "paper rules, 100% NAV"),
            Variant("B1", "B", b1, "headline", "competition L/S, 40% NAV"),
            Variant("B1-LO", "B", replace(b1, name="B1-LO", shorts=False), "headline", "competition long-only"),
            Variant("B2", "B", replace(b0, name="B2", sizing="fixed", fraction=0.40, costs=ETF_COSTS,
                                       label="B0 SIGNALS + COMPETITION SIZING/FEES ONLY"), "headline",
                    "B0's exact signals/exits (16:00 exit, no loss lock), 40% notional, competition fees + 1 bp")]
    babl = {
        "B1-stateful-trail": replace(b1, name="B1-stateful-trail", stateful=True),
        "B1-hlc3-vwap": replace(b1, name="B1-hlc3-vwap", vwap="hlc3"),
        "B1-skip-history": replace(b1, name="B1-skip-history", strict_history=False),
        "B1-no-losslock": replace(b1, name="B1-no-losslock", loss_lock=None),
        "B1-close-exit": replace(b1, name="B1-close-exit", flatten_before_close=0),
        "B1-monitor5": replace(b1, name="B1-monitor5", exe=ETF_EXEC.with_(monitor_every=5)),
        "B1-monitor15": replace(b1, name="B1-monitor15", exe=ETF_EXEC.with_(monitor_every=15)),
        "B1-monitor30": replace(b1, name="B1-monitor30", exe=ETF_EXEC.with_(monitor_every=30)),
        "B1-delay1": replace(b1, name="B1-delay1", exe=ETF_EXEC.with_(delay=1)),
        "B1-delay2": replace(b1, name="B1-delay2", exe=ETF_EXEC.with_(delay=2)),
        "B1-delay5": replace(b1, name="B1-delay5", exe=ETF_EXEC.with_(delay=5)),
        "B0-competition-fees": replace(b0, name="B0-competition-fees", costs=ETF_COSTS),
    }
    out += [Variant(k, "B", c, "ablation") for k, c in babl.items()]
    sel = frozen["selected"]
    c1 = strat_c.CConfig("C1", band=sel["band"], stop_atr=sel["stop_atr"], hold=sel["hold"],
                         label="PRE-REGISTERED HYPOTHESIS (frozen on 2021-2022)")
    c0 = replace(c1, name="C0", sizing="full", costs=PAPER_NOISE, exe=NATIVE_EXEC, loss_lock=None,
                 label="HYPOTHESIS, unconstrained 100% NAV, paper costs")
    out += [Variant("C0", "C", c0, "headline", "100% NAV, $0.0035+$0.001/sh, native stops"),
            Variant("C1", "C", c1, "headline", "competition L/S"),
            Variant("C1-LO", "C", replace(c1, name="C1-LO", shorts=False), "headline", "competition long-only")]
    pre = dict(zip(("band", "stop_atr", "hold"), C_PREREGISTERED))
    cabl = {
        "C1-turnover-vwap": replace(c1, name="C1-turnover-vwap", vwap="turnover"),
        "C1-expiry3": replace(c1, name="C1-expiry3", expiry_bars=3),
        "C1-expiry12": replace(c1, name="C1-expiry12", expiry_bars=12),
        "C1-no-losslock": replace(c1, name="C1-no-losslock", loss_lock=None),
        "C1-native-stop": replace(c1, name="C1-native-stop", exe=NATIVE_EXEC),
    }
    if sel != pre:
        cabl["C1-preregistered"] = replace(c1, name="C1-preregistered", **pre)
    out += [Variant(k, "C", c, "ablation") for k, c in cabl.items()]
    heads = {v.vid: v for v in out if v.vid in ("A1", "B1", "C1")}
    for vid, v in heads.items():
        for bps in SLIP_GRID:
            out.append(Variant(f"{vid}-slip{bps}", v.strategy, replace(v.cfg, name=f"{vid}-slip{bps}",
                               costs=v.cfg.costs.with_slippage(bps)), "slippage"))
        base = v.cfg.exe
        stress = {
            "stop-native": base.with_(stop_mode="native", stop_latency_bps=0.0),
            "stop-stressed": base.with_(stop_mode="stressed"),
            "delay1": base.with_(delay=1),
            "delay2": base.with_(delay=2),
            "reject2pct": base.with_(reject_rate=0.02),
            "miss2pct": base.with_(miss_rate=0.02),
            "participation0.05": base.with_(participation=0.05),
            "combined": base.with_(stop_mode="stressed", delay=1, reject_rate=0.02, miss_rate=0.02),
        }
        for k, exe in stress.items():
            out.append(Variant(f"{vid}-{k}", v.strategy, replace(v.cfg, name=f"{vid}-{k}", exe=exe), "stress"))
        out.append(Variant(f"{vid}-combined-slip10", v.strategy,
                           replace(v.cfg, name=f"{vid}-combined-slip10", costs=v.cfg.costs.with_slippage(10),
                                   exe=stress["combined"]), "stress"))
    return out


def walk_forward_c(engine: Engine) -> list[dict]:
    """Re-select C on each trailing 2-year window, test on the next 6 months (stitched OOS-of-fold)."""
    folds = []
    starts = [date(2023, 1, 1), date(2023, 7, 1), date(2024, 1, 1), date(2024, 7, 1), date(2025, 1, 1),
              date(2025, 7, 1), date(2026, 1, 1), date(2026, 7, 1)]
    for s in starts:
        train = [d for d in sessions() if date(s.year - 2, s.month, 1) <= d < s]
        e = date(s.year + (s.month == 7), 1 if s.month == 7 else 7, 1)
        test = [d for d in sessions() if s <= d < e and d <= END]
        if not test:
            continue
        chosen, _ = select_c(engine, train)
        cfg = strat_c.CConfig("C1-wf", band=chosen[0], stop_atr=chosen[1], hold=chosen[2])
        res = engine.run(Variant("C1-wf", "C", cfg, "wf"), test)
        r = res.daily["ret"].to_numpy()
        folds.append({"train": f"{train[0]}..{train[-1]}", "test": f"{test[0]}..{test[-1]}",
                      "selected": {"band": chosen[0], "stop_atr": chosen[1], "hold": chosen[2]},
                      "test_trades": len(res.trades), "test_net_return_pct": round(100 * (float(np.prod(1 + r)) - 1), 3),
                      "test_sharpe": round(_sharpe_of(res), 3) if len(r) > 20 else None, "returns": r.tolist(),
                      "days": [d.isoformat() for d in test]})
    return folds


def git_commit() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True)
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True).stdout
        return out.stdout.strip() + ("+uncommitted-research-modules" if dirty.strip() else "")
    except Exception:
        return "unknown"


def main(argv: list[str] | None = None) -> None:
    from dual_strategy.research import report
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", choices=["A", "B", "C"], action="append")
    parser.add_argument("--out", default=str(ROOT / "reports" / "backtest_v3"))
    args = parser.parse_args(argv)
    t0 = time.time()
    engine = Engine()
    frozen = freeze_c(engine)
    print(f"C frozen: {frozen['selected']} (development_pass={frozen['development_pass']})", flush=True)
    todo = [v for v in variants(engine, frozen) if not args.only or v.strategy in args.only]
    rows, full_results = [], {}
    for i, v in enumerate(todo):
        for seg in ("dev", "val", "oos", "full"):
            res = engine.run(v, seg_days(seg))
            m = metrics(res)
            rows.append({"variant": v.vid, "group": v.group, "segment": seg, "note": v.note, **m})
            if seg == "full":
                full_results[v.vid] = res
        print(f"[{i + 1}/{len(todo)}] {v.vid} {time.time() - t0:.0f}s", flush=True)
    wf = walk_forward_c(engine) if not args.only or "C" in args.only else []
    report.write_all(Path(args.out), engine=engine, variants=todo, rows=rows, full=full_results, frozen=frozen, wf=wf,
                     segments=SEGMENTS,
                     command=" ".join([".venv/bin/python -m dual_strategy.research.run", *(argv or sys.argv[1:])]),
                     commit=git_commit(), python=platform.python_version(), elapsed=time.time() - t0,
                     partial=bool(args.only))
    print(f"done in {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
