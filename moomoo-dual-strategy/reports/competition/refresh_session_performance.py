#!/usr/bin/env python3
import argparse
import csv
import json
import math
import statistics
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")


def load_json(path):
    return json.loads(Path(path).read_text())


def compute_sessions(equity_path, initial_nav):
    rows = []
    with Path(equity_path).open(newline="") as handle:
        for row in csv.DictReader(handle):
            rows.append((datetime.fromisoformat(row["at"]), float(row["nav"]), float(row["high_water"]), float(row["drawdown_pct"])))
    rows.sort(key=lambda item: item[0])
    grouped = defaultdict(list)
    for row in rows:
        grouped[row[0].astimezone(NY).date().isoformat()].append(row)
    sessions = []
    prior_nav = initial_nav
    for session, values in sorted(grouped.items()):
        last_nav = values[-1][1]
        sessions.append({
            "session": session,
            "last_runner_nav": last_nav,
            "session_return_pct": (last_nav / prior_nav - 1) * 100,
            "end_drawdown_pct": values[-1][3],
        })
        prior_nav = last_nav
    returns = [item["session_return_pct"] for item in sessions]
    sharpe = statistics.mean(returns) / statistics.stdev(returns) * math.sqrt(252) if len(returns) >= 2 and statistics.stdev(returns) else None
    return sessions, min((row[3] for row in rows), default=0.0), sharpe, rows[-1][1] if rows else None, len(rows)


def compute_exposure(audit_root):
    per_session = {}
    for path in sorted(Path(audit_root).glob("????-??-??/*.json")):
        try:
            record = load_json(path)
        except (OSError, ValueError):
            continue
        if record.get("kind") != "risk" or record.get("mode") != "EXECUTE":
            continue
        session = record.get("session")
        if session:
            per_session.setdefault(session, []).append(record)
    output = []
    total_records = 0
    total_observed = 0.0
    total_exposed = 0.0
    total_weighted = 0.0
    max_pct = 0.0
    max_gross = 0.0
    for session, records in sorted(per_session.items()):
        records.sort(key=lambda item: item["logged_at"])
        observed = exposed = weighted = 0.0
        session_max_pct = 0.0
        session_max_gross = 0.0
        for index, record in enumerate(records):
            account = record.get("account") or {}
            positions = account.get("positions") or {}
            quotes = account.get("quotes") or {}
            gross = sum(abs(float(qty)) * float(quotes[symbol]) for symbol, qty in positions.items()
                        if symbol in quotes and math.isfinite(float(quotes[symbol])))
            nav = account.get("broker_total")
            if not isinstance(nav, (int, float)) or not math.isfinite(float(nav)) or nav <= 0:
                nav = float(account.get("cash", 0)) + gross
            pct = gross / float(nav) * 100 if nav else 0.0
            session_max_pct = max(session_max_pct, pct)
            session_max_gross = max(session_max_gross, gross)
            if index + 1 < len(records):
                t0 = datetime.fromisoformat(record["logged_at"])
                t1 = datetime.fromisoformat(records[index + 1]["logged_at"])
                dt = (t1 - t0).total_seconds()
                if 0 < dt <= 300:
                    observed += dt
                    weighted += dt * pct
                    if gross > 0:
                        exposed += dt
        output.append({
            "session": session,
            "exposure_time_pct": round(exposed / observed * 100, 2) if observed else None,
            "duration_weighted_avg_gross_exposure_pct_of_nav": round(weighted / observed, 3) if observed else None,
            "max_gross_exposure_pct_of_nav": round(session_max_pct, 3),
        })
        total_records += len(records)
        total_observed += observed
        total_exposed += exposed
        total_weighted += weighted
        max_pct = max(max_pct, session_max_pct)
        max_gross = max(max_gross, session_max_gross)
    return {
        "risk_snapshots": total_records,
        "observed_seconds": round(total_observed, 1),
        "exposure_time_pct": round(total_exposed / total_observed * 100, 2) if total_observed else None,
        "duration_weighted_avg_gross_exposure_pct_of_nav": round(total_weighted / total_observed, 3) if total_observed else None,
        "max_gross_exposure_pct_of_nav": round(max_pct, 3),
        "max_gross_exposure_usd": round(max_gross, 2),
        "sessions": output,
        "method": "Duration-weighted intervals between EXECUTE risk snapshots; gross exposure is absolute quoted position value divided by broker NAV. Intervals over 300 seconds are excluded.",
    }


def compute_worst_lot(dashboard, positions):
    quotes = {}
    for row in positions.get("positions", []):
        symbol = row.get("symbol")
        if symbol:
            quotes[f"US.{symbol}"] = float(row["cur_price"])
    lots = []
    for lot in dashboard.get("open_positions", []):
        symbol = lot.get("symbol")
        if symbol not in quotes:
            continue
        qty = int(lot["qty"])
        entry = float(lot["entry_price"])
        mark = quotes[symbol]
        side = 1 if lot.get("side") == "long" else -1
        unrealized = (mark - entry) * qty * side
        lots.append({
            "symbol": symbol,
            "shares": qty,
            "entry_price": entry,
            "mark_price": mark,
            "unrealized_gross": round(unrealized, 2),
            "opened_at": lot.get("opened_at"),
        })
    return min(lots, key=lambda item: item["unrealized_gross"]) if lots else None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--equity", required=True)
    parser.add_argument("--audit-root", required=True)
    parser.add_argument("--dashboard", required=True)
    parser.add_argument("--positions", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    dashboard = load_json(args.dashboard)
    positions = load_json(args.positions)
    results = load_json(Path(args.equity).parent / "submission_results.json")
    initial_nav = float(results["account"]["initial_nav"])
    sessions, max_drawdown, sharpe, last_mark, equity_points = compute_sessions(args.equity, initial_nav)
    exposure = compute_exposure(args.audit_root)
    broker_nav = float((dashboard.get("broker_observation") or {}).get("nav") or results["account"]["nav"])
    final_done = any(item["session"] == "2026-11-27" for item in sessions)
    output = {
        "schema_version": 1,
        "generated_at": datetime.now(ZoneInfo("UTC")).isoformat(),
        "source": "moomoo-dual-strategy/reports/competition/submission_assets/submission_equity.csv",
        "initial_nav": initial_nav,
        "completed_sessions": len(sessions),
        "sessions": sessions,
        "max_observed_drawdown_pct": max_drawdown,
        "annualized_sharpe_session_returns": sharpe,
        "sharpe_caveat": f"Only {len(sessions)} completed session returns are available; the annualized figure is computable but not statistically meaningful and is not used as a strategy claim.",
        "current_broker_nav": broker_nav,
        "last_runner_mark": last_mark,
        "broker_minus_last_runner_mark": round(broker_nav - last_mark, 2) if last_mark is not None else None,
        "worst_open_lot": compute_worst_lot(dashboard, positions),
        "exposure_metrics": exposure,
        "final_session": "2026-11-27 half-day reconciled" if final_done else "2026-11-27 half-day pending",
    }
    Path(args.output).write_text(json.dumps(output, indent=2) + "\n")
    print(json.dumps({"completed_sessions": len(sessions), "equity_points": equity_points, "max_drawdown_pct": max_drawdown, "sharpe": sharpe, "exposure": exposure, "worst_open_lot": output["worst_open_lot"]}, indent=2))


if __name__ == "__main__":
    main()
