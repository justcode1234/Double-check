from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

from dual_strategy.competition.broker import account_cash

PROJECT = Path(__file__).resolve().parents[2]
ROOT = PROJECT.parent
OUT = Path(__file__).resolve().parent / "submission_assets"
NY = ZoneInfo("America/New_York")


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def outbox_rows():
    for path in sorted((PROJECT / "private/competition/live/outbox").glob("????-??-??.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                yield path.stem, json.loads(line)


def svg_line_chart(rows, path: Path, title: str, series, thresholds=None) -> None:
    width, height = 1100, 480
    left, right, top, bottom = 70, 30, 55, 65
    plot_width, plot_height = width - left - right, height - top - bottom
    xs = [datetime.fromisoformat(row[0]).timestamp() for row in rows]
    xmin, xmax = min(xs), max(xs)
    values = [value for _, serie, _, _ in series for value in serie]
    if thresholds:
        values += [item[0] for item in thresholds]
    ymin, ymax = min(values), max(values)
    pad = (ymax - ymin) * 0.12 or 1
    ymin, ymax = ymin - pad, ymax + pad

    def x(timestamp):
        return left + (timestamp - xmin) / (xmax - xmin or 1) * plot_width

    def y(value):
        return top + (ymax - value) / (ymax - ymin) * plot_height

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        f'<rect width="100%" height="100%" fill="#0B1220"/>',
        f'<text x="{left}" y="30" fill="#F8FAFC" font-family="sans-serif" font-size="20" font-weight="700">{escape(title)}</text>',
    ]
    for index in range(5):
        value = ymin + (ymax - ymin) * index / 4
        yy = y(value)
        parts.append(f'<line x1="{left}" y1="{yy:.2f}" x2="{width-right}" y2="{yy:.2f}" stroke="#334155" opacity="0.55"/>')
        parts.append(f'<text x="{left-10}" y="{yy+5:.2f}" text-anchor="end" fill="#94A3B8" font-family="sans-serif" font-size="12">{value:,.0f}</text>')
    for value, label, color in thresholds or []:
        yy = y(value)
        parts.append(f'<line x1="{left}" y1="{yy:.2f}" x2="{width-right}" y2="{yy:.2f}" stroke="{color}" stroke-width="1.5" stroke-dasharray="7 6"/>')
        parts.append(f'<text x="{width-right-6}" y="{yy-6:.2f}" text-anchor="end" fill="{color}" font-family="sans-serif" font-size="12">{escape(label)}</text>')
    for label, serie, color, dash in series:
        points = " ".join(f"{x(timestamp):.2f},{y(value):.2f}" for timestamp, value in zip(xs, serie))
        parts.append(f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="2.4" stroke-dasharray="{dash}"/>')
    for index in range(5):
        timestamp = xmin + (xmax - xmin) * index / 4
        label = datetime.fromtimestamp(timestamp, NY).strftime("%b %d %H:%M")
        parts.append(f'<text x="{x(timestamp):.2f}" y="{height-bottom+28}" text-anchor="middle" fill="#94A3B8" font-family="sans-serif" font-size="12">{escape(label)}</text>')
    legend_x = left
    for label, _, color, dash in series:
        parts.append(f'<line x1="{legend_x}" y1="{height-18}" x2="{legend_x+26}" y2="{height-18}" stroke="{color}" stroke-width="3" stroke-dasharray="{dash}"/>')
        parts.append(f'<text x="{legend_x+34}" y="{height-13}" fill="#CBD5E1" font-family="sans-serif" font-size="13">{escape(label)}</text>')
        legend_x += 220
    parts.append("</svg>")
    path.write_text("".join(parts), encoding="utf-8")


def backtest_chart(path: Path) -> None:
    research = load(PROJECT / "reports/competition/exit_backtest/research_costs.json")
    variants = [
        ("H  no target, no stop (flip + close only)", "H · no target/stop", "#2DD4BF"),
        ("A  current: $100 target + vote flip + 04:55 close", "A · $100 target", "#60A5FA"),
        ("K  until tonight: $10k lots x4, $50 target, fresh-agreement entries", "K · old $10k/$50", "#F59E0B"),
    ]
    periods = ["2021-2026 (5.9 yrs)", "2025-2026", "last 3 months"]
    labels = ["2021–2026", "2025–2026", "Last 3 months"]
    width, height = 1100, 480
    left, right, top, bottom = 70, 30, 55, 70
    plot_width, plot_height = width - left - right, height - top - bottom
    all_values = [research["variants"][key][period]["expectancy_usd"] for key, _, _ in variants for period in periods]
    ymin, ymax = min(all_values + [0]) - 2, max(all_values + [0]) + 3

    def y(value):
        return top + (ymax - value) / (ymax - ymin) * plot_height

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        f'<rect width="100%" height="100%" fill="#0B1220"/>',
        f'<text x="{left}" y="30" fill="#F8FAFC" font-family="sans-serif" font-size="20" font-weight="700">Exit research · expectancy per trade by regime</text>',
    ]
    for index in range(5):
        value = ymin + (ymax - ymin) * index / 4
        yy = y(value)
        parts.append(f'<line x1="{left}" y1="{yy:.2f}" x2="{width-right}" y2="{yy:.2f}" stroke="#334155" opacity="0.55"/>')
        parts.append(f'<text x="{left-10}" y="{yy+5:.2f}" text-anchor="end" fill="#94A3B8" font-family="sans-serif" font-size="12">${value:.0f}</text>')
    group = plot_width / 3
    bar_width = 72
    for group_index, (period, label) in enumerate(zip(periods, labels)):
        center = left + group * (group_index + 0.5)
        for variant_index, (key, _, color) in enumerate(variants):
            value = research["variants"][key][period]["expectancy_usd"]
            x = center + (variant_index - 1) * 90
            yy, zero = y(value), y(0)
            top_y, bar_height = min(yy, zero), abs(yy - zero)
            parts.append(f'<rect x="{x-bar_width/2:.2f}" y="{top_y:.2f}" width="{bar_width}" height="{bar_height:.2f}" rx="5" fill="{color}"/>')
            parts.append(f'<text x="{x:.2f}" y="{(yy-8 if value >= 0 else yy+18):.2f}" text-anchor="middle" fill="#F8FAFC" font-family="sans-serif" font-size="13" font-weight="700">${value:.2f}</text>')
        parts.append(f'<text x="{center:.2f}" y="{height-bottom+32}" text-anchor="middle" fill="#CBD5E1" font-family="sans-serif" font-size="14">{escape(label)}</text>')
    legend_x = left
    for _, label, color in variants:
        parts.append(f'<rect x="{legend_x}" y="{height-22}" width="18" height="18" rx="4" fill="{color}"/>')
        parts.append(f'<text x="{legend_x+26}" y="{height-8}" fill="#CBD5E1" font-family="sans-serif" font-size="13">{escape(label)}</text>')
        legend_x += 250
    parts.append("</svg>")
    path.write_text("".join(parts), encoding="utf-8")


def main() -> None:
    global OUT
    parser = argparse.ArgumentParser()
    parser.add_argument("--funds", type=Path, required=True)
    parser.add_argument("--positions", type=Path, required=True)
    parser.add_argument("--orders", type=Path, required=True)
    parser.add_argument("--dashboard", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=OUT)
    parser.add_argument("--final", action="store_true")
    args = parser.parse_args()
    OUT = args.output_dir
    OUT.mkdir(parents=True, exist_ok=True)

    funds = load(args.funds)
    positions = load(args.positions).get("positions", [])
    orders_doc = load(args.orders)
    orders = orders_doc.get("data", {}).get("orders", orders_doc.get("orders", []))
    dashboard = load(args.dashboard)
    state = load(PROJECT / "private/competition/live/state.json")
    previous = load(OUT / "submission_results.json") if (OUT / "submission_results.json").exists() else {}
    performance = dashboard["performance"]
    totals = performance["exits"]["totals"]

    outcome_by_intent = {}
    decision_rows = []
    for session, row in outbox_rows():
        event = row.get("event")
        if event == "outcome":
            outcome_by_intent[row.get("intent_id")] = row
        if event in ("intent", "long_sale_held", "order_error", "order_refused", "runner_error", "step_skipped", "broker_reconciliation"):
            decision_rows.append({
                "session": session,
                "at": row.get("created_at", row.get("logged_at")),
                "event": event,
                "sleeve": row.get("sleeve"),
                "symbol": row.get("symbol"),
                "side": row.get("side"),
                "qty": row.get("qty", row.get("shares")),
                "reason": row.get("reason", row.get("source", row.get("step"))),
                "detail": json.dumps({key: value for key, value in row.items() if key not in ("logged_at", "created_at", "event", "audit_id", "intent_id", "sleeve", "symbol", "side", "qty", "shares", "reason", "source", "step")}, sort_keys=True),
            })
    for decision in state.get("intraday", {}).get("decisions", []):
        decision_rows.append({
            "session": state["intraday"]["session"],
            "at": decision.get("at"),
            "event": "state_decision",
            "sleeve": decision.get("sleeve"),
            "symbol": decision.get("symbol"),
            "side": decision.get("side"),
            "qty": None,
            "reason": decision.get("action"),
            "detail": json.dumps({key: value for key, value in decision.items() if key not in ("at", "sleeve", "symbol", "side", "action")}, sort_keys=True),
        })
    decision_rows.sort(key=lambda row: (row["session"], row["at"] or ""))
    with (OUT / "submission_decision_log.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(decision_rows[0]))
        writer.writeheader()
        writer.writerows(decision_rows)

    trade_rows = []
    for trade in performance.get("trades", []):
        outcome = outcome_by_intent.get(trade.get("intent_id"), {})
        trade_rows.append({
            "session": trade["session"],
            "ordered_at_et": trade["ordered_at"],
            "sleeve": trade["sleeve"],
            "symbol": trade["symbol"],
            "action": trade["action"],
            "side": trade["side"],
            "shares": trade["shares"],
            "avg_price": trade["avg_price"],
            "notional": round(trade["notional"], 2),
            "realized_gross": trade.get("realized_gross"),
            "reason": trade["reason"],
            "source": trade["source"],
            "status": trade["status"],
            "rules": "; ".join(trade.get("rules", [])),
            "verified": trade["verified"],
            "broker_order_id": outcome.get("order_id"),
            "audit_id": trade["audit_id"],
            "intent_id": trade["intent_id"],
        })
    trade_rows.sort(key=lambda row: row["ordered_at_et"])
    with (OUT / "submission_trade_log.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(trade_rows[0]))
        writer.writeheader()
        writer.writerows(trade_rows)

    basis_by_symbol = {}
    for sleeve in performance.get("sleeves", {}).values():
        for symbol, position in (sleeve.get("positions") or {}).items():
            if position.get("cost_basis") is not None:
                basis_by_symbol[symbol] = float(position["cost_basis"])
                basis_by_symbol[symbol.replace("US.", "")] = float(position["cost_basis"])
    public_positions = []
    for row in positions:
        quantity = int(float(row["qty"]))
        price = float(row["cur_price"])
        market_value = float(row["mv"])
        cost_basis = basis_by_symbol.get(row["symbol"], round(float(row["cost_price"]) * abs(quantity), 2))
        public_positions.append({
            "symbol": row["symbol"],
            "qty": quantity,
            "current_price": price,
            "market_value": market_value,
            "cost_basis": round(cost_basis, 2),
            "unrealized_gross": round(market_value - cost_basis, 2),
        })
    nav = float(funds["total_asset"])
    cash = account_cash(funds)
    high_water = float(state["high_water"])
    day_start = float(state["day_start_nav"])
    open_unrealized = round(sum(position["unrealized_gross"] for position in public_positions), 2)
    daily_orders = Counter(datetime.fromtimestamp(int(order["create_time"]) / 1e6, tz=timezone.utc).astimezone(NY).date().isoformat() for order in orders)
    status_counts = Counter(str(order["status"]) for order in orders)
    broker_ids = {str(order.get("order_id")) for order in orders if order.get("order_id") is not None}
    ledger_ids = {str(row["broker_order_id"]) for row in trade_rows if row.get("broker_order_id") is not None}
    previous_reconciliation = previous.get("order_reconciliation", {})
    results = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Moomoo x Qoder competition paper account; public/redacted submission snapshot",
        "account": {
            "initial_nav": float(state["initial_nav"]),
            "nav": nav,
            "cash": cash,
            "market_value": round(sum(position["market_value"] for position in public_positions), 2),
            "net_change_usd": round(nav - float(state["initial_nav"]), 2),
            "net_change_pct": round(100 * (nav / float(state["initial_nav"]) - 1), 4),
            "high_water": high_water,
            "drawdown_pct": round(100 * (nav / high_water - 1), 4),
            "day_start_nav": day_start,
            "day_change_pct": round(100 * (nav / day_start - 1), 4),
            "halted": state["halted"],
            "locked_day": state["locked_day"],
        },
        "positions": public_positions,
        "open_unrealized_gross": open_unrealized,
        "performance": {
            "realized_gross": round(totals["realized_gross"], 2),
            "closed_trade_count": totals["closed_trade_count"],
            "win_rate_pct": round(totals["win_rate_pct"], 2),
            "average_win": round(totals["average_win"], 2),
            "average_loss": round(totals["average_loss"], 2),
            "expectancy_dollars": round(totals["expectancy_dollars"], 2),
            "profit_factor": round(totals["profit_factor"], 4),
            "automatic_exits": totals["automatic_exits"],
            "manual_exits": totals["manual_exits"],
        },
        "orders": {
            "total": len(orders),
            "status_counts": dict(status_counts),
            "by_session_et": dict(sorted(daily_orders.items())),
            "all_terminal": all(order["status"] in (4, 5, 7) for order in orders),
        },
        "config": {
            "two_vote_initial_allocation_usd": dashboard["config"]["two_vote"]["initial_allocation_usd"],
            "entry_usd": dashboard["config"]["two_vote"]["entry_usd"],
            "name_cap_usd": dashboard["config"]["risk"]["name_cap_usd"],
            "name_cap_fraction_of_nav": dashboard["config"]["risk"]["name_cap_fraction_of_nav"],
            "daily_loss_fraction": dashboard["config"]["risk"]["daily_loss_fraction"],
            "reduce_drawdown_fraction": dashboard["config"]["risk"]["reduce_drawdown_fraction"],
            "halt_drawdown_fraction": dashboard["config"]["risk"]["halt_drawdown_fraction"],
            "long_sales_only_at_profit": dashboard["config"]["risk"]["long_sales_only_at_profit"],
            "short_twins": dashboard["config"]["two_vote"]["short_twins"],
        },
        "known_issues": [
            {
                "issue": "2026-11-25 native scheduler recorded unexpected_exit (-15) at 10:57 ET. The runner restarted twice after source edits, leaving 48.8s and 40.7s audit-telemetry gaps; no outbox event or broker order occurred in either gap, all 11 session fills reconcile, and the daily report completed.",
                "source": "private/competition/scheduler/2026-11-25.json",
                "evidence": "moomoo-dual-strategy/reports/competition/submission_assets/scheduler_reconciliation_2026-11-25.json",
                "status": "operationally_reconciled_with_disclosed_gaps",
                "residual_limitation": "The restart initiator is not recorded in available journal or shell-history evidence.",
            },
            {
                "issue": "Short-open NAV mismatch affected 148 risk records on 24 Nov and 12 on 25 Nov; entries and loss-lock/halt evaluation were blocked until the account_cash correction in the current working tree.",
                "source": "src/dual_strategy/competition/broker.py account_cash; 2026-11-25 decision log",
                "evidence": {"2026-11-24_mismatched_risk_records": 148, "2026-11-25_mismatched_risk_records": 12},
            },
            {
                "issue": "Full local suite has one Node-dependent dashboard DOM test failure because node is absent from PATH; browser manual checks have no console errors.",
                "source": "tests/test_dashboard.py:1004",
            },
            {
                "issue": "Cloud automation task creation is confirmed, but execution outputs are not exposed to either the Linux or Windows agent; no completed Cloud output is claimed.",
                "source": "moomoo-dual-strategy/reports/competition/submission_assets/cloud_audit_runs.json",
            },
        ],
        "limitations": [
            "Paper fills do not predict live returns.",
            "Independent rule implementation matches live signal code on 264 identical-input checks; current Moomoo historical bars reproduce 189/264 recorded rows, with 75 provider-revision differences disclosed in independent_live_vote_audit.json.",
            "Two one-time Cloud automations were created on 27 Nov; task creation is confirmed, but execution outputs are not exposed to the Linux or Windows agent and are not claimed as completed evidence.",
        ] + ([] if args.final else ["The 27 November half-day final session is not yet included.", "Final settlement will use regular-market closing prices."]),
        "position_cost_method": "Identified entry-lot cost basis from the runner ledger; Moomoo cumulative position cost/profit fields are not used when dashboard lot basis is available.",
        "reconciliation": {
            "realized_gross": round(totals["realized_gross"], 2),
            "open_unrealized_gross": open_unrealized,
            "gross_total": round(totals["realized_gross"] + open_unrealized, 2),
            "account_net_change_usd": round(nav - float(state["initial_nav"]), 2),
            "implied_fees_and_other_cash_flows_usd": round(nav - float(state["initial_nav"]) - totals["realized_gross"] - open_unrealized, 2),
        },
        "test_summary": previous.get("test_summary", {}),
        "order_reconciliation": {
            "broker_orders": len(orders),
            "ledger_orders": len(trade_rows),
            "matched_order_ids": len(broker_ids & ledger_ids),
            "missing_from_broker": sorted(ledger_ids - broker_ids),
            "missing_from_ledger": sorted(broker_ids - ledger_ids),
            "status_legend": {"4": "FILLED", "5": "CANCELLED", "7": "REJECTED"},
            "status_counts": dict(Counter("FILLED" if str(order["status"]) == "4" else "CANCELLED" if str(order["status"]) in ("5", "7") else "OTHER" for order in orders)),
            "open_orders": sum(1 for order in orders if str(order["status"]) in ("1", "2", "3", "6")),
            "attribution_errors": [],
            "partial_fill_evidence": previous_reconciliation.get("partial_fill_evidence"),
            "trade_log_crosswalk": "submission_trade_log.csv broker_order_id",
            "sources": ["private/competition/live/outbox/*.jsonl", "Moomoo sim order history", "private/competition/live/state.json"],
        },
    }
    write_json(OUT / "submission_results.json", results)

    points = []
    for path in sorted((PROJECT / "private/competition/live/audit").glob("????-??-??/[!.]*.json")):
        try:
            record = load(path)
        except (OSError, ValueError):
            continue
        if record.get("kind") != "risk" or record.get("mode") != "EXECUTE":
            continue
        after = record.get("state_after") or {}
        if after.get("last_nav") is None or after.get("high_water") is None or after.get("last_mark_at") is None:
            continue
        points.append((datetime.fromisoformat(after["last_mark_at"]), float(after["last_nav"]), float(after["high_water"])))
    points = sorted(set(points))
    with (OUT / "submission_equity.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["at", "nav", "high_water", "drawdown_pct"])
        for at, nav_value, high_water_value in points:
            writer.writerow([at.isoformat(), nav_value, high_water_value, 100 * (nav_value / high_water_value - 1)])
    if points:
        rows = [(at.isoformat(), nav_value, high_water_value, 100 * (nav_value / high_water_value - 1)) for at, nav_value, high_water_value in points]
        svg_line_chart(rows, OUT / "equity_curve.svg", "Live paper equity · verified EXECUTE risk snapshots", [("Account NAV", [row[1] for row in rows], "#2DD4BF", ""), ("High-water mark", [row[2] for row in rows], "#F59E0B", "8 6")])
        svg_line_chart(rows, OUT / "drawdown_curve.svg", "Drawdown from high-water · live risk snapshots", [("Drawdown %", [row[3] for row in rows], "#F87171", "")], [(-2.5, "Daily lock -2.5%", "#F59E0B"), (-4.0, "Reduce -4.0%", "#FB923C"), (-7.0, "Halt -7.0%", "#EF4444")])
    backtest_chart(OUT / "backtest_expectancy.svg")
    print(json.dumps({
        "trade_rows": len(trade_rows),
        "decision_rows": len(decision_rows),
        "equity_points": len(points),
        "nav": nav,
        "net_change_usd": round(nav - float(state["initial_nav"]), 2),
        "open_unrealized_gross": open_unrealized,
        "realized_gross": round(totals["realized_gross"], 2),
        "orders": len(orders),
        "matched_order_ids": len(broker_ids & ledger_ids),
    }, indent=2))


if __name__ == "__main__":
    main()
