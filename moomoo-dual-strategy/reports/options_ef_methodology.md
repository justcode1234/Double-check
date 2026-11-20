# E and F: methodology (EOD SPY options, 2021-01-04 → 2025-12-12)

Code: `src/dual_strategy/research/options/ef_study.py` (study), `eod_backtest.py` (fill engine), `montecarlo.py`, `strat_f.py`, `pricing.py`. Command: `PYTHONUTF8=1 .venv/Scripts/python.exe -m dual_strategy.research.options.ef_study`. Outputs: `reports/options_ef/`.

## Timing (identical for every variant)

1. **Decision at day d's 16:00 ET snapshot.** Inputs: day-d option quotes; SPY closes ≤ d; realised variance through d; no later data.
2. **Entry at day d+1's snapshot:** each leg separately, buy at the **ask** and sell at the **bid**, optionally worsened by 1–2 ticks in stress tests. Fill size is capped at the displayed size, and a leg with no two-sided quote cancels the entry. Mid prices are never used as fills.
3. **Take-profit** is observed at day t's snapshot and filled at t+1 (bid/ask). **Scheduled exits** (expiry close for E; the session before expiry for F) fill at their own snapshot.
4. **Expiry:** ITM legs are closed at the expiry-day quote. If unquoted, intrinsic value from the Moomoo close is used and counted as an *estimated fill*: 0% of legs in every variant. OTM legs expire with no order and no fee.
5. **Fees:** handbook schedule per leg order: $0.65 (premium > $0.10) or $0.15 per contract with a $1.99 minimum, plus $0.30 platform, $0.015 regulatory and $0.00279 activity (sells) per contract.
6. **Splits:** development 2021–2022 (all thresholds chosen here, one fixed rule: highest daily Sharpe with ≥ N trades and Sharpe > 0, otherwise the pre-registered default and a development FAIL); validation 2023; OOS 2024-01-02 → 2025-12-12.

**Contamination disclosure.** OOS 2024–25 results of the earlier E and F variants were inspected before this study. The new variants (E-drift, E-hv, F_credit, HV-forecast baselines) were designed after that, so their OOS is not strictly untouched, although their parameters were still chosen on 2021–22 only. A genuinely fresh holdout needs 2026 option quotes, which we don't have. **No 2026 options result is claimed.**

## Strategy E: Monte Carlo debit verticals (defined risk)

- **Expiry:** the first listed expiry 5–10 sessions after d (H sessions).
- **Candidates:** call and put debit verticals. Long strike is the listed strike nearest S_d; short strike is the listed strike nearest S_d·(1 ± w), w ∈ {1%, 2%}. Both legs need bid > 0, OI ≥ 500 and spread ≤ 10% of mid (5% and 20% in stress tests). Debit D = ask_long − bid_short (decision-time quotes), with 0 < D < width.
- **Forecast models (physical measure, past data only)**, using r = the trailing 500 daily log returns of adjusted SPY up to d:
  - `boot` (original E): circular 5-day block bootstrap of demeaned r, rescaled to EWMA(λ=0.94) volatility, 10,000 paths, zero drift.
  - `drift`: `boot` × exp(mean(r)·H), i.e. the trailing mean return added back.
  - `hv`: lognormal, σ = std of the last 20 returns, drift −σ²/2.
  - `rn` (benchmark only, not traded): risk-neutral P(S_T > x) = −∂C/∂K (calls) or 1 − ∂P/∂K (puts), from adjacent-strike mids. Estimates outside [0, 1] are discarded.
- **Signal:** edge = E[net payoff]/(D + fees), with net = clip(±(S_T − K_long), 0, width) − D − fees. Also P(loss) = P(net < 0). Enter the best-edge candidate if edge ≥ θ and P(loss) ≤ π, with (θ, π) chosen on development data from a 5 × 3 grid.
- **Size and exits:** max loss ≤ 0.5% of equity, ≤ 3 open. Exit at 50% of max gain or at the expiry close.
- **Selected on 2021–22:** boot (0.00, 0.65), drift (0.30, 0.55), hv (0.10, 0.60).
- **Calibration** (all candidates, hold-to-expiry outcome; `ef_calibration_scores.csv`, `ef_reliability_oos.svg`, `ef_coverage_pit.csv`):
  - Brier score and log loss of P(loss), against climatology (the development loss rate) and the risk-neutral baseline.
  - Probability integral transform (PIT) of S_T, and 50%/90% interval coverage.

