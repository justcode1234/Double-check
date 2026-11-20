# Risk from simulated daily returns

The independent calculation draws 10,000 circular paths for each horizon and block size, with seed 20261119. Returns compound within paths. A 95% full-window interval is the 2.5th and 97.5th percentiles of the four-day block bootstrap. The eight-session risk grid evaluates one-, two-, four- and eight-day blocks on reset-window returns. Historical cash days are retained. Negative return, zero return and positive return are separate outcomes.

| OOS variant | 8-session median | P(negative) | 5th percentile | Mean in lower tail |
|---|---:|---:|---:|---:|
| A1 | −0.81% | 71.1% | −2.58% | −2.95% |
| B0 | +0.02% | 49.2% | −3.26% | −4.22% |
| B1 | −0.06% | 57.8% | −0.58% | −0.74% |
| B1 long-only | −0.02% | 56.3% | −0.37% | −0.51% |
| C1 | −0.09% | 69.9% | −0.50% | −0.70% |
| E boot | −0.04% | 57.0% | −0.95% | −1.22% |
| F-long HAR | −0.01% | 51.0% | −1.02% | −1.31% |
| F-credit HAR | 0.00% | 0.0% | 0.00% | 0.00% |
| D original | — | — | — | — |

F-credit's zero results represent zero trades at the frozen threshold. D has no valid daily return series. The reported E and F probability of profit can coexist with a much smaller probability of loss than one minus that number because many paths remain flat. B1 long-only's 95% full-window interval [−6.00%, +3.00%] includes cash. E's interval [−15.92%, −4.30%] describes the simulated EOD rule under the historical execution proxy. A–C and options use different end dates. Bootstrap tails depend on the observed sample and carry no new market regimes, assignment paths, gap risk, live platform behavior or unobserved intraday fills. [Risk grid](monte_carlo_risk.csv) · [Full-horizon metrics](six_strategy_verdict.csv) · [Original nested E/F risk results](reproduction/options_ef/ef_monte_carlo.json).
