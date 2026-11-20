# Options backtest on historical SPY quotes: D, E, F (2026-11-19)

SIMULATED fills on **real historical end-of-day SPY option quotes**. No model-generated prices are used as fills. No orders were placed.

## 1. Data sources investigated

| Source | Free? | What it actually offers | Usable here |
|---|---|---|---|
| github.com/anahatsingh-ui/options-dataset-hist | **Yes**, MIT, no account | Preservation mirror of Philipp Dubach's dataset: daily 4:00 PM snapshots of SPY/QQQ/IWM chains 2008 → **2025-12-12**, with bid/ask and sizes, mark, last, volume, OI, BS IV and Greeks (24.7M SPY rows). Its schema matches Alpha Vantage's historical-options output; the vendor is unnamed | **Yes**: downloaded SPY 2021–2025 (283 MB), pinned to commit `37f6c45`, every file git-blob-verified |
| optionsdx.com | Free EOD variant | SPY EOD/intraday CSVs, but the year selector covers **2010–2023 only**; checkout flow | No 2024–26; not needed |
| thetadata.net | Free = 1 year of EOD (from 2023-06) | 1-minute quotes from $40/mo (Value, 2020+); tick NBBO from $80/mo. Needs an account and the Java Theta Terminal | Needs your account and money |
| databento.com | $125 sign-up credit | OPRA.PILLAR intraday (CBBO-1m/1s, trades, definitions), no IV or Greeks. Needs an account and API key | Needs your account; could fund a sample of intraday SPY quotes |
| Historical timestamped **news** | None of the four | — | **Not available** |

Account-based sources were not used: creating accounts in your name needs your approval.

## 2. Validation of the free dataset (`reports/options_data_validation.json`)

| Check (SPY 2021-01-04 → 2025-12-12) | Result |
|---|---|
| Rows / trading days | 11.37M rows. 2021: 252, 2022: 251, 2023: 250, 2024: 252 (+1 junk holiday row on 2024-01-15, 2 rows, dropped), 2025: 238 (**ends 2025-12-12**) |
| Duplicates / expired contracts / NaN or negative quotes / crossed markets | 0 / 0 / 0 / 0 (locked bid=ask: 27–39 rows per year) |
| Zero-bid rows | 4.7–8.8% (far OTM). Any leg we trade must have bid > 0 |
| Underlying close vs Moomoo SPY close | identical on all 1,243 days |
| Put–call parity (C−P vs strike, 3,280 expiry snapshots) | residual ≤ half-spread for 94%. Implied carry r−q by year: −0.2%, +0.4%, +3.7%, +4.2%, +3.7%, consistent with T-bills minus ~1.3% dividend yield |
| IV recomputed from mid vs dataset IV (ATM 20–45 DTE) | median abs diff 0.92 vol pts, correlation 0.97. Delta median abs diff 0.013 |
| ATM IV vs VIX | correlation 0.965; VIX ≈ ATM IV + 3.0 pts (skew), as expected |
| ATM 5–15 DTE spread | median $0.02–0.05 (0.6–1.0% of mid); displayed size ~70–130 contracts |
| Known defects | `in_the_money` flag wrong (agrees 50%), so it is ignored. `mark` ≠ mid on <1% of rows, so bid/ask are used. No 2026 data. **End-of-day only** |

Verdict: good enough for end-of-day backtests of E and F. It **cannot** validate anything that depends on intraday price paths.

## 3. What can now be tested

| Strategy | Testable? | Why |
|---|---|---|
| **E** (Monte Carlo debit verticals) | **Yes, EOD version** | Decisions at the close, multi-day holds; next-day fills on real bid/ask |
| **F** (BS IV vs forecast) | **Yes, EOD version** | Same |
| **D** (buy, sell at +$0.01, no stop; sentiment dropped per your instruction) | **Bounded, not fully** | EOD closes miss intraday touches of +1¢, so the measured hit rate is a lower bound. Running every trade at a 100% hit rate gives a hard upper bound, and that upper bound is **negative** |

EOD rules, fixed before any results: decide on day d's 16:00 snapshot; buy at d+1's **ask**, sell at d+1's **bid**; take-profits trigger at d and fill at d+1 (D uses a resting limit that fills at exactly the target); handbook option fees on every leg order; fills capped at the displayed size; legs never priced from a model. Segments: development 2021–2022 (thresholds chosen here only), validation 2023, OOS 2024-01-02 → 2025-12-12.

## 4. Results (net of fees and bid/ask; separate $50,000 ledgers)

> **Superseded for D and E (2026-11-19):**
> - The D rows below are an **EOD proxy, not a backtest** of D's intraday +$0.01 exit, which EOD data cannot observe. D's decisive test is the fee arithmetic in `reports/options_fee_and_strategy_D_feasibility.md`.
> - E and F are re-tested with calibration, baselines and new variants in `reports/options_ef_backtest_results.md`.

