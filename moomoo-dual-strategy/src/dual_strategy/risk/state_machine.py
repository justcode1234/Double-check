from dataclasses import dataclass, replace
from datetime import date, datetime
from enum import Enum
from math import isfinite
from typing import Any

from dual_strategy.models import StrategyId, aware


class RuntimeState(str, Enum):
    BLOCKED_SETUP = "BLOCKED_SETUP"
    SHADOW = "SHADOW"
    PAPER_READY = "PAPER_READY"
    PAPER_ACTIVE = "PAPER_ACTIVE"
    RISK_HALTED = "RISK_HALTED"
    TERMINATED = "TERMINATED"


REQUIRED_RELEASE_GATES = frozenset({
    "paper_account_verified", "independent_50k_ledgers", "hand_calculated_signals",
    "historical_backtests_with_costs", "supported_order_types", "practice_order_lifecycle",
    "partial_timeout_duplicate_stale_restart_tests", "daily_drawdown_terminal_tests",
    "schedule_sentinel_early_close_tests", "dashboard_reconciliation",
})


@dataclass(frozen=True)
class GateEvidence:
    gate: str
    passed: bool
    verified_at: datetime
    config_hash: str
    evidence_ref: str


@dataclass(frozen=True)
class ReleaseChecklist:
    strategy: StrategyId
    config_hash: str
    evidence: tuple[GateEvidence, ...] = ()
    shorts_enabled: bool = False

    def failures(self, now: datetime, config_hash: str, strategy: StrategyId) -> tuple[str, ...]:
        aware(now)
        failures = []
        if strategy not in ("orb", "noise_area") or self.strategy != strategy:
            failures.append("release_strategy_mismatch")
        if not config_hash or self.config_hash != config_hash:
            failures.append("release_config_mismatch")
        required = set(REQUIRED_RELEASE_GATES)
        if strategy == "orb":
            required.add("orb_adverse_stop_tests")
        if self.shorts_enabled:
            required.add("practice_short_and_cover_lifecycle")
        if type(self.shorts_enabled) is not bool:
            failures.append("invalid_short_release")
        for gate in sorted(required):
            records = [item for item in self.evidence if item.gate == gate]
            if len(records) != 1:
                failures.append("missing_or_duplicate_gate:" + gate)
                continue
            record = records[0]
            try:
                valid_time = aware(record.verified_at) <= now
            except (TypeError, ValueError, AttributeError):
                valid_time = False
            if (record.passed is not True or not valid_time or not record.evidence_ref.strip()
                    or record.config_hash != config_hash):
                failures.append("unverified_gate:" + gate)
        return tuple(failures)

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy, "config_hash": self.config_hash,
            "shorts_enabled": self.shorts_enabled,
            "evidence": [dict(gate=e.gate, passed=e.passed, verified_at=e.verified_at.isoformat(),
                              config_hash=e.config_hash, evidence_ref=e.evidence_ref) for e in self.evidence],
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ReleaseChecklist":
        return cls(value["strategy"], value["config_hash"], tuple(
            GateEvidence(e["gate"], e["passed"], datetime.fromisoformat(e["verified_at"]),
                         e["config_hash"], e["evidence_ref"]) for e in value["evidence"]
        ), value["shorts_enabled"])


@dataclass(frozen=True)
class RearmApproval:
    strategy: StrategyId
    session: date
    approved_at: datetime
    config_hash: str
    authorization_ref: str


