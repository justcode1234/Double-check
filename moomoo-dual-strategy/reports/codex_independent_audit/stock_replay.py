"""Offline raw-bar event replay for B/C, and independent A price and feature checks."""

from dataclasses import dataclass, replace
from functools import lru_cache
import json

import exchange_calendars
import numpy as np
import pandas as pd

from audit_core import HIST, OUT, REPRO, ROOT, minute, stock_cost

COLS = ["open", "high", "low", "close", "volume", "turnover"]
CAL = exchange_calendars.get_calendar("XNYS", start="2019", end="2027")
OOS = ("2024-01-02", "2026-11-17")
DEV = ("2021-01-04", "2022-12-30")
VAL = ("2023-01-03", "2023-12-29")
RECENT = ("2025-01-02", "2026-11-17")


def daily(symbol="US.SPY", adjusted=False):
    raw = pd.read_csv(HIST / f"daily_a{int(adjusted)}/{symbol}.csv.gz", dtype={"date": str})
    raw.index = pd.to_datetime(raw.pop("date"), format="%Y%m%d").dt.strftime("%Y-%m-%d")
    return raw.reindex(CAL.sessions_in_range("2019-01-02", OOS[1]).strftime("%Y-%m-%d"))


def actions(symbol):
    return json.loads((HIST / f"actions/{symbol}.json").read_text(encoding="utf-8"))


def prior_features(symbol):
    raw = daily(symbol)
    action = actions(symbol)
    factor = np.ones(len(raw))
    splits = action["splits"].get("data") or action["splits"]
    for row in splits.get("split_list", []):
        if row.get("rate"):
            before, after = map(float, row["rate"].replace("->", "→").split("→"))
            ex = pd.Timestamp(row.get("ex_date_str") or row["ex_date"]).date().isoformat()
            if ex <= OOS[1]:
                factor[raw.index < ex] *= after / before
    adjusted_price = raw[["close", "high", "low"]].div(factor, axis=0)
    pc = adjusted_price.close.shift(1)
    tr = np.maximum.reduce([adjusted_price.high - adjusted_price.low,
                            (adjusted_price.high - pc).abs(), (adjusted_price.low - pc).abs()])
    atr = pd.Series(tr, index=raw.index).rolling(14).mean().shift(1) * factor
    adv = (raw.volume * factor).rolling(14).mean().shift(1) / factor
    return atr, adv, pd.Series(factor, index=raw.index)


