"""Offline specification tests; no account, network, order or deployment calls."""

from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
import socket

import pytest
import yaml

from dual_strategy.agents.specs import (
    NY, ROOT, SignalSnapshot, SpecError, _UniqueLoader, config_digest,
    load_spec, preview_day, validate_snapshot, validate_spec,
)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("cloud specification tests must remain offline")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)


@pytest.fixture
def bundle():
    return load_spec()


def snapshot(bundle, portfolio="A", day=17, hour=10, minute=0):
    _, _, digest = bundle
    due = datetime(2026, 11, day, hour, minute, tzinfo=NY)
    now = due + timedelta(seconds=1)
    item = SignalSnapshot(
        portfolio=portfolio, workflow="pipeline_a" if portfolio == "A" else "pipeline_b",
        scheduled_for=due, expires_at=due + timedelta(seconds=60),
        symbol="US.AAPL" if portfolio == "A" else "US.SPY",
        asset_class="STOCK" if portfolio == "A" else "ETF",
        signal_id="closed-bar-001", config_hash=digest,
        data_asof={"quote": now, "account": now, "sentinel": now, "bar": due},
    )
    return item, now


def test_local_only_contract(bundle):
    spec, roles, digest = bundle
    assert len(digest) == 64
    assert spec["status"] == roles["status"] == "NOT DEPLOYED"
    assert spec["api_request_ready"] is False
    assert spec["cloud_writes_enabled"] is spec["paper_execution_enabled"] is False
    assert all(role["tool_allowlist"] == [] and role["cloud_id"] is None
               and role["enabled"] is False for role in roles["roles"].values())
    assert all(schedule["enabled"] is False and schedule["timezone"] == "America/New_York"
               for schedule in spec["schedules"].values())
    assert spec["portfolios"]["A"]["state_namespace"] != spec["portfolios"]["B"]["state_namespace"]
    assert all(p["initial_virtual_nav"] == 50000 and p["state"] == "BLOCKED_SETUP"
               for p in spec["portfolios"].values())


@pytest.mark.parametrize("field,value", [
    ("status", "DEPLOYED"), ("api_request_ready", True), ("cloud_writes_enabled", True),
    ("paper_execution_enabled", True), ("timezone", "Asia/Singapore"), ("calendar", "weekdays"),
    ("revision", "latest"), ("revision", True), ("paper_execution_enabled", 0),
    ("agent", "invented-id"), ("environment_id", "invented-environment"),
])
def test_rejects_unsafe_top_level(bundle, field, value):
    spec, roles, _ = bundle
    spec[field] = value
    with pytest.raises(SpecError):
        validate_spec(spec, roles)


@pytest.mark.parametrize("role_name", [
    "coordinator", "researcher", "signal_a", "signal_b", "risk_supervisor", "watchdog",
    "auditor", "paper_executor", "reporter",
])
@pytest.mark.parametrize("tool", ["trading_order_place", "sim_trade_order_place", "Bash", "*"])
def test_no_live_paper_or_escape_tool_allowlists(bundle, role_name, tool):
    spec, roles, _ = bundle
    roles["roles"][role_name]["tool_allowlist"] = [tool]
    with pytest.raises(SpecError, match="role"):
        validate_spec(spec, roles)


@pytest.mark.parametrize("name,field,value", [
    ("signal_a", "scope", ["noise_area"]), ("signal_b", "scope", ["orb", "noise_area"]),
    ("signal_a", "engine", "llm"), ("risk_supervisor", "exposure_effect", "approve"),
    ("risk_supervisor", "outputs", ["approval"]), ("watchdog", "exposure_effect", "increase"),
    ("watchdog", "outputs", ["open_order"]), ("auditor", "engine", "executor"),
    ("reporter", "exposure_effect", "increase"), ("paper_executor", "enabled", True),
    ("coordinator", "version", "latest"), ("reporter", "cloud_id", "fake-id"),
])
def test_role_authority_and_scope_are_fixed(bundle, name, field, value):
    spec, roles, _ = bundle
    roles["roles"][name][field] = value
    with pytest.raises(SpecError):
        validate_spec(spec, roles)


