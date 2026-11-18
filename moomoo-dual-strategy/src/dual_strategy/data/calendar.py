"""NYSE sessions; all public datetimes are aware America/New_York times."""

from datetime import date, datetime, time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

import exchange_calendars

from dual_strategy.models import aware

NY = ZoneInfo("America/New_York")


@lru_cache(maxsize=1)
def _calendar():
    # Do not extend coverage or substitute weekday rules when data is unavailable.
    return exchange_calendars.get_calendar("XNYS")


def session_day(day: date | datetime) -> date:
    if isinstance(day, datetime):
        return aware(day).astimezone(NY).date()
    if not isinstance(day, date):
        raise TypeError("Expected a date or timezone-aware datetime")
    return day


def _covered(day: date | datetime) -> date:
    value = session_day(day)
    cal = _calendar()
    if not cal.first_session.date() <= value <= cal.last_session.date():
        raise ValueError(f"Date {value} outside XNYS calendar coverage")
    return value


def session_bounds(day: date | datetime) -> tuple[datetime, datetime] | None:
    """Return regular-session open/close, or None for a covered non-session."""
    value = _covered(day)
    cal = _calendar()
    label = value.isoformat()
    if not cal.is_session(label):
        return None
    return (
        cal.session_open(label).to_pydatetime().astimezone(NY),
        cal.session_close(label).to_pydatetime().astimezone(NY),
    )


def decision_times(day: date | datetime) -> list[datetime]:
    """Closed half-hour ticks, 10:00 through 15:30, strictly before close."""
    bounds = session_bounds(day)
    if bounds is None:
        return []
    opened, closed = bounds
    tick = datetime.combine(opened.date(), time(10), NY)
    last = datetime.combine(opened.date(), time(15, 30), NY)
    result = []
    while tick < closed and tick <= last:
        if tick > opened:
            result.append(tick)
        tick += timedelta(minutes=30)
    return result


def flatten_time(day: date | datetime) -> datetime | None:
    bounds = session_bounds(day)
    return bounds[1] - timedelta(minutes=5) if bounds else None


def prior_sessions(day: date | datetime, n: int) -> list[date]:
    """The n immediately preceding sessions, oldest first; never include day."""
    value = _covered(day)
    if isinstance(n, bool) or not isinstance(n, int) or n < 0:
        raise ValueError("Session count must be a nonnegative integer")
    sessions = _calendar().sessions
    end = int(sessions.searchsorted(value.isoformat(), side="left"))
    if end < n:
        raise ValueError("Insufficient prior sessions within XNYS calendar coverage")
    return [stamp.date() for stamp in sessions[end - n:end]]
