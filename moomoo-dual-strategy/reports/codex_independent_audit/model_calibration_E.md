# E Monte Carlo debit verticals

The independent implementation rebuilt candidate chains and forecasts, selected spreads with the original frozen grid outcomes, and simulated the next-session fill with ask for long legs and bid for short legs. It uses 500 adjusted SPY log returns through the decision date's close, 10,000 terminal draws, circular 5-session blocks, zero mean and EWMA 0.94 rescaling. The trailing-drift version adds back the past mean. The HV version uses a 20-return lognormal estimate. The first available expiry 5–10 exchange sessions out is searched at the ATM long strike and 1%/2% wings. Trade structure, liquidity, expected return and maximum-loss probability are determined from the decision-day snapshot. The payoff at expiration is the signed strike difference clipped to zero and wing width. Options trade at recorded quotes in the baseline and at those quotes plus adverse ticks in stress.

| OOS forecaster | Brier, original labelled pairs | Brier, outcomes within the split | Log loss, original |
|---|---:|---:|---:|
| Option-implied adjacent strikes | 0.22930 | 0.22980 | 0.65086 |
| Trailing-drift bootstrap | 0.23281 | 0.23329 | 0.65880 |
| Zero-drift bootstrap | 0.23488 | 0.23607 | 0.66260 |
| HV20 lognormal | 0.24297 | 0.24389 | 0.67971 |

All scores use the same candidates with valid expiry outcomes and option-implied probability. There are 771 such OOS candidates before removing seven that settle after the sample cutoff. The original 2021–22 loss-rate comparator scores 0.23795 in the OOS sample. Risk-neutral probabilities estimated from narrow quoted strike spreads ignore small discounting and American exercise. They are descriptive benchmark probabilities. Several structures share each date and outcomes overlap by expiry. Raw row counts overstate independent sample size. [Independent candidate scores](e_independent_calibration.csv) · [Candidate-level labels](e_independent_calibration_candidates.csv).

| OOS strategy | Trades | Net | Observation |
|---|---:|---:|---|
| Original bootstrap | 55 | −10.204% | −$4,802 gross before $299.85 fees |
| Trailing drift | 6 | −1.814% | Six losses, small sample |
| HV20 | 77 | −10.011% | All put spreads |
| Eligible-day structure benchmark | 67 | −8.842% | Cheapest debit relative to wing, without forecast filter |
| Original with stricter fills and fill-time risk budgets | 46 | −7.404% | Combined quote, sizing and settlement sensitivity |
| Original, two adverse ticks per leg | 55 | −10.724% | Quotes with additional spread cost |

The independent ledger shows 48 put spreads in the 55-trade call/put mix. Put spread P&L was −$5,031.72 and the full set had 46 losers. Eleven of the 55 fills exceeded the decision-day risk budget. The historical quotes model EOD execution. Simultaneous executable spread prices, borrow or exercise when a short leg is assigned, and the intraday setup are outside the baseline.

The archived random experiment returned median −2.1345% and fifth percentile −5.2833% over 200 seeds. It averaged 35.1 actual OOS trades and never reached 55. The exposure and count mismatch preclude using the lower-tail ranking as a matched statistical test. Earlier EOD E was −8.796% on 51 trades under a different rule set. The later E-family window was previously examined, and the holdout analysis is retrospective. [Original random experiment](reproduction/options_ef/ef_random_baseline.csv) · [Independent trades](option_independent_trades.csv) · [Detailed variants](six_strategy_verdict.csv).
