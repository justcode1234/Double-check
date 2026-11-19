"""Fee, slippage and fill rules shared by every research portfolio. All prices are raw (unadjusted) session prices."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, replace

import numpy as np


@dataclass(frozen=True)
class Costs:
    """Competition: $0.99/order platform fee + $0.000166/share sold (cap $8.30/trade), no commission.
    Paper replication: $0.0035/share commission (+ the paper's $0.001/share slippage for Noise-Area)."""

    name: str
    per_order: float = 0.0
    sell_per_share: float = 0.0
    sell_cap: float = 0.0
    per_share: float = 0.0
    slip_per_share: float = 0.0
    slip_bps: float = 0.0

    def fee(self, qty: int, sell: bool) -> float:
        if qty <= 0:
            return 0.0
        fee = self.per_order + self.per_share * qty
        if sell:
            fee += min(self.sell_per_share * qty, self.sell_cap)
        return fee

    def slipped(self, raw: float, buy: bool) -> float:
        """Adverse execution price for a buy (higher) or a sell (lower)."""
        sign = 1 if buy else -1
        return raw * (1 + sign * self.slip_bps / 1e4) + sign * self.slip_per_share

    def with_slippage(self, bps: float) -> "Costs":
        return replace(self, slip_bps=bps)


COMPETITION = Costs("competition", per_order=0.99, sell_per_share=0.000166, sell_cap=8.30, slip_bps=5.0)
PAPER_ORB = Costs("paper_orb", per_share=0.0035)
PAPER_NOISE = Costs("paper_noise", per_share=0.0035, slip_per_share=0.001)


@dataclass(frozen=True)
class Execution:
    """delay: extra bars after the next-bar open before a market order fills (0 = next bar open).
    stop_mode: native = resting stop, fill min(open, stop) for a long;
               software = native price worsened by stop_latency_bps (polling + market order);
               stressed = act only at the close of the bar that touched the stop (worse of stop and close).
    participation: max fill per bar as a multiple of that bar's volume (handbook partial-fill rule = 20x).
    reject_rate / miss_rate: deterministic per-order / per-signal probabilities (seeded hash)."""

    delay: int = 0
    stop_mode: str = "software"
    stop_latency_bps: float = 5.0
    participation: float = 20.0
    reject_rate: float = 0.0
    miss_rate: float = 0.0
    monitor_every: int = 1
    seed: int = 20261119

    def with_(self, **kw) -> "Execution":
        return replace(self, **kw)


BASELINE_EXEC = Execution()
NATIVE_EXEC = Execution(stop_mode="native", stop_latency_bps=0.0)


def chance(rate: float, seed: int, *keys) -> bool:
    """Deterministic Bernoulli draw keyed by the event identity, so variants are reproducible."""
    if rate <= 0:
        return False
    digest = hashlib.sha256(("|".join(map(str, (seed, *keys)))).encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2**64 < rate


def stop_price(o: float, c: float, stop: float, long: bool, exe: Execution) -> float:
    """Raw price for a stop exit on a bar whose range touched the stop."""
    if long:
        native = min(o, stop)
        if exe.stop_mode == "stressed":
            native = min(native, c)
        return native * (1 - exe.stop_latency_bps / 1e4) if exe.stop_mode != "native" else native
    native = max(o, stop)
    if exe.stop_mode == "stressed":
        native = max(native, c)
    return native * (1 + exe.stop_latency_bps / 1e4) if exe.stop_mode != "native" else native


def capacity(volume: float, exe: Execution) -> int:
    if not math.isfinite(volume) or volume <= 0:
        return 0
    return int(math.floor(exe.participation * volume))


def next_valid(close_row: np.ndarray, k: int, last: int) -> int | None:
    """First bar index >= k (and < last) that exists; missing bars are stale data, never interpolated."""
    while k < last:
        if not math.isnan(close_row[k]):
            return k
        k += 1
    return None
