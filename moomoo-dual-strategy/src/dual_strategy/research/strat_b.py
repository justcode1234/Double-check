"""Strategy B: SPY Noise-Area intraday momentum (Zarattini, Aziz & Barbon 2024), SIMULATED offline only.

Bands: UB = max(Open, prevClose_div_adj)*(1+sigma), LB = min(...)*(1-sigma), sigma = 14-session mean
|close_HH:MM / open_09:30 - 1|. Decisions on bars ending 10:00..15:30. Stateless target: long iff close > UB
and > VWAP; short iff close < LB and < VWAP; else flat (equivalent to the paper's band/VWAP trailing stop).
Orders fill at the next 1-minute bar open (+delay); the paper filled at the decision price itself.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from dual_strategy.research.book import DayBook
from dual_strategy.research.etf import DECISION_MINUTES, EtfContext, decision_slot
from dual_strategy.research.execution import BASELINE_EXEC, COMPETITION, Costs, Execution, next_valid


@dataclass(frozen=True)
class BConfig:
    name: str
    sizing: str = "fixed"            # "vol_target" = min(4, 2% / sigma_SPY14), "fixed" = fraction of NAV
    fraction: float = 0.40
    shorts: bool = True
    stateful: bool = False
    vwap: str = "turnover"
    strict_history: bool = True
    flatten_before_close: int = 5     # minutes; 0 = exit at the 16:00 closing price
    loss_lock: float | None = 0.0125
    costs: Costs = COMPETITION
    exe: Execution = field(default=BASELINE_EXEC)
    label: str = ""


def _fraction(cfg: BConfig, ctx: EtfContext, t: int) -> float:
    if cfg.sizing == "vol_target":
        vol = ctx.daily_vol[t]
        return float("nan") if not vol > 0 else min(4.0, 0.02 / vol)
    return cfg.fraction


def run_day(ctx: EtfContext, t: int, equity: float, cfg: BConfig, book: DayBook) -> dict:
    b = ctx.b1
    o, h, l, c, v = b.open[t], b.high[t], b.low[t], b.close[t], b.volume[t]
    vwap = ctx.vwap_turnover[t] if cfg.vwap == "turnover" else ctx.vwap_hlc3[t]
    sigma = ctx.sigma_strict if cfg.strict_history else ctx.sigma_skip
    n = int(b.expected[t])
    close_min = 570 + n
    decisions = {decision_slot(m): m for m in DECISION_MINUTES if m < close_min}
    prev = ctx.prev_close[t] - ctx.dividend[t]
    open0 = o[0]
    frac = _fraction(cfg, ctx, t)
    sym = ctx.symbol
    flatten_bar = n - cfg.flatten_before_close if cfg.flatten_before_close else None
    side, stop = 0, None
    pending: tuple[int, int, str, int] | None = None
    locked = False
    exposure, worst = [], 0.0

    def market_exit(k: int, reason: str, sig_min: int) -> None:
        nonlocal side, stop
        k2 = next_valid(c, k, n)
        rows = [(o[k2], book.bar_start(k2), v[k2])] if k2 is not None else []
        if k2 is not None and k2 + 1 < n:
            rows.append((o[k2 + 1], book.bar_start(k2 + 1), v[k2 + 1]))
        rows.append((c[n - 1], close_min, math.inf))
        book.exit(sym, rows, reason, sig_min)
        side, stop = 0, None

    for j in range(n):
        if math.isnan(c[j]):
            exposure.append(exposure[-1] if exposure else 0.0)
            continue
        if pending is not None and j >= pending[0]:
            exec_bar, target, reason, sig_min = pending
            pending = None
            if side and target != side:
                if book.rejected(sym, "exit", j):
                    pending = (j + 1, target, reason, sig_min)
                else:
                    market_exit(j, reason, sig_min)
            if target and not side and not locked and (flatten_bar is None or j < flatten_bar):
                qty = int(math.floor(frac * equity / max(open0, o[j]))) if math.isfinite(frac) else 0
                if book.enter(sym, target, qty, o[j], v[j], book.bar_start(j), signal_minute=sig_min,
                              signal=reason, order_type="market", stop=None,
                              meta={"fraction": round(frac, 4)}):
                    side = target
        if flatten_bar is not None and j == flatten_bar and side:
            market_exit(j, f"flatten_{cfg.flatten_before_close}m_before_close", book.bar_start(j))
            pending = None
        marks = {sym: c[j]}
        pnl_now = book.realized + book.unrealized(marks)
        worst = min(worst, pnl_now)
        exposure.append(book.gross_notional(marks) / equity)
        end = book.bar_end(j)
        if cfg.loss_lock and not locked and (j + 1) % book.exe.monitor_every == 0 and pnl_now <= -cfg.loss_lock * equity:
            locked = True
            if side:
                pending = (j + 1 + book.exe.delay, 0, "daily_loss_lock", end)
        if j in decisions and not locked and (flatten_bar is None or j + 1 < flatten_bar):
            if book.missed(sym, "decision", j):
                continue
            sig = sigma[j][t]
            if not (sig > 0) or not (prev > 0) or math.isnan(vwap[j]):
                continue
            ub, lb, px = max(open0, prev) * (1 + sig), min(open0, prev) * (1 - sig), c[j]
            if cfg.stateful and side:
                stop = max(stop, ub, vwap[j]) if side > 0 else min(stop, lb, vwap[j])
                exit_now = px <= stop if side > 0 else px >= stop
                target, reason = (0, "trailing_exit") if exit_now else (side, "hold")
            elif px > ub and px > vwap[j]:
                target, reason = 1, "above_upper_and_vwap"
            elif px < lb and px < vwap[j] and cfg.shorts:
                target, reason = -1, "below_lower_and_vwap"
            else:
                target, reason = 0, "inside_noise_or_vwap"
            if cfg.stateful and not side and target:
                stop = max(ub, vwap[j]) if target > 0 else min(lb, vwap[j])
            if target != side:
                pending = (j + 1 + book.exe.delay, target, reason, end)
    if side:
        book.exit(sym, [(c[n - 1], close_min, math.inf)], "session_close", close_min, order_type="close")
    return book.summary(exposure, worst)