@lru_cache(maxsize=48)
def bars(symbol="US.SPY", width=1):
    folder = HIST / ("k1" if width == 1 else "k6") / symbol
    sources = sorted(folder.glob("*.csv.gz"))
    if not sources:
        return {}
    table = pd.concat([pd.read_csv(path) for path in sources], ignore_index=True)
    ts = pd.to_datetime(table.pop("end_ms"), unit="ms", utc=True).dt.tz_convert("America/New_York")
    table["date"] = ts.dt.strftime("%Y-%m-%d")
    table["slot"] = (ts.dt.hour * 60 + ts.dt.minute - 570) // width - 1
    return {d: g.set_index("slot")[COLS].reindex(range(session_minutes(d) // width)).to_numpy(dtype=float)
            for d, g in table.groupby("date", sort=True)}


@lru_cache(maxsize=None)
def session_minutes(d):
    return int((CAL.session_close(d) - CAL.session_open(d)).total_seconds() // 60)


def spy_inputs():
    data = bars()
    raw = daily()
    atr, _, _ = prior_features("US.SPY")
    v = daily(adjusted=True).close.pct_change().rolling(14).std().shift(1)
    divs = actions("US.SPY")["dividends"]
    divs = (divs.get("data") or divs).get("dividend_list", [])
    div = {r["ex_date"].replace("/", "-"): r["dividend_per_share"] for r in divs if r.get("dividend_per_share")}
    history = {False: {}, True: {}}
    slots = np.arange(29, 360, 30)
    seen = [[] for _ in slots]
    for day, a in data.items():
        for mode in (False, True):
            history[mode][day] = {}
        for i, j in enumerate(slots):
            recent = np.array(seen[i][-14:])
            valid = [x for x in seen[i] if np.isfinite(x)][-14:]
            history[True][day][j] = recent.mean() if len(recent) == 14 else np.nan
            history[False][day][j] = np.mean(valid) if len(valid) == 14 else np.nan
            seen[i].append(abs(a[j, 3] / a[0, 0] - 1) if j < len(a) else np.nan)
    return data, atr, v, raw.close.shift(1) - pd.Series(div).reindex(raw.index, fill_value=0), history


@dataclass(frozen=True)
class Spec:
    name: str
    fraction: float | None = .40
    costs: str = "competition"
    bps: float = 1
    strict: bool = True
    flatten: int = 5
    lock: bool = True
    shorts: bool = True
    latency: int = 0
    vwap: str = "turnover"
    close_fill: bool = False
    capital: float = 50_000
    initial_cap: bool = False
    c_cutoff: int = 300
    c_stop_first: bool = True


def trade_exit(position, px, equity, spec, j, reason):
    side, n, entry, fill, fee, at, stop, signal = position
    close, exit_fee = stock_cost(n, px, side < 0, spec.costs, spec.bps)
    return {"entry_slot": at, "exit_slot": j, "side": "long" if side > 0 else "short", "qty": n,
            "entry_raw": entry, "exit_raw": px, "entry_fill": fill, "exit_fill": close,
            "net_pnl": side * n * (close - fill) - fee - exit_fee, "fees": fee + exit_fee,
            "slippage": n * (abs(fill - entry) + abs(close - px)), "gross_pnl": side * n * (px - entry),
            "stop": stop, "signal_slot": signal, "equity_sod": equity, "reason": reason}


def replay_session(a, spec, equity, bands, ref, vol, atr, c=False):
    n_bars = len(a)
    if c and not atr > 0:
        return [], 0., 0.
    o, h, l, close, volume, turnover = a.T
    vwap_num = turnover if spec.vwap == "turnover" else (h + l + close) / 3 * volume
    with np.errstate(invalid="ignore", divide="ignore"):
        vwap = np.nancumsum(vwap_num) / np.nancumsum(volume)
    if c:
        groups = a.reshape(-1, 5, 6)
        v5 = groups[:, :, 4].sum(axis=1)
        close5 = groups[:, -1, 3]
        numerator = (groups[:, :, 1].max(axis=1) + groups[:, :, 2].min(axis=1) + close5) / 3 * v5
        with np.errstate(invalid="ignore", divide="ignore"):
            vwap5 = np.nancumsum(numerator) / np.nancumsum(v5)
        vwap5[np.isnan(v5)] = np.nan
    positions, position, pending, setup = [], None, None, None
    ready = {1: True, -1: True}
    marked, locked, last_vwap, exposure = 0., False, np.nan, []
    flatten = n_bars - spec.flatten if spec.flatten else n_bars
    frac = min(4., .02 / vol) if spec.fraction is None and vol > 0 else (spec.fraction or 0.)

    def transact(target, raw, size_ref, j, signal, reason):
        nonlocal position, marked, ready
        if position and target != position[0]:
            result = trade_exit(position, raw, equity, spec, j, reason)
            positions.append(result)
            marked += result["net_pnl"]
            position = None
            ready = {1: False, -1: False}
        if target and position is None and not locked and j < flatten:
            base = spec.capital if spec.initial_cap else equity
            sized = min(.40 * base / size_ref, .0025 * equity / (.25 * atr), equity / size_ref) if c and frac < 1 else frac * base / size_ref
            cap = max(0, int(20 * volume[min(j, n_bars - 1)]))
            qty = min(int(sized), cap)
            if qty > 0:
                fill, fee = stock_cost(qty, raw, target > 0, spec.costs, spec.bps)
                position = (target, qty, raw, fill, fee, j, raw - target * .25 * atr if c else None, signal)

    for j, px in enumerate(close):
        if not np.isfinite(px):
            exposure.append(exposure[-1] if exposure else 0.)
            continue
        end = j + 1
        if pending and j >= pending[0]:
            _, target, refpx, signal, reason = pending
            pending = None
            transact(target, o[j], refpx or max(o[0], o[j]), j, signal, reason)
        if not c and j == flatten and position:
            transact(0, o[j], 0, j, j, "flatten")
            pending = None
        if c and end % 5 == 0:
            last_vwap = vwap5[end // 5 - 1]
        if position:
            sign, qty, _, fill, _, at, stop, _ = position
            target_hit = c and sign * (px - last_vwap) >= 0
            hit = c and (l[j] <= stop if sign == 1 else h[j] >= stop)
            if hit and (spec.c_stop_first or not target_hit):
                stop_raw = min(o[j], stop) if sign == 1 else max(o[j], stop)
                latency = .0002 if spec.costs != "paper" else 0.
                transact(0, stop_raw * (1 - sign * latency), 0, j, j, "stop")
                pending = None
        mtm = marked
        if position:
            sign, qty, _, fill, _, at, _, _ = position
            mtm += sign * qty * (px - fill) - stock_cost(qty, px, sign < 0, spec.costs, spec.bps)[1]
        exposure.append(position[1] * px / equity if position else 0.)
        if spec.lock and not locked and mtm <= -.0125 * equity:
            locked, setup = True, None
            if position and (not c or pending is None):
                pending = (end + spec.latency, 0, None, end, "loss_lock")
        if c:
            if position and pending is None:
                sign, _, _, _, _, at, _, _ = position
                reason = ("time" if end >= n_bars - 10 else "hold" if end - at >= 60 else
                          "vwap" if sign * (px - last_vwap) >= 0 else None)
                if reason:
                    pending = (end + spec.latency, 0, None, end, reason)
            if end % 5 or end < 30 or locked or not np.isfinite(last_vwap):
                continue
            distance = (close5[end // 5 - 1] - last_vwap) / atr
            for sign in (1, -1):
                if sign * distance > -.30:
                    ready[sign] = True
            if position or pending:
                setup = None
                continue
            if setup:
                sign, armed = setup
                if sign * distance > -.30:
                    if end <= min(spec.c_cutoff, n_bars - 90):
                        pending = (end + spec.latency, sign, px, end, "reentry")
                    setup = None
                elif end - armed >= 30:
                    ready[sign], setup = False, None
            elif distance <= -.30 and ready[1]:
                setup = (1, end)
            elif spec.shorts and distance >= .30 and ready[-1]:
                setup = (-1, end)
        elif not locked and j in bands and end < flatten:
            sigma = bands[j]
            if not sigma > 0 or not ref > 0:
                continue
            upper, lower = max(o[0], ref) * (1 + sigma), min(o[0], ref) * (1 - sigma)
            target = 1 if px > max(upper, vwap[j]) else -1 if spec.shorts and px < min(lower, vwap[j]) else 0
            if target != (position[0] if position else 0):
                if spec.close_fill:
                    transact(target, px, o[0], j, end, "decision_close")
                else:
                    pending = (end + spec.latency, target, None, end, "decision")
    if position:
        transact(0, close[-1], 0, n_bars, n_bars, "close")
    return positions, np.mean(exposure), max(exposure, default=0.)


def run(spec, inputs, lo, hi, c=False):
    data, atr, vol, ref, hist = inputs
    equity = spec.capital
    ledger, daily_rows = [], []
    for d in CAL.sessions_in_range(lo, hi).strftime("%Y-%m-%d"):
        start = equity
        if d in data:
            trades, exposure, peak = replay_session(data[d], spec, start, hist[spec.strict][d], ref[d], vol[d], atr[d], c)
        else:
            trades, exposure, peak = [], 0., 0.
        for t in trades:
            ledger.append({"strategy": "C" if c else "B", "variant": spec.name, "date": d, **t})
        net = sum(t["net_pnl"] for t in trades)
        equity += net
        daily_rows.append({"variant": spec.name, "date": d, "return": net / start, "exposure": exposure,
                           "peak_exposure": peak, "equity": equity, "fees": sum(t["fees"] for t in trades),
                           "slippage": sum(t["slippage"] for t in trades), "trades": len(trades)})
    return ledger, daily_rows


def stratified(group, n=24):
    if len(group) <= n:
        return group
    ranked = group.sort_values("net_pnl")
    quantiles = ranked.iloc[np.linspace(0, len(group) - 1, n - 8, dtype=int)]
    edges = group.sort_values(sorted(set(group) & {"entry_time", "exit_date"}) or ["net_pnl"])
    boundaries = pd.concat([edges.head(2), edges.tail(2), group.groupby("exit_reason").head(1)])
    return pd.concat([quantiles, boundaries]).drop_duplicates().head(n)


def check_a(ledger):
    universe = json.loads((ROOT / "config/research_universe.json").read_text())["stocks"]
    features, opening = {}, {}
    for sym in universe:
        b = bars(sym, 5)
        if not b:
            continue
        atr, adv, factor = prior_features(sym)
        dates = CAL.sessions_in_range("2020-11-02", OOS[1]).strftime("%Y-%m-%d")
        first_volume = pd.Series({d: x[0, 4] for d, x in b.items()}).reindex(dates)
        adjusted = first_volume * factor.reindex(dates)
        rvol = adjusted / adjusted.rolling(14).mean().shift(1)
        features[sym] = pd.DataFrame({"rvol": rvol, "atr": atr, "adv": adv})
        opening[sym] = b
    checks, exposures = [], []
    for variant, group in ledger[ledger.strategy == "A"].groupby("variant"):
        for d, trades in group.groupby("date"):
            marked = np.zeros(session_minutes(d) // 5)
            for trade in trades.itertuples(index=False):
                lo = minute(trade.entry_time) // 5
                hi = minute(trade.exit_time) // 5
                close = opening[trade.symbol][d][lo:hi, 3]
                marked[lo:hi] += trade.qty * np.where(np.isnan(close), trade.entry_fill, close)
            exposures.append({"variant": variant, "date": d,
                              "peak_exposure": float(marked.max() / trades.equity_sod.iloc[0])})
        for t in stratified(group).itertuples(index=False):
            is_paper = variant == "A0-R"
            side = 1 if t.side == "long" else -1
            b = opening[t.symbol][t.date]
            ranked = []
            for sym, feature in features.items():
                ob = opening[sym].get(t.date)
                if ob is None or np.isnan(ob[:, 3]).any():
                    continue
                f = feature.loc[t.date]
                if ob[0, 0] > 5 and f.adv >= 1_000_000 and f.atr > .5 and f.rvol >= 1:
                    if ob[0, 3] != ob[0, 0] and (variant != "A1-LO" or ob[0, 3] > ob[0, 0]):
                        ranked.append((-f.rvol, sym))
            candidates = [s for _, s in sorted(ranked)[:20 if is_paper else 8]]
            f = features[t.symbol].loc[t.date]
            enter, signal, leave = (minute(x) // 5 for x in (t.entry_time, t.signal_time, t.exit_time))
            check_n = signal if is_paper else signal - 1
            level = b[0, 1 if side > 0 else 2]
            extreme = b[check_n, 1 if side > 0 else 2]
            expected_entry = (max(b[enter, 0], level) if side == 1 else min(b[enter, 0], level)) if is_paper else b[enter, 0]
            came_from = expected_entry - side * .1 * f.atr
            if t.exit_reason.startswith("stop"):
                hit = (b[leave, 2] <= came_from) if side == 1 else (b[leave, 1] >= came_from)
                gap_px = min(b[leave, 0], came_from) if side == 1 else max(b[leave, 0], came_from)
                expected_exit = came_from if t.exit_reason == "stop_same_bar" else gap_px * (1 if is_paper else 1 - side * .0005)
            else:
                hit = None
                expected_exit = b[-1, 3] if leave == len(b) else b[leave, 0]
            if is_paper:
                in_fill, out_fill = expected_entry, expected_exit
                fees = .007 * t.qty
            else:
                in_fill, f1 = stock_cost(t.qty, expected_entry, side > 0, "competition", 3)
                out_fill, f2 = stock_cost(t.qty, expected_exit, side < 0, "competition", 3)
                fees = f1 + f2
            net = side * t.qty * (out_fill - in_fill) - fees
            checks.append({"variant": variant, "date": t.date, "symbol": t.symbol, "entry_time": t.entry_time,
                           "exit_reason": t.exit_reason, "qualified_rank": t.symbol in candidates,
                           "trigger": (side * (extreme - level) >= 0 if is_paper else side * (extreme - level) > 0),
                           "direction": side * (b[0, 3] - b[0, 0]) > 0, "observed_stop_touch": hit,
                           "on_time": is_paper or (signal == enter and minute(t.entry_time) <= 60),
                           "atr_diff": f.atr - t.meta_atr14, "rvol_diff": f.rvol - t.meta_rvol,
                           "entry_diff": expected_entry - t.entry_raw, "exit_diff": expected_exit - t.exit_raw,
                           "net_audited": net, "net_original": t.net_pnl, "net_diff": net - t.net_pnl})
    pd.DataFrame(checks).to_csv(OUT / "stock_A_sample.csv", index=False)
    pd.DataFrame(exposures).to_csv(OUT / "stock_A_peak_exposure.csv", index=False)


def main():
    b0 = Spec("B0", fraction=None, strict=False, flatten=0, lock=False, costs="paper", bps=0)
    b1 = Spec("B1")
    cap = replace(b0, name="B2", fraction=.4, costs="competition", bps=1)
    b_variants = [b0, replace(b0, name="B0-100", fraction=1.), b1, replace(b1, name="B1-LO", shorts=False), cap,
                  replace(b0, name="B-size40", fraction=.4),
                  replace(b0, name="B-size40-fees", fraction=.4, costs="competition", bps=0),
                  replace(cap, name="B-initial-100k", initial_cap=True, capital=100_000, flatten=5, lock=True, strict=True),
                  replace(cap, name="B-strict", strict=True),
                  replace(cap, name="B-flatten", strict=True, flatten=5),
                  replace(cap, name="B-delay1", strict=True, flatten=5, latency=1),
                  replace(b1, name="B-paper-vwap-aside", vwap="hlc3"),
                  replace(b0, name="B0-paper-close-HLC3", close_fill=True, vwap="hlc3"),
                  replace(b1, name="B1-slip0", bps=0), replace(b1, name="B1-slip5", bps=5),
                  replace(b1, name="B1-frictionless", costs="zero", bps=0)]
    c1 = Spec("C1", vwap="hlc3", flatten=0)
    c_variants = [c1, replace(c1, name="C1-LO", shorts=False),
                  replace(c1, name="C0", fraction=1., costs="paper", bps=0, lock=False),
                  replace(c1, name="C1-cutoff1330", c_cutoff=240),
                  replace(c1, name="C1-target-priority", c_stop_first=False),
                  replace(c1, name="C1-frictionless", costs="zero", bps=0)]
    entries, days = [], []
    inputs = spy_inputs()
    for spec in b_variants + c_variants:
        for seg, span in (("oos", OOS), ("recent", RECENT), ("dev", DEV), ("val", VAL)):
            if seg in ("dev", "val") and spec.name not in ("B0", "B1", "C1", "C1-LO"):
                continue
            trades, stats = run(spec, inputs, *span, c=spec in c_variants)
            entries.extend({"segment": seg, **v} for v in trades)
            days.extend({"segment": seg, **v} for v in stats)
        print(spec.name, flush=True)
    pd.DataFrame(entries).to_csv(OUT / "stock_independent_trades.csv", index=False)
    pd.DataFrame(days).to_csv(OUT / "stock_independent_daily.csv", index=False)
    source = pd.read_csv(REPRO / "trade_level_ledger_A_to_F.csv", skiprows=1, low_memory=False)
    check_a(source)


if __name__ == "__main__":
    main()
