"""Two-vote exit-variant backtest on cached SPY/QQQ 5-minute research bars (HYPOTHETICAL replay P&L).

Reuses the repo's replay engine for votes, sizing caps and the research cost model
(scripts/competition_vote_replay.py, competition/intraday.replay_pnl). Adds per-lot intrabar exits:
profit target, stop-loss and trailing stop, judged on each 5-minute bar's high/low after the entry.
Conservative intrabar order: a stop/trail touch in the same bar as a target touch counts as the stop.
Gaps through a level fill at the bar open. Entries and vote/EOD exits fill at bar opens (as the replay).

Run from moomoo-dual-strategy (reads the committed data-cache):
    PYTHONPATH=src .venv/bin/python scripts/competition_exit_backtest.py reports/competition/exit_backtest/research_costs.json
    FR=0 ... fees_only.json          # paper-like fills: no slippage, fees kept
    VOTES=3 ONLY=AHDI ... votes3.json   # signal sensitivity on a subset of variants
Decision taken from it (2026-11-25): variant H, no profit target and no stop for Two-vote.
"""
import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path

PROJECT = Path.cwd()
sys.path[:0] = [str(PROJECT / "src"), str(PROJECT / "reports" / "claude_regime"), str(PROJECT / "scripts")]

import numpy as np  # noqa: E402

import competition_vote_replay as V  # noqa: E402
from competition_parity_regime import I, R, RS  # noqa: E402
from dual_strategy.competition.vote_rules import FIRST_ENTRY, SYMBOLS, last_entry, supported_side  # noqa: E402

import os  # noqa: E402
if os.environ.get("FR") is not None:            # cost sensitivity: slippage per side (fees unchanged)
    I.FR = float(os.environ["FR"])
if os.environ.get("VOTES"):                     # signal sensitivity: votes required to agree
    V.DEFAULT_VOTES = int(os.environ["VOTES"])
ONLY = os.environ.get("ONLY")                   # e.g. "AHD" to run a subset

START, END, WARM = "2021-01-04", "2026-11-20", "2020-11-02"
PERIODS = {"2021-2026 (5.9 yrs)": ("2021-01-04", "2026-11-20"), "2025-2026": ("2025-01-01", "2026-11-20"),
           "last 3 months": ("2026-08-20", "2026-11-20"), "16-20 Nov 2026": ("2026-11-16", "2026-11-20")}


@dataclass
class Lot:
    side: int
    shares: int
    entry: float
    slot: int
    peak: float


@dataclass
class Variant:
    name: str
    tp: float | None = None          # $ per lot
    sl: float | None = None          # $ per lot
    trail: float | None = None       # $ per lot, from the best price since entry
    flip: bool = True                # opposite 2-vote agreement closes the symbol
    entry_rule: str = "add"          # "add": each agreeing bar adds a lot / re-enters; "fresh": old rule
    lot_usd: float = 20_000
    max_lots: int = 2


