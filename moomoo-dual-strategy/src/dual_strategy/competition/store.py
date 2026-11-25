"""Persistent competition state and order outbox under private/competition (gitignored).

state.json   risk state + sleeve ownership; written atomically; never reset by code
outbox/<session>.jsonl   every planned intent and every broker outcome, append-only
"""

from __future__ import annotations

import json
import math
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import yaml

from dual_strategy.competition.planner import SHORTABLE, SLEEVES, capital_limits, new_state
from dual_strategy.reporting.metrics import closed_trade_metrics


OUTBOX_PATTERN = "????-??-??.jsonl"
MANUAL_FILL = "user_confirmed_manual_broker_sell"


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
        record = {"logged_at": datetime.now(timezone.utc).isoformat(), **record, "audit_id": audit_id}
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
        files = sorted((self.dir / "outbox").glob(OUTBOX_PATTERN))
        return files[-1].stem if files else None


def _exit_summary(fills, closed_trades):
    sources = Counter(fill["source"] for fill in fills)
    return {
        "realized_gross": math.fsum(fill["realized_gross"] for fill in fills),
        "exit_orders": len(fills),
        "manual_exits": sources["manual"],
        "automatic_exits": sources["automatic"],
        "closed_trade_gross": math.fsum(closed_trades),
        **closed_trade_metrics(closed_trades),
        "loss_rate_pct": 100 * sum(pnl < 0 for pnl in closed_trades) / len(closed_trades) if closed_trades else None,
    }


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
    sleeves["two_vote"].update(open_entry_lots={}, entry_lots={}, ledger_lots={}, reserved_profit=None,
                               cost_method="identified_entry_lots")
    exits = {"status": "not_recorded", "fills": [], "totals": None, "sleeves": {}, "reviews": {}}
    result = {"asof": (account or {}).get("at"), "sleeves": sleeves, "exits": exits, "trades": [],
              "account_pnl": None, "unallocated_account_change": None, "cost_method": "weighted_average",
              "capital_basis": "initial_allocation_plus_own_gross_pnl"}
    if state is None:
        return result
    groups = {}
    try:
        for path in sorted((store.dir / "outbox").glob(OUTBOX_PATTERN)):
            for row in store.outbox(path.stem):
                if not isinstance(row, dict):
                    raise ValueError("Invalid receipt")
                if row.get("event") == "exit_review":
                    exits["reviews"][path.stem] = row.get("audit_id")
                if row.get("event") not in ("intent", "acknowledged", "outcome", "order_refused"):
                    continue
                key = (row.get("audit_id"), row.get("intent_id"))
                if any(k is not None and not isinstance(k, str) for k in key):
                    raise ValueError("Invalid receipt identity")
                groups.setdefault(key, (path.stem, []))[1].append(row)
    except (OSError, ValueError):
        for sleeve in sleeves.values():
            sleeve["status"] = "incomplete"
        exits["status"] = "incomplete"
        return result
    incomplete, seen_orders = set(), {}
    realized = {s: 0.0 for s in sleeves}
    close_fills, closed_trades, open_trade_pnl = defaultdict(list), defaultdict(list), defaultdict(float)
    trades = []   # every filled order (entries and exits), in receipt order
    vote_lots = defaultdict(list)
    vote_gains = vote_losses = 0.0
    for (audit_id, intent_id), (session, rows) in groups.items():
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
                or (outcome["status"] == "REJECTED" and q != 0)
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
        closed = min(abs(held), q) if held * dq < 0 else 0
        pnl_before = realized[s]
        if s == "two_vote":
            # A profit exit names its entry lot. Other exits close the oldest shares first.
            lots = vote_lots[symbol]
            remaining = q
            target = intent.get("take_profit_lot_id")
            closing = [lot for lot in lots if lot["id"] == target] if target is not None else list(lots)
            if target is not None and (held * dq >= 0 or len(closing) != 1 or intent["qty"] > closing[0]["shares"]):
                incomplete.add(s)
                continue
            if held * dq < 0:
                for lot in closing:
                    if not remaining:
                        break
                    shares = min(lot["shares"], remaining)
                    pnl = shares * (price - lot["entry_price"]) * lot["side"]
                    trade_key = (s, lot["id"])
                    realized[s] += pnl
                    vote_gains += max(0.0, pnl)
                    vote_losses += min(0.0, pnl)
                    open_trade_pnl[trade_key] += pnl
                    remaining -= shares
                    lot["shares"] -= shares
                    if not lot["shares"]:
                        closed_trades[s].append(round(open_trade_pnl.pop(trade_key), 8))
                        lots.remove(lot)
            if remaining:
                lots.append({"id": f"{audit_id}:{intent_id}", "shares": remaining, "entry_price": price,
                             "side": 1 if dq > 0 else -1})
            book["qty"] = held + dq
            book["cost_basis"] = math.fsum(lot["shares"] * lot["entry_price"] * lot["side"] for lot in lots)
        elif held == 0 or (held > 0) == (dq > 0):
            if dq < 0 and s not in SHORTABLE:
                incomplete.add(s)
                continue
            book["qty"], book["cost_basis"] = held + dq, book["cost_basis"] + dq * price
        else:
            avg = book["cost_basis"] / held
            pnl = closed * (price - avg) * (1 if held > 0 else -1)
            realized[s] += pnl
            trade_key = (s, symbol)
            open_trade_pnl[trade_key] += pnl
            if closed == abs(held):
                closed_trades[s].append(round(open_trade_pnl.pop(trade_key), 8))
            book["qty"] = held + (closed if dq > 0 else -closed)
            book["cost_basis"] = avg * book["qty"]
            rest = abs(dq) - closed
            if rest:
                if dq < 0 and s not in SHORTABLE:
                    incomplete.add(s)
                    continue
                book["qty"] = rest if dq > 0 else -rest
                book["cost_basis"] = book["qty"] * price
        if closed:
            evidence_source = outcome.get("source")
            close_fills[s].append({
                "session": session, "sleeve": s, "symbol": symbol, "side": intent["side"],
                "shares": closed, "avg_price": price, "realized_gross": round(realized[s] - pnl_before, 8),
                "source": "manual" if evidence_source == MANUAL_FILL else "automatic",
                "evidence_source": evidence_source, "status": outcome["status"],
                "recorded_at": outcome.get("logged_at"), "audit_id": audit_id, "intent_id": intent_id,
            })
        trades.append({
            "session": session, "sleeve": s, "symbol": symbol, "side": intent["side"],
            # held is the sleeve's position before this fill: trading against it closes, otherwise it opens
            "action": ("sell" if dq < 0 else "cover") if held * dq < 0 else ("buy" if dq > 0 else "short"),
            "shares": q, "avg_price": price, "notional": q * price,
            "realized_gross": round(realized[s] - pnl_before, 8) if closed else None,
            "reason": intent.get("reason"), "source": "manual" if outcome.get("source") == MANUAL_FILL else "automatic",
            "status": outcome["status"], "recorded_at": outcome.get("logged_at"), "audit_id": audit_id,
            "intent_id": intent_id,
            # When the order was placed (manual sells carry the broker's fill time); reconciliation can log much later
            "ordered_at": intent.get("created_at") or outcome.get("logged_at"),
        })
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
    balanced = set(sleeves) - incomplete     # receipts complete and equal to the runner's books; marks not needed yet
    # Historical realized P&L needs complete, balanced receipts. It does not need a current quote.
    def when(trade):
        try:
            at = datetime.fromisoformat(trade["ordered_at"])
            return at if at.tzinfo else at.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            return datetime.max.replace(tzinfo=timezone.utc)
    result["trades"] = sorted(({**t, "verified": t["sleeve"] not in incomplete} for t in trades),
                              key=lambda t: (t["session"], when(t)))
    exits["status"] = "incomplete" if incomplete else "available"
    exits["fills"] = sorted((fill for s in sleeves if s not in incomplete for fill in close_fills[s]),
                            key=lambda fill: (fill["session"], fill["recorded_at"] or ""))
    exits["sleeves"] = {s: _exit_summary(close_fills[s], closed_trades[s]) for s in sleeves if s not in incomplete}
    if not incomplete:
        exits["totals"] = _exit_summary(exits["fills"], [pnl for trades in closed_trades.values() for pnl in trades])
    for s, sleeve in sleeves.items():
        positions = sleeve["positions"]
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
        if s == "two_vote" and s in balanced:
            # Display only (the dashboard's per-lot rows without live quotes). Trading reads entry_lots, which exist
            # only when the sleeve is fully available.
            sleeve["ledger_lots"] = {sym: [dict(lot) for lot in lots] for sym, lots in vote_lots.items() if lots}
        if s not in incomplete:
            sleeve.update(realized_gross=realized[s], unrealized_gross=unrealized, gross_pnl=realized[s] + unrealized)
            bank_gains = s == "two_vote" and cfg.get("two_vote", {}).get("bank_realized_gains")
            if s == "two_vote":
                sleeve.update(open_entry_lots={sym: len(lots) for sym, lots in vote_lots.items() if lots},
                              entry_lots={sym: lots for sym, lots in vote_lots.items() if lots},
                              reserved_profit=vote_gains if bank_gains else 0.0)
            if initialized:
                if bank_gains:
                    sleeve["capital"] = sleeve["initial_capital"] + vote_losses
                else:
                    sleeve["capital"] = sleeve["initial_capital"] + sleeve["gross_pnl"]
                sleeve["limits"] = capital_limits(s, sleeve["capital"], cfg, reduced)
            if not sleeve["fill_count"]:
                sleeve.update(fees=0.0, net_pnl=0.0)
    cash, total = (account or {}).get("cash"), (account or {}).get("broker_total")
    if initialized and type(total) in (int, float) and math.isfinite(total) and total > 0:
        # Moomoo's total_asset. Its cash excludes short-sale proceeds, so cash + signed positions undercounts shorts.
        result["account_pnl"] = total - initial
    elif (initialized and type(cash) in (int, float)
            and all(type(quotes.get(s)) in (int, float) and math.isfinite(quotes[s]) and quotes[s] > 0 for s in broker)):
        result["account_pnl"] = cash + sum(q * quotes[s] for s, q in broker.items()) - initial
    if result["account_pnl"] is not None:
        if not incomplete and owned == broker:
            result["unallocated_account_change"] = result["account_pnl"] - sum(s["gross_pnl"] for s in sleeves.values())
    return result
