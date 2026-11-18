"""SPY target-position Noise Area and a separately named trailing-stop ablation.

Baseline: exactly 14 prior COMPLETE sessions, VM=1, gap-adjusted bands,
closed half-hour decisions, and explicit genuine-turnover or HLC3-proxy VWAP.
No partial-session averaging or skipping half-days to fill a missing slot.
"""

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date, datetime
from math import fsum, isfinite
from typing import Literal

from dual_strategy.data.calendar import NY, decision_times
from dual_strategy.data.validation import DataValidationError, validate_rth_bars, validate_session_history
from dual_strategy.models import Bar, Signal, aware

VWAPMethod = Literal["turnover", "hlc3"]


@dataclass(frozen=True)
class NoiseAreaMetrics:
    sigma: float
    upper: float
    lower: float
    vwap: float
    price: float
    today_open: float
    previous_close: float
    vwap_method: VWAPMethod


def _sigma(sessions: Mapping[date, Sequence[Bar]], at: datetime) -> float:
    returns = []
    for day, bars in sessions.items():
        slot = datetime.combine(day, at.time(), NY)
        matches = [bar for bar in bars if bar.end == slot]
        if len(matches) != 1:
            raise DataValidationError(f"Prior session {day} has no closed bar at {at:%H:%M}; do not skip it")
        returns.append(abs(matches[0].close / bars[0].open - 1))
    if len(returns) != 14:
        raise DataValidationError("sigma_slot requires exactly 14 prior complete sessions")
    return fsum(returns) / 14


def sigma_slot(
    history: Mapping[date, Sequence[Bar]], *, at: datetime,
    symbol: str = "SPY", minutes: int = 1,
) -> float:
    """Mean absolute open-to-slot return, not daily volatility or a rolling EMA."""
    at = aware(at).astimezone(NY)
    if at not in decision_times(at.date()):
        raise DataValidationError("Not a closed scheduled decision tick")
    sessions = validate_session_history(history, symbol=symbol, day=at.date(), asof=at, minutes=minutes)
    return _sigma(sessions, at)


def cumulative_vwap(bars: Sequence[Bar], *, method: VWAPMethod = "turnover") -> float:
    """Whole-prefix RTH VWAP; never silently replace missing turnover with HLC3."""
    if not bars:
        raise DataValidationError("VWAP needs bars")
    ordered = sorted(bars, key=lambda b: b.end)
    last = ordered[-1]
    ordered = validate_rth_bars(
        ordered, symbol=last.symbol, day=last.end.astimezone(NY).date(),
        asof=last.end, through=last.end, minutes=last.minutes,
    )
    volume = fsum(bar.volume for bar in ordered)
    if volume <= 0:
        raise DataValidationError("VWAP has zero cumulative volume")
    if method == "turnover":
        if any(bar.turnover is None for bar in ordered):
            raise DataValidationError("Missing turnover; explicitly request hlc3 proxy if intended")
        if any((bar.volume == 0) != (bar.turnover == 0) for bar in ordered):
            raise DataValidationError("Inconsistent zero volume/turnover")
        weighted = fsum(bar.turnover for bar in ordered)
    elif method == "hlc3":
        weighted = fsum(((bar.high + bar.low + bar.close) / 3) * bar.volume for bar in ordered)
    else:
        raise DataValidationError("Unknown VWAP method")
    value = weighted / volume
    if not isfinite(value) or value <= 0:
        raise DataValidationError("Invalid VWAP")
    return value


def compute_noise_area(
    today_bars: Sequence[Bar], history: Mapping[date, Sequence[Bar]], *,
    at: datetime, symbol: str = "SPY", vwap_method: VWAPMethod = "turnover", minutes: int = 1,
    cash_dividend: float = 0,
) -> NoiseAreaMetrics:
    """Compute the baseline metrics or reject incomplete/lookahead data."""
    at = aware(at).astimezone(NY)
    if symbol not in ('SPY', 'US.SPY'):
        raise DataValidationError("Baseline Noise Area is SPY only")
    if not isfinite(cash_dividend) or cash_dividend < 0:
        raise DataValidationError('Cash dividend must be finite and nonnegative')
    if at not in decision_times(at.date()):
        raise DataValidationError("Not a closed scheduled decision tick")
    current = validate_rth_bars(today_bars, symbol=symbol, day=at.date(), asof=at, through=at, minutes=minutes)
    sessions = validate_session_history(history, symbol=symbol, day=at.date(), asof=at, minutes=minutes)
    sigma = _sigma(sessions, at)
    previous_close = sessions[max(sessions)][-1].close - cash_dividend
    if previous_close <= 0:
        raise DataValidationError('Dividend-adjusted previous close must be positive')
    today_open = current[0].open
    return NoiseAreaMetrics(
        sigma, max(today_open, previous_close) * (1 + sigma),
        min(today_open, previous_close) * (1 - sigma),
        cumulative_vwap(current, method=vwap_method), current[-1].close,
        today_open, previous_close, vwap_method,
    )


