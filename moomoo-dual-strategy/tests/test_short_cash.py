"""The runner NAV with shorts open (found live 2026-11-25 with the IVV twin short).

Moomoo's sim `balance` withholds 160% of the short market value (proceeds + 60% initial short margin) while
`total_asset` counts it. Reading `balance` as cash undervalued the account by ~1.6 x the short, so every risk mark with
a short open disagreed with the broker total: entries were blocked and the loss lock / halt checks were skipped
(148 of 148 such marks on 2026-11-23/24).
"""

from datetime import datetime, timezone

import pytest

from dual_strategy.competition import planner
from dual_strategy.competition.broker import CompetitionBroker, account_cash
from dual_strategy.competition.store import load_config
from pathlib import Path

CFG = load_config(Path(__file__).resolve().parents[1])
# The exact funds Moomoo returned at 2026-11-26 00:0x SGT: SPY 13, QQQ 30 long, IVV 28 short.
LIVE = {"balance": "59781.558", "hold": "0", "long_mv": "27392.64", "max_power_long": "146955.756",
        "mv": "7736.64", "short_mv": "-19656", "total_asset": "98967.798"}


def test_live_funds_give_the_true_cash():
    assert account_cash(LIVE) == pytest.approx(91231.158, abs=1e-6)
    assert account_cash(LIVE) + 27392.64 - 19656 == pytest.approx(98967.798, abs=1e-6)


@pytest.mark.parametrize("funds", [
    {"balance": "71570.278", "total_asset": "98893.278", "short_mv": "0", "long_mv": "27323"},     # no short
    {"balance": "71570.278", "total_asset": "98893.278"},                                          # no short field
    {**LIVE, "total_asset": "99967.798"},                                  # does not reconcile: fail closed
    {**LIVE, "short_mv": "abc"},
    {**LIVE, "short_mv": "NaN"},
])
def test_anything_else_keeps_the_plain_balance(funds):
    assert account_cash(funds) == float(funds["balance"])


def _broker(funds, rows):
    b = CompetitionBroker.__new__(CompetitionBroker)
    b.cfg, b.acc_id = CFG, "acct"
    b.call = lambda tool, args, min_interval=1.0: (
        {"data": funds} if "cash" in tool else {"data": {"positions": rows}})
    return b


def test_runner_nav_matches_the_broker_with_a_short_open_so_risk_checks_run():
    rows = [{"symbol": "US.SPY", "qty": 13, "mv": 9097.4, "cur_price": 699.8},
            {"symbol": "US.QQQ", "qty": 30, "mv": 18295.24, "cur_price": 609.84},
            {"symbol": "US.IVV", "qty": -28, "mv": -19656, "cur_price": 702.0}]
    quotes = {"US.SPY": 699.8, "US.QQQ": 609.84, "US.IVV": 702.0}
    b = _broker(LIVE, rows)
    acct = b.account(lambda s: (quotes[s], datetime.now(timezone.utc)), {"US.SPY": 13, "US.QQQ": 30, "US.IVV": -28})
    assert acct.positions == {"US.SPY": 13, "US.QQQ": 30, "US.IVV": -28}
    assert acct.nav == pytest.approx(98967.798, abs=0.5)
    state = planner.new_state()
    state.update(initial_nav=100000.0, high_water=100174.348, day="2026-11-25", day_start_nav=99228.128)
    assert planner.mark_risk(state, acct, "2026-11-25", CFG) is None and state["nav_mismatch"] is None
    assert state["last_nav"] == pytest.approx(98967.798, abs=0.5)
    state["day_start_nav"] = 101600.0                                   # the loss lock is live again with a short open
    assert planner.mark_risk(state, acct, "2026-11-25", CFG) == "daily_loss_lock"
    # No leverage: new gross must fit in NAV - gross (the short's notional stays reserved).
    assert planner.cash_room(acct) == pytest.approx(98967.798 - acct.gross, abs=0.5)
