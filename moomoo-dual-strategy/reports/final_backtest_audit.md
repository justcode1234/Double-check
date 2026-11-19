# Final backtest audit - strategies A, B, C (SIMULATED, offline)

Generated 2026-11-18T21:54:38+00:00. This is a historical simulation on Moomoo bars. No orders were sent and no account was touched. It does not replace `final_audit.md`.

Segments: development 2021-01-04..2022-12-30, validation 2023, untouched OOS 2024-01-02..2026-11-17. C rules were frozen from development data before any validation/OOS simulation of C.

Gate legend: PASS / FAIL / BLOCKED (missing data makes the faithful test impossible) / EXPLORATORY (runs, but on biased data, so it cannot support a GO).

## Headline OOS results

| variant | trades | net_return_pct | expectancy_usd | expectancy_r | sharpe | sharpe_ci95 | max_drawdown_pct | avg_gross_exposure | excess_vs_matched_pct | start | end |
|---|---|---|---|---|---|---|---|---|---|---|---|
| A0-R | 6850 | -88.319 | -6.450 | -0.461 | -6.048 | [-8.166, -4.48] | -88.972 | 0.179 | -97.552 | 2024-01-02 | 2026-11-17 |
| A1 | 3439 | -50.870 | -7.400 | -0.450 | -2.942 | [-4.654, -1.544] | -52.438 | 0.130 | -57.524 | 2024-01-02 | 2026-11-17 |
| A1-LO | 2572 | -37.008 | -7.190 | -0.427 | -2.276 | [-3.847, -0.949] | -37.472 | 0.102 | -42.227 | 2024-01-02 | 2026-11-17 |
| B0 | 645 | 33.031 | 25.610 | N/A | 0.736 | [-0.438, 1.682] | -17.469 | 0.720 | -7.660 | 2024-01-02 | 2026-11-17 |
| B0-100 | 645 | 9.356 | 7.250 | N/A | 0.541 | [-0.609, 1.461] | -7.840 | 0.259 | -4.208 | 2024-01-02 | 2026-11-17 |
| B1 | 627 | -20.637 | -16.460 | N/A | -3.311 | [-4.876, -2.036] | -21.528 | 0.100 | -25.749 | 2024-01-02 | 2026-11-17 |
| B1-LO | 333 | -11.649 | -17.490 | N/A | -2.711 | [-4.229, -1.466] | -12.349 | 0.056 | -14.455 | 2024-01-02 | 2026-11-17 |
| C0 | 437 | -5.520 | -6.320 | -0.048 | -0.613 | [-1.648, 0.329] | -7.167 | 0.063 | -8.705 | 2024-01-02 | 2026-11-17 |
| C1 | 437 | -20.908 | -23.920 | -0.452 | -5.708 | [-6.845, -4.667] | -20.932 | 0.025 | -22.157 | 2024-01-02 | 2026-11-17 |
| C1-LO | 249 | -13.079 | -26.260 | -0.482 | -4.236 | [-5.183, -3.37] | -13.079 | 0.013 | -13.717 | 2024-01-02 | 2026-11-17 |


## Gates


### A0-R - NO-GO

| gate | status | evidence |
|---|---|---|
| data | BLOCKED | no point-in-time universe; 40 present-day names (survivorship bias) |
| sample | PASS | OOS sessions 723, trades 6850 |
| oos_sharpe | FAIL | OOS Sharpe -6.048, CI95 [-8.166, -4.48], bootstrap P(Sharpe<=0) 1.0000 |
| validation_2023 | FAIL | 2023 net -43.996% |
| vs_exposure_matched_spy | FAIL | OOS excess vs exposure-matched SPY -97.552 pp |
| oos_drawdown_<=5% | FAIL | OOS max DD -88.972% |
| walk_forward | FAIL | 8 half-year test windows 2023+, 0% positive, stitched -89.64% |


### A1 - NO-GO

| gate | status | evidence |
|---|---|---|
| data | EXPLORATORY | no point-in-time universe; 40 present-day names (survivorship bias) |
| sample | PASS | OOS sessions 723, trades 3439 |
| oos_sharpe | FAIL | OOS Sharpe -2.942, CI95 [-4.654, -1.544], bootstrap P(Sharpe<=0) 1.0000 |
| validation_2023 | FAIL | 2023 net -16.865% |
| cost_10bps | FAIL | OOS net at 10 bps -71.855% |
| stress | FAIL | OOS net, stressed stops + 1-bar delay + 2% rejects + 2% misses + 10 bps: -84.010% |
| vs_exposure_matched_spy | FAIL | OOS excess vs exposure-matched SPY -57.524 pp |
| oos_drawdown_<=5% | FAIL | OOS max DD -52.438% |
| walk_forward | FAIL | 8 half-year test windows 2023+, 0% positive, stitched -69.54% |


