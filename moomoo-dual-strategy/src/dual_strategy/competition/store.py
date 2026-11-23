"""Persistent competition state and order outbox under private/competition (gitignored).

state.json   risk state + sleeve ownership; written atomically; never reset by code
outbox/<session>.jsonl   every planned intent and every broker outcome, append-only
"""

from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import yaml

from dual_strategy.competition.planner import SHORTABLE, SLEEVES, capital_limits, new_state


def load_config(project: Path) -> dict:
    return yaml.safe_load((project / "config" / "competition.yaml").read_text(encoding="utf-8"))


class Store:
    def __init__(self, project: Path, cfg: dict):
        self.dir = project / cfg["state_dir"]                 # an absolute state_dir is used as given
        self.path = self.dir / "state.json"                   # directories are created on first write only

    def load(self) -> dict:
        if not self.path.exists():
            return new_state()
        return json.loads(self.path.read_text(encoding="utf-8"))

    def save(self, state: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=1, default=str), encoding="utf-8")
        os.replace(tmp, self.path)

    def append(self, session: str, record: dict) -> None:
        record = {"logged_at": datetime.now(timezone.utc).isoformat(), **record}
        (self.dir / "outbox").mkdir(parents=True, exist_ok=True)
        with (self.dir / "outbox" / f"{session}.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    def audit(self, session: str, record: dict, *, fixed_name: str | None = None) -> str:
        audit_id = f"{session}/{fixed_name or uuid4().hex}"
        path = self.dir / "audit" / f"{audit_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {**record, "audit_id": audit_id, "logged_at": datetime.now(timezone.utc).isoformat()}
        with path.open("x", encoding="utf-8") as fh:
            json.dump(record, fh, default=str)
            fh.flush()
            os.fsync(fh.fileno())
        return audit_id

    def outbox(self, session: str) -> list[dict]:
        p = self.dir / "outbox" / f"{session}.jsonl"
        if not p.exists():
            return []
        return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]

    def latest_session(self) -> str | None:
        files = sorted((self.dir / "outbox").glob("*.jsonl"))
        return files[-1].stem if files else None


