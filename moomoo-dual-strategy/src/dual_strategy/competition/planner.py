"""Deterministic risk state, sizing and order intents for the competition portfolio. No broker I/O.

State (persisted by `store.py`, never reset by code): initial NAV, high-water NAV, day-start NAV, the account halt
(3.5% drawdown, permanent until a human clears it), the daily loss lock, the half-size flag, today's order count and
the sleeve that owns every share. Several sleeves can hold the same ETF (SPY can be held by all five), so ownership is
tracked per sleeve and must sum to the broker position before any entry is allowed. Only the sleeves listed in
risk.short_sleeves may hold negative quantities (shorts); shorts are covered by close-only BUYs and never kept overnight.

Long sales only at a profit (risk.long_sales_only_at_profit, user decision 2026-11-25): no order ever sells a long at a
loss, whatever asked for it (opposite votes, a scheduled flatten, an H exit, a risk lock or halt, a manual flatten).
A sale is allowed only when its worst-case fill (the SELL limit; a limit never fills lower) nets more than
risk.min_long_sale_net_usd after the entry order fee and the sale's order and activity fees, per entry lot (Two-vote
lots are judged on their own entry price and size, so old $10k lots and new $20k lots each have their own break-even).
A losing long is kept, overnight if needed; shorts are still covered at a loss.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime

SLEEVES = ("b3", "h_etf", "noise", "vwap", "orb", "two_vote")
INTRADAY = ("b3", "noise", "vwap", "orb", "two_vote")         # flattened at every close (Two-vote: only lots in profit)

ORDER_FEE = 0.99                     # Moomoo US: $0.99 per order (broker.py corroborates it against cash on every fill)


def sale_fee(shares: int) -> float:
    """Order fee plus the regulatory activity fee on a sale: $0.000166 per share, at most $8.30 (broker.py)."""
    return ORDER_FEE + min(0.000166 * abs(shares), 8.30)


def long_sale_net(entry_price: float, shares: int, sale_price: float) -> float:
    """Worst-case net P&L of selling `shares` bought at `entry_price` at `sale_price`: price gain minus the entry order
    fee and the sale's fees."""
    return shares * (sale_price - entry_price) - ORDER_FEE - sale_fee(shares)


def loss_guard(cfg: dict) -> bool:
    return cfg.get("risk", {}).get("long_sales_only_at_profit") is True


def min_sale_net(cfg: dict) -> float:
    v = cfg.get("risk", {}).get("min_long_sale_net_usd", 0.0)
    return float(v) if type(v) in (int, float) and math.isfinite(v) and v >= 0 else 0.0


def break_even_quote(entry_price: float, shares: int, cfg: dict) -> float:
    """Lowest quote (in cents) at which the marketable SELL limit planned from it (_limit: quote - limit_offset_bps,
    rounded to the cent) nets > min_sale_net for `shares` bought at `entry_price`: exactly the rule the guard applies."""
    off = cfg["risk"]["limit_offset_bps"] / 1e4
    need = entry_price + (ORDER_FEE + sale_fee(shares) + min_sale_net(cfg)) / shares
    quote = round(math.floor(need / (1 - off) * 100) / 100 - 0.03, 2)
    while not long_sale_net(entry_price, shares, _limit(quote, "SELL", cfg)) > min_sale_net(cfg):
        quote = round(quote + 0.01, 2)
    return quote


def basis_view(sleeves: dict) -> dict:
    """The part of store.sleeve_performance()["sleeves"] the profit-only rule reads (audited with every decision, so
    the decision replays exactly): status, positions (qty, cost_basis) and Two-vote entry lots."""
    return {name: {"status": book.get("status"),
                   "positions": {sym: {"qty": p.get("qty"), "cost_basis": p.get("cost_basis")}
                                 for sym, p in (book.get("positions") or {}).items()},
                   "entry_lots": {sym: [{"id": lot["id"], "shares": lot["shares"], "entry_price": lot["entry_price"],
                                         "side": lot["side"]} for lot in lots]
                                  for sym, lots in (book.get("entry_lots") or {}).items()}}
            for name, book in (sleeves or {}).items()}


def long_lots(sleeve: str, symbol: str, basis: dict | None) -> list[dict] | None:
    """Open long entry lots [{id, shares, entry_price}] from confirmed receipts (store.sleeve_performance): Two-vote's
    identified lots, oldest first; other sleeves one lot at their weighted average cost. None when unverified."""
    book = (basis or {}).get(sleeve)
    if not book or book.get("status") != "available":
        return None
    if sleeve == "two_vote":
        lots = (book.get("entry_lots") or {}).get(symbol)
        if not lots or any(lot.get("side") != 1 for lot in lots):
            return None
        return [{"id": lot["id"], "shares": lot["shares"], "entry_price": lot["entry_price"]} for lot in lots]
    position = (book.get("positions") or {}).get(symbol)
    if not position or position.get("qty", 0) <= 0:
        return None
    return [{"id": None, "shares": position["qty"], "entry_price": position["cost_basis"] / position["qty"]}]


