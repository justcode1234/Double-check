# Final backtest audit - strategies A, B, C (SIMULATED, offline)

Generated 2026-11-19T05:11:27+00:00. This is a historical simulation on Moomoo bars. No orders were sent and no account was touched. It does not replace `final_audit.md`.

Segments: development 2021-01-04..2022-12-30, validation 2023, untouched OOS 2024-01-02..2026-11-17. C rules were frozen from development data before any validation/OOS simulation of C.

Gate legend: PASS / FAIL / BLOCKED (missing data makes the faithful test impossible) / EXPLORATORY (runs, but on biased data, so it cannot support a GO).

Cost baseline (v2): competition fees + 1 bp/side slippage and 2 bp software-stop latency for SPY (B, C); 3 bp/side and 5 bp latency for stocks (A). The previous run used 5 bp/side for everything; the full 0-15 bp grid and the break-even slippage are reported for each primary variant.

## Headline OOS results

| variant | trades | net_return_pct | expectancy_usd | expectancy_r | sharpe | sharpe_ci95 | max_drawdown_pct | avg_gross_exposure | excess_vs_matched_pct | start | end |
|---|---|---|---|---|---|---|---|---|---|---|---|
| A0-R | 6850 | -88.319 | -6.450 | -0.461 | -6.048 | [-8.166, -4.48] | -88.972 | 0.179 | -97.552 | 2024-01-02 | 2026-11-17 |
| A1 | 3441 | -38.251 | -5.560 | -0.364 | -1.970 | [-3.575, -0.666] | -42.061 | 0.130 | -44.922 | 2024-01-02 | 2026-11-17 |
| A1-LO | 2569 | -25.951 | -5.050 | -0.343 | -1.462 | [-2.943, -0.224] | -27.570 | 0.103 | -31.181 | 2024-01-02 | 2026-11-17 |
| B0 | 645 | 33.031 | 25.610 | N/A | 0.736 | [-0.438, 1.682] | -17.469 | 0.720 | -7.660 | 2024-01-02 | 2026-11-17 |
| B0-100 | 645 | 9.356 | 7.250 | N/A | 0.541 | [-0.609, 1.461] | -7.840 | 0.259 | -4.208 | 2024-01-02 | 2026-11-17 |
| B1 | 627 | -3.170 | -2.530 | N/A | -0.475 | [-1.732, 0.542] | -6.195 | 0.101 | -8.303 | 2024-01-02 | 2026-11-17 |
| B1-LO | 333 | -1.772 | -2.660 | N/A | -0.407 | [-1.675, 0.568] | -3.355 | 0.056 | -4.580 | 2024-01-02 | 2026-11-17 |
| B2 | 645 | -3.566 | -2.760 | N/A | -0.506 | [-1.852, 0.55] | -6.743 | 0.103 | -8.790 | 2024-01-02 | 2026-11-17 |
| C0 | 546 | -6.074 | -5.560 | -0.041 | -0.604 | [-1.624, 0.352] | -7.065 | 0.080 | -10.139 | 2024-01-02 | 2026-11-17 |
| C1 | 546 | -9.331 | -8.540 | -0.173 | -2.393 | [-3.497, -1.423] | -9.416 | 0.032 | -10.924 | 2024-01-02 | 2026-11-17 |
| C1-LO | 310 | -6.364 | -10.270 | -0.206 | -2.040 | [-3.071, -1.047] | -6.433 | 0.016 | -7.184 | 2024-01-02 | 2026-11-17 |


## Gates


### A0-R - NO-GO

