"""Read-only competition dashboard observations; no broker calls or state writes."""

from __future__ import annotations

import json
import math
import re
import shlex
import subprocess
from collections import Counter
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

from dual_strategy.competition.signals import b3_decision_times
from dual_strategy.competition.store import Store, load_config, sleeve_performance
from dual_strategy.data.calendar import NY, session_bounds
from dual_strategy.reporting.service import redacted

SAFE_KEYS = ("initial_nav", "high_water", "last_nav", "last_mark_at", "day", "day_start_nav", "halted", "locked_day",
             "reduced", "orders_today", "order_errors_today", "sleeves", "h_entry_session", "h_plan", "h_plan_audit_id")


def _summary(state: dict | None, cfg: dict, now: datetime) -> dict | None:
    if state is None:
        return None
    out = {k: state.get(k) for k in SAFE_KEYS}
    hw, nav = state.get("high_water"), state.get("last_nav")
    out["nav_estimate"] = nav
    out["halt_level"] = round(hw * (1 - cfg["risk"]["halt_drawdown_fraction"]), 2) if hw else None
    out["drawdown_pct"] = round(100 * (nav / hw - 1), 3) if nav and hw else None
    out["locked_today"] = state.get("locked_day") == now.astimezone(NY).date().isoformat()
    out["events"] = (state.get("events") or [])[-20:]
    return out


def _clean(x):
    if isinstance(x, float) and not math.isfinite(x):
        return None
    if isinstance(x, dict):
        return {k: ("Recorded error; inspect the private runner log." if k in ("error", "blocked") and v
                    else "Order error; human reconciliation required." if k == "reason" and isinstance(v, str) and v.startswith("order error:")
                    else _clean(v)) for k, v in redacted(x).items() if k != "order_id"}
    if isinstance(x, list):
        return [_clean(v) for v in x]
    return x


def _process(project: Path) -> dict:
    try:
        result = subprocess.run(["ps", "-axo", "pid=,stat=,args="], capture_output=True, text=True, timeout=2)
        if result.returncode:
            return {"status": "unknown", "count": None}
        count = 0
        for row in result.stdout.splitlines():
            parts = row.split(maxsplit=2)
            if len(parts) != 3 or "Z" in parts[1]:
                continue
            args = shlex.split(parts[2])
            if (args and Path(args[0]).name.lower().startswith("python")
                    and str(project.resolve() / "scripts/run_competition.py") in args
                    and "loop" in args and "--execute" in args):
                count += 1
        return {"status": "running" if count == 1 else "multiple" if count > 1 else "stopped", "count": count}
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return {"status": "unknown", "count": None}


def _runtime(project: Path, state: dict | None, cfg: dict, now: datetime) -> dict:
    bounds = session_bounds(now)
    schedule = []
    opened, closed = bounds if bounds else (None, None)
    if bounds:
        schedule = [{"at": opened + timedelta(seconds=10), "label": "H open orders"}]
        schedule += [{"at": t + timedelta(seconds=15), "label": "B3 decision"}
                     for t in b3_decision_times(opened, closed, cfg["b3"]["decision_times"])]
        schedule += [{"at": closed - timedelta(minutes=cfg["b3"]["flatten_minutes_before_close"]), "label": "B3 flatten"},
                     {"at": closed, "label": "Market close"},
                     {"at": closed + timedelta(minutes=15), "label": "Next-session H plan"}]
    next_action = next((s for s in schedule if s["at"] > now), None)
    age, mark_status = None, "not_recorded"
    mark = (state or {}).get("last_mark_at")
    if mark:
        age = (now - datetime.fromisoformat(mark)).total_seconds()
        mark_status = "clock_mismatch" if age < 0 else "stale" if age > 120 else "recent"
    return {"process": _process(project), "market_phase": "closed" if not bounds or now >= closed else "preopen" if now < opened else "open",
            "session_open": opened.isoformat() if opened else None, "session_close": closed.isoformat() if closed else None,
            "schedule": [{**s, "at": s["at"].isoformat()} for s in schedule],
            "next_action": {**next_action, "at": next_action["at"].isoformat()} if next_action else None,
            "mark_status": mark_status, "mark_age_seconds": age, "mark_at": mark}


