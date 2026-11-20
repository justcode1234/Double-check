# Options release verdict: D, E, F (2026-11-19)

Scope: offline simulation only. No orders, accounts, purchases or permission changes. Evidence: historical EOD SPY option quotes 2021-01-04 → 2025-12-12 (validated free mirror), GDELT news tone, and the official handbook fees. **No 2026 options result exists or is claimed.**

| Variant | Verdict | Decisive evidence |
|---|---|---|
| **D**: sentiment signal, buy option, sell at +$0.01, no stop (unchanged) | **NO-GO**; intraday evaluation **BLOCKED** | 1 contract: $4.61 round-trip fees vs $1.00 gross per win. Any premium > $0.10: −$0.93 or worse per contract per win. Net-positive only for ≤ $0.10 options at ≥ 11 contracts, with the bid reaching ask + 1¢; that needs a **97–99.9% win rate** with no stop, against 2–7% observed at closes. Sentiment quality, tested separately (GDELT stock-market tone, 15:30 ET batch): direction hit rate 55.5% in 2021–22 (p = 0.007) but 49.4% in 2023 and 52.7% in 2024–25, below just betting "up". Correlation with returns ≈ 0. The 1-day option round trip is negative in every segment. Never approved for unattended trading (loss up to 100% of premium per position) |
| **E**: Monte Carlo debit verticals (original) | **NO-GO** | OOS −10.2% (55 trades, Sharpe −2.27). Loses **before** fees (−$4,802 gross). Below the 5th percentile of random same-structure trades. Its probabilities are less accurate than the option market's own (Brier 0.235 vs 0.229). 48/55 trades were losing put spreads (skew read as cheapness by a zero-drift model) |
| E-drift (trailing drift) | **NO-GO** (inconclusive sample) | Best E forecast (Brier 0.233), still worse than the market's. Its development-chosen filter traded 6 times OOS, all losers |
| E-hv (lognormal HV) | **NO-GO** | OOS −10.0% (77 trades), all put spreads, worst calibration |
| **F_long**: straddle when IV ≤ HAR forecast (original long-vol) | **NO-GO** | OOS −0.4% (39 trades, Sharpe −0.03, CI spans 0). Gross +$174 is erased by $360 fees. Only +1.3 pp over a naive straddle, which is noise. The HV-forecast version is worse (−1.7%) |
| **F_credit_defined_risk**: iron condor when IV/forecast ≥ R | **NO-GO** | Development FAIL (too few 2021–22 trades at every R). The frozen rule made 0 OOS trades (HAR ran above IV). Descriptive OOS sweep: every threshold negative (−0.2% to −3.3%). Naive condor −4.1% and loses before fees. 7/44 positions carried early-assignment risk. Defined risk held (no loss beyond max loss) |

## What failed, and why

- **Structural, not data-related:**
  - D's exit is beaten by fees on any normal-size order.
  - E prices risk with a forecast that is worse than the one already embedded in option prices.
  - Short-dated SPY vol selling at these strikes and costs did not pay.
  - Better data would not change these three.
- **Data-limited:**
  - D's intraday path (whether the +1¢ is touched before expiry) can't be observed with EOD quotes. It doesn't matter for one-contract or > $0.10 orders, which lose on every win regardless.
  - No uncontaminated holdout: the 2024–25 OOS was inspected for earlier variants, and there are no 2026 quotes.
- **Alternatives tested within E and F:** drift-adjusted and HV forecasts, defined-risk credit spreads, HV-forecast baselines, spread filters, tick stresses and sizing. **None improved matters enough to pass.**

## Single highest-priority next action

**Keep D, E and F out of the competition account** (6 sessions left), and do not spend on intraday data for them.

If options research continues afterwards, the one step with real value is to start a **free, read-only daily recording of the SPY option chain at 15:55–16:00 ET via the existing Moomoo CLI**. That builds the uncontaminated 2026+ holdout that every variant now lacks; no variant should be reconsidered without passing on it.

## Evidence files

- `reports/options_data_access_and_provenance.md`
- `reports/options_fee_and_strategy_D_feasibility.md`
- `reports/options_ef_methodology.md`
- `reports/options_ef_backtest_results.md` and `.csv`
- `reports/options_risk_monte_carlo.md`
- Raw outputs: `reports/options_ef/` (trades, freeze, calibration, reliability plot, Monte Carlo, D sentiment)

## Reproduce (Windows Git Bash, project root)

```bash
.venv/Scripts/python.exe -m pip install pyarrow==22.0.0
PYTHONUTF8=1 .venv/Scripts/python.exe -m dual_strategy.research.options.validate_chains   # data checks
PYTHONUTF8=1 .venv/Scripts/python.exe -m dual_strategy.research.options.ef_study          # E/F (~15 min)
PYTHONUTF8=1 .venv/Scripts/python.exe -m dual_strategy.research.options.gdelt_tone        # GDELT (~40 min, resumable)
PYTHONUTF8=1 .venv/Scripts/python.exe -m dual_strategy.research.options.d_sentiment       # D sentiment test
PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest -q -p no:cacheprovider                    # 934 passed
```

Hashes: `data-cache/options_history_raw/dubach_mirror/manifest.json` (commit `37f6c45`), `data-cache/history_extra/gdelt_files_manifest.json`, `reports/options_ef/ef_freeze.json` (frozen thresholds), `config/six_strategy_freeze.json`.