| gate | status | evidence |
|---|---|---|
| data | BLOCKED | no point-in-time universe; 40 present-day names (survivorship bias) |
| sample | PASS | OOS sessions 723, trades 6850 |
| oos_sharpe | FAIL | OOS Sharpe -6.048, CI95 [-8.166, -4.48], bootstrap P(Sharpe<=0) 1.0000 |
| validation_2023 | FAIL | 2023 net -43.996% |
| vs_exposure_matched_spy | FAIL | OOS excess vs exposure-matched SPY -97.552 pp |
| 8_session_5pct_drawdown_risk | PASS | 3.8% of OOS 8-session spans hit a 5% drawdown (repo terminal stop); limit 5% |
| walk_forward | FAIL | 8 half-year test windows 2023+, 0% positive, stitched -89.64% |


### A1 - NO-GO

| gate | status | evidence |
|---|---|---|
| data | EXPLORATORY | no point-in-time universe; 40 present-day names (survivorship bias) |
| sample | PASS | OOS sessions 723, trades 3441 |
| oos_sharpe | FAIL | OOS Sharpe -1.970, CI95 [-3.575, -0.666], bootstrap P(Sharpe<=0) 0.9990 |
| validation_2023 | FAIL | 2023 net -11.222% |
| cost_robust | FAIL | break-even slippage 0.00 bp/side (needs >= 2x the 3 bp baseline) |
| stress | FAIL | OOS net, stressed stops + 1-bar delay + 2% rejects + 2% misses at baseline slippage: -67.853% |
| vs_exposure_matched_spy | FAIL | OOS excess vs exposure-matched SPY -44.922 pp |
| 8_session_5pct_drawdown_risk | PASS | 0.0% of OOS 8-session spans hit a 5% drawdown (repo terminal stop); limit 5% |
| walk_forward | FAIL | 8 half-year test windows 2023+, 0% positive, stitched -53.66% |


### A1-LO - NO-GO

| gate | status | evidence |
|---|---|---|
| data | EXPLORATORY | no point-in-time universe; 40 present-day names (survivorship bias) |
| sample | PASS | OOS sessions 723, trades 2569 |
| oos_sharpe | FAIL | OOS Sharpe -1.462, CI95 [-2.943, -0.224], bootstrap P(Sharpe<=0) 0.9920 |
| validation_2023 | FAIL | 2023 net -9.705% |
| vs_exposure_matched_spy | FAIL | OOS excess vs exposure-matched SPY -31.181 pp |
| 8_session_5pct_drawdown_risk | PASS | 0.0% of OOS 8-session spans hit a 5% drawdown (repo terminal stop); limit 5% |
| walk_forward | FAIL | 8 half-year test windows 2023+, 0% positive, stitched -38.69% |


### B0 - NO-GO

| gate | status | evidence |
|---|---|---|
| data | PASS | SPY 1m, OOS gap sessions = 0 |
| sample | PASS | OOS sessions 723, trades 645 |
| oos_sharpe | FAIL | OOS Sharpe 0.736, CI95 [-0.438, 1.682], bootstrap P(Sharpe<=0) 0.1025 |
| validation_2023 | PASS | 2023 net 41.243% |
| vs_exposure_matched_spy | FAIL | OOS excess vs exposure-matched SPY -7.660 pp |
| 8_session_5pct_drawdown_risk | PASS | 1.4% of OOS 8-session spans hit a 5% drawdown (repo terminal stop); limit 5% |
| walk_forward | PASS | 8 half-year test windows 2023+, 62% positive, stitched 88.14% |


### B0-100 - NO-GO

| gate | status | evidence |
|---|---|---|
| data | PASS | SPY 1m, OOS gap sessions = 0 |
| sample | PASS | OOS sessions 723, trades 645 |
| oos_sharpe | FAIL | OOS Sharpe 0.541, CI95 [-0.609, 1.461], bootstrap P(Sharpe<=0) 0.1785 |
| validation_2023 | PASS | 2023 net 13.200% |
| vs_exposure_matched_spy | FAIL | OOS excess vs exposure-matched SPY -4.208 pp |
| 8_session_5pct_drawdown_risk | PASS | 0.0% of OOS 8-session spans hit a 5% drawdown (repo terminal stop); limit 5% |
| walk_forward | PASS | 8 half-year test windows 2023+, 75% positive, stitched 23.89% |


