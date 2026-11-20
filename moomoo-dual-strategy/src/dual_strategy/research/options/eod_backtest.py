"""End-of-day historical backtest of Strategies E and F on real SPY option quotes (free EOD mirror, 2021-01 -> 2025-12-12).

    PYTHONUTF8=1 .venv/Scripts/python.exe -m dual_strategy.research.options.eod_backtest

EOD adaptation, fixed before any E/F option result was seen (see reports/options_eod_backtest.md):
- Decisions use the 16:00 ET snapshot of day d; entries fill at day d+1's quotes (ask for buys, bid for sells).
  Take-profit triggers observed at d also fill at d+1. Only scheduled time exits fill at their own snapshot.
- Fees: handbook option fees per leg order. Fill size capped at the displayed quote size. No model prices are ever
  used as fills; a leg with no quote on an exit day is valued at intrinsic (counted and reported).
- E: 0.5% equity max loss per trade, <=3 open, 1 new/day; thresholds chosen on 2021-22 only.
- F: 2% equity max loss per trade (one ATM SPY straddle costs ~$800-1,200), 1 open; HAR horizon 7 sessions to match
  7-14 DTE; ATM IV = mean of the dataset's ATM call and put IV; thresholds chosen on 2021-22 only (HAR warm-up ->
  first forecast 2022-02).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from dual_strategy.research.data import sessions
from dual_strategy.research.features import daily_on_calendar
from dual_strategy.research.fetch import ROOT
from dual_strategy.research.metrics import block_bootstrap, drawdown
from dual_strategy.research.options import montecarlo, strat_f
from dual_strategy.research.options.pricing import option_order_fee
from dual_strategy.research.options.validate_chains import load_year

NEAR = ROOT / "data-cache" / "options_history" / "spy_near_2021_2025.parquet"
OUT = ROOT / "reports" / "options_eod"
SEG = {"dev": (date(2021, 1, 4), date(2022, 12, 30)), "val": (date(2023, 1, 3), date(2023, 12, 29)),
       "oos": (date(2024, 1, 2), date(2025, 12, 12))}
START_EQUITY = 50_000.0


# ---------------------------------------------------------------- data
def build_near() -> pd.DataFrame:
    if NEAR.exists():
        return pd.read_parquet(NEAR)
    sess = set(sessions())
    spot = daily_on_calendar("US.SPY", 0)["close"]
    frames = []
    for y in range(2021, 2026):
        df = load_year(y, ["expiration", "strike", "type", "bid", "bid_size", "ask", "ask_size", "open_interest",
                           "date", "implied_volatility"])
        df = df[df["date"].isin(sess)]
        dte = np.array([(e - d).days for e, d in zip(df["expiration"], df["date"])])
        s = df["date"].map(spot).to_numpy(float)
        keep = (dte >= 0) & (dte <= 21) & (np.abs(df["strike"].to_numpy() / s - 1) <= 0.08)
        df = df[keep].copy()
        df["right"] = np.where(df["type"] == "call", "C", "P")
        frames.append(df.drop(columns=["type"]))
    out = pd.concat(frames, ignore_index=True)
    NEAR.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(NEAR, index=False)
    return out


class Chain:
    def __init__(self, df: pd.DataFrame):
        self.q = {}
        self.exp_by_day: dict[date, list[date]] = {}
        self.strikes: dict[tuple, np.ndarray] = {}
        for r in df.itertuples(index=False):
            self.q[(r.date, r.expiration, float(r.strike), r.right)] = (r.bid, r.ask, r.bid_size, r.ask_size,
                                                                       r.open_interest, r.implied_volatility)
        for (d, e), g in df.groupby(["date", "expiration"]):
            self.strikes[(d, e)] = np.sort(g["strike"].unique().astype(float))
        for d, g in df.groupby("date"):
            self.exp_by_day[d] = sorted(g["expiration"].unique())

    def get(self, d, e, k, right):
        return self.q.get((d, e, float(k), right))

    def nearest(self, d, e, x):
        ks = self.strikes.get((d, e))
        return None if ks is None or not len(ks) else float(ks[np.argmin(np.abs(ks - x))])


# ---------------------------------------------------------------- portfolio engine
@dataclass
class Pos:
    kind: str
    expiry: date
    legs: list            # [(strike, right, sign)] sign +1 long / -1 short, per contract
    n: int
    entry_date: date
    entry_cash: float     # signed cash flow at entry incl. fees (negative = paid)
    max_loss: float
    meta: dict = field(default_factory=dict)
    exit_after: date | None = None      # scheduled time exit on/after this session


@dataclass
class Book:
    chain: Chain
    spot: pd.Series
    spread_mult: float = 1.0
    ticks: int = 0                       # extra adverse ticks ($0.01) per leg fill (stress)
    cash: float = START_EQUITY
    open: list = field(default_factory=list)
    trades: list = field(default_factory=list)
    stats: dict = field(default_factory=lambda: {"partial": 0, "skipped_no_quote": 0, "intrinsic_exit_legs": 0,
                                                 "legs_filled": 0, "fees": 0.0})
    risk_log: list = field(default_factory=list)

    def _px(self, quote, buy: bool) -> float:
        bid, ask = quote[0], quote[1]
        mid, half = (bid + ask) / 2, (ask - bid) / 2
        t = 0.01 * self.ticks
        return mid + self.spread_mult * half + t if buy else max(mid - self.spread_mult * half - t, 0.0)

    def enter(self, d, kind, expiry, legs, n, meta, exit_after=None) -> Pos | None:
        quotes = [self.chain.get(d, expiry, k, r) for k, r, s in legs]
        if any(q is None or (s < 0 and q[0] <= 0) or q[1] <= 0 for q, (k, r, s) in zip(quotes, legs)):
            self.stats["skipped_no_quote"] += 1
            return None
        size = min((q[3] if s > 0 else q[2]) for q, (k, r, s) in zip(quotes, legs))
        if size and size < n:
            self.stats["partial"] += 1
            n = int(size)
        if n < 1:
            return None
        cash, entry_fees = 0.0, 0.0
        for q, (k, r, s) in zip(quotes, legs):
            px = self._px(q, buy=s > 0)
            f = option_order_fee(n, px, sell=s < 0)
            cash += -s * px * 100 * n - f
            entry_fees += f
            self.stats["fees"] += f
            self.stats["legs_filled"] += 1
        per = meta.get("max_loss_per_contract_fn")
        max_loss = per(quotes) * n + entry_fees if per else -cash      # always includes entry fees
        p = Pos(kind, expiry, legs, n, d, cash, max_loss, {k: v for k, v in meta.items() if k != "max_loss_per_contract_fn"},
                exit_after)
        self.cash += cash
        self.open.append(p)
        return p

    def leg_value(self, d, p, k, r, mode):
        q = self.chain.get(d, p.expiry, k, r)
        if q is None or (q[0] <= 0 and q[1] <= 0):
            s = self.spot[d]
            return (max(s - k, 0.0) if r == "C" else max(k - s, 0.0)), True
        if mode == "mid":
            return (q[0] + q[1]) / 2, False
        return self._px(q, buy=(mode == "buy")), False

    def close(self, d, p, reason):
        s = self.spot[d]
        intrinsic = sum(sg * (max(s - k, 0.0) if r == "C" else max(k - s, 0.0)) for k, r, sg in p.legs)
        cash = 0.0
        expired = d >= p.expiry
        for k, r, sg in p.legs:
            itm = (s > k) if r == "C" else (s < k)
            if expired and not itm:
                continue                                   # expires worthless: no order, no fee
            v, used_intr = self.leg_value(d, p, k, r, "sell" if sg > 0 else "buy")
            self.stats["intrinsic_exit_legs"] += used_intr
            self.stats["legs_filled"] += 1
            f = option_order_fee(p.n, v, sell=sg > 0)
            cash += sg * v * 100 * p.n - f
            self.stats["fees"] += f
        self.cash += cash
        self.open.remove(p)
        pnl = p.entry_cash + cash
        self.trades.append({"label": "SIMULATED on historical EOD quotes", "strategy": p.kind[0], "kind": p.kind,
                            "entry_date": p.entry_date.isoformat(), "exit_date": d.isoformat(), "expiry": p.expiry.isoformat(),
                            "legs": json.dumps(p.legs), "contracts": p.n, "entry_cash": round(p.entry_cash, 2),
                            "exit_cash": round(cash, 2), "net_pnl": round(pnl, 2), "max_loss": round(p.max_loss, 2),
                            "r_multiple": round(pnl / p.max_loss, 4) if p.max_loss > 0 else None, "exit_reason": reason,
                            "intrinsic_at_exit": round(intrinsic * 100 * p.n, 2), **{f"meta_{k}": v for k, v in p.meta.items()}})

    def mark(self, d) -> float:
        val = 0.0
        for p in self.open:
            for k, r, sg in p.legs:
                v, _ = self.leg_value(d, p, k, r, "mid")
                val += sg * v * 100 * p.n
        return self.cash + val


def run_book(days, chain, spot, decide, tp_check, spread_mult=1.0, same_snapshot=False, max_open=3, ticks=0):
    """Generic daily loop. decide(d, book) -> list of entry orders; tp_check(d, book, pos) -> bool."""
    book = Book(chain, spot, spread_mult, ticks)
    pending_entries, pending_exits, rows = [], [], []
    for i, d in enumerate(days):
        for p in [p for p in pending_exits if p in book.open]:
            book.close(d, p, "take_profit")
        pending_exits = []
        for order in pending_entries:
            if len(book.open) < max_open and d < order["expiry"]:
                book.enter(d, order["kind"], order["expiry"], order["legs"], order["n"], order["meta"], order.get("exit_after"))
        pending_entries = []
        for p in list(book.open):
            if d >= p.expiry:
                book.close(d, p, "expiry")
            elif p.exit_after is not None and d >= p.exit_after:
                book.close(d, p, "time_exit")
        eqv = book.mark(d)
        rows.append({"date": d, "equity": eqv, "open": len(book.open),
                     "capital_at_risk": sum(p.max_loss for p in book.open)})
        for p in book.open:
            if tp_check(d, book, p):
                pending_exits.append(p)
        orders = decide(d, book) if i + 1 < len(days) else []
        if same_snapshot:
            for order in orders:
                if len(book.open) < max_open:
                    book.enter(d, order["kind"], order["expiry"], order["legs"], order["n"], order["meta"], order.get("exit_after"))
        else:
            pending_entries = orders
    for p in list(book.open):
        book.close(days[-1], p, "end_of_data")
    eq = pd.DataFrame(rows).set_index("date")
    eq["ret"] = eq["equity"].pct_change().fillna(eq["equity"].iloc[0] / START_EQUITY - 1)
    return book, eq


# ---------------------------------------------------------------- shared helpers
def session_index():
    s = sessions()
    return s, {d: i for i, d in enumerate(s)}


def sessions_between(idx, a: date, b: date) -> int:
    return idx[b] - idx[a] if a in idx and b in idx else 10_000


def liquid(q, max_spread=0.10, min_oi=500) -> bool:
    if q is None or q[0] <= 0 or q[1] <= 0:
        return False
    mid = (q[0] + q[1]) / 2
    return (q[1] - q[0]) / mid <= max_spread and q[4] >= min_oi


# ---------------------------------------------------------------- Strategy E
def e_candidates(chain: Chain, spot: pd.Series, lr: pd.Series, days, rng_seed=7) -> dict:
    """For every decision day: MC-evaluated debit verticals (stats at d's quotes). Computed once, reused by the grid."""
    allsess, idx = session_index()
    out = {}
    rng = np.random.default_rng(rng_seed)
    for d in days:
        exps = [e for e in chain.exp_by_day.get(d, []) if 5 <= sessions_between(idx, d, e) <= 10]
        if not exps:
            continue
        e = exps[0]
        H = sessions_between(idx, d, e)
        past = lr[lr.index <= d].to_numpy()[-500:]
        if len(past) < 500:
            continue
        vol = montecarlo.ewma_vol(past)
        S = spot[d]
        term = montecarlo.bootstrap_terminal(past, S, H, 10_000, 5, vol, rng)
        cands = []
        for right, sign in (("C", 1), ("P", -1)):
            kl = chain.nearest(d, e, S)
            for w in (0.01, 0.02):
                ks = chain.nearest(d, e, S * (1 + sign * w))
                if kl is None or ks is None or (ks - kl) * sign <= 0:
                    continue
                ql, qs = chain.get(d, e, kl, right), chain.get(d, e, ks, right)
                if not (liquid(ql) and liquid(qs)):
                    continue
                debit = ql[1] - qs[0]
                width = abs(ks - kl)
                if not 0 < debit < width:
                    continue
                payoff = np.clip((term - kl) * sign, 0, width)     # per-share vertical value at expiry
                fees_ps = 4 * (0.65 + 0.30 + 0.015) / 100 + 0.00279 * 2 / 100
                net = payoff - debit - fees_ps
                cands.append({"expiry": e, "right": right, "k_long": kl, "k_short": ks, "debit": debit, "width": width,
                              "edge": float(net.mean() / (debit + fees_ps)), "p_loss": float(np.mean(net < 0)),
                              "p_itm_full": float(np.mean(payoff >= width)), "H": H})
        if cands:
            out[d] = cands
    return out


def run_e(chain, spot, days, cands, min_edge, max_p_loss, spread_mult=1.0, same_snapshot=False, risk=0.005):
    def decide(d, book):
        if d not in cands or len(book.open) >= 3:
            return []
        ok = [c for c in cands[d] if c["edge"] >= min_edge and c["p_loss"] <= max_p_loss]
        if not ok:
            return []
        c = max(ok, key=lambda c: c["edge"])
        per = c["debit"] * 100 + 4 * 2.3
        n = int(risk * book.mark(d) // per)
        if n < 1:
            return []
        legs = [(c["k_long"], c["right"], 1), (c["k_short"], c["right"], -1)]
        return [{"kind": "E_vertical", "expiry": c["expiry"], "legs": legs, "n": n,
                 "meta": {"edge": round(c["edge"], 4), "p_loss_pred": round(c["p_loss"], 4), "width": c["width"],
                          "planned_debit": c["debit"]}}]

    def tp(d, book, p):
        (kl, r, _), (ks, _, _) = p.legs
        ql, qs = book.chain.get(d, p.expiry, kl, r), book.chain.get(d, p.expiry, ks, r)
        if ql is None or qs is None:
            return False
        debit = -p.entry_cash / (100 * p.n)
        return (ql[0] - qs[1]) >= debit + 0.5 * (p.meta["width"] - debit)

    return run_book(days, chain, spot, decide, tp, spread_mult, same_snapshot, max_open=3)


# ---------------------------------------------------------------- Strategy F
def f_signals(chain: Chain, spot: pd.Series, har7: pd.Series, days) -> dict:
    allsess, idx = session_index()
    out = {}
    for d in days:
        f = har7.get(d)
        if f is None or not math.isfinite(f):
            continue
        exps = [e for e in chain.exp_by_day.get(d, []) if 7 <= (e - d).days <= 14]
        if not exps:
            continue
        e = min(exps, key=lambda e: abs((e - d).days - 10))
        S = spot[d]
        k0 = chain.nearest(d, e, S)
        qc, qp = chain.get(d, e, k0, "C"), chain.get(d, e, k0, "P")
        if not (liquid(qc) and liquid(qp)):
            continue
        iv = (qc[5] + qp[5]) / 2
        T = (e - d).days / 365
        em = S * iv * math.sqrt(T)
        kc, kp = chain.nearest(d, e, k0 + em), chain.nearest(d, e, k0 - em)
        if kc is None or kp is None or kc <= k0 or kp >= k0:
            continue
        prev = [x for x in allsess if x < e]
        out[d] = {"expiry": e, "k0": k0, "kc": kc, "kp": kp, "iv": float(iv), "forecast": float(f),
                  "spread": 100 * (float(iv) - float(f)), "exit_after": prev[-1] if prev else None}
    return out


def run_f(chain, spot, days, sig, leg: str, rich=None, cheap=None, naive=False, spread_mult=1.0, same_snapshot=False,
          risk=0.02):
    def decide(d, book):
        s = sig.get(d)
        if s is None or book.open:
            return []
        if leg == "short" and (naive or (rich is not None and s["spread"] >= rich)):
            legs = [(s["k0"], "C", -1), (s["k0"], "P", -1), (s["kc"], "C", 1), (s["kp"], "P", 1)]
            wing = max(s["kc"] - s["k0"], s["k0"] - s["kp"])
            q = [book.chain.get(d, s["expiry"], k, r) for k, r, _ in legs]
            credit = q[0][0] + q[1][0] - q[2][1] - q[3][1]
            per = (wing - credit) * 100 + 8 * 1.0
            if credit <= 0 or per <= 0:
                return []
            kind, meta = "F_short_iron_fly", {"credit_planned": credit, "wing": wing}
        elif leg == "long" and (naive or (cheap is not None and s["spread"] <= -cheap)):
            legs = [(s["k0"], "C", 1), (s["k0"], "P", 1)]
            q = [book.chain.get(d, s["expiry"], k, r) for k, r, _ in legs]
            per = (q[0][1] + q[1][1]) * 100 + 4 * 1.0
            kind, meta = "F_long_straddle", {}
        else:
            return []
        n = int(risk * book.mark(d) // per)
        if n < 1:
            return []
        meta.update({"iv": round(s["iv"], 4), "forecast": round(s["forecast"], 4), "spread_volpts": round(s["spread"], 2)})
        return [{"kind": kind, "expiry": s["expiry"], "legs": legs, "n": n, "meta": meta, "exit_after": s["exit_after"]}]

    def tp(d, book, p):
        vals = [book.chain.get(d, p.expiry, k, r) for k, r, _ in p.legs]
        if any(v is None for v in vals):
            return False
        if p.kind == "F_short_iron_fly":
            cost = sum((v[1] if sg < 0 else -v[0]) for v, (k, r, sg) in zip(vals, p.legs))
            credit = p.entry_cash / (100 * p.n)
            return cost <= 0.5 * credit
        value = sum(v[0] for v in vals)
        return value >= 1.5 * (-p.entry_cash / (100 * p.n))

    return run_book(days, chain, spot, decide, tp, spread_mult, same_snapshot, max_open=1)


# ---------------------------------------------------------------- Strategy D (user rule 2026-11-19: no sentiment)
def run_d(chain, spot, days, mode: str, right: str = "C", contracts: int = 1):
    """D0-simplified: buy one option position, sell with a resting limit at entry ask + $0.01, no stop loss.
    Assumptions (user left them open): one position at a time; contract chosen at close d, bought at d+1's ASK;
    expiry = nearest listed expiry >= 2 sessions after d; mode 'atm' = strike nearest spot, mode 'cheap' = the
    highest-priced contract of that expiry with 0 < ask <= $0.10. A close-quote bid >= target means the resting limit
    filled at the target (EOD lower bound on hits: intraday touches are invisible). Unhit positions are held to expiry:
    ITM -> sold at the expiry-day bid (intrinsic if unquoted), OTM -> worthless."""
    allsess, idx = session_index()
    cash, pos, pending = START_EQUITY, None, None
    trades, rows = [], []
    for i, d in enumerate(days):
        if pending is not None and pos is None:
            e, k = pending
            q = chain.get(d, e, k, right)
            if q is not None and q[1] > 0 and d < e:
                fee = option_order_fee(contracts, q[1], False)
                cash -= q[1] * 100 * contracts + fee
                pos = {"e": e, "k": k, "px": q[1], "n": contracts, "entry": d, "fee_in": fee}
            pending = None
        if pos is not None and d > pos["entry"]:
            q = chain.get(d, pos["e"], pos["k"], right)
            target = round(pos["px"] + 0.01, 2)
            exit_px, reason = None, None
            if q is not None and q[0] >= target:
                exit_px, reason = target, "take_profit_1c"
            elif d >= pos["e"]:
                s = spot[d]
                intr = max(s - pos["k"], 0.0) if right == "C" else max(pos["k"] - s, 0.0)
                exit_px, reason = ((q[0] if q is not None and q[0] > 0 else intr), "expiry_itm") if intr > 0 else (0.0, "expired_worthless")
            if reason:
                fee_out = option_order_fee(pos["n"], exit_px, True) if exit_px > 0 else 0.0
                cash += exit_px * 100 * pos["n"] - fee_out
                pnl = (exit_px - pos["px"]) * 100 * pos["n"] - pos["fee_in"] - fee_out
                best = (0.01 * 100 * pos["n"]) - pos["fee_in"] - option_order_fee(pos["n"], target, True)
                trades.append({"label": "SIMULATED on historical EOD quotes", "strategy": "D", "kind": f"D_{mode}_{right}",
                               "entry_date": pos["entry"].isoformat(), "exit_date": d.isoformat(), "expiry": pos["e"].isoformat(),
                               "strike": pos["k"], "contracts": pos["n"], "entry_ask": pos["px"], "exit_price": exit_px,
                               "net_pnl": round(pnl, 2), "pnl_if_target_always_hit": round(best, 2), "exit_reason": reason})
                pos = None
        val = 0.0
        if pos is not None:
            q = chain.get(d, pos["e"], pos["k"], right)
            val = ((q[0] + q[1]) / 2 if q is not None else 0.0) * 100 * pos["n"]
        rows.append({"date": d, "equity": cash + val})
        if pos is None and pending is None and i + 1 < len(days):
            exps = [e for e in chain.exp_by_day.get(d, []) if sessions_between(idx, d, e) >= 2]
            if exps:
                e = exps[0]
                if mode == "atm":
                    k = chain.nearest(d, e, spot[d])
                else:
                    ks = [(chain.get(d, e, k, right) or (0, 0))[1] for k in chain.strikes[(d, e)]]
                    cheap = [(a, k) for a, k in zip(ks, chain.strikes[(d, e)]) if 0 < a <= 0.10]
                    k = max(cheap)[1] if cheap else None
                if k is not None:
                    pending = (e, float(k))
    eq = pd.DataFrame(rows).set_index("date")
    eq["ret"] = eq["equity"].pct_change().fillna(eq["equity"].iloc[0] / START_EQUITY - 1)

    class _B:  # minimal book facade for summarize()
        pass
    b = _B()
    b.trades, b.stats = trades, {"partial": 0, "skipped_no_quote": 0, "intrinsic_exit_legs": 0}
    return b, eq


# ---------------------------------------------------------------- metrics + driver
def seg_days(chain_days, seg):
    lo, hi = SEG[seg]
    return [d for d in chain_days if lo <= d <= hi]


def summarize(name, book, eq, seg) -> dict:
    tr = pd.DataFrame(book.trades)
    ret = eq["ret"].to_numpy()
    dd_abs, dd_pct = drawdown(eq["equity"].to_numpy(), START_EQUITY)
    boot = block_bootstrap(ret)
    sd = ret.std(ddof=1)
    pnl = tr["net_pnl"].to_numpy() if len(tr) else np.array([])
    return {"variant": name, "segment": seg, "start": str(eq.index[0]), "end": str(eq.index[-1]), "sessions": len(eq),
            "trades": len(tr), "net_pnl": round(float(eq["equity"].iloc[-1] - START_EQUITY), 2),
            "net_return_pct": round(100 * (eq["equity"].iloc[-1] / START_EQUITY - 1), 3),
            "sharpe": round(float(ret.mean() / sd * math.sqrt(252)), 3) if sd > 0 and len(ret) >= 60 else None,
            "sharpe_ci95": boot["sharpe_ci"], "p_sharpe_le_0": boot["p_sharpe_le_0"],
            "max_drawdown_pct": round(100 * dd_pct, 3), "win_rate_pct": round(100 * float(np.mean(pnl > 0)), 1) if len(pnl) else None,
            "avg_pnl_per_trade": round(float(pnl.mean()), 2) if len(pnl) else None,
            "worst_trade": round(float(pnl.min()), 2) if len(pnl) else None,
            "avg_r": round(float(tr["r_multiple"].mean()), 4) if len(tr) and "r_multiple" in tr else None,
            "take_profit_hit_pct": round(100 * float((tr["exit_reason"] == "take_profit_1c").mean()), 1) if len(tr) and "pnl_if_target_always_hit" in tr else None,
            "upper_bound_pnl_if_every_trade_hit_target": round(float(tr["pnl_if_target_always_hit"].sum()), 2) if len(tr) and "pnl_if_target_always_hit" in tr else None,
            "partial_fills": book.stats["partial"], "skipped_no_quote": book.stats["skipped_no_quote"],
            "intrinsic_exit_legs": book.stats["intrinsic_exit_legs"]}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    print("building near-term chain table...", flush=True)
    chain = Chain(build_near())
    spot = daily_on_calendar("US.SPY", 0)["close"]
    adj = daily_on_calendar("US.SPY", 1)["close"]
    lr = np.log(adj / adj.shift(1)).dropna()
    chain_days = sorted(chain.exp_by_day)
    rows, trades, curves = [], [], {}

    # ---- E: candidates once, thresholds on dev only
    print("E: Monte Carlo candidates...", flush=True)
    cands = e_candidates(chain, spot, lr, chain_days)
    grid = []
    for me in (0.0, 0.05, 0.10, 0.20, 0.30):
        for pl in (0.55, 0.65, 0.75):
            b, eq = run_e(chain, spot, seg_days(chain_days, "dev"), cands, me, pl)
            m = summarize(f"E-grid-{me}-{pl}", b, eq, "dev")
            grid.append({"min_edge": me, "max_p_loss": pl, **{k: m[k] for k in ("trades", "net_return_pct", "sharpe")}})
    elig = [g for g in grid if g["trades"] >= 30 and g["sharpe"] is not None and g["sharpe"] > 0]
    sel = max(elig, key=lambda g: g["sharpe"]) if elig else {"min_edge": 0.10, "max_p_loss": 0.60}
    e_freeze = {"selected": {"min_edge": sel["min_edge"], "max_p_loss": sel["max_p_loss"]},
                "development_pass": bool(elig), "grid_dev": grid}
    print("E frozen:", e_freeze["selected"], "dev pass", e_freeze["development_pass"], flush=True)
    # ---- F: HAR(7) forecasts, signals once, thresholds on dev only
    print("F: forecasts and signals...", flush=True)
    from dual_strategy.research import run as abc
    eng = abc.Engine()
    daily = daily_on_calendar("US.SPY", 1).reindex(eng.spy.days)
    rv = strat_f.daily_realized_variance(eng.spy.b1, daily["open"].to_numpy(), daily["close"].to_numpy())
    f7 = strat_f.har_forecasts(rv, strat_f.FConfig(horizon=7))
    # f7[t] is made at the close of t-1; a decision at the close of d uses the forecast indexed at the next session
    har7 = pd.Series(f7, index=eng.spy.days).shift(-1)
    sig = f_signals(chain, spot, har7, chain_days)
    fgrid = {"short": [], "long": []}
    for th in (0.0, 2.0, 4.0, 6.0):
        for leg in ("short", "long"):
            kw = {"rich": th} if leg == "short" else {"cheap": th}
            b, eq = run_f(chain, spot, seg_days(chain_days, "dev"), sig, leg, **kw)
            m = summarize("x", b, eq, "dev")
            fgrid[leg].append({"threshold": th, **{k: m[k] for k in ("trades", "net_return_pct", "sharpe")}})
    f_sel = {}
    for leg in ("short", "long"):
        el = [g for g in fgrid[leg] if g["trades"] >= 10 and g["sharpe"] is not None and g["sharpe"] > 0]
        f_sel[leg] = {"threshold": max(el, key=lambda g: g["sharpe"])["threshold"] if el else None, "development_pass": bool(el)}
    print("F frozen:", f_sel, flush=True)
    freeze = {"E": e_freeze, "F": {"grid_dev": fgrid, "selected": f_sel}}
    (OUT / "eod_freeze.json").write_text(json.dumps(freeze, indent=1, default=str), encoding="utf-8")

    # ---- run every variant on dev / val / oos with frozen parameters
    me, pl = e_freeze["selected"]["min_edge"], e_freeze["selected"]["max_p_loss"]
    rich = f_sel["short"]["threshold"] if f_sel["short"]["threshold"] is not None else 4.0
    cheap = f_sel["long"]["threshold"] if f_sel["long"]["threshold"] is not None else 4.0
    variants = {
        "E": lambda days, **kw: run_e(chain, spot, days, cands, me, pl, **kw),
        "E-always (no MC filter)": lambda days, **kw: run_e(chain, spot, days, cands, -1e9, 1.0, **kw),
        "F-short (iron fly when IV rich)": lambda days, **kw: run_f(chain, spot, days, sig, "short", rich=rich, **kw),
        "F-short naive (every day)": lambda days, **kw: run_f(chain, spot, days, sig, "short", naive=True, **kw),
        "F-long (straddle when IV cheap)": lambda days, **kw: run_f(chain, spot, days, sig, "long", cheap=cheap, **kw),
        "F-long naive (every day)": lambda days, **kw: run_f(chain, spot, days, sig, "long", naive=True, **kw),
        "D ATM call +1c TP, no stop": lambda days, **kw: run_d(chain, spot, days, "atm", "C", 1),
        "D ATM put +1c TP, no stop": lambda days, **kw: run_d(chain, spot, days, "atm", "P", 1),
        "D cheap call (<=$0.10, 20 lots) +1c TP, no stop": lambda days, **kw: run_d(chain, spot, days, "cheap", "C", 20),
    }
    stress = {"baseline": {}, "spread x2": {"spread_mult": 2.0}, "spread x0.5": {"spread_mult": 0.5},
              "same-snapshot fill (optimistic)": {"same_snapshot": True}}
    mc_rows = []
    for name, fn in variants.items():
        for seg in ("dev", "val", "oos"):
            for sname, kw in stress.items():
                if sname != "baseline" and (seg != "oos" or name.startswith("D ")):
                    continue
                b, eq = fn(seg_days(chain_days, seg), **kw)
                m = summarize(name, b, eq, seg)
                m["stress"] = sname
                rows.append(m)
                if sname == "baseline":
                    trades += [{**t, "variant": name, "segment": seg} for t in b.trades]
                    if seg == "oos":
                        curves[name] = eq["equity"]
                        for blk in (1, 4, 8):
                            risk = montecarlo.path_risk(montecarlo.block_bootstrap_paths(eq["ret"].to_numpy(), 8, 10_000, blk))
                            mc_rows.append({"variant": name, "sample": "oos_2024_2025-12-12", "block_days": blk,
                                            **{k: round(v, 4) for k, v in risk.items()}})
        print("ran", name, flush=True)
    # ---- calibration of E's predicted loss probability on executed OOS trades
    tdf = pd.DataFrame(trades)
    e_oos = tdf[(tdf["variant"] == "E") & (tdf["segment"] == "oos")]
    calib = {}
    if len(e_oos):
        y = (e_oos["net_pnl"] < 0).astype(float).to_numpy()
        p = e_oos["meta_p_loss_pred"].astype(float).to_numpy()
        calib = {"trades": len(e_oos), "mean_predicted_p_loss": round(float(p.mean()), 4), "realised_loss_rate": round(float(y.mean()), 4),
                 "brier": round(float(np.mean((p - y) ** 2)), 4), "brier_climatology": round(float(np.mean((y.mean() - y) ** 2)), 4)}
    pd.DataFrame(rows).to_csv(OUT / "eod_results.csv", index=False)
    tdf.to_csv(OUT / "eod_trades.csv", index=False)
    pd.DataFrame(mc_rows).to_csv(OUT / "eod_monte_carlo.csv", index=False)
    (OUT / "eod_calibration.json").write_text(json.dumps(calib, indent=1), encoding="utf-8")
    pd.DataFrame(curves).to_csv(OUT / "eod_equity_oos.csv")
    print(json.dumps(calib, indent=1))
    print(pd.DataFrame(rows)[["variant", "segment", "stress", "trades", "net_return_pct", "sharpe", "max_drawdown_pct",
                              "win_rate_pct", "avg_pnl_per_trade", "worst_trade"]].to_string(index=False))


if __name__ == "__main__":
    main()


def merge_into_six() -> None:
    """Replace the D/E/F 'N/A' rows of reports/backtest_A_to_F.csv and reports/monte_carlo_A_to_F.csv with the EOD
    results (labelled: EOD quotes, 2021-01-04..2025-12-12, different sample from A-C)."""
    rep = ROOT / "reports"
    res = pd.read_csv(OUT / "eod_results.csv")
    res = res[res["stress"] == "baseline"]
    bt = pd.read_csv(rep / "backtest_A_to_F.csv", comment="#")
    bt = bt[~bt["strategy"].isin(["D", "E", "F"])]
    add = res.assign(strategy=res["variant"].str[0], period=res["segment"],
                     data_note="historical EOD SPY option quotes (free mirror), fills at next-day bid/ask; sample ends 2025-12-12")
    add = add.rename(columns={"max_drawdown_pct": "max_drawdown_pct"})
    out = pd.concat([bt, add[[c for c in add.columns if c in bt.columns or c in ("data_note", "take_profit_hit_pct",
                                                                                  "upper_bound_pnl_if_every_trade_hit_target")]]],
                    ignore_index=True)
    with open(rep / "backtest_A_to_F.csv", "w", newline="", encoding="utf-8") as fh:
        fh.write("# SIMULATED offline backtest. A-C: Moomoo bars 2021-01-04..2026-11-17 (backtest_v3). D/E/F: historical EOD "
                 "SPY option quotes 2021-01-04..2025-12-12 (reports/options_eod/), a different sample - do not rank raw returns across them.\n")
        out.to_csv(fh, index=False)
    mc = pd.read_csv(rep / "monte_carlo_A_to_F.csv", comment="#")
    mc = mc[~mc["strategy"].isin(["D", "E", "F"])]
    emc = pd.read_csv(OUT / "eod_monte_carlo.csv")
    emc.insert(0, "strategy", emc["variant"].str[0])
    emc["horizon_sessions"], emc["paths"] = 8, 10_000
    with open(rep / "monte_carlo_A_to_F.csv", "w", newline="", encoding="utf-8") as fh:
        fh.write("# Day-block bootstrap of SIMULATED daily net returns, 10000 paths x 8 sessions. A-C OOS 2024-01-02..2026-11-17; "
                 "D/E/F OOS 2024-01-02..2025-12-12 on historical EOD option quotes.\n")
        pd.concat([mc, emc], ignore_index=True).to_csv(fh, index=False)
