# Strategy specifications A–F (v3, frozen 2026-11-19)

Spec/config freeze: `config/six_strategy_freeze.json` (sha256 of every strategy, execution and data-manifest file, written **before** the v3 holdout run). Data cutoff 2026-11-17. Every result is a SIMULATED $50,000 research ledger; there are six ledgers, not six broker accounts.

Segments: development 2021-01-04..2022-12-30 · validation 2023 · untouched OOS 2024-01-02..2026-11-17 · recent 2025-01-02..2026-11-17.

## Competition rules used (handbook T&C, re-read verbatim 2026-11-19)

- **Stock/ETF fees:** $0 commission, $0.99 platform fee per order, $0.000166 per share sold (cap $8.30, sells only).
- **Option fees:**
  - Commission: $0.65/contract when the premium is above $0.10, $0.15/contract at $0.10 or below, with a $1.99 minimum per order.
  - Platform fee $0.30/contract, activity fee $0.00279/contract, regulatory fee $0.015/contract.
  - No separate index-option schedule.
- **40% rule:** stock/ETF exposure may not exceed 40% of total initial account assets, applied **per ticker** ("no single ticker exceeds 40%"). Options are excluded, with a cap of 1,000 contracts per contract.
- **Eligibility:** price ≥ $1 and market cap ≥ $500M.
- **Limits:** ≤100 positions; ≤200 executed orders per day.
- **Shorting and margin:** shorting is available (60% initial margin; 6% p.a. interest).
- **Options at expiry:** ITM options are auto-exercised after the close; OTM options expire worthless.
- **Final pricing:** regular-market close on the final day.
- An earlier summary in this project called the 40% rule a combined cap. That reading was wrong and is corrected in v3.

## A — 5-minute Opening Range Breakout (stocks)

| Item | A0-R (published rules, restricted universe) | A1 (competition) | Source |
|---|---|---|---|
| Universe | 40 present-day top-turnover US stocks (`config/research_universe.json`) | same | survivorship-biased, so **EXPLORATORY** |
| Filters | open > $5, 14-day ADV ≥ 1M sh, ATR14 > $0.50, RVOL ≥ 1.0 | same | `research/strat_a.py:27-31,82` |
| RVOL | first-5-min volume ÷ mean first-5-min volume of prior 14 sessions (split-adjusted) | same | `research/features.py` `opening_rvol` |
| Selection | top 20 by RVOL | top 8 | `strat_a.py:27`; `run.py:133` |
| Direction | 09:30–09:35 candle: green → long only, red → short only, doji → none | same | `strat_a.py` `candidates` |
| Entry | resting stop at OR high/low (fill = max(open, level)) | market at next 5-min open after a bar trades through the level; no entries after 10:30 | `strat_a.py:32,45,149` |
| Stop | 0.10 × prior ATR14 from **actual entry** (native) | same, software stop (+5 bp latency) | `strat_a.py:34`; `execution.py:75` |
| Target | none; exit 16:00 | none; flatten 15:55 | `strat_a.py:43` |
| Size | min(1% equity risk, 4× equity ÷ 20) per name (up to 4× gross) | min(0.25% risk, 15%/name); ≤4 open, ≤30%/sector, ≤100% gross, ≤1% of prior-bar volume | `strat_a.py:37-46,107-113` |
| Risk overlay | none | daily loss lock −1.25% (flatten, no new entries) | `strat_a.py:44,227` |
| Costs | $0.0035/sh, no slippage | competition fees + 3 bp/side | `execution.py:47,49` |
| Faithful replication | **BLOCKED**: no point-in-time universe, market-cap history or delisted names; stock bars are 5-minute only (paper uses 1-minute) | — | `reports/data_coverage_and_bias_audit.md` |

## B — SPY Noise-Area intraday momentum