def long_sale_block(state: dict, sleeve: str, symbol: str, qty: int, limit_price: float, cfg: dict, basis: dict | None,
                    lot_id: str | None = None) -> str | None:
    """Why selling `qty` of a long at `limit_price` is not allowed (None: allowed). The named lot, else the oldest lots
    first (the store's attribution), each judged alone: one losing lot blocks the whole order."""
    if not loss_guard(cfg) or state["sleeves"].get(sleeve, {}).get(symbol, 0) <= 0:
        return None
    lots = long_lots(sleeve, symbol, basis)
    if lots is None:
        return "cost basis unverified: a long is never sold without proof the sale is profitable"
    if lot_id is not None:
        lots = [lot for lot in lots if lot["id"] == lot_id]
        if len(lots) != 1 or qty > lots[0]["shares"]:
            return f"lot {lot_id} not open for {qty} shares"
    left = qty
    for lot in lots:
        if left <= 0:
            break
        n = min(left, lot["shares"])
        net = long_sale_net(lot["entry_price"], n, limit_price)
        if not net > min_sale_net(cfg):
            return (f"would sell {n} {symbol} bought at {lot['entry_price']:.4f} at a limit of {limit_price:.2f}: "
                    f"net {net:+.2f} after fees (long sales only at a profit)")
        left -= n
    return "close exceeds the verified lots" if left > 0 else None


def profitable_long_sales(state: dict, account: "Account", session: str, sleeve: str, symbol: str, reason: str,
                          cfg: dict, now: datetime, tag: str, basis: dict | None) -> tuple[list["Intent"], list[dict]]:
    """Close-only SELLs for the long lots that would sell at a profit now, and the lots kept because they would not.
    Two-vote: one order per profitable lot, naming the lot. Other sleeves: the whole position or nothing."""
    price = account.quotes.get(symbol)
    held = state["sleeves"].get(sleeve, {}).get(symbol, 0)
    if held <= 0 or not price or not math.isfinite(price) or price <= 0:
        return [], []
    lots = long_lots(sleeve, symbol, basis)
    if lots is None or sum(lot["shares"] for lot in lots) != held:
        return [], [{"sleeve": sleeve, "symbol": symbol, "shares": held, "why": "cost basis unverified"}]
    limit = _limit(price, "SELL", cfg)
    sells, kept = [], []
    for lot in lots:
        net = long_sale_net(lot["entry_price"], lot["shares"], limit)
        if net > min_sale_net(cfg):
            lot_tag = tag if lot["id"] is None else f"{tag}{hashlib.sha256(lot['id'].encode()).hexdigest()[:8]}"
            i = _intent(now, session, sleeve, symbol, "SELL", lot["shares"], price, reason, cfg, True, lot_tag)
            i.take_profit_lot_id = lot["id"]
            sells.append(i)
        else:
            kept.append({"sleeve": sleeve, "symbol": symbol, "lot_id": lot["id"], "shares": lot["shares"],
                         "entry_price": lot["entry_price"], "limit": limit, "net": round(net, 2),
                         "break_even_quote": break_even_quote(lot["entry_price"], lot["shares"], cfg)})
    return sells, kept


def name_cap(cfg: dict, account: "Account") -> float:
    """Per-ticker cap, every sleeve summed: risk.name_cap_usd, and at most risk.name_cap_fraction_of_nav of the live
    account NAV (official rule: no more than 40% of assets in one stock; only price-move breaches are exempt)."""
    r = cfg["risk"]
    cap = float(r["name_cap_usd"])
    frac = r.get("name_cap_fraction_of_nav")
    if frac is not None:
        nav = account.nav
        cap = min(cap, frac * nav) if math.isfinite(nav) and nav > 0 else 0.0
    return cap


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
    take_profit_lot_id: str | None = None

    def to_dict(self):
        fields = asdict(self)
        if self.take_profit_lot_id is None:
            fields.pop("take_profit_lot_id")
        return fields


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