## Strategy F: implied vs forecast volatility (defined risk)

- **Expiry:** the expiry 7–14 calendar days out (nearest to 10).
- **Market vol:** IV = mean of the dataset's ATM call and put IV (nearest strike). The ATM call/put average cancels most carry error.
- **Forecasts** of realised vol (annualised), made at d's close:
  - `har`: HAR-RV (Corsi 2009) with a 7-session horizon, refit daily on the trailing 756 sessions using only fully observed targets. RV = 5-min RTH log-return variance + overnight² (Moomoo bars).
  - `hv` (constant-vol baseline): mean RV of the last 20 sessions.
- **Black–Scholes' role:** IV is the BS-implied number. BS is the *translation* between price and volatility, not a source of edge. The signal is only the comparison with an independent forecast.
- **F_long (original long-vol):** buy the ATM straddle when 100·(IV − forecast) ≤ −c, c chosen on development data from {0, 2, 4, 6} (≥ 10 trades). Size: debit ≤ 2% of equity (one SPY ATM straddle costs about $800–1,200), one open. Exit at +50% of debit (bid) or the close one session before expiry.
- **F_credit_defined_risk (new, labelled):** sell an iron condor when IV/forecast ≥ R, R chosen on development data from {1.0, 1.1, 1.2, 1.3, 1.5} with ≥ 30 trades.
  - Short strikes: nearest S ± EM, EM = S·IV·√T. Long wings: short strikes ± max($5, round(0.5·EM)). Wings must be outside the shorts and every leg liquid.
  - Max loss = (wing − credit)·100·n + entry fees, ≤ 1% of equity; ≤ 3 open, never two on one expiry.
  - Exit when the cost to close is ≤ 50% of the credit, otherwise the close one session before expiry (no expiry-day assignment risk).
  - **No naked short options at any time.**
- **Selected on 2021–22:** F_long har c = 0 and hv c = 0 (development pass). F_credit har and hv: **development FAIL** (the most trades at any R was 8 for har and 18 for hv, below the 30 required), so the pre-registered R = 1.2 was used.
- **Early assignment / exercise audit:** while each credit position was open, flag short legs that were ITM with extrinsic value below the next SPY dividend (calls) or < $0.01 (puts). The engine does not model early assignment; the handbook describes only auto-exercise of ITM options at expiry.

## Benchmarks

- Cash.
- SPY buy-and-hold, and SPY scaled to each variant's average capital at risk (crude exposure match).
- Same-structure-every-day versions: `E_always_same_structure`, `F_long_naive`, `F_credit_naive`.
- **Random-timestamp, random-direction verticals** (200 seeds, matched entry frequency).
- Constant-HV forecasts for E (`hv`) and F (`hv`).

## Limitations

1. EOD only: no intraday entries, stops or 0-DTE dynamics; take-profits fill a day late.
2. The 16:00/16:15 snapshot ambiguity; vendor not named.
3. Small samples. F holds one position (F_long) or ≤ 3 (F_credit), so 10–40 trades per segment. No Sharpe is reported below 30 trades or 120 sessions.
4. Contract granularity. The "half size" stress produced **zero** trades for E (0.25% = $125 < one spread) and F_long (1% < one straddle), so results are sensitive to sizing.
5. The OOS was previously inspected (above). No 2026 data.
6. Early assignment of short American legs (ex-dividend) is flagged, not simulated.
7. HAR forecasts were biased high relative to IV in OOS (median IV/HAR 0.94), so the HAR credit rule almost never fired. That is a property of the forecast, not a free parameter to re-tune on the OOS.
