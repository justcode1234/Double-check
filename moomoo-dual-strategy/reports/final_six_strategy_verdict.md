# Final six-strategy verdict (A–F), 2026-11-19

All results are SIMULATED offline backtests on separate $50,000 research ledgers. No orders were placed and no account was touched. Out-of-sample (OOS) = 2024-01-02 → 2026-11-17 (723 sessions); none of these rules or parameters was tuned on it. Costs are competition fees plus 1 bp/side slippage on SPY and 3 bp/side on stocks, unless a row is labelled as published-paper rules.

## Summary

| | Variant | Tested period | OOS net | Max DD | Sharpe [95% CI] | 2025–26 net | 8-session MC: median / P(profit) / 5th pct | Data quality | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| **A** ORB | A1 competition | 2021-01 → 2026-11 | **−38.3%** | −42.1% | −1.97 [−3.58, −0.67] | −26.0% | −0.81% / 29% / −2.58% | 40 present-day stocks, 5-min bars | **NO-GO** (exploratory) |
| | A1-LO | same | −26.0% | −27.6% | −1.46 | −15.2% | −0.53% / 33% / −2.09% | same | NO-GO |
| | A0-R published rules | same | −88.3% | −89.0% | −6.05 | −77.3% | −2.57% / 12% / −5.08% | intrabar ambiguity decides 69% of trades | **BLOCKED** (faithful) |
| **B** Noise-Area | B1 competition | same | **−3.2%** | −6.2% | −0.48 [−1.73, 0.54] | −3.2% | −0.06% / 42% / −0.58% | SPY 1-min, 0 gaps | **NO-GO** |
| | B1-LO | same | −1.8% | −3.4% | −0.41 | −2.0% | −0.02% / 40% / −0.37% | same | NO-GO (least bad) |
| | B2 = B0 signals, competition size+fees | same | −3.6% | −6.7% | −0.51 | −3.7% | −0.07% / 41% / −0.61% | same | NO-GO |
| | B0 published (≤4× leverage, $0.0035/sh) | same | +33.0% | −17.5% | 0.74 [−0.44, 1.68] | −1.8% | +0.02% / 51% / −3.26% | same | NO-GO: not significant, edge gone in 2025–26, not competition-feasible |
| **C** VWAP reversion | C1 competition | same | **−9.3%** | −9.4% | −2.39 [−3.50, −1.42] | −6.4% | −0.09% / 29% / −0.50% | SPY 1-min | **NO-GO** (loses before costs; development FAIL) |
| | C0 100% notional, paper costs | same | −6.1% | −7.1% | −0.60 | −4.8% | −0.05% / 44% / −0.94% | same | NO-GO |
| **D** buy option on sentiment, sell at +$0.01, no stop | — | **intraday exit not testable** (no minute-level option quotes) | n/a (the earlier EOD proxy, −53.7%, is **not** a valid test of a penny intraday exit) | n/a | n/a | n/a | BLOCKED | fee arithmetic on official fees + real spreads | **NO-GO by arithmetic; intraday evaluation BLOCKED.** One contract pays $4.61 in fees against $1 gross. Positive only for ≤ $0.10 options at ≥ 11 contracts, needing a 97–99.9% win rate (`reports/options_fee_and_strategy_D_feasibility.md`) |
| **E** Monte Carlo debit verticals | E (thresholds frozen on 2021–22) | 2021-01 → 2025-12-12, EOD quotes | **−10.2%** (re-run in `options_ef_backtest_results.md`; first run −8.8%) | −10.5% | −2.27 | (no 2026 data) | −0.04% / 15% | real EOD bid/ask | **NO-GO**: forecast less accurate than the option market's own; 48/55 trades were losing put spreads; worse than random |
| **F** IV-vs-forecast options | F-long (straddle when IV ≤ HAR) | same | **−0.4%** | −5.5% | −0.03 | (no 2026 data) | −0.01% / 34% / −1.02% | same | **NO-GO** (≈ 0; adds +0.5 pp over a naive straddle). F-short fired once OOS: **inconclusive**; naive iron fly −10.4% |

