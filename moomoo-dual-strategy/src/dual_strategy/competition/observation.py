"""Independent read-only observations of the named competition paper account."""

import math
import re
from datetime import datetime, timezone
from threading import Event

from dual_strategy.competition.broker import parse_positions
from dual_strategy.execution.moomoo_cli import ACCOUNT_LIST, CASH_INFO, POSITION_LIST, CliTransport
from dual_strategy.execution.moomoo_paper import ExecutionBlocked

COMPETITION_TITLE = "Moomoo x Qoder AI Paper Trading Agent Challenge"
POLL_SECONDS = 60


def read_account(transport_factory=CliTransport) -> dict:
    discovery = transport_factory(frozenset(), frozenset(), timeout=8)
    accounts = discovery(ACCOUNT_LIST, {})["data"]["accounts"]
    matches = [a for a in accounts if (a.get("account_title"), a.get("account_type"), a.get("market_id"))
               == (COMPETITION_TITLE, 2, 100)]
    if len(matches) != 1 or not isinstance(matches[0].get("account_id"), str) or not matches[0]["account_id"]:
        raise ExecutionBlocked("The named US competition paper account was not uniquely identified")
    account_id = matches[0]["account_id"]
    broker = transport_factory(frozenset({account_id}), frozenset(), timeout=8)
    args = {"acc_id": account_id, "market": 100}
    funds = broker(CASH_INFO, args)["data"]
    balance, nav = float(funds["balance"]), float(funds["total_asset"])
    if not all(math.isfinite(value) for value in (balance, nav)):
        raise ExecutionBlocked("Invalid competition account funds")
    rows = broker(POSITION_LIST, args)["data"]["positions"]
    for row in rows:
        qty = row.get("qty", row.get("quantity"))
        if (not re.fullmatch(r"US\.[A-Z0-9][A-Z0-9.\-]*", row["symbol"])
                or isinstance(qty, bool) or not float(qty).is_integer()):
            raise ExecutionBlocked("Invalid competition position")
    positions = parse_positions(rows, funds)
    has_longs = any(qty > 0 for qty in positions.values())
    long_mv = funds.get("long_mv")
    if long_mv not in (None, ""):
        value = float(long_mv)
        if not math.isfinite(value) or value < 0 or (value > 0.005) != has_longs:
            raise ExecutionBlocked("Competition positions disagree with the broker funds")
    if not positions and abs(nav - balance) > 0.01:
        raise ExecutionBlocked("Competition account reported exposure without positions")
    values = {}
    for row in rows:
        if row["symbol"] not in positions:
            continue
        value = row.get("mv", row.get("market_val"))
        if value not in (None, "") and math.isfinite(float(value)):
            symbol = row["symbol"]
            values[symbol] = values.get(symbol, 0.0) + abs(float(value))
    return {"at": datetime.now(timezone.utc).isoformat(), "cash": balance, "nav": nav,
            "positions": positions, "market_values": values}


class AccountObserver:
    def __init__(self):
        self.snapshot = None

    def run(self, stop: Event):
        while not stop.is_set():
            try:
                self.snapshot = {"status": "verified", **read_account()}
            except (ExecutionBlocked, OSError, ValueError, TypeError, KeyError, AttributeError):
                # Keep the last verified observation and mark it stale on failure.
                self.snapshot = {**(self.snapshot or {}), "status": "unavailable"}
            stop.wait(POLL_SECONDS)