def simulate(variant, prices, votes, days):
    trades, daily = [], {}
    for i in days:
        capital = V.VoteCash()          # fresh $80k book each session (the live book only shrinks; 6 years would starve it)
        n = int(R.MINUTES[i] // 5)
        positions: dict[str, list[Lot]] = {}
        prior_sides, tp_block = {}, {}
        day_trades = []

        def close_lot(sym, lot, px, j, why):
            pnl = I.replay_pnl(lot.side, lot.shares, lot.entry, px)
            capital.settle(pnl)
            day_trades.append({"sym": sym, "side": lot.side, "shares": lot.shares, "entry": lot.entry, "exit": px,
                               "in": lot.slot, "out": j, "why": why, "pnl": pnl})

        for j in range(FIRST_ENTRY, n):
            o = {s: float(prices[s].open[i, j]) for s in SYMBOLS}
            hi = {s: float(prices[s].high[i, j]) for s in SYMBOLS}
            lo = {s: float(prices[s].low[i, j]) for s in SYMBOLS}
            sides = {s: supported_side(votes[s][i][0][j], votes[s][i][1][j], V.DEFAULT_VOTES) for s in SYMBOLS}
            for s, side in list(tp_block.items()):              # old rule: no re-entry on the same agreement
                if sides[s] != side:
                    tp_block.pop(s)
            closed = set()
            for s, lots in list(positions.items()):              # bar-open exits: vote flip, end of day
                side = lots[0].side
                if j == n - 1 or (variant.flip and sides[s] == -side):
                    for lot in lots:
                        close_lot(s, lot, o[s], j, "eod" if j == n - 1 else "flip")
                    del positions[s]
                    closed.add(s)
            if j <= last_entry(n):
                def eligible(s):
                    if not sides[s] or s in closed or len(positions.get(s, ())) >= variant.max_lots:
                        return False
                    held = positions.get(s)
                    if held and held[0].side != sides[s]:
                        return False
                    if variant.entry_rule == "fresh":
                        return s not in tp_block and (not held or sides[s] != prior_sides.get(s))
                    return True
                order = sorted((s for s in SYMBOLS if eligible(s)),
                               key=lambda s: -int(votes[s][i][0 if sides[s] < 0 else 1][j]))
                sizing = {s: p * (1 + I.FR) for s, p in o.items()}
                gross = {s: sum(l.shares for l in lots) * sizing[s] for s, lots in positions.items()}
                remaining = min(V.PORTFOLIO_CAP, capital.sizing_capital) - math.fsum(gross.values())
                for s in order:
                    room = max(0.0, min(variant.lot_usd, remaining, V.SYMBOL_CAP - gross.get(s, 0.0)))
                    shares = math.floor(room / sizing[s])
                    if shares * sizing[s] > room:
                        shares -= 1
                    if shares > 0:
                        positions.setdefault(s, []).append(Lot(sides[s], shares, o[s], j, o[s]))
                        remaining -= shares * sizing[s]
                        gross[s] = gross.get(s, 0.0) + shares * sizing[s]
            prior_sides = sides
            if j == n - 1:
                continue
            for s, lots in list(positions.items()):              # intrabar exits, per lot
                keep = []
                for lot in lots:
                    d = lot.side
                    adverse, favor = (lo[s], hi[s]) if d > 0 else (hi[s], lo[s])
                    stops = []
                    if variant.sl:
                        stops.append(lot.entry - d * variant.sl / lot.shares)
                    if variant.trail:
                        stops.append(lot.peak - d * variant.trail / lot.shares)
                    stop = (max(stops) if d > 0 else min(stops)) if stops else None
                    target = lot.entry + d * variant.tp / lot.shares if variant.tp else None
                    if stop is not None and (adverse - stop) * d <= 0:
                        px = o[s] if (o[s] - stop) * d < 0 and lot.slot < j else stop
                        close_lot(s, lot, px, j, "stop" if variant.sl and stop == lot.entry - d * variant.sl / lot.shares else "trail")
                    elif target is not None and (favor - target) * d >= 0:
                        px = o[s] if (o[s] - target) * d > 0 and lot.slot < j else target
                        close_lot(s, lot, px, j, "target")
                        if variant.entry_rule == "fresh":
                            tp_block[s] = d
                    else:
                        lot.peak = max(lot.peak, favor) if d > 0 else min(lot.peak, favor)
                        keep.append(lot)
                if keep:
                    positions[s] = keep
                else:
                    positions.pop(s)
        if positions:
            raise RuntimeError(f"open positions after the close on {R.DAYS[i]}")
        for t in day_trades:
            t["day"] = R.DAYS[i].isoformat()
        trades.extend(day_trades)
        daily[R.DAYS[i].isoformat()] = math.fsum(t["pnl"] for t in day_trades)
    return trades, daily


def metrics(trades, daily, lo, hi):
    tr = [t for t in trades if lo <= t["day"] <= hi]
    dd = [v for d, v in daily.items() if lo <= d <= hi]
    pnl = [t["pnl"] for t in tr]
    wins, losses = [p for p in pnl if p > 0], [p for p in pnl if p <= 0]
    eq = np.cumsum(dd) if dd else np.array([0.0])
    mdd = float(np.max(np.maximum.accumulate(np.concatenate([[0.0], eq])) - np.concatenate([[0.0], eq])))
    sd = float(np.std(dd, ddof=1)) if len(dd) > 1 else 0.0
    why = {}
    for t in tr:
        why[t["why"]] = why.get(t["why"], 0) + 1
    return {"sessions": len(dd), "trades": len(pnl), "net_usd": round(math.fsum(pnl), 2),
            "win_rate_pct": round(100 * len(wins) / len(pnl), 1) if pnl else None,
            "avg_win": round(np.mean(wins), 2) if wins else None, "avg_loss": round(np.mean(losses), 2) if losses else None,
            "expectancy_usd": round(math.fsum(pnl) / len(pnl), 2) if pnl else None,
            "profit_factor": round(math.fsum(wins) / -math.fsum(losses), 2) if losses and math.fsum(losses) < 0 else None,
            "worst_trade": round(min(pnl), 2) if pnl else None, "worst_day": round(min(dd), 2) if dd else None,
            "max_drawdown_usd": round(mdd, 2),
            "sharpe_daily_ann": round(float(np.mean(dd)) / sd * math.sqrt(252), 2) if sd > 0 else None,
            "exits": why}


def main():
    out_path = Path(sys.argv[1])
    gaps = {"2021-05-05", "2023-06-05"}     # the only cached sessions with missing SPY/QQQ 5-minute bars
    days = [i for i in RS.in_window(START, END) if R.DAYS[i].isoformat() not in gaps]
    warm = [i for i in RS.in_window(WARM, END) if R.DAYS[i].isoformat() not in gaps]
    prices = {s: R.bars5(s) for s in SYMBOLS}
    shorts = {s: V.technical_windows(b, warm, days) for s, b in prices.items()}
    longs = {s: V.technical_windows(b, warm, days, mirror=True) for s, b in prices.items()}
    noise = V.noise_votes(days)
    votes = {s: {i: V.counts(i, prices[s], shorts[s][i], longs[s][i], noise[i] if s == "US.SPY" else None)
                 for i in days} for s in SYMBOLS}
    variants = [
        Variant("A  current: $100 target + vote flip + 04:55 close", tp=100),
        Variant("B  + stop = target ($100/$100)", tp=100, sl=100),
        Variant("C  + tight stop ($100 target / $50 stop)", tp=100, sl=50),
        Variant("D  wider ($200 target / $100 stop)", tp=200, sl=100),
        Variant("E  trailing $100, no target", trail=100),
        Variant("F  trailing $150, no target", trail=150),
        Variant("G  $100 target + trailing $100", tp=100, trail=100),
        Variant("H  no target, no stop (flip + close only)"),
        Variant("I  $100 target, NO vote-flip exit", tp=100, flip=False),
        Variant("J  $100/$100 target+stop, NO vote-flip exit", tp=100, sl=100, flip=False),
        Variant("K  until tonight: $10k lots x4, $50 target, fresh-agreement entries", tp=50, entry_rule="fresh",
                lot_usd=10_000, max_lots=4),
        Variant("L  $50 target at $20k (old target, new size)", tp=50),
    ]
    report = {"note": "HYPOTHETICAL replay on cached research bars; research cost model; conservative intrabar order",
              "data": {"from": START, "to": END, "sessions": len(days)}, "variants": {}}
    for v in variants:
        if ONLY and v.name[0] not in ONLY:
            continue
        trades, daily = simulate(v, prices, votes, days)
        report["variants"][v.name] = {"params": {k: getattr(v, k) for k in ("tp", "sl", "trail", "flip", "entry_rule", "lot_usd", "max_lots")},
                                      **{p: metrics(trades, daily, *rng) for p, rng in PERIODS.items()}}
        m = report["variants"][v.name]["2021-2026 (5.9 yrs)"]
        r = report["variants"][v.name]["2025-2026"]; q = report["variants"][v.name]["last 3 months"]
        print(f"{v.name[:44]:44s} 5.9y exp ${m['expectancy_usd']:6.2f} | 2025-26 exp ${r['expectancy_usd']:6.2f} net ${r['net_usd']:8,.0f} DD ${r['max_drawdown_usd']:7,.0f} Sh {r['sharpe_daily_ann']} | 3mo exp ${q['expectancy_usd']:6.2f} net ${q['net_usd']:7,.0f}", flush=True)
    out_path.write_text(json.dumps(report, indent=1, default=float))


if __name__ == "__main__":
    main()
