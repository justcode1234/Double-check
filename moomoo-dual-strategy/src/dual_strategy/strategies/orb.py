"""Five-minute stock ORB baseline, with a clearly labeled long-only default.

Signals observe the latest CLOSED bar's high/low: this is delayed bar-observed
breakout execution, not the paper's resting-stop fill. Execution must anchor the
returned 0.10 * daily ATR distance to the actual fill; no target/VWAP/trailing
exit is imposed here. Position/sector limits and repeated-entry control belong
to execution/risk, not this deterministic signal calculation.
"""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from math import fsum, isfinite

from dual_strategy.data.calendar import NY, flatten_time, session_bounds
from dual_strategy.data.validation import (
    DataValidationError, validate_daily_history, validate_rth_bars, validate_session_history,
)
from dual_strategy.models import Bar, DailyBar, Security, Signal, aware

LOOKBACK = 14
MAX_NAMES = 8


@dataclass(frozen=True)
class OpeningRange:
    open: float
    high: float
    low: float
    close: float
    volume: float

    @property
    def direction(self) -> int:
        return (self.close > self.open) - (self.close < self.open)


@dataclass(frozen=True)
class ORBCandidate:
    symbol: str
    day: date
    asof: datetime
    sector: str
    opening: OpeningRange
    atr: float
    adv: float
    rvol: float
    eligible: bool
    reason: str
    metadata_asof: date


def atr14(bars: Sequence[DailyBar], *, symbol: str, day: date) -> float:
    """Arithmetic mean of 14 true ranges, requiring 15 prior daily closes."""
    history = validate_daily_history(bars, symbol=symbol, day=day, count=LOOKBACK + 1)
    return fsum(
        max(bar.high - bar.low, abs(bar.high - previous.close), abs(bar.low - previous.close))
        for previous, bar in zip(history, history[1:])
    ) / LOOKBACK


def adv14(bars: Sequence[DailyBar], *, symbol: str, day: date) -> float:
    history = validate_daily_history(bars, symbol=symbol, day=day, count=LOOKBACK)
    return fsum(bar.volume for bar in history) / LOOKBACK


def opening_range(
    bars: Sequence[Bar], *, symbol: str, day: date, asof: datetime, minutes: int = 1,
) -> OpeningRange:
    """Accept exactly the first five minutes (five one-minute bars preferred)."""
    bounds = session_bounds(day)
    if bounds is None:
        raise DataValidationError("No opening range on a non-session")
    if minutes not in (1, 5):
        raise DataValidationError("ORB requires one-minute or five-minute bars")
    opening = validate_rth_bars(
        bars, symbol=symbol, day=day, asof=asof,
        through=bounds[0] + timedelta(minutes=5), minutes=minutes,
    )
    return OpeningRange(
        opening[0].open, max(b.high for b in opening), min(b.low for b in opening),
        opening[-1].close, fsum(b.volume for b in opening),
    )


def opening_rvol(
    current: OpeningRange, history: Mapping[date, Sequence[Bar]], *,
    symbol: str, day: date, asof: datetime, minutes: int = 1,
) -> float:
    """Current opening volume / MEAN opening volume of prior 14 complete sessions."""
    sessions = validate_session_history(history, symbol=symbol, day=day, asof=asof, minutes=minutes)
    volumes = []
    for d, bars in sessions.items():
        end = session_bounds(d)[0] + timedelta(minutes=5)
        opening = opening_range([b for b in bars if b.end <= end], symbol=symbol, day=d, asof=asof, minutes=minutes)
        volumes.append(opening.volume)
    mean_volume = fsum(volumes) / LOOKBACK
    if mean_volume <= 0:
        raise DataValidationError("Zero mean prior opening volume")
    return current.volume / mean_volume


def prepare_candidate(
    security: Security, today_bars: Sequence[Bar], daily_history: Sequence[DailyBar],
    opening_history: Mapping[date, Sequence[Bar]], *, at: datetime, minutes: int = 1,
) -> ORBCandidate:
    """Compute point-in-time eligibility and RVOL before cross-sectional ranking.

    Security has date-only metadata: require asof BEFORE today, conservatively,
    because a same-day record cannot prove availability by the opening scan.
    """
    at = aware(at).astimezone(NY)
    day = at.date()
    if isinstance(security.asof, datetime) or security.asof >= day:
        raise DataValidationError("Security metadata must be known before today's session")
    if not isfinite(security.market_cap) or security.market_cap < 0:
        raise DataValidationError("Invalid point-in-time market capitalization")
    bounds = session_bounds(day)
    if bounds is None or at < bounds[0] + timedelta(minutes=5):
        raise DataValidationError("Opening range not closed")
    bars = validate_rth_bars(today_bars, symbol=security.symbol, day=day, asof=at, through=at, minutes=minutes)
    end = bounds[0] + timedelta(minutes=5)
    opening = opening_range([b for b in bars if b.end <= end], symbol=security.symbol, day=day, asof=at, minutes=minutes)
    atr = atr14(daily_history, symbol=security.symbol, day=day)
    adv = adv14(daily_history, symbol=security.symbol, day=day)
    rvol = opening_rvol(opening, opening_history, symbol=security.symbol, day=day, asof=at, minutes=minutes)
    reasons = []
    if security.kind != "STOCK":
        reasons.append("individual_stocks_only")
    if not security.tradable:
        reasons.append("not_tradable")
    if opening.open <= 5:
        reasons.append("opening_price_not_above_5")
    if adv < 1_000_000:
        reasons.append("adv14_below_1m")
    if atr <= 0.50:
        reasons.append("atr14_not_above_0.50")
    if security.market_cap < 1_000_000_000:
        reasons.append("market_cap_below_1b")
    if rvol < 1:
        reasons.append("rvol_below_1")
    return ORBCandidate(
        security.symbol, day, at, security.sector, opening, atr, adv, rvol,
        not reasons, ";".join(reasons) or "eligible", security.asof,
    )