### A1-LO - NO-GO

| gate | status | evidence |
|---|---|---|
| data | EXPLORATORY | no point-in-time universe; 40 present-day names (survivorship bias) |
| sample | PASS | OOS sessions 723, trades 2572 |
| oos_sharpe | FAIL | OOS Sharpe -2.276, CI95 [-3.847, -0.949], bootstrap P(Sharpe<=0) 1.0000 |
| validation_2023 | FAIL | 2023 net -14.161% |
| vs_exposure_matched_spy | FAIL | OOS excess vs exposure-matched SPY -42.227 pp |
| oos_drawdown_<=5% | FAIL | OOS max DD -37.472% |
| walk_forward | FAIL | 8 half-year test windows 2023+, 0% positive, stitched -52.08% |


### B0 - NO-GO

| gate | status | evidence |
|---|---|---|
| data | PASS | SPY 1m, OOS gap sessions = 0 |
| sample | PASS | OOS sessions 723, trades 645 |
| oos_sharpe | FAIL | OOS Sharpe 0.736, CI95 [-0.438, 1.682], bootstrap P(Sharpe<=0) 0.1025 |
| validation_2023 | PASS | 2023 net 41.243% |
| vs_exposure_matched_spy | FAIL | OOS excess vs exposure-matched SPY -7.660 pp |
| oos_drawdown_<=5% | FAIL | OOS max DD -17.469% |
| walk_forward | PASS | 8 half-year test windows 2023+, 62% positive, stitched 88.14% |


### B0-100 - NO-GO

| gate | status | evidence |
|---|---|---|
| data | PASS | SPY 1m, OOS gap sessions = 0 |
| sample | PASS | OOS sessions 723, trades 645 |
| oos_sharpe | FAIL | OOS Sharpe 0.541, CI95 [-0.609, 1.461], bootstrap P(Sharpe<=0) 0.1785 |
| validation_2023 | PASS | 2023 net 13.200% |
| vs_exposure_matched_spy | FAIL | OOS excess vs exposure-matched SPY -4.208 pp |
| oos_drawdown_<=5% | FAIL | OOS max DD -7.840% |
| walk_forward | PASS | 8 half-year test windows 2023+, 75% positive, stitched 23.89% |


### B1 - NO-GO

| gate | status | evidence |
|---|---|---|
| data | PASS | SPY 1m, OOS gap sessions = 0 |
| sample | PASS | OOS sessions 723, trades 627 |
| oos_sharpe | FAIL | OOS Sharpe -3.311, CI95 [-4.876, -2.036], bootstrap P(Sharpe<=0) 1.0000 |
| validation_2023 | FAIL | 2023 net -4.970% |
| cost_10bps | FAIL | OOS net at 10 bps -38.081% |
| stress | FAIL | OOS net, stressed stops + 1-bar delay + 2% rejects + 2% misses + 10 bps: -38.408% |
| vs_exposure_matched_spy | FAIL | OOS excess vs exposure-matched SPY -25.749 pp |
| oos_drawdown_<=5% | FAIL | OOS max DD -21.528% |
| walk_forward | FAIL | 8 half-year test windows 2023+, 0% positive, stitched -24.94% |


### B1-LO - NO-GO

| gate | status | evidence |
|---|---|---|
| data | PASS | SPY 1m, OOS gap sessions = 0 |
| sample | PASS | OOS sessions 723, trades 333 |
| oos_sharpe | FAIL | OOS Sharpe -2.711, CI95 [-4.229, -1.466], bootstrap P(Sharpe<=0) 1.0000 |
| validation_2023 | FAIL | 2023 net -2.722% |
| vs_exposure_matched_spy | FAIL | OOS excess vs exposure-matched SPY -14.455 pp |
| oos_drawdown_<=5% | FAIL | OOS max DD -12.349% |
| walk_forward | FAIL | 8 half-year test windows 2023+, 12% positive, stitched -14.11% |


### C0 - NO-GO

| gate | status | evidence |
|---|---|---|
| data | PASS | SPY 1m, OOS gap sessions = 0 |
| sample | PASS | OOS sessions 723, trades 437 |
| oos_sharpe | FAIL | OOS Sharpe -0.613, CI95 [-1.648, 0.329], bootstrap P(Sharpe<=0) 0.8985 |
| validation_2023 | FAIL | 2023 net -4.717% |
| vs_exposure_matched_spy | FAIL | OOS excess vs exposure-matched SPY -8.705 pp |
| oos_drawdown_<=5% | FAIL | OOS max DD -7.167% |
| walk_forward | FAIL | 8 half-year test windows 2023+, 12% positive, stitched -9.95% |
| development_2021_22 | FAIL | best eligible DEV grid Sharpe > 0: False; frozen {'band': 0.3, 'stop_atr': 0.25, 'hold': 60} |