@dataclass(frozen=True)
class RiskState:
    strategy: StrategyId
    runtime: RuntimeState = RuntimeState.BLOCKED_SETUP
    nav: float = 50_000.0
    day_start_nav: float = 50_000.0
    high_water_nav: float = 50_000.0
    session: date | None = None
    daily_locked: bool = False
    reduced: bool = False
    halted_on: date | None = None
    reason: str = "setup_unverified"

    def __post_init__(self):
        if self.strategy not in ("orb", "noise_area") or not isinstance(self.runtime, RuntimeState):
            raise ValueError("Invalid strategy or runtime")
        if any(isinstance(v, bool) or not isfinite(v) for v in
               (self.nav, self.day_start_nav, self.high_water_nav)):
            raise ValueError("NAV values must be finite")
        if self.day_start_nav <= 0 or self.high_water_nav <= 0:
            raise ValueError("NAV baselines must be positive")
        if self.high_water_nav < max(self.nav, self.day_start_nav):
            raise ValueError("High water cannot be below current or opening NAV")
        if type(self.daily_locked) is not bool or type(self.reduced) is not bool:
            raise ValueError("Invalid risk flags")
        if self.session is not None and type(self.session) is not date:
            raise ValueError("Session must be an exchange date")
        if self.halted_on is not None and (type(self.halted_on) is not date
                or self.session is None or self.halted_on > self.session):
            raise ValueError("Invalid halt session")
        if self.runtime in (RuntimeState.RISK_HALTED, RuntimeState.TERMINATED) and self.halted_on is None:
            raise ValueError("Halted state requires persistent halt session")

    @property
    def daily_loss(self) -> float:
        return max(0.0, (self.day_start_nav - self.nav) / self.day_start_nav)

    @property
    def drawdown(self) -> float:
        return max(0.0, (self.high_water_nav - self.nav) / self.high_water_nav)

    @property
    def exposure_factor(self) -> float:
        return 0.5 if self.reduced or self.nav <= self.high_water_nav * 0.98 else 1.0

    @property
    def entries_allowed(self) -> bool:
        return (self.runtime == RuntimeState.PAPER_ACTIVE and not self.daily_locked
                and self.nav > self.day_start_nav * 0.9875
                and self.nav > self.high_water_nav * 0.965)

    def mark(self, nav: float, session: date) -> "RiskState":
        if isinstance(nav, bool) or not isfinite(nav) or type(session) is not date:
            raise ValueError("Expected finite NAV and exchange session date")
        if self.session is not None and session < self.session:
            raise ValueError("Cannot mark an earlier session")
        opening = self.day_start_nav if session == self.session else nav
        if opening <= 0:
            opening = self.day_start_nav
        high = max(self.high_water_nav, nav)
        daily_breach = nav <= opening * 0.9875
        locked = self.daily_locked or daily_breach
        reduced = nav <= high * (0.985 if self.reduced else 0.98)
        runtime, reason, halted = self.runtime, self.reason, self.halted_on
        if runtime == RuntimeState.TERMINATED or nav <= high * 0.95:
            runtime, reason = RuntimeState.TERMINATED, "terminal_drawdown"
            halted = halted or session
        elif nav <= high * 0.965:
            runtime, reason = RuntimeState.RISK_HALTED, "drawdown_halt"
            halted = session
        elif locked:
            runtime = RuntimeState.RISK_HALTED
            reason = "daily_loss_lock" if daily_breach else self.reason
            # WHY: a fresh breach while already halted must lock this session, not just the first halt day.
            halted = session if daily_breach else (halted or session)
        return replace(self, nav=nav, day_start_nav=opening, high_water_nav=high, session=session,
                       daily_locked=locked, reduced=reduced, runtime=runtime, reason=reason, halted_on=halted)

    def transition(self, target: RuntimeState, *, now: datetime,
                   checklist: ReleaseChecklist | None = None, config_hash: str = "") -> "RiskState":
        aware(now)
        if self.runtime == RuntimeState.TERMINATED:
            raise ValueError("Terminal state cannot be rearmed")
        if self.runtime == RuntimeState.RISK_HALTED or self.daily_locked:
            raise ValueError("Explicit next-session rearm approval required")
        allowed = {
            RuntimeState.BLOCKED_SETUP: {RuntimeState.SHADOW, RuntimeState.PAPER_READY},
            RuntimeState.SHADOW: {RuntimeState.BLOCKED_SETUP, RuntimeState.PAPER_READY},
            RuntimeState.PAPER_READY: {RuntimeState.SHADOW, RuntimeState.PAPER_ACTIVE, RuntimeState.BLOCKED_SETUP},
            RuntimeState.PAPER_ACTIVE: {RuntimeState.SHADOW, RuntimeState.PAPER_READY, RuntimeState.BLOCKED_SETUP},
        }
        if target not in allowed.get(self.runtime, set()):
            raise ValueError("Invalid runtime transition")
        if target in (RuntimeState.PAPER_READY, RuntimeState.PAPER_ACTIVE):
            if (not isinstance(checklist, ReleaseChecklist)
                    or checklist.failures(now, config_hash, self.strategy)):
                raise ValueError("Required release evidence is incomplete")
            if self.nav <= self.high_water_nav * 0.965 or self.nav <= self.day_start_nav * 0.9875:
                raise ValueError("Risk threshold breached")
            if self.session is None:
                raise ValueError("Session baseline required")
        return replace(self, runtime=target, reason="release_verified" if target in
                       (RuntimeState.PAPER_READY, RuntimeState.PAPER_ACTIVE) else target.value.lower())

    def rearm(self, approval: RearmApproval, *, now: datetime, checklist: ReleaseChecklist,
              config_hash: str) -> "RiskState":
        aware(now)
        if self.runtime != RuntimeState.RISK_HALTED or self.halted_on is None:
            raise ValueError("Only a nonterminal risk halt may rearm")
        if not isinstance(approval, RearmApproval):
            raise ValueError("Explicit approval record required")
        from zoneinfo import ZoneInfo
        if (approval.strategy != self.strategy or approval.session != self.session
                or self.session <= self.halted_on or approval.config_hash != config_hash
                or not approval.authorization_ref.strip() or aware(approval.approved_at) > now
                or approval.approved_at.astimezone(ZoneInfo("America/New_York")).date() != self.session
                or now.astimezone(ZoneInfo("America/New_York")).date() != self.session):
            raise ValueError("Approval must be for this strategy, config and next session")
        # WHY: recovery below 3% retains the original peak and avoids immediately retriggering 3.5%.
        if self.nav <= self.high_water_nav * 0.97 or self.nav <= self.day_start_nav * 0.99:
            raise ValueError("Recovery hysteresis not satisfied")
        if not isinstance(checklist, ReleaseChecklist) or checklist.failures(now, config_hash, self.strategy):
            raise ValueError("Required release evidence is incomplete")
        return replace(self, runtime=RuntimeState.PAPER_READY, daily_locked=False,
                       halted_on=None, reason="explicit_next_session_rearm")

    def to_dict(self) -> dict[str, Any]:
        return dict(strategy=self.strategy, runtime=self.runtime.value, nav=self.nav,
                    day_start_nav=self.day_start_nav, high_water_nav=self.high_water_nav,
                    session=self.session.isoformat() if self.session else None,
                    daily_locked=self.daily_locked, reduced=self.reduced,
                    halted_on=self.halted_on.isoformat() if self.halted_on else None, reason=self.reason)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "RiskState":
        return cls(strategy=value["strategy"], runtime=RuntimeState(value["runtime"]), nav=value["nav"],
                   day_start_nav=value["day_start_nav"], high_water_nav=value["high_water_nav"],
                   session=date.fromisoformat(value["session"]) if value["session"] else None,
                   daily_locked=value["daily_locked"], reduced=value["reduced"],
                   halted_on=date.fromisoformat(value["halted_on"]) if value["halted_on"] else None,
                   reason=value["reason"])