### B2 - NO-GO

| gate | status | evidence |
|---|---|---|
| data | PASS | SPY 1m, OOS gap sessions = 0 |
| sample | PASS | OOS sessions 723, trades 645 |
| oos_sharpe | FAIL | OOS Sharpe -0.506, CI95 [-1.852, 0.55], bootstrap P(Sharpe<=0) 0.8255 |
| validation_2023 | PASS | 2023 net 2.605% |
| vs_exposure_matched_spy | FAIL | OOS excess vs exposure-matched SPY -8.790 pp |
| 8_session_5pct_drawdown_risk | PASS | 0.0% of OOS 8-session spans hit a 5% drawdown (repo terminal stop); limit 5% |
| walk_forward | FAIL | 8 half-year test windows 2023+, 50% positive, stitched -0.78% |


### B1 - NO-GO

| gate | status | evidence |
|---|---|---|
| data | PASS | SPY 1m, OOS gap sessions = 0 |
| sample | PASS | OOS sessions 723, trades 627 |
| oos_sharpe | FAIL | OOS Sharpe -0.475, CI95 [-1.732, 0.542], bootstrap P(Sharpe<=0) 0.8140 |
| validation_2023 | PASS | 2023 net 1.467% |
| cost_robust | FAIL | break-even slippage 0.37 bp/side (needs >= 2x the 1 bp baseline) |
| stress | FAIL | OOS net, stressed stops + 1-bar delay + 2% rejects + 2% misses at baseline slippage: -3.768% |
| vs_exposure_matched_spy | FAIL | OOS excess vs exposure-matched SPY -8.303 pp |
| 8_session_5pct_drawdown_risk | PASS | 0.0% of OOS 8-session spans hit a 5% drawdown (repo terminal stop); limit 5% |
| walk_forward | FAIL | 8 half-year test windows 2023+, 38% positive, stitched -1.53% |


### B1-LO - NO-GO

| gate | status | evidence |
|---|---|---|
| data | PASS | SPY 1m, OOS gap sessions = 0 |
| sample | PASS | OOS sessions 723, trades 333 |
| oos_sharpe | FAIL | OOS Sharpe -0.407, CI95 [-1.675, 0.568], bootstrap P(Sharpe<=0) 0.7945 |
| validation_2023 | PASS | 2023 net 1.063% |
| vs_exposure_matched_spy | FAIL | OOS excess vs exposure-matched SPY -4.580 pp |
| 8_session_5pct_drawdown_risk | PASS | 0.0% of OOS 8-session spans hit a 5% drawdown (repo terminal stop); limit 5% |
| walk_forward | FAIL | 8 half-year test windows 2023+, 38% positive, stitched -0.69% |


### C0 - NO-GO

| gate | status | evidence |
|---|---|---|
| data | PASS | SPY 1m, OOS gap sessions = 0 |
| sample | PASS | OOS sessions 723, trades 546 |
| oos_sharpe | FAIL | OOS Sharpe -0.604, CI95 [-1.624, 0.352], bootstrap P(Sharpe<=0) 0.8965 |
| validation_2023 | FAIL | 2023 net -5.358% |
| vs_exposure_matched_spy | FAIL | OOS excess vs exposure-matched SPY -10.139 pp |
| 8_session_5pct_drawdown_risk | PASS | 0.0% of OOS 8-session spans hit a 5% drawdown (repo terminal stop); limit 5% |
| walk_forward | FAIL | 8 half-year test windows 2023+, 12% positive, stitched -11.11% |
| development_2021_22 | FAIL | best eligible DEV grid Sharpe > 0: False; frozen {'band': 0.3, 'stop_atr': 0.25, 'hold': 60} |


### C1 - NO-GO

