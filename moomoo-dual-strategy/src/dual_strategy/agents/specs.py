"""Offline contracts/preview only. No dispatcher, API client or order authority."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Mapping

import yaml

from dual_strategy.data.calendar import NY, decision_times, session_bounds

ROOT = Path(__file__).resolve().parents[3]
ROLES_PATH = "src/dual_strategy/agents/roles.yaml"
PIN_PATHS = ("config/orb.yaml", "config/noise_area.yaml", "config/risk.yaml", ROLES_PATH)
START, END = date(2026, 11, 17), date(2026, 11, 28)
SESSIONS = [f"2026-11-{d}" for d in (17, 18, 19, 20, 23, 24, 25, 27)]
BOTH = ["orb", "noise_area"]
# Scope, deterministic/local authority, allowed outputs, maximum exposure effect.
ROLE_CONTRACTS = {
    "coordinator": (BOTH, "orchestration_only", ["dispatch_spec", "status_summary"], "none"),
    "researcher": (BOTH, "read_only", ["data_quality_report", "setup_explanation"], "none"),
    "signal_a": (["orb"], "dual_strategy.strategies.orb", ["signal_proposal"], "proposal_only"),
    "signal_b": (["noise_area"], "dual_strategy.strategies.noise_area", ["signal_proposal"], "proposal_only"),
    "risk_supervisor": (BOTH, "veto_only", ["veto", "risk_explanation"], "veto_only"),
    "watchdog": (BOTH, "close_only", ["block_entries", "cancel_close_request", "alert"], "close_only"),
    "auditor": (BOTH, "read_only", ["audit_report", "veto"], "veto_only"),
    "paper_executor": (BOTH, "disabled_local_boundary", ["blocked_setup"], "none"),
    "reporter": (BOTH, "read_only", ["daily_report", "comparison_export"], "none"),
}
# All timings are local, exchange-guarded preview semantics, not API fields.
SCHEDULE_CONTRACTS = {
    "research": (["researcher"], BOTH, {"kind": "open_offset", "minutes": -30}),
    "pipeline_a": (["signal_a", "risk_supervisor", "auditor", "paper_executor"], ["orb"], {"kind": "orb_closed_minutes"}),
    "pipeline_b": (["signal_b", "risk_supervisor", "auditor", "paper_executor"], ["noise_area"], {"kind": "spy_closed_half_hours"}),
    "risk_audit": (["risk_supervisor", "auditor"], BOTH, {"kind": "session_interval", "minutes": 5}),
    "watchdog": (["watchdog"], BOTH, {"kind": "session_interval", "minutes": 1}),
    "flatten": (["watchdog"], BOTH, {"kind": "close_offset", "minutes": -5}),
    "verify_close": (["auditor"], BOTH, {"kind": "close_offset", "minutes": -2}),
    "report": (["reporter"], BOTH, {"kind": "close_offset", "minutes": 10}),
}
GUARDS = {
    "exchange_calendar_required": True,
    "half_day_aware": True,
    "local_sentinel_required": True,
    "cloud_replaces_sentinel": False,
    "missing_data_policy": "block_entries_alert",
    "missed_run_policy": "skip_expired_block_entries_alert",
    "retry_policy": "reconcile_before_retry_same_key",
    "max_concurrent_runs_per_portfolio": 1,
    "automatic_attempts": 1,
    "run_expiration_seconds": 60,
    "max_data_age_seconds": {"quote": 5, "account": 15, "sentinel": 5, "bar": 90},
    "idempotency_fields": ["strategy_id", "session_date", "symbol", "signal_id", "workflow", "scheduled_for", "config_hash"],
    "ledger_requires_atomic_persistent_deduplication": True,
    "financial_decisions": "deterministic_local_only",
    "paper_activation": "blocked_pending_all_release_gates",
}


class SpecError(ValueError):
    """Malformed, unpinned or unsafe local contract."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise SpecError(message)


class _UniqueLoader(yaml.SafeLoader):
    pass


def _unique_mapping(loader, node):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        _require(isinstance(key, str) and key not in result, "duplicate/non-string YAML key")
        result[key] = loader.construct_object(value_node)
    return result


_UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)


def _load(path: Path) -> dict:
    value = yaml.load(path.read_text(encoding="utf-8"), Loader=_UniqueLoader)
    _require(isinstance(value, dict), "expected YAML object")
    return value


def config_digest(value: dict) -> str:
    """Stable semantic hash; includes pins, roles, policies and inactive timings."""
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _exact(value, expected, label: str) -> None:
    # JSON comparison distinguishes false from 0 and true from 1.
    _require(json.dumps(value, sort_keys=True) == json.dumps(expected, sort_keys=True), f"invalid {label}")


