# Moomoo × Qoder independent audit

## Results, formulas and parameters for strategies A–F

**No strategy demonstrated a positive, supported competition edge.** The headline historical simulations reproduced. B1 long-only had the smallest loss among the tested A–C competition variants, with a 95% return interval of **−6.00% to +3.00%**. The audit passed **934 original tests, 55 original subtests and 11 independent checks**. All **1,801 frozen input hashes** matched after execution.

| Strategy | Assessment of Claude’s verdict | Reproduced results |
|---|---|---|
| **A · Stock opening-range breakout** | Correct within the tested sample | A1 **−38.25%** (3,441 trades) and A1 long-only **−25.95%** (2,569). A faithful published A0 test remains **BLOCKED (DATA)**. |
| **B · SPY Noise-Area momentum** | Partially correct | B0 **+33.03%** (645), B0-100 **+9.36%** (645), B2 **−3.57%** (645), B1 **−3.17%** (627), and B1 long-only **−1.77%** (333). B0 had 18 additional entries, and 209 shared entries exited differently. |
| **C · SPY VWAP mean reversion** | Correct | C1 **−9.33%** (546), C1 long-only **−6.36%** (310), and C0 **−6.07%** (546). All 27 development cells had negative Sharpe. |
| **D · News-sentiment options** | Partially correct | One contract earns **$1 gross** at the exact penny target and costs about **$4.61** in round-trip fees. The first positive exact-target quantity is **11** if both premiums stay in the lower fee tier. The original intraday strategy remains **BLOCKED (DATA)**. |
| **E · Monte Carlo debit verticals** | Partially correct | The later end-of-day (EOD) version returned **−10.20%** (55), with **−$4,802 gross** and **$299.85 fees**. Drift **−1.81%** (6), historical volatility **−10.01%** (77), and eligible-day benchmark **−8.84%** (67). |
| **F · Implied vs. forecast volatility** | Partially correct | F-long HAR **−0.37%** (39), with **+$174.00 gross**, **$359.80 fees** and **−$185.80 net**. F-credit HAR had **zero trades** at the frozen threshold. The eligible-day condor returned **−4.05%** (35). |

### Three consequential discrepancies