@pytest.mark.parametrize("name,field,value", [
    ("flatten", "enabled", True), ("report", "timezone", "UTC"),
    ("flatten", "timing", {"kind": "cron", "expression": "55 15 * * 1-5"}),
    ("verify_close", "timing", {"kind": "close_offset", "minutes": 1}),
    ("pipeline_a", "scope", ["noise_area"]), ("pipeline_b", "roles", ["signal_a"]),
])
def test_rejects_schedule_activation_and_unsafe_timing(bundle, name, field, value):
    spec, roles, _ = bundle
    spec["schedules"][name][field] = value
    with pytest.raises(SpecError):
        validate_spec(spec, roles)


@pytest.mark.parametrize("guard,value", [
    ("cloud_replaces_sentinel", True), ("local_sentinel_required", False),
    ("run_expiration_seconds", 999), ("automatic_attempts", 2),
    ("ledger_requires_atomic_persistent_deduplication", False),
    ("retry_policy", "blind_retry"), ("exchange_calendar_required", False),
])
def test_rejects_weakened_guards(bundle, guard, value):
    spec, roles, _ = bundle
    spec["guards"][guard] = value
    with pytest.raises(SpecError):
        validate_spec(spec, roles)


def test_pins_required_and_drift_fails_closed(bundle):
    original, roles, _ = bundle
    for pin in original["pins"]:
        spec = deepcopy(original)
        del spec["pins"][pin]
        with pytest.raises(SpecError, match="pin paths"):
            validate_spec(spec, roles)
        spec = deepcopy(original)
        spec["pins"][pin] = "0" * 64
        with pytest.raises(SpecError, match="pin drift"):
            validate_spec(spec, roles)


def test_duplicate_yaml_fields_rejected():
    with pytest.raises(SpecError, match="duplicate"):
        yaml.load("enabled: false\nenabled: true\n", Loader=_UniqueLoader)


def test_competition_calendar_has_eight_sessions(bundle):
    spec, roles, _ = bundle
    observed = [d for d in range(17, 29) if preview_day(date(2026, 11, d), spec, roles)["research"]]
    assert observed == [17, 18, 19, 20, 23, 24, 25, 27]


@pytest.mark.parametrize("day", [date(2026, 11, 16), date(2026, 11, 21), date(2026, 11, 22),
                                 date(2026, 11, 26), date(2026, 11, 28), date(2026, 11, 30)])
def test_no_events_on_closed_or_outside_dates(bundle, day):
    spec, roles, _ = bundle
    assert all(not ticks for ticks in preview_day(day, spec, roles).values())


@pytest.mark.parametrize("day,close_hour,b_count", [(17, 16, 12), (27, 13, 6)])
def test_halfday_aware_close_and_independent_verification(bundle, day, close_hour, b_count):
    spec, roles, _ = bundle
    plan = preview_day(date(2026, 11, day), spec, roles)
    assert plan["research"][0].time() == time(9)
    assert plan["flatten"][0].time() == time(close_hour - 1, 55)
    assert plan["verify_close"][0].time() == time(close_hour - 1, 58)
    assert plan["report"][0].time() == time(close_hour, 10)
    assert len(plan["pipeline_a"]) == 55
    assert plan["pipeline_a"][0].time() == time(9, 35)
    assert plan["pipeline_a"][-1].time() == time(10, 29)
    assert len(plan["pipeline_b"]) == b_count
    assert plan["pipeline_b"][-1].time() == time(close_hour - 1, 30)
    for name, ticks in plan.items():
        assert all(tick.tzinfo.key == "America/New_York" for tick in ticks)
        if name not in ("research", "report"):
            assert all(time(9, 30) <= tick.time() < time(close_hour) for tick in ticks)


