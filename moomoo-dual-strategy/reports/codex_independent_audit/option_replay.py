"""Raw-quote E/F candidate construction, event replay, and independent model diagnostics."""

from collections import defaultdict
from dataclasses import dataclass, replace
from functools import cached_property
import math

import numpy as np
import pandas as pd

from audit_core import OUT, ROOT, intrinsic, invert_iv, option_fee, prob_above
from stock_replay import DEV, VAL, actions, bars, daily

WINDOWS = {"dev": DEV, "val": VAL, "oos": ("2024-01-02", "2025-12-12")}
TERMS = {"boot": (0, .65), "drift": (.30, .55), "hv": (.10, .60)}
START = 50_000.
EST_FEE = (4 * .965 + 2 * .00279) / 100


def segment(d):
    return "dev" if d < WINDOWS["val"][0] else "val" if d < WINDOWS["oos"][0] else "oos"


class Quotes:
    def __init__(self):
        raw = pd.read_parquet(ROOT / "data-cache/options_history/spy_near_2021_2025.parquet")
        self.snapshots = defaultdict(dict)
        self.chain = defaultdict(set)
        for d, e, k, kind, bid, ask, bsz, asz, oi, iv in raw[
            ["date", "expiration", "strike", "right", "bid", "ask", "bid_size", "ask_size", "open_interest",
             "implied_volatility"]
        ].itertuples(index=False, name=None):
            d, e = str(d)[:10], str(e)[:10]
            self.snapshots[d, e][k, kind] = (bid, ask, bsz, asz, oi, iv)
            self.chain[d].add(e)
        self.strikes = {key: np.array(sorted({k for k, _ in snapshot})) for key, snapshot in self.snapshots.items()}
        self.chain = {d: sorted(es) for d, es in self.chain.items()}

    def nearest(self, d, e, x):
        values = self.strikes[d, e]
        return float(values[np.argmin(abs(values - x))])

    def get(self, d, e, k, right):
        return self.snapshots.get((d, e), {}).get((k, right))

    def mids(self, d, e, strike, cp):
        ks = self.strikes[d, e]
        j = int(np.searchsorted(ks, strike))
        if j == 0 or strike >= ks[-1]:
            return np.nan
        right = "C" if cp == 1 else "P"
        a, b = (self.get(d, e, k, right) for k in ks[j - 1:j + 1])
        if not all(q and q[0] > 0 for q in (a, b)):
            return np.nan
        return prob_above(mid(a), mid(b), ks[j] - ks[j - 1], cp)


def mid(q):
    return (q[0] + q[1]) / 2


def cost_ratio(q):
    return (q[1] - q[0]) / mid(q)


def liquid(q, strict=False):
    return bool(q and q[0] > 0 and q[1] > 0 and cost_ratio(q) <= .20 and q[4] >= 500
                and (not strict or (q[1] >= q[0] and min(q[2:4]) > 0)))


def terminal_models(past, s, horizon, rng):
    weights = .94 ** np.arange(len(past) - 1, -1, -1)
    vol = np.sqrt(np.average(past ** 2, weights=weights))
    offset = rng.integers(0, len(past), (10_000, math.ceil(horizon / 5)))
    ix = ((offset[..., None] + np.arange(5)) % len(past)).reshape(10_000, -1)[:, :horizon]
    adjusted = (past - past.mean()) * (vol / past.std(ddof=1))
    boot = s * np.exp(adjusted[ix].sum(axis=1))
    sd = past[-20:].std(ddof=1)
    hv = s * np.exp(-.5 * sd ** 2 * horizon + sd * np.sqrt(horizon) * rng.standard_normal(10_000))
    return {"boot": boot, "drift": boot * np.exp(past.mean() * horizon), "hv": hv}