1. **Starting capital.** The research portfolios used $50,000. The official handbook specifies $100,000 and describes a 40% stock/ETF cap per ticker. B1 returned **−1.86%** in the $100,000 sensitivity with a fixed $40,000 SPY cap. [Challenge handbook](https://moomoo-qoder-challenge-rgkelnyuzbm.qoder.website/)
2. **Option execution risk.** Next-session fills exceeded planned budgets on **11 of 55 E trades** and **5 of 39 F-long trades**. Two benchmark condors lost **$507.45** and **$554.45** against recorded limits of **$444.23** and **$422.23**.
3. **News timing.** D’s sentiment proxy used **nine 15:30 ET GDELT batches on 13:00 ET closes**. After timing exclusions, its holdout accuracy was **52.54%** against **58.47%** for always-up.

## Shared data, execution and formulas

All returns are simulated and times are U.S. Eastern. Development was **2021-01-04 through 2022-12-30**, validation was **2023-01-03 through 2023-12-29**, and the holdout began **2024-01-02**. A–C ended **2026-11-17**. Options ended **2025-12-12**, and earlier E/F research had already examined that window. A uses five-minute stock bars. B and C use one-minute SPY bars. D–F use historical SPY option bid/ask snapshots with session dates. The mirror describes closing snapshots and supplies no intraday timestamp. [SPY options data mirror](https://github.com/anahatsingh-ui/options-dataset-hist)

In the formulas, **E** inside a position-size calculation is equity, **n** is shares or contracts, **s** is +1 for long stock and −1 for short stock, and a standard SPY contract represents **100 shares**. Premiums are quoted per share. One basis point (bp) equals 0.01%.

    Stock buy fee(n)  = 0.99
    Stock sell fee(n) = 0.99 + min(0.000166 × n, 8.30)
    Buy fill  = raw price × (1 + slippage_bps / 10,000)
    Sell fill = raw price × (1 − slippage_bps / 10,000)
    Stock net P&L = s × n × (exit fill − entry fill) − entry fee − exit fee

    Option commission(n, premium) = max(1.99, n × tier)
    tier = 0.15 if premium ≤ 0.10, otherwise 0.65
    Option order fee = commission + n × (0.30 + 0.015 + 0.00279 × is_sell)
    Option leg cash flow = 100 × n × premium × (+1 for sale, −1 for purchase) − order fee
    Spread net P&L = sum of leg cash flows, including any settlement cash flow

The option replays apply the activity fee on sales. The D fee sensitivity also computes that component on both sides. E/F use the next session’s ask for buys and bid for sells after an EOD signal. Scheduled exits use their own day’s snapshot. Profit targets observed on a closing snapshot generate orders for the next session. Stock spreads and latency are modeled. [Challenge handbook](https://moomoo-qoder-challenge-rgkelnyuzbm.qoder.website/) · [FINRA activity-fee rule](https://www.finra.org/rules-guidance/rulebooks/corporate-organization/section-1-member-regulatory-fees) · [OCC ETF option specifications](https://www.theocc.com/clearance-and-settlement/clearing/etf-options)

    True range = max(high − low, |high − previous close|, |low − previous close|)
    ATR14 = mean of true range over the 14 completed prior sessions
    HLC3 = (high + low + close) / 3
    HLC3 VWAP = cumulative sum(HLC3 × volume) / cumulative sum(volume)
    Turnover VWAP = cumulative traded dollar turnover / cumulative share volume

A adjusts prices and volumes for splits when computing across-day features and uses raw prices for fills. B, D, E and F use adjusted SPY daily data where specified below.

## A · Five-minute opening-range breakout

Use the **09:30–09:35** bar. A green candle permits longs, a red candle permits shorts, and a doji is skipped. Require opening price **>$5**, prior 14-session average daily volume **≥1,000,000 shares**, prior ATR14 **>$0.50**, and opening relative volume **≥1.0**. Rank qualifying names by relative volume.

    Opening RVOL = today’s first five-minute volume / mean(first five-minute volume, prior 14 sessions)
    Long breakout level = opening bar high
    Short breakout level = opening bar low
    Stop distance = 0.10 × prior ATR14
    Stop price = raw entry price − s × stop distance
    A0-R shares = floor(min(0.01 × E / stop distance, 4 × E / (20 × entry price)))
    A1 shares = floor(min(0.0025 × E / stop distance, 0.15 × E / entry price,
                         gross dollars remaining / entry price, sector dollars remaining / entry price,
                         0.01 × previous completed bar volume))

| Parameter | A0-R restricted published-rule replay | A1 and A1 long-only |
|---|---|---|
| Selection | Top **20** | Top **8**. Long-only excludes short candidates before ranking. |
| Entry | Resting order at the breakout. On longs use the higher of the bar open and the level. On shorts use the lower. | Enter at the next five-minute open after a completed bar trades through the range. Last fill **10:30**. |
| Stop and exit | Native stop and session close. | Software stop with **5 bp** adverse latency. Flatten **five minutes before close**. |
| Risk | **1%** planned risk per trade and **4×** gross allocation divided across the top-20 slots. | **0.25%** planned risk, **15%** per name, **30%** per sector, **100%** gross, at most **4** concurrent positions and **1%** prior-bar volume. Daily loss lock **−1.25%**. |
| Costs | **$0.0035/share per side**, zero price slippage. | Competition stock fees and **3 bp per side**. |

The 40 names were NVDA, MU, AMD, SPCX, SNDK, TSLA, AAPL, AMZN, AVGO, MSFT, GOOGL, META, TSM, INTC, UNH, NBIS, CRWV, WMT, LLY, MRVL, PANW, TTWO, BRK.B, TJX, MSTR, GEV, AES, BE, CRWD, DELL, V, CAT, ASML, PLTR, MDLN, TGT, LITE, SKHY, CRM and STX. A turnover snapshot selected them after the last test day. **4,771 of 6,850 A0-R trades** entered and reached the stop in the same five-minute bar under the pessimistic rule. The return changed from **−88.32%** to **+176.61%** under optimistic sequencing. A faithful replay needs a point-in-time universe and finer timestamps. [Stock ORB paper](https://concretumgroup.com/wp-content/uploads/2026/02/A-Profitable-Day-Trading-Strategy-For-The-U.S.-Equity-Market.pdf)

## B · SPY Noise-Area momentum

At half-hour bar closes from **10:00 to 15:30**, set the target long when price exceeds both the upper band and turnover VWAP. Set it short when price is below both the lower band and VWAP and shorting is enabled. Otherwise set it flat. Changes normally execute at the next one-minute open.

    σ(t) = mean(|prior close at clock time t / that day’s 09:30 open − 1|, prior 14 observations)
    Previous price anchor = previous raw close − dividend on the current ex-date
    Upper(t) = max(today’s open, previous price anchor) × (1 + σ(t))
    Lower(t) = min(today’s open, previous price anchor) × (1 − σ(t))
    Daily vol14 = sample standard deviation of the prior 14 adjusted daily returns
    B0 fraction = min(4, 0.02 / daily vol14)
    Shares = floor(fraction × day-start equity / max(today’s open, execution bar open))

| Variant | Target fraction | History and exits | Costs |
|---|---|---|---|
| B0 | **2%** daily volatility target, capped at **4×** | Last 14 sessions with an available same-time bar. Session-close exit. | **$0.0035/share fees** and **$0.001/share adverse slippage**, per side. |
| B0-100 | **100%** | B0 timing and history. | B0 costs. |
| B2 | **40%** | B0 timing and history. | Competition fees plus **1 bp per side**. |
| B1 | **40%** | Exact prior **14 sessions** must have same-time bars. Flatten **five minutes before close**. **−1.25%** daily loss lock. | Competition fees plus **1 bp per side**. |
| B1 long-only | **40%** | B1 rules with shorts disabled. | B1 costs. |

B0 averaged **1.645 bp gross per trade**, falling to **0.097 bp** in 2026. B1 modeled costs averaged **3.017 bp per trade**. With competition fees, B1 returned **+1.85%** at zero slippage, **−3.17%** at 1 bp per side and **−20.64%** at 5 bp per side. The daily loss lock did not trigger in the holdout. The B0 run uses local price data, turnover VWAP and next-minute fills. [Published SPY strategy](https://concretumgroup.com/wp-content/uploads/2026/02/Beat-the-Market.pdf)

## C · SPY VWAP mean reversion

Build finalized five-minute bars from one-minute SPY history and maintain an HLC3 VWAP. Starting at **10:00**, arm a long when normalized distance reaches **−0.30** or lower. Trigger when a later finalized bar crosses above **−0.30** within **six five-minute bars**. The short mirrors the rule at **+0.30**. Enter at the next valid one-minute open.

    Normalized distance = (finalized five-minute close − five-minute HLC3 VWAP) / prior ATR14
    Stop distance = 0.25 × prior ATR14
    Stop price = raw entry fill price − s × stop distance
    C1 shares = floor(min(0.0025 × E / stop distance, 0.40 × E / signal close, E / signal close))
    C0 shares = floor(E / signal close)

C1 allows **one position**, takes signals through **14:30** or **90 minutes before early close**, and uses a **−1.25%** daily loss lock. Check the intrabar stop before the bar-close VWAP target. The maximum hold is **60 minutes**, with a time exit at **15:50** or **10 minutes before early close**. Non-stop exits execute at the next one-minute open. C1 uses competition fees, **1 bp per side** and **2 bp** additional software-stop latency. C1 long-only disables shorts. C0 uses **100%** sizing, native stops, no daily lock, **$0.0035/share fees** and **$0.001/share slippage** per side.

The development grid used bands **{0.20, 0.30, 0.40}**, stop multiples **{0.20, 0.25, 0.35} ATR**, and hold times **{30, 60, 90} minutes**. Selection required **at least 100 trades** and positive daily Sharpe. The best was **−0.9709**, so the evaluation kept the preregistered **(0.30, 0.25, 60)**. Moving the cutoff back to **13:30** with other rules held fixed gave **−7.81%** on **437 trades**.

## D · News-sentiment options with a penny target

The requested strategy buys a directional option and sells when its premium has risen **$0.01**, with **no trade stop-loss**. The fee test uses entry at the ask and an executable target at the bid. The original entry, option-selection and order rules and intraday quote history are incomplete. The tested EOD proxies have their own parameters.

    Target bid = actual entry premium + 0.01
    Gross target P&L = 0.01 × 100 × contracts = $1 × contracts
    Net target P&L = gross target P&L − buy order fee − sell order fee
    Worthless-expiry loss = 100 × contracts × entry premium + buy order fee
    Break-even hit rate = worthless-expiry loss / (worthless-expiry loss + net target P&L)

The break-even formula assumes profitable target fills and that every miss expires worthless. At one lot, the buy and sell fees are **$2.305** and **$2.30779** before rounding in the sell-only activity model. At ten lower-tier contracts the exact-target net is **−$0.30790**. At eleven it is **+$0.05931**. Applying activity fees to both sides gives **$4.61558** total at one lot and **+$0.02862** at eleven. Both premiums must be **≤$0.10** for this positive penny-target result, so the entry ask must be **≤$0.09**. A one-cent rise in the mid-price yields an executable premium change of one cent minus the sum of the entry and exit half-spreads.

**Sentiment proxy.** Take the **15:30** GDELT stock-market-theme batch. Deduplicate article URLs and identical tone vectors and average the remaining news tone. A positive z-score selects an ATM call and a negative score selects an ATM put. Compare with the next SPY session’s direction and with a separate one-contract option round trip. Select the nearest expiry at least **two sessions** out, buy at the current closing ask, and sell at the next closing bid.

    z = (current batch mean tone − mean of prior 20 successful batches)
        / sample standard deviation of those prior batches
    Minimum warm-up = 15 successful batches
    Next-day direction = sign(next adjusted SPY close / today’s adjusted SPY close − 1)

Directional accuracy as run was **55.53%** in development, **49.40%** in validation and **52.72%** in the holdout. The holdout option proxy averaged **−$3.56** over **475** priced round trips. After timing and expiry checks it averaged **−$2.98** over **469**. [GDELT data](https://gdeltproject.org/data.html)

**Earlier penny-target proxy.** Select at the close, buy on the next closing ask and hold one position at a time. Test a one-lot ATM call, a one-lot ATM put and a 20-lot call selected with ask **≤$0.10**. Use the nearest expiry at least two sessions out. A later closing bid at or above the target counts as a simulated fill at the exact target. Otherwise hold to expiry. The respective returns were **−53.68%**, **−58.71%** and **−66.73%**. Closing observations leave the original intraday target-hit rate unknown.

## E · Monte Carlo SPY debit verticals

The original intraday spec called for **10:00** decisions, minimum edge **0.10**, maximum loss probability **0.60** and a **15:30** expiry exit. The reproduced EOD version uses closing decisions and next-session closing entries. Choose the first listed expiry **5–10 exchange sessions** ahead. Pair an ATM long option with the contract nearest spot **±1% or ±2%**, using a higher short strike for calls and a lower one for puts. Each leg needs positive quotes, **open interest ≥500** and a spread **≤10% of mid** in the baseline.

    r[i] = ln(adjusted SPY close[i] / adjusted SPY close[i−1])
    EWMA_vol = sqrt(sum(0.94^age × r[i]^2) / sum(0.94^age))
    Rescaled return = (r[i] − mean(r)) × EWMA_vol / sample_std(r)
    Bootstrap terminal price = spot × exp(sum of sampled rescaled returns)
    Drift terminal price = bootstrap terminal price × exp(mean(r) × H)
    HV terminal price = spot × exp(−0.5 × σ20^2 × H + σ20 × sqrt(H) × Z)

H is sessions to expiry, Z is standard normal, and σ20 is the sample standard deviation of the last 20 log returns. The baseline uses **500** past returns, **10,000** terminal paths, circular **five-session** blocks and random seed **7**.

    Debit per share = long ask − short bid
    Wing = |short strike − long strike|
    Expiry spread value = clip(right_sign × (terminal spot − long strike), 0, wing)
    Forecast fee estimate per share = (4 × 0.965 + 2 × 0.00279) / 100
    Simulated net per share = expiry spread value − debit − forecast fee estimate
    Edge = mean(simulated net per share) / (debit + forecast fee estimate)
    P(loss) = share of paths with simulated net per share < 0
    Planned contracts = floor(0.005 × marked equity / (100 × decision-day debit + 9.20))

Use right_sign **+1** for calls and **−1** for puts. Require **0 < debit < wing**, select the best qualifying edge, and allow at most **three** simultaneous positions. The EOD profit target triggers when the long bid minus the short ask reaches the fee-inclusive entry debit plus **50% of the remaining distance to the wing value**. At expiry the baseline models the closing snapshot, with intrinsic value as a missing-quote fallback. Executed leg fees are computed per order. The forecast and sizing each use their own fee estimates as shown above.

The development grid was edge **{0, 0.05, 0.10, 0.20, 0.30}** × maximum P(loss) **{0.55, 0.65, 0.75}**, requiring **at least 30 trades** and positive Sharpe. The frozen thresholds were **(0.00, 0.65)** for bootstrap, **(0.30, 0.55)** for drift and **(0.10, 0.60)** as the HV20 fallback. The eligible-day benchmark selects the cheapest qualifying debit relative to its wing.

Brier score is the mean squared difference between predicted loss probability and the actual loss indicator. Adjacent strikes approximate the probability of settlement above a strike with **−dCall/dStrike** or **1 − dPut/dStrike**, approximating short-horizon discounting as one. The loss event is below break-even on calls and above break-even on puts. Holdout Brier was **0.23488** for bootstrap and **0.22930** for the option-implied comparator. Of the 55 trades, **48 were puts** and **46 trades across both directions lost**. The random baseline averaged **35.1** trades, limiting a like-for-like comparison. The strict quote and fill-budget sensitivity returned **−7.40%**. An earlier EOD implementation separately returned **−8.80%**.

## F · SPY volatility-value straddles and credit spreads

The original intraday specification called for **10:00** signals, a **21-session** HAR forecast, an American-model IV inferred from quote mids, **0.5%** planned risk and a **15:30** exit one session before expiry. The later EOD run uses **seven-session** HAR volatility and vendor IV. Choose a **7–14 calendar-day** expiry nearest **10 days** and an ATM strike nearest spot. Require positive quotes, **open interest ≥500** and baseline spread **≤10% of mid** per leg.

    Daily realized variance = sum(squared five-minute intraday log returns)
                            + squared adjusted overnight log return
    HAR inputs = [1, prior-day variance, trailing 5-session mean, trailing 22-session mean]
    HAR fit = ordinary least squares of next 7-session mean variance on HAR inputs
    Annualized HAR vol = sqrt(252 × max(forecast variance, 0.00000001))
    Annualized HV20 = sqrt(252 × mean(realized variance over last 20 sessions))
    ATM IV = (vendor IV of ATM call + vendor IV of ATM put) / 2
    Expected move = spot × ATM IV × sqrt(calendar days to expiry / 365)

HAR uses up to **756 sessions** and at least **250** usable training rows. Training targets must be fully observable at the decision. The five- and 22-session regressors require **4** and **20** observed sessions. The seven-session target allows **5** observations, and HV20 requires **18**. The independent European IV sensitivity inverted each ATM midpoint with fixed **4% interest** and **1.3% dividend yield**.

    d1 = [ln(spot / strike) + (rate − yield + IV^2 / 2) × T] / (IV × sqrt(T))
    d2 = d1 − IV × sqrt(T)
    European call = spot × exp(−yield × T) × N(d1) − strike × exp(−rate × T) × N(d2)
    European put  = strike × exp(−rate × T) × N(−d2) − spot × exp(−yield × T) × N(−d1)
    T = calendar days to expiry / 365. N = standard-normal cumulative distribution.

| Parameter | F-long straddle | F-credit iron condor |
|---|---|---|
| Entry filter | Buy ATM call and put when 100 × (IV − forecast) ≤ −c. Grid **{0, 2, 4, 6}** vol points. Frozen **c = 0** for HAR and HV. | Sell the call and put nearest spot **± expected move** when IV / forecast ≥ R. Grid **{1.0, 1.1, 1.2, 1.3, 1.5}**. Frozen fallback **R = 1.2** for HAR and HV. |
| Legs | Long call and put share the same ATM strike and expiry. | Buy wings outward from the short strikes by approximately **max($5, round(0.5 × expected move))**. Use valid listed strikes outside the shorts. |
| Planned size | Divide **2% of marked equity** by (100 × sum of entry asks + $5) and round down. Maximum **one** open. | Divide **1% of marked equity** by (100 × (max wing width − quoted credit) + $10) and round down. Maximum **three** open, one per expiry. |
| Profit trigger | Sum of both bids reaches **1.5×** the fee-inclusive entry debit per share. | Ask-to-buy shorts minus bid-to-sell wings falls to **0.5×** fee-adjusted entry credit per share. |
| Scheduled exit | Close of the exchange session before expiry. | Same scheduled exit. |

The long grid required **10** development trades and positive Sharpe. Credit required **30** and positive Sharpe. Credit failed the count gate. Eligible-day benchmarks remove the signal thresholds. Later HAR credit ratios **0.9**, **1.0** and **1.1** returned **−3.32%**, **−1.71%** and **−0.20%**. The earlier EOD short variant was an **ATM iron fly** with wings around one expected move from the ATM strike, a **+6 vol-point** trigger and **2%** planned risk. It returned **−0.51%** on one holdout trade.

    Straddle expiry payoff per share = |terminal spot − ATM strike|
    Condor expiry payoff per share = sum of signed leg intrinsic values
    Condor maximum expiry loss = 100 × contracts × max wing width − net entry credit

The maximum expiry loss assumes disjoint call and put wings, with entry fees included in net entry credit. Liquidating before expiry can exceed the modeled bound after crossing spreads and paying fees. Seven of 44 benchmark and HV credit positions had an early-assignment warning. The test flags ITM calls with extrinsic value below an upcoming dividend and ITM puts with less than **$0.01** extrinsic value. It does not simulate assignment. European IV provides a benchmark for American-style SPY options.

## Statistical checks and missing evidence

    Daily return = day-end equity / previous day-end equity − 1
    Total return = product(1 + daily return) − 1
    Drawdown = current equity / running peak equity − 1
    Expectancy = mean(net P&L per trade)
    Annualized Sharpe = sqrt(252) × mean(daily net return) / sample_std(daily net return)

The risk audit uses **10,000** circular return paths, seed **20261119**, **1-, 2-, 4- and 8-session** blocks for eight-session risk, and **four-session** blocks for 95% full-window return intervals. B1 long-only’s eight-session median was **−0.02%**, its fifth percentile **−0.37%**, and its probability of a negative return **56.3%**. The short options window, overlapping outcomes and prior review of the E/F holdout limit statistical confidence.

Completing A requires point-in-time stock eligibility, delistings and intrabar history. Validating B/C execution requires historical SPY bid/ask and time-stamped fills. D requires exact news-scoring, contract selection, order and expiry rules, article publication and receipt timestamps, and intraday option bid/ask and size. Original intraday E/F require time-stamped chains and an untouched holdout after **2025-12-12**.

The local evidence includes the [variant results](six_strategy_verdict.csv), [reproduction comparisons](reproduction_comparisons.csv), [independent trade ledger](trade_ledger.csv), [fee grid](fee_audit.csv) and [input hash verification](input_verification_after_run.json).
