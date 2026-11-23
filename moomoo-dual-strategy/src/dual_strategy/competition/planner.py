"""Deterministic risk state, sizing and order intents for the five-sleeve competition portfolio. No broker I/O.

State (persisted by `store.py`, never reset by code): initial NAV, high-water NAV, day-start NAV, the account halt
(3.5% drawdown, permanent until a human clears it), the daily loss lock, the half-size flag, today's order count and
the sleeve that owns every share. Several sleeves can hold the same ETF (SPY can be held by all five), so ownership is
tracked per sleeve and must sum to the broker position before any entry is allowed. Only the sleeves listed in
risk.short_sleeves may hold negative quantities (shorts); shorts are covered by close-only BUYs and never kept overnight.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import datetime

SLEEVES = ("b3", "h_etf", "noise", "vwap", "orb")
INTRADAY = ("b3", "noise", "vwap", "orb")         # must be flat at every close


@dataclass
class Account:
    """Broker truth at one moment: cash, share positions and the quotes used to value and price them."""
    at: datetime
    cash: float
    positions: dict[str, int]
    quotes: dict[str, float]
    quote_age_seconds: dict[str, float] = field(default_factory=dict)
    sleeve_capital: dict[str, float | None] = field(default_factory=dict)
    broker_total: float | None = None            # broker total_asset, used only to sanity-check the runner NAV

    @property
    def nav(self) -> float:
        return self.cash + sum(q * self.quotes[s] for s, q in self.positions.items() if q)

    @property
    def gross(self) -> float:
        return sum(abs(q) * self.quotes[s] for s, q in self.positions.items() if q)

    def missing_quotes(self) -> list[str]:
        return [s for s, q in self.positions.items()
                if q and not (math.isfinite(self.quotes.get(s, 0)) and self.quotes.get(s, 0) > 0)]


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
    opens_short: bool = False  # a SELL that opens or adds to a short (only for risk.short_sleeves)

    def to_dict(self):
        return asdict(self)


def new_state() -> dict:
    return {"version": 1, "initial_nav": None, "high_water": None, "day": None, "day_start_nav": None,
            "halted": None, "locked_day": None, "reduced": False, "orders_today": 0,
            "sleeves": {s: {} for s in SLEEVES}, "h_entry_session": {}, "h_plan": None, "b3_done": [], "events": []}


def _event(state, at, kind, **data):
    state["events"] = (state.get("events") or [])[-199:] + [{"at": at.isoformat(), "kind": kind, **data}]


def mark_risk(state: dict, account: Account, session: str, cfg: dict) -> str | None:
    """Update the account risk state from broker NAV. Returns a flatten reason when everything must be closed now."""
    r = cfg["risk"]
    if account.missing_quotes():
        return None
    nav = account.nav
    if not math.isfinite(nav) or nav <= 0:
        return None                                         # cannot value the account: entries are blocked elsewhere
    bt = account.broker_total
    if type(bt) in (int, float) and math.isfinite(bt) and bt > 0:
        if abs(nav - bt) > r.get("nav_check_fraction", 0.01) * bt:
            state["nav_mismatch"] = {"at": account.at.isoformat(), "runner_nav": nav, "broker_total": bt}
            return None                                     # never halt or lock on a NAV the broker does not confirm
    state["nav_mismatch"] = None
    if state["initial_nav"] is None:
        state["initial_nav"] = state["high_water"] = nav
        _event(state, account.at, "initialized", nav=nav)
    if state["day"] != session:
        state["day"], state["day_start_nav"], state["orders_today"], state["b3_done"] = session, nav, 0, []
        state["order_errors_today"] = 0
        _event(state, account.at, "session_start", nav=nav)
    state["high_water"] = max(state["high_water"], nav)
    state["last_nav"], state["last_mark_at"] = nav, account.at.isoformat()
    hw = state["high_water"]
    was = state["reduced"]
    state["reduced"] = nav <= hw * (1 - r["reduce_drawdown_fraction"] + (0.005 if was else 0.0))
    if state["reduced"] != was:
        _event(state, account.at, "half_size_on" if state["reduced"] else "half_size_off", nav=nav, high_water=hw)
    if state["halted"] is None and nav <= hw * (1 - r["halt_drawdown_fraction"]):
        state["halted"] = {"kind": "drawdown", "at": account.at.isoformat(), "nav": nav, "high_water": hw,
                           "reason": f"NAV {nav:.2f} <= {100 * (1 - r['halt_drawdown_fraction']):.1f}% of high-water {hw:.2f}"}
        _event(state, account.at, "drawdown_halt", nav=nav, high_water=hw)
        return "drawdown_halt"
    if state["locked_day"] != session and nav <= state["day_start_nav"] * (1 - r["daily_loss_fraction"]):
        state["locked_day"] = session
        _event(state, account.at, "daily_loss_lock", nav=nav, day_start=state["day_start_nav"])
        return "daily_loss_lock"
    if state["halted"] is not None:
        # Only the drawdown halt keeps forcing exits. An order-error halt blocks entries and waits for a human:
        # re-selling every 30 s on possibly wrong ownership after an ambiguous order could compound the error.
        if state["halted"].get("kind", "drawdown") == "drawdown" and any(account.positions.values()):
            return "drawdown_halt"
        return None
    if state["locked_day"] == session:
        return "daily_loss_lock" if any(account.positions.values()) else None
    return None


def attribution_errors(state: dict, account: Account) -> list[str]:
    owned = {}
    for sleeve in SLEEVES:
        for s, q in state["sleeves"].get(sleeve, {}).items():
            owned[s] = owned.get(s, 0) + q
    errors = []
    for sleeve, book in state["sleeves"].items():
        if sleeve not in SLEEVES:
            errors.append(f"unknown sleeve {sleeve}")
        for s, q in book.items():
            if q < 0 and sleeve not in short_sleeves_of(state):
                errors.append(f"{sleeve} short {s} {q} but the sleeve may not short")
    for s in set(owned) | {s for s, q in account.positions.items() if q}:
        if owned.get(s, 0) != account.positions.get(s, 0):
            errors.append(f"{s}: sleeves {owned.get(s, 0)} vs broker {account.positions.get(s, 0)}")
    return errors


def entries_disabled(cfg: dict, sleeve: str | None) -> bool:
    risk = cfg.get("risk", {})
    return bool(risk.get("legacy_exit_only") and sleeve not in risk.get("entry_sleeves", ()))


def entry_block(state: dict, account: Account, session: str, cfg: dict, sleeve: str | None = None) -> str | None:
    r = cfg["risk"]
    if entries_disabled(cfg, sleeve):
        return "legacy strategies are exit-only"
    if state["halted"] is not None:
        return "drawdown_halt (human must clear private state)"
    if state["locked_day"] == session:
        return "daily_loss_lock"
    if state["orders_today"] >= r["max_orders_per_day"] - 4:
        return "order_budget"
    if account.missing_quotes():
        return f"missing quotes {account.missing_quotes()}"
    if not math.isfinite(account.nav) or account.nav <= 0:
        return "unverified account NAV"
    stale = [s for s, a in account.quote_age_seconds.items() if not -5 <= a <= r["max_quote_age_seconds"]]
    if stale:
        return f"stale quotes {stale}"
    errs = attribution_errors(state, account)
    if errs:
        return "position attribution mismatch: " + "; ".join(errs)
    if state.get("nav_mismatch"):
        m = state["nav_mismatch"]
        return f"runner NAV {m['runner_nav']:.2f} disagrees with broker total {m['broker_total']:.2f}"
    return None


def _limit(price, side, cfg):
    off = cfg["risk"]["limit_offset_bps"] / 1e4
    return round(price * (1 + off), 2) if side == "BUY" else round(price * (1 - off), 2)


def _intent(now, session, sleeve, symbol, side, qty, ref, reason, cfg, close_only, tag):
    return Intent(f"{session}-{sleeve}-{tag}-{symbol.split('.')[-1]}-{side}", now.isoformat(), sleeve, symbol, side, int(qty),
                  _limit(ref, side, cfg), ref, reason, close_only)


def plan_flatten(state: dict, account: Account, session: str, reason: str, cfg: dict, now: datetime,
                 sleeves=SLEEVES) -> list[Intent]:
    """Close-only orders for everything the given sleeves own: short covers (BUY) first, then long sales (SELL), so no
    single order crosses the broker's net position through zero. With consistent attribution each sleeve closes its own
    quantity (sleeves net at the broker); otherwise the order is capped at what the broker shows on that side."""
    consistent = not attribution_errors(state, account)
    covers, sells = [], []
    for sleeve in sleeves:
        for s, q in sorted(state["sleeves"].get(sleeve, {}).items()):
            if not q or s not in account.quotes:
                continue
            broker = account.positions.get(s, 0)
            if q < 0:
                n = -q if consistent else min(-q, max(0, -broker))
                if n > 0:
                    covers.append(_intent(now, session, sleeve, s, "BUY", n, account.quotes[s], reason, cfg, True,
                                          f"flat{now.strftime('%H%M%S')}"))
            else:
                n = q if consistent else min(q, max(0, broker))   # never sell more than the broker shows
                if n > 0:
                    sells.append(_intent(now, session, sleeve, s, "SELL", n, account.quotes[s], reason, cfg, True,
                                         f"flat{now.strftime('%H%M%S')}"))
    return covers + sells


def exposure(state: dict, symbol: str, price: float) -> float:
    """The handbook per-symbol cap counts every sleeve, longs and shorts by absolute value."""
    return sum(abs(book.get(symbol, 0)) for book in state["sleeves"].values()) * price


def short_sleeves_of(state_or_cfg) -> tuple:
    r = (state_or_cfg or {}).get("risk") if isinstance(state_or_cfg, dict) and "risk" in state_or_cfg else None
    return tuple(r.get("short_sleeves", ())) if r and not r.get("long_only", True) else SHORTABLE


SHORTABLE = ("noise", "vwap", "orb")


def cash_room(account: Account) -> float:
    """No leverage: new gross notional must fit in cash, and a short reserves its full notional as collateral."""
    return max(0.0, min(account.cash - 0.99, account.nav - account.gross))


def capital_limits(sleeve: str, capital: float | None, cfg: dict, reduced: bool) -> dict:
    capital = capital if type(capital) in (int, float) and math.isfinite(capital) else None
    limits = {"allocation_budget": capital, "target_before_cap": None, "per_etf_budget": None, "aggregate_limit": None}
    if capital is None:
        return limits
    available = max(0.0, capital)
    factor = 0.5 if reduced else 1.0
    if sleeve == "b3":
        limits["target_before_cap"] = available * cfg["b3"]["position_fraction_of_sleeve"] * factor
    elif sleeve == "h_etf":
        limits["per_etf_budget"] = available * cfg["h_etf"]["per_name_fraction_of_sleeve"] * factor
        limits["aggregate_limit"] = available * cfg["h_etf"]["aggregate_fraction_of_sleeve"]
    elif sleeve == "noise":                                # 100% of the sleeve per entry, like B3
        limits["target_before_cap"] = available * cfg["noise"]["position_fraction_of_sleeve"] * factor
    elif sleeve == "orb":                                  # the reference's 10% cap per trade, within the sleeve
        limits["per_trade_notional"] = min(available, cfg["orb"]["max_notional"]) * factor
        limits["aggregate_limit"] = available
    else:                                                  # vwap: one trade's notional never exceeds the sleeve
        limits["per_trade_notional"] = available * factor
        limits["aggregate_limit"] = available
    return limits


def plan_b3(state: dict, account: Account, session: str, decision, cfg: dict, now: datetime) -> tuple[list[Intent], str]:
    """Decision -> at most one SPY order for the B3 sleeve."""
    sym = cfg["b3"]["symbol"]
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
    why = entry_block(state, account, session, cfg, "b3")
    if why:
        return [], f"entry blocked: {why}"
    limits = capital_limits("b3", account.sleeve_capital.get("b3"), cfg, state["reduced"])
    if limits["allocation_budget"] is None or limits["allocation_budget"] <= 0:
        return [], "entry blocked: unverified or depleted B3 capital"
    held_value = exposure(state, sym, price)                                 # SPY other sleeves hold, long or short
    budget = min(limits["target_before_cap"], cfg["risk"]["name_cap_usd"] - held_value, cash_room(account))
    limit = _limit(price, "BUY", cfg)
    qty = math.floor(budget / max(limit, decision.metrics.get("open_0930", limit)))
    if qty <= 0:
        return [], "entry size zero"
    return [_intent(now, session, "b3", sym, "BUY", qty, price, "b3_target_long", cfg, False, tag)], "entry"


def plan_h_open(state: dict, account: Account, session: str, plan: dict, cfg: dict, now: datetime) -> tuple[list[Intent], str]:
    """Next-open orders from the last close's H plan: exits first, then entries within the H sleeve limits."""
    out = []
    held = state["sleeves"]["h_etf"]
    for s, why in sorted(plan.get("exits", {}).items()):
        q = held.get(s, 0)
        if q > 0 and s in account.quotes:
            out.append(_intent(now, session, "h_etf", s, "SELL", q, account.quotes[s], f"h_{why}", cfg, True, "open"))
    entries = [s for s in dict.fromkeys(plan.get("entries", [])) if held.get(s, 0) == 0]
    if not entries:
        return out, "no entries"
    why = entry_block(state, account, session, cfg, "h_etf")
    if why:
        return out, f"entries blocked: {why}"
    limits = capital_limits("h_etf", account.sleeve_capital.get("h_etf"), cfg, state["reduced"])
    if limits["allocation_budget"] is None or limits["allocation_budget"] <= 0:
        return out, "entries blocked: unverified or depleted H capital"
    exiting = {i.symbol for i in out}
    gross = sum(q * account.quotes.get(s, 0.0) for s, q in held.items() if s not in exiting)
    room = limits["aggregate_limit"] - gross
    cash = min(account.cash - 0.99, account.nav - account.gross)          # no leverage
    for s in entries:
        price = account.quotes.get(s)
        if price is None or not math.isfinite(price) or price <= 0:
            continue
        limit = _limit(price, "BUY", cfg)
        held_value = exposure(state, s, max(price, limit))                # every sleeve counts toward the cap
        budget = min(limits["per_etf_budget"], cfg["risk"]["name_cap_usd"] - held_value, room, cash)
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
    book = state["sleeves"].setdefault(i["sleeve"], {})
    sign = 1 if i["side"] == "BUY" else -1
    q = book.get(i["symbol"], 0) + sign * filled
    if q < 0 and i["sleeve"] not in SHORTABLE:
        q = 0                                             # only short-capable sleeves can ever hold a negative book
    book[i["symbol"]] = q
    if q == 0:
        book.pop(i["symbol"], None)
        if i["sleeve"] == "h_etf":
            state["h_entry_session"].pop(i["symbol"], None)
    elif i["sleeve"] == "h_etf" and sign > 0:
        state["h_entry_session"].setdefault(i["symbol"], session)


