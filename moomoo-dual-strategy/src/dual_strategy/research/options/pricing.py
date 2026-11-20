"""Black-Scholes-Merton (continuous dividend yield), Greeks, implied volatility, CRR American binomial, payoffs and
the competition's simulated option fee schedule (handbook T&C section 8, re-read verbatim 2026-11-19)."""

from __future__ import annotations

import math
from dataclasses import dataclass

SQRT2 = math.sqrt(2.0)
MULTIPLIER = 100


def _ncdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / SQRT2))


def _npdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def _d1d2(S, K, T, r, q, sigma):
    v = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / v
    return d1, d1 - v


def bs_price(S: float, K: float, T: float, r: float, q: float, sigma: float, call: bool) -> float:
    """European price per share. T in years; at T<=0 or sigma<=0 returns the discounted-forward intrinsic value."""
    if T <= 0 or sigma <= 0:
        fwd = S * math.exp(-q * max(T, 0)) - K * math.exp(-r * max(T, 0))
        return max(fwd, 0.0) if call else max(-fwd, 0.0)
    d1, d2 = _d1d2(S, K, T, r, q, sigma)
    if call:
        return S * math.exp(-q * T) * _ncdf(d1) - K * math.exp(-r * T) * _ncdf(d2)
    return K * math.exp(-r * T) * _ncdf(-d2) - S * math.exp(-q * T) * _ncdf(-d1)


@dataclass(frozen=True)
class Greeks:
    delta: float
    gamma: float
    vega: float      # per 1.00 change in sigma (divide by 100 for per vol point)
    theta: float     # per year
    rho: float


def bs_greeks(S: float, K: float, T: float, r: float, q: float, sigma: float, call: bool) -> Greeks:
    d1, d2 = _d1d2(S, K, T, r, q, sigma)
    dq, dr = math.exp(-q * T), math.exp(-r * T)
    gamma = dq * _npdf(d1) / (S * sigma * math.sqrt(T))
    vega = S * dq * _npdf(d1) * math.sqrt(T)
    if call:
        delta = dq * _ncdf(d1)
        theta = -S * dq * _npdf(d1) * sigma / (2 * math.sqrt(T)) - r * K * dr * _ncdf(d2) + q * S * dq * _ncdf(d1)
        rho = K * T * dr * _ncdf(d2)
    else:
        delta = -dq * _ncdf(-d1)
        theta = -S * dq * _npdf(d1) * sigma / (2 * math.sqrt(T)) + r * K * dr * _ncdf(-d2) - q * S * dq * _ncdf(-d1)
        rho = -K * T * dr * _ncdf(-d2)
    return Greeks(delta, gamma, vega, theta, rho)


def crr_price(S: float, K: float, T: float, r: float, q: float, sigma: float, call: bool, american: bool = True,
              steps: int = 400) -> float:
    """Cox-Ross-Rubinstein binomial tree; American early exercise when `american`."""
    if T <= 0 or sigma <= 0:
        return max(S - K, 0.0) if call else max(K - S, 0.0)
    dt = T / steps
    u = math.exp(sigma * math.sqrt(dt))
    d = 1 / u
    p = (math.exp((r - q) * dt) - d) / (u - d)
    if not 0 < p < 1:
        raise ValueError("CRR risk-neutral probability outside (0,1); increase steps")
    disc = math.exp(-r * dt)
    vals = [max((S * u ** j * d ** (steps - j) - K) if call else (K - S * u ** j * d ** (steps - j)), 0.0)
            for j in range(steps + 1)]
    for i in range(steps - 1, -1, -1):
        for j in range(i + 1):
            cont = disc * (p * vals[j + 1] + (1 - p) * vals[j])
            if american:
                spot = S * u ** j * d ** (i - j)
                cont = max(cont, (spot - K) if call else (K - spot))
            vals[j] = cont
    return vals[0]


def implied_vol(price: float, S: float, K: float, T: float, r: float, q: float, call: bool,
                lo: float = 1e-4, hi: float = 5.0, tol: float = 1e-8, american: bool = False) -> float | None:
    """Bisection IV. Returns None when the price is outside the no-arbitrage range of the model."""
    f = (lambda s: crr_price(S, K, T, r, q, s, call, True, 200)) if american else (lambda s: bs_price(S, K, T, r, q, s, call))
    plo, phi = f(lo), f(hi)
    if not plo - 1e-12 <= price <= phi + 1e-12:
        return None
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if f(mid) < price:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol:
            break
    return 0.5 * (lo + hi)


def vertical_payoff(S_T, k_long: float, k_short: float, call: bool):
    """Per-share expiry value of a long vertical (long k_long, short k_short). Works on floats or numpy arrays."""
    import numpy as np
    S_T = np.asarray(S_T, dtype=float)
    if call:
        return np.maximum(S_T - k_long, 0) - np.maximum(S_T - k_short, 0)
    return np.maximum(k_long - S_T, 0) - np.maximum(k_short - S_T, 0)


# ---------------------------------------------------------------- competition option fees (simulated)
def option_order_fee(contracts: int, premium: float, sell: bool) -> float:
    """One option order: commission $0.65/contract (premium > $0.10) or $0.15 (<= $0.10), min $1.99 per order;
    platform $0.30/contract; regulatory $0.015/contract; activity $0.00279/contract (sells only)."""
    if contracts <= 0:
        return 0.0
    commission = max((0.65 if premium > 0.10 else 0.15) * contracts, 1.99)
    fee = commission + 0.30 * contracts + 0.015 * contracts
    if sell:
        fee += 0.00279 * contracts
    return fee


def round_trip_fee_per_contract(contracts: int, premium: float) -> float:
    return (option_order_fee(contracts, premium, False) + option_order_fee(contracts, premium, True)) / contracts
