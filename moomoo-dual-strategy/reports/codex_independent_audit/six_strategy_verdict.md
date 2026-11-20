# Six-strategy verdict

All returns are simulated. The verdict describes reproducibility of each result.
The CSV also includes development, validation and recent-period results.

| ID | Variant | Holdout dates | Data quality | Trades | Net | Fees / slip | Drawdown | Expectancy | Return 95% CI | Verdict |
|---|---|---|---|---:|---:|---:|---:|---:|---|---|
| A | A0 faithful published ORB | 2024-01-02 to 2026-11-17 | Point-in-time universe, delistings and intrabar sequencing absent | — | — | — / — | — | — | — | BLOCKED (DATA) |
| A | A0-R | 2024-01-02 to 2026-11-17 | 40 current names, 5-min OHLCV only | 6850 | -88.32 | 1747.68 / 0.00 | -88.97 | -6.45 | [-91.93%, -82.94%] | REPRODUCED |
| A | A0-R-optimistic | 2024-01-02 to 2026-11-17 | 40 current names, optimistic intrabar sequence | 6928 | +176.61 | 4805.93 / 0.00 | -9.44 | +12.75 | — | REPRODUCED |
| A | A1 | 2024-01-02 to 2026-11-17 | 40 current names, 5-min OHLCV only | 3441 | -38.25 | 6842.42 / 11590.65 | -42.06 | -5.56 | [-53.37%, -17.41%] | REPRODUCED |
| A | A1-LO | 2024-01-02 to 2026-11-17 | 40 current names, 5-min OHLCV only | 2569 | -25.95 | 5107.53 / 9368.02 | -27.57 | -5.05 | [-40.90%, -6.49%] | REPRODUCED |
| B | B0 | 2024-01-02 to 2026-11-17 | SPY 1-min OHLCV and turnover, no historical NBBO | 645 | +33.03 | 1258.39 / 359.54 | -17.47 | +25.61 | [-16.26%, +115.85%] | REPRODUCED |
| B | B0-100 | 2024-01-02 to 2026-11-17 | SPY 1-min OHLCV and turnover, no historical NBBO | 645 | +9.36 | 391.03 / 111.72 | -7.84 | +7.25 | [-8.91%, +33.44%] | REPRODUCED |
| B | B1 | 2024-01-02 to 2026-11-17 | SPY 1-min OHLCV and turnover, no historical NBBO | 627 | -3.17 | 1244.78 / 2448.16 | -6.20 | -2.53 | [-9.57%, +4.14%] | REPRODUCED |
| B | B1-LO | 2024-01-02 to 2026-11-17 | SPY 1-min OHLCV and turnover, no historical NBBO | 333 | -1.77 | 661.11 / 1311.14 | -3.35 | -2.66 | [-6.00%, +3.00%] | REPRODUCED |
| B | B2 | 2024-01-02 to 2026-11-17 | SPY 1-min OHLCV and turnover, no historical NBBO | 645 | -3.57 | 1280.51 / 2515.85 | -6.74 | -2.76 | [-10.41%, +4.46%] | REPRODUCED |
| C | C0 | 2024-01-02 to 2026-11-17 | SPY 1-min OHLCV and turnover, no historical NBBO | 546 | -6.07 | 301.48 / 86.14 | -7.06 | -5.56 | [-15.18%, +3.54%] | REPRODUCED |
| C | C1 | 2024-01-02 to 2026-11-17 | SPY 1-min OHLCV and turnover, no historical NBBO | 546 | -9.33 | 1083.86 / 2041.67 | -9.42 | -8.54 | [-13.20%, -5.48%] | REPRODUCED |
| C | C1-LO | 2024-01-02 to 2026-11-17 | SPY 1-min OHLCV and turnover, no historical NBBO | 310 | -6.36 | 615.41 / 1178.58 | -6.43 | -10.27 | [-9.69%, -3.08%] | REPRODUCED |
| D | D original +$0.01 and no stop | — | Intraday bid/ask, point-in-time news and complete rules required | — | — | — / — | — | — | — | BLOCKED (DATA) |
| D | D GDELT 1-day option proxy | 2024-01-02 to 2025-12-11 | EOD spread crossing, one lot, separate timing test | 475 | — | 2191.08 / — | — | -3.56 | — | REPRODUCED |
| E | E_boot | 2024-01-02 to 2025-12-12 | SPY EOD snapshots, reported time only, reused holdout | 55 | -10.20 | 299.85 / — | -10.51 | -92.76 | [-15.92%, -4.30%] | REPRODUCED |
| E | E_drift | 2024-01-02 to 2025-12-12 | SPY EOD snapshots, reported time only, reused holdout | 6 | -1.81 | 29.98 / — | -1.81 | -151.16 | [-3.49%, -0.47%] | REPRODUCED |
| E | E_hv | 2024-01-02 to 2025-12-12 | SPY EOD snapshots, reported time only, reused holdout | 77 | -10.01 | 431.32 / — | -10.32 | -65.00 | [-16.55%, -3.13%] | REPRODUCED |
| E | E_always_same_structure | 2024-01-02 to 2025-12-12 | SPY EOD snapshots, reported time only, reused holdout | 67 | -8.84 | 385.19 / — | -9.54 | -65.99 | [-15.36%, -1.97%] | REPRODUCED |
| F | F_long_har | 2024-01-02 to 2025-12-12 | SPY EOD snapshots, reported time only, reused holdout | 39 | -0.37 | 359.80 / — | -5.54 | -4.76 | [-10.23%, +11.05%] | REPRODUCED |
| F | F_long_hv | 2024-01-02 to 2025-12-12 | SPY EOD snapshots, reported time only, reused holdout | 19 | -1.66 | 175.29 / — | -6.09 | -43.70 | [-8.20%, +5.75%] | REPRODUCED |
| F | F_long_naive | 2024-01-02 to 2025-12-12 | SPY EOD snapshots, reported time only, reused holdout | 37 | -1.71 | 341.35 / — | -6.98 | -23.14 | [-11.25%, +9.58%] | REPRODUCED |
| F | F_credit_har | 2024-01-02 to 2025-12-12 | SPY EOD snapshots, reported time only, reused holdout | 0 | +0.00 | 0.00 / — | 0.00 | — | [+0.00%, +0.00%] | REPRODUCED |
| F | F_credit_hv | 2024-01-02 to 2025-12-12 | SPY EOD snapshots, reported time only, reused holdout | 9 | -2.27 | 166.06 / — | -2.48 | -126.01 | [-4.53%, -0.33%] | REPRODUCED |
| F | F_credit_naive | 2024-01-02 to 2025-12-12 | SPY EOD snapshots, reported time only, reused holdout | 35 | -4.05 | 645.79 / — | -4.58 | -57.88 | [-8.18%, -0.30%] | REPRODUCED |
| E | Earlier EOD E | 2024-01-02 to 2025-12-12 | Earlier SPY EOD recipe, daily quote proxy | 51 | -8.80 | — / — | -9.11 | -86.24 | [-14.26%, -3.29%] | REPRODUCED |
| E | Earlier EOD E-always (no MC filter) | 2024-01-02 to 2025-12-12 | Earlier SPY EOD recipe, daily quote proxy | 124 | -11.94 | — / — | -13.18 | -48.14 | [-20.88%, -1.38%] | REPRODUCED |
| F | Earlier EOD F-short (iron fly when IV rich) | 2024-01-02 to 2025-12-12 | Earlier SPY EOD recipe, daily quote proxy | 1 | -0.51 | — / — | -0.85 | -256.45 | [-2.20%, +0.67%] | REPRODUCED |
| F | Earlier EOD F-short naive (every day) | 2024-01-02 to 2025-12-12 | Earlier SPY EOD recipe, daily quote proxy | 60 | -10.36 | — / — | -12.55 | -87.06 | [-22.21%, +2.99%] | REPRODUCED |
| F | Earlier EOD F-long (straddle when IV cheap) | 2024-01-02 to 2025-12-12 | Earlier SPY EOD recipe, daily quote proxy | 39 | -0.37 | — / — | -5.54 | -4.77 | [-10.23%, +11.05%] | REPRODUCED |
| F | Earlier EOD F-long naive (every day) | 2024-01-02 to 2025-12-12 | Earlier SPY EOD recipe, daily quote proxy | 40 | -0.85 | — / — | -5.45 | -10.63 | [-10.76%, +10.71%] | REPRODUCED |
| D | Earlier EOD D ATM call +1c TP, no stop | 2024-01-02 to 2025-12-12 | Earlier SPY EOD recipe, daily quote proxy | 243 | -53.68 | — / — | -53.68 | -110.45 | [-60.06%, -46.83%] | REPRODUCED |
| D | Earlier EOD D ATM put +1c TP, no stop | 2024-01-02 to 2025-12-12 | Earlier SPY EOD recipe, daily quote proxy | 243 | -58.71 | — / — | -58.71 | -120.72 | [-67.76%, -44.54%] | REPRODUCED |
| D | Earlier EOD D cheap call (<=$0.10, 20 lots) +1c TP, no stop | 2024-01-02 to 2025-12-12 | Earlier SPY EOD recipe, daily quote proxy | 240 | -66.73 | — / — | -66.73 | -138.95 | [-73.79%, -58.98%] | REPRODUCED |