def validate_spec(spec: dict, roles: dict, root: Path = ROOT) -> str:
    """Validate own v1 schema and file-byte pins; success never authorizes a run."""
    _exact(set_as_list(spec), sorted([
        "schema", "revision", "status", "api_request_ready", "cloud_writes_enabled",
        "paper_execution_enabled", "timezone", "calendar", "window", "portfolios",
        "roles", "guards", "schedules", "pins",
    ]), "workflow fields")
    for key, expected in {
        "schema": "dual-strategy.local-cloud/v1", "revision": 1, "status": "NOT DEPLOYED",
        "api_request_ready": False, "cloud_writes_enabled": False,
        "paper_execution_enabled": False, "timezone": "America/New_York", "calendar": "XNYS",
        "roles": {"path": ROLES_PATH, "revision": 1}, "guards": GUARDS,
        "window": {"start": START.isoformat(), "end": END.isoformat(), "sessions": SESSIONS,
                   "closed": ["2026-11-26"], "early_closes": {"2026-11-27": "13:00"}},
    }.items():
        _exact(spec[key], expected, key)
    expected_portfolios = {
        "A": {"strategy_id": "orb", "state_namespace": "orb", "account_binding_ref": "private:orb_practice",
              "initial_virtual_nav": 50000, "state": "BLOCKED_SETUP", "instruments": "US_INDIVIDUAL_STOCKS_NO_ETFS"},
        "B": {"strategy_id": "noise_area", "state_namespace": "noise_area", "account_binding_ref": "private:noise_area_practice",
              "initial_virtual_nav": 50000, "state": "BLOCKED_SETUP", "instruments": "US.SPY"},
    }
    _exact(spec["portfolios"], expected_portfolios, "portfolio isolation")
    _exact(set_as_list(roles), ["revision", "roles", "schema", "status"], "role document fields")
    _exact(roles["schema"], "dual-strategy.local-roles/v1", "role schema")
    _exact(roles["revision"], 1, "role revision")
    _exact(roles["status"], "NOT DEPLOYED", "role deployment status")
    _exact(set_as_list(roles["roles"]), sorted(ROLE_CONTRACTS), "role roster")
    for name, (scope, engine, outputs, effect) in ROLE_CONTRACTS.items():
        role = roles["roles"][name]
        _require(isinstance(role, dict), f"invalid role {name}")
        instructions = role.get("instructions")
        _require(isinstance(instructions, str) and bool(instructions.strip()), f"missing instructions: {name}")
        _exact(role, {"version": 1, "enabled": False, "cloud_id": None, "tool_allowlist": [],
                      "scope": scope, "engine": engine, "outputs": outputs,
                      "exposure_effect": effect, "instructions": instructions}, f"role {name}")
    _exact(set_as_list(spec["schedules"]), sorted(SCHEDULE_CONTRACTS), "schedule roster")
    for name, (chain, scope, timing) in SCHEDULE_CONTRACTS.items():
        _exact(spec["schedules"][name], {"revision": 1, "enabled": False, "timezone": "America/New_York",
                                        "roles": chain, "scope": scope, "timing": timing}, f"schedule {name}")
    _exact(set_as_list(spec["pins"]), sorted(PIN_PATHS), "pin paths")
    for relative in PIN_PATHS:
        expected = spec["pins"][relative]
        _require(isinstance(expected, str) and re.fullmatch(r"[0-9a-f]{64}", expected) is not None,
                 f"invalid SHA-256 pin: {relative}")
        _require(sha256((root / relative).read_bytes()).hexdigest() == expected, f"config pin drift: {relative}")
    _require(_load(root / ROLES_PATH) == roles, "role snapshot differs from pinned file")
    risk = _load(root / "config/risk.yaml")
    _require(risk.get("paper_execution_enabled") is False, "paper transport must remain disabled")
    for kind, age in GUARDS["max_data_age_seconds"].items():
        _exact(risk.get(f"{kind}_max_age_seconds"), age, f"risk {kind} age")
    for name in ("orb", "noise_area"):
        strategy = _load(root / f"config/{name}.yaml")
        _require(strategy.get("mode") == "SHADOW" and strategy.get("shorts_enabled") is False,
                 f"{name} must remain shadow and long-only")
    _exact(_load(root / "config/orb.yaml").get("entry_deadline_et"), "10:30", "ORB deadline")
    actual_sessions = []
    for offset in range((END - START).days + 1):
        day = START + timedelta(days=offset)
        bounds = session_bounds(day)  # Unknown/out-of-coverage calendar raises; no weekday fallback.
        if bounds:
            actual_sessions.append(day.isoformat())
            expected_close = time(13) if day == date(2026, 11, 27) else time(16)
            _require(bounds[0].time() == time(9, 30) and bounds[1].time() == expected_close,
                     "exchange calendar disagrees with reviewed window")
    _exact(actual_sessions, SESSIONS, "exchange sessions")
    return config_digest({"workflow": spec, "roles": roles})


def set_as_list(value) -> list:
    _require(isinstance(value, dict), "expected mapping")
    return sorted(value)


def load_spec(root: Path = ROOT) -> tuple[dict, dict, str]:
    spec, roles = _load(root / "config/cloud_workflows.yaml"), _load(root / ROLES_PATH)
    return spec, roles, validate_spec(spec, roles, root)