def e_candidates(q, spot, returns, index):
    rng = np.random.default_rng(7)
    choices, calibration = {}, []
    for d in sorted(q.chain):
        expiries = [e for e in q.chain[d] if 5 <= index.get(e, -999) - index[d] <= 10]
        past = returns.loc[:d].tail(500).to_numpy()
        if not expiries or len(past) < 500:
            continue
        e = expiries[0]
        s, st = spot[d], spot.get(e, np.nan)
        h = index[e] - index[d]
        paths = terminal_models(past, s, h, rng)
        pool = []
        for cp, right in ((1, "C"), (-1, "P")):
            kl = q.nearest(d, e, s)
            for width in (.01, .02):
                ks = q.nearest(d, e, s * (1 + cp * width))
                a, b = q.get(d, e, kl, right), q.get(d, e, ks, right)
                if (ks - kl) * cp <= 0 or not all(liquid(x) for x in (a, b)):
                    continue
                debit, strike_width = a[1] - b[0], abs(kl - ks)
                if not 0 < debit < strike_width:
                    continue
                score, loss = {}, {}
                for name, sims in paths.items():
                    pnl = np.clip(cp * (sims - kl), 0, strike_width) - debit - EST_FEE
                    score[name], loss[name] = float(pnl.mean() / (debit + EST_FEE)), float((pnl < 0).mean())
                p = q.mids(d, e, kl + cp * (debit + EST_FEE), cp)
                loss["rn"] = (1 - p) if cp == 1 else p
                realised = int(np.clip(cp * (st - kl), 0, strike_width) < debit + EST_FEE) if np.isfinite(st) else np.nan
                calibration.append({"date": d, "expiry": e, "segment": segment(d), "right": right,
                                    "realised": realised, **loss, "strike_long": kl, "strike_short": ks})
                pool.append({"expiry": e, "legs": ((kl, right, 1), (ks, right, -1)), "debit": debit,
                             "score": score, "loss": loss, "width": strike_width, "quotes": (a, b)})
        choices[d] = pool
    return choices, pd.DataFrame(calibration)


def f_forecasts():
    data = bars()
    adj = daily(adjusted=True)
    overnight = np.log(adj.open / adj.close.shift(1))
    realised = {}
    for d, a in data.items():
        vector = np.r_[a[0, 0], a[4::5, 3]]
        realised[d] = float(np.square(np.diff(np.log(vector))).sum() + np.square(overnight[d])) if np.isfinite(a[:, 3]).all() else np.nan
    rv = pd.Series(realised).reindex(adj.loc["2020-11-02":].index)
    x = np.column_stack([np.ones(len(rv)), rv.shift(1), rv.rolling(5, min_periods=4).mean().shift(1),
                         rv.rolling(22, min_periods=20).mean().shift(1)])
    y = rv.rolling(7, min_periods=5).mean().shift(-6).to_numpy()
    har = np.full(len(rv), np.nan)
    for i in range(256, len(rv)):
        rows = np.arange(max(0, i - 756), i - 6)
        subset = rows[np.isfinite(y[rows]) & np.isfinite(x[rows]).all(axis=1)]
        if len(subset) >= 250 and np.isfinite(x[i]).all():
            beta = np.linalg.lstsq(x[subset], y[subset], rcond=None)[0]
            har[i - 1] = np.sqrt(252 * max(x[i] @ beta, 1e-8))
    return pd.DataFrame({"har": har, "hv": np.sqrt(252 * rv.rolling(20, min_periods=18).mean())}, index=rv.index)


def f_candidates(q, spot, index, dates):
    forecasts = f_forecasts()
    result, iv_rows = {}, []
    for d in sorted(q.chain):
        dtes = {e: (pd.Timestamp(e) - pd.Timestamp(d)).days for e in q.chain[d]}
        expiries = [e for e, n in dtes.items() if 7 <= n <= 14 and e in index]
        if not expiries:
            continue
        e = min(expiries, key=lambda e: abs(dtes[e] - 10))
        s = spot[d]
        k = q.nearest(d, e, s)
        call, put = q.get(d, e, k, "C"), q.get(d, e, k, "P")
        if not all(liquid(v) for v in (call, put)):
            continue
        iv = (call[5] + put[5]) / 2
        years = dtes[e] / 365
        iv_alt = [invert_iv(mid(v), s, k, years, .04, .013, cp) for v, cp in ((call, 1), (put, -1))]
        iv_rows.append({"date": d, "expiry": e, "strike": k, "vendor_call_iv": call[5], "vendor_put_iv": put[5],
                        "european_call_iv_r4_q1p3": iv_alt[0], "european_put_iv_r4_q1p3": iv_alt[1], **forecasts.loc[d]})
        move = s * iv * np.sqrt(years)
        put_short, call_short = q.nearest(d, e, s - move), q.nearest(d, e, s + move)
        w = max(5, round(.5 * move))
        pk, ck = q.nearest(d, e, put_short - w), q.nearest(d, e, call_short + w)
        condor = ((call_short, "C", -1), (put_short, "P", -1), (ck, "C", 1), (pk, "P", 1))
        c_quotes = tuple(q.get(d, e, strike, r) for strike, r, _ in condor)
        credit = None
        if pk < put_short < k < call_short < ck and all(liquid(v) for v in c_quotes):
            credit = (condor, c_quotes, max(ck - call_short, put_short - pk))
        result[d] = {"expiry": e, "iv": iv, "iv_alt": np.mean(iv_alt), "exit_after": dates[index[e] - 1],
                     "forecasts": forecasts.loc[d], "long": (((k, "C", 1), (k, "P", 1)), (call, put), 0), "credit": credit}
    pd.DataFrame(iv_rows).to_csv(OUT / "option_implied_vol_check.csv", index=False)
    return result


