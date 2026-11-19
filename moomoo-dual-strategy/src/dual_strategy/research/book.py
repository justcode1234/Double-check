"""Per-session order/position book for one simulated portfolio. Every record is SIMULATED, never a broker order."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

import numpy as np

from dual_strategy.research.data import OPEN_MINUTE
from dual_strategy.research.execution import Costs, Execution, capacity, chance


def hhmm(minute: int) -> str:
    return f"{minute // 60:02d}:{minute % 60:02d}"


@dataclass
class Leg:
    symbol: str
    side: int
    qty: int
    requested: int
    entry_raw: float
    entry_exec: float
    entry_minute: int
    signal_minute: int
    signal: str
    order_type: str
    stop: float | None
    risk: float
    entry_fee: float
    meta: dict = field(default_factory=dict)


@dataclass
class DayBook:
    strategy: str
    variant: str
    day: date
    equity: float
    costs: Costs
    exe: Execution
    minutes: int
    open_legs: dict[str, Leg] = field(default_factory=dict)
    trades: list[dict] = field(default_factory=list)
    realized: float = 0.0
    fees: float = 0.0
    slippage: float = 0.0
    events: dict[str, int] = field(default_factory=lambda: {"rejected": 0, "missed": 0, "partial": 0, "no_capacity": 0})
    turnover: float = 0.0

    def bar_start(self, k: int) -> int:
        return OPEN_MINUTE + k * self.minutes

    def bar_end(self, k: int) -> int:
        return OPEN_MINUTE + (k + 1) * self.minutes

    def rejected(self, *keys) -> bool:
        if chance(self.exe.reject_rate, self.exe.seed, "reject", self.strategy, self.variant, self.day, *keys):
            self.events["rejected"] += 1
            return True
        return False

    def missed(self, *keys) -> bool:
        if chance(self.exe.miss_rate, self.exe.seed, "miss", self.strategy, self.variant, self.day, *keys):
            self.events["missed"] += 1
            return True
        return False

    def enter(self, symbol: str, side: int, qty: int, raw: float, volume: float, minute: int, *,
              signal_minute: int, signal: str, order_type: str, stop: float | None, meta: dict | None = None) -> Leg | None:
        """Fill an entry at a raw price (market: bar open; resting stop: trigger level) with slippage/partial fill."""
        if qty <= 0 or symbol in self.open_legs:
            return None
        if self.rejected(symbol, "entry", minute):
            return None
        filled = min(qty, capacity(volume, self.exe))
        if filled <= 0:
            self.events["no_capacity"] += 1
            return None
        if filled < qty:
            self.events["partial"] += 1
        price = self.costs.slipped(raw, buy=side > 0)
        fee = self.costs.fee(filled, sell=side < 0)
        self.fees += fee
        self.slippage += abs(price - raw) * filled
        self.turnover += price * filled
        risk = abs(price - stop) * filled if stop is not None else math.nan
        leg = Leg(symbol, side, filled, qty, raw, price, minute, signal_minute, signal, order_type, stop, risk, fee, meta or {})
        self.open_legs[symbol] = leg
        return leg

    def exit(self, symbol: str, prices: list[tuple[float, int, float]], reason: str, signal_minute: int,
             order_type: str = "market") -> dict:
        """Close a leg through [(raw_price, minute, volume_capacity_basis)...]; later rows absorb partial remainders.
        The final row is always fully filled (session end: forced flatten at the last available price)."""
        leg = self.open_legs.pop(symbol)
        remaining, raw_notional, exec_notional, minute = leg.qty, 0.0, 0.0, prices[-1][1]
        for i, (raw, minute, volume) in enumerate(prices):
            take = remaining if i == len(prices) - 1 else min(remaining, capacity(volume, self.exe))
            if take < remaining and i < len(prices) - 1 and take > 0:
                self.events["partial"] += 1
            if take <= 0:
                continue
            raw_notional += raw * take
            exec_notional += self.costs.slipped(raw, buy=leg.side < 0) * take
            remaining -= take
            if remaining == 0:
                break
        raw_exit, exec_exit = raw_notional / leg.qty, exec_notional / leg.qty
        fee = self.costs.fee(leg.qty, sell=leg.side > 0)
        gross = leg.side * (raw_exit - leg.entry_raw) * leg.qty
        slip = abs(exec_exit - raw_exit) * leg.qty + abs(leg.entry_exec - leg.entry_raw) * leg.qty
        net = leg.side * (exec_exit - leg.entry_exec) * leg.qty - fee - leg.entry_fee
        self.fees += fee
        self.slippage += abs(exec_exit - raw_exit) * leg.qty
        self.turnover += exec_exit * leg.qty
        self.realized += net
        record = {
            "label": "SIMULATED", "strategy": self.strategy, "variant": self.variant, "date": self.day.isoformat(),
            "symbol": symbol, "side": "long" if leg.side > 0 else "short", "signal": leg.signal,
            "signal_time": hhmm(leg.signal_minute), "order_type": leg.order_type, "entry_time": hhmm(leg.entry_minute),
            "requested_qty": leg.requested, "qty": leg.qty, "entry_raw": round(float(leg.entry_raw), 4),
            "entry_fill": round(float(leg.entry_exec), 4), "stop": None if leg.stop is None else round(float(leg.stop), 4),
            "exit_signal_time": hhmm(signal_minute), "exit_order_type": order_type, "exit_time": hhmm(minute),
            "exit_raw": round(float(raw_exit), 4), "exit_fill": round(float(exec_exit), 4), "exit_reason": reason,
            "gross_pnl": round(float(gross), 2), "fees": round(float(fee + leg.entry_fee), 2), "slippage_cost": round(float(slip), 2),
            "net_pnl": round(float(net), 2), "risk_dollars": None if math.isnan(leg.risk) else round(float(leg.risk), 2),
            "r_multiple": None if math.isnan(leg.risk) or leg.risk <= 0 else round(float(net / leg.risk), 4),
            "notional": round(float(leg.entry_exec * leg.qty), 2), "equity_sod": round(float(self.equity), 2),
            "hold_minutes": minute - leg.entry_minute, **{f"meta_{k}": v for k, v in leg.meta.items()},
        }
        self.trades.append(record)
        return record

    def unrealized(self, marks: dict[str, float]) -> float:
        total = 0.0
        for sym, leg in self.open_legs.items():
            px = marks.get(sym)
            if px is not None and not math.isnan(px):
                total += leg.side * (px - leg.entry_exec) * leg.qty - self.costs.fee(leg.qty, sell=leg.side > 0)
        return total

    def gross_notional(self, marks: dict[str, float]) -> float:
        return sum(abs(leg.qty * marks.get(s, leg.entry_exec)) for s, leg in self.open_legs.items()
                   if not math.isnan(marks.get(s, leg.entry_exec)))

    def summary(self, exposure_path: list[float], worst_mark: float) -> dict:
        return {"date": self.day, "pnl": self.realized, "fees": self.fees, "slippage": self.slippage,
                "exposure": float(np.mean(exposure_path)) if exposure_path else 0.0,
                "in_market": float(np.mean([x > 0 for x in exposure_path])) if exposure_path else 0.0,
                "turnover": self.turnover, "trades": len(self.trades), "worst_intraday": worst_mark, **self.events}