| Item | B0 (published) | B2 (B0 signals, competition sizing+fees only) | B1 (competition) | Source |
|---|---|---|---|---|
| Bands | σ(slot) = mean over prior 14 sessions of \|close_slot ÷ open_0930 − 1\|. Upper = max(open, prev close − div)·(1+σ); lower = min(…)·(1−σ) | same | same, but strict 14-session history | `strat_b.py`, `features.py` `noise_sigma` |
| Decisions | bar ends 10:00, 10:30 … 15:30. Long if close > upper **and** > VWAP; short if < lower **and** < VWAP; else flat. **Stateless target rule** (the stateful trailing variant is ablation `B1-stateful-trail`) | same | same | `strat_b.py:1-10` |
| Fill | next 1-min open | same | same | |
| Stop | no price stop; exit at the next 30-min check | same | + daily loss lock −1.25% (checked each minute) | `strat_b.py:29` |
| Exit | 16:00 close | 16:00 close | flatten 15:55 | `strat_b.py:28` |
| Size | min(4, 2% ÷ σ SPY 14-day) × equity (median 2.5×) | 40% equity | 40% equity | `strat_b.py:22-23`; `run.py:162-169` |
| Costs | $0.0035/sh + $0.001/sh | competition + 1 bp/side | competition + 1 bp/side | `execution.py:46,50` |

## C — SPY intraday VWAP mean reversion (preregistered hypothesis, not a published strategy)

| Item | Value | Source |
|---|---|---|
| Signal | d = (5-min close − session VWAP(HLC3)) ÷ prior ATR14. From 10:00: long arms at d ≤ −0.30 and triggers on a later 5-min close with d > −0.30 within 6 bars (mirror for short) | `strat_c.py:22-25` |
| Entry window | 10:05–14:30 (half-days: close − 90 min). **Bug fixed 2026-11-19**: was 13:30 (`close − 150`); test `test_c_entries_allowed_until_1430_on_a_full_day` | `strat_c.py:51` |
| Stop / target / time | 0.25 × ATR14 from entry (stop checked first on ambiguous 1-min bars) / back to VWAP / 60-min max hold / 15:50 hard exit | `strat_c.py:23-24,52` |
| Size | C0: 100% equity, paper costs, native stops. C1: min(0.25% risk, 40% notional), competition + 1 bp, software stop +2 bp | `strat_c.py:27-29,38-41` |
| Parameter freeze | (0.30, 0.25, 60) re-selected on 2021–22 only after the cutoff fix. **No grid cell was profitable in development** (best Sharpe −0.97), so the preregistered cell is kept and the development gate FAILS. The pre-fix freeze is preserved as `config/c_frozen_v1_pre_cutoff_fix.json` | `config/c_frozen.json` |

## D — news-sentiment options (the user's strategy, preserved as D0)

| Item | Status |
|---|---|
| Repository definition | **None.** Searched for sentiment/news/option/profit-target/take-profit: no file defined D before 2026-11-19 |
| Stated rules | Chat (2026-11-19): "reads news sentiment and only aim options and once it get **1 cent** profit it sells and has no stop loss". Prompt (2026-11-19): "no trade-level stop-loss and the originally defined **1%** profit target" |
| Conflict | +$0.01 per share vs +1%. Whether "1%" means premium or equity is not stated |
| Undefined | news source and timestamps, sentiment model and threshold, calls/puts/spreads, buy vs sell premium, underlyings, strike, expiry, size, concurrency, treatment of an open loser at expiry |
| Verdict | **D0 BLOCKED — ORIGINAL RULES UNAVAILABLE** (and HISTORICAL OPTIONS CHAINS MISSING). Nothing was invented. Stated-exit economics: `reports/six_strategy/D0_exit_economics.csv`; code `research/options/strat_d.py` |
| Risk (if long premium) | Premium at risk = 100% of the debit per contract. If it sells premium, loss is undefined (structure unknown). Exercise: ITM long options auto-exercise at the close, which needs cash for 100 shares per contract or causes forced liquidation |
| D1 (risk-managed comparison) | Not run; it would be a different strategy. Proposed only after D0's rules are written down |
| **User revision (2026-11-19)** | "ignore sentiment, just keep buy and take profit of 1 cent with no stop loss" |
| D as tested (EOD) | Buy at the next close's ask: (a) ATM call, (b) ATM put, (c) the highest-priced call with ask ≤ $0.10 at 20 contracts; nearest expiry ≥ 2 sessions out; 1 position at a time. Resting sell limit at entry + $0.01; no stop. Unhit positions held to expiry (ITM sold at the bid, OTM worthless). The user left strike, expiry, size and side open; these are labelled assumptions. Code: `research/options/eod_backtest.py` `run_d` |

