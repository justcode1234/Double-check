"""Gated broker access for the competition account. Constructed ONLY in --execute mode.

Three independent locks must all be open before an order can be sent:
  1. config/competition.yaml  execution_enabled: true
  2. private/competition/account.json exists with confirmed_competition_account: true, account_type 2, market_id 100
     (written by the user/Qoder from the Moomoo app; never committed, never guessed)
  3. the runner was started with --execute
Orders are LIMIT, long-only, U.S. only, through the same authenticated CLI transport as the practice runner, with an
allowlist of exactly one account. Every acknowledgement and fill is reconciled from broker order history.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dual_strategy.competition.planner import Account, Intent
from dual_strategy.execution.moomoo_cli import (ACCOUNT_LIST, CANCEL, CASH_INFO, HISTORY, PLACE, POSITION_LIST,
                                                CliTransport)
from dual_strategy.execution.moomoo_paper import ExecutionBlocked

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
        listed = [a for a in self.transport(ACCOUNT_LIST, {})["data"]["accounts"] if a.get("account_id") == self.acc_id]
        if len(listed) != 1 or listed[0].get("account_type") != 2:
            raise ExecutionBlocked("competition account not uniquely listed as account_type 2 by the broker")

    def account(self, quote) -> Account:
        cash = float(self.transport(CASH_INFO, {"acc_id": self.acc_id, "market": 100})["data"]["balance"])
        rows = self.transport(POSITION_LIST, {"acc_id": self.acc_id, "market": 100})["data"]["positions"]
        positions = {}
        for r in rows:
            q = int(float(r.get("qty", r.get("quantity", 0))))
            if q < 0:
                raise ExecutionBlocked(f"short position {r['symbol']} found; long-only portfolio")
            if q:
                positions[r["symbol"]] = q
        quotes, ages = {}, {}
        now = datetime.now(timezone.utc)
        for s in positions:
            px, at = quote(s)
            quotes[s], ages[s] = px, (now - at).total_seconds()
        return Account(now, cash, positions, quotes, ages)

    def place(self, intent: Intent) -> str:
        if intent.side == "SELL" and not intent.close_only:
            raise ExecutionBlocked("sells must be close-only (no shorts)")
        if intent.order_type != "LIMIT" or not intent.symbol.startswith("US."):
            raise ExecutionBlocked("only U.S. LIMIT orders")
        args = {"acc_id": self.acc_id, "market": 100, "order_type": 1, "order_side": 1 if intent.side == "BUY" else 2,
                "symbol": intent.symbol, "qty": intent.qty, "price": f"{intent.limit_price:.2f}",
                "text": intent.intent_id[:60]}
        return self.transport(PLACE, args)["data"]["order_id"]

    def wait(self, order_id: str, placed_at: datetime, seconds: float) -> dict:
        """Poll order history until FILLED/CANCELLED/REJECTED; cancel at the deadline and reconcile the remainder."""
        deadline = time.monotonic() + seconds
        cancelled = False
        while True:
            row = self._find(order_id, placed_at)
            if row and row["status"] in ("FILLED", "CANCELLED", "REJECTED"):
                return row
            if time.monotonic() >= deadline and not cancelled:
                self.transport(CANCEL, {"acc_id": self.acc_id, "market": 100, "order_id": order_id})
                cancelled = True
                deadline = time.monotonic() + 20
            elif time.monotonic() >= deadline and cancelled:
                raise ExecutionBlocked(f"order {order_id} not terminal after cancel; reconcile before any retry")
            time.sleep(2)

    def _find(self, order_id, placed_at):
        begin = int((placed_at - timedelta(minutes=2)).timestamp() * 1e6)
        end = int((datetime.now(timezone.utc) + timedelta(minutes=2)).timestamp() * 1e6)
        rows = self.transport(HISTORY, {"acc_id": self.acc_id, "market": 100, "time_begin": begin, "time_end": end})["data"]["orders"]
        for r in rows:
            if str(r.get("order_id")) == str(order_id):
                return {"order_id": str(order_id), "status": STATUS.get(r.get("status"), "UNKNOWN"),
                        "filled": int(float(r.get("cum_qty", 0))), "avg_price": float(r.get("avg_fill_price", 0) or 0),
                        "qty": int(float(r.get("qty", 0)))}
        return None
