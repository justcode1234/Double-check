from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from math import isfinite
from typing import Literal

StrategyId = Literal["orb", "noise_area"]


def aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timestamp must include an explicit timezone")
    return value


def positive(*values: float) -> None:
    if any(not isfinite(v) or v <= 0 for v in values):
        raise ValueError("Expected finite positive prices")


@dataclass(frozen=True)
class Bar:
    symbol: str
    end: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    turnover: float | None = None
    minutes: int = 1

    def __post_init__(self):
        aware(self.end)
        positive(self.open, self.high, self.low, self.close)
        if self.low > min(self.open, self.close) or self.high < max(self.open, self.close):
            raise ValueError("Invalid OHLC range")
        if not isfinite(self.volume) or self.volume < 0 or self.minutes < 1:
            raise ValueError("Invalid bar volume or interval")
        if self.turnover is not None and (not isfinite(self.turnover) or self.turnover < 0):
            raise ValueError("Invalid turnover")

    @property
    def start(self) -> datetime:
        return self.end - timedelta(minutes=self.minutes)


@dataclass(frozen=True)
class DailyBar:
    symbol: str
    session: date
    open: float
    high: float
    low: float
    close: float
    volume: float

    def __post_init__(self):
        positive(self.open, self.high, self.low, self.close)
        if self.low > min(self.open, self.close) or self.high < max(self.open, self.close):
            raise ValueError("Invalid daily OHLC range")
        if not isfinite(self.volume) or self.volume < 0:
            raise ValueError("Invalid daily volume")


@dataclass(frozen=True)
class Security:
    symbol: str
    kind: Literal["STOCK", "ETF"]
    sector: str
    market_cap: float
    asof: date
    tradable: bool = True


@dataclass(frozen=True)
class Quote:
    symbol: str
    at: datetime
    bid: float
    ask: float

    def __post_init__(self):
        aware(self.at)
        positive(self.bid, self.ask)
        if self.bid > self.ask:
            raise ValueError("Crossed quote")


@dataclass(frozen=True)
class Signal:
    strategy: StrategyId
    symbol: str
    at: datetime
    target_side: Literal[-1, 0, 1]
    reason: str
    stop_distance: float | None = None
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        aware(self.at)
        if self.strategy not in ("orb", "noise_area") or self.target_side not in (-1, 0, 1):
            raise ValueError("Invalid strategy or direction")
        if self.stop_distance is not None:
            positive(self.stop_distance)