def rank_candidates(candidates: Sequence[ORBCandidate]) -> list[ORBCandidate]:
    """Baseline top eight by descending RVOL; symbol breaks ties deterministically.

    Rank before long-only adaptation: a red/doji opening can occupy a research
    selection slot but will never produce a prohibited short or doji entry.
    """
    if len({c.symbol for c in candidates}) != len(candidates):
        raise DataValidationError("Duplicate candidate symbol")
    if len({c.day for c in candidates}) > 1:
        raise DataValidationError("Cannot rank different sessions")
    if any(not isfinite(c.rvol) or c.rvol < 0 for c in candidates):
        raise DataValidationError("Invalid ranking RVOL")
    return sorted((c for c in candidates if c.eligible), key=lambda c: (-c.rvol, c.symbol))[:MAX_NAMES]


def orb_signal(
    security: Security, today_bars: Sequence[Bar], daily_history: Sequence[DailyBar],
    opening_history: Mapping[date, Sequence[Bar]], *, at: datetime,
    selected_symbols: Collection[str], shorts_enabled: bool = False,
    short_verified: bool = False, minutes: int = 1,
) -> Signal:
    """Return a loggable trade/no-trade signal; invalid input data never creates exposure.

    selected_symbols must come from rank_candidates over the point-in-time
    universe. Opting into shorts does not replace separate per-symbol verification.
    Naive decision timestamps raise rather than inventing a timezone.
    """
    at = aware(at).astimezone(NY)
    metadata = {
        "variant": "short_enabled" if shorts_enabled else "long_only",
        "actionable": False, "bar_timestamp": "closed_end",
        "entry_interpretation": "closed_bar_high_low_breakout; delayed_not_resting_stop",
        "selection_limit": MAX_NAMES, "published_selection_limit": 20,
        "stop_anchor": "actual_executed_entry", "take_profit": None,
    }

    def no_trade(reason: str) -> Signal:
        return Signal("orb", security.symbol, at, 0, reason, metadata=metadata)

    try:
        bounds = session_bounds(at.date())
        if bounds is None:
            return no_trade("non_session")
        if at >= flatten_time(at.date()):
            return no_trade("flatten_window_no_entry")
        if at <= bounds[0] + timedelta(minutes=5):
            return no_trade("wait_until_after_09:35")
        if isinstance(selected_symbols, (str, bytes)) or len(selected_symbols) > MAX_NAMES or len(set(selected_symbols)) != len(selected_symbols):
            raise DataValidationError("Selection must contain at most eight unique symbols")
        candidate = prepare_candidate(security, today_bars, daily_history, opening_history, at=at, minutes=minutes)
        metadata.update({
            "atr14": candidate.atr, "adv14": candidate.adv, "rvol": candidate.rvol,
            "range_high": candidate.opening.high, "range_low": candidate.opening.low,
            "metadata_asof": candidate.metadata_asof.isoformat(), "sector": candidate.sector,
        })
        if not candidate.eligible:
            return no_trade(candidate.reason)
        if security.symbol not in selected_symbols:
            return no_trade("not_top8_selected")
        direction = candidate.opening.direction
        if direction == 0:
            return no_trade("opening_doji")
        if direction == -1 and not (shorts_enabled and short_verified):
            return no_trade("short_disabled_or_unverified")
        last = max(today_bars, key=lambda bar: bar.end)
        if last.start < bounds[0] + timedelta(minutes=5):
            return no_trade("breakout_bar_overlaps_opening_range")
        triggered = last.high > candidate.opening.high if direction == 1 else last.low < candidate.opening.low
        if not triggered:
            return no_trade("no_breakout")
        metadata.update({"actionable": True, "reference_close": last.close})
        return Signal("orb", security.symbol, at, direction, "opening_range_breakout", 0.10 * candidate.atr, metadata)
    except (ValueError, OverflowError) as exc:
        return no_trade(f"invalid_data: {exc}")