## Sensitivities and scope checks

| ID | Variant | Holdout dates | Data quality | Trades | Net | Fees / slip | Drawdown | Expectancy | Return 95% CI | Verdict |
|---|---|---|---|---:|---:|---:|---:|---:|---|---|
| A | A1-100k-sensitivity | 2024-01-02 to 2026-11-17 | 40 current names, 5-min OHLCV only | 3440 | -30.94 | 6870.46 / 24500.65 | -36.45 | -8.99 | [-48.34%, -6.87%] | REPRODUCED |
| A | A1-entry-time-completeness | 2024-01-02 to 2026-11-17 | 40 current names, 5-min OHLCV only | 3441 | -38.41 | 6842.37 / 11570.15 | -42.18 | -5.58 | [-53.52%, -17.76%] | REPRODUCED |
| B | B-delay1 | 2024-01-02 to 2026-11-17 | SPY 1-min OHLCV and turnover, no historical NBBO | 627 | -4.44 | 1244.74 / 2418.90 | -6.27 | -3.54 | [-10.56%, +2.49%] | REPRODUCED |
| B | B-flatten | 2024-01-02 to 2026-11-17 | SPY 1-min OHLCV and turnover, no historical NBBO | 627 | -3.17 | 1244.78 / 2448.16 | -6.20 | -2.53 | [-9.57%, +4.14%] | REPRODUCED |
| B | B-initial-100k | 2024-01-02 to 2026-11-17 | SPY 1-min OHLCV and turnover, no historical NBBO | 627 | -1.86 | 1248.17 / 4964.06 | -5.52 | -2.97 | [-8.36%, +5.58%] | REPRODUCED |
| B | B-paper-vwap-aside | 2024-01-02 to 2026-11-17 | SPY 1-min OHLCV and turnover, no historical NBBO | 642 | -3.58 | 1274.55 / 2501.43 | -6.49 | -2.79 | [-9.97%, +3.69%] | REPRODUCED |
| B | B-size40 | 2024-01-02 to 2026-11-17 | SPY 1-min OHLCV and turnover, no historical NBBO | 645 | +3.74 | 148.46 / 42.42 | -3.14 | +2.90 | [-3.53%, +12.31%] | REPRODUCED |
| B | B-size40-fees | 2024-01-02 to 2026-11-17 | SPY 1-min OHLCV and turnover, no historical NBBO | 645 | +1.61 | 1280.59 / 0.00 | -4.17 | +1.25 | [-5.53%, +10.05%] | REPRODUCED |
| B | B-strict | 2024-01-02 to 2026-11-17 | SPY 1-min OHLCV and turnover, no historical NBBO | 627 | -3.56 | 1244.78 / 2447.59 | -6.94 | -2.84 | [-10.28%, +4.34%] | REPRODUCED |
| B | B0 exact published data and fills | — | Different sample, VWAP and fill convention | — | — | — / — | — | — | — | NOT REPRODUCIBLE |
| B | B0-paper-close-HLC3 | 2024-01-02 to 2026-11-17 | SPY 1-min OHLCV and turnover, no historical NBBO | 659 | +32.35 | 1292.91 / 369.40 | -19.51 | +24.55 | [-17.06%, +116.09%] | REPRODUCED |
| B | B1-frictionless | 2024-01-02 to 2026-11-17 | SPY 1-min OHLCV and turnover, no historical NBBO | 627 | +4.29 | 0.00 / 0.00 | -2.48 | +3.42 | [-2.46%, +12.04%] | REPRODUCED |
| B | B1-slip0 | 2024-01-02 to 2026-11-17 | SPY 1-min OHLCV and turnover, no historical NBBO | 627 | +1.85 | 1244.85 / 0.00 | -3.61 | +1.47 | [-4.78%, +9.47%] | REPRODUCED |
| B | B1-slip5 | 2024-01-02 to 2026-11-17 | SPY 1-min OHLCV and turnover, no historical NBBO | 627 | -20.64 | 1244.49 / 11075.93 | -21.53 | -16.46 | [-26.33%, -14.24%] | REPRODUCED |
| C | C1-cutoff1330 | 2024-01-02 to 2026-11-17 | SPY 1-min OHLCV and turnover, no historical NBBO | 437 | -7.81 | 867.50 / 1646.30 | -7.89 | -8.93 | [-11.32%, -4.33%] | REPRODUCED |
| C | C1-frictionless | 2024-01-02 to 2026-11-17 | SPY 1-min OHLCV and turnover, no historical NBBO | 546 | -3.12 | 0.00 / 0.00 | -3.30 | -2.86 | [-7.06%, +0.78%] | REPRODUCED |
| C | C1-target-priority | 2024-01-02 to 2026-11-17 | SPY 1-min OHLCV and turnover, no historical NBBO | 546 | -9.33 | 1083.86 / 2041.67 | -9.42 | -8.54 | [-13.20%, -5.48%] | REPRODUCED |
| D | D −53.7% applied to original | — | Earlier ATM call EOD proxy | — | — | — / — | — | — | — | DISPUTED |
| E | E original intraday specification | — | Intraday option snapshots and untouched post-2025 holdout absent | — | — | — / — | — | — | — | BLOCKED (DATA) |
| E | E_boot_free | 2024-01-02 to 2025-12-12 | SPY EOD snapshots, reported time only, reused holdout | 55 | -9.60 | 0.00 / — | -9.94 | -87.31 | [-15.29%, -3.72%] | REPRODUCED |
| E | E_boot_strict | 2024-01-02 to 2025-12-12 | SPY EOD snapshots, reported time only, reused holdout | 46 | -7.40 | 212.19 / — | -7.70 | -80.48 | [-12.52%, -2.07%] | REPRODUCED |
| E | E_boot_ticks2 | 2024-01-02 to 2025-12-12 | SPY EOD snapshots, reported time only, reused holdout | 55 | -10.72 | 299.85 / — | -11.00 | -97.49 | [-16.43%, -4.82%] | REPRODUCED |
| F | F original intraday specification | — | Intraday option snapshots and untouched post-2025 holdout absent | — | — | — / — | — | — | — | BLOCKED (DATA) |
| F | F-long screenshot fee split | — | Gross and fee amounts transposed | — | — | — / — | — | — | — | DISPUTED |
| F | F_credit_har_0.9 | 2024-01-02 to 2025-12-12 | SPY EOD snapshots, reported time only, reused holdout | 21 | -3.32 | 387.47 / — | -3.77 | -79.07 | [-6.75%, -0.17%] | REPRODUCED |
| F | F_credit_har_1 | 2024-01-02 to 2025-12-12 | SPY EOD snapshots, reported time only, reused holdout | 10 | -1.71 | 184.51 / — | -2.03 | -85.65 | [-3.98%, +0.28%] | REPRODUCED |
| F | F_credit_har_1.1 | 2024-01-02 to 2025-12-12 | SPY EOD snapshots, reported time only, reused holdout | 4 | -0.20 | 73.80 / — | -0.52 | -24.70 | [-1.14%, +0.72%] | REPRODUCED |
| F | F_credit_har_1.2 | 2024-01-02 to 2025-12-12 | SPY EOD snapshots, reported time only, reused holdout | 0 | +0.00 | 0.00 / — | 0.00 | — | [+0.00%, +0.00%] | REPRODUCED |
| F | F_credit_har_1.3 | 2024-01-02 to 2025-12-12 | SPY EOD snapshots, reported time only, reused holdout | 0 | +0.00 | 0.00 / — | 0.00 | — | [+0.00%, +0.00%] | REPRODUCED |
| F | F_credit_har_euro_iv | 2024-01-02 to 2025-12-12 | SPY EOD snapshots, reported time only, reused holdout | 0 | +0.00 | 0.00 / — | 0.00 | — | [+0.00%, +0.00%] | REPRODUCED |
| F | F_credit_hv_0.9 | 2024-01-02 to 2025-12-12 | SPY EOD snapshots, reported time only, reused holdout | 28 | -3.15 | 516.63 / — | -3.67 | -56.17 | [-6.99%, +0.36%] | REPRODUCED |
| F | F_credit_hv_1 | 2024-01-02 to 2025-12-12 | SPY EOD snapshots, reported time only, reused holdout | 26 | -2.83 | 479.73 / — | -3.85 | -54.34 | [-6.57%, +0.52%] | REPRODUCED |
| F | F_credit_hv_1.1 | 2024-01-02 to 2025-12-12 | SPY EOD snapshots, reported time only, reused holdout | 20 | -2.61 | 369.02 / — | -3.61 | -65.20 | [-6.15%, +0.48%] | REPRODUCED |
| F | F_credit_hv_1.2 | 2024-01-02 to 2025-12-12 | SPY EOD snapshots, reported time only, reused holdout | 9 | -2.27 | 166.06 / — | -2.48 | -126.01 | [-4.53%, -0.33%] | REPRODUCED |
| F | F_credit_hv_1.3 | 2024-01-02 to 2025-12-12 | SPY EOD snapshots, reported time only, reused holdout | 6 | -1.52 | 110.71 / — | -1.66 | -126.45 | [-3.50%, +0.07%] | REPRODUCED |
| F | F_credit_naive_free | 2024-01-02 to 2025-12-12 | SPY EOD snapshots, reported time only, reused holdout | 37 | -2.75 | 0.00 / — | -3.50 | -37.22 | [-6.79%, +0.98%] | REPRODUCED |
| F | F_credit_naive_strict | 2024-01-02 to 2025-12-12 | SPY EOD snapshots, reported time only, reused holdout | 35 | -3.15 | 498.18 / — | -3.97 | -45.06 | [-7.57%, +1.01%] | REPRODUCED |
| F | F_credit_naive_ticks2 | 2024-01-02 to 2025-12-12 | SPY EOD snapshots, reported time only, reused holdout | 35 | -5.04 | 645.79 / — | -5.41 | -71.94 | [-9.22%, -1.26%] | REPRODUCED |
| F | F_long_har_2 | 2024-01-02 to 2025-12-12 | SPY EOD snapshots, reported time only, reused holdout | 23 | -0.21 | 212.19 / — | -4.82 | -4.66 | [-8.35%, +9.54%] | REPRODUCED |
| F | F_long_har_4 | 2024-01-02 to 2025-12-12 | SPY EOD snapshots, reported time only, reused holdout | 6 | -0.25 | 55.35 / — | -1.59 | -20.89 | [-3.36%, +3.45%] | REPRODUCED |
| F | F_long_har_6 | 2024-01-02 to 2025-12-12 | SPY EOD snapshots, reported time only, reused holdout | 0 | +0.00 | 0.00 / — | 0.00 | — | [+0.00%, +0.00%] | REPRODUCED |
| F | F_long_har_euro_iv | 2024-01-02 to 2025-12-12 | SPY EOD snapshots, reported time only, reused holdout | 39 | -0.37 | 359.80 / — | -5.54 | -4.76 | [-10.23%, +11.05%] | REPRODUCED |
| F | F_long_har_free | 2024-01-02 to 2025-12-12 | SPY EOD snapshots, reported time only, reused holdout | 38 | -0.30 | 0.00 / — | -6.51 | -3.97 | [-10.30%, +11.33%] | REPRODUCED |
| F | F_long_har_strict | 2024-01-02 to 2025-12-12 | SPY EOD snapshots, reported time only, reused holdout | 34 | -1.26 | 313.67 / — | -5.78 | -18.49 | [-10.20%, +9.31%] | REPRODUCED |
| F | F_long_har_ticks2 | 2024-01-02 to 2025-12-12 | SPY EOD snapshots, reported time only, reused holdout | 38 | -0.42 | 350.57 / — | -5.73 | -5.46 | [-10.34%, +10.97%] | REPRODUCED |
| F | F_long_hv_2 | 2024-01-02 to 2025-12-12 | SPY EOD snapshots, reported time only, reused holdout | 10 | +0.61 | 92.26 / — | -1.51 | +30.47 | [-3.97%, +5.94%] | REPRODUCED |
| F | F_long_hv_4 | 2024-01-02 to 2025-12-12 | SPY EOD snapshots, reported time only, reused holdout | 3 | -0.58 | 27.68 / — | -1.52 | -95.89 | [-2.72%, +1.43%] | REPRODUCED |
| F | F_long_hv_6 | 2024-01-02 to 2025-12-12 | SPY EOD snapshots, reported time only, reused holdout | 1 | +0.11 | 9.23 / — | -0.74 | +54.77 | [-1.28%, +1.63%] | REPRODUCED |

Returns, drawdowns and confidence bounds are percent. Fees, slippage and expectancy are USD. The option rows embed bid/ask spreads in gross P&L. A0-R uses a retrospective 40-stock universe. D's EOD proxy expectancy is per one-contract round trip. The E/F holdout was previously inspected.

The E/F capital-at-risk comparison weights daily SPY returns by average modeled maximum loss. SPY and options have different payoff distributions.