def _orders(rows: list[dict]):
    intents, acknowledged, outcomes = {}, set(), {}
    errors, refused = set(), set()
    for row in rows:
        key = (row.get("audit_id"), row.get("intent_id"))
        event = row.get("event")
        if event == "intent":
            intents[key] = {**row, "status": "PLANNED" if row.get("mode") == "PLAN" else "UNCONFIRMED", "filled": None, "avg_price": None}
        elif event == "acknowledged":
            acknowledged.add(row["order_id"])
            if key in intents:
                intents[key]["status"] = "ACKNOWLEDGED"
        elif event == "outcome":
            outcomes[row["order_id"]] = row
            if key in intents:
                intents[key].update({k: row.get(k) for k in ("status", "filled", "avg_price")})
        elif event == "order_error":
            errors.add(key)
            if key in intents:
                intents[key]["has_error"] = True
                if intents[key]["status"] not in ("REJECTED", "FILLED", "CANCELLED"):
                    intents[key]["status"] = "ERROR"
        elif event == "order_refused":
            refused.add(key)
            if key in intents:
                intents[key]["status"] = "REFUSED"
    counts = {"acknowledged": len(acknowledged), "filled": 0, "partial": 0, "rejected": 0, "cancelled": 0,
              "errors": len(errors), "refused": len(refused)}
    for row in outcomes.values():
        status = row.get("status", "").lower()
        if status in ("filled", "rejected", "cancelled"):
            counts[status] += 1
        if 0 < row.get("filled", 0) < row.get("qty", 0):
            counts["partial"] += 1
    return list(intents.values()), counts


@lru_cache(maxsize=1024)
def _audit_summary(path: Path, mtime_ns: int, size: int) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {k: data.get(k) for k in ("kind", "mode", "logged_at", "audit_id", "account", "state_after", "decision", "reason", "error")}


def _observations(store: Store, session: str):
    equity, accounts, decisions, warnings = [], [], [], []
    for path in (store.dir / "audit" / session).glob("*.json"):
        try:
            stat = path.stat()
            data = _audit_summary(path, stat.st_mtime_ns, stat.st_size)
        except (OSError, ValueError):
            warnings.append("An audit snapshot is unreadable or still being written; displayed evidence is incomplete.")
            continue
        if data.get("mode") != "EXECUTE":
            continue
        account = data.get("account")
        if account and account.get("at"):
            accounts.append(account)
        if data.get("kind") == "risk":
            state = data.get("state_after") or {}
            nav, hw, at = state.get("last_nav"), state.get("high_water"), state.get("last_mark_at")
            if at and nav is not None and hw:
                equity.append({"at": at, "nav": nav, "drawdown_pct": round(100 * (nav / hw - 1), 6)})
        if data.get("kind") == "b3" and data.get("decision"):
            decisions.append({**data["decision"], "audit_id": data["audit_id"], "plan_reason": data["reason"]})
    equity.sort(key=lambda p: p["at"])
    return {"equity": equity, "account_snapshot": max(accounts, key=lambda a: a["at"]) if accounts else None,
            "b3_decision": max(decisions, key=lambda d: d["at"]) if decisions else None,
            "evidence_warnings": list(dict.fromkeys(warnings))}


def _observed_time(value, now):
    try:
        at = datetime.fromisoformat(value)
        return at if at.tzinfo is not None and at <= now else None
    except (TypeError, ValueError):
        return None


def _access_health(store: Store, state: dict | None, now: datetime) -> dict:
    # CLI auth status can refresh the shared token; use passive runner evidence instead.
    mark = _observed_time((state or {}).get("last_mark_at"), now)
    failures, incomplete = [], False
    try:
        records = []
        for path in sorted((store.dir / "outbox").glob("????-??-??.jsonl"))[-2:]:
            records.extend(store.outbox(path.stem))
        for directory in sorted((store.dir / "audit").glob("????-??-??"))[-2:]:
            for path in directory.glob("*.json"):
                try:
                    stat = path.stat()
                    record = _audit_summary(path, stat.st_mtime_ns, stat.st_size)
                    if record.get("mode") == "EXECUTE":
                        records.append(record)
                except (OSError, ValueError):
                    incomplete = True
        for record in records:
            error = record.get("error")
            if not error or record.get("mode") == "PLAN":
                continue
            at = _observed_time(record.get("logged_at"), now)
            if at is None:
                incomplete = True
            elif mark is None or at >= mark:
                auth = re.search(r'\[(AUTH_EXPIRED|AUTH_INVALID)\]|"code"\s*:\s*"(AUTH_EXPIRED|AUTH_INVALID)"', str(error))
                failures.append({"status": "sign_in_required" if auth else "check_required", "at": at.isoformat()})
    except (OSError, ValueError):
        incomplete = True
    if failures:
        return max(failures, key=lambda f: (f["status"] == "sign_in_required", f["at"]))
    recent = mark is not None and (now - mark).total_seconds() <= 120 and not incomplete
    return {"status": "recent_success" if recent else "unverified", "at": mark.isoformat() if mark else None}


