"""Shared SPY/ETF intraday context: 1m bars, derived 5m bars, VWAPs, daily features, dividends."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from functools import lru_cache

import numpy as np

from dual_strategy.research.data import Intraday, calendar_frame, dividends, load_intraday, resample
from dual_strategy.research.features import noise_sigma, session_vwap, spy_daily_vol, stock_daily_features

DECISION_MINUTES = [10 * 60 + 30 * i for i in range(12)]  # bar END times 10:00 ... 15:30


@dataclass
class EtfContext:
    symbol: str
    days: list[date]
    b1: Intraday
    b5: Intraday
    complete1: np.ndarray
    vwap_turnover: np.ndarray
    vwap_hlc3: np.ndarray
    vwap5_hlc3: np.ndarray
    vwap5_turnover: np.ndarray
    sigma_strict: dict
    sigma_skip: dict
    daily_vol: np.ndarray
    atr14: np.ndarray
    prev_close: np.ndarray
    dividend: np.ndarray
    close_minute: np.ndarray


def decision_slot(minute: int) -> int:
    return minute - 570 - 1


@lru_cache(maxsize=4)
def etf_context(symbol: str = "US.SPY") -> EtfContext:
    b1 = load_intraday(symbol, 1)
    b5 = resample(b1, 5)
    days = b1.days
    slots = [decision_slot(m) for m in DECISION_MINUTES]
    feats = stock_daily_features(symbol)
    div = dividends(symbol)
    vol = spy_daily_vol(days) if symbol == "US.SPY" else np.full(len(days), np.nan)
    return EtfContext(
        symbol, days, b1, b5, b1.complete(),
        session_vwap(b1, "turnover"), session_vwap(b1, "hlc3"), session_vwap(b5, "hlc3"), session_vwap(b5, "turnover"),
        noise_sigma(b1, slots, strict=True), noise_sigma(b1, slots, strict=False),
        vol, feats["atr14"].reindex(days).to_numpy(), feats["prev_close"].reindex(days).to_numpy(),
        np.array([div.get(d, 0.0) for d in days]), calendar_frame()["close_minute"].reindex(days).to_numpy(),
    )
