# Backtest methodology - strategies A, B, C (offline, SIMULATED)

Historical simulation only. No orders, no broker writes, no account access beyond Moomoo market-data history
requests. All trades in `trades_*.csv` are labelled SIMULATED. Prior audits (`final_audit.md`,
`backtest_comparison.json`, `backtest_b85841adb51f96b7/`) are untouched.

## Reproduce

```
.venv/bin/python -m dual_strategy.research.fetch      # once; resumable, writes data-cache/history/ + manifest.json
.venv/bin/python -m dual_strategy.research.run        # all variants, writes reports/ and config/c_frozen.json
.venv/bin/python -m pytest -q tests/test_research_engine.py
```

`reports/backtest_run_manifest.json` records the command, git commit, a content hash of all cached data files,
the config hash of every variant, the C freeze hash and library versions.

## Data

Moomoo `history-kline` via the `moomoo` CLI (see `data_inventory.md`): SPY 1-minute bars, 40 stocks 5-minute bars,
daily raw and adjusted bars, splits and dividends, 2020-11-02 .. 2026-11-17 (the first two months are warm-up).
Session calendar: `exchange_calendars` XNYS (holidays and 13:00 half-days); timestamps are converted from UTC to
America/New_York, so DST is handled by the time-zone database, not by fixed offsets.

## Portfolios and accounting

- Three independent portfolios, each starting at $50,000 in every segment; no cross-subsidy.
- Sizing uses start-of-day equity; realized P&L is booked the same day; every position is flat at the close
  (no overnight risk, no financing).
- Net P&L = gross P&L - fees - slippage, per trade and per day (tested identity).

## Execution model (same for all strategies)

| Item | Baseline | Stress |
|---|---|---|
| Fees (competition) | $0.99 per order + $0.000166 per share sold, capped at $8.30 | - |
| Fees (paper replication only) | $0.0035/share (+ $0.001/share slippage for Noise-Area) | competition fees |
| Slippage | 5 bps adverse per fill | 0 / 2 / 5 / 10 / 15 bps |
| Signal -> fill | market order at the next bar open after the signal bar closes | +1 / +2 (B: +5) bars |
| Stops | software: native stop price worsened 5 bps | native; stressed = act at the close of the touching bar |
| Partial fills | at most 20x the fill bar's volume; unfilled entry remainder is cancelled, exit remainder fills on the next bar(s), then the close | 0.05x |
| Rejects / missed signals | none | 2% each, deterministic sha256 draws (reproducible) |
| Stale data | a missing bar is never filled; orders move to the next existing bar | - |
| Monitoring (B, C loss lock) | every minute | every 5 / 15 / 30 min |

The 5 bps baseline is deliberately stricter than the repo's `config/backtest.yaml` (1 bp spread + 1 bp slippage);
SPY's quoted spread is usually about 1 cent (~0.2 bps). The 0 and 2 bps sweep rows show the cost sensitivity.

## No look-ahead

Cross-day features (ATR14, ADV14, RVOL baseline, Noise-Area sigma, SPY 14-day vol) use only strictly prior sessions;
intraday signals use only bars that have closed at decision time, and fills happen at a later bar. Tests perturb
future bars and assert identical decisions, check warm-up NaNs, DST, half-days, splits, duplicates, out-of-RTH bars,
intrabar ambiguity, partial fills, duplicate orders, stale data, fees and the accounting identity.

## Validation design

| Segment | Dates | Use |
|---|---|---|
| Development | 2021-01-04 .. 2022-12-30 | C parameter grid; nothing else is fitted |
| Validation | 2023-01-03 .. 2023-12-29 | reported, never used to re-select |
| Untouched OOS | 2024-01-02 .. 2026-11-17 | final evaluation |

- Walk-forward: C re-selected on rolling 2-year windows, tested on the next 6 months; A/B (no fitted parameters)
  on half-year windows.
- Uncertainty: circular day-block bootstrap (5-day blocks, 2,000 reps) for Sharpe and total return.
- Benchmarks: SPY buy-and-hold total return, and SPY scaled to each variant's mean gross exposure.
- Ratios need at least 60 sessions; annualized figures and "statistically meaningful" need at least 252 sessions and
  30 trades; otherwise N/A.

## Gates (`final_backtest_audit.md`)

A strategy is GO (for supervised paper trading only) only if every gate passes: data (A is BLOCKED / EXPLORATORY by
construction); sample >= 252 OOS sessions and >= 30 trades; OOS Sharpe > 0 with bootstrap P(Sharpe <= 0) < 0.05;
2023 validation net > 0; OOS net > 0 at 10 bps; OOS net > 0 under combined stress at 10 bps; OOS excess over
exposure-matched SPY > 0; OOS max drawdown <= 5%; walk-forward >= 60% positive half-years with a positive stitched
return; and, for C, a positive development result.