def direction_conflict(state: dict, symbol: str, side: int, *, closing_sleeve: str | None = None) -> str | None:
    for sleeve, book in state["sleeves"].items():
        if sleeve != closing_sleeve and side * book.get(symbol, 0) < 0:
            return f"{symbol} already has an opposing position in {sleeve}"
    return None


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
                 sleeves=SLEEVES, basis: dict | None = None, kept: list | None = None) -> list[Intent]:
    """Close-only orders for everything the given sleeves own: short covers (BUY) first, then long sales (SELL), so no
    single order crosses the broker's net position through zero. With consistent attribution each sleeve closes its own
    quantity (sleeves net at the broker); otherwise the order is capped at what the broker shows on that side.
    With risk.long_sales_only_at_profit, longs sell only the lots in profit (`basis`: sleeve_performance()["sleeves"];
    without it no long is sold). Lots kept are appended to `kept`."""
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
            elif loss_guard(cfg):
                if not consistent:
                    (kept if kept is not None else []).append({"sleeve": sleeve, "symbol": s, "shares": q,
                                                               "why": "attribution mismatch"})
                    continue
                lot_sells, held = profitable_long_sales(state, account, session, sleeve, s, reason, cfg, now,
                                                        f"flat{now.strftime('%H%M%S')}", basis)
                sells += lot_sells
                if kept is not None:
                    kept += held
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


SHORTABLE = ("noise", "vwap", "orb", "two_vote")


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
        h = cfg["h_etf"]
        limits["per_etf_budget"] = min(h.get("entry_usd", math.inf), available * h["per_name_fraction_of_sleeve"]) * factor
        limits["aggregate_limit"] = available * cfg["h_etf"]["aggregate_fraction_of_sleeve"]
    elif sleeve == "noise":                                # 100% of the sleeve per entry, like B3
        limits["target_before_cap"] = available * cfg["noise"]["position_fraction_of_sleeve"] * factor
    elif sleeve == "orb":                                  # the reference's 10% cap per trade, within the sleeve
        limits["per_trade_notional"] = min(available, cfg["orb"]["max_notional"]) * factor
        limits["aggregate_limit"] = available
    elif sleeve == "two_vote":                             # fixed two_vote.entry_usd lots within the sleeve's book
        limits["per_trade_notional"] = min(available, cfg["two_vote"]["entry_usd"]) * factor
        limits["aggregate_limit"] = available              # $100k (incl. retired H's $20k); each ticker <= name_cap
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
    why = entry_block(state, account, session, cfg, "b3") or direction_conflict(state, sym, 1)
    if why:
        return [], f"entry blocked: {why}"
    limits = capital_limits("b3", account.sleeve_capital.get("b3"), cfg, state["reduced"])
    if limits["allocation_budget"] is None or limits["allocation_budget"] <= 0:
        return [], "entry blocked: unverified or depleted B3 capital"
    held_value = exposure(state, sym, price)                                 # SPY other sleeves hold, long or short
    budget = min(limits["target_before_cap"], name_cap(cfg, account) - held_value, cash_room(account))
    limit = _limit(price, "BUY", cfg)
    qty = math.floor(budget / max(limit, decision.metrics.get("open_0930", limit)))
    if qty <= 0:
        return [], "entry size zero"
    return [_intent(now, session, "b3", sym, "BUY", qty, price, "b3_target_long", cfg, False, tag)], "entry"


def plan_h_open(state: dict, account: Account, session: str, plan: dict, cfg: dict, now: datetime,
                basis: dict | None = None, kept: list | None = None) -> tuple[list[Intent], str]:
    """Next-open orders from the last close's H plan: exits first, then entries within the H sleeve limits.
    With risk.long_sales_only_at_profit an exit sells only a position in profit (else it is kept, see `kept`)."""
    out = []
    held = state["sleeves"]["h_etf"]
    for s, why in sorted(plan.get("exits", {}).items()):
        q = held.get(s, 0)
        if q > 0 and s in account.quotes:
            if loss_guard(cfg):
                sells, losing = profitable_long_sales(state, account, session, "h_etf", s, f"h_{why}", cfg, now,
                                                      "open", basis)
                out += sells
                if kept is not None:
                    kept += losing
                continue
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
    conflicts = []
    for s in entries:
        conflict = direction_conflict(state, s, 1)
        if conflict:
            conflicts.append(conflict)
            continue
        price = account.quotes.get(s)
        if price is None or not math.isfinite(price) or price <= 0:
            continue
        limit = _limit(price, "BUY", cfg)
        held_value = exposure(state, s, max(price, limit))                # every sleeve counts toward the cap
        budget = min(limits["per_etf_budget"], name_cap(cfg, account) - held_value, room, cash)
        qty = math.floor(budget / limit)
        if qty <= 0:
            continue
        out.append(_intent(now, session, "h_etf", s, "BUY", qty, price, "h_rsi2_entry", cfg, False, "open"))
        room -= qty * limit
        cash -= qty * limit + 0.99
    return out, f"opposing entries skipped ({', '.join(conflicts)})" if conflicts else "planned"


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
    why = (entry_block(state, account, session, cfg, sleeve) or no_entry_reason(sleeve, session, now, cfg)
           or direction_conflict(state, symbol, side))
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
                 name_cap(cfg, account) - exposure(state, symbol, ref_price), cash_room(account))
    factor = 0.5 if state["reduced"] else 1.0
    qty = min(int(risk_qty * factor), math.floor(max(0.0, budget) / max(limit, ref_price)))
    if qty <= 0:
        return [], "entry size zero"
    i = _intent(now, session, sleeve, symbol, order_side, qty, ref_price, reason, cfg, False, tag)
    i.opens_short = side < 0
    return [i], "entry"


