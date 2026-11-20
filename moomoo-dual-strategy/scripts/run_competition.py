"""Competition runner: B3 (SPY Noise-Area, 80%) + H (ETF RSI-2 panel, 20%) with a 3.5% account drawdown halt.

The TOOL decides, sizes, gates and (only with --execute) sends orders; an agent may launch, monitor or stop it but
never places or edits orders by hand. Default is PLAN: intents are written to private/competition/plan/outbox and
nothing is sent. Commands (America/New_York times):

  status                      config, risk state, sleeves, last intents
  plan-h [--asof DATE]        H plan from DATE's completed close (default: last completed session) for the next open
  h-open                      next-open H exits/entries from the stored plan            (run 09:30:10 ET)
  b3 [--at HH:MM]             B3 decision at a completed 5-minute bar (default: latest decision tick today)
  flatten [--sleeve b3|h_etf|all] [--reason TEXT]   close-only sells of what the sleeve owns
  loop                        all of the above on the XNYS clock until the session ends

Add --execute to send orders. That also requires execution_enabled: true in config/competition.yaml and the private
competition account file; otherwise the runner refuses before contacting any account.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from dual_strategy.competition import planner  # noqa: E402
from dual_strategy.competition.market import Market, MarketDataError  # noqa: E402
from dual_strategy.competition.signals import b3_decision, b3_decision_times, h_plan  # noqa: E402
from dual_strategy.competition.store import Store, load_config  # noqa: E402
from dual_strategy.data.calendar import NY, session_bounds  # noqa: E402

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
    def __init__(self, execute: bool, nav: float):
        self.cfg = load_config(PROJECT)
        self.execute = execute
        mode = "live" if execute else "plan"
        self.cfg = {**self.cfg, "state_dir": f"{self.cfg['state_dir']}/{mode}"}
        self.store = Store(PROJECT, self.cfg)
        self.state = self.store.load()
        self.market = Market()
        self.broker = None
        self.virtual_nav = nav
        if execute:
            from dual_strategy.competition.broker import CompetitionBroker      # gated: raises unless all locks open
            self.broker = CompetitionBroker(PROJECT, self.cfg, execute=True)

    # ------------------------------------------------------------------ account
    def quote(self, symbol):
        return self.market.quote(symbol)

    def account(self, extra: list[str] = ()) -> planner.Account:
        if self.broker:
            acct = self.broker.account(self.quote)
        else:                                            # PLAN: a virtual account holding exactly the sleeves' shares
            held = {}
            for sleeve in planner.SLEEVES:
                for s, q in self.state["sleeves"][sleeve].items():
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

    def session(self, now=None) -> str:
        return (now or datetime.now(timezone.utc)).astimezone(NY).date().isoformat()

    # ------------------------------------------------------------------ orders
    def send(self, intents, session):
        for i in intents:
            self.store.append(session, {"event": "intent", **i.to_dict(), "mode": "EXECUTE" if self.broker else "PLAN"})
            if not self.broker:
                log(intent=i.to_dict(), sent=False)
                continue
            placed = datetime.now(timezone.utc)
            try:
                oid = self.broker.place(i)
                self.state["orders_today"] += 1
                result = self.broker.wait(oid, placed, self.cfg["risk"]["order_wait_seconds"])
            except Exception as exc:                     # ambiguous: block entries until reconciled by a human
                self.state["halted"] = self.state["halted"] or {"at": placed.isoformat(), "reason": f"order error: {exc}"}
                self.store.append(session, {"event": "order_error", "intent_id": i.intent_id, "error": str(exc)})
                self.store.save(self.state)
                raise
            planner.apply_fill(self.state, i, result["filled"], session)
            self.store.append(session, {"event": "outcome", "intent_id": i.intent_id, **result})
            log(intent=i.intent_id, outcome=result)
        self.store.save(self.state)

    # ------------------------------------------------------------------ risk
    def risk(self, symbols=()):
        acct = self.account(list(symbols))
        session = self.session()
        reason = planner.mark_risk(self.state, acct, session, self.cfg)
        self.store.save(self.state)
        if reason:
            intents = planner.plan_flatten(self.state, acct, session, reason, self.cfg, datetime.now(NY))
            log(risk=reason, nav=acct.nav, high_water=self.state["high_water"], flatten=len(intents))
            self.send(intents, session)
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
            self.store.save(self.state)
            return log(h_plan=self.state["h_plan"])
        p = h_plan(panel, asof, held, sessions_until(asof), c)
        self.state["h_plan"] = {"asof": asof.isoformat(), "universe": p.universe, "entries": p.entries,
                                "exits": p.exits, "blocked": p.blocked,
                                "metrics": {s: {k: (round(v, 4) if isinstance(v, float) else v) for k, v in m.items()}
                                            for s, m in p.metrics.items()}}
        self.store.save(self.state)
        log(h_plan={k: v for k, v in self.state["h_plan"].items() if k != "metrics"})

    def h_open(self):
        plan = self.state.get("h_plan")
        session = self.session()
        prior = [s for s in sessions_until(date.fromisoformat(session)) if s < date.fromisoformat(session)][-1]
        if not plan or plan.get("blocked") or plan["asof"] != prior.isoformat():
            return log(h_open="skipped", why="no unblocked plan from the previous close", plan_asof=plan and plan.get("asof"))
        acct = self.risk(plan["entries"] + list(plan["exits"]))
        intents, why = planner.plan_h_open(self.state, acct, session, plan, self.cfg, datetime.now(NY))
        log(h_open=why, intents=len(intents))
        self.send(intents, session)

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
            return log(b3="blocked", why=str(exc))
        from dual_strategy.research.data import dividends   # cached corporate actions (SPY ex-dates through 2026-09)
        dividend = dividends(sym).get(today, 0.0)
        decision = b3_decision(today_bars, prior, float(prev_close), dividend, tick, bounds[0],
                               self.cfg["b3"]["lookback_sessions"], self.cfg["b3"]["band_width"])
        acct = self.risk([sym])
        intents, why = planner.plan_b3(self.state, acct, today.isoformat(), decision, self.cfg, now)
        log(b3={"at": key, "target": decision.target, "reason": decision.reason,
                "metrics": {k: round(v, 5) for k, v in decision.metrics.items() if isinstance(v, float)}}, plan=why)
        self.send(intents, today.isoformat())
        if self.broker:
            self.state["b3_done"].append(key)
            self.store.save(self.state)

    def flatten(self, sleeve: str, reason: str):
        sleeves = planner.SLEEVES if sleeve == "all" else (sleeve,)
        acct = self.account([s for sl in sleeves for s in self.state["sleeves"][sl]])
        intents = planner.plan_flatten(self.state, acct, self.session(), reason, self.cfg, datetime.now(NY), sleeves)
        self.send(intents, self.session())

    def status(self):
        s = self.state
        log(mode="EXECUTE" if self.broker else "PLAN", execution_enabled=self.cfg["execution_enabled"],
            allocation=self.cfg["allocation"], initial_nav=s["initial_nav"], high_water=s["high_water"],
            halt_level=None if s["high_water"] is None else round(s["high_water"] * (1 - self.cfg["risk"]["halt_drawdown_fraction"]), 2),
            halted=s["halted"], locked_day=s["locked_day"], half_size=s["reduced"], sleeves=s["sleeves"],
            h_plan={k: v for k, v in (s.get("h_plan") or {}).items() if k != "metrics"},
            last_intents=self.store.outbox(self.store.latest_session() or "")[-6:])

    # ------------------------------------------------------------------ loop
    def loop(self):
        done = set()
        while True:
            now = datetime.now(NY)
            bounds = session_bounds(now.date())
            if not bounds or now >= bounds[1] + timedelta(minutes=20):
                if bounds and f"plan-h-{now.date()}" not in done:
                    self.plan_h(now.date())
                    done.add(f"plan-h-{now.date()}")
                log(loop="session over" if bounds else "no session today")
                return
            opened, closed = bounds
            if now < opened:
                time.sleep(min(30, (opened - now).total_seconds() + 1))
                continue
            if f"h-open-{now.date()}" not in done and now >= opened + timedelta(seconds=10):
                self.h_open()
                done.add(f"h-open-{now.date()}")
            for t in b3_decision_times(opened, closed, self.cfg["b3"]["decision_times"]):
                if f"b3-{t}" not in done and t + timedelta(seconds=15) <= now < t + timedelta(minutes=4):
                    self.b3(t.strftime("%H:%M"))
                    done.add(f"b3-{t}")
            flat = closed - timedelta(minutes=self.cfg["b3"]["flatten_minutes_before_close"])
            if f"flat-{now.date()}" not in done and flat <= now < closed:
                self.flatten("b3", "b3_scheduled_flatten")
                done.add(f"flat-{now.date()}")
            if now < closed:
                self.risk()
            if closed + timedelta(minutes=15) <= now and f"plan-h-{now.date()}" not in done:
                self.plan_h(now.date())
                done.add(f"plan-h-{now.date()}")
            time.sleep(30)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["status", "plan-h", "h-open", "b3", "flatten", "loop"])
    ap.add_argument("--execute", action="store_true", help="send orders (also needs config + private account file)")
    ap.add_argument("--nav", type=float, default=100_000.0, help="PLAN mode virtual account NAV")
    ap.add_argument("--asof", type=date.fromisoformat)
    ap.add_argument("--at")
    ap.add_argument("--day", type=date.fromisoformat, help="PLAN-only: replay B3 on a completed past session")
    ap.add_argument("--sleeve", default="all", choices=["b3", "h_etf", "all"])
    ap.add_argument("--reason", default="manual_flatten")
    a = ap.parse_args()
    r = Runner(a.execute, a.nav)
    {"status": r.status, "plan-h": lambda: r.plan_h(a.asof), "h-open": r.h_open, "b3": lambda: r.b3(a.at, a.day),
     "flatten": lambda: r.flatten(a.sleeve, a.reason), "loop": r.loop}[a.command]()


if __name__ == "__main__":
    main()
