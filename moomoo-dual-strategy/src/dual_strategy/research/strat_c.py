"""Strategy C: SPY intraday VWAP mean reversion. A PRE-REGISTERED HYPOTHESIS, not a published strategy.

d = (close_5m - VWAP_5m_HLC3) / ATR14_daily_prior. On 5m bars ending >= 10:00 a long setup arms when d <= -band,
and triggers on a later finalized 5m close with d > -band within 6 bars (symmetric short). Entry at the next
1-minute open (+delay). Exits: 1m low/high touches entry -/+ stop_atr*ATR (checked before the target on the same
bar), 1m close back at/through the latest finalized 5m VWAP, max hold, or 15:50; non-stop exits fill at the next
1m open. One position at a time; no entries after 14:30 (half-days: close-90m), hard exit 15:50 (half-days: close-10m)."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from dual_strategy.research.book import DayBook, hhmm
from dual_strategy.research.etf import EtfContext
from dual_strategy.research.execution import ETF_COSTS, ETF_EXEC, Costs, Execution, next_valid, stop_price


@dataclass(frozen=True)
class CConfig:
    name: str
    band: float = 0.30
    stop_atr: float = 0.25
    hold: int = 60
    expiry_bars: int = 6
    shorts: bool = True
    sizing: str = "competition"      # "competition": min(0.25% risk, 40% notional, buying power); "full": 100% NAV
    risk_frac: float = 0.0025
    notional_cap: float = 0.40
    vwap: str = "hlc3"
    loss_lock: float | None = 0.0125
    costs: Costs = ETF_COSTS
    exe: Execution = field(default=ETF_EXEC)
    label: str = ""


def _qty(cfg: CConfig, equity: float, ref: float, atr: float) -> int:
    if cfg.sizing == "full":
        return int(math.floor(equity / ref))
    by_risk = cfg.risk_frac * equity / (cfg.stop_atr * atr)
    return int(math.floor(min(by_risk, cfg.notional_cap * equity / ref, equity / ref)))


def run_day(ctx: EtfContext, t: int, equity: float, cfg: CConfig, book: DayBook) -> dict:
    b = ctx.b1
    o, h, l, c, v = b.open[t], b.high[t], b.low[t], b.close[t], b.volume[t]
    vwap5 = ctx.vwap5_hlc3[t] if cfg.vwap == "hlc3" else ctx.vwap5_turnover[t]
    c5 = ctx.b5.close[t]
    n = int(b.expected[t])
    close_min = 570 + n
    last_entry = min(14 * 60 + 30, close_min - 90)    # 14:30 on a 16:00 close, 11:30 on a 13:00 half-day
    hard_exit = min(15 * 60 + 50, close_min - 10)
    atr = ctx.atr14[t]
    sym = ctx.symbol
    exposure, worst = [], 0.0
    if not (atr > 0):
        return book.summary([0.0] * n, 0.0)
    leg_side, stop = 0, 0.0
    entry_min = 0
    pending: tuple | None = None
    setup: tuple[int, int] | None = None
    reset = {1: False, -1: False}
    latest_vwap = math.nan
    locked = False

    for j in range(n):
        if math.isnan(c[j]):
            exposure.append(exposure[-1] if exposure else 0.0)
            continue
        if pending is not None and j >= pending[0]:
            kind, payload = pending[1], pending[2]
            pending = None
            if kind == "entry" and not leg_side and not locked:
                side, sig_min, reason, ref = payload
                qty = _qty(cfg, equity, ref, atr)
                st = o[j] - side * cfg.stop_atr * atr
                if book.enter(sym, side, qty, o[j], v[j], book.bar_start(j), signal_minute=sig_min, signal=reason,
                              order_type="market", stop=st, meta={"atr14": round(atr, 4), "band": cfg.band}):
                    leg_side, stop, entry_min = side, st, book.bar_start(j)
            elif kind == "exit" and leg_side:
                reason, sig_min = payload
                if book.rejected(sym, "exit", j):
                    pending = (j + 1, "exit", payload)
                else:
                    rows = [(o[j], book.bar_start(j), v[j])]
                    k2 = next_valid(c, j + 1, n)
                    if k2 is not None:
                        rows.append((o[k2], book.bar_start(k2), v[k2]))
                    rows.append((c[n - 1], close_min, math.inf))
                    book.exit(sym, rows, reason, sig_min)
                    leg_side = 0
                    reset = {1: True, -1: True}
        if leg_side and (l[j] <= stop if leg_side > 0 else h[j] >= stop):
            raw = stop_price(o[j], c[j], stop, leg_side > 0, book.exe)
            book.exit(sym, [(raw, book.bar_end(j) if book.exe.stop_mode == "stressed" else book.bar_start(j), v[j]),
                            (c[n - 1], close_min, math.inf)], "stop", book.bar_start(j), order_type=f"stop_{book.exe.stop_mode}")
            leg_side, pending = 0, None
            reset = {1: True, -1: True}
        end = book.bar_end(j)
        marks = {sym: c[j]}
        pnl_now = book.realized + book.unrealized(marks)
        worst = min(worst, pnl_now)
        exposure.append(book.gross_notional(marks) / equity)
        if (j + 1) % 5 == 0:
            latest_vwap = vwap5[(j + 1) // 5 - 1]
        if cfg.loss_lock and not locked and (j + 1) % book.exe.monitor_every == 0 and pnl_now <= -cfg.loss_lock * equity:
            locked = True
            setup = None
            if leg_side and pending is None:
                pending = (j + 1 + book.exe.delay, "exit", ("daily_loss_lock", end))
        if leg_side and pending is None:
            reason = None
            if end >= hard_exit:
                reason = "time_1550"
            elif end - entry_min >= cfg.hold:
                reason = "max_hold"
            elif not math.isnan(latest_vwap) and (c[j] >= latest_vwap if leg_side > 0 else c[j] <= latest_vwap):
                reason = "vwap_target"
            if reason:
                pending = (j + 1 + book.exe.delay, "exit", (reason, end))
        if (j + 1) % 5 or locked:
            continue
        k = (j + 1) // 5 - 1
        if end < 10 * 60 or math.isnan(c5[k]) or math.isnan(vwap5[k]):
            continue
        d = (c5[k] - vwap5[k]) / atr
        if d > -cfg.band:
            reset[1] = False
        if d < cfg.band:
            reset[-1] = False
        if leg_side or pending is not None:
            setup = None
            continue
        if setup is not None:
            side, armed = setup
            if (d > -cfg.band) if side > 0 else (d < cfg.band):
                setup = None
                if end <= last_entry and not book.missed(sym, "trigger", k):
                    pending = (j + 1 + book.exe.delay, "entry",
                               (side, end, f"{'long' if side > 0 else 'short'}_reentry d={d:.3f} armed_bar_end={hhmm(575 + 5 * armed)}", c5[k]))
                continue
            if k - armed >= cfg.expiry_bars:
                setup = None
                reset[side] = True
            continue
        if d <= -cfg.band and not reset[1]:
            setup = (1, k)
        elif d >= cfg.band and not reset[-1] and cfg.shorts:
            setup = (-1, k)
    if leg_side:
        book.exit(sym, [(c[n - 1], close_min, math.inf)], "session_close", close_min, order_type="close")
    return book.summary(exposure, worst)
