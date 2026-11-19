"""Strategy A: 5-minute Opening Range Breakout on Stocks in Play (Zarattini, Barbon & Aziz 2024), SIMULATED.

Filters (paper): open > $5, ADV14 >= 1M shares, ATR14 > $0.50 (prior sessions, split-adjusted), RVOL >= 1.0,
top-N by RVOL. Direction from the 09:30-09:35 candle (green long / red short / doji none). Stop 10% ATR14
from the ACTUAL entry (QuantConnect variant: from the opening-range level). Exit at stop or session end.
Only 5-minute stock bars exist, so intrabar ordering is unknown: a resting-stop entry whose own bar also reaches
the stop is assumed stopped (pessimistic), with an optimistic ablation.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from functools import lru_cache

import numpy as np

from dual_strategy.research.book import DayBook
from dual_strategy.research.data import Intraday, load_intraday, sector_map
from dual_strategy.research.execution import BASELINE_EXEC, COMPETITION, Costs, Execution, next_valid, stop_price
from dual_strategy.research.features import opening_rvol, stock_daily_features


@dataclass(frozen=True)
class AConfig:
    name: str
    top_n: int = 8
    rvol_min: float = 1.0
    min_open: float = 5.0
    min_adv: float = 1_000_000
    min_atr: float = 0.50
    entry: str = "bar_close_market"   # "resting_stop" (paper), "bar_close_market", "touch_market"
    stop_anchor: str = "entry"        # "entry" (paper) or "range" (QuantConnect variant)
    stop_atr: float = 0.10
    shorts: bool = True
    sizing: str = "competition"       # "paper": min(1% risk, 4x NAV / top_n); "competition": spec/prompt.md
    risk_frac: float = 0.0025
    name_cap: float = 0.15
    max_concurrent: int | None = 4
    gross_cap: float = 1.0
    sector_cap: float | None = 0.30
    same_bar_stop: str = "pessimistic"
    flatten_before_close: int = 5
    loss_lock: float | None = 0.0125
    entry_deadline: int | None = 10 * 60 + 30   # latest entry fill (minute of day), config/orb.yaml
    liquidity_frac: float | None = 0.01          # max shares vs the last closed bar's volume, config/backtest.yaml
    costs: Costs = COMPETITION
    exe: Execution = field(default=BASELINE_EXEC)
    label: str = ""


@dataclass
class StockData:
    symbol: str
    b5: Intraday
    complete: np.ndarray
    atr14: np.ndarray
    adv14: np.ndarray
    rvol: np.ndarray
    sector: str


@lru_cache(maxsize=2)
def stock_universe(symbols: tuple[str, ...]) -> dict[str, StockData]:
    sectors = sector_map()
    out = {}
    for sym in symbols:
        b5 = load_intraday(sym, 5)
        feats = stock_daily_features(sym).reindex(b5.days)
        out[sym] = StockData(sym, b5, b5.complete(), feats["atr14"].to_numpy(), feats["adv14"].to_numpy(),
                             opening_rvol(b5, sym), sectors.get(sym, "UNKNOWN"))
    return out


def candidates(universe: dict[str, StockData], t: int, cfg: AConfig) -> list[dict]:
    rows = []
    for sym, s in universe.items():
        if not s.complete[t]:
            continue
        o0, c0 = s.b5.open[t, 0], s.b5.close[t, 0]
        atr, adv, rvol = s.atr14[t], s.adv14[t], s.rvol[t]
        if not (o0 > cfg.min_open and adv >= cfg.min_adv and atr > cfg.min_atr and rvol >= cfg.rvol_min):
            continue
        side = 1 if c0 > o0 else -1 if c0 < o0 else 0
        if side == 0 or (side < 0 and not cfg.shorts):
            continue
        rows.append({"symbol": sym, "side": side, "rvol": float(rvol), "atr": float(atr),
                     "orh": float(s.b5.high[t, 0]), "orl": float(s.b5.low[t, 0]), "sector": s.sector})
    rows.sort(key=lambda r: (-r["rvol"], r["symbol"]))
    return rows[:cfg.top_n]


def _stop_for(cfg: AConfig, cand: dict, entry_raw: float) -> float:
    dist = cfg.stop_atr * cand["atr"]
    if cfg.stop_anchor == "range":
        return cand["orh"] - dist if cand["side"] > 0 else cand["orl"] + dist
    return entry_raw - cand["side"] * dist


def _size(cfg: AConfig, book: DayBook, cand: dict, raw: float, stop: float, marks: dict, sectors: dict) -> int:
    equity = book.equity
    risk_ps = abs(raw - stop)
    if risk_ps <= 0:
        return 0
    if cfg.sizing == "paper":
        return int(math.floor(min(0.01 * equity / risk_ps, 4.0 * equity / (cfg.top_n * raw))))
    qty = min(cfg.risk_frac * equity / risk_ps, cfg.name_cap * equity / raw)
    gross_room = cfg.gross_cap * equity - book.gross_notional(marks)
    qty = min(qty, gross_room / raw)
    if cfg.sector_cap is not None:
        used = sum(abs(leg.qty * marks.get(sym, leg.entry_exec)) for sym, leg in book.open_legs.items()
                   if sectors.get(sym) == cand["sector"])
        qty = min(qty, (cfg.sector_cap * equity - used) / raw)
    return max(0, int(math.floor(qty)))


def run_day(universe: dict[str, StockData], t: int, equity: float, cfg: AConfig, book: DayBook) -> dict:
    cands = candidates(universe, t, cfg)
    any_s = next(iter(universe.values()))
    n = int(any_s.b5.expected[t])
    close_min = 570 + 5 * n
    sectors = {c["symbol"]: c["sector"] for c in cands}
    state = {c["symbol"]: "waiting" for c in cands}
    by_sym = {c["symbol"]: c for c in cands}
    pending_entries: list[tuple[int, dict, int]] = []
    pending_exit: tuple[int, str, int] | None = None
    flatten_bar = n - 1 if cfg.flatten_before_close else None
    locked = False
    exposure, worst = [], 0.0
    book.events["candidates"] = len(cands)

    def bars(sym):
        b = universe[sym].b5
        return b.open[t], b.high[t], b.low[t], b.close[t], b.volume[t]

    def flatten_all(j: int, reason: str, sig_min: int) -> None:
        for sym in list(book.open_legs):
            o, h, l, c, v = bars(sym)
            k2 = next_valid(c, j, n)
            rows = [(o[k2], book.bar_start(k2), v[k2])] if k2 is not None else []
            rows.append((c[n - 1], close_min, math.inf))
            book.exit(sym, rows, reason, sig_min)
            state[sym] = "done"

    def try_enter(sym: str, j: int, raw: float, sig_min: int, order_type: str, marks: dict) -> bool:
        cand = by_sym[sym]
        if cfg.max_concurrent is not None and len(book.open_legs) >= cfg.max_concurrent:
            return False
        if cfg.entry_deadline is not None and book.bar_start(j) > cfg.entry_deadline:
            return False
        stop = _stop_for(cfg, cand, raw)
        if (raw - stop) * cand["side"] <= 0:
            return False
        qty = _size(cfg, book, cand, raw, stop, marks, sectors)
        o, h, l, c, v = bars(sym)
        if cfg.liquidity_frac is not None:
            qty = min(qty, int(math.floor(cfg.liquidity_frac * v[j - 1]))) if v[j - 1] > 0 else 0
        leg = book.enter(sym, cand["side"], qty, raw, v[j], book.bar_start(j), signal_minute=sig_min,
                         signal=f"{'long' if cand['side'] > 0 else 'short'}_orb rvol={cand['rvol']:.2f}",
                         order_type=order_type, stop=stop,
                         meta={"rvol": round(cand["rvol"], 3), "atr14": round(cand["atr"], 4), "sector": cand["sector"]})
        return leg is not None

    for j in range(1, n):
        marks = {s: bars(s)[3][j - 1] for s in book.open_legs}
        if pending_exit is not None and j >= pending_exit[0]:
            flatten_all(j, pending_exit[1], pending_exit[2])
            pending_exit = None
        if flatten_bar is not None and j == flatten_bar:
            flatten_all(j, f"flatten_{cfg.flatten_before_close}m_before_close", book.bar_start(j))
            locked = True
        still = []
        for due, cand, sig_min in pending_entries:
            sym = cand["symbol"]
            if j < due:
                still.append((due, cand, sig_min))
                continue
            o = bars(sym)[0]
            if state[sym] == "triggered" and not locked and not math.isnan(o[j]):
                entered = try_enter(sym, j, o[j], sig_min, "market", marks)
                state[sym] = "open" if entered else "done"
            elif state[sym] == "triggered":
                state[sym] = "done"
        pending_entries = still
        for cand in cands:
            sym = cand["symbol"]
            o, h, l, c, v = bars(sym)
            if math.isnan(c[j]):
                continue
            side = cand["side"]
            entered_now = False
            if cfg.entry in ("resting_stop", "touch_market") and state[sym] == "waiting" and not locked:
                level = cand["orh"] if side > 0 else cand["orl"]
                if (h[j] >= level) if side > 0 else (l[j] <= level):
                    raw = max(o[j], level) if side > 0 else min(o[j], level)
                    if cfg.entry == "touch_market":
                        raw *= 1 + side * book.exe.stop_latency_bps / 1e4
                    if book.missed(sym, "trigger", j):
                        state[sym] = "done"
                    else:
                        ok = try_enter(sym, j, raw, book.bar_start(j),
                                       "stop_entry" if cfg.entry == "resting_stop" else "touch_market", marks)
                        state[sym] = "open" if ok else "done"
                        entered_now = ok
            if state[sym] == "open" and sym in book.open_legs:
                leg = book.open_legs[sym]
                hit = (l[j] <= leg.stop) if side > 0 else (h[j] >= leg.stop)
                if hit and entered_now and cfg.same_bar_stop == "optimistic":
                    hit = False
                if hit:
                    if entered_now:
                        raw = leg.stop
                        if book.exe.stop_mode != "native":
                            raw *= 1 - side * book.exe.stop_latency_bps / 1e4
                    else:
                        raw = stop_price(o[j], c[j], leg.stop, side > 0, book.exe)
                    book.exit(sym, [(raw, book.bar_start(j), v[j]), (c[n - 1], close_min, math.inf)],
                              "stop_same_bar" if entered_now else "stop", book.bar_start(j),
                              order_type=f"stop_{book.exe.stop_mode}")
                    state[sym] = "done"
        marks = {s: bars(s)[3][j] for s in book.open_legs}
        marks = {s: (p if not math.isnan(p) else book.open_legs[s].entry_exec) for s, p in marks.items()}
        pnl_now = book.realized + book.unrealized(marks)
        worst = min(worst, pnl_now)
        exposure.append(book.gross_notional(marks) / equity)
        end = book.bar_end(j)
        if cfg.loss_lock and not locked and pnl_now <= -cfg.loss_lock * equity:
            locked = True
            pending_entries = []
            if book.open_legs:
                pending_exit = (j + 1 + book.exe.delay, "daily_loss_lock", end)
        if cfg.entry == "bar_close_market" and not locked and (flatten_bar is None or j + 1 < flatten_bar):
            for cand in cands:
                sym = cand["symbol"]
                o, h, l, c, v = bars(sym)
                if state[sym] != "waiting" or math.isnan(c[j]):
                    continue
                broke = h[j] > cand["orh"] if cand["side"] > 0 else l[j] < cand["orl"]
                if broke:
                    if book.missed(sym, "trigger", j):
                        state[sym] = "done"
                        continue
                    state[sym] = "triggered"
                    pending_entries.append((j + 1 + book.exe.delay, cand, end))
    for sym in list(book.open_legs):
        c = bars(sym)[3]
        k = n - 1
        while k > 0 and math.isnan(c[k]):
            k -= 1
        book.exit(sym, [(c[k], close_min, math.inf)], "session_close", close_min, order_type="close")
    return book.summary(exposure, worst)
