"""Historical option-chain interface. A D/E/F backtest may only run on timestamped historical quotes stored under
data-cache/options_history/<UNDERLYING>/*.csv.gz with REQUIRED_FIELDS. None exist: Moomoo's CLI returns
"symbol not found" for expired contracts and has no historical chain endpoint (probed 2026-11-19)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from dual_strategy.research.fetch import ROOT

OPTIONS_DIR = ROOT / "data-cache" / "options_history"
REQUIRED_FIELDS = ("quote_time_utc", "underlying", "expiry", "strike", "right", "bid", "ask", "volume",
                   "open_interest", "multiplier")
BLOCKER = "D/E/F BACKTEST BLOCKED - HISTORICAL OPTIONS CHAINS MISSING"


class OptionsDataBlocked(RuntimeError):
    pass


@dataclass(frozen=True)
class OptionQuote:
    quote_time: datetime
    underlying: str
    expiry: date
    strike: float
    right: str          # "C" or "P"
    bid: float
    ask: float
    volume: int
    open_interest: int
    multiplier: int = 100

    @property
    def mid(self) -> float:
        return 0.5 * (self.bid + self.ask)

    @property
    def spread_pct(self) -> float:
        return (self.ask - self.bid) / self.mid if self.mid > 0 else float("inf")


def load_chains(underlying: str, root: Path = OPTIONS_DIR) -> pd.DataFrame:
    files = sorted((root / underlying).glob("*.csv.gz")) if (root / underlying).exists() else []
    if not files:
        raise OptionsDataBlocked(f"{BLOCKER}: no files under {root / underlying}")
    df = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)
    missing = [c for c in REQUIRED_FIELDS if c not in df.columns]
    if missing:
        raise OptionsDataBlocked(f"{BLOCKER}: missing fields {missing}")
    df["quote_time_utc"] = pd.to_datetime(df["quote_time_utc"], utc=True)
    bad = (df["bid"] < 0) | (df["ask"] < df["bid"])
    return df.loc[~bad].sort_values("quote_time_utc")


def snapshot(chains: pd.DataFrame, decision_time_utc: pd.Timestamp, max_age_s: float = 60.0) -> pd.DataFrame:
    """Latest quote per contract strictly before the decision time and no older than max_age_s (no look-ahead,
    no stale quotes)."""
    past = chains[(chains["quote_time_utc"] < decision_time_utc) &
                  (chains["quote_time_utc"] >= decision_time_utc - pd.Timedelta(seconds=max_age_s))]
    return past.groupby(["expiry", "strike", "right"], as_index=False).last()
