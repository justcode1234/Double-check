"""Independent $50,000 simulated portfolios: sequential sessions, start-of-day equity sizing, no cross-subsidy."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from typing import Callable

import pandas as pd

from dual_strategy.research.book import DayBook

START_EQUITY = 50_000.0


@dataclass
class Result:
    strategy: str
    variant: str
    label: str
    config: dict
    trades: list[dict]
    daily: pd.DataFrame


def simulate(strategy: str, cfg, day_fn: Callable, data, days: list[date], index: dict[date, int],
             tradable: Callable[[int], bool] | None = None, start: float = START_EQUITY) -> Result:
    equity = start
    rows, trades = [], []
    for d in days:
        t = index[d]
        book = DayBook(strategy, cfg.name, d, equity, cfg.costs, cfg.exe, 1 if strategy in ("B", "C") else 5)
        if tradable is not None and not tradable(t):
            summary = book.summary([0.0], 0.0)
            summary["data_gap"] = 1
        else:
            summary = day_fn(data, t, equity, cfg, book)
            summary["data_gap"] = 0
        trades.extend(book.trades)
        summary["equity_sod"] = equity
        summary["gross_pnl"] = sum(tr["gross_pnl"] for tr in book.trades)
        equity += book.realized
        summary["equity_eod"] = equity
        summary["ret"] = book.realized / summary["equity_sod"]
        summary["worst_intraday_pct"] = summary.pop("worst_intraday") / summary["equity_sod"]
        rows.append(summary)
    daily = pd.DataFrame(rows).set_index("date")
    return Result(strategy, cfg.name, cfg.label, asdict(cfg), trades, daily)
