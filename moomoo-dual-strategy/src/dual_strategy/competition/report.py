"""Read-only JSON for the dashboard's /competition page. Never includes account identifiers."""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

from dual_strategy.competition.store import Store, load_config
from dual_strategy.data.calendar import NY

SAFE_KEYS = ("initial_nav", "high_water", "last_nav", "last_mark_at", "day", "day_start_nav", "halted", "locked_day",
             "reduced", "orders_today", "sleeves", "h_entry_session", "h_plan")


def _summary(state: dict | None, cfg: dict) -> dict | None:
    if not state or state.get("high_water") is None:
        return None
    out = {k: state.get(k) for k in SAFE_KEYS}
    hw, nav = state["high_water"], state.get("last_nav")
    out["nav_estimate"] = nav
    out["halt_level"] = round(hw * (1 - cfg["risk"]["halt_drawdown_fraction"]), 2)
    out["drawdown_pct"] = round(100 * (nav / hw - 1), 3) if nav else None
    out["locked_today"] = state.get("locked_day") == datetime.now(NY).date().isoformat()
    out["events"] = (state.get("events") or [])[-20:]
    return out


def _clean(x):
    if isinstance(x, float) and not math.isfinite(x):
        return None
    if isinstance(x, dict):
        return {k: _clean(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_clean(v) for v in x]
    return x


def competition_payload(project: Path) -> dict:
    cfg = load_config(project)
    stores = {mode: Store(project, {**cfg, "state_dir": f"{cfg['state_dir']}/{mode}"}) for mode in ("live", "plan")}
    states = {m: (s.load() if s.path.exists() else None) for m, s in stores.items()}
    source = "live" if states["live"] else "plan"
    store = stores[source]
    session = store.latest_session()
    preview_path = project / "reports" / "competition" / "backtest_preview.json"
    preview = json.loads(preview_path.read_text(encoding="utf-8")) if preview_path.exists() else None
    if preview:
        for k, w in preview["windows"].items():
            if k != "long":
                w.pop("equity", None)
    return _clean({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config": {k: cfg[k] for k in ("allocation", "b3", "h_etf", "risk", "execution_enabled")},
        "live": _summary(states["live"], cfg), "plan": _summary(states["plan"], cfg),
        "outbox_source": "LIVE runner" if source == "live" else "PLAN runner (nothing sent)",
        "outbox_session": session, "outbox": store.outbox(session)[-40:] if session else [],
        "preview": preview,
    })
