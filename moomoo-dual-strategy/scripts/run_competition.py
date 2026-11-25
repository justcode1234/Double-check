"""Competition runner: B3, H, Noise-area, VWAP, ORB and the Two-vote SPY/QQQ sleeve, with a 3.5% account halt.

The TOOL decides, sizes, gates and (only with --execute) sends orders; an agent may launch, monitor or stop it but
never places or edits orders by hand. Default is PLAN: intents are written to private/competition/plan/outbox and
nothing is sent. Commands (America/New_York times):

  status                      config, risk state, sleeves, last intents
  plan-h [--asof DATE]        H plan from DATE's completed close (default: last completed session) for the next open
  h-open                      next-open H exits/entries from the stored plan            (run 09:30:10 ET)
  b3 [--at HH:MM]             B3 decision at a completed 5-minute bar (default: latest decision tick today)
  flatten [--sleeve b3|h_etf|noise|vwap|orb|two_vote|all] [--reason TEXT]   close-only exits (short covers first)
  noise --at HH:MM            Noise-area decision at a completed bar (10:30, 11:30, 13:30, 14:30)
  replay-intraday --day DATE  PLAN-only: Noise-area/VWAP/ORB research replay of a completed session (hypothetical P&L)
  loop                        all of the above on the XNYS clock until the session ends: every completed 5-minute slot
                              ORB then VWAP then the Two-vote sleeve; Noise-area at its four times; ~10 s stop/target/
                              trigger monitor; 15:45 VWAP flatten; 15:55 ORB/Noise-area/B3/Two-vote flatten; ~16:15 H plan

Add --execute to send orders. That also requires execution_enabled: true in config/competition.yaml and the private
competition account file; otherwise the runner refuses before contacting any account.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import date, datetime, timedelta, timezone
from importlib.metadata import version
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from dual_strategy.competition import planner  # noqa: E402
from dual_strategy.competition.market import Market, MarketDataError  # noqa: E402
from dual_strategy.competition.signals import b3_decision, b3_decision_times, h_plan  # noqa: E402
from dual_strategy.competition import intraday as I  # noqa: E402
from dual_strategy.competition.store import Store, load_config, sleeve_performance  # noqa: E402
from dual_strategy.competition.vote_live import Monitor  # noqa: E402
from dual_strategy.competition.vote_rules import FIRST_ENTRY, SYMBOLS, last_entry  # noqa: E402
from dual_strategy.data.calendar import NY, session_bounds  # noqa: E402
from dual_strategy.execution.moomoo_paper import ExecutionBlocked

import exchange_calendars  # noqa: E402

XNYS = exchange_calendars.get_calendar("XNYS")


def log(**kw):
    print(json.dumps(kw, default=str), flush=True)


def sessions_until(d: date, n: int = 400) -> list[date]:
    return [s.date() for s in XNYS.sessions_in_range((d - timedelta(days=int(n * 1.6))).isoformat(), d.isoformat())]


def last_completed_session(now: datetime) -> date:
    today = now.astimezone(NY).date()
    b = session_bounds(today)
    if b and now >= b[1] + timedelta(minutes=10):
        return today
    return [s for s in sessions_until(today) if s < today][-1]


class Runner:
    def __init__(self, execute: bool, nav: float, ephemeral: bool = False):
        self.cfg = load_config(PROJECT)
        self.execute = execute
        mode = "live" if execute else "plan"
        if ephemeral:
            if execute:
                raise SystemExit("--ephemeral is PLAN-only: live state must persist")
            import tempfile
            state_dir = tempfile.mkdtemp(prefix="competition-plan-")     # outside the repo; private/ is untouched
            log(ephemeral_state_dir=state_dir)
        else:
            state_dir = f"{self.cfg['state_dir']}/{mode}"
        self.cfg = {**self.cfg, "state_dir": state_dir}
        self.store = Store(PROJECT, self.cfg)
        self.state = self.store.load()
        self._provenance_id = None
        self.market = Market()
        self.broker = None
        self._vote_monitor = None
        self.virtual_nav = nav
        if execute:
            from dual_strategy.competition.broker import CompetitionBroker      # gated: raises unless all locks open
            self.broker = CompetitionBroker(PROJECT, self.cfg, execute=True)

    # ------------------------------------------------------------------ account
    def quote(self, symbol):
        return self.market.quote(symbol)

    def account(self, extra: list[str] = ()) -> planner.Account:
        if self.broker:
            expected = {}
            for book in self.state["sleeves"].values():
                for s, q in book.items():
                    expected[s] = expected.get(s, 0) + q
            acct = self.broker.account(self.quote, expected)
        else:                                            # PLAN: a virtual account holding exactly the sleeves' shares
            held = {}
            for sleeve in planner.SLEEVES:
                for s, q in self.state["sleeves"].get(sleeve, {}).items():
                    held[s] = held.get(s, 0) + q
            quotes, ages = {}, {}
            for s in held:
                quotes[s], at = self.quote(s)
                ages[s] = (datetime.now(timezone.utc) - at).total_seconds()
            cash = self.virtual_nav - sum(q * quotes[s] for s, q in held.items())
            acct = planner.Account(datetime.now(timezone.utc), cash, held, quotes, ages)
        for s in extra:
            if s not in acct.quotes:
                acct.quotes[s], at = self.quote(s)
                acct.quote_age_seconds[s] = (datetime.now(timezone.utc) - at).total_seconds()
        return acct

    def attribute_capital(self, acct):
        performance = sleeve_performance(self.store, self.state, asdict(acct), self.cfg)
        acct.sleeve_capital = {s: p["capital"] for s, p in performance["sleeves"].items()}
        return performance

    def basis(self, acct) -> dict:
        """Confirmed cost basis per sleeve (entry lots, weighted cost) for the profit-only rule on long sales."""
        return planner.basis_view(sleeve_performance(self.store, self.state, asdict(acct), self.cfg)["sleeves"])

    def note_kept(self, session, kept, source):
        """Record (once per lot per session) a long the runner did not sell because the sale would not be profitable."""
        noted = self.__dict__.setdefault("_held_noted", set())
        for row in kept:
            key = (session, row.get("sleeve"), row.get("symbol"), row.get("lot_id"))
            if key in noted:
                continue
            noted.add(key)
            self.store.append(session, {"event": "long_sale_held", "source": source, **row})
            log(long_sale_held=row, source=source)

    def session(self, now=None) -> str:
        return (now or datetime.now(timezone.utc)).astimezone(NY).date().isoformat()

    # ------------------------------------------------------------------ orders
    def audit(self, session, kind, **data):
        if self._provenance_id is None:
            files = ["scripts/run_competition.py", "src/dual_strategy/data/calendar.py",
                     "src/dual_strategy/execution/moomoo_cli.py", "src/dual_strategy/execution/moomoo_paper.py",
                     "src/dual_strategy/research/data.py"]
            files += [f"src/dual_strategy/competition/{name}.py"
                      for name in ("signals", "planner", "market", "broker", "store", "vote_rules", "vote_live")]
            sources = {}
            for name in files:
                text = (PROJECT / name).read_text(encoding="utf-8")
                sources[name] = {"text": text, "sha256": hashlib.sha256(text.encode()).hexdigest()}
            self._provenance_id = self.store.audit("provenance", {
                "kind": "provenance", "schema_version": 1,
                "config": {k: v for k, v in self.cfg.items() if k not in ("account_file", "state_dir")},
                "sources": sources, "environment": {"python": sys.version,
                    "packages": {name: version(name) for name in ("pandas", "numpy", "exchange-calendars", "PyYAML")}}})
        record = {"kind": kind, "session": session, "mode": "EXECUTE" if self.broker else "PLAN",
                  "provenance_id": self._provenance_id, **data}
        if kind == "h_open_started":
            return self.store.audit(session, record, fixed_name=kind)
        return self.store.audit(session, record)

    def send(self, intents, session, audit_id=None):
        halt = self.state.get("halted")
        if self.broker and isinstance(halt, dict) and halt.get("kind") == "order_error":
            return []  # An unresolved order also blocks scheduled close orders against the saved position.
        audit_id = audit_id or self.audit(session, "orders", state=self.state, intents=[i.to_dict() for i in intents])
        results = []
        for i in intents:
            link = {"intent_id": i.intent_id, "audit_id": audit_id}
            reason = None
            before_close = None
            account = None
            if not i.close_only:
                if planner.entries_disabled(self.cfg, i.sleeve):
                    reason = "legacy strategies are exit-only"
                else:
                    reason = planner.direction_conflict(self.state, i.symbol, 1 if i.side == "BUY" else -1)
            if i.close_only and self.broker and self.cfg["risk"].get("legacy_exit_only"):
                account = self.account([i.symbol])
                if not planner.attribution_errors(self.state, account):
                    before_close = account
                held = self.state["sleeves"].get(i.sleeve, {}).get(i.symbol, 0)
                net = account.positions.get(i.symbol, 0)
                sign = 1 if i.side == "SELL" else -1
                if not 0 < i.qty <= min(sign * held, sign * net):
                    reason = "close would exceed the reconciled position on that side"
            if (not reason and i.close_only and i.side == "SELL" and planner.loss_guard(self.cfg)
                    and self.state["sleeves"].get(i.sleeve, {}).get(i.symbol, 0) > 0):
                # Last line of defence for the profit-only rule: whatever planned it, a long sale whose worst-case fill
                # (its limit) would not net a profit after fees is never sent.
                account = account or self.account([i.symbol])
                block = planner.long_sale_block(self.state, i.sleeve, i.symbol, i.qty, i.limit_price, self.cfg,
                                                self.basis(account), i.take_profit_lot_id)
                if block:
                    self.note_kept(session, [{"sleeve": i.sleeve, "symbol": i.symbol, "lot_id": i.take_profit_lot_id,
                                              "shares": i.qty, "limit": i.limit_price, "intent_id": i.intent_id,
                                              "why": block}], "send_guard")
                    continue
            if reason:
                self.store.append(session, {"event": "order_refused", **link, "reason": reason})
                continue
            self.store.append(session, {"event": "intent", **i.to_dict(), "audit_id": audit_id,
                                        "mode": "EXECUTE" if self.broker else "PLAN"})
            if not self.broker:
                log(intent=i.to_dict(), sent=False)
                results.append((i, None))
                continue
            placed = datetime.now(timezone.utc)
            oid = None
            if self.state.get("order_errors_today", 0) >= 3:
                self.store.append(session, {"event": "order_refused", **link, "reason": "3 order errors today"})
                self.store.save(self.state)
                raise ExecutionBlocked("order error budget (3/day) exhausted; human reconciliation required")
            try:
                oid = self.broker.place(i)
                self.state["orders_today"] += 1
                self.store.append(session, {"event": "acknowledged", **link, "order_id": oid})
                result = self.broker.wait(oid, placed, self.cfg["risk"]["order_wait_seconds"])
                if result["status"] == "REJECTED":
                    self.store.append(session, {"event": "outcome", **link, **result})
                    raise ExecutionBlocked("broker rejected order; human reconciliation required")
                if (before_close is not None and result.get("order_id") == oid and result["status"] == "CANCELLED"
                        and type(result.get("filled")) is int and result["filled"] == 0
                        and type(result.get("qty")) is int and 0 < result["qty"] < i.qty):
                    result, evidence = self.broker.reconcile_cancelled_close(i, placed, before_close, oid, result["qty"])
                    review = self.audit(session, "broker_reconciliation", reviewed_errors=[],
                                        broker_cancellations={oid: result["broker_terminal"]}, evidence=evidence)
                    self.store.append(session, {"event": "broker_reconciliation", "audit_id": review})
                filled = result.get("filled")
                price = result.get("avg_price")
                if (result.get("order_id") != oid or result.get("qty") != i.qty
                        or result.get("status") not in ("FILLED", "CANCELLED")
                        or type(filled) is not int or not 0 <= filled <= i.qty
                        or (result["status"] == "FILLED" and filled != i.qty)
                        or (filled and (type(price) not in (int, float) or not math.isfinite(price) or price <= 0))):
                    raise ExecutionBlocked("invalid terminal fill; reconcile before sending another order")
            except Exception as exc:                     # ambiguous: block entries until reconciled by a human
                self.state["order_errors_today"] = self.state.get("order_errors_today", 0) + 1
                self.state["halted"] = self.state["halted"] or {"kind": "order_error", "at": placed.isoformat(),
                                                                "reason": f"order error: {exc}"}
                self.store.append(session, {"event": "order_error", **link, "order_id": oid, "error": str(exc)})
                self.store.save(self.state)
                raise
            planner.apply_fill(self.state, i, result["filled"], session)
            if i.sleeve == "h_etf" and not i.close_only and result["filled"]:
                target = self.cfg["h_etf"].get("take_profit_usd")
                if target is not None:
                    self.state.setdefault("h_take_profit_targets", {})[i.symbol] = target
            flat = not self.state["sleeves"][i.sleeve].get(i.symbol)
            if i.close_only and (flat or (i.take_profit_lot_id and result["filled"] == i.qty)):
                self.state.get("take_profit_pending", {}).get(i.sleeve, {}).pop(i.symbol, None)
                if flat and i.sleeve == "h_etf":
                    self.state.get("h_take_profit_targets", {}).pop(i.symbol, None)
            self.store.append(session, {"event": "outcome", **link, **result})
            self.store.save(self.state)
            log(intent=i.intent_id, outcome=result)
            results.append((i, result))
        self.store.save(self.state)
        return results

    # ------------------------------------------------------------------ risk
    def risk(self, symbols=()):
        acct = self.account(list(symbols))
        self._profit_account = acct
        session = self.session()
        before = deepcopy(self.state)
        reason = planner.mark_risk(self.state, acct, session, self.cfg)
        self.store.save(self.state)
        now = datetime.now(NY)
        kept, basis = [], self.basis(acct) if reason else None
        intents = planner.plan_flatten(self.state, acct, session, reason, self.cfg, now,
                                       basis=basis, kept=kept) if reason else []
        audit_id = self.audit(session, "risk", account=asdict(acct), state_before=before, state_after=self.state,
                              now=now, reason=reason, intents=[i.to_dict() for i in intents], kept=kept, basis=basis)
        self.note_kept(session, kept, reason or "risk")
        if reason:
            log(risk=reason, nav=acct.nav, high_water=self.state["high_water"], flatten=len(intents))
            self.send(intents, session, audit_id)
        return acct

    # ------------------------------------------------------------------ commands
    def plan_h(self, asof: date | None = None):
        asof = asof or last_completed_session(datetime.now(timezone.utc))
        c = self.cfg["h_etf"]
        held = {s: date.fromisoformat(d) for s, d in self.state["h_entry_session"].items()}
        try:
            panel = self.market.h_panel(c["core"] + c["sectors"], asof)
        except MarketDataError as exc:
            self.state["h_plan"] = {"asof": asof.isoformat(), "blocked": str(exc), "entries": [], "exits": {}}
            self.state["h_plan_audit_id"] = self.audit(asof.isoformat(), "h_plan_blocked", asof=asof,
                                                      held=held, error=str(exc))
            self.store.save(self.state)
            return log(h_plan=self.state["h_plan"])
        sessions = sessions_until(asof)
        p = h_plan(panel, asof, held, sessions, c)
        self.state["h_plan_audit_id"] = self.audit(asof.isoformat(), "h_plan", inputs={
            "asof": asof, "held": held, "sessions": sessions,
            "daily": {s: f.to_dict(orient="split") for s, f in panel.items()}}, plan=asdict(p))
        self.state["h_plan"] = {"asof": asof.isoformat(), "universe": p.universe, "entries": p.entries,
                                "exits": p.exits, "blocked": p.blocked,
                                "metrics": {s: {k: (round(v, 4) if isinstance(v, float) else v) for k, v in m.items()}
                                            for s, m in p.metrics.items()}}
        self.store.save(self.state)
        log(h_plan={k: v for k, v in self.state["h_plan"].items() if k != "metrics"})

    def h_open(self):
        plan = self.state.get("h_plan")
        session = self.session()
        if self.broker:
            for path in (self.store.dir / "audit" / session).glob("[!.]*.json"):
                record = json.loads(path.read_text(encoding="utf-8"))
                if record.get("mode") == "EXECUTE" and record.get("kind") in ("h_open", "h_open_skipped", "h_open_started"):
                    return log(h_open="already attempted", session=session)
            if any(r.get("event") == "runner_error" and r.get("step") == "h-open" for r in self.store.outbox(session)):
                return log(h_open="already attempted", session=session)
            try:
                self.audit(session, "h_open_started")
            except FileExistsError:
                return log(h_open="already attempted", session=session)
        prior = [s for s in sessions_until(date.fromisoformat(session)) if s < date.fromisoformat(session)][-1]
        if not plan or plan.get("blocked") or plan["asof"] != prior.isoformat():
            self.audit(session, "h_open_skipped", plan=plan, reason="no unblocked plan from the previous close")
            return log(h_open="skipped", why="no unblocked plan from the previous close", plan_asof=plan and plan.get("asof"))
        if not self.broker or not plan["entries"]:
            acct = self.risk(plan["entries"] + list(plan["exits"]))
            return self._send_h_open(session, plan, acct, "h_open")
        if plan["exits"]:
            # Terminal order receipts and a subsequent broker read decide how much capacity was freed.
            exits = {**plan, "entries": []}
            self._send_h_open(session, exits, self.risk(list(exits["exits"])), "h_open_exits")
        for index, symbol in enumerate(dict.fromkeys(plan["entries"])):
            entry = {**plan, "exits": {}, "entries": [symbol]}
            kind = "h_open" if index == 0 else "h_open_entry"
            self._send_h_open(session, entry, self.risk([symbol]), kind)

    def _send_h_open(self, session, plan, acct, kind):
        capital = self.attribute_capital(acct)
        now = datetime.now(NY)
        kept = []
        basis = planner.basis_view(capital["sleeves"])
        intents, why = planner.plan_h_open(self.state, acct, session, plan, self.cfg, now, basis=basis, kept=kept)
        audit_id = self.audit(session, kind, plan=plan, signal_audit_id=self.state.get("h_plan_audit_id"),
                              state=self.state, account=asdict(acct), capital=capital, now=now, reason=why,
                              intents=[i.to_dict() for i in intents], kept=kept, basis=basis)
        self.note_kept(session, kept, kind)
        log(h_open=why, intents=len(intents))
        self.send(intents, session, audit_id)

    def b3(self, at: str | None = None, day: date | None = None):
        if day and self.broker:
            return log(b3="refused", why="--day replays a past session and is PLAN-only")
        now = datetime.now(NY) if not day else datetime.combine(day, datetime.max.time(), NY)
        today = now.date()
        bounds = session_bounds(today)
        if not bounds:
            return log(b3="no session today")
        ticks = b3_decision_times(*bounds, self.cfg["b3"]["decision_times"])
        if at:
            h, m = (int(x) for x in at.split(":"))
            tick = next((t for t in ticks if (t.hour, t.minute) == (h, m)), None)
        else:
            done = [t for t in ticks if t + timedelta(seconds=10) <= now]
            tick = done[-1] if done else None
        if tick is None:
            return log(b3="no completed decision tick", ticks=[t.strftime("%H:%M") for t in ticks])
        key = tick.isoformat()
        if self.broker and key in self.state["b3_done"]:
            return log(b3="already processed", at=key)
        sym = self.cfg["b3"]["symbol"]
        prev_day = [s for s in sessions_until(today) if s < today][-1]
        try:
            prior, today_bars = self.market.b3_inputs(sym, today, self.cfg["b3"]["lookback_sessions"], replay=day is not None)
            prev_close = self.market.daily(sym, prev_day - timedelta(days=7), prev_day, 0).close.loc[prev_day]
        except (MarketDataError, KeyError) as exc:
            self.audit(today.isoformat(), "b3_blocked", symbol=sym, at=tick, error=str(exc))
            return log(b3="blocked", why=str(exc))
        from dual_strategy.research.data import dividends   # cached corporate actions (SPY ex-dates through 2026-09)
        dividend = dividends(sym).get(today, 0.0)
        decision = b3_decision(today_bars, prior, float(prev_close), dividend, tick, bounds[0],
                               self.cfg["b3"]["lookback_sessions"], self.cfg["b3"]["band_width"])
        acct = self.risk([sym])
        capital = self.attribute_capital(acct)
        intents, why = planner.plan_b3(self.state, acct, today.isoformat(), decision, self.cfg, now)
        audit_id = self.audit(today.isoformat(), "b3", inputs={
            "today": [asdict(b) for b in today_bars],
            "prior": {d.isoformat(): [asdict(b) for b in rows] for d, rows in prior.items()},
            "prev_close": float(prev_close), "dividend_today": dividend, "at": tick, "session_open": bounds[0],
            "lookback": self.cfg["b3"]["lookback_sessions"], "width": self.cfg["b3"]["band_width"]},
            decision=asdict(decision), state=self.state, account=asdict(acct), capital=capital, now=now, reason=why,
            intents=[i.to_dict() for i in intents])
        log(b3={"at": key, "target": decision.target, "reason": decision.reason,
                "metrics": {k: round(v, 5) for k, v in decision.metrics.items() if isinstance(v, float)}}, plan=why)
        self.send(intents, today.isoformat(), audit_id)
        if self.broker:
            self.state["b3_done"].append(key)
            self.store.save(self.state)

    def flatten(self, sleeve: str, reason: str):
        sleeves = planner.SLEEVES if sleeve == "all" else (sleeve,)
        acct = self.account([s for sl in sleeves for s in self.state["sleeves"].get(sl, {})])
        session, now = self.session(), datetime.now(NY)
        kept, basis = [], self.basis(acct)
        intents = planner.plan_flatten(self.state, acct, session, reason, self.cfg, now, sleeves,
                                       basis=basis, kept=kept)
        audit_id = self.audit(session, "flatten", state=self.state, account=asdict(acct), now=now,
                              sleeves=sleeves, reason=reason, intents=[i.to_dict() for i in intents], kept=kept,
                              basis=basis)
        self.note_kept(session, kept, reason)
        self.send(intents, session, audit_id)

    # ------------------------------------------------------------------ Noise-area / VWAP / ORB
    def _day(self, today: date):
        bounds = session_bounds(today)
        if not bounds:
            return None
        return bounds[0], bounds[1], I.expected_slots(*bounds)

    def _intraday(self, session: str) -> dict:
        """Per-session runner memory for the intraday sleeves (reset at each new session; never holds shares)."""
        cur = self.state.get("intraday")
        if not cur or cur.get("session") != session:
            cur = {"session": session, "armed": {}, "positions": {}, "orb_done": [], "orb_base": {}, "vwap_trades": {},
                   "noise_done": [], "decisions": [], "last_slot": None}
            self.state["intraday"] = cur
        cur.setdefault("vote_sides", {})
        cur.setdefault("vote_slot", None)
        return cur

    def _decide(self, day: dict, sleeve: str, **row):
        day["decisions"] = (day.get("decisions") or [])[-299:] + [{"at": datetime.now(NY).isoformat(), "sleeve": sleeve, **row}]

    def _atr(self, symbol: str, today: date) -> float:
        cache = self.__dict__.setdefault("_atr_cache", {})
        if (symbol, today) not in cache:
            d = self.market.daily(symbol, today - timedelta(days=45), today - timedelta(days=1), 0)
            cache[(symbol, today)] = I.atr14(d, today)
        return cache[(symbol, today)]

    def _orb_base(self, symbol: str, today: date, k: int) -> list:
        """First-range volume of the 20 prior sessions (None where a first-range bar is missing)."""
        days = [s for s in sessions_until(today) if s < today][-20:]
        out = {d: None for d in days}
        for c in range(0, len(days), 4):                                  # CLI caps 370 bars: 4 sessions per call
            chunk = days[c:c + 4]
            rows = self.market.five_minute(symbol, chunk[0], chunk[-1])
            for d in chunk:
                opened = session_bounds(d)[0]
                a = I.day_arrays([b for b in rows if b.end.date() == d], opened)
                v = I.first_range_volume(a["volume"], k)
                out[d] = v if math.isfinite(v) else None
        return [out[d] for d in days]

    def _arrays(self, symbol: str, today: date, opened: datetime, now: datetime):
        bars = [b for b in self.market.five_minute_today(symbol, today) if b.end <= now]
        a = I.day_arrays(bars, opened)
        return a, I.cum_vwap(a["volume"], a["turnover"], a["close"]), bars

    def bar_step(self, at: datetime | None = None):
        """Every completed 5-minute slot: bar-based exits (stop / target / ORB half and trail), then ORB (from the bar
        ending 09:50), then VWAP (from 10:10). The 10-second quote monitor normally acts first; these checks back it
        up when a wick between polls touched a level, and the ORB trail is defined on completed bars."""
        now = at or datetime.now(NY)
        today = now.date()
        ctx = self._day(today)
        if not ctx:
            return
        opened, closed, n = ctx
        k = I.last_completed_slot(opened, now, n)
        session = today.isoformat()
        day = self._intraday(session)
        if k < 2 or day.get("last_slot") == k:
            return
        day["last_slot"] = k
        acct = None
        arrays = {sym: self._arrays(sym, today, opened, now)
                  for sym in sorted(set(self.cfg["orb"]["symbols"]) | set(self.cfg["vwap"]["symbols"]))}
        self.audit(session, "bar_step", slot=k, at=now, bars={s: [asdict(b) for b in v[2]] for s, v in arrays.items()})
        # --- bar-based exits for positions opened on earlier slots
        for key, pos in list(day["positions"].items()):
            sleeve, sym = key.split("|")
            if sym not in arrays or pos.get("entry_slot", 99) > k:
                continue
            a = arrays[sym][0]
            side, lo, hi, cl = pos["side"], a["low"][k], a["high"][k], a["close"][k]
            touched = lambda level: (lo <= level) if side > 0 else (hi >= level)
            reached = lambda level: (hi >= level) if side > 0 else (lo <= level)
            action = None
            if touched(pos["stop"]):
                action = ("all", f"{sleeve}_{'breakeven' if pos.get('half_done') else 'stop'}_bar")
            elif sleeve == "orb" and not pos.get("half_done") and reached(pos["target"]):
                action = ("half", "orb_target_half_bar")
            elif sleeve == "vwap" and reached(pos["target"]):
                action = ("all", "vwap_target_bar")
            if action:
                acct = acct or self.risk(list(arrays))
                self._close(sleeve, sym, action[1], acct, session, day, half=action[0] == "half")
                acct = None
            pos = day["positions"].get(key)
            if pos and sleeve == "orb" and pos.get("half_done") and k > pos["entry_slot"] and k + 1 < n - 1 and k >= 1:
                broke = (cl < a["low"][k - 1]) if side > 0 else (cl > a["high"][k - 1])
                if broke:
                    acct = acct or self.risk(list(arrays))
                    self._close(sleeve, sym, "orb_trail", acct, session, day)
                    acct = None
        # --- ORB breakout/breakdown
        c = self.cfg["orb"]
        kr = c["range_minutes"] // 5
        for sym in c["symbols"]:
            if sym in day["orb_done"] or k < kr:
                continue
            a, vw, _ = arrays[sym]
            try:
                atr = self._atr(sym, today)
                if sym not in day["orb_base"]:
                    day["orb_base"][sym] = self._orb_base(sym, today, kr)
            except MarketDataError as exc:
                self._decide(day, "orb", symbol=sym, slot=k, action="blocked", why=f"data: {exc}")
                continue
            base = [x if x is not None else math.nan for x in day["orb_base"][sym]]
            rvol = I.orb_rvol(I.first_range_volume(a["volume"], kr), base)
            trade, pending, skip = I.orb_scan(a, vw, atr, rvol, n, c, k, 100_000.0, float(c["max_notional"]))
            if skip:
                day["orb_done"].append(sym)
                self._decide(day, "orb", symbol=sym, slot=k, action="skip", why=skip, rvol=rvol, atr=atr)
                continue
            if pending is None:
                continue
            day["orb_done"].append(sym)                               # one ORB decision per symbol per day
            acct = acct or self.risk(list(arrays))
            self.attribute_capital(acct)
            if len([p for p in day["positions"] if p.startswith("orb|")]) >= c["max_open"]:
                self._decide(day, "orb", symbol=sym, slot=k, action="skip", why="max open")
                continue
            ref = acct.quotes.get(sym) or self.quote(sym)[0]
            acct.quotes.setdefault(sym, ref)
            cap = min(acct.sleeve_capital.get("orb") or 0.0, float(c["max_notional"]))
            t = I.orb_entry(pending, ref, c, atr, acct.nav, cap)
            if t is None:
                self._decide(day, "orb", symbol=sym, slot=k, action="skip", why="size zero")
                continue
            side = pending["side"]
            intents, why = planner.plan_entry(self.state, acct, session, "orb", sym, side, t["qty"], ref,
                                              f"orb_{'long' if side > 0 else 'short'}", self.cfg, now, f"e{k + 1}")
            self._decide(day, "orb", symbol=sym, slot=k, action=why, side=side, stop=t["stop"], target=t["target"],
                         rvol=rvol, atr=atr)
            self._enter(intents, "orb", sym, side, t, k + 1, session, day, acct, pending=pending)
            acct = None if intents else acct
        # --- VWAP trend continuation (arm a stop trigger for the next slot)
        c2 = self.cfg["vwap"]
        for sym in c2["symbols"]:
            a, vw, _ = arrays[sym]
            try:
                atr = self._atr(sym, today)
            except MarketDataError as exc:
                self._decide(day, "vwap", symbol=sym, slot=k, action="blocked", why=f"ATR data: {exc}")
                continue
            cands, pending = I.s2_scan(a, vw, atr, n, c2, k, 100_000.0, 20_000.0)
            day["armed"].pop(sym, None)                              # a trigger is valid for one slot only
            if pending is None:
                continue
            if f"vwap|{sym}" in day["positions"] or day["vwap_trades"].get(sym, 0) >= c2["max_trades_per_symbol"]:
                continue
            day["armed"][sym] = {**pending, "atr": atr, "valid_from": I.slot_start(opened, k + 1).isoformat(),
                                 "valid_until": I.slot_start(opened, k + 2).isoformat(), "first_quote": None}
            self._decide(day, "vwap", symbol=sym, slot=k, action="armed", side=pending["side"], trigger=pending["trigger"],
                         stop=pending["stop"])
        self.store.save(self.state)

    def noise(self, at: str):
        """Noise-area decision at a completed bar (10:30, 11:30, 13:30, 14:30): act only when the target changes."""
        now = datetime.now(NY)
        today = now.date()
        bounds = session_bounds(today)
        if not bounds:
            return log(noise="no session today")
        c = self.cfg["noise"]
        sym = c["symbol"]
        h, m = (int(x) for x in at.split(":"))
        tick = bounds[0].replace(hour=h, minute=m)
        session = today.isoformat()
        day = self._intraday(session)
        if tick.isoformat() in day["noise_done"]:
            return log(noise="already processed", at=tick.isoformat())
        day["noise_done"].append(tick.isoformat())
        self.store.save(self.state)
        prev_day = [s for s in sessions_until(today) if s < today][-1]
        try:
            prior, today_bars = self.market.b3_inputs(sym, today, c["lookback_sessions"])
            prev_close = float(self.market.daily(sym, prev_day - timedelta(days=7), prev_day, 0).close.loc[prev_day])
        except (MarketDataError, KeyError) as exc:
            self.audit(session, "noise_blocked", at=tick, error=str(exc))
            self._decide(day, "noise", symbol=sym, at=tick.isoformat(), action="blocked", why=str(exc))
            return log(noise="blocked", why=str(exc))
        d = I.noise_decision(today_bars, prior, prev_close, tick, bounds[0], c)
        acct = self.risk([sym])
        capital = self.attribute_capital(acct)
        intents, why = ([], f"blocked: {d['reason']}") if d["target"] is None else planner.plan_target(
            self.state, acct, session, "noise", sym, d["target"], d["metrics"]["open_0930"], acct.quotes.get(sym),
            f"noise_{({1: 'long', -1: 'short', 0: 'flat'})[d['target']]}", self.cfg, now, tick.strftime("%H%M"))
        audit_id = self.audit(session, "noise", inputs={
            "today": [asdict(b) for b in today_bars], "prior": {x.isoformat(): [asdict(b) for b in r] for x, r in prior.items()},
            "prev_close": prev_close, "at": tick, "session_open": bounds[0], "config": c}, decision=d, state=self.state,
            account=asdict(acct), capital=capital, now=now, reason=why, intents=[i.to_dict() for i in intents])
        self._decide(day, "noise", symbol=sym, at=tick.isoformat(), action=why, target=d["target"], reason=d["reason"],
                     metrics={k: round(v, 4) for k, v in d["metrics"].items() if isinstance(v, float)})
        log(noise={"at": tick.isoformat(), "target": d["target"], "reason": d["reason"]}, plan=why)
        self.send(intents, session, audit_id)

    # ------------------------------------------------------------------ Two-vote SPY/QQQ
    def vote_step(self):
        """Each completed 5-minute bar: opposite 2-vote agreement closes the symbol: every short lot, but only the long
        lots that sell at a profit after fees (risk.long_sales_only_at_profit; losing long lots are kept). While a 2-vote
        agreement holds, every bar may open one more lot in that direction, 10:00-14:30 ET, within the name cap (and
        two_vote.max_lots_per_symbol when set; null = dollar cap only, user decision 2026-11-25), including re-entry
        after an exit. Never on the bar an opposite agreement closed the symbol, and never while that symbol's profit
        exit is still working."""
        now = datetime.now(NY)
        ctx = self._day(now.date())
        if not ctx:
            return
        opened, closed, n = ctx
        session = now.date().isoformat()
        day = self._intraday(session)
        if self._vote_monitor is None:
            self._vote_monitor = Monitor(self.market)
        report = self._vote_monitor.observe(datetime.now(timezone.utc))
        if report.get("status") != "current":
            return
        k = int((datetime.fromisoformat(report["bar_end"]) - opened).total_seconds() // 300)
        if k < FIRST_ENTRY or day.get("vote_slot") == k:
            return
        day["vote_slot"] = k
        self.audit(session, "vote_step", slot=k, bar_end=report["bar_end"], at=now, votes=report["tickers"])
        c = self.cfg["two_vote"]
        sides = {sym: row["signal"] for sym, row in report["tickers"].items()}
        pending_profit = self.state.get("take_profit_pending", {}).get("two_vote", {})
        held = self.state["sleeves"].get("two_vote", {})
        closed_now, acct = set(), None
        for sym, q in sorted(held.items()):
            if sides.get(sym) == -(1 if q > 0 else -1):
                acct = acct or self.risk(sorted(set(held) | set(SYMBOLS)))
                if q > 0 and planner.loss_guard(self.cfg):
                    # Longs: sell only the lots that would sell at a profit after fees; losing lots are kept.
                    sells, kept = planner.profitable_long_sales(self.state, acct, session, "two_vote", sym, "",
                                                                self.cfg, now, "", self.basis(acct))
                    self.note_kept(session, kept, "two_vote_opposite_agreement")
                    for lot in kept:
                        self._decide(day, "two_vote", symbol=sym, slot=k, action="kept: lot would not sell at a profit",
                                     lot=lot.get("lot_id"), net=lot.get("net"), break_even=lot.get("break_even_quote"))
                    for sell in sells:
                        self._close("two_vote", sym, "two_vote_opposite_agreement", acct, session, day,
                                    lot_id=sell.take_profit_lot_id, lot_qty=sell.qty)
                else:
                    self._close("two_vote", sym, "two_vote_opposite_agreement", acct, session, day)
                closed_now.add(sym)
                acct = None
        held = self.state["sleeves"].get("two_vote", {})
        if k <= last_entry(n):
            eligible = [sym for sym in SYMBOLS if sides.get(sym) and sym not in closed_now
                        and sym not in pending_profit and held.get(sym, 0) * sides[sym] >= 0]   # flat or same side
            eligible.sort(key=lambda sym: -report["tickers"][sym]["buy" if sides[sym] > 0 else "sell"])
            for sym in eligible:
                acct = acct or self.risk(list(SYMBOLS))
                performance = self.attribute_capital(acct)["sleeves"]["two_vote"]
                max_lots = c.get("max_lots_per_symbol")
                if max_lots is not None and performance["open_entry_lots"].get(sym, 0) >= max_lots:
                    intents, why = [], "entry blocked: maximum open lots"
                else:
                    intents, why = planner.plan_vote_entry(self.state, acct, session, sym, sides[sym], self.cfg, now, f"e{k}")
                row = report["tickers"][sym]
                self._decide(day, "two_vote", symbol=sym, slot=k, action=why, side=sides[sym],
                             buy=row["buy"], sell=row["sell"])
                if intents:
                    audit_id = self.audit(session, "two_vote_entry", state=self.state, account=asdict(acct),
                                          intents=[i.to_dict() for i in intents])
                    self.send(intents, session, audit_id)
                    acct = None
        day["vote_sides"] = sides
        self.store.save(self.state)

    def _enter(self, intents, sleeve, sym, side, t, entry_slot, session, day, acct, **extra):
        audit_id = self.audit(session, f"{sleeve}_entry", state=self.state, account=asdict(acct), trade=t, extra=extra,
                              intents=[i.to_dict() for i in intents])
        if not intents:
            return
        results = self.send(intents, session, audit_id)
        res = results[0][1] if results else None
        filled = res["filled"] if res else 0
        if filled > 0 or not self.broker:
            day["positions"][f"{sleeve}|{sym}"] = {"side": side, "stop": t["stop"], "target": t["target"],
                                                   "entry_ref": t["entry"], "entry_slot": entry_slot,
                                                   "qty0": filled or intents[0].qty, "half_done": False,
                                                   "fill": res["avg_price"] if res else None, "planned_only": not self.broker}
        if sleeve == "vwap":
            day["vwap_trades"][sym] = day["vwap_trades"].get(sym, 0) + 1
        self.store.save(self.state)

    def _close(self, sleeve, sym, reason, acct, session, day, half=False, lot_id=None, lot_qty=None, **evidence):
        now = datetime.now(NY)
        pos = day["positions"].get(f"{sleeve}|{sym}")
        qty = lot_qty
        if half and pos:                                           # ORB: half at the target (round(qty x 0.5))
            held = self.state["sleeves"].get(sleeve, {}).get(sym, 0)
            q = min(abs(held), int(round(pos["qty0"] * 0.5)))
            qty = q if held > 0 else -q
        tag = f"x{now.strftime('%H%M%S')}"
        if lot_id is not None:
            tag += hashlib.sha256(lot_id.encode()).hexdigest()[:8]
        intents = planner.plan_close(self.state, acct, session, sleeve, sym, reason, self.cfg, now, tag, qty=qty)
        if lot_id is not None:
            for intent in intents:
                intent.take_profit_lot_id = lot_id
        audit_id = self.audit(session, f"{sleeve}_exit", state=self.state, account=asdict(acct), reason=reason,
                              position=pos, intents=[i.to_dict() for i in intents], **evidence)
        take_profit = evidence.get("take_profit")
        if take_profit and take_profit["activation"]:
            self.store.append(session, {"event": "take_profit_activation", "audit_id": audit_id,
                                        "sleeve": sleeve, "symbol": sym, "entry_lot_id": lot_id,
                                        "shares": abs(lot_qty) if lot_qty is not None else abs(self.state["sleeves"][sleeve].get(sym, 0)),
                                        **take_profit})
        self.send(intents, session, audit_id)
        if half and pos and (self.state["sleeves"].get(sleeve, {}).get(sym) or not self.broker):
            pos["half_done"], pos["stop"] = True, pos["entry_ref"]  # remainder: breakeven stop + trail
        elif not self.state["sleeves"].get(sleeve, {}).get(sym) or not self.broker:
            day["positions"].pop(f"{sleeve}|{sym}", None)
        self._decide(day, sleeve, symbol=sym, action="exit_half" if half else "exit", why=reason)
        self.store.save(self.state)

    def _profit_candidates(self, watch, acct, quotes):
        if planner.attribution_errors(self.state, acct):
            return []
        reports = sleeve_performance(self.store, self.state, asdict(acct), self.cfg)["sleeves"]
        pending = self.state.get("take_profit_pending", {})
        now = datetime.now(timezone.utc)
        candidates = []
        for sleeve, sym, target in watch:
            report = reports[sleeve]
            sample = quotes.get(sym)
            position = report["positions"].get(sym)
            if report["status"] != "available" or position is None or sample is None:
                continue
            price, at = sample
            age = (now - at).total_seconds()
            if not -5 <= age <= self.cfg["risk"]["max_quote_age_seconds"]:
                continue
            waiting = pending.get(sleeve, {}).get(sym)
            if sleeve == "two_vote":
                lots = report["entry_lots"].get(sym, ())
                if not any(lot["id"] == waiting for lot in lots):
                    waiting = None
                for lot in lots:
                    qty = lot["shares"] * lot["side"]
                    gross = round(qty * (price - lot["entry_price"]), 8)
                    if waiting == lot["id"] or (waiting is None and gross >= target):
                        candidates.append((sleeve, sym, target, lot["id"], qty, price, gross, at))
            else:
                gross = round(position["qty"] * price - position["cost_basis"], 8)
                if waiting or gross >= target:
                    candidates.append((sleeve, sym, target, None, None, price, gross, at))
        return candidates

    def take_profit(self):
        """Check marked gross profit per sleeve and ticker, using timestamped broker quotes and confirmed fills."""
        now = datetime.now(NY)
        bounds = session_bounds(now)
        if not self.broker or not bounds or not bounds[0] <= now < bounds[1] or self.state.get("halted"):
            return
        session = now.date().isoformat()
        if self.state.get("day") != session or self.state.get("locked_day") == session:
            return
        watch = []
        for sleeve in ("two_vote", "h_etf"):
            config = self.cfg[sleeve]
            if sleeve == "two_vote" and now >= bounds[1] - timedelta(minutes=config["flatten_minutes_before_close"]):
                continue
            for sym, qty in self.state["sleeves"].get(sleeve, {}).items():
                target = config.get("take_profit_usd")
                if sleeve == "h_etf":
                    target = self.state.get("h_take_profit_targets", {}).get(sym, config.get("legacy_take_profit_usd", target))
                if qty and type(target) in (int, float) and math.isfinite(target) and target > 0:
                    watch.append((sleeve, sym, target))
        if not watch:
            return
        quotes = self.market.timestamped_quotes(sorted({sym for _, sym, _ in watch}))
        acct = getattr(self, "_profit_account", None)
        if acct is None or planner.attribution_errors(self.state, acct):
            acct = self._profit_account = self.account()
        for sleeve, sym, target, lot_id, *_ in self._profit_candidates(watch, acct, quotes):
            if self.state.get("halted") or datetime.now(NY) >= bounds[1]:
                break
            acct = self._profit_account = self.account()
            quotes = self.market.timestamped_quotes([sym])
            confirmed = [row for row in self._profit_candidates([(sleeve, sym, target)], acct, quotes)
                         if row[3] == lot_id]
            if not confirmed:
                continue
            _, _, _, _, lot_qty, price, gross, at = confirmed[0]
            pending = self.state.setdefault("take_profit_pending", {}).setdefault(sleeve, {})
            day = self._intraday(session)
            pending_key = lot_id if lot_id is not None else True
            activated = pending.get(sym) != pending_key
            if activated:
                pending[sym] = pending_key
                counts = self.state.setdefault("take_profit_counts", {}).setdefault(sleeve, {})
                counts[sym] = counts.get(sym, 0) + 1
                self.store.save(self.state)
            marked = replace(acct, quotes={**acct.quotes, sym: price})
            self._close(sleeve, sym, f"{sleeve}_take_profit", marked, session, day,
                        lot_id=lot_id, lot_qty=lot_qty,
                        take_profit={"activation": activated, "target_usd": target, "gross_usd": gross,
                                     "quote_at": at.isoformat()})

    def monitor(self):
        """~10 s: VWAP triggers armed for the current slot; stops and targets of open VWAP/ORB positions (quotes)."""
        now = datetime.now(NY)
        session = now.date().isoformat()
        day = self._intraday(session)
        watch = set(day["armed"]) | {k.split("|")[1] for k in day["positions"]}
        if not watch:
            return
        quotes = {sym: self.quote(sym)[0] for sym in sorted(watch)}       # market data only; broker read on action
        for key, pos in list(day["positions"].items()):
            sleeve, sym = key.split("|")
            px = quotes.get(sym)
            if not px:
                continue
            side = pos["side"]
            if (px <= pos["stop"]) if side > 0 else (px >= pos["stop"]):
                acct = self.risk(sorted(watch))
                self._close(sleeve, sym, f"{sleeve}_{'breakeven' if pos.get('half_done') else 'stop'}", acct, session, day)
            elif not pos.get("half_done") and ((px >= pos["target"]) if side > 0 else (px <= pos["target"])):
                acct = self.risk(sorted(watch))
                self._close(sleeve, sym, f"{sleeve}_target{'_half' if sleeve == 'orb' else ''}", acct, session, day,
                            half=sleeve == "orb")
        c2 = self.cfg["vwap"]
        for sym, arm in list(day["armed"].items()):
            start, end = datetime.fromisoformat(arm["valid_from"]), datetime.fromisoformat(arm["valid_until"])
            if now >= end:
                day["armed"].pop(sym, None)
                self._decide(day, "vwap", symbol=sym, action="expired", trigger=arm["trigger"])
                continue
            px = quotes.get(sym)
            if now < start or not px:
                continue
            if arm["first_quote"] is None:
                arm["first_quote"] = px                          # proxy for the slot's open (spec: max(open, trigger))
            side = arm["side"]
            if not ((px >= arm["trigger"]) if side > 0 else (px <= arm["trigger"])):
                continue
            day["armed"].pop(sym, None)
            acct = self.risk(sorted(watch))
            px = acct.quotes.get(sym) or px
            if len([p for p in day["positions"] if p.startswith("vwap|")]) >= c2["max_open"]:
                self._decide(day, "vwap", symbol=sym, action="skip", why="max open")
                continue
            self.attribute_capital(acct)
            t = I.s2_entry(arm, arm["first_quote"], c2, arm["atr"], acct.nav, acct.sleeve_capital.get("vwap") or 0.0)
            if t is None:
                self._decide(day, "vwap", symbol=sym, action="skip", why="size zero")
                continue
            intents, why = planner.plan_entry(self.state, acct, session, "vwap", sym, side, t["qty"], px,
                                              f"vwap_{'long' if side > 0 else 'short'}", self.cfg, now, f"e{arm['entry_j']}")
            self._decide(day, "vwap", symbol=sym, slot=arm["entry_j"], action=why, side=side, stop=t["stop"], target=t["target"])
            self._enter(intents, "vwap", sym, side, t, arm["entry_j"], session, day, acct, armed=arm)
        self.store.save(self.state)

    def flatten_sleeves(self, sleeves: tuple, reason: str):
        """Scheduled close-only exit of whole sleeves (covers first). The loop retries until nothing is left."""
        held = [s for sl in sleeves for s, q in self.state["sleeves"].get(sl, {}).items() if q]
        session = self.session()
        day = self._intraday(session)
        if "vwap" in sleeves:
            day["armed"] = {}                                   # no VWAP trigger survives its flatten time
        if not held:
            return
        acct = self.risk(held)
        now = datetime.now(NY)
        kept, basis = [], self.basis(acct)
        intents = planner.plan_flatten(self.state, acct, session, reason, self.cfg, now, sleeves,
                                       basis=basis, kept=kept)
        self.note_kept(session, kept, reason)
        if intents or not kept:
            audit_id = self.audit(session, "flatten", state=self.state, account=asdict(acct), now=now, sleeves=sleeves,
                                  reason=reason, intents=[i.to_dict() for i in intents], kept=kept, basis=basis)
            self.send(intents, session, audit_id)
        for key in list(day["positions"]):
            sl, sym = key.split("|")
            if sl in sleeves and not self.state["sleeves"].get(sl, {}).get(sym):
                day["positions"].pop(key, None)
        self.store.save(self.state)

    def daily_report(self, session: str):
        """After the close: NAV, high-water, halt/lock and each sleeve's capital and P&L, written to
        <state_dir>/reports/<session>.json and optionally announced by the desktop."""
        acct = self.account() if self.broker else None
        perf = sleeve_performance(self.store, self.state, asdict(acct) if acct else None, self.cfg)
        s = self.state
        rows = self.store.outbox(session)
        exits = perf["exits"]
        report = {"session": session, "generated_at": datetime.now(timezone.utc).isoformat(),
                  "mode": "EXECUTE" if self.broker else "PLAN", "nav": acct.nav if acct else s.get("last_nav"),
                  "day_start_nav": s.get("day_start_nav"), "high_water": s.get("high_water"), "halted": s.get("halted"),
                  "locked_day": s.get("locked_day"), "account_pnl_since_start": perf.get("account_pnl"),
                  "orders_filled": sum(1 for r in rows if r.get("event") == "outcome" and (r.get("filled") or 0) > 0),
                  "order_errors": s.get("order_errors_today", 0),
                  "runner_errors": sum(1 for r in rows if r.get("event") == "runner_error"),
                  "exit_performance": {"status": exits["status"], "since_start": exits["totals"],
                                       "session_fills": [f for f in exits["fills"] if f["session"] == session],
                                       "profit_target_activations_since_enabled": s.get("take_profit_counts", {})},
                  "sleeves": {k: {"capital": v["capital"], "gross_pnl": v["gross_pnl"], "realized": v["realized_gross"],
                                  "unrealized": v["unrealized_gross"], "status": v["status"],
                                  "positions": {sym: q["qty"] for sym, q in v["positions"].items()}}
                              for k, v in perf["sleeves"].items()}}
        path = self.store.dir / "reports" / f"{session}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
        log(daily_report=report)
        try:
            import shutil
            import subprocess
            nav = report["nav"]
            text = f"NAV {nav:,.2f}" if isinstance(nav, (int, float)) else "NAV unknown"
            text += " · halted" if report["halted"] else ""
            title = f"Competition {session} closed"
            if sys.platform == "darwin":
                subprocess.run(["/usr/bin/osascript", "-e", f'display notification "{text}" with title "{title}"'],
                               timeout=10, capture_output=True)
            elif sys.platform.startswith("linux") and (notify := shutil.which("notify-send")):
                subprocess.run([notify, title, text], timeout=10, capture_output=True)
        except Exception:                                    # noqa: BLE001 - a notification never affects trading
            pass

    def is_flat(self, sleeves: tuple) -> bool:
        return not any(q for sl in sleeves for q in self.state["sleeves"].get(sl, {}).values())

    def replay_intraday(self, day: date):
        """PLAN-only research replay of one completed session from Moomoo bars: VWAP, ORB and Noise-area trades and
        HYPOTHETICAL P&L under the research cost model on a fixed $100,000 book at the reference sizes (Noise-area 40%,
        VWAP/ORB risk sizing). Never a broker result."""
        if self.broker:
            return log(replay="refused", why="replays are PLAN-only")
        opened, closed, n = self._day(day)
        out = {"day": day.isoformat(), "vwap": [], "orb": [], "noise": [], "basis": "hypothetical, research cost model, $100k book"}
        bars = {}
        for sym in sorted(set(self.cfg["orb"]["symbols"]) | set(self.cfg["vwap"]["symbols"])):
            rows = [b for b in self.market.five_minute(sym, day, day) if b.end.date() == day]
            a = I.day_arrays(rows, opened)
            bars[sym] = (a, I.cum_vwap(a["volume"], a["turnover"], a["close"]))
        kept = []
        for sym in self.cfg["vwap"]["symbols"]:
            a, vw = bars[sym]
            cands, _ = I.s2_scan(a, vw, self._atr(sym, day), n, self.cfg["vwap"], I.SLOTS - 1, 100_000.0, 20_000.0)
            kept += [{**c, "symbol": sym} for c in cands]
        for c in I.concurrency(kept, self.cfg["vwap"]["max_open"]):
            out["vwap"].append({"symbol": c["symbol"], "side": c["side"], "qty": c["qty"], "entry": c["entry"],
                                "fills": c["fills"], "pnl": round(I.replay_pnl(c["side"], c["qty"], c["entry"], c["fills"]), 2)})
        co = self.cfg["orb"]
        kr = co["range_minutes"] // 5
        for sym in co["symbols"]:
            a, vw = bars[sym]
            base = [x if x is not None else math.nan for x in self._orb_base(sym, day, kr)]
            rvol = I.orb_rvol(I.first_range_volume(a["volume"], kr), base)
            t, _, skip = I.orb_scan(a, vw, self._atr(sym, day), rvol, n, co, I.SLOTS - 1, 100_000.0, float(co["max_notional"]))
            if t:
                out["orb"].append({"symbol": sym, "side": t["side"], "qty": t["qty"], "entry": t["entry"], "fills": t["fills"],
                                   "pnl": round(I.replay_pnl(t["side"], t["qty"], t["entry"], t["fills"]), 2)})
        cn = {**self.cfg["noise"]}
        prior, today_bars = self.market.b3_inputs(cn["symbol"], day, cn["lookback_sessions"], replay=True)
        prev_day = [s for s in sessions_until(day) if s < day][-1]
        prev_close = float(self.market.daily(cn["symbol"], prev_day - timedelta(days=7), prev_day, 0).close.loc[prev_day])
        a = I.day_arrays(today_bars, opened)
        pos, entry, qty = 0, None, 0
        for tick in I.noise_decision_times(opened, closed, cn["decision_times"]):
            d = I.noise_decision(today_bars, prior, prev_close, tick, opened, cn)
            j = I.last_completed_slot(opened, tick, n)
            if d["target"] is None or d["target"] == pos or not math.isfinite(a["open"][j + 1]):
                continue
            px = float(a["open"][j + 1])
            if pos:
                out["noise"].append({"side": pos, "qty": qty, "entry": entry, "exit": px,
                                     "pnl": round(I.replay_pnl(pos, qty, entry, px), 2)})
            pos = d["target"]
            if pos:
                entry, qty = px, I.noise_qty(0.40 * 100_000, px, a["open"][0])     # reference "alloc 0.4"
        if pos:
            px = float(a["open"][n - 1])
            out["noise"].append({"side": pos, "qty": qty, "entry": entry, "exit": px, "pnl": round(I.replay_pnl(pos, qty, entry, px), 2)})
        out["pnl"] = {k: round(sum(x["pnl"] for x in out[k]), 2) for k in ("noise", "vwap", "orb")}
        log(replay=out)
        return out

    def status(self):
        s = self.state
        acct = self.account() if self.broker else None
        log(mode="EXECUTE" if self.broker else "PLAN", execution_enabled=self.cfg["execution_enabled"],
            nav=acct.nav if acct else None, broker_positions=acct.positions if acct else None,
            attribution_errors=planner.attribution_errors(s, acct) if acct else [],
            allocation=self.cfg["allocation"], capital=self.attribute_capital(acct) if acct else None,
            initial_nav=s["initial_nav"], high_water=s["high_water"],
            halt_level=None if s["high_water"] is None else round(s["high_water"] * (1 - self.cfg["risk"]["halt_drawdown_fraction"]), 2),
            halted=s["halted"], locked_day=s["locked_day"], half_size=s["reduced"], sleeves=s["sleeves"],
            h_plan={k: v for k, v in (s.get("h_plan") or {}).items() if k != "metrics"},
            last_intents=self.store.outbox(self.store.latest_session() or "")[-6:])

    # ------------------------------------------------------------------ loop
    def guarded(self, name, fn, *args):
        """Run one loop step. Any failure (data, broker, order) is logged and recorded, never fatal to the loop:
        the runner must keep marking risk and must still flatten every intraday sleeve before the close.
        A market-data failure only skips the step (entries were never planned from missing data): it is recorded as
        step_skipped. Everything else (broker, order, state) is a runner_error that needs human review."""
        try:
            fn(*args)
            return True
        except Exception as exc:                         # noqa: BLE001 - supervision must survive every step failure
            session = self.session()
            event = "step_skipped" if isinstance(exc, MarketDataError) else "runner_error"
            log(step=name, error=f"{type(exc).__name__}: {exc}", event=event)
            try:
                self.store.append(session, {"event": event, "step": name, "error": f"{type(exc).__name__}: {exc}"})
            except Exception:                            # noqa: BLE001
                pass
            return False

    def loop(self):
        done, last_risk, last_monitor, retry_flat = set(), -1e9, -1e9, {}
        while True:
            now = datetime.now(NY)
            bounds = session_bounds(now.date())
            if not bounds or now >= bounds[1] + timedelta(minutes=20):
                if bounds and f"plan-h-{now.date()}" not in done:
                    self.guarded("plan-h", self.plan_h, now.date())
                    done.add(f"plan-h-{now.date()}")
                if bounds:
                    self.guarded("daily-report", self.daily_report, now.date().isoformat())
                log(loop="session over" if bounds else "no session today")
                return
            opened, closed = bounds
            prior = [s for s in sessions_until(now.date()) if s < now.date()][-1]
            plan = self.state.get("h_plan") or {}
            # Rebuild only a plan OLDER than the prior close (first live day or a missed evening). Today's own
            # evening plan is newer than `prior` and must never be overwritten by a stale one.
            if f"plan-h-prior-{now.date()}" not in done and (plan.get("asof") or "") < prior.isoformat():
                self.guarded("plan-h-prior", self.plan_h, prior)
                done.add(f"plan-h-prior-{now.date()}")
            if now < opened:
                time.sleep(min(30, (opened - now).total_seconds() + 1))
                continue
            if f"h-open-{now.date()}" not in done and now >= opened + timedelta(seconds=10):
                self.guarded("h-open", self.h_open)
                done.add(f"h-open-{now.date()}")
            for t in b3_decision_times(opened, closed, self.cfg["b3"]["decision_times"]):
                if f"b3-{t}" not in done and t + timedelta(seconds=15) <= now < t + timedelta(minutes=4):
                    self.guarded("b3", self.b3, t.strftime("%H:%M"))
                    done.add(f"b3-{t}")
            n = I.expected_slots(opened, closed)
            seen = now - timedelta(seconds=15)                        # a slot is used ~15 s after it closes
            if opened + timedelta(minutes=5) <= seen < closed:
                slot = I.last_completed_slot(opened, seen, n)
                if f"bar-{now.date()}-{slot}" not in done:
                    self.guarded("bar", self.bar_step, seen)            # ORB, then VWAP
                self.guarded("two-vote", self.vote_step)
            for t in I.noise_decision_times(opened, closed, self.cfg["noise"]["decision_times"]):
                if f"noise-{t}" not in done and t + timedelta(seconds=15) <= now < t + timedelta(minutes=4):
                    self.guarded("noise", self.noise, t.strftime("%H:%M"))
                    done.add(f"noise-{t}")
                    done.add(f"bar-{now.date()}-{slot}")
            if now < closed and time.monotonic() - last_monitor >= self.cfg["risk"].get("monitor_seconds", 10):
                self.guarded("take-profit", self.take_profit)
                self.guarded("monitor", self.monitor)
                last_monitor = time.monotonic()
            for name, at in (("vwap", closed - timedelta(minutes=5 * self.cfg["vwap"]["flatten_slots_before_close"])),
                             ("orb", closed - timedelta(minutes=5 * self.cfg["orb"]["flatten_slots_before_close"])),
                             ("noise", closed - timedelta(minutes=self.cfg["noise"]["flatten_minutes_before_close"])),
                             ("two_vote", closed - timedelta(minutes=self.cfg["two_vote"]["flatten_minutes_before_close"]))):
                if (f"flat-{name}-{now.date()}" not in done and at <= now < closed
                        and time.monotonic() >= retry_flat.get(name, -1e9)):
                    self.guarded(f"{name}-flatten", self.flatten_sleeves, (name,), f"{name}_scheduled_flatten")
                    if self.is_flat((name,)) or now >= closed - timedelta(minutes=1):
                        done.add(f"flat-{name}-{now.date()}")           # retried until 1 minute before the close
                    else:
                        retry_flat[name] = time.monotonic() + 20         # kept losing lots: re-check every 20 s
            flat = closed - timedelta(minutes=self.cfg["b3"]["flatten_minutes_before_close"])
            if f"flat-{now.date()}" not in done and flat <= now < closed:
                if self.guarded("b3-flatten", self.flatten, "b3", "b3_scheduled_flatten") or now >= closed - timedelta(minutes=1):
                    done.add(f"flat-{now.date()}")           # a failed flatten is retried on the next pass until 15:59
            if now < closed and time.monotonic() - last_risk >= 30:
                self.guarded("risk", self.risk)
                last_risk = time.monotonic()
            if closed + timedelta(minutes=15) <= now and f"plan-h-{now.date()}" not in done:
                self.guarded("plan-h", self.plan_h, now.date())
                done.add(f"plan-h-{now.date()}")
            time.sleep(5)

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["status", "plan-h", "h-open", "b3", "noise", "flatten", "loop", "replay-intraday"])
    ap.add_argument("--execute", action="store_true", help="send orders (also needs config + private account file)")
    ap.add_argument("--nav", type=float, default=100_000.0, help="PLAN mode virtual account NAV")
    ap.add_argument("--ephemeral", action="store_true", help="PLAN-only: keep state in a temp dir (writes nothing under private/)")
    ap.add_argument("--asof", type=date.fromisoformat)
    ap.add_argument("--at")
    ap.add_argument("--day", type=date.fromisoformat, help="PLAN-only: replay B3 on a completed past session")
    ap.add_argument("--sleeve", default="all", choices=["b3", "h_etf", "noise", "vwap", "orb", "two_vote", "all"])
    ap.add_argument("--reason", default="manual_flatten")
    a = ap.parse_args()
    r = Runner(a.execute, a.nav, a.ephemeral)
    {"status": r.status, "plan-h": lambda: r.plan_h(a.asof), "h-open": r.h_open, "b3": lambda: r.b3(a.at, a.day),
     "flatten": lambda: r.flatten(a.sleeve, a.reason), "loop": r.loop,
     "replay-intraday": lambda: r.replay_intraday(a.day), "noise": lambda: r.noise(a.at)}[a.command]()


if __name__ == "__main__":
    main()