### C1 - NO-GO

| gate | status | evidence |
|---|---|---|
| data | PASS | SPY 1m, OOS gap sessions = 0 |
| sample | PASS | OOS sessions 723, trades 437 |
| oos_sharpe | FAIL | OOS Sharpe -5.708, CI95 [-6.845, -4.667], bootstrap P(Sharpe<=0) 1.0000 |
| validation_2023 | FAIL | 2023 net -8.304% |
| cost_10bps | FAIL | OOS net at 10 bps -33.407% |
| stress | FAIL | OOS net, stressed stops + 1-bar delay + 2% rejects + 2% misses + 10 bps: -34.175% |
| vs_exposure_matched_spy | FAIL | OOS excess vs exposure-matched SPY -22.157 pp |
| oos_drawdown_<=5% | FAIL | OOS max DD -20.932% |
| walk_forward | FAIL | 8 half-year test windows 2023+, 0% positive, stitched -27.36% (C re-selected per fold) |
| development_2021_22 | FAIL | best eligible DEV grid Sharpe > 0: False; frozen {'band': 0.3, 'stop_atr': 0.25, 'hold': 60} |


### C1-LO - NO-GO

| gate | status | evidence |
|---|---|---|
| data | PASS | SPY 1m, OOS gap sessions = 0 |
| sample | PASS | OOS sessions 723, trades 249 |
| oos_sharpe | FAIL | OOS Sharpe -4.236, CI95 [-5.183, -3.37], bootstrap P(Sharpe<=0) 1.0000 |
| validation_2023 | FAIL | 2023 net -5.147% |
| vs_exposure_matched_spy | FAIL | OOS excess vs exposure-matched SPY -13.717 pp |
| oos_drawdown_<=5% | FAIL | OOS max DD -13.079% |
| walk_forward | FAIL | 8 half-year test windows 2023+, 0% positive, stitched -17.77% |
| development_2021_22 | FAIL | best eligible DEV grid Sharpe > 0: False; frozen {'band': 0.3, 'stop_atr': 0.25, 'hold': 60} |


## Diversification (OOS daily net returns)

|  | A1 | B1 | C1 | SPY |
|---|---|---|---|---|
| A1 | 1.000 | 0.121 | 0.039 | -0.055 |
| B1 | 0.121 | 1.000 | -0.113 | -0.006 |
| C1 | 0.039 | -0.113 | 1.000 | 0.062 |
| SPY | -0.055 | -0.006 | 0.062 | 1.000 |


## Verdict summary

| variant | verdict |
|---|---|
| A0-R | NO-GO |
| A1 | NO-GO |
| A1-LO | NO-GO |
| B0 | NO-GO |
| B0-100 | NO-GO |
| B1 | NO-GO |
| B1-LO | NO-GO |
| C0 | NO-GO |
| C1 | NO-GO |
| C1-LO | NO-GO |


A GO here would only mean 'eligible for supervised paper trading under the existing authorization'; it is never a promise of future returns.

## Disclosure

OOS results were previewed once in a pipeline smoke run (/tmp/bt_fullcheck) before this final run, while stock history was still downloading (A ran on a partial universe there). No strategy rules, parameters, gates or the C freeze were changed after viewing any OOS output; the C freeze was written before any C validation/OOS simulation. The holdout was therefore not strictly untouched by human eyes, though it was untouched by selection.


## Not done / blocked

- A0 faithful replication: BLOCKED - DATA (no point-in-time universe, no market-cap history, no 1-minute stock bars).
- QQQ variants: NOT RUN (optional; intraday not fetched).
- Stocks without history: none.


## Reproduce

```
.venv/bin/python -m dual_strategy.research.run
```

Git `0088dd7c5f3a10577cf9f3f8df0d7cacf52c14fb+uncommitted-research-modules`, data content sha256 `4e0bb91dcad4080c2fc93a0c58d79c696eaa16a9ade77c9417cb17f9a8764820` (415 files), config sha256 `fadad7bee083bf153f5ce59788fd98363711f919b5bf34540a39a0a7df3f4487`, C freeze sha256 `98854eb6258701dd808ab9cd9d66ce66ab581632b5a36f7b93043814763d2c1d`.
