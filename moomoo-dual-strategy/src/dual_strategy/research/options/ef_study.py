"""E/F audit and backtest on historical EOD SPY option quotes (2021-01-04 -> 2025-12-12). SIMULATED.

    PYTHONUTF8=1 .venv/Scripts/python.exe -m dual_strategy.research.options.ef_study

Every fill uses the NEXT session's 16:00 quote: ask for buys, bid for sells (+ optional adverse ticks). Thresholds are
chosen on 2021-22 only with one fixed rule (max daily Sharpe, >= MIN_TRADES trades, Sharpe > 0; else the pre-registered
value and a development FAIL). 2024-25 OOS was ALREADY INSPECTED for the earlier E/F variants (contamination disclosed in
reports/options_ef_methodology.md); new variants (E-drift, E-hv, F_credit) were designed after that inspection.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from dual_strategy.research.data import dividends, sessions
from dual_strategy.research.features import daily_on_calendar
from dual_strategy.research.fetch import ROOT
from dual_strategy.research.metrics import block_bootstrap, drawdown
from dual_strategy.research.options import montecarlo, strat_f
from dual_strategy.research.options.eod_backtest import (SEG, START_EQUITY, Chain, build_near, run_book,
                                                        sessions_between, session_index)
from dual_strategy.research.options.pricing import option_order_fee

OUT = ROOT / "reports" / "options_ef"
MIN_TRADES = {"E": 30, "F_long": 10, "F_credit": 30}
FEE_PS_4 = (4 * (0.65 + 0.30 + 0.015) + 2 * 0.00279) / 100      # per-share fee estimate used inside forecasts only


def seg_days(days, seg):
    lo, hi = SEG[seg]
    return [d for d in days if lo <= d <= hi]


def leg_spread(q):
    mid = (q[0] + q[1]) / 2
    return (q[1] - q[0]) / mid if mid > 0 else 9.9


def ok_leg(q, max_spread=0.20, min_oi=500):
    return q is not None and q[0] > 0 and q[1] > 0 and leg_spread(q) <= max_spread and q[4] >= min_oi


def rn_prob_above(chain, d, e, x, right):
    """Risk-neutral P(S_T > x) from adjacent-strike mids: calls -dC/dK, puts 1 - dP/dK (discounting ~1 at <=2 weeks).
    Both strikes need a live two-sided quote; a slope outside the no-arbitrage range [0, 1] returns None."""
    ks = chain.strikes.get((d, e))
    if ks is None or x <= ks[0] or x >= ks[-1]:
        return None
    j = int(np.searchsorted(ks, x))
    ka, kb = ks[j - 1], ks[j]
    qa, qb = chain.get(d, e, ka, right), chain.get(d, e, kb, right)
    if qa is None or qb is None or qa[0] <= 0 or qb[0] <= 0:
        return None
    ma, mb = (qa[0] + qa[1]) / 2, (qb[0] + qb[1]) / 2
    slope = (mb - ma) / (kb - ka)
    p = -slope if right == "C" else 1 - slope
    return float(p) if 0.0 <= p <= 1.0 else None


# ---------------------------------------------------------------- E: forecasts + candidates (all models at once)
def e_eval(chain, spot, lr, days, seed=7):
    allsess, idx = session_index()
    rng = np.random.default_rng(seed)
    cands, pit = {}, []
    for d in days:
        exps = [e for e in chain.exp_by_day.get(d, []) if 5 <= sessions_between(idx, d, e) <= 10]
        past = lr[lr.index <= d].to_numpy()[-500:]
        if not exps or len(past) < 500:
            continue
        e = exps[0]
        H = sessions_between(idx, d, e)
        S = spot[d]
        vol = montecarlo.ewma_vol(past)
        hv20 = float(np.std(past[-20:], ddof=1))
        drift = float(past.mean())
        boot = montecarlo.bootstrap_terminal(past, S, H, 10_000, 5, vol, rng)
        models = {"boot": boot, "drift": boot * math.exp(drift * H),
                  "hv": S * np.exp(-0.5 * hv20 ** 2 * H + hv20 * math.sqrt(H) * rng.standard_normal(10_000))}
        ST = spot.get(e) if e in spot.index else None
        if ST is not None and not math.isnan(ST):
            pit.append({"date": d, "H": H, **{f"pit_{m}": float(np.mean(t <= ST)) for m, t in models.items()}})
        out = []
        for right, sign in (("C", 1), ("P", -1)):
            kl = chain.nearest(d, e, S)
            for w in (0.01, 0.02):
                ks = chain.nearest(d, e, S * (1 + sign * w))
                if kl is None or ks is None or (ks - kl) * sign <= 0:
                    continue
                ql, qs = chain.get(d, e, kl, right), chain.get(d, e, ks, right)
                if not (ok_leg(ql) and ok_leg(qs)):
                    continue
                debit, width = ql[1] - qs[0], abs(ks - kl)
                if not 0 < debit < width:
                    continue
                c = {"date": d, "expiry": e, "H": H, "right": right, "w": w, "k_long": kl, "k_short": ks, "debit": debit,
                     "width": width, "leg_spread_max": max(leg_spread(ql), leg_spread(qs)), "S": S}
                be = kl + sign * (debit + FEE_PS_4)
                for m, t in models.items():
                    net = np.clip((t - kl) * sign, 0, width) - debit - FEE_PS_4
                    c[f"edge_{m}"] = float(net.mean() / (debit + FEE_PS_4))
                    c[f"p_loss_{m}"] = float(np.mean(net < 0))
                pa = rn_prob_above(chain, d, e, be, right)
                c["p_loss_rn"] = None if pa is None else (1 - pa if right == "C" else pa)
                if ST is not None and not math.isnan(ST):
                    pay = min(max((ST - kl) * sign, 0.0), width)
                    c["realised_payoff"], c["realised_loss_at_expiry"] = pay, int(pay - debit - FEE_PS_4 < 0)
                out.append(c)
        if out:
            cands[d] = out
    return cands, pd.DataFrame(pit)


def run_e(chain, spot, days, cands, model, min_edge, max_p_loss, *, rand=None, always=False, ticks=0,
          spread_mult=1.0, risk=0.005, max_spread=0.10, tp=True):
    rng = np.random.default_rng(rand) if rand is not None else None

    def decide(d, book):
        if d not in cands or len(book.open) >= 3:
            return []
        pool = [c for c in cands[d] if c["leg_spread_max"] <= max_spread]
        if rng is not None:
            if not pool or rng.random() > always:      # `always` carries the entry probability for random baselines
                return []
            c = pool[rng.integers(len(pool))]
        elif always is True:
            if not pool:
                return []
            c = max(pool, key=lambda c: -c["debit"] / c["width"])
        else:
            ok = [c for c in pool if c[f"edge_{model}"] >= min_edge and c[f"p_loss_{model}"] <= max_p_loss]
            if not ok:
                return []
            c = max(ok, key=lambda c: c[f"edge_{model}"])
        n = int(risk * book.mark(d) // (c["debit"] * 100 + 9.2))
        if n < 1:
            return []
        return [{"kind": "E_vertical", "expiry": c["expiry"], "legs": [(c["k_long"], c["right"], 1), (c["k_short"], c["right"], -1)],
                 "n": n, "meta": {"width": c["width"], "w": c["w"], "H": c["H"], "right": c["right"],
                                  "p_loss_pred": round(c.get(f"p_loss_{model}", float("nan")), 4)}}]

    def tp_check(d, book, p):
        if not tp:
            return False
        (kl, r, _), (ks, _, _) = p.legs
        ql, qs = book.chain.get(d, p.expiry, kl, r), book.chain.get(d, p.expiry, ks, r)
        if ql is None or qs is None:
            return False
        debit = -p.entry_cash / (100 * p.n)
        return (ql[0] - qs[1]) >= debit + 0.5 * (p.meta["width"] - debit)

    return run_book(days, chain, spot, decide, tp_check, spread_mult, False, 3, ticks)


# ---------------------------------------------------------------- F
def f_eval(chain, spot, fc: dict, days):
    allsess, idx = session_index()
    out = {}
    for d in days:
        exps = [e for e in chain.exp_by_day.get(d, []) if 7 <= (e - d).days <= 14]
        if not exps:
            continue
        e = min(exps, key=lambda e: abs((e - d).days - 10))
        S = spot[d]
        k0 = chain.nearest(d, e, S)
        qc, qp = chain.get(d, e, k0, "C"), chain.get(d, e, k0, "P")
        if not (ok_leg(qc) and ok_leg(qp)):
            continue
        iv = (qc[5] + qp[5]) / 2
        T = (e - d).days / 365
        em = S * iv * math.sqrt(T)
        sc, sp = chain.nearest(d, e, S + em), chain.nearest(d, e, S - em)
        wing = max(5.0, round(0.5 * em))
        lc, lp = chain.nearest(d, e, sc + wing), chain.nearest(d, e, sp - wing)
        legs_c = [(sc, "C", -1), (sp, "P", -1), (lc, "C", 1), (lp, "P", 1)]
        qs = [chain.get(d, e, k, r) for k, r, _ in legs_c]
        condor_ok = (sc is not None and sp is not None and lc > sc and lp < sp and sc > k0 and sp < k0
                     and all(ok_leg(q) for q in qs))
        prev = [x for x in allsess if x < e]
        out[d] = {"expiry": e, "k0": k0, "iv": float(iv), "em": em, "condor": legs_c if condor_ok else None,
                  "straddle_spread": max(leg_spread(qc), leg_spread(qp)), "exit_after": prev[-1] if prev else None,
                  **{f"fc_{m}": fc[m].get(d) for m in fc}}
    return out


def run_f(chain, spot, days, sig, kind, model, threshold, *, naive=False, ticks=0, spread_mult=1.0, risk=None,
          max_spread=0.10, tp=True):
    risk = risk if risk is not None else (0.02 if kind == "long" else 0.01)
    max_open = 1 if kind == "long" else 3

    def decide(d, book):
        s = sig.get(d)
        if s is None or len(book.open) >= max_open:
            return []
        f = s.get(f"fc_{model}")
        if f is None or not math.isfinite(f):
            return []
        if kind == "long":
            if s["straddle_spread"] > max_spread or not (naive or 100 * (s["iv"] - f) <= -threshold):
                return []
            legs = [(s["k0"], "C", 1), (s["k0"], "P", 1)]
            q = [book.chain.get(d, s["expiry"], k, r) for k, r, _ in legs]
            per = (q[0][1] + q[1][1]) * 100 + 5.0
            meta = {}
        else:
            if s["condor"] is None or any(b in book.open for b in []) or not (naive or s["iv"] / f >= threshold):
                return []
            if any(o.expiry == s["expiry"] for o in book.open):          # no stacking on the same expiry
                return []
            legs = s["condor"]
            q = [book.chain.get(d, s["expiry"], k, r) for k, r, _ in legs]
            if max(leg_spread(x) for x in q) > max_spread:
                return []
            credit = q[0][0] + q[1][0] - q[2][1] - q[3][1]
            wing = max(legs[2][0] - legs[0][0], legs[1][0] - legs[3][0])
            per = (wing - credit) * 100 + 10.0
            if credit <= 0 or per <= 0:
                return []
            meta = {"credit_planned": credit, "wing": wing}
        n = int(risk * book.mark(d) // per)
        if n < 1:
            return []
        meta.update({"iv": round(s["iv"], 4), "forecast": round(f, 4)})
        mlf = None
        if kind == "credit":
            wing = meta["wing"]
            mlf = lambda quotes, wing=wing: (wing - (quotes[0][0] + quotes[1][0] - quotes[2][1] - quotes[3][1])) * 100
        if mlf:
            meta["max_loss_per_contract_fn"] = mlf
        return [{"kind": f"F_{kind}", "expiry": s["expiry"], "legs": legs, "n": n, "meta": meta, "exit_after": s["exit_after"]}]

    def tp_check(d, book, p):
        if not tp:
            return False
        vals = [book.chain.get(d, p.expiry, k, r) for k, r, _ in p.legs]
        if any(v is None for v in vals):
            return False
        if p.kind == "F_credit":
            cost = sum((v[1] if sg < 0 else -v[0]) for v, (k, r, sg) in zip(vals, p.legs))
            return cost <= 0.5 * (p.entry_cash / (100 * p.n))
        return sum(v[0] for v in vals) >= 1.5 * (-p.entry_cash / (100 * p.n))

    return run_book(days, chain, spot, decide, tp_check, spread_mult, False, max_open, ticks)


def early_assignment_audit(chain, spot, trades: pd.DataFrame) -> dict:
    """Short legs that were ITM with extrinsic value below the next SPY dividend (calls) or ~0 (puts) while open."""
    divs = {d: v for d, v in dividends("US.SPY").items()}
    allsess = sessions()
    flagged, checked = 0, 0
    for t in trades.itertuples():
        legs = json.loads(t.legs)
        days = [d for d in allsess if pd.Timestamp(t.entry_date).date() <= d <= pd.Timestamp(t.exit_date).date()]
        exp = pd.Timestamp(t.expiry).date()
        hit = False
        for d in days:
            S = spot.get(d)
            for k, r, sg in legs:
                if sg > 0 or S is None:
                    continue
                q = chain.get(d, exp, float(k), r)
                if q is None:
                    continue
                intr = max(S - k, 0) if r == "C" else max(k - S, 0)
                if intr <= 0:
                    continue
                extr = (q[0] + q[1]) / 2 - intr
                nxt = [v for x, v in divs.items() if d < x <= exp]
                if (r == "C" and nxt and extr < nxt[0]) or (r == "P" and extr < 0.01):
                    hit = True
        checked += 1
        flagged += hit
    return {"positions_checked": checked, "positions_with_early_assignment_risk": flagged}


# ---------------------------------------------------------------- metrics
def summarize(name, seg, book, eq, spy_ret=None) -> dict:
    tr = pd.DataFrame(book.trades)
    ret = eq["ret"].to_numpy()
    pnl = tr["net_pnl"].to_numpy() if len(tr) else np.array([])
    wins, losses = pnl[pnl > 0], pnl[pnl <= 0]
    sd = ret.std(ddof=1) if len(ret) > 1 else 0
    down = np.sqrt(np.mean(np.minimum(ret, 0) ** 2))
    _, dd = drawdown(eq["equity"].to_numpy(), START_EQUITY)
    boot = block_bootstrap(ret)
    car = (eq["capital_at_risk"] / eq["equity"]).mean() if "capital_at_risk" in eq else float("nan")
    net = float(eq["equity"].iloc[-1] - START_EQUITY)
    fees = float(book.stats.get("fees", 0.0))
    meaningful = len(pnl) >= 30 and len(ret) >= 120
    r = {"variant": name, "segment": seg, "start": str(eq.index[0]), "end": str(eq.index[-1]), "sessions": len(eq),
         "trades": len(pnl), "net_pnl": round(net, 2), "gross_pnl_before_fees": round(net + fees, 2), "fees": round(fees, 2),
         "net_return_pct": round(100 * net / START_EQUITY, 3),
         "annualised_pct": round(100 * ((1 + net / START_EQUITY) ** (252 / len(ret)) - 1), 2) if len(ret) >= 120 else None,
         "expectancy_usd": round(float(pnl.mean()), 2) if len(pnl) else None,
         "expectancy_per_max_loss": round(float(pnl.sum() / tr["max_loss"].sum()), 4) if len(pnl) and tr["max_loss"].sum() > 0 else None,
         "win_rate_pct": round(100 * len(wins) / len(pnl), 1) if len(pnl) else None,
         "avg_win": round(float(wins.mean()), 2) if len(wins) else None, "avg_loss": round(float(losses.mean()), 2) if len(losses) else None,
         "profit_factor": round(float(wins.sum() / -losses.sum()), 3) if len(losses) and losses.sum() < 0 else None,
         "sharpe": round(float(ret.mean() / sd * math.sqrt(252)), 3) if meaningful and sd > 0 else None,
         "sortino": round(float(ret.mean() / down * math.sqrt(252)), 3) if meaningful and down > 0 else None,
         "sharpe_ci95": boot["sharpe_ci"] if meaningful else None, "max_drawdown_pct": round(100 * dd, 3),
         "p_day_loss_gt_1pct": round(float(np.mean(ret < -0.01)), 4), "worst_trade": round(float(pnl.min()), 2) if len(pnl) else None,
         "avg_capital_at_risk_pct": round(100 * float(car), 3),
         "return_per_avg_capital_at_risk": round(net / START_EQUITY / car, 3) if car and car > 0 else None,
         "estimated_fill_share": round(book.stats["intrinsic_exit_legs"] / max(1, book.stats["legs_filled"]), 4),
         "partial_fills": book.stats["partial"], "skipped_no_quote": book.stats["skipped_no_quote"],
         "sample_note": "OK" if meaningful else "SMALL SAMPLE - no Sharpe reported"}
    if spy_ret is not None:
        s = spy_ret.reindex(eq.index).fillna(0).to_numpy()
        r["spy_buy_hold_pct"] = round(100 * (np.prod(1 + s) - 1), 2)
        r["spy_matched_to_capital_at_risk_pct"] = round(100 * (np.prod(1 + car * s) - 1), 2) if car == car else None
    return r


def select(grid: list[dict], kind: str, default):
    el = [g for g in grid if g["trades"] >= MIN_TRADES[kind] and g["sharpe"] is not None and g["sharpe"] > 0]
    return (max(el, key=lambda g: g["sharpe"])["threshold"], True) if el else (default, False)


def two_level_mc(ret: np.ndarray, outer=200, inner=2000, seed=11) -> dict:
    """Point estimates from 10,000 paths on the actual series + 90% intervals from re-sampling the series itself."""
    base = montecarlo.path_risk(montecarlo.block_bootstrap_paths(ret, 8, 10_000, 4))
    rng = np.random.default_rng(seed)
    keys = ("median_pct", "p_profit", "p_loss_gt_3pct", "p5_pct", "cvar95_pct", "p_dd_ge_5pct")
    acc = {k: [] for k in keys}
    n = len(ret)
    for i in range(outer):
        starts = rng.integers(0, n, math.ceil(n / 10))
        res = np.concatenate([np.r_[ret, ret[:10]][s:s + 10] for s in starts])[:n]
        r = montecarlo.path_risk(montecarlo.block_bootstrap_paths(res, 8, inner, 4, seed=seed + i))
        for k in keys:
            acc[k].append(r[k])
    return {**{k: round(base[k], 4) for k in keys}, **{f"{k}_ci90": [round(float(np.percentile(acc[k], 5)), 4),
                                                                    round(float(np.percentile(acc[k], 95)), 4)] for k in keys}}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    chain = Chain(build_near())
    spot = daily_on_calendar("US.SPY", 0)["close"]
    adj = daily_on_calendar("US.SPY", 1)["close"]
    lr = np.log(adj / adj.shift(1)).dropna()
    spy_ret = adj.pct_change()
    days = sorted(chain.exp_by_day)
    print("E: forecasts/candidates...", flush=True)
    cands, pit = e_eval(chain, spot, lr, days)
    # ---------------- calibration (all candidates, hold-to-expiry outcome, decision-time probabilities)
    cdf = pd.DataFrame([c for v in cands.values() for c in v])
    cdf["segment"] = np.select([cdf["date"] < SEG["val"][0], cdf["date"] < SEG["oos"][0]], ["dev", "val"], "oos")
    cal = []
    lab = cdf.dropna(subset=["realised_loss_at_expiry", "p_loss_rn"])
    for seg, g in lab.groupby("segment"):
        y = g["realised_loss_at_expiry"].to_numpy()
        base = cdf[(cdf["segment"] == "dev")]["realised_loss_at_expiry"].mean()
        row = {"segment": seg, "candidates": len(g), "realised_loss_rate": round(float(y.mean()), 4)}
        for m in ("boot", "drift", "hv", "rn"):
            p = np.clip(g[f"p_loss_{m}"].to_numpy(float), 1e-4, 1 - 1e-4)
            row[f"brier_{m}"] = round(float(np.mean((p - y) ** 2)), 5)
            row[f"logloss_{m}"] = round(float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))), 5)
            row[f"mean_pred_{m}"] = round(float(p.mean()), 4)
        row["brier_climatology_dev_rate"] = round(float(np.mean((base - y) ** 2)), 5)
        cal.append(row)
    rel = []
    for m in ("boot", "drift", "hv", "rn"):
        g = lab[lab["segment"] == "oos"]
        b = pd.cut(g[f"p_loss_{m}"].astype(float), np.linspace(0, 1, 11), include_lowest=True)
        for k, h in g.groupby(b, observed=True):
            rel.append({"model": m, "bin": str(k), "n": len(h), "mean_pred": round(float(h[f"p_loss_{m}"].mean()), 4),
                        "realised": round(float(h["realised_loss_at_expiry"].mean()), 4)})
    pit["segment"] = np.select([pit["date"] < SEG["val"][0], pit["date"] < SEG["oos"][0]], ["dev", "val"], "oos")
    cov = []
    for seg, g in pit.groupby("segment"):
        for m in ("boot", "drift", "hv"):
            u = g[f"pit_{m}"].to_numpy()
            cov.append({"segment": seg, "model": m, "n": len(u), "cover_50": round(float(np.mean((u > .25) & (u < .75))), 3),
                        "cover_90": round(float(np.mean((u > .05) & (u < .95))), 3), "pit_mean": round(float(u.mean()), 3),
                        "pit_hist10": np.histogram(u, bins=10, range=(0, 1))[0].tolist()})
    # ---------------- E thresholds on dev
    grid_e = {}
    sel_e = {}
    dev = seg_days(days, "dev")
    for m in ("boot", "drift", "hv"):
        grid_e[m] = []
        for me in (0.0, 0.05, 0.10, 0.20, 0.30):
            for pl in (0.55, 0.65, 0.75):
                b, eq = run_e(chain, spot, dev, cands, m, me, pl)
                s = summarize("g", "dev", b, eq)
                grid_e[m].append({"threshold": (me, pl), "trades": s["trades"],
                                  "sharpe": round(float(eq["ret"].mean() / eq["ret"].std() * math.sqrt(252)), 3) if s["trades"] else None,
                                  "net_return_pct": s["net_return_pct"]})
        sel_e[m] = select(grid_e[m], "E", (0.10, 0.60))
    # ---------------- F forecasts and thresholds on dev
    from dual_strategy.research import run as abc
    eng = abc.Engine()
    daily = daily_on_calendar("US.SPY", 1).reindex(eng.spy.days)
    rv = strat_f.daily_realized_variance(eng.spy.b1, daily["open"].to_numpy(), daily["close"].to_numpy())
    har7 = pd.Series(strat_f.har_forecasts(rv, strat_f.FConfig(horizon=7)), index=eng.spy.days).shift(-1)
    hv20 = pd.Series(np.sqrt(252 * pd.Series(rv).rolling(20, min_periods=18).mean().to_numpy()), index=eng.spy.days)
    sig = f_eval(chain, spot, {"har": har7.to_dict(), "hv": hv20.to_dict()}, days)
    grid_f, sel_f = {}, {}
    for kind, ths, default in (("long", (0.0, 2.0, 4.0, 6.0), 0.0), ("credit", (1.0, 1.1, 1.2, 1.3, 1.5), 1.2)):
        for m in ("har", "hv"):
            key = f"{kind}_{m}"
            grid_f[key] = []
            for th in ths:
                b, eq = run_f(chain, spot, dev, sig, kind, m, th)
                s = summarize("g", "dev", b, eq)
                grid_f[key].append({"threshold": th, "trades": s["trades"], "net_return_pct": s["net_return_pct"],
                                    "sharpe": round(float(eq["ret"].mean() / eq["ret"].std() * math.sqrt(252)), 3) if s["trades"] and eq["ret"].std() > 0 else None})
            sel_f[key] = select(grid_f[key], "F_long" if kind == "long" else "F_credit", default)
    freeze = {"E": {m: {"selected": sel_e[m][0], "development_pass": sel_e[m][1], "grid_dev": grid_e[m]} for m in sel_e},
              "F": {k: {"selected": sel_f[k][0], "development_pass": sel_f[k][1], "grid_dev": grid_f[k]} for k in sel_f}}
    (OUT / "ef_freeze.json").write_text(json.dumps(freeze, indent=1, default=str), encoding="utf-8")
    print("frozen", {k: v["selected"] for k, v in freeze["E"].items()}, {k: v["selected"] for k, v in freeze["F"].items()}, flush=True)
    # ---------------- variants on dev / val / oos
    V = {}
    for m in ("boot", "drift", "hv"):
        me, pl = sel_e[m][0]
        V[f"E_{m}"] = lambda d, m=m, me=me, pl=pl, **kw: run_e(chain, spot, d, cands, m, me, pl, **kw)
    V["E_always_same_structure"] = lambda d, **kw: run_e(chain, spot, d, cands, "boot", 0, 1, always=True, **kw)
    for key, (th, _) in sel_f.items():
        kind, m = key.split("_")
        V[f"F_{kind}_{m}"] = lambda d, kind=kind, m=m, th=th, **kw: run_f(chain, spot, d, sig, kind, m, th, **kw)
    V["F_long_naive"] = lambda d, **kw: run_f(chain, spot, d, sig, "long", "har", 0, naive=True, **kw)
    V["F_credit_naive"] = lambda d, **kw: run_f(chain, spot, d, sig, "credit", "har", 0, naive=True, **kw)
    rows, trades, curves = [], [], {}
    for name, fn in V.items():
        for seg in ("dev", "val", "oos"):
            b, eq = fn(seg_days(days, seg))
            r = summarize(name, seg, b, eq, spy_ret)
            r["stress"] = "baseline"
            rows.append(r)
            trades += [{**t, "variant": name, "segment": seg} for t in b.trades]
            if seg == "oos":
                curves[name] = eq["ret"]
        print("ran", name, flush=True)
    # random-timestamp baseline for E (same structure, matched entry frequency), 200 seeds
    rand = []
    e_main = "E_boot"
    for seg in ("val", "oos"):
        sd = seg_days(days, seg)
        n_e = next(r["trades"] for r in rows if r["variant"] == e_main and r["segment"] == seg)
        elig = sum(1 for d in sd if d in cands)
        prob = min(1.0, n_e / max(1, elig) * 1.5)
        for s in range(200):
            b, eq = run_e(chain, spot, sd, cands, "boot", 0, 1, rand=s, always=prob)
            rand.append({"segment": seg, "seed": s, "net_return_pct": round(100 * (eq["equity"].iloc[-1] / START_EQUITY - 1), 3),
                         "trades": len(b.trades)})
    # sensitivity on OOS
    sens = []
    oos = seg_days(days, "oos")
    for name in ("E_boot", "E_drift", "F_long_har", "F_credit_har"):
        for sname, kw in {"+1 tick/leg": {"ticks": 1}, "+2 ticks/leg": {"ticks": 2}, "spread x2": {"spread_mult": 2.0},
                          "max leg spread 5%": {"max_spread": 0.05}, "max leg spread 20%": {"max_spread": 0.20},
                          "half size": {"risk": (0.0025 if name.startswith("E") else 0.01 if "long" in name else 0.005)},
                          "double size": {"risk": (0.01 if name.startswith("E") else 0.04 if "long" in name else 0.02)},
                          "no take-profit": {"tp": False}}.items():
            b, eq = V[name](oos, **kw)
            r = summarize(name, "oos", b, eq, spy_ret)
            r["stress"] = sname
            sens.append(r)
    # descriptive only (NOT used for selection): how F_credit behaves across the threshold grid on OOS, and how often
    # the IV/forecast ratio reaches each level
    desc = []
    for m in ("har", "hv"):
        for th in (0.9, 1.0, 1.1, 1.2, 1.3):
            b, eq = run_f(chain, spot, oos, sig, "credit", m, th)
            r = summarize(f"F_credit_{m}", "oos", b, eq, spy_ret)
            r["stress"] = f"DESCRIPTIVE ratio>={th}"
            desc.append(r)
    ratio = pd.DataFrame([{"date": d, "ratio_har": s["iv"] / s["fc_har"] if s.get("fc_har") else np.nan,
                           "ratio_hv": s["iv"] / s["fc_hv"] if s.get("fc_hv") else np.nan} for d, s in sig.items()])
    ratio["segment"] = np.select([ratio["date"] < SEG["val"][0], ratio["date"] < SEG["oos"][0]], ["dev", "val"], "oos")
    ratio.groupby("segment")[["ratio_har", "ratio_hv"]].describe(percentiles=[.1, .5, .9]).round(3).to_csv(OUT / "ef_iv_forecast_ratio.csv")
    sens += desc
    tdf = pd.DataFrame(trades)
    ea = early_assignment_audit(chain, spot, tdf[(tdf["variant"].str.startswith("F_credit")) & (tdf["segment"] == "oos")])
    mc = {name: two_level_mc(r.to_numpy()) for name, r in curves.items()}
    pd.DataFrame(rows + sens).to_csv(OUT / "ef_results.csv", index=False)
    tdf.to_csv(OUT / "ef_trades.csv", index=False)
    pd.DataFrame(rand).to_csv(OUT / "ef_random_baseline.csv", index=False)
    pd.DataFrame(cal).to_csv(OUT / "ef_calibration_scores.csv", index=False)
    pd.DataFrame(rel).to_csv(OUT / "ef_reliability_oos.csv", index=False)
    pd.DataFrame(cov).to_csv(OUT / "ef_coverage_pit.csv", index=False)
    pd.DataFrame(curves).to_csv(OUT / "ef_oos_daily_returns.csv")
    (OUT / "ef_monte_carlo.json").write_text(json.dumps(mc, indent=1), encoding="utf-8")
    (OUT / "ef_early_assignment.json").write_text(json.dumps(ea, indent=1), encoding="utf-8")
    reliability_svg(pd.DataFrame(rel), OUT / "ef_reliability_oos.svg")
    print(pd.DataFrame(cal).to_string(index=False))
    print(pd.DataFrame(rows)[["variant", "segment", "trades", "net_return_pct", "sharpe", "max_drawdown_pct", "win_rate_pct",
                              "expectancy_per_max_loss", "avg_capital_at_risk_pct", "sample_note"]].to_string(index=False))


def reliability_svg(rel: pd.DataFrame, path) -> None:
    W, H, L, B, T, R = 660, 460, 60, 50, 40, 230
    X = lambda v: L + v * (W - L - R)
    Y = lambda v: H - B - v * (H - B - T)
    colors = {"boot": "#2a78d6", "drift": "#eb6834", "hv": "#1baf7a", "rn": "#6250d6"}
    names = {"boot": "E bootstrap (zero drift)", "drift": "E bootstrap + drift", "hv": "Lognormal HV20", "rn": "Option-implied (risk-neutral)"}
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" font-family="sans-serif" font-size="11">',
           f'<rect width="{W}" height="{H}" fill="#fcfcfb"/>',
           f'<text x="{L}" y="22" font-size="13" fill="#0b0b0b">P(debit spread loses at expiry): predicted vs realised, OOS 2024-25</text>']
    for v in (0, .25, .5, .75, 1):
        out.append(f'<line x1="{X(0)}" y1="{Y(v)}" x2="{X(1)}" y2="{Y(v)}" stroke="#e6e5e0"/>'
                   f'<text x="{L - 8}" y="{Y(v) + 4}" text-anchor="end" fill="#52514e">{v:.2f}</text>'
                   f'<text x="{X(v)}" y="{H - B + 16}" text-anchor="middle" fill="#52514e">{v:.2f}</text>')
    out.append(f'<line x1="{X(0)}" y1="{Y(0)}" x2="{X(1)}" y2="{Y(1)}" stroke="#8a8984" stroke-dasharray="4 4"/>')
    for i, (m, g) in enumerate(rel.groupby("model")):
        g = g[g["n"] >= 20].sort_values("mean_pred")
        pts = " ".join(f"{X(a):.1f},{Y(b):.1f}" for a, b in zip(g["mean_pred"], g["realised"]))
        out.append(f'<polyline points="{pts}" fill="none" stroke="{colors[m]}" stroke-width="2"/>')
        for a, b in zip(g["mean_pred"], g["realised"]):
            out.append(f'<circle cx="{X(a):.1f}" cy="{Y(b):.1f}" r="4" fill="{colors[m]}" stroke="#fcfcfb" stroke-width="2"/>')
        out.append(f'<rect x="{W - R + 12}" y="{T + 18 * i}" width="14" height="3" fill="{colors[m]}"/>'
                   f'<text x="{W - R + 30}" y="{T + 18 * i + 5}" fill="#0b0b0b">{names[m]}</text>')
    out.append(f'<text x="{(X(0) + X(1)) / 2}" y="{H - 12}" text-anchor="middle" fill="#52514e">predicted probability (bins with n &gt;= 20)</text>'
               f'<text x="16" y="{H / 2}" transform="rotate(-90 16 {H / 2})" text-anchor="middle" fill="#52514e">realised frequency</text>'
               f'<text x="{W - R + 12}" y="{T + 90}" fill="#52514e">dashed = perfect calibration</text></svg>')
    path.write_text("\n".join(out), encoding="utf-8")


if __name__ == "__main__":
    main()
