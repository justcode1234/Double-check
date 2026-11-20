# Independent Moomoo × Qoder audit

**No strategy demonstrated a positive, supported competition edge.** The headline simulation returns reproduce. Their interpretation changes where data, execution or handbook rules differ from the simulation. B1 long-only had the smallest observed loss among the A–C competition variants and its uncertainty includes cash. A faithful A0 and the original intraday D, E and F remain **BLOCKED (DATA)**.

| Strategy | Frozen result | Evidence |
|---|---|---|
| A | A1 −38.25%, long-only −25.95% | Restricted, retrospectively selected stock universe. A0's result flips with 5-minute bar ordering. |
| B | B0 +33.03%, B0-100 +9.36%, B1 −3.17%, B1 long-only −1.77% | B0's leverage and cost assumptions produce most of the headline gap. B1 benefits from lower modeled costs and remains sensitive to them. |
| C | C1 −9.33%, long-only −6.36% | Negative development grid and negative zero-fee/zero-spread sensitivity with software-stop latency retained. |
| D | Original blocked | A penny yields $1 gross per standard contract. Fees take about $4.61 for a one-contract round trip. A separate GDELT EOD proxy falls below the always-up baseline in the later samples. |
| E | EOD −10.20%, 55 trades | Independent cash ledger and weaker forecast calibration reproduce. A quote and risk-budget sensitivity remains negative. |
| F | EOD long −0.37%, 39 trades. Frozen credit 0 trades. | Long gross +$174.00, fees $359.80 and net −$185.80. The credit benchmark exposes weaknesses in maximum-loss reporting. |

## Reproduction

Run from the project directory on Windows with the original local inputs and Python environment available.

    & .\reports\codex_independent_audit\reproduce.ps1

Add -IndependentOnly to reuse the archived reproduction outputs. The full command runs the original suite, the A–C research runner, the six-strategy stock study, both EOD options generations, and the GDELT proxy using its cached tone. The rerun redirects research outputs into this directory. The independent code reads the raw bars and verified option chain fields and does its own event or valuation calculations. The paper A0 is blocked. Its rolling features, rankings, trigger, stop and ledger arithmetic were independently checked on a stratified sample of 22 restricted-universe A0-R trades. The sample also covers 21 A1 and 21 A1-long-only trades. B/C and E/F used independent full trade replays.

The audit froze 1,801 original files using SHA-256 before execution and verified them again afterwards. Matching source outputs include 372 A–C result rows, all three archived equity curves and trade CSVs, 45 prior and 72 later option result rows, 1,171 later option trades, 490 option return rows and the D sentiment JSON. Original tests reported **934 passed and 55 subtests passed**. Eleven independent audit checks also passed. [Environment](environment.txt) · [Input hashes](input_sha256.csv) · [Post-run input verification](input_verification_after_run.json) · [Reproduction comparisons](reproduction_comparisons.csv) · [Original test log](pytest.log) · [Audit test log](independent_pytest.log).

## Results

- [Six-strategy verdict](six_strategy_verdict.md) and [variant metrics](six_strategy_verdict.csv).
- [Claim-by-claim check](claim_by_claim_check.md) and [bugs and reproducible examples](bugs_found.md).
- [Trade ledger](trade_ledger.csv), [ledger hash](trade_ledger.sha256), [A trade samples](stock_A_sample.csv), [A end-of-bar peak exposures](stock_A_peak_exposure.csv), [B/C reconciliations](stock_BC_trade_comparison.csv) and [E/F reconciliations](option_trade_comparison.csv).
- [Fee calculations](fee_audit.csv), [data quality](options_data_quality.md), [B ablation](ablation_B0_vs_B1.md), [D sentiment](sentiment_D.md), [E calibration](model_calibration_E.md), [F pricing and spreads](black_scholes_and_spreads_F.md) and [risk](monte_carlo_risk.md).

A–C holdout labels cover 2024-01-02 through 2026-11-17. E/F cover 2024-01-02 through 2025-12-12. The E/F dates were already inspected during earlier strategy development. All reported trades are simulated. Research used cached inputs and public sources without accounts, paid requests, order submissions, permission changes or active configuration edits.

The earlier repository comparison used a short October–November 2026 bar sample with unverified historical corporate actions and universe membership. It remains a separate experiment. The named options_backtest_results.md was absent. The earlier options_eod_backtest.md and later options_ef_backtest_results.md each had corresponding local code, inputs and output tables. [Earlier A/B comparison](../backtest_comparison.json) · [Earlier options report](../options_eod_backtest.md) · [Later E/F report](../options_ef_backtest_results.md).
