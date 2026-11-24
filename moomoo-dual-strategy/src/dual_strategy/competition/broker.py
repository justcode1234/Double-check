"""Gated broker access for the competition account. Constructed ONLY in --execute mode.

Three independent locks must all be open before an order can be sent:
  1. config/competition.yaml  execution_enabled: true
  2. private/competition/account.json exists with confirmed_competition_account: true, account_type 2, market_id 100
     (written by the user/Qoder from the Moomoo app; never committed, never guessed)
  3. the runner was started with --execute
Orders are LIMIT, U.S. only (shorts only for risk.short_sleeves, marked opens_short), through the same authenticated CLI transport as the practice runner, with an
allowlist of exactly one account. Every acknowledgement and fill is reconciled from broker order history.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from dual_strategy.competition.planner import Account, Intent, entries_disabled
from dual_strategy.execution.moomoo_cli import (ACCOUNT_LIST, CANCEL, CASH_INFO, HISTORY, PLACE, POSITION_LIST,
                                                CliTransport)
from dual_strategy.execution.moomoo_paper import ExecutionBlocked

READS = frozenset({ACCOUNT_LIST, CASH_INFO, POSITION_LIST, HISTORY})
STATUS = {1: "WORKING", 2: "WORKING", 3: "PARTIAL", 4: "FILLED", 5: "CANCELLED", 6: "REJECTED", 7: "CANCELLED"}


def load_account(project: Path, cfg: dict) -> dict:
    path = project / cfg["account_file"]
    if not path.exists():
        raise ExecutionBlocked(f"{cfg['account_file']} missing: the user must record the competition account first")
    acct = json.loads(path.read_text(encoding="utf-8"))
    if not (acct.get("confirmed_competition_account") is True and acct.get("account_type") == 2
            and acct.get("market_id") == 100 and isinstance(acct.get("account_id"), str) and acct["account_id"]):
        raise ExecutionBlocked("account file must confirm the competition account (account_type 2, market_id 100)")
    return acct


def parse_positions(rows: list[dict], funds: dict, expected: dict[str, int] | None = None) -> dict[str, int]:
    """Signed positions. The sim position rows carry no side field: a short is a negative qty or a negative market
    value. Cross-checked against the funds call: shorts in the rows <=> short_mv > 0, else fail closed. If the rows
    show shorts unsigned, rows are read as short only where the runner's own books expect exactly that short and the
    funds short_mv equals those rows' value within 2%; anything else fails closed."""
    positions, prices = {}, {}
    for r in rows:
        q = int(float(r.get("qty", r.get("quantity", 0))))
        mv = r.get("mv", r.get("market_val"))
        try:
            mv = float(mv) if mv not in (None, "") else None
        except (TypeError, ValueError):
            mv = None
        if q > 0 and mv is not None and mv < 0:
            q = -q                                          # short reported as a positive qty with negative value
        if q:
            positions[r["symbol"]] = positions.get(r["symbol"], 0) + q
            try:
                prices[r["symbol"]] = float(r.get("cur_price") or 0)
            except (TypeError, ValueError):
                prices[r["symbol"]] = 0.0
    short_mv = funds.get("short_mv")
    try:
        short_mv = abs(float(short_mv)) if short_mv not in (None, "") else None
    except (TypeError, ValueError):
        short_mv = None
    has_short = any(q < 0 for q in positions.values())
    if short_mv is not None and short_mv > 0.005 and not has_short and expected:
        unsigned = [s for s, q in expected.items() if q < 0 and positions.get(s) == -q]
        value = sum(positions[s] * prices.get(s, 0.0) for s in unsigned)
        if unsigned and value > 0 and abs(value - short_mv) <= 0.02 * short_mv:
            for s in unsigned:
                positions[s] = -positions[s]
            has_short = True
    if short_mv is not None and (short_mv > 0.005) != has_short:
        raise ExecutionBlocked(f"short positions ambiguous: rows {'show' if has_short else 'show no'} shorts, "
                               f"funds short_mv {short_mv}; reconcile before trading")
    return positions