def short_twins(cfg: dict) -> dict:
    """two_vote.short_twins {signal ticker: twin ETF on the same index}; only distinct U.S. tickers, else none."""
    twins = (cfg.get("two_vote") or {}).get("short_twins") or {}
    if not isinstance(twins, dict):
        return {}
    ok = {s: t for s, t in twins.items() if isinstance(s, str) and isinstance(t, str) and s.startswith("US.")
          and t.startswith("US.") and s != t}
    return ok if len(set(ok.values())) == len(ok) and not set(ok.values()) & set(ok) else {}


def twin_for(symbol: str, side: int, held: dict, twins: dict) -> str:
    """The ticker a two-vote lot is traded in. A short signal on a ticker the sleeve holds long goes to its twin ETF
    (Moomoo nets one position per ticker, so shorting the ticker itself would first sell the long lots); a short that
    already lives in the twin keeps stacking there. Everything else trades the signal ticker."""
    twin = twins.get(symbol)
    if side < 0 and twin and (held.get(symbol, 0) > 0 or held.get(twin, 0) < 0):
        return twin
    return symbol


def plan_vote_entry(state: dict, account: Account, session: str, symbol: str, side: int, cfg: dict,
                    now: datetime, tag: str) -> tuple[list[Intent], str]:
    """One two-vote lot: two_vote.entry_usd (half size when reduced), within the sleeve's book, the cross-sleeve per-symbol
    cap (name_cap) and cash; a top-up below two_vote.min_lot_usd is skipped. Several lots of the same symbol may be open;
    the runner enforces two_vote.max_lots_per_symbol when it is set (null: the dollar cap alone, user decision 2026-11-25)."""
    if side < 0 and not shorts_allowed("two_vote", cfg):
        return [], "entry blocked: two_vote may not short"
    why = (entry_block(state, account, session, cfg, "two_vote") or no_entry_reason("two_vote", session, now, cfg)
           or direction_conflict(state, symbol, side))
    if why:
        return [], f"entry blocked: {why}"
    limits = capital_limits("two_vote", account.sleeve_capital.get("two_vote"), cfg, state["reduced"])
    if limits["allocation_budget"] is None or limits["allocation_budget"] <= 0:
        return [], "entry blocked: unverified or depleted two_vote capital"
    price = account.quotes.get(symbol)
    if price is None or not math.isfinite(price) or price <= 0:
        return [], "blocked: no quote"
    order_side = "BUY" if side > 0 else "SELL"
    limit = _limit(price, order_side, cfg)
    open_gross = sum(abs(q) * account.quotes.get(s, 0.0) for s, q in state["sleeves"].get("two_vote", {}).items())
    budget = min(limits["per_trade_notional"], limits["aggregate_limit"] - open_gross,
                 name_cap(cfg, account) - exposure(state, symbol, price), cash_room(account))
    qty = math.floor(max(0.0, budget) / max(limit, price))
    if qty <= 0:
        return [], "entry size zero"
    min_lot = cfg["two_vote"].get("min_lot_usd") or 0
    if qty * max(limit, price) < min_lot:
        return [], f"entry blocked: room for ${qty * max(limit, price):,.0f} is below the ${min_lot:,.0f} minimum lot"
    i = _intent(now, session, "two_vote", symbol, order_side, qty, price,
                f"two_vote_{'long' if side > 0 else 'short'}", cfg, False, tag)
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
    why = (why or no_entry_reason(sleeve, session, now, cfg)
           or direction_conflict(state, symbol, target, closing_sleeve=sleeve))
    if why:
        return out, f"entry blocked: {why}"
    limits = capital_limits(sleeve, account.sleeve_capital.get(sleeve), cfg, state["reduced"])
    if limits["allocation_budget"] is None or limits["allocation_budget"] <= 0:
        return out, f"entry blocked: unverified or depleted {sleeve} capital"
    side = "BUY" if target > 0 else "SELL"
    limit = _limit(ref_price, side, cfg)
    others = exposure(state, symbol, ref_price) - abs(held) * ref_price      # this sleeve's old side is being closed
    budget = min(limits["target_before_cap"], name_cap(cfg, account) - others,
                 cash_room(account) + abs(held) * ref_price)
    qty = math.floor(max(0.0, budget) / max(limit, open0, ref_price))
    if qty <= 0:
        return out, "entry size zero"
    i = _intent(now, session, sleeve, symbol, side, qty, ref_price, reason, cfg, False, tag)
    i.opens_short = target < 0
    return out + [i], "entry"