def test_idempotent_keys_are_repeatable_and_portfolio_separated(bundle):
    spec, roles, digest = bundle
    a, now = snapshot(bundle)
    first = validate_snapshot(a, now, spec, roles)
    assert first == validate_snapshot(a, now, spec, roles)
    # Retries refresh snapshots but do not create a new logical signal/key.
    assert first == validate_snapshot(replace(a, data_asof={**a.data_asof, "quote": now + timedelta(seconds=1)}),
                                      now + timedelta(seconds=1), spec, roles)
    assert first != validate_snapshot(replace(a, signal_id="next-signal"), now, spec, roles)
    b, now = snapshot(bundle, "B")
    assert first != validate_snapshot(b, now, spec, roles)
    utc = replace(a, scheduled_for=a.scheduled_for.astimezone(timezone.utc))
    assert first == validate_snapshot(utc, now, spec, roles)
    assert digest == config_digest({"roles": roles, "workflow": spec})


@pytest.mark.parametrize("kind,max_age", [("quote", 5), ("account", 15), ("sentinel", 5), ("bar", 90)])
def test_stale_missing_future_data_fails_closed(bundle, kind, max_age):
    spec, roles, _ = bundle
    item, now = snapshot(bundle)
    for age in (max_age + 1, -1):
        data = {**item.data_asof, kind: now - timedelta(seconds=age)}
        with pytest.raises(SpecError, match="stale/future"):
            validate_snapshot(replace(item, data_asof=data), now, spec, roles)
    data = dict(item.data_asof)
    del data[kind]
    with pytest.raises(SpecError, match="data timestamps"):
        validate_snapshot(replace(item, data_asof=data), now, spec, roles)


@pytest.mark.parametrize("change", [
    {"portfolio": "C"}, {"workflow": "pipeline_b"}, {"config_hash": "0" * 64},
    {"symbol": "US.SPY"}, {"asset_class": "ETF"}, {"signal_id": ""},
    {"scheduled_for": datetime(2026, 11, 17, 10)},
    {"expires_at": datetime(2026, 11, 17, 10)},
    {"expires_at": datetime(2026, 11, 17, 10, 2, tzinfo=NY)},
])
def test_invalid_proposal_metadata_is_rejected(bundle, change):
    spec, roles, _ = bundle
    item, now = snapshot(bundle)
    with pytest.raises(SpecError):
        validate_snapshot(replace(item, **change), now, spec, roles)


def test_early_expired_and_unclosed_bar_fail(bundle):
    spec, roles, _ = bundle
    item, now = snapshot(bundle)
    for stamp in (item.scheduled_for - timedelta(seconds=1), item.expires_at, now.replace(tzinfo=None)):
        with pytest.raises(SpecError):
            validate_snapshot(item, stamp, spec, roles)
    with pytest.raises(SpecError, match="completed bar"):
        validate_snapshot(replace(item, data_asof={**item.data_asof, "bar": item.scheduled_for - timedelta(minutes=1)}),
                          now, spec, roles)


@pytest.mark.parametrize("day,hour,minute", [(26, 10, 0), (27, 13, 0), (17, 16, 0), (17, 10, 1)])
def test_spy_rejects_closed_late_and_wrong_ticks(bundle, day, hour, minute):
    spec, roles, _ = bundle
    item, now = snapshot(bundle, "B", day, hour, minute)
    with pytest.raises(SpecError, match="unscheduled/closed"):
        validate_snapshot(item, now, spec, roles)


def test_spy_cannot_propose_individual_stock(bundle):
    spec, roles, _ = bundle
    item, now = snapshot(bundle, "B")
    with pytest.raises(SpecError, match="SPY-only"):
        validate_snapshot(replace(item, symbol="US.AAPL", asset_class="STOCK"), now, spec, roles)


def test_preview_and_validation_have_no_execution_entry_point(bundle):
    # Positive validation still leaves all execution disabled and reserves no keys.
    spec, roles, _ = bundle
    before = deepcopy(spec)
    preview_day(date(2026, 11, 27), spec, roles)
    item, now = snapshot(bundle)
    validate_snapshot(item, now, spec, roles)
    assert spec == before
    assert (ROOT / "config/cloud_workflows.yaml").is_file()
