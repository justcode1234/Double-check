# Data coverage and bias audit (2026-11-19)

## Sources and hashes

| Dataset | Source / access | Coverage | Integrity |
|---|---|---|---|
| SPY 1-minute RTH bars (raw, bar-end timestamps) | Moomoo CLI `history-kline --ktype 1 --autype 0`, fetched by Qoder on macOS, pushed in commit `5614548` | 2020-11-02 → 2026-11-17, 1,518 sessions: 1,516 complete, 2 with missing bars, 0 missing sessions | `data-cache/history/manifest.json`: 415 files, content hash `4e0bb91d…8764820`. All 415 file hashes re-verified on Windows; v1 results reproduced exactly (B1 −20.637%, C1 −20.908%) |
| 40 stocks, 5-minute RTH bars | same | same window. Names listed later start later (see bias) | same |
| Daily raw + forward-adjusted, 42 symbols | `history-kline --ktype 2` | 2019-01-02 → 2026-11-17 | same |
| Splits / dividends | `corporate-actions` | every split matches every >3% jump in the raw/adjusted close ratio (`verify_splits`: 0 unmatched) | same |
| VIX daily (Cboe 30-day S&P 500 IV) | `history-kline US..VIX --ktype 2`, fetched 2026-11-19 | 2019-01-02 → 2026-11-17, 1,987 days | `data-cache/history_extra/manifest.json` |
| Moomoo SPY IV/HV daily | `option-underlying-his-volatility US.SPY --count 260` (API maximum; `--end-time` paging returns empty) | 2025-11-06 → 2026-11-18 only | same |
| Historical option chains via Moomoo | **Not available**: expired contracts return `INVALID_SYMBOL`; there is no historical chain endpoint | none | — |
| **Historical SPY option chains, end of day** (added 2026-11-19) | github.com/anahatsingh-ui/options-dataset-hist (MIT preservation mirror of Philipp Dubach's dataset, Alpha Vantage-style schema), commit `37f6c45` | 2021-01-04 → **2025-12-12**, 11.37M rows: 4 PM bid/ask, sizes, volume, OI, IV, Greeks | git-blob-verified; validation in `reports/options_data_validation.json` (parity, IV recompute, VIX, Moomoo close). **EOD only, no 2026.** E/F tested, D bounded: `reports/options_eod_backtest.md` |
| **Historical timestamped news / sentiment** | **Not available**: `news-by-security` returns the latest page only, with no date range | **none** | D0 signal cannot be reconstructed point-in-time |

Time zone: all intraday bars are converted from epoch-ms UTC to America/New_York. The XNYS calendar (exchange_calendars) supplies holidays, half-days (210 bars) and DST. Tests: `test_loader_dst_duplicates_out_of_rth`, `test_c_no_entry_after_1430`, `test_c_entries_allowed_until_1430_on_a_full_day`.

## Point-in-time and leakage controls

| Feature | Uses only | Test |
|---|---|---|
| ATR14 / ADV14 | 14–15 completed prior sessions, split-adjusted for splits between sessions only | `test_split_back_factor_and_split_adjusted_atr` |
| RVOL | today's first 5-min bar ÷ mean of the 14 prior first bars | `test_a_filters_and_doji` |
| Noise σ (B) | 14 prior sessions | `test_noise_sigma_uses_only_prior_sessions_and_needs_14_day_warmup` |
| VWAP | cumulative through the closed bar | `test_vwap_is_cumulative_through_the_bar_only` |
| B decisions | closed bar; fill at next 1-min open | `test_b_trades_unchanged_when_future_bars_change` |
| HAR-RV forecast (F) | training targets fully observed before the decision | `test_har_forecast_has_no_lookahead` |
| E Monte Carlo | returns up to the prior close; EWMA from the same window | `test_ewma_vol_uses_only_given_past` |
| Option snapshot | quotes strictly before the decision and ≤60 s old | `test_snapshot_excludes_future_and_stale_quotes` |
| VIX in F | prior day's close (shift 1) | code: `six.py` `f_study` |
| C parameters | 2021–22 grid only; no profitable cell, so preregistered (0.30/0.25/60) kept | `test_c_freeze_refuses_tampered_file` |

## Known biases (not fixable with this data)

1. **A universe is survivorship- and look-ahead-biased.** It holds today's 40 highest-turnover names. Several were not listed for most of the test: SPCX (first bar 2026-06-12), SKHY (2026-07-10), MDLN (2025-12-17), CRWV (2025-03-28), SNDK (2025-02-13), NBIS (2024-10-21), GEV (2024-03-27). There is no point-in-time universe, no market-cap history (handbook ≥$500M and spec ≥$1B are unchecked) and no delisted names, so every A result is EXPLORATORY.
2. **A uses 5-minute bars; the paper uses 1-minute.** Whether the entry or the stop came first inside a bar is unknown. 69% of A0-R trades are settled by that assumption: −88% if the stop came first, +177% if the entry did (v1 ablation).
3. **Sector labels are current**, not point-in-time.
4. **No quotes.** Spread and slippage are modelled in basis points (SPY 1 bp, stocks 3 bp per side) and swept 0–15 bp. SPY's quoted spread is about 0.15–0.2 bp, so the SPY baseline is conservative.
5. **F proxy basis.** VIX is a 30-day SPX variance-swap-style index (it includes skew), not SPY ATM IV. Over the 259-day overlap it correlates 0.996 with Moomoo's SPY IV, at +0.17 vol points on average. Before 2025-11 the proxy cannot be checked.
6. **F warm-up.** The HAR forecast needs about 270 prior sessions, so its development window is 2022-03-03 → 2022-12-30, not all of 2021–22.
7. **Overlapping horizons.** E (5-day) and F (21-day) outcomes overlap day to day, so their effective sample sizes are much smaller than the day counts. No significance test here treats them as independent.

## Smallest step to unblock D/E/F

**Update 2026-11-19:** EOD chains were found (row above), so E and F are tested end-of-day and D is bounded. Still missing: **intraday** option quotes (to measure D's +1¢ exit and intraday entries) and any timestamped news. Databento ($125 credit) or ThetaData ($40/mo) would supply intraday quotes, and both need your account. The original note follows.

D/E/F need historical **intraday SPY option quotes**: bid, ask, size, OI, strike, expiry and right, timestamped, for 2021–2026, plus the user's written D rules. Legitimate sources that need the user's approval and money, and that I have **not** used:

- Cboe DataShop (Option EOD/intraday, per-month purchase)
- OptionMetrics IvyDB via WRDS (institutional licence)
- ORATS (intraday/EOD, subscription)
- ThetaData (OPRA-derived NBBO history, subscription)
- Databento OPRA (pay-per-use)
- Polygon/Massive Options (subscription)

Even a single source covering SPY quotes at 10:00 and 15:30 ET daily for 2021–2026 would let E and F run exactly as specified. Prices change, so check each provider's current terms.