def _startup_health(project: Path, process: dict, now: datetime) -> dict:
    directory = project / "private/competition/scheduler"
    empty = {"status": "not_recorded", "session": None, "at": None, "reason": None}
    records = []
    reasons = {"read_only_preflight_failed_twice", "persistent_safety_block", "prior_startup_requires_review",
               "process_probe_failed", "startup_window_elapsed", "session_already_touched",
               "competition_account_not_confirmed", "execution_gate_or_storage_changed", "persisted_capital_invalid",
               "prior_b3_position_not_flat", "prior_execution_error_requires_review", "state_changed_during_preflight",
               "runner_appeared_during_preflight"}
    try:
        for path in sorted(directory.glob("????-??-??.json")):
            record = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(record, dict):
                raise ValueError("Invalid startup record")
            at = _observed_time(record.get("at"), now)
            status = record.get("status")
            if (at is None or record.get("session") != path.stem or at.astimezone(NY).date().isoformat() != path.stem
                    or status not in ("blocked", "unexpected_exit", "preflight_started", "launched", "completed")):
                raise ValueError("Invalid startup observation")
            prior = path.stem < now.astimezone(NY).date().isoformat()
            if status != "completed" and prior:
                status = "blocked"
            elif status == "unexpected_exit":
                status = "blocked"
            elif status == "preflight_started":
                status = "checking" if (now - at).total_seconds() <= 900 else "blocked"
            elif status == "launched" and process["status"] != "running":
                status = "unverified" if process["status"] == "unknown" else "blocked"
            reason = record.get("reason")
            records.append({"status": status, "session": path.stem, "at": at.isoformat(),
                            "reason": reason if isinstance(reason, str) and reason in reasons else None})
        blocked = next((r for r in records if r["status"] == "blocked"), None)
        if blocked:
            return blocked
        log = directory / "launchd.log"
        if log.exists():
            with log.open("rb") as stream:
                stream.seek(max(0, log.stat().st_size - 8192))
                lines = stream.read().decode("utf-8", errors="replace").splitlines()
            if lines:
                last = json.loads(lines[-1])
                if not isinstance(last, dict):
                    raise ValueError("Invalid scheduler output")
                if last.get("status") in ("blocked", "unexpected_exit"):
                    return {**empty, "status": "blocked"}
        return records[-1] if records else empty
    except (OSError, ValueError):
        return {**empty, "status": "unverified"}


def competition_payload(project: Path, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    cfg = load_config(project)
    stores = {mode: Store(project, {**cfg, "state_dir": f"{cfg['state_dir']}/{mode}"}) for mode in ("live", "plan")}
    states = {m: (s.load() if s.path.exists() else None) for m, s in stores.items()}
    source = "live" if states["live"] is not None else "plan"
    store = stores[source]
    session = now.astimezone(NY).date().isoformat()
    rows = store.outbox(session)
    orders, counts = _orders(rows)
    preview_path = project / "reports" / "competition" / "backtest_preview.json"
    preview = json.loads(preview_path.read_text(encoding="utf-8")) if preview_path.exists() else None
    if preview:
        for k, w in preview["windows"].items():
            if k != "long":
                w.pop("equity", None)
    observations = _observations(stores["live"], session)
    runtime = _runtime(project, states["live"], cfg, now)
    return _clean({
        "generated_at": now.isoformat(),
        "config": {k: cfg[k] for k in ("allocation", "b3", "h_etf", "risk", "execution_enabled")},
        "live": _summary(states["live"], cfg, now), "plan": _summary(states["plan"], cfg, now),
        "runtime": runtime, "access": _access_health(stores["live"], states["live"], now),
        "startup": _startup_health(project, runtime["process"], now),
        "outbox_source": "LIVE runner" if source == "live" else "PLAN runner (nothing sent)",
        "outbox_session": session, "outbox": rows[-200:], "orders": orders, "order_counts": counts,
        "step_errors": dict(Counter(r.get("step", "unknown") for r in rows if r.get("event") == "runner_error")),
        "performance": sleeve_performance(stores["live"], states["live"], observations["account_snapshot"], cfg),
        **observations, "preview": preview,
    })