## E — Monte Carlo-driven SPY debit verticals (preregistered)

Rule text: `research/options/strat_e.py` (module docstring).

- **Forward distribution:** at 10:00 ET, 10,000 terminal paths from a circular 5-day block bootstrap of the trailing 500 daily log returns (past only, demeaned), rescaled to EWMA(0.94) volatility.
- **Contracts:** the expiry 5–10 sessions out; call and put debit verticals, long ATM, short ±1% or ±2%.
- **Costs:** pay the long ask and receive the short bid, plus handbook fees on 4 orders.
- **Entry:** only if E[net] ÷ max loss ≥ 0.10 and P(loss) ≤ 0.60. Both legs must be quoted with spreads ≤ 10% of mid and OI ≥ 500.
- **Size:** max loss ≤ 0.5% of equity; at most 3 open trades.
- **Exit:** 50% of max gain, or 15:30 on expiry day.
- **Thresholds:** preregistered, **not** fitted, because no development chains exist.
- **Status:**
  - Option backtest **BLOCKED** (no chains).
  - Empirical part: forecast calibration (Brier) on SPY 2021–2026, in `reports/six_strategy/E_calibration.json`.

## F — Black–Scholes implied-vs-forecast volatility, defined-risk SPY options (preregistered)

Rule text: `research/options/strat_f.py` (module docstring).

- **Forecast:** HAR-RV forecast of 21-session realised vol, refit daily on the trailing 756 sessions, using only targets fully observed before the decision.
- **Market vol:** ATM IV of the 7–14-day expiry, inverted from mid with a CRR American model, continuous dividend yield and T-bill rate. Quote filters as in E.
- **Trade:**
  - IV − forecast ≥ RICH: sell an ATM iron butterfly (wings ±1 expected move).
  - ≤ −CHEAP: buy the ATM straddle.
- **Size:** max loss ≤ 0.5% of equity.
- **Exit:** 50% of max profit, or close one session before expiry (no exercise risk carried).
- **Naive baseline:** the same structure on every day.
- **Status:**
  - Option backtest **BLOCKED** (no chains).
  - Empirical part: VIX-proxy signal study, in `reports/six_strategy/F_signal_study.json`.

## Citations

| Source | Used for |
|---|---|
| Zarattini, Barbon & Aziz (2024), *A Profitable Day Trading Strategy for the U.S. Equity Market* (concretumgroup.com PDF) | A rules |
| Zarattini, Aziz & Barbon (2024), *Beat the Market: An Effective Intraday Momentum Strategy for S&P500 ETF (SPY)* (concretumgroup.com) | B rules |
| github.com/PazSheimy/spy-intraday-momentum-oos | B independent replication reference |
| Corsi (2009), *A Simple Approximate Long-Memory Model of Realized Volatility* (J. Fin. Econometrics) | F forecast (HAR-RV) |
| Black & Scholes (1973); Merton (1973); Cox, Ross & Rubinstein (1979) | F/E pricing |
| Competition handbook, moomoo-qoder-challenge-rgkelnyuzbm.qoder.website (read 2026-11-19) | fees, limits |

Published returns from these papers are **not** this project's results.
