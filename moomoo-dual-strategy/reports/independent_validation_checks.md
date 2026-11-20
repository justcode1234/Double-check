# Independent validation checks (2026-11-19)

Full suite: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest -q -p no:cacheprovider` gives **915 passed, 55 subtests passed**, with 4 third-party deprecation warnings (Windows, Python 3.14.7, numpy 2.5.5, pandas 3.1.1).

## Reproducibility

| Check | Result |
|---|---|
| Data integrity | All 415 files in `data-cache/history/manifest.json` re-hashed on Windows: 0 mismatches. Content hash `4e0bb91d…8764820` equals the v1 run's |
| Cross-machine reproduction | v1 (macOS) B1 and C1 OOS reproduced exactly on Windows with v1 settings: −20.637% and −20.908% |
| Run-to-run determinism | Reject/miss draws are SHA-256-seeded by event identity; MC seed 20261119; E MC seed 7. B1 is identical in v2 and v3 (−3.170%), since the A and C changes don't touch it |
| Spec freeze | `config/six_strategy_freeze.json` (sha256 of strategy, execution and config files, data manifest) written before the v3 holdout run |
| C freeze tamper guard | `test_c_freeze_refuses_tampered_file` |

## Options pricer: independent reference (TradingCalc MCP v2.17.0, signed results)

| Case | TradingCalc | This repo | Test |
|---|---|---|---|
| BS call S=730 K=730 10d σ=20% r=4% q=1.2% | 9.915958441 | 9.915958441 (Δ<1e-9) | `test_bs_matches_tradingcalc_reference` |
| same put | 9.356357190 | Δ<1e-9 | same |
| delta / gamma / vega per pt / theta per day | 0.51567 / 0.016490 / 0.48151 / −0.50930 | Δ<1e-9 | same |
| American put S=730 K=760 30d σ=25% (CRR 500) | 38.552711172 | Δ<1e-6 | `test_american_put_matches_tradingcalc_reference` |
| IV of a $7.50 call, K=740, 14d | 19.84709% | Δ<1e-6 | `test_implied_vol_matches_tradingcalc_reference` |

## Options pricer: internal identities

- **Put–call parity, no-arbitrage bounds:** 3 cases (`test_put_call_parity_and_bounds`).
- **Greeks vs finite differences:** delta and vega to rel 1e-5, gamma to 1e-3 (`test_greeks_match_finite_differences`).
- **American vs European:**
  - American ≥ European.
  - An American call with no dividend equals the European call.
  - CRR converges to Black–Scholes.
  - Test: `test_american_dominates_european_and_call_without_dividend_is_european`.
- **Dividend/American sensitivity:** deep-ITM American puts carry an early-exercise premium of +0.276 on the reference case. F's rule therefore inverts IV with CRR American and rejects quotes outside model bounds (`implied_vol` returns None).
- **Expiry payoffs** for verticals, and intrinsic value at T=0 (`test_expiry_payoffs`).

## Fees and slippage

| Check | Test |
|---|---|
| Stock: $0.99/order, $0.000166/sh sold, cap $8.30 | `test_competition_fees_platform_plus_sell_side_capped` |
| Options: $0.65/$0.15 per contract, $1.99 minimum, $0.30 platform, $0.015 regulatory, $0.00279 activity on sells | `test_option_fee_schedule` (verbatim handbook values, re-read 2026-11-19) |
| Slippage always adverse | `test_slippage_is_always_adverse` |
| Net = gross − fees − slippage, per trade and per day | `test_round_trip_accounting_identity` |
| Partial fills and spill-over to later bars | `test_partial_fill_capacity_and_exit_spill` |
| No duplicate entries; deterministic rejects | `test_duplicate_entry_rejected_and_rejects_deterministic` |

## Look-ahead and timing

- **Finalized bars only:**
  - next-bar fills (`test_b_no_trades_during_warmup_and_fills_after_decision`, `test_a_bar_close_market_enters_next_open_and_paper_sizing`)
  - changing future bars leaves past trades unchanged (`test_b_trades_unchanged_when_future_bars_change`)
- **Warm-up:**
  - noise σ and ATR need 14 prior sessions
  - HAR has no look-ahead (`test_har_forecast_has_no_lookahead`)
  - EWMA uses past data only
  - option snapshots exclude future and stale (>60 s) quotes
- **Stale or missing bars:** skipped, never interpolated (`test_stale_bars_skipped_never_interpolated`, `test_b_missing_decision_bar_is_a_missed_decision`).
- **Intrabar ambiguity:** C assumes the stop is hit before the target (`test_c_stop_assumed_before_target_on_same_bar`). A's same-bar stop ambiguity is parameterized as pessimistic or optimistic (`test_a_resting_stop_same_bar_ambiguity`).
- **Entry windows:** C now allows entries until 14:30 and none after (`test_c_entries_allowed_until_1430_on_a_full_day`, `test_c_no_entry_after_1430`).
- **Time zones, DST, duplicate bars, out-of-hours bars:** `test_loader_dst_duplicates_out_of_rth`. Split adjustment without look-ahead: `test_split_back_factor_and_split_adjusted_atr`.

## Risk state machine and controls (pre-existing suites, all passing)

Covered by `tests/test_risk.py`, `test_sentinel.py`, `test_sentinel_service.py`, `test_ledger.py`, `test_runtime.py` and `test_execution.py`: daily loss lock, drawdown ladder, terminal halt persistence, close-only sentinel, idempotent stop, and no retry before reconciliation. These test the forward/paper system; the research backtest applies its own daily loss lock (B1/C1/A1) as an explicit ablation.

## Bugs found and corrected this session

| Issue | Effect | Fix |
|---|---|---|
| C entry cutoff `close − 150` meant 13:30, not the specified 14:30 | C missed about 22% of its window; OOS C1 went −7.8% → −9.3% once corrected | `strat_c.py:51`, plus a new test |
| v2 capped A1 total stock exposure at 40%, from my misreading of the handbook (the rule is per ticker) | A1 OOS −28.8% (v2) → −38.3% (v3, ≤100% gross, ≤15% per name) | `strat_a.py:40` |
| v1 used 5 bp/side slippage on SPY (~30× its spread) as the baseline | B1 was shown at −20.6%; −3.2% at 1 bp, +1.8% at 0 bp | `execution.py:46-48` |
| Windows: CLI path and cp1252 decoding of UTF-8 CLI output | the fetcher could not run on Windows | `fetch.py` |

## Not executed, and why

- **D/E/F historical option backtests and their empirical Monte Carlo:** no historical option chains (`chain.load_chains` raises `OptionsDataBlocked`).
- **D0 point-in-time news signal:** no historical news feed, and D0's rules are undefined.
- **American exercise and assignment on real chains:** needs chains. Only synthetic pricer tests ran.
- **A faithful replication on 1-minute bars with a point-in-time universe:** that data is not available.
- **Broker paper fills:** out of scope (this task authorizes backtesting only). No orders were sent.
