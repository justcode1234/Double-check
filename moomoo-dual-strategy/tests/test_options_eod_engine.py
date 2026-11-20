"""EOD option-backtest engine mechanics on a tiny SYNTHETIC chain (unit tests only; not historical results)."""

from datetime import date

import pandas as pd
import pytest

from dual_strategy.research.options import eod_backtest as eb

D1, D2, D3, D4 = date(2025, 3, 3), date(2025, 3, 4), date(2025, 3, 5), date(2025, 3, 6)
EXP = date(2025, 3, 6)


def _chain(rows):
    df = pd.DataFrame(rows, columns=["date", "expiration", "strike", "right", "bid", "ask", "bid_size", "ask_size",
                                     "open_interest", "implied_volatility"])
    return eb.Chain(df)


def _spot(v=500.0):
    return pd.Series({d: v for d in (D1, D2, D3, D4)})


def test_d_buys_next_day_at_ask_and_sells_at_exact_one_cent_target():
    rows = [(D1, EXP, 500.0, "C", 2.00, 2.05, 100, 100, 1000, .2),
            (D2, EXP, 500.0, "C", 2.10, 2.12, 100, 100, 1000, .2),      # bought here at the ASK 2.12
            (D3, EXP, 500.0, "C", 2.13, 2.15, 100, 100, 1000, .2),      # bid 2.13 >= 2.12 + 0.01 -> limit fills at 2.13
            (D4, EXP, 500.0, "C", 2.50, 2.55, 100, 100, 1000, .2)]
    b, eq = eb.run_d(_chain(rows), _spot(), [D1, D2, D3, D4], "atm", "C", 1)
    t = b.trades[0]
    assert t["entry_date"] == D2.isoformat() and t["entry_ask"] == 2.12
    assert t["exit_reason"] == "take_profit_1c" and t["exit_price"] == pytest.approx(2.13)
    assert t["net_pnl"] == pytest.approx(1.00 - 2.305 - 2.30779, abs=0.01)     # +$1 gross, fees > gain
    assert t["pnl_if_target_always_hit"] < 0


def test_d_unhit_otm_expires_worthless_without_exit_fee():
    rows = [(D1, EXP, 510.0, "C", 0.05, 0.06, 100, 100, 1000, .2), (D1, EXP, 500.0, "C", 1.0, 1.1, 100, 100, 1000, .2),
            (D2, EXP, 500.0, "C", 1.00, 1.05, 100, 100, 1000, .2), (D3, EXP, 500.0, "C", 0.50, 0.55, 100, 100, 1000, .2),
            (D4, EXP, 500.0, "C", 0.00, 0.01, 100, 100, 1000, .2)]
    b, eq = eb.run_d(_chain(rows), _spot(499.0), [D1, D2, D3, D4], "atm", "C", 1)
    t = b.trades[0]
    assert t["exit_reason"] == "expired_worthless" and t["exit_price"] == 0.0
    assert t["net_pnl"] == pytest.approx(-(1.05 * 100 + 2.305), abs=0.01)


def test_book_fills_entries_at_next_snapshot_and_caps_size():
    rows = [(D1, EXP, 500.0, "C", 2.0, 2.1, 100, 100, 1000, .2),
            (D2, EXP, 500.0, "C", 3.0, 3.2, 100, 2, 1000, .2),          # ask size 2 only
            (D3, EXP, 500.0, "C", 3.0, 3.2, 100, 100, 1000, .2), (D4, EXP, 500.0, "C", 1.0, 1.1, 100, 100, 1000, .2)]
    chain = _chain(rows)
    calls = []

    def decide(d, book):
        if d == D1:
            calls.append(d)
            return [{"kind": "E_test", "expiry": EXP, "legs": [(500.0, "C", 1)], "n": 5, "meta": {}}]
        return []
    b, eq = eb.run_book([D1, D2, D3, D4], chain, _spot(), decide, lambda d, book, p: False, max_open=1)
    t = b.trades[0]
    assert t["entry_date"] == D2.isoformat() and t["contracts"] == 2 and b.stats["partial"] == 1
    assert t["entry_cash"] == pytest.approx(-(3.2 * 100 * 2) - (1.99 + 0.6 + 0.03))


def test_missing_quote_skips_entry_and_never_invents_prices():
    rows = [(D1, EXP, 500.0, "C", 2.0, 2.1, 100, 100, 1000, .2), (D3, EXP, 500.0, "C", 2.0, 2.1, 100, 100, 1000, .2)]
    b, eq = eb.run_book([D1, D2, D3], _chain(rows), _spot(),
                        lambda d, book: [{"kind": "E_t", "expiry": EXP, "legs": [(500.0, "C", 1)], "n": 1, "meta": {}}] if d == D1 else [],
                        lambda d, book, p: False)
    assert b.trades == [] and b.stats["skipped_no_quote"] == 1