def sleeve_performance(store: Store, state: dict | None, account: dict | None, cfg: dict) -> dict:
    initial = (state or {}).get("initial_nav")
    initialized = type(initial) in (int, float) and math.isfinite(initial) and initial > 0
    reduced = (state or {}).get("reduced", False)
    initial_allocations = {s: cfg.get(s, {}).get("initial_allocation_usd", initial * cfg["allocation"][s])
                           for s in SLEEVES} if initialized else {}
    sleeves = {s: {"status": "not_recorded", "realized_gross": None, "unrealized_gross": None,
                   "gross_pnl": None, "fees": None, "net_pnl": None, "fill_count": 0,
                   "initial_capital": initial_allocations.get(s),
                   "capital": None, "limits": capital_limits(s, None, cfg, reduced),
                   "positions": {}, "receipts": []} for s in SLEEVES}
    result = {"asof": (account or {}).get("at"), "sleeves": sleeves,
              "account_pnl": None, "unallocated_account_change": None, "cost_method": "weighted_average",
              "capital_basis": "initial_allocation_plus_own_gross_pnl"}
    if state is None:
        return result
    groups = {}
    try:
        for path in sorted((store.dir / "outbox").glob("????-??-??.jsonl")):
            for row in store.outbox(path.stem):
                if not isinstance(row, dict):
                    raise ValueError("Invalid receipt")
                if row.get("event") not in ("intent", "acknowledged", "outcome", "order_refused"):
                    continue
                key = (row.get("audit_id"), row.get("intent_id"))
                if any(k is not None and not isinstance(k, str) for k in key):
                    raise ValueError("Invalid receipt identity")
                groups.setdefault(key, []).append(row)
    except (OSError, ValueError):
        for sleeve in sleeves.values():
            sleeve["status"] = "incomplete"
        return result
    incomplete, seen_orders = set(), {}
    realized = {s: 0.0 for s in sleeves}
    for (audit_id, intent_id), rows in groups.items():
        intents = [r for r in rows if r["event"] == "intent" and r.get("mode") == "EXECUTE"]
        if not intents:
            if any(r["event"] in ("acknowledged", "outcome") for r in rows):
                incomplete.update(sleeves)
            continue
        intent = intents[0]
        s = intent.get("sleeve")
        if not isinstance(s, str) or s not in sleeves:
            incomplete.update(sleeves)
            continue
        acks = [r for r in rows if r["event"] == "acknowledged"]
        outcomes = [r for r in rows if r["event"] == "outcome"]
        if not acks and not outcomes and any(r["event"] == "order_refused" for r in rows):
            continue
        if len(intents) != 1 or len(acks) != 1 or len(outcomes) != 1:
            incomplete.add(s)
            continue
        outcome = outcomes[0]
        q, price = outcome.get("filled"), outcome.get("avg_price")
        if (not isinstance(acks[0].get("order_id"), str) or not acks[0]["order_id"]
                or acks[0]["order_id"] != outcome.get("order_id")
                or outcome.get("status") not in ("FILLED", "CANCELLED", "REJECTED")
                or type(q) is not int or type(intent.get("qty")) is not int or not 0 <= q <= intent["qty"]
                or outcome.get("qty") != intent["qty"]
                or (outcome["status"] == "FILLED" and q != intent["qty"])
                or intent.get("side") not in ("BUY", "SELL") or not isinstance(intent.get("symbol"), str)):
            incomplete.add(s)
            continue
        order_id = acks[0]["order_id"]
        if order_id in seen_orders:
            incomplete.update((s, seen_orders[order_id]))
            continue
        seen_orders[order_id] = s
        if q == 0:
            continue
        if type(price) not in (int, float) or not math.isfinite(price) or price <= 0:
            incomplete.add(s)
            continue
        sleeve, symbol = sleeves[s], intent["symbol"]
        book = sleeve["positions"].setdefault(symbol, {"qty": 0, "cost_basis": 0.0})
        dq, held = (q if intent["side"] == "BUY" else -q), book["qty"]      # signed average cost; shorts negative
        if held == 0 or (held > 0) == (dq > 0):
            if dq < 0 and s not in SHORTABLE:
                incomplete.add(s)
                continue
            book["qty"], book["cost_basis"] = held + dq, book["cost_basis"] + dq * price
        else:
            closed = min(abs(dq), abs(held))
            avg = book["cost_basis"] / held
            realized[s] += closed * (price - avg) * (1 if held > 0 else -1)
            book["qty"] = held + (closed if dq > 0 else -closed)
            book["cost_basis"] = avg * book["qty"]
            rest = abs(dq) - closed
            if rest:
                if dq < 0 and s not in SHORTABLE:
                    incomplete.add(s)
                    continue
                book["qty"] = rest if dq > 0 else -rest
                book["cost_basis"] = book["qty"] * price
        if not book["qty"]:
            sleeve["positions"].pop(symbol)
        sleeve["fill_count"] += 1
        sleeve["receipts"].append({"audit_id": audit_id, "intent_id": intent_id, "symbol": symbol,
            "side": intent["side"], "filled": q, "avg_price": price, "notional": q * price,
            "recorded_at": outcome.get("logged_at")})
    quotes, broker = (account or {}).get("quotes", {}), (account or {}).get("positions", {})
    owned = {}
    for book in state["sleeves"].values():
        for symbol, q in book.items():
            owned[symbol] = owned.get(symbol, 0) + q
    for s, sleeve in sleeves.items():
        positions = sleeve["positions"]
        if {symbol: p["qty"] for symbol, p in positions.items()} != state["sleeves"].get(s, {}):
            incomplete.add(s)
        unrealized = 0.0
        for symbol, position in positions.items():
            price = quotes.get(symbol)
            if (type(price) not in (int, float) or not math.isfinite(price) or price <= 0
                    or owned.get(symbol) != broker.get(symbol)):
                incomplete.add(s)
                continue
            position["mark_price"] = price
            position["unrealized_gross"] = position["qty"] * price - position["cost_basis"]
            unrealized += position["unrealized_gross"]
        if not math.isfinite(realized[s] + unrealized):
            incomplete.add(s)
        sleeve["status"] = "incomplete" if s in incomplete else "available"
        if s not in incomplete:
            sleeve.update(realized_gross=realized[s], unrealized_gross=unrealized, gross_pnl=realized[s] + unrealized)
            if initialized:
                sleeve["capital"] = sleeve["initial_capital"] + sleeve["gross_pnl"]
                sleeve["limits"] = capital_limits(s, sleeve["capital"], cfg, reduced)
            if not sleeve["fill_count"]:
                sleeve.update(fees=0.0, net_pnl=0.0)
    cash = (account or {}).get("cash")
    if (initialized and type(cash) in (int, float)
            and all(type(quotes.get(s)) in (int, float) and math.isfinite(quotes[s]) and quotes[s] > 0 for s in broker)):
        result["account_pnl"] = cash + sum(q * quotes[s] for s, q in broker.items()) - initial
        if not incomplete and owned == broker:
            result["unallocated_account_change"] = result["account_pnl"] - sum(s["gross_pnl"] for s in sleeves.values())
    return result
