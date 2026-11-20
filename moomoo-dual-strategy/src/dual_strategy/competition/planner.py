"""Deterministic risk state, sizing and order intents for the B3 80% / H 20% competition portfolio. No broker I/O.

State (persisted by `store.py`, never reset by code): initial NAV, high-water NAV, day-start NAV, the account halt
(3.5% drawdown, permanent until a human clears it), the daily loss lock, the half-size flag, today's order count and
the sleeve that owns every share (B3 and H can both hold SPY, so ownership is tracked per sleeve and must sum to the
broker position before any entry is allowed).
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import datetime

SLEEVES = ("b3", "h_etf")


@dataclass
class Account:
    """Broker truth at one moment: cash, share positions and the quotes used to value and price them."""
    at: datetime
    cash: float
    positions: dict[str, int]
    quotes: dict[str, float]
    quote_age_seconds: dict[str, float] = field(default_factory=dict)

    @property
    def nav(self) -> float:
        return self.cash + sum(q * self.quotes[s] for s, q in self.positions.items() if q)

    @property
    def gross(self) -> float:
        return sum(abs(q) * self.quotes[s] for s, q in self.positions.items() if q)

    def missing_quotes(self) -> list[str]:
        return [s for s, q in self.positions.items() if q and not (s in self.quotes and self.quotes[s] > 0)]


@dataclass
class Intent:
    intent_id: str
    created_at: str
    sleeve: str
    symbol: str
    side: str                 # BUY | SELL
    qty: int
    limit_price: float
    reference_price: float
    reason: str
    close_only: bool
    order_type: str = "LIMIT"
    time_in_force: str = "DAY"

    def to_dict(self):
        return asdict(self)


def new_state() -> dict:
    return {"version": 1, "initial_nav": None, "high_water": None, "day": None, "day_start_nav": None,
            "halted": None, "locked_day": None, "reduced": False, "orders_today": 0,
            "sleeves": {"b3": {}, "h_etf": {}}, "h_entry_session": {}, "h_plan": None, "b3_done": [], "events": []}


def _event(state, at, kind, **data):
    state["events"] = (state.get("events") or [])[-199:] + [{"at": at.isoformat(), "kind": kind, **data}]


def mark_risk(state: dict, account: Account, session: str, cfg: dict) -> str | None:
    """Update the account risk state from broker NAV. Returns a flatten reason when everything must be closed now."""
    r = cfg["risk"]
    nav = account.nav
    if not math.isfinite(nav) or nav <= 0 or account.missing_quotes():
        return None                                         # cannot value the account: entries are blocked elsewhere
    if state["initial_nav"] is None:
        state["initial_nav"] = state["high_water"] = nav
        _event(state, account.at, "initialized", nav=nav)
    if state["day"] != session:
        state["day"], state["day_start_nav"], state["orders_today"], state["b3_done"] = session, nav, 0, []
        _event(state, account.at, "session_start", nav=nav)
    state["high_water"] = max(state["high_water"], nav)
    state["last_nav"], state["last_mark_at"] = nav, account.at.isoformat()
    hw = state["high_water"]
    was = state["reduced"]
    state["reduced"] = nav <= hw * (1 - r["reduce_drawdown_fraction"] + (0.005 if was else 0.0))
    if state["reduced"] != was:
        _event(state, account.at, "half_size_on" if state["reduced"] else "half_size_off", nav=nav, high_water=hw)
    if state["halted"] is None and nav <= hw * (1 - r["halt_drawdown_fraction"]):
        state["halted"] = {"at": account.at.isoformat(), "nav": nav, "high_water": hw,
                           "reason": f"NAV {nav:.2f} <= {100 * (1 - r['halt_drawdown_fraction']):.1f}% of high-water {hw:.2f}"}
        _event(state, account.at, "drawdown_halt", nav=nav, high_water=hw)
        return "drawdown_halt"
    if state["locked_day"] != session and nav <= state["day_start_nav"] * (1 - r["daily_loss_fraction"]):
        state["locked_day"] = session
        _event(state, account.at, "daily_loss_lock", nav=nav, day_start=state["day_start_nav"])
        return "daily_loss_lock"
    if state["halted"] is not None:
        return "drawdown_halt" if any(account.positions.values()) else None
    if state["locked_day"] == session:
        return "daily_loss_lock" if any(account.positions.values()) else None
    return None


def attribution_errors(state: dict, account: Account) -> list[str]:
    owned = {}
    for sleeve in SLEEVES:
        for s, q in state["sleeves"][sleeve].items():
            owned[s] = owned.get(s, 0) + q
    errors = []
    for s in set(owned) | {s for s, q in account.positions.items() if q}:
        if owned.get(s, 0) != account.positions.get(s, 0):
            errors.append(f"{s}: sleeves {owned.get(s, 0)} vs broker {account.positions.get(s, 0)}")
    return errors


def entry_block(state: dict, account: Account, session: str, cfg: dict) -> str | None:
    r = cfg["risk"]
    if state["halted"] is not None:
        return "drawdown_halt (human must clear private state)"
    if state["locked_day"] == session:
        return "daily_loss_lock"
    if state["orders_today"] >= r["max_orders_per_day"] - 4:
        return "order_budget"
    if account.missing_quotes():
        return f"missing quotes {account.missing_quotes()}"
    stale = [s for s, a in account.quote_age_seconds.items() if a > r["max_quote_age_seconds"]]
    if stale:
        return f"stale quotes {stale}"
    errs = attribution_errors(state, account)
    if errs:
        return "position attribution mismatch: " + "; ".join(errs)
    return None


def _limit(price, side, cfg):
    off = cfg["risk"]["limit_offset_bps"] / 1e4
    return round(price * (1 + off), 2) if side == "BUY" else round(price * (1 - off), 2)


def _intent(now, session, sleeve, symbol, side, qty, ref, reason, cfg, close_only, tag):
    return Intent(f"{session}-{sleeve}-{tag}-{symbol.split('.')[-1]}-{side}", now.isoformat(), sleeve, symbol, side, int(qty),
                  _limit(ref, side, cfg), ref, reason, close_only)


def plan_flatten(state: dict, account: Account, session: str, reason: str, cfg: dict, now: datetime,
                 sleeves=SLEEVES) -> list[Intent]:
    out = []
    for sleeve in sleeves:
        for s, q in sorted(state["sleeves"][sleeve].items()):
            if q > 0 and s in account.quotes:
                out.append(_intent(now, session, sleeve, s, "SELL", q, account.quotes[s], reason, cfg, True,
                                   f"flat{now.strftime('%H%M')}"))
    return out


def plan_b3(state: dict, account: Account, session: str, decision, cfg: dict, now: datetime) -> tuple[list[Intent], str]:
    """Decision -> at most one SPY order for the B3 sleeve."""
    c, sym = cfg["b3"], cfg["b3"]["symbol"]
    held = state["sleeves"]["b3"].get(sym, 0)
    tag = decision.at.strftime("%H%M")
    if decision.target is None:
        return [], f"blocked: {decision.reason}"
    price = account.quotes.get(sym)
    if not price or price <= 0:
        return [], "blocked: no SPY quote"
    if decision.target == 0:
        if held > 0:
            return [_intent(now, session, "b3", sym, "SELL", held, price, "b3_target_flat", cfg, True, tag)], "exit"
        return [], "flat, nothing held"
    if held > 0:
        return [], "long, already held"
    why = entry_block(state, account, session, cfg)
    if why:
        return [], f"entry blocked: {why}"
    factor = 0.5 if state["reduced"] else 1.0
    budget = min(cfg["allocation"]["b3"] * c["position_fraction_of_sleeve"] * account.nav * factor,
                 cfg["risk"]["name_cap_usd"], max(0.0, account.cash - 0.99), max(0.0, account.nav - account.gross))
    limit = _limit(price, "BUY", cfg)
    qty = math.floor(budget / max(limit, decision.metrics.get("open_0930", limit)))
    if qty <= 0:
        return [], "entry size zero"
    return [_intent(now, session, "b3", sym, "BUY", qty, price, "b3_target_long", cfg, False, tag)], "entry"


def plan_h_open(state: dict, account: Account, session: str, plan: dict, cfg: dict, now: datetime) -> tuple[list[Intent], str]:
    """Next-open orders from the last close's H plan: exits first, then entries within the H sleeve limits."""
    c = cfg["h_etf"]
    out = []
    held = state["sleeves"]["h_etf"]
    for s, why in sorted(plan.get("exits", {}).items()):
        q = held.get(s, 0)
        if q > 0 and s in account.quotes:
            out.append(_intent(now, session, "h_etf", s, "SELL", q, account.quotes[s], f"h_{why}", cfg, True, "open"))
    entries = [s for s in plan.get("entries", []) if held.get(s, 0) == 0]
    if not entries:
        return out, "no entries"
    why = entry_block(state, account, session, cfg)
    if why:
        return out, f"entries blocked: {why}"
    factor = 0.5 if state["reduced"] else 1.0
    sleeve = cfg["allocation"]["h_etf"] * account.nav
    exiting = {i.symbol for i in out}
    gross = sum(q * account.quotes.get(s, 0.0) for s, q in held.items() if s not in exiting)
    room = c["aggregate_fraction_of_sleeve"] * sleeve - gross
    cash = min(account.cash - 0.99, account.nav - account.gross)          # no leverage
    for s in entries:
        price = account.quotes.get(s)
        if not price or price <= 0:
            continue
        limit = _limit(price, "BUY", cfg)
        budget = min(c["per_name_fraction_of_sleeve"] * sleeve * factor, cfg["risk"]["name_cap_usd"], room, cash)
        qty = math.floor(budget / limit)
        if qty <= 0:
            continue
        out.append(_intent(now, session, "h_etf", s, "BUY", qty, price, "h_rsi2_entry", cfg, False, "open"))
        room -= qty * limit
        cash -= qty * limit + 0.99
    return out, "planned"


def apply_fill(state: dict, intent: Intent | dict, filled: int, session: str) -> None:
    """Credit filled shares to the sleeve that placed the order (broker positions remain the source of truth)."""
    i = intent if isinstance(intent, dict) else intent.to_dict()
    if filled <= 0:
        return
    book = state["sleeves"][i["sleeve"]]
    sign = 1 if i["side"] == "BUY" else -1
    book[i["symbol"]] = book.get(i["symbol"], 0) + sign * filled
    if book[i["symbol"]] <= 0:
        book.pop(i["symbol"], None)
        if i["sleeve"] == "h_etf":
            state["h_entry_session"].pop(i["symbol"], None)
    elif i["sleeve"] == "h_etf" and sign > 0:
        state["h_entry_session"].setdefault(i["symbol"], session)