class CompetitionBroker:
    def __init__(self, project: Path, cfg: dict, execute: bool, transport=None):
        if not execute:
            raise ExecutionBlocked("broker is only constructed with --execute")
        if cfg.get("execution_enabled") is not True:
            raise ExecutionBlocked("config/competition.yaml execution_enabled is false")
        acct = load_account(project, cfg)
        self.acc_id = acct["account_id"]
        self.cfg = cfg
        self.transport = transport or CliTransport(frozenset({self.acc_id}), frozenset())
        self._last = 0.0
        listed = [a for a in self.call(ACCOUNT_LIST, {})["data"]["accounts"] if a.get("account_id") == self.acc_id]
        if len(listed) != 1 or listed[0].get("account_type") != 2:
            raise ExecutionBlocked("competition account not uniquely listed as account_type 2 by the broker")

    def call(self, tool, args, min_interval=1.0):
        """Paced broker call. Read-only tools are retried twice (safe to repeat); PLACE and CANCEL are never
        auto-retried: their failure is ambiguous and must be reconciled from order history first."""
        attempts = 3 if tool in READS else 1
        for k in range(attempts):
            wait = getattr(self, "_last", 0.0) + min_interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            try:
                return self.transport(tool, args)
            except ExecutionBlocked:
                if k == attempts - 1:
                    raise
                time.sleep(3 * (k + 1))

    def account(self, quote, expected: dict[str, int] | None = None) -> Account:
        funds = self.call(CASH_INFO, {"acc_id": self.acc_id, "market": 100})["data"]
        cash = float(funds["balance"])
        rows = self.call(POSITION_LIST, {"acc_id": self.acc_id, "market": 100})["data"]["positions"]
        positions = parse_positions(rows, funds, expected)
        quotes, ages = {}, {}
        now = datetime.now(timezone.utc)
        for s in positions:
            px, at = quote(s)
            quotes[s], ages[s] = px, (now - at).total_seconds()
        total = funds.get("total_asset")
        return Account(now, cash, positions, quotes, ages,
                       broker_total=float(total) if total not in (None, "") else None)

    def place(self, intent: Intent) -> str:
        cfg = getattr(self, "cfg", None) or {}
        r = cfg.get("risk", {})                          # no config = long-only
        if entries_disabled(cfg, intent.sleeve) and not intent.close_only:
            raise ExecutionBlocked("legacy strategies are exit-only")
        if intent.side == "SELL" and not intent.close_only:
            if not (getattr(intent, "opens_short", False) and r.get("long_only") is False
                    and intent.sleeve in r.get("short_sleeves", ()) and cfg.get(intent.sleeve, {}).get("shorts")):
                raise ExecutionBlocked(f"sell must be close-only: {intent.sleeve} may not open shorts")
        if intent.side == "BUY" and getattr(intent, "opens_short", False):
            raise ExecutionBlocked("a BUY cannot open a short")
        if intent.order_type != "LIMIT" or not intent.symbol.startswith("US."):
            raise ExecutionBlocked("only U.S. LIMIT orders")
        args = {"acc_id": self.acc_id, "market": 100, "order_type": 1, "order_side": 1 if intent.side == "BUY" else 2,
                "symbol": intent.symbol, "qty": intent.qty, "price": f"{intent.limit_price:.2f}",
                "text": intent.intent_id[:60]}
        return self.call(PLACE, args)["data"]["order_id"]

    def wait(self, order_id: str, placed_at: datetime, seconds: float) -> dict:
        """Poll order history until FILLED/CANCELLED/REJECTED; cancel at the deadline and reconcile the remainder."""
        deadline = time.monotonic() + seconds
        cancelled = False
        while True:
            row = self._find(order_id, placed_at)
            if row and row["status"] in ("FILLED", "CANCELLED", "REJECTED"):
                return row
            if time.monotonic() >= deadline and not cancelled:
                self.call(CANCEL, {"acc_id": self.acc_id, "market": 100, "order_id": order_id})
                cancelled = True
                deadline = time.monotonic() + 20
            elif time.monotonic() >= deadline and cancelled:
                raise ExecutionBlocked(f"order {order_id} not terminal after cancel; reconcile before any retry")
            time.sleep(2)

    def _find(self, order_id, placed_at):
        rows = self._history(placed_at - timedelta(minutes=2))
        for row in rows:
            if str(row.get("order_id")) == str(order_id):
                qty = Decimal(str(row["qty"]))
                filled = Decimal(str(row.get("cum_qty", 0)))
                if not all(value.is_finite() and value == value.to_integral_value() for value in (qty, filled)):
                    raise ExecutionBlocked("non-integer ETF order quantity; reconcile before retrying")
                return {"order_id": str(order_id), "status": STATUS.get(row.get("status"), "UNKNOWN"),
                        "filled": int(filled), "avg_price": float(row.get("avg_fill_price", 0) or 0), "qty": int(qty)}
        return None

    def _history(self, since):
        begin = int(since.timestamp() * 1e6)
        end = int((datetime.now(timezone.utc) + timedelta(minutes=2)).timestamp() * 1e6)
        return self.call(HISTORY, {"acc_id": self.acc_id, "market": 100, "time_begin": begin, "time_end": end})["data"]["orders"]

    def reconcile_cancelled_close(self, intent: Intent, placed_at: datetime, before: Account, order_id: str, cancelled_qty: int):
        """Verify a partial close when Moomoo reports only the cancelled remainder as the order quantity."""
        filled = intent.qty - cancelled_qty
        signed_change = -filled if intent.side == "SELL" else filled
        held = before.positions.get(intent.symbol, 0)
        if not intent.close_only or not 0 < cancelled_qty < intent.qty or signed_change * held >= 0 or intent.qty > abs(held):
            raise ExecutionBlocked("invalid terminal fill; close quantity does not reconcile")
        expected = {**before.positions, intent.symbol: held + signed_change}
        expected = {sym: qty for sym, qty in expected.items() if qty}
        try:
            account = {"acc_id": self.acc_id, "market": 100}
            funds = self.call(CASH_INFO, account)["data"]
            rows = self.call(POSITION_LIST, account)["data"]["positions"]
            if any(Decimal(str(r.get("qty", r.get("quantity")))) % 1 for r in rows):
                raise ValueError("non-integer position")
            positions = parse_positions(rows, funds, expected)
            cash = Decimal(str(funds["balance"]))

            # Include orders which were already working before the close, and reject any overlapping activity.
            history = self._history(placed_at - timedelta(days=1))
            matches = [row for row in history if str(row["order_id"]) == order_id]
            if len(matches) != 1:
                raise ValueError("unique broker order missing")
            row = matches[0]
            start = int(placed_at.timestamp()) * 1_000_000
            if any(str(other["order_id"]) != order_id and
                   (STATUS.get(other["status"]) in ("WORKING", "PARTIAL", None) or
                    max(int(other["create_time"]), int(other["update_time"])) >= start) for other in history):
                raise ValueError("other order activity overlapped the close")
            limit = Decimal(f"{intent.limit_price:.2f}")
            if (STATUS.get(row["status"]) != "CANCELLED" or Decimal(str(row["qty"])) != cancelled_qty
                    or Decimal(str(row["cum_qty"])) != 0 or row["text"] != intent.intent_id[:60]
                    or row["symbol"] not in (intent.symbol, intent.symbol[3:])
                    or int(row.get("side", row.get("order_side"))) != (2 if intent.side == "SELL" else 1)
                    or Decimal(str(row["price"])) != limit
                    or not 0 < start <= int(row["create_time"]) <= int(row["update_time"])):
                raise ValueError("terminal receipt no longer matches the close")
            raw_price = row.get("reported_avg_fill_price", row.get("avg_fill_price"))
            price = Decimal(str(raw_price))
            if not price.is_finite() or price <= 0 or (price - limit) * signed_change > 0:
                raise ValueError("broker price violates the limit")

            # Cash must independently corroborate the reported average and the exact shares that disappeared.
            fee = Decimal(str(before.cash)) - cash - signed_change * price
            activity_fee = min(Decimal("8.30"), filled * Decimal("0.000166")) if signed_change < 0 else Decimal(0)
            if positions != expected or not cash.is_finite() or abs(fee - Decimal("0.99") - activity_fee) > Decimal("0.02"):
                raise ValueError("position or cash failed to corroborate the fill")
        except (InvalidOperation, KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ExecutionBlocked("invalid terminal fill; broker cancellation evidence did not reconcile") from exc

        terminal = {"status": "CANCELLED", "qty": cancelled_qty, "filled": 0, "avg_price": float(price)}
        evidence = {"positions_before": before.positions, "positions_after": positions, "cash_before": before.cash,
                    "cash_after": str(cash), "cash_corroborated_fee": str(fee)}
        return {"order_id": order_id, "status": "CANCELLED", "qty": intent.qty, "filled": filled,
                "avg_price": float(price), "broker_terminal": terminal,
                "source": "reconciled_from_broker_position_and_cash"}, evidence
