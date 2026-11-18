"""Fail-closed, closed-bar validation. A bar ending 09:31 covers 09:30–09:31."""

from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta

from dual_strategy.data.calendar import NY, prior_sessions, session_bounds, session_day
from dual_strategy.models import Bar, DailyBar, aware


class DataValidationError(ValueError):
    """Missing, inconsistent, or not-yet-available market data."""


def validate_rth_bars(
    bars: Sequence[Bar], *, symbol: str, day: date, asof: datetime,
    through: datetime | None = None, minutes: int = 1,
) -> tuple[Bar, ...]:
    """Require a complete RTH prefix through a closed timestamp (default: close).

    Extra bars are rejected, not silently clipped. Pass only data known as of the
    decision; historical indicator inputs must include the whole prior session.
    """
    aware(asof)
    bounds = session_bounds(day)
    if bounds is None:
        raise DataValidationError("Not an NYSE session")
    opened, closed = bounds
    through = aware(through) if through is not None else closed
    if isinstance(minutes, bool) or not isinstance(minutes, int) or minutes < 1:
        raise DataValidationError("Interval must be positive integer minutes")
    step = timedelta(minutes=minutes)
    if not opened < through <= closed or (through - opened) % step:
        raise DataValidationError("Through time is not a closed RTH interval")
    if through > asof:
        raise DataValidationError("Requested bars are in the future")
    ordered = tuple(sorted(bars, key=lambda b: b.end))
    expected = {opened + i * step for i in range(1, int((through - opened) / step) + 1)}
    seen: set[datetime] = set()
    for bar in ordered:
        if bar.symbol != symbol or type(bar.minutes) is not int or bar.minutes != minutes:
            raise DataValidationError("Wrong symbol or bar interval")
        if bar.end > asof:
            raise DataValidationError("Future bar")
        if bar.end in seen:
            raise DataValidationError("Duplicate bar")
        if bar.end not in expected or bar.start < opened:
            raise DataValidationError("Bar outside requested closed RTH intervals")
        seen.add(bar.end)
    if seen != expected:
        raise DataValidationError(f"Missing {len(expected - seen)} RTH bars")
    return ordered


def validate_daily_history(
    bars: Sequence[DailyBar], *, symbol: str, day: date, count: int,
) -> tuple[DailyBar, ...]:
    """Select exactly count consecutive prior completed sessions, rejecting lookahead."""
    day = session_day(day)
    expected = prior_sessions(day, count)
    by_day: dict[date, DailyBar] = {}
    for bar in bars:
        if bar.symbol != symbol:
            raise DataValidationError("Wrong daily symbol")
        if isinstance(bar.session, datetime) or bar.session >= day:
            raise DataValidationError("Daily history includes uncompleted/future session")
        if session_bounds(bar.session) is None:
            raise DataValidationError("Daily bar on a non-session")
        if bar.session in by_day:
            raise DataValidationError("Duplicate daily bar")
        by_day[bar.session] = bar
    if any(d not in by_day for d in expected):
        raise DataValidationError(f"Need {count} consecutive prior daily sessions")
    return tuple(by_day[d] for d in expected)


def validate_session_history(
    history: Mapping[date, Sequence[Bar]], *, symbol: str, day: date,
    asof: datetime, count: int = 14, minutes: int = 1,
) -> dict[date, tuple[Bar, ...]]:
    """Require the immediately previous sessions in full, including half-days."""
    aware(asof)
    day = session_day(day)
    if asof.astimezone(NY).date() != day:
        raise DataValidationError("History asof must belong to the decision session")
    for d in history:
        if isinstance(d, datetime) or not isinstance(d, date) or d >= day:
            raise DataValidationError("History contains a current/future session")
        if session_bounds(d) is None:
            raise DataValidationError("History contains a non-session")
    expected = prior_sessions(day, count)
    if any(d not in history for d in expected):
        raise DataValidationError(f"Need {count} consecutive prior intraday sessions")
    return {
        d: validate_rth_bars(history[d], symbol=symbol, day=d, asof=asof, minutes=minutes)
        for d in expected
    }