def preview_day(day: date, spec: dict, roles: dict, root: Path = ROOT) -> dict[str, list[datetime]]:
    """Expand inactive timings for inspection only. Never enqueue or run anything."""
    validate_spec(spec, roles, root)
    _require(type(day) is date, "preview requires a session date")
    result = {name: [] for name in SCHEDULE_CONTRACTS}
    if not START <= day <= END:
        return result
    bounds = session_bounds(day)
    if bounds is None:
        return result
    opened, closed = bounds
    flatten = closed - timedelta(minutes=5)
    for name, (_, _, timing) in SCHEDULE_CONTRACTS.items():
        kind = timing["kind"]
        if kind in ("open_offset", "close_offset"):
            anchor = opened if kind == "open_offset" else closed
            result[name] = [anchor + timedelta(minutes=timing["minutes"])]
        elif kind == "spy_closed_half_hours":
            result[name] = [tick for tick in decision_times(day) if tick < flatten]
        else:
            start = opened + timedelta(minutes=5) if kind == "orb_closed_minutes" else opened
            stop = min(datetime.combine(day, time(10, 30), NY), flatten) if kind == "orb_closed_minutes" else closed
            step = 1 if kind == "orb_closed_minutes" else timing["minutes"]
            while start < stop:
                result[name].append(start)
                start += timedelta(minutes=step)
    return result


def _aware(stamp: datetime) -> datetime:
    _require(isinstance(stamp, datetime) and stamp.tzinfo is not None and stamp.utcoffset() is not None,
             "timezone-aware timestamp required")
    return stamp


@dataclass(frozen=True)
class SignalSnapshot:
    """Untrusted local proposal metadata, not approval or an order request."""

    portfolio: str
    workflow: str
    scheduled_for: datetime
    expires_at: datetime
    symbol: str
    asset_class: str
    signal_id: str
    config_hash: str
    data_asof: Mapping[str, datetime]


def validate_snapshot(snapshot: SignalSnapshot, now: datetime, spec: dict, roles: dict, root: Path = ROOT) -> str:
    """Check metadata/freshness; return deterministic key, NEVER permission to trade.

    No key reservation is performed. Durable atomic deduplication, exact account
    guard, financial risk checks and broker reconciliation remain release blockers.
    """
    digest = validate_spec(spec, roles, root)
    _aware(now)
    due, expires = _aware(snapshot.scheduled_for), _aware(snapshot.expires_at)
    _require(snapshot.config_hash == digest, "snapshot config hash mismatch")
    _require(snapshot.portfolio in ("A", "B"), "unknown portfolio")
    workflow = "pipeline_a" if snapshot.portfolio == "A" else "pipeline_b"
    _require(snapshot.workflow == workflow, "cross-portfolio workflow")
    day = due.astimezone(NY).date()
    _require(due in preview_day(day, spec, roles, root)[workflow], "unscheduled/closed signal tick")
    closed = session_bounds(day)[1]
    deadline = closed - timedelta(minutes=5)
    if snapshot.portfolio == "A":
        deadline = min(deadline, datetime.combine(day, time(10, 30), NY))
    _require(due < expires <= min(due + timedelta(seconds=GUARDS["run_expiration_seconds"]), deadline),
             "invalid run expiration")
    _require(due <= now < expires, "early or expired run; skip, block entries and alert")
    _require(isinstance(snapshot.signal_id, str) and bool(snapshot.signal_id.strip()), "signal ID required")
    _require(isinstance(snapshot.symbol, str) and re.fullmatch(r"US\.[A-Z][A-Z0-9.\-]*", snapshot.symbol) is not None,
             "US symbol required")
    if snapshot.portfolio == "B":
        _require(snapshot.symbol == "US.SPY" and snapshot.asset_class == "ETF", "B is SPY-only")
    else:
        _require(snapshot.symbol != "US.SPY" and snapshot.asset_class == "STOCK", "A is individual stocks only")
    _exact(sorted(snapshot.data_asof), sorted(GUARDS["max_data_age_seconds"]), "data timestamps")
    for kind, max_age in GUARDS["max_data_age_seconds"].items():
        age = (now - _aware(snapshot.data_asof[kind])).total_seconds()
        _require(0 <= age <= max_age, f"stale/future {kind}; block entries")
    _require(snapshot.data_asof["bar"] == due, "expected completed bar missing")
    strategy = spec["portfolios"][snapshot.portfolio]["strategy_id"]
    return config_digest({"strategy_id": strategy, "session_date": day.isoformat(), "symbol": snapshot.symbol,
                          "signal_id": snapshot.signal_id, "workflow": workflow,
                          "scheduled_for": due.astimezone(timezone.utc).isoformat(), "config_hash": digest})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--preview", type=date.fromisoformat)
    args = parser.parse_args()
    spec, roles, digest = load_spec(args.root)
    print(f"NOT DEPLOYED | local spec valid | config_sha256={digest}")
    if args.preview:
        preview = preview_day(args.preview, spec, roles, args.root)
        for name, ticks in preview.items():
            print(f"{name}: " + ", ".join(tick.isoformat() for tick in ticks))


if __name__ == "__main__":
    main()