D/E/F rows were updated after historical SPY option quotes were found (free EOD mirror, validated: `reports/options_eod_backtest.md`). Their sample ends 2025-12-12, so it differs from A–C (to 2026-11-17). Don't rank raw returns across the two samples.

The 8-session Monte Carlo is 10,000 day-block-bootstrap paths (4-day blocks) of OOS daily net returns. Results barely change with block size (1/2/4/8): `reports/monte_carlo_A_to_F.csv`.

## Ranking

- **Highest expected risk-adjusted return within competition rules:** none is positive. Least negative: B1-LO (Sharpe −0.41), then B1 (−0.48), B2 (−0.51). A and C are clearly negative.
- **Highest short-horizon upside:** B0 (8-session 95th percentile +5.1%). It needs up to 4× SPY leverage, which the 40%-per-ticker cap forbids, and it lost 1.8% over 2025–26. Within the rules, A1 has the widest upside tail (+2.4%), but with a −0.8% median and a 29% chance of profit.

## Evidence notes

- **B:** B0 and B1 share the same signals (627/627 matched). The edge is 1.65 bp per trade before costs, against 3.0 bp of competition costs, and it decayed to 0.10 bp/trade in 2026. See `reports/B0_B1_B2_trade_reconciliation.md`.
- **A:** the gross edge is about zero (A1 −1.4% before costs over the OOS). Tight stops plus position caps leave a median risk of about $14 per trade, which fixed fees and slippage overwhelm. The universe is survivorship-biased: SPCX, SKHY, MDLN, CRWV, SNDK, NBIS and GEV listed during the test window.
- **C:** C0 (near-zero costs) is negative in every year 2021–2026. All 27 development grid cells had a negative Sharpe at baseline cost. The entry-window bug is fixed and re-tested.
- **F (proxy study, not P&L):**
  - VIX exceeded realised SPY vol on 73–98% of days, by a mean of +2.9 to +4.5 vol points.
  - The development-chosen "rich" filter (VIX − HAR ≥ 6 points) raised the OOS mean premium to 7.7 vs 3.5 points, across 68 days with overlapping 21-day windows.
  - The development grid itself was non-monotonic (2.9 / 2.7 / 2.5 / 3.3), so the filter is weak evidence.
  - The "cheap → buy straddle" leg is contradicted: on cheap days implied still beat realised by 5.3 points.
  - HAR beat a naive 22-day average only out-of-sample (6.8 vs 7.4 RMSE), not in development or 2023.
  - None of this accounts for bid/ask, skew or short-volatility tail losses.
- **E:** the zero-drift bootstrap under-predicted up-moves (P(5-day up) 53% predicted vs 61% realised OOS). With no forecast skill, any E edge would have to come from option mispricing, which cannot be tested without chains.
- **D0 stated exit:** see `reports/six_strategy/D0_exit_economics.csv`.
  - "+1 cent": every win **loses money** on options priced above $0.10 ($1.00 gross vs $1.93 round-trip fees per contract). On ≤$0.10 options it nets at most +$0.07 per contract at 100 lots, needing ≥98.9% wins with no stop.
  - "+1% of premium": loses on premiums below $1.93 (round-trip fees at 4+ contracts). At $5 it nets +$3.07 per contract but needs a 99.4% win rate to cover one full-premium loss.
  - Neither version includes the bid/ask crossing (the mid must move target + 2 × half-spread).

- **Options update (EOD quotes):**
  - E is negative in validation and OOS, and worse without the MC filter (−11.9%).
  - F's naive short-vol iron fly loses −10.4% OOS, despite the VIX-proxy "variance premium". Wings, the bid/ask and fees eat it.
  - D fails at its theoretical best case.
  - Details and stress tests: `reports/options_eod_backtest.md`.