# ------------------------------------------------------------------ Noise-area / VWAP / ORB (intraday sleeves)
def shorts_allowed(sleeve: str, cfg: dict) -> bool:
    r = cfg["risk"]
    return (r.get("long_only") is False and sleeve in r.get("short_sleeves", ()) and bool(cfg.get(sleeve, {}).get("shorts")))


def no_entry_reason(sleeve: str, session: str, now: datetime, cfg: dict) -> str | None:
    """Dated config gates (spec section 4): no entries before a clock time, or none at all, on given sessions."""
    c = cfg.get(sleeve, {})
    if session in (c.get("no_entry_sessions") or []):
        return f"no {sleeve} entries on {session} (config)"
    before = (c.get("no_entries_before") or {}).get(session)
    if before and now.strftime("%H:%M") < before:
        return f"no {sleeve} entries before {before} on {session} (config)"
    after = (c.get("no_entries_after") or {}).get(session)
    if after and now.strftime("%H:%M") >= after:
        return f"no {sleeve} entries after {after} on {session} (config)"
    return None


def plan_entry(state: dict, account: Account, session: str, sleeve: str, symbol: str, side: int, risk_qty: int,
               ref_price: float, reason: str, cfg: dict, now: datetime, tag: str) -> tuple[list[Intent], str]:
    """One VWAP/ORB entry: research risk size, then capped by the sleeve's capital, the cross-sleeve per-symbol cap and
    cash (shorts reserve their full notional). Half size applies to both the risk size and the notional cap."""
    if side < 0 and not shorts_allowed(sleeve, cfg):
        return [], f"entry blocked: {sleeve} may not short"
    why = entry_block(state, account, session, cfg, sleeve) or no_entry_reason(sleeve, session, now, cfg)
    if why:
        return [], f"entry blocked: {why}"
    if state["sleeves"].get(sleeve, {}).get(symbol):
        return [], f"entry blocked: {sleeve} already holds {symbol}"
    limits = capital_limits(sleeve, account.sleeve_capital.get(sleeve), cfg, state["reduced"])
    if limits["allocation_budget"] is None or limits["allocation_budget"] <= 0:
        return [], f"entry blocked: unverified or depleted {sleeve} capital"
    order_side = "BUY" if side > 0 else "SELL"
    limit = _limit(ref_price, order_side, cfg)
    open_gross = sum(abs(q) * account.quotes.get(s, 0.0) for s, q in state["sleeves"].get(sleeve, {}).items())
    budget = min(limits["per_trade_notional"], limits["aggregate_limit"] - open_gross,
                 cfg["risk"]["name_cap_usd"] - exposure(state, symbol, ref_price), cash_room(account))
    factor = 0.5 if state["reduced"] else 1.0
    qty = min(int(risk_qty * factor), math.floor(max(0.0, budget) / max(limit, ref_price)))
    if qty <= 0:
        return [], "entry size zero"
    i = _intent(now, session, sleeve, symbol, order_side, qty, ref_price, reason, cfg, False, tag)
    i.opens_short = side < 0
    return [i], "entry"