| Variant | Dev 2021–22 | Val 2023 | **OOS 2024–25** | OOS Sharpe | OOS max DD | OOS trades / win % | Verdict |
|---|---|---|---|---|---|---|---|
| **E** (MC filter, frozen edge ≥ 0, P(loss) ≤ 0.65) | +0.5% | −9.7% | **−8.8%** | −2.02 | −9.1% | 51 / 18% | **NO-GO** |
| E without MC filter (always trade) | −12.7% | −8.1% | −11.9% | −1.37 | −13.2% | 124 / 32% | NO-GO |
| **F-short** (iron fly when IV − HAR ≥ 6 pts) | +1.5% (12 trades) | +1.9% (1) | −0.5% (**1 trade**) | — | −0.9% | 1 / 0% | **INCONCLUSIVE**: the signal almost never fires |
| F-short naive (iron fly every eligible day) | −9.7% | −9.9% | −10.4% | −1.06 | −12.5% | 60 / 50% | NO-GO |
| **F-long** (straddle when IV ≤ HAR) | +4.4% (11) | +1.3% | **−0.4%** | −0.03 | −5.5% | 39 / 51% | **NO-GO** (≈ 0) |
| F-long naive (straddle every eligible day) | +6.1% | +1.7% | −0.9% | −0.08 | −5.4% | 40 / 53% | NO-GO |
| **D** ATM call, +1¢, no stop | −48.7% | −18.7% | **−53.7%** | −6.97 | −53.7% | 243 / 0%* | **NO-GO** |
| D ATM put, +1¢, no stop | −47.8% | −21.4% | −58.7% | −4.51 | −58.7% | 243 / 0.4%* | NO-GO |
| D cheap call (ask ≤ $0.10, 20 lots), +1¢ | −53.5% | −29.2% | −66.7% | −6.68 | −66.7% | 240 / 0.4%* | NO-GO |

\*D "wins" lose money. The +1¢ target was reached (EOD lower bound) on 30.5% of ATM-call trades, 18.9% of ATM-put trades and 2.1% of cheap-call trades. Each hit still nets **−$3.61 per contract** (+$1.00 gross vs about $4.61 round-trip fees on 1 contract; about −$0.93 per contract at 4+ contracts). Unhit trades average −$157 (most expire worthless or near it).
**Upper bound (every trade hits +1¢):** −$877 (ATM, 243 trades) and −$408 (cheap, 240 trades) over the OOS. Intraday data can only move D between these bounds, and both are negative.

Stress, OOS (`reports/options_eod/eod_results.csv`):

- **Doubling the bid/ask cost:** E −9.9%, F-long −1.4%, F-short naive −14.2%.
- **Halving it:** E −8.9%, F-long −0.3%, F-short naive −7.8%.
- **Optimistic same-snapshot fills:** E −11.1%, F-long −1.0%.

No variant turns positive under any cost assumption.

## 5. Forecast calibration and Monte Carlo

- **E's simulated probabilities were overconfident.** On executed OOS trades it predicted a mean P(loss) of 0.62, but 0.82 of trades lost. Its Brier score of 0.183 is worse than climatology's 0.145. This matches the SPY-level calibration study (skill ≈ 0). The simulation is a forecast, not an edge.
- **F's IV-vs-forecast signal adds almost nothing.** On the long side it beat the same-days naive straddle by +0.5 pp OOS (−0.4% vs −0.9%), which is noise. The short side traded once OOS.
- **8-session Monte Carlo** (10,000 paths, 4-day blocks; `eod_monte_carlo.csv`):

| Variant | Median | P(profit) | Note |
|---|---|---|---|
| E | −0.03% | 17% | |
| F-long | −0.01% | 34% | |
| F-short naive | −0.07% | | 5th percentile −1.8% |
| D ATM call | −1.05% | 0.01% | P(5% drawdown) 0.3% |
| D cheap call | −1.28% | | P(loss > 3%) 15% |

## 6. Remaining limitations

1. **EOD only.** No intraday entries or exits, no intraday stop paths, no 0-DTE intraday behaviour. D's +1¢ exit can only be bounded, not measured.
2. **No 2026 data.** The sample ends 2025-12-12, so A–C (to 2026-11-17) and D–F are **different samples**; don't rank raw returns across them.
3. **Unnamed upstream vendor.** Provenance is a preservation mirror of a third-party dataset. It validates well, but it isn't exchange-certified NBBO.
4. **Small samples.** F trades one position at a time (11–60 trades per segment). F-short fired once OOS, so it is unproven, not disproven.
5. **Exercise and assignment.** Positions are closed at the expiry close at quoted prices. Early assignment of short American legs (ex-dividend) is not modelled; the handbook only describes auto-exercise at expiry.
6. **No historical news anywhere**, so a sentiment-driven D cannot be tested (you've since dropped sentiment).

## 7. Smallest next data step

To measure D's +1¢ exit and test intraday E/F entries, you'd need one of:

- **Databento**: the $125 sign-up credit, OPRA `cbbo-1m` for SPY on a sample of days.
- **ThetaData Options Value**: $40/month, 1-minute quotes from 2020.

Both need an account in your name. D already fails at its upper bound, so this would only refine E and F.

## Reproduce

```bash
.venv/Scripts/python.exe -m pip install pyarrow==22.0.0          # Parquet reader (added this session)
# data: data-cache/options_history_raw/dubach_mirror/ (commit in SOURCE_COMMIT, hashes in manifest.json)
PYTHONUTF8=1 .venv/Scripts/python.exe -m dual_strategy.research.options.validate_chains   # -> reports/options_data_validation.json
PYTHONUTF8=1 .venv/Scripts/python.exe -m dual_strategy.research.options.eod_backtest      # -> reports/options_eod/
```

Outputs:

- `reports/options_eod/eod_results.csv`: every variant × segment × stress
- `eod_trades.csv`: every SIMULATED trade, with legs, fills, fees and exit reason
- `eod_monte_carlo.csv`, `eod_calibration.json`
- `eod_freeze.json`: development-only threshold grids and selections
- `eod_equity_oos.csv`