| gate | status | evidence |
|---|---|---|
| data | PASS | SPY 1m, OOS gap sessions = 0 |
| sample | PASS | OOS sessions 723, trades 546 |
| oos_sharpe | FAIL | OOS Sharpe -2.393, CI95 [-3.497, -1.423], bootstrap P(Sharpe<=0) 1.0000 |
| validation_2023 | FAIL | 2023 net -4.408% |
| cost_robust | FAIL | break-even slippage 0.00 bp/side (needs >= 2x the 1 bp baseline) |
| stress | FAIL | OOS net, stressed stops + 1-bar delay + 2% rejects + 2% misses at baseline slippage: -11.083% |
| vs_exposure_matched_spy | FAIL | OOS excess vs exposure-matched SPY -10.924 pp |
| 8_session_5pct_drawdown_risk | PASS | 0.0% of OOS 8-session spans hit a 5% drawdown (repo terminal stop); limit 5% |
| walk_forward | FAIL | 8 half-year test windows 2023+, 0% positive, stitched -13.29% (C re-selected per fold) |
| development_2021_22 | FAIL | best eligible DEV grid Sharpe > 0: False; frozen {'band': 0.3, 'stop_atr': 0.25, 'hold': 60} |


### C1-LO - NO-GO

| gate | status | evidence |
|---|---|---|
| data | PASS | SPY 1m, OOS gap sessions = 0 |
| sample | PASS | OOS sessions 723, trades 310 |
| oos_sharpe | FAIL | OOS Sharpe -2.040, CI95 [-3.071, -1.047], bootstrap P(Sharpe<=0) 1.0000 |
| validation_2023 | FAIL | 2023 net -2.499% |
| vs_exposure_matched_spy | FAIL | OOS excess vs exposure-matched SPY -7.184 pp |
| 8_session_5pct_drawdown_risk | PASS | 0.0% of OOS 8-session spans hit a 5% drawdown (repo terminal stop); limit 5% |
| walk_forward | FAIL | 8 half-year test windows 2023+, 0% positive, stitched -8.87% |
| development_2021_22 | FAIL | best eligible DEV grid Sharpe > 0: False; frozen {'band': 0.3, 'stop_atr': 0.25, 'hold': 60} |


## Diversification (OOS daily net returns)

|  | A1 | B1 | C1 | SPY |
|---|---|---|---|---|
| A1 | 1.000 | 0.138 | 0.005 | -0.061 |
| B1 | 0.138 | 1.000 | -0.198 | -0.008 |
| C1 | 0.005 | -0.198 | 1.000 | 0.056 |
| SPY | -0.061 | -0.008 | 0.056 | 1.000 |


## Verdict summary

| variant | verdict |
|---|---|
| A0-R | NO-GO |
| A1 | NO-GO |
| A1-LO | NO-GO |
| B0 | NO-GO |
| B0-100 | NO-GO |
| B2 | NO-GO |
| B1 | NO-GO |
| B1-LO | NO-GO |
| C0 | NO-GO |
| C1 | NO-GO |
| C1-LO | NO-GO |


A GO here would only mean 'eligible for supervised paper trading under the existing authorization'; it is never a promise of future returns.


## Not done / blocked

- A0 faithful replication: BLOCKED - DATA (no point-in-time universe, no market-cap history, no 1-minute stock bars).
- QQQ variants: NOT RUN (optional; intraday not fetched).
- Stocks without history: none.


## Reproduce

```
.venv/bin/python -m dual_strategy.research.run
```

Git `561454885e8aa8374682bb9460eff349c4273d16+uncommitted-research-modules`, data content sha256 `4e0bb91dcad4080c2fc93a0c58d79c696eaa16a9ade77c9417cb17f9a8764820` (415 files), config sha256 `c22b12d5d829633f2af58e469424956f3cb2349d0ab123e4e546a58a34aea881`, C freeze sha256 `467569c6e46dc57215de7729f766db3bfb37bf6f48c8c434c17de7b3db80e038`.
