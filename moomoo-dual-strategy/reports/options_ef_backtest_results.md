# E and F backtest results (historical EOD SPY quotes, SIMULATED)

All rows: $50,000 start, next-session bid/ask fills, handbook fees. Full table: `reports/options_ef_backtest_results.csv` (80 rows). Trades: `reports/options_ef/ef_trades.csv`. No Sharpe is shown below 30 trades.

## Side by side (net return; trades in brackets)

| Variant | Dev 2021–22 | Val 2023 | **OOS 2024-01-02 → 2025-12-12** | OOS Sharpe | OOS max DD | OOS win % | OOS R per trade* |
|---|---|---|---|---|---|---|---|
| **E** (original, zero-drift bootstrap) | +0.3% (37) | −8.9% (63) | **−10.2% (55)** | −2.27 | −10.5% | 16% | −0.54 |
| E-drift (+ trailing mean drift) | +0.6% (43) | −0.2% (1) | −1.8% (6) | n/a | −1.8% | 0% | −0.89 |
| E-hv (lognormal HV20) | −7.3% (129) | −6.3% (77) | −10.0% (77) | −1.55 | −10.3% | 23% | −0.41 |
| *Baseline: same structure every eligible day* | −9.8% (134) | −9.9% (101) | −8.8% (67) | −1.37 | −9.5% | 28% | −0.34 |
| *Baseline: random direction / timestamps (200 seeds)* | — | median −4.2% [5–95%: −7.8, +0.2] | **median −2.1% [−5.3, +1.6]** | — | — | — | — |
| **F_long** (straddle when IV ≤ HAR) | +4.4% (11) | +1.3% (32) | **−0.4% (39)** | −0.03 | −5.5% | 51% | −0.01 |
| F_long with constant-HV forecast | +2.7% (30) | +0.1% (17) | −1.7% (19) | n/a | −6.1% | 47% | −0.06 |
| *Baseline: straddle every eligible day* | +6.1% (19) | +1.7% (38) | −1.7% (37) | −0.19 | −7.0% | 49% | −0.03 |
| **F_credit_defined_risk** (iron condor when IV/HAR ≥ 1.2; dev FAIL) | −0.6% (1) | −0.0% (6) | **0 trades** | n/a | 0 | — | — |
| F_credit with HV forecast (R = 1.2, dev FAIL) | −0.2% (6) | −1.8% (21) | −2.3% (9) | n/a | −2.5% | 33% | −0.31 |
| *Baseline: iron condor every eligible day* | −1.4% (18) | −2.5% (43) | **−4.1% (35)** | −1.38 | −4.6% | 63% | −0.14 |
| *Cash* | 0 | 0 | 0 | — | 0 | — | — |
| *SPY buy & hold (OOS)* | | | +46.6% | | | | |

\*Net P&L ÷ defined max loss (incl. fees), averaged over trades: P&L on matched capital at risk.

The SPY buy-and-hold row is for scale only. Scaled to these strategies' average capital at risk (0.1–0.6% of equity), SPY would have returned +0.0–0.3%. Average capital at risk is tiny because positions are small and intermittent.

## Gross vs net (OOS)

| Variant | Gross before fees | Fees | Net | Reading |
|---|---|---|---|---|
| E | −$4,802 | $300 | −$5,102 | **Loses before costs**, so the forecast is wrong, not just expensive |
| E-hv | −$4,574 | $431 | −$5,005 | same |
| F_long | **+$174** | $360 | −$186 | Small gross edge, erased by fees |
| F_long naive | −$515 | $341 | −$856 | |
| F_credit naive | −$1,380 | $646 | −$2,026 | Loses before costs: small wins ($62 avg), larger losses (−$261 avg) |

## Why E fails (audit)

1. **The market's option-implied probabilities forecast better than E.** P(debit spread loses at expiry) across all candidates, OOS:

   | Forecast | Brier | Log loss |
   |---|---|---|
   | Option-implied | **0.229** | 0.651 |
   | E-drift | 0.233 | 0.659 |
   | E (original) | 0.235 | 0.663 |
   | Climatology | 0.238 | |
   | E-hv | 0.243 | 0.680 |

   The same ranking holds in development and validation. Buying spreads at market prices with a forecast worse than the price's own can't produce an edge.
2. **Directional bias.** 48 of E's 55 OOS trades (and all 77 E-hv trades) were put spreads, losing $5,032. The skew makes OTM puts expensive, so put debit spreads look cheap to a symmetric zero-drift model, which keeps buying them in a rising market.
3. **Miscalibrated ranges.**
   - E's 50% interval covered the realised price 34–46% of the time (should be 50%).
   - The PIT mean of 0.52–0.57 means outcomes landed above the forecast median (bearish bias).
   - In 2021–22 its tails were too thin (U-shaped PIT).
   - Adding drift centres the forecast (PIT mean 0.46–0.50) and improves the Brier score slightly. But its development-chosen threshold is so selective that it traded **6 times** OOS (all losers): inconclusive, not a rescue.
4. **E is worse than random.** Its OOS −10.2% sits below the 5th percentile of random same-structure trades (−5.3%), so the filter selects *against* the outcome.

Reliability plot: `reports/options_ef/ef_reliability_oos.svg`. Scores: `ef_calibration_scores.csv`, `ef_coverage_pit.csv`.

## Why F fails or is inconclusive

- **F_long** is about 0 OOS (−0.4% over 39 trades, Sharpe −0.03, CI spans 0). Its signal adds only +1.3 points over the naive straddle, which is within noise. The constant-HV forecast does worse (−1.7%).
- **F_credit_defined_risk:**
  - Development FAIL: too few trades at every threshold in 2021–22.
  - HAR forecasts ran **above** IV in OOS (median IV/HAR 0.94), so the frozen rule never fired.
  - **Descriptive only, not used for selection:** across thresholds 0.9–1.3 every OOS run lost money. HAR: −3.3% (21 trades) to −0.2% (4). HV: −3.1% (28) to −1.5% (6).
  - The naive condor lost −4.1%.
  - Defined risk worked (no loss exceeded the max-loss bound), but short-vol carry didn't cover the bid/ask, fees and tail losses on 7–14-day SPY options.
- **Early assignment:** 7 of 44 OOS credit positions held a short ITM leg with extrinsic value below the next dividend (or ≈ 0). That risk is real and unmodelled; it is flagged, not simulated.

## OOS sensitivity

| Stress | E | F_long |
|---|---|---|
| Baseline | −10.2% | −0.4% |
| +1 tick per leg | −10.5% | −0.1% |
| +2 ticks per leg | −10.7% | −0.4% |
| Spread × 2 | −11.4% | −0.3% |
| Max leg spread 5% | −10.4% | −1.2% |
| Max leg spread 20% | −9.9% | −0.4% |
| Double size | −19.3% | −9.4% |
| No take-profit | −10.2% | +0.0% |
| Half size | **0 trades** (contract granularity) | **0 trades** (contract granularity) |

F_long's small non-monotonic changes come from sizing: worse fills change which days can afford one straddle. Nothing turns meaningfully positive.
