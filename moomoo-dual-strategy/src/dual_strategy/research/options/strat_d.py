"""Strategy D0 - the user's news-sentiment options strategy, PRESERVED AS STATED. No file in the repository defined
D before 2026-11-19 (searched: sentiment / news / option / profit target / take-profit). The only sources are the
user's own words, which disagree on the profit target. D0 is therefore BLOCKED - ORIGINAL RULES UNAVAILABLE: the
signal, sentiment source/threshold, call/put/spread choice, strike, expiry and size were never defined, and they are
not invented here. This module records the stated rules and quantifies what the stated exit implies."""

from __future__ import annotations

from dual_strategy.research.options.pricing import MULTIPLIER, round_trip_fee_per_contract

D0_STATED = {
    "status": "BLOCKED - ORIGINAL RULES UNAVAILABLE",
    "sources": [
        {"where": "chat, 2026-11-19", "text": "add a fourth stategy where it reads news sentiment and only aim options "
                                               "and once it get 1 cent profit it sells and has no stop loss"},
        {"where": "claude_six_strategy_backtest_prompt.md, 2026-11-19", "text": "preserve my existing rules, including "
                                               "no trade-level stop-loss and the originally defined 1% profit target"},
    ],
    "defined": {"instrument": "U.S.-listed options only", "signal": "news sentiment (source, scoring, threshold undefined)",
                "exit": "sell at a profit target", "stop_loss": "none (per-trade)"},
    "conflict": "profit target is '+1 cent' in chat but '1%' in the prompt; whether 1% is of premium or of equity is unstated",
    "undefined": ["news source and timestamping", "sentiment model and threshold", "calls vs puts vs spreads",
                  "buy or sell premium", "underlyings", "strike/delta selection", "expiry/DTE", "position size",
                  "max concurrent positions", "what happens to an open loser at expiry"],
}


def target_economics(premium: float, contracts: int, target: str, half_spread: float = 0.01) -> dict:
    """Per-contract economics of one long-option round trip that exits exactly at the stated target.
    target: "cent" (+$0.01/share) or "pct" (+1% of premium). Entry pays the ask and exit hits the bid, so the
    option's mid must rise by target + 2 x half_spread. A loser with no stop can lose the whole premium."""
    gain_per_share = 0.01 if target == "cent" else 0.01 * premium
    gross = gain_per_share * MULTIPLIER
    fees = round_trip_fee_per_contract(contracts, premium)
    net_win = gross - fees
    max_loss = premium * MULTIPLIER + fees
    required = max_loss / (max_loss + net_win) if net_win > 0 else None
    return {"premium": premium, "contracts": contracts, "target": target, "gross_win_usd": round(gross, 4),
            "fees_round_trip_usd": round(fees, 4), "net_win_usd": round(net_win, 4),
            "mid_move_needed_per_share": round(gain_per_share + 2 * half_spread, 4),
            "max_loss_usd": round(max_loss, 2), "breakeven_win_rate": None if required is None else round(required, 5)}
