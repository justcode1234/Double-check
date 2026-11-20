# Audit discrepancies and reproducible cases

## Competition scale and modeled risk

**Handbook mismatch.** The organizer's terms set $100,000 initial virtual assets. The research portfolio initializes at $50,000 and B1 sizes 40% from day-start equity. The handbook's cap description refers to initial assets and its later example uses total account value. The research result is valid for its own simulated size. Switching to $100,000 and a fixed $40,000 SPY cap gives −1.864% for B1. Separately, raising A1's starting equity alone gives −30.937% and retains the other historical assumptions. Neither sensitivity simulates broker margin, market-cap eligibility or trade-by-trade matching. [Handbook](https://moomoo-qoder-challenge-rgkelnyuzbm.qoder.website/) · [Variant metrics](six_strategy_verdict.csv) · [Stock simulation start](../../src/dual_strategy/research/portfolio.py) · [B sizing](../../src/dual_strategy/research/strat_b.py).

**A day selection sees the rest of the session.** The candidate test uses whole-day bar completeness at 09:35. In a minimal example, two otherwise identical sessions can admit different stocks if only one is missing a later bar. Releasing this eligibility filter and requiring the opening bar gives A1 −38.413% versus −38.251% on the original current universe. The independent sample validated the math of the original entries and stops. The academic and fully point-in-time performance remain blocked. [A candidate filter](../../src/dual_strategy/research/strat_a.py) · [Sensitivity source](stock_track_one_details.py) · [Sample checks](stock_A_sample.csv).

**Liquidity modeling.** The stock engine uses total volume of the execution bar to cap orders tagged at that bar's open, and the final exit candidate is force-filled even when market matching is unobserved. The original A1, B1 and C1 OOS replays registered zero partial fills under baseline assumptions. In a one-minute fixture, volume reported only near the minute's end can supply capacity to an order stamped at the open. This construction cannot establish opening capacity or queue position. [Fill code](../../src/dual_strategy/research/book.py) · [Handbook order matching](https://moomoo-qoder-challenge-rgkelnyuzbm.qoder.website/).

## Options

**Next-day trades exceed planned entry budgets.** The original code sizes using the signal day's quotes. The fill engine takes updated quotes the next session without recomputing risk limits. On 2024-02-15 E planned a one-contract 502/497 SPY put vertical with budget $246.94. The next-day fill took $252.61 including entry fees. The pattern occurs on 11 of 55 E-boot and 5 of 39 F-long OOS positions. The strict independent replay applies the budget to the executed quote, drops crossed or zero-size fills and marks expiry intrinsically. It returns E −7.404%, F-long −1.257%. The strict sensitivity still omits actual matching and early assignments. [Trade ledger with budget and actual max loss](option_independent_trades.csv) · [Original options fill code](../../src/dual_strategy/research/options/eod_backtest.py).

**F-credit entry-fee fix and remaining loss-bound errors.** Defined risk at expiry for a condor with disjoint wings equals the maximum wing payoff less net entry cash. Entry fees are present in the baseline max-loss value. Under stress the original max-loss callback uses unadjusted quotes. Four adverse $0.02 leg ticks add $8 per contract before fees. A tested fixture enters a $5-wing condor and requires that its risk increase by $8 and the strict cap reject it. The original close calculation can exceed expiry width from executable-side spread pricing and fees. Two OOS naive condors had net losses beyond their recorded maximum.

| Entry | Exit | Recorded max loss | Realized model loss | Excess |
|---|---|---:|---:|---:|
| 2024-12-12 | 2024-12-19 | $444.23 | $507.45 | $63.23 |
| 2025-02-20 | 2025-02-27 | $422.23 | $554.45 | $132.23 |

The 2025-02-20 condor uses $5 put and call wings. Its four-leg close cash outflow is $632.23 against $77.77 net entry credit. The frozen HAR credit run has zero trades, so these failures are in the eligible-day benchmark. [Independent option ledger](option_independent_trades.csv) · [Risk regression tests](test_audit.py).

**Zero size and synthetic fallback.** The original entry uses a truthiness test for displayed size, so zero bypasses the partial-quantity cap. A positive ask with displayed ask size zero can consequently be filled. Close code allows zero or insufficient displayed size and can substitute intrinsic for a missing quote, even before expiry. In the later baseline OOS ledger, 16 of 35 naive condor exits had insufficient display size on at least one leg. Expiry uses hypothetical intrinsic for settlement in the strict replay. The handbook also describes physical exercise and possible forced liquidation when funds are insufficient. [Original options fill code](../../src/dual_strategy/research/options/eod_backtest.py) · [Strict quote test](test_audit.py).

**End-of-window equity and cash can differ.** The recorded final equity is marked before forced liquidation at the end of the segment. E-boot validation equity exceeds realized final cash by $12.73 on 2023-12-29. The primary E/F OOS accounts finish flat. [Independent daily equity and final cash](option_independent_daily.csv).

**Input and timing claims.** The raw options file had 1,585 crossed bid/ask rows and the accepted near-term cache retained 521. These occurred in 2021–22, so they do not explain the 2024–25 mark-to-market results directly. D's signal uses nine batches after early closes, and three endpoint returns cross split boundaries. E's calibration includes seven labels settling after its holdout cutoff. The corresponding restricted samples retain the original conclusions. [Data scan](option_data_scan.csv) · [D daily timing](sentiment_signal_daily.csv) · [E candidate labels](e_independent_calibration_candidates.csv).

## Confirmed fixes and reporting errors

- **C cutoff.** The frozen rule now allows signals through 14:30 on a full session. Moving the cutoff back one hour drops 109 OOS trades and moves −9.331% to −7.807%. The independent model makes closed-bar signals and fills at the following open. [Independent stock daily runs](stock_independent_daily.csv).
- **Put sign.** The current pricing routine computes P(underlying above strike) as 1 minus the put slope. For puts with $1.20 and $1.70 mids at adjacent $1 strikes, the above-strike probability is 0.50. Taking negative slope then clipping would turn that into 0.00 and could give 1.00 to the complementary event. The exact prior code snapshot is unavailable. [Probability regression](test_audit.py).
- **F headline arithmetic.** $174.00 gross minus $359.80 fees is −$185.80 net. The options release table confirms those figures. [Claim register](claim_by_claim_check.md).
- **B signal attribution.** All 627 B1 entries match B0. B0 has an additional 18 entries and 209 common entries differ on exit. [B0 versus B1](ablation_B0_vs_B1.md).

Run the independent tests and checks from the project root with the command below. It uses the completed frozen reproduction in this audit directory.

    & .\reports\codex_independent_audit\reproduce.ps1 -IndependentOnly
