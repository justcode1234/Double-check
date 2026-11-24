"""Read-only market data for the competition runner via the authenticated Moomoo CLI (quote OAuth only).

No account is addressed here. Every function fails closed (raises MarketDataError) on a non-zero exit, non-JSON output
or a missing field, and the caller then blocks entries.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

import pandas as pd

from dual_strategy.competition.signals import Bar5
from dual_strategy.data.calendar import NY
from dual_strategy.execution.moomoo_cli import default_cli_path


class MarketDataError(RuntimeError):
    pass


class Market:
    def __init__(self, cli: str | None = None, runner=None, min_interval: float = 1.0):
        self.cli = cli or default_cli_path()
        self.runner = runner
        self.min_interval = min_interval
        self._last = 0.0

    def _run(self, args: list[str]):
        """One CLI call, paced, retrying only on the CLI's explicit RATE_LIMIT answer (never on other failures)."""
        argv = [self.cli, *args, "--format", "json"]
        for attempt in range(8):
            wait = self._last + self.min_interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            if self.runner:
                out = self.runner(argv)
            else:
                r = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                   timeout=90, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if r.returncode != 0:
                    text = (r.stderr or "") + (r.stdout or "")
                    if "RATE_LIMIT" in text and attempt < 7:
                        m = re.search(r"retry after (\d+)s", text)
                        time.sleep((int(m.group(1)) if m else 5) + 1)
                        continue
                    raise MarketDataError(f"CLI {args[0]} failed: {text[:200]}")
                out = r.stdout
            try:
                return json.loads(out)
            except ValueError as exc:
                raise MarketDataError(f"CLI {args[0]} returned non-JSON") from exc
        raise MarketDataError(f"CLI {args[0]} rate limited")

    # ------------------------------------------------------------------ quotes
    def quote(self, symbol: str) -> tuple[float, datetime]:
        data = self._run(["quote", symbol])
        row = data[0] if isinstance(data, list) else (data.get("data") or [data])[0]
        price = float(row["last_price"])
        if not price > 0:
            raise MarketDataError(f"no last price for {symbol}")
        return price, datetime.now(timezone.utc)

    def timestamped_quotes(self, symbols) -> dict[str, tuple[float, datetime]]:
        data = self._run(["quote", *symbols])
        rows = data.get("data", [data]) if isinstance(data, dict) else data
        quotes = {}
        for row in rows if isinstance(rows, list) else []:
            try:
                price = float(row["last_price"])
                at = datetime.fromtimestamp(int(row["data_time"]) / 1000, timezone.utc)
                symbol = row.get("code") or row.get("symbol")
            except (KeyError, TypeError, ValueError, OverflowError, OSError):
                continue
            if symbol in symbols and 0 < price < float("inf"):
                quotes[symbol] = (price, at)
        return quotes

    # ------------------------------------------------------------------ bars
    @staticmethod
    def _klines(data):
        if isinstance(data, dict):
            inner = data.get("data")
            rows = (inner or {}).get("kline_list") if isinstance(inner, dict) else None
            return rows if rows is not None else data.get("kline_list", [])
        return data

    def five_minute(self, symbol: str, start: date, end: date) -> list[Bar5]:
        """Sessions start..end INCLUSIVE (the CLI's --end is exclusive, and it returns at most 370 bars per call)."""
        out = []
        for row in self._klines(self._run(["history-kline", symbol, "--ktype", "6", "--autype", "0", "--start",
                                           start.isoformat(), "--end", (end + timedelta(days=1)).isoformat(), "--num", "370"])):
            end_dt = datetime.fromtimestamp(row["time_key"] / 1000, timezone.utc).astimezone(NY)
            if not start <= end_dt.date() <= end:
                continue
            out.append(Bar5(end_dt, float(row.get("open_price", row.get("open"))), float(row.get("high_price", row.get("high"))),
                            float(row.get("low_price", row.get("low"))), float(row.get("close_price", row.get("close"))),
                            float(row["volume"]), float(row["turnover"])))
        return out

    def five_minute_today(self, symbol: str, today: date) -> list[Bar5]:
        rows = self._klines(self._run(["kline", symbol, "--ktype", "6", "--autype", "0", "--num", "80"]))
        bars = []
        for row in rows:
            end_dt = datetime.fromtimestamp(row["time_key"] / 1000, timezone.utc).astimezone(NY)
            if end_dt.date() == today:
                bars.append(Bar5(end_dt, float(row.get("open_price", row.get("open"))), float(row.get("high_price", row.get("high"))),
                                 float(row.get("low_price", row.get("low"))), float(row.get("close_price", row.get("close"))),
                                 float(row["volume"]), float(row["turnover"])))
        return bars

    def b3_inputs(self, symbol: str, today: date, sessions_needed: int = 14, replay: bool = False):
        """Prior sessions' 5-minute bars (>= sessions_needed + spare for half-days) and today's bars."""
        prior = defaultdict(list)
        import exchange_calendars
        cal = exchange_calendars.get_calendar("XNYS")
        days = [x.date() for x in cal.sessions_in_range((today - timedelta(days=40)).isoformat(), today.isoformat())]
        days = [d for d in days if d < today][-(sessions_needed + 3):]          # spare sessions for half-days
        for k in range(0, len(days), 4):                                       # CLI caps 370 bars: 4 x 78 per call
            chunk = days[k:k + 4]
            for b in self.five_minute(symbol, chunk[0], chunk[-1]):
                if b.end.date() < today:
                    prior[b.end.date()].append(b)
        for d in prior:
            uniq = {b.end: b for b in prior[d]}
            prior[d] = [uniq[k] for k in sorted(uniq)]
        if len(prior) < sessions_needed:
            raise MarketDataError(f"only {len(prior)} prior sessions of 5-minute bars")
        today_bars = ([b for b in self.five_minute(symbol, today, today) if b.end.date() == today] if replay
                      else self.five_minute_today(symbol, today))
        return dict(prior), today_bars

    def daily(self, symbol: str, start: date, end: date, autype: int) -> pd.DataFrame:
        rows = self._klines(self._run(["history-kline", symbol, "--ktype", "2", "--autype", str(autype),
                                       "--start", start.isoformat(), "--end", (end + timedelta(days=1)).isoformat()]))
        if not rows:
            raise MarketDataError(f"no daily bars for {symbol}")
        f = pd.DataFrame(rows)
        if "date" in f:
            f.index = [datetime.strptime(str(int(x)), "%Y%m%d").date() for x in f["date"]]
        else:
            f.index = [datetime.fromtimestamp(t / 1000, timezone.utc).astimezone(NY).date() for t in f["time_key"]]
        f = f.rename(columns={"close_price": "close", "open_price": "open", "high_price": "high", "low_price": "low"})
        f = f[[start <= d <= end for d in f.index]]
        cols = ["open", "close", "volume"] + [c for c in ("high", "low") if c in f]
        return f[cols].astype(float).sort_index()

    def h_panel(self, symbols: list[str], asof: date, sessions: int = 160) -> dict[str, pd.DataFrame]:
        start = asof - timedelta(days=int(sessions * 1.6))
        out = {}
        for s in symbols:
            raw = self.daily(s, start, asof, 0)
            adj = self.daily(s, start, asof, 1)
            f = raw[["close", "volume"]].copy()
            f["adjusted"] = adj.close.reindex(f.index)
            out[s] = f.dropna()
        return out