def _target(metrics: NoiseAreaMetrics, shorts_enabled: bool, short_verified: bool) -> tuple[int, str]:
    if metrics.price > metrics.upper and metrics.price > metrics.vwap:
        return 1, "above_upper_and_vwap"
    if metrics.price < metrics.lower and metrics.price < metrics.vwap:
        if shorts_enabled and short_verified:
            return -1, "below_lower_and_vwap"
        return 0, "short_disabled_or_unverified"
    return 0, "inside_noise_or_vwap_filter"


def noise_area_signal(
    today_bars: Sequence[Bar], history: Mapping[date, Sequence[Bar]], *,
    at: datetime, symbol: str = "SPY", shorts_enabled: bool = False,
    short_verified: bool = False, vwap_method: VWAPMethod = "turnover", minutes: int = 1,
    cash_dividend: float = 0,
) -> Signal:
    """Stateless target: long/verified-short/flat on scheduled closed ticks only.

    A valid flat target is actionable (reconcile/exit); an invalid or unscheduled
    no-trade is not actionable and must not be mistaken for a scheduled decision.
    Independent pre-close and emergency exits remain the execution layer's job.
    """
    at = aware(at).astimezone(NY)
    metadata = {
        "interpretation": "target_position", "variant": "short_enabled" if shorts_enabled else "long_only",
        "bar_timestamp": "closed_end", "lookback": 14, "vm": 1.0,
        "actionable": False, "vwap_method": vwap_method,
    }
    try:
        if at not in decision_times(at.date()):
            return Signal("noise_area", symbol, at, 0, "not_decision_tick", metadata=metadata)
        metrics = compute_noise_area(today_bars, history, at=at, symbol=symbol, vwap_method=vwap_method, minutes=minutes, cash_dividend=cash_dividend)
        metadata.update(asdict(metrics))
        metadata["actionable"] = True
        side, reason = _target(metrics, shorts_enabled, short_verified)
        return Signal("noise_area", symbol, at, side, reason, metadata=metadata)
    except (ValueError, OverflowError) as exc:
        return Signal("noise_area", symbol, at, 0, f"invalid_data: {exc}", metadata=metadata)


@dataclass(frozen=True)
class NoiseAreaState:
    side: Literal[-1, 0, 1] = 0
    trailing_stop: float | None = None
    last_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.side not in (-1, 0, 1):
            raise ValueError("Invalid ablation side")
        if self.last_at is not None:
            aware(self.last_at)
        if self.side:
            if self.last_at is None or self.trailing_stop is None or not isfinite(self.trailing_stop) or self.trailing_stop <= 0:
                raise ValueError("An open ablation state needs a timestamp and positive stop")
        elif self.trailing_stop is not None:
            raise ValueError("Flat ablation state cannot have a stop")


def stateful_trailing_signal(
    today_bars: Sequence[Bar], history: Mapping[date, Sequence[Bar]], *,
    at: datetime, state: NoiseAreaState, symbol: str = "SPY", shorts_enabled: bool = False,
    short_verified: bool = False, vwap_method: VWAPMethod = "turnover", minutes: int = 1,
    cash_dividend: float = 0,
) -> tuple[Signal, NoiseAreaState]:
    """Explicit research ablation, NEVER silently substituted for the baseline.

    Flat-state entries use baseline bands/VWAP. While long, ratchet the stop to
    max(previous_stop, upper, VWAP); while short, min(previous_stop, lower, VWAP).
    A closed-tick touch exits. No reversal on that exit tick. Evaluate only on
    scheduled ticks; this is not an intrabar emergency monitor. Callers must
    reconcile the state with fills and reset to flat after scheduled liquidation.
    """
    at = aware(at).astimezone(NY)
    metadata = {"interpretation": "stateful_ratcheting_band_vwap_ablation", "actionable": False}
    try:
        if state.last_at is not None:
            if at <= state.last_at:
                raise DataValidationError("Ablation requires strictly increasing decision times")
            if state.side and at.date() != state.last_at.astimezone(NY).date():
                raise DataValidationError("Open ablation state carried overnight; reconcile flatten first")
        if at not in decision_times(at.date()):
            return Signal("noise_area", symbol, at, 0, "not_decision_tick", metadata=metadata), state
        metrics = compute_noise_area(today_bars, history, at=at, symbol=symbol, vwap_method=vwap_method, minutes=minutes, cash_dividend=cash_dividend)
        metadata.update(asdict(metrics))
        if state.side == 1:
            stop = max(state.trailing_stop, metrics.upper, metrics.vwap)
            side, reason = (0, "trailing_exit") if metrics.price <= stop else (1, "stateful_hold")
        elif state.side == -1:
            stop = min(state.trailing_stop, metrics.lower, metrics.vwap)
            side, reason = (0, "trailing_exit") if metrics.price >= stop or not (shorts_enabled and short_verified) else (-1, "stateful_hold")
        else:
            side, reason = _target(metrics, shorts_enabled, short_verified)
            stop = max(metrics.upper, metrics.vwap) if side == 1 else min(metrics.lower, metrics.vwap)
        next_state = NoiseAreaState(side, stop if side else None, at)
        metadata.update({"actionable": True, "trailing_stop": next_state.trailing_stop, "previous_side": state.side})
        return Signal("noise_area", symbol, at, side, reason, metadata=metadata), next_state
    except (ValueError, OverflowError) as exc:
        return Signal("noise_area", symbol, at, 0, f"invalid_data: {exc}", metadata=metadata), state