## False or unsupported earlier results

0. **My earlier "D/E/F BLOCKED — no historical option chains"** was true of Moomoo, but the user's suggested free mirror does contain validated EOD SPY chains for 2008–2025. E and F are now tested (end-of-day version) and D is bounded.

1. **v1 "B1 −20.6%"**: an artefact of the 5 bp/side slippage baseline (about 30× SPY's spread). It is −3.2% at 1 bp and +1.8% at 0 bp.
2. **"A0 −88%" is not a measurement**: it flips to +177% under the opposite intrabar assumption. The faithful ORB test is BLOCKED.
3. **C in v1/v2** stopped entering at 13:30 because of a bug. Corrected, it's slightly worse (−9.3%).
4. **My v2 40% total cap on A1** misread the handbook (the rule is per ticker); corrected in v3.
5. **B0's +33%** is real in this simulation, but only with leverage up to 4× and near-zero costs. It is not significant (CI includes 0), not achievable in the competition, and lost money in 2025–26.
6. **The README's 19-session A/B comparison** (A +0.18%, B −0.54%) is too short to mean anything; superseded.

## Recommendation for the competition (6 sessions left)

- **No strategy passes.**
- If you must trade, **B1-LO** (SPY Noise-Area, long-only, 40%) is the least bad: an expected loss of about 0, and an 8-session 5th percentile of −0.37%. That is close to holding cash, which is an equally defensible choice.
- **Do not deploy:**
  - A: negative, and it needs sub-second stops the paper API can't provide.
  - C: negative.
  - D: negative even if every +1¢ target is hit.
  - E: −8.8% OOS on real quotes.
  - F: about 0 OOS, with auto-exercise risk.
- The rubric gives 75% to process (Qoder use, risk design, documentation). A documented, reproducible NO-GO with these controls is a stronger submission than an untested options bet.

## Artifacts

| File | Content |
|---|---|
| `reports/strategy_specs_A_to_F.md` | Versioned rules, sources, adaptations, citations |
| `reports/data_coverage_and_bias_audit.md` | Provenance, hashes, point-in-time checks, biases, how to unblock D/E/F |
| `reports/trade_level_ledger_A_to_F.csv` | 17,157 SIMULATED OOS fills (A/B/C). D/E/F have no rows |
| `reports/backtest_A_to_F.csv` | 58 rows: variant × dev/val/oos/full/2025–26, plus D/E/F N/A rows |
| `reports/monte_carlo_A_to_F.csv` | 10,000-path bootstrap by variant, sample, block size; cost and parameter sensitivity |
| `reports/six_strategy/mc_8session_distribution.svg`, `drawdown_oos.svg`, `reports/backtest_v3/equity_curve_*.svg` | Charts |
| `reports/B0_B1_B2_trade_reconciliation.md` | Same-date B attribution |
| `reports/independent_validation_checks.md` | Tests, pricer cross-checks, bugs, what was not run |
| `reports/six_strategy/{E_calibration,F_signal_study,B_trade_reconciliation}.json`, `D0_exit_economics.csv`, `B_step_attribution.csv`, `manifest.json` | Study outputs |
| `reports/backtest_v3/` | Full A–C run (dashboard page `/backtest`) |
| `config/six_strategy_freeze.json`, `config/c_frozen.json` | Spec and parameter freezes |

## Reproduce (Windows Git Bash, project root)

```bash
PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest -q -p no:cacheprovider    # 915 passed
PYTHONUTF8=1 .venv/Scripts/python.exe -m dual_strategy.research.run        # ~8 min -> reports/backtest_v3/
PYTHONUTF8=1 .venv/Scripts/python.exe -m dual_strategy.research.six        # ~2 min -> six-strategy artifacts
```

Inputs: `data-cache/history/` (manifest hash `4e0bb91d…`) and `data-cache/history_extra/` (VIX, SPY IV), plus seeds fixed in code.