@dataclass(frozen=True)
class Spec:
    name: str
    kind: str
    model: str
    threshold: float
    p_loss: float = 1.
    naive: bool = False
    ticks: int = 0
    no_fees: bool = False
    strict: bool = False
    alt_iv: bool = False

    @cached_property
    def max_open(self):
        return 1 if self.kind == "long" else 3

    @cached_property
    def risk(self):
        return {"E": .005, "long": .02, "credit": .01}[self.kind]


def choose(d, spec, candidates, positions, equity):
    def tradable(quotes):
        return all(cost_ratio(v) <= .10 and liquid(v, spec.strict) for v in quotes)

    if spec.kind == "E":
        pool = [v for v in candidates.get(d, ()) if tradable(v["quotes"])]
        selected = [v for v in pool if spec.naive or
                    (v["score"][spec.model] >= spec.threshold and v["loss"][spec.model] <= spec.p_loss)]
        if not selected:
            return None
        c = max(selected, key=lambda v: -v["debit"] / v["width"] if spec.naive else v["score"][spec.model])
        e, width, legs, exit_after = c["expiry"], c["width"], c["legs"], None
        planned_loss = c["debit"] * 100 + 9.2
    else:
        c = candidates.get(d)
        if not c or not c[spec.kind]:
            return None
        fc, iv = c["forecasts"][spec.model], c["iv_alt"] if spec.alt_iv else c["iv"]
        if not np.isfinite(fc) or not np.isfinite(iv):
            return None
        e, exit_after = c["expiry"], c["exit_after"]
        legs, quotes, width = c[spec.kind]
        if not tradable(quotes):
            return None
        if spec.kind == "long":
            if not spec.naive and (iv - fc) * 100 > -spec.threshold:
                return None
            planned_loss = 100 * (quotes[0][1] + quotes[1][1]) + 5
        else:
            if (not spec.naive and iv / fc < spec.threshold) or any(p["expiry"] == e for p in positions):
                return None
            credit = quotes[0][0] + quotes[1][0] - quotes[2][1] - quotes[3][1]
            planned_loss = (width - credit) * 100 + 10
            if credit <= 0 or planned_loss <= 0:
                return None
    budget = spec.risk * equity
    n = int(budget // planned_loss)
    if n < 1:
        return None
    return {"signal_date": d, "expiry": e, "legs": legs, "width": width, "exit_after": exit_after,
            "contracts": n, "budget": budget}


def order_cash(prices, sides, n, no_fees):
    fees = sum(float(option_fee(n, p, side < 0)) for p, side in zip(prices, sides)) if not no_fees else 0.
    return -100 * n * np.dot(prices, sides) - fees, fees


def execute(q, d, spec, order):
    e, legs = order["expiry"], order["legs"]
    quotes = [q.get(d, e, k, r) for k, r, _ in legs]
    sides = [s for _, _, s in legs]
    if any(v is None or v[1] <= 0 or (s < 0 and v[0] <= 0) for v, s in zip(quotes, sides)):
        return None
    size = min(v[3 if s > 0 else 2] for v, s in zip(quotes, sides))
    if spec.strict and (size <= 0 or any(v[1] < v[0] for v in quotes)):
        return None
    n = min(order["contracts"], int(size)) if size else order["contracts"]
    prices = [max(v[1] + .01 * spec.ticks, 0) if s > 0 else max(v[0] - .01 * spec.ticks, 0)
              for v, s in zip(quotes, sides)]
    while n > 0:
        cash, fees = order_cash(prices, sides, n, spec.no_fees)
        maxloss = order["width"] * 100 * n - cash if spec.kind == "credit" else -cash
        if not spec.strict or (maxloss <= order["budget"] and maxloss > 0):
            return {**order, "entry_date": d, "entry_cash": cash, "entry_fee": fees, "entry_quotes": quotes,
                    "entry_prices": prices, "contracts": n, "max_loss": maxloss,
                    "crossed_entry": any(v[1] < v[0] for v in quotes), "zero_executable_size_entry": size == 0}
        n -= 1
    return None


def close_cash(q, spot, d, spec, p, settlement=False):
    legs, n, e = p["legs"], p["contracts"], p["expiry"]
    if settlement and spec.strict:
        return intrinsic(spot[d], legs) * n * 100, 0., 0, False, False
    prices, sides, intrinsic_count, crossed, zero_size = [], [], 0, False, False
    for strike, r, side in legs:
        value = intrinsic(spot[d], [(strike, r, 1)])
        if settlement and value == 0:
            continue
        qt = q.get(d, e, strike, r)
        if qt is None or (qt[0] <= 0 and qt[1] <= 0):
            if spec.strict:
                return None
            px, intrinsic_count = value, intrinsic_count + 1
        else:
            px = max(0., qt[side < 0] - side * .01 * spec.ticks)
            crossed |= qt[1] < qt[0]
            zero_size |= qt[2 if side > 0 else 3] < n
            if spec.strict and (crossed or zero_size or px <= 0):
                return None
        prices.append(px)
        sides.append(-side)
    cash, fee = order_cash(prices, sides, n, spec.no_fees)
    return cash, fee, intrinsic_count, crossed, zero_size


def market_value(q, spot, d, p):
    values = []
    for k, r, s in p["legs"]:
        quote = q.get(d, p["expiry"], k, r)
        value = mid(quote) if quote and quote[1] > 0 else intrinsic(spot[d], [(k, r, 1)])
        values.append(value * s)
    return 100 * p["contracts"] * sum(values)


def target(q, d, spec, p):
    legs, e = p["legs"], p["expiry"]
    qs = [q.get(d, e, k, r) for k, r, _ in legs]
    if any(v is None for v in qs):
        return False
    entry_per = p["entry_cash"] / (100 * p["contracts"])
    if spec.kind == "E":
        return qs[0][0] - qs[1][1] >= -entry_per + .5 * (p["width"] + entry_per)
    if spec.kind == "long":
        return sum(v[0] for v in qs) >= -entry_per * 1.5
    return sum(v[1] if s < 0 else -v[0] for v, (_, _, s) in zip(qs, legs)) <= .5 * entry_per


def replay(q, spot, candidates, days, spec, seg):
    cash, positions, orders, ledger, curve, prev = START, [], None, [], [], START

    def close(d, p, reason):
        nonlocal cash
        out = close_cash(q, spot, d, spec, p, d >= p["expiry"])
        if out is None:
            return
        exit_cash, fee, fallback, crossed, zero_size = out
        pnl = p["entry_cash"] + exit_cash
        cash += exit_cash
        positions.remove(p)
        ledger.append({"strategy": spec.name[0], "variant": spec.name, "segment": seg, **p, "exit_date": d,
                       "exit_reason": reason, "exit_cash": exit_cash, "exit_fee": fee, "net_pnl": pnl,
                       "gross_pnl": pnl + fee + p["entry_fee"], "fees": fee + p["entry_fee"],
                       "exit_fallback_legs": fallback, "crossed_exit": crossed, "insufficient_exit_size": zero_size,
                       "r_multiple": pnl / p["max_loss"] if p["max_loss"] > 0 else np.nan})

    for i, d in enumerate(days):
        for p in list(positions):
            if p.pop("target", False):
                close(d, p, "take_profit")
        if orders and d < orders["expiry"] and len(positions) < spec.max_open:
            p = execute(q, d, spec, orders)
            if p:
                cash += p["entry_cash"]
                positions.append(p)
        for p in list(positions):
            if d >= p["expiry"]:
                close(d, p, "expiry")
            elif p["exit_after"] and d >= p["exit_after"]:
                close(d, p, "time_exit")
        marked = cash + sum(market_value(q, spot, d, p) for p in positions)
        curve.append({"variant": spec.name, "segment": seg, "date": d, "equity": marked, "return": marked / prev - 1,
                      "risk_fraction": sum(p["max_loss"] for p in positions) / marked, "positions": len(positions)})
        prev = marked
        for p in positions:
            p["target"] = target(q, d, spec, p)
        orders = choose(d, spec, candidates, positions, marked) if i + 1 < len(days) and len(positions) < spec.max_open else None
    for p in list(positions):
        close(days[-1], p, "end_of_data")
    curve[-1]["cash_after_final_liquidation"] = cash
    return ledger, curve


def assignment_flags(q, spot, trades):
    dividends = actions("US.SPY")["dividends"]
    dividends = (dividends.get("data") or dividends).get("dividend_list", [])
    divs = sorted((r["ex_date"].replace("/", "-"), r["dividend_per_share"])
                  for r in dividends if r.get("dividend_per_share"))
    flags = []
    for p in trades:
        if p["segment"] != "oos" or p["variant"] not in ("F_credit_har", "F_credit_hv", "F_credit_naive"):
            continue
        reasons = []
        e = p["expiry"]
        for d, s in spot.loc[p["entry_date"]:p["exit_date"]].items():
            for k, right, side in p["legs"]:
                if side > 0:
                    continue
                quote = q.get(d, e, k, right)
                this = intrinsic(s, [(k, right, 1)])
                if not quote or this <= 0:
                    continue
                next_div = next((v for ex, v in divs if d < ex <= e), 0.)
                extrinsic = mid(quote) - this
                if extrinsic < (next_div if right == "C" else .01) and (right == "P" or next_div > 0):
                    reasons.append(f"{d}/{right}/{k:g}/{extrinsic:.4f}")
        flags.append({key: p[key] for key in ("variant", "entry_date", "exit_date", "expiry", "legs")}
                     | {"flagged": bool(reasons), "evidence": "|".join(reasons)})
    pd.DataFrame(flags).to_csv(OUT / "option_assignment_flags.csv", index=False)


def main():
    q = Quotes()
    spot = daily().close
    returns = np.log(daily(adjusted=True).close).diff().dropna()
    dates = list(spot.index)
    index = {d: i for i, d in enumerate(dates)}
    eco, cal = e_candidates(q, spot, returns, index)
    fco = f_candidates(q, spot, index, dates)
    cal.to_csv(OUT / "e_independent_calibration_candidates.csv", index=False)
    outcomes = []
    for seg, group in cal.dropna(subset=["rn", "realised"]).groupby("segment"):
        for label, g in (("as_run", group), ("inside_split", group[group.expiry <= WINDOWS[seg][1]])):
            for model in ("boot", "drift", "hv", "rn"):
                p = g[model].clip(.0001, .9999)
                y = g.realised
                outcomes.append({"segment": seg, "outcome_window": label, "model": model, "count": len(g),
                                 "brier": ((p - y) ** 2).mean(),
                                 "logloss": -(y * np.log(p) + (1 - y) * np.log(1 - p)).mean()})
    pd.DataFrame(outcomes).to_csv(OUT / "e_independent_calibration.csv", index=False)
    versions = [Spec(f"E_{m}", "E", m, *levels) for m, levels in TERMS.items()]
    versions.extend([Spec("E_always_same_structure", "E", "boot", 0, naive=True),
                     Spec("F_long_har", "long", "har", 0), Spec("F_long_hv", "long", "hv", 0),
                     Spec("F_credit_har", "credit", "har", 1.2), Spec("F_credit_hv", "credit", "hv", 1.2),
                     Spec("F_long_naive", "long", "har", 0, naive=True),
                     Spec("F_credit_naive", "credit", "har", 0, naive=True)])
    extra = [replace(s, name=s.name + suffix, **kwargs)
             for s in versions if s.name in ("E_boot", "F_long_har", "F_credit_naive")
             for suffix, kwargs in (("_strict", {"strict": True}), ("_ticks2", {"ticks": 2}), ("_free", {"no_fees": True}))]
    extra.extend(Spec(f"F_credit_{m}_{t:g}", "credit", m, t) for m in ("har", "hv") for t in (.9, 1., 1.1, 1.2, 1.3))
    extra.extend(Spec(f"F_long_{m}_{t:g}", "long", m, t) for m in ("har", "hv") for t in (2., 4., 6.))
    extra.extend([Spec("F_long_har_euro_iv", "long", "har", 0, alt_iv=True),
                  Spec("F_credit_har_euro_iv", "credit", "har", 1.2, alt_iv=True)])
    trades, curves = [], []
    for spec in versions + extra:
        for seg, (lo, hi) in WINDOWS.items():
            if spec in extra and seg != "oos":
                continue
            days = [d for d in sorted(q.chain) if lo <= d <= hi]
            ledger, daily_curve = replay(q, spot, eco if spec.kind == "E" else fco, days, spec, seg)
            trades.extend(ledger)
            curves.extend(daily_curve)
        print(spec.name, flush=True)
    pd.DataFrame(trades).to_csv(OUT / "option_independent_trades.csv", index=False)
    pd.DataFrame(curves).to_csv(OUT / "option_independent_daily.csv", index=False)
    assignment_flags(q, spot, trades)


if __name__ == "__main__":
    main()
