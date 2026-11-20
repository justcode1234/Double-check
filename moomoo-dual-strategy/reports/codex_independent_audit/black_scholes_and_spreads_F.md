# F implied volatility and spread accounting

The baseline uses EOD chain IV supplied by the mirror. The independent replay rebuilt 5-minute realized variance plus adjusted overnight variance, forecast seven-session variance by rolling HAR regressions with completed training outcomes, and used the frozen IV-to-forecast thresholds. The 7–14 calendar-day expiry nearest 10 days and the ATM call/put pair determine the baseline IV. It fills buying at ask and selling at bid on the following session. This is an EOD adaptation of the original intraday rules.

European Black–Scholes inversion from both bid/ask mids at illustrative fixed rates of 4% interest and 1.3% dividend yield produced median absolute vendor IV differences of 0.45 vol points for calls and 0.43 for puts in 217 OOS candidate snapshots. This replacement of the entry IV signal left the frozen long and credit results unchanged in this sample. It keeps the baseline's vendor-IV wing construction. Actual SPY options are American-style with a physical deliverable. A European continuous-yield IV and a vendor IV cannot resolve optimal early exercise, discrete ex-dividend effects, funding or actual assigned stock. [OCC ETF options terms](https://www.theocc.com/clearance-and-settlement/clearing/etf-options) · [IV comparison and forecast data](option_implied_vol_check.csv).

| Later EOD variant | OOS trades | Net | Fees |
|---|---:|---:|---:|
| HAR straddle | 39 | −0.372% | $359.80 |
| HV straddle | 19 | −1.661% | $175.29 |
| Eligible-day straddle | 37 | −1.713% | $341.35 |
| HAR straddle, strict quote and fill budget | 34 | −1.257% | $313.67 |
| HAR condor, IV/HAR ≥ 1.2 | 0 | 0% | $0 |
| HV condor, IV/HV ≥ 1.2 | 9 | −2.268% | $166.06 |
| Eligible-day condor | 35 | −4.052% | $645.79 |

The 39-trade F-long book earned $174 before fees and lost $185.80 net. Its four-day block total-return interval is [−10.23%, +11.05%]. The earlier EOD naive straddle was −0.85%, giving a 0.48-point difference. The later baseline was −1.71%, giving a 1.34-point difference. The earlier restricted iron fly and the later condor have distinct legs and sample outcomes. The later HAR credit thresholds at 0.9, 1.0 and 1.1 yielded −3.32%, −1.71% and −0.20%, respectively. The CSV includes the remaining HAR and HV threshold runs for long and credit variants. [Earlier EOD archive](reproduction/options_eod/eod_results.csv) · [Later E/F output](reproduction/options_ef/ef_results.csv) · [Independent variant metrics](six_strategy_verdict.csv).

For disjoint put and call wings, expiry maximum dollar loss is maximum wing width × 100 × contracts minus net entry credit. Net entry credit includes all entry fees. A debit long's settled downside begins with the paid cash including entry fees. A pre-expiry four-leg liquidation can cost more than the theoretical wing after crossing bid/ask and paying closing fees. The original ledger violated its max-loss value on two naive positions and understated it under stressed fills because the calculation used pre-stress quotes. Its 44 OOS credit benchmark and HV positions independently reproduce seven assignment-risk flags. The flags compare ITM short extrinsic value to a dividend inside the remaining window for calls and one cent for puts. They do not simulate assignment or estimate its probability. [Assignment flags](option_assignment_flags.csv) · [Risk bug examples](bugs_found.md).

The adjacent-strike benchmark uses −dC/dK and 1 − dP/dK to estimate the probability of settlement above a strike, with discounting approximated as one. A put debit spread loses when settlement is above its break-even. A call debit spread loses when settlement is below its break-even. The probability helper rejects slopes inconsistent with a probability in [0, 1]. [Independent probability tests](test_audit.py).