def plan_close(state: dict, account: Account, session: str, sleeve: str, symbol: str, reason: str, cfg: dict,
               now: datetime, tag: str, qty: int | None = None) -> list[Intent]:
    """Close a sleeve position (stop / target / time): SELL a long, BUY back a short. `qty` (signed, the position to
    remove) closes part of a position (the ORB half at the target)."""
    q = state["sleeves"].get(sleeve, {}).get(symbol, 0) if qty is None else qty
    price = account.quotes.get(symbol)
    if not q or not price:
        return []
    return [_intent(now, session, sleeve, symbol, "SELL" if q > 0 else "BUY", abs(q), price, reason, cfg, True, tag)]


def plan_target(state: dict, account: Account, session: str, sleeve: str, symbol: str, target: int, open0: float,
                ref_price: float, reason: str, cfg: dict, now: datetime, tag: str) -> tuple[list[Intent], str]:
    """Noise-area: act only when the target side changes. Close the current position (close-only), then open the new
    side sized at the sleeve's capital / max(price, 09:30 open), within the shared per-symbol cap and cash."""
    held = state["sleeves"].get(sleeve, {}).get(symbol, 0)
    cur = (held > 0) - (held < 0)
    if target == cur:
        return [], "unchanged"
    out = []
    if held:
        out += plan_close(state, account, session, sleeve, symbol, f"{reason}_exit", cfg, now, f"{tag}x")
    if target == 0:
        return out, "exit to flat"
    if entries_disabled(cfg, sleeve):
        return out, "entry blocked: legacy strategies are exit-only"
    if target < 0 and not shorts_allowed(sleeve, cfg):
        return out, f"entry blocked: {sleeve} may not short"
    why = entry_block(state, account, session, cfg, sleeve) if not held else None
    why = why or no_entry_reason(sleeve, session, now, cfg)
    if why:
        return out, f"entry blocked: {why}"
    limits = capital_limits(sleeve, account.sleeve_capital.get(sleeve), cfg, state["reduced"])
    if limits["allocation_budget"] is None or limits["allocation_budget"] <= 0:
        return out, f"entry blocked: unverified or depleted {sleeve} capital"
    side = "BUY" if target > 0 else "SELL"
    limit = _limit(ref_price, side, cfg)
    others = exposure(state, symbol, ref_price) - abs(held) * ref_price      # this sleeve's old side is being closed
    budget = min(limits["target_before_cap"], cfg["risk"]["name_cap_usd"] - others,
                 cash_room(account) + abs(held) * ref_price)
    qty = math.floor(max(0.0, budget) / max(limit, open0, ref_price))
    if qty <= 0:
        return out, "entry size zero"
    i = _intent(now, session, sleeve, symbol, side, qty, ref_price, reason, cfg, False, tag)
    i.opens_short = target < 0
    return out + [i], "entry"
