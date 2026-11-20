# Risk Monte Carlo: block bootstrap of actual backtest returns (all strategies)

This is a **risk analysis, not evidence of alpha.** It resamples each strategy's own simulated daily net returns and cannot turn a negative history positive.

- **Point estimates:** 10,000 paths of 8 sessions (the competition horizon), circular 4-day blocks.
- **90% intervals:** two-level bootstrap. The OOS return series itself is re-sampled 200 times (10-day blocks), with 2,000 paths each. The interval reflects uncertainty in the history, not just Monte Carlo noise.

Code: `ef_study.two_level_mc`. Outputs: `reports/options_ef/ef_monte_carlo.json`, `reports/options_ef/abc_monte_carlo_two_level.json`.

## 8-session outcome

| Strategy (sample) | Median 8-session return [90% CI] | P(profit) [90% CI] | P(loss > 3%) | P(5% drawdown) |
|---|---|---|---|---|
| **E** (OOS 2024–25-12) | −0.04% [−0.16, 0.00] | 15% [11, 23] | 0% | 0% |
| E-hv | −0.11% [−0.21, −0.02] | 23% [18, 32] | 0% | 0% |
| E same-structure baseline | −0.03% [−0.14, 0.00] | 19% [15, 29] | 0% | 0% |
| **F_long** | −0.01% [−0.11, 0.00] | 34% [28, 44] | 0% | 0% |
| F_long naive | 0.00% [−0.11, 0.00] | 33% [26, 44] | 0% | 0% |
| **F_credit_defined_risk** (HAR) | 0 (no OOS trades) | — | — | — |
| F_credit (HV forecast) | 0.00% [0.00, 0.00] | 8% [4, 14] | 0% | 0% |
| F_credit naive | 0.00% [0.00, 0.00] | 33% [26, 42] | 0% | 0% |
| A1 ORB (OOS 2024–26-11) | −0.97% [−1.13, −0.68] | 26% [20, 32] | 2.4% | 0% |
| A0-R published ORB | −2.02% [−2.26, −1.75] | 13% [8, 17] | 22.7% | 1.9% |
| B1 Noise-Area | −0.06% [−0.12, −0.00] | 42% [34, 49] | 0% | 0% |
| B1-LO | −0.02% [−0.06, 0.00] | 41% [34, 47] | 0% | 0% |
| B0 published (≤4× leverage) | +0.02% [−0.36, +0.38] | 51% [42, 58] | 6.3% | 1.3% |
| C1 VWAP reversion | −0.09% [−0.13, −0.07] | 28% [23, 35] | 0% | 0% |
| **D** (any form) | **BLOCKED**: no valid return series | — | — | — |

## Reading it

- **Options strategies sit mostly in cash.** Positions are small and intermittent, so the median 8-session path is about 0 and P(profit) is low (8–34%): most 8-day windows contain either no trade or a losing one. Their tail risk is small *because they rarely trade*, not because they are safe. At the double size, E lost −19% and F_long −9% over the OOS.
- **Only B0 has a median at or above 0**, with its CI spanning 0. It needs leverage the competition's 40%-per-ticker rule forbids. Everything else has a median ≤ 0, with CIs mostly at or below 0.
- **D has no Monte Carlo.** D has no valid return series. EOD closes cannot observe its intraday +1¢ exit, and bootstrapping the EOD proxy would present a mis-specified series as risk evidence. D's fee arithmetic, not a simulation, is the decisive test (`reports/options_fee_and_strategy_D_feasibility.md`).
- **Limits:** the OOS samples are short (D/E/F 2024-01-02 → 2025-12-12; A–C to 2026-11-17, a different window). Block bootstraps keep only short-range dependence and can't create crash regimes absent from the sample. Results scale with position size.
