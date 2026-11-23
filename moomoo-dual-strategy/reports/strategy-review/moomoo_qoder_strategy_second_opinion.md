# Second Opinion: Strategy for the Moomoo × Qoder AI Paper Trading Agent Challenge

**Prepared:** 17 Nov 2026, the first trading session of the window.
**Scope:** Competition paper account only. Nothing here proposes or supports live-money trading.
**Evidence labels used throughout:**
- **[Doc]**: stated in an official rule, a paper or a platform document.
- **[Author]**: reported by a strategy's own authors; in-sample unless stated otherwise.
- **[Calc]**: my own arithmetic on published figures.
- **[Assume]**: a working assumption.
- **[Unverified]**: could not be confirmed.

---

## 1. Verdict

**Winner: cross-asset intraday momentum ("Noise-Area breakout") on SPY and QQQ (full weight) and TLT and GLD (half weight).** Decide only at :00 and :30 from 10:00 to 15:30 ET, trail exits at max(band, VWAP), and be flat by 15:55 ET. **Edge:** once price leaves its normal intraday range, dealer gamma hedging and late-informed flow keep it moving into the close, an effect published in the JFE across 60+ futures markets. **Why not ORB:** its 2.81 Sharpe assumes resting stops, 4x leverage and zero slippage. This account offers market and limit DAY orders, about 2x margin, and agents on ≥1-minute schedules. This strategy's published test already uses half-hour decisions and slippage, and has post-publication results.

**On your provisional ORB settings, in short:**
- The evidence supports ORB only in its published form:
  - a broad universe of "stocks in play" (top-20 relative volume across about 7,000 stocks, not mega-caps);
  - a stop at 10% of the *daily* ATR;
  - **an exit at the close with no profit target.**
- Your settings change three of these load-bearing choices:
  - the universe: highly liquid names with RVOL ≥ 2;
  - the exit: a 2R cap;
  - an added VWAP filter.
- None of those three changes has a documented test.
- If the trade-level win rate of about 17% that a replication user reported is right [Unverified], a 2R cap breaks even only if the win rate roughly doubles to at least 33.3% before costs.

**Honest expectation:** over 8 sessions, even a genuine Sharpe-1 to Sharpe-2 edge gives only about a 55–65% chance of finishing positive [Calc: 8-day Sharpe = annual Sharpe × √(8/252) ≈ 0.18–0.36]. The return component (25%) is mostly luck for any honest strategy. The other 75% of the score is won through design, automation and evidence, and the choice above is the one this execution stack can implement faithfully.

---

## 2. What the official sources say (and what remains open)

| Item | Finding | Status |
|---|---|---|
| Sessions | Nov 17, 18, 19, 20, 23, 24, 25 (full days) and **Nov 27 (closes 13:00 ET)**; Nov 26 is closed. That makes **7 full sessions plus 1 half session**. RTH is 22:30–05:00 SGT; the Nov 27 close is 02:00 SGT on Nov 28. | [Doc] NYSE calendar |
| Final valuation | Regular-market closing prices on the final competition day; after-hours trades that day are excluded. A weekly snapshot is taken Friday at 10 PM ET. | [Doc] handbook (model-summarised section) |
| Submission | 30 Nov, 23:59 SGT: a **Qoder-generated deck plus a runnable trading-agent package**. It must cover strategy, risk limits, the decision log, fills and results, and a demo of what worked, what failed and why. The top 10 present on 8 Dec. | [Doc] Luma + handbook |
| Margin | Longs: 50% initial / 40% maintenance. Shorts: 60% / 50%. Interest is charged daily after the close: 4.8% a year on margin and 6.0% a year on shorts. **That is about 2x buying power, not 4x.** | [Doc] handbook (model-summarised) |
| Limits | No more than 40% of assets in a single stock (breaches caused only by price moves are exempt). At most 100 positions and 200 executed orders a day (cancels don't count). No stocks under $1 or companies under $500M. **Whether the 40% cap applies to ETFs is not stated.** | [Doc] / ETF scope [Unverified] |
| Stock fees | $0.99 per order, plus $0.000166 per share sold (capped at $8.30). No commission. | [Doc] |
| Option fees | $0.65 per contract (premium above $0.10), $1.99 minimum per order, $0.30 per contract platform fee, plus regulatory and activity fees. ITM options are auto-exercised. | [Doc] |
| **Paper order types** | **Market and limit only. DAY validity only. No query of today's or historical deals (fills). No fee query.** Modify and cancel are supported. Extended hours work only on US margin paper accounts. | [Doc] Moomoo OpenAPI trade FAQ + `moomoo sim` CLI reference |
| Fill model | Partial fills happen when order quantity exceeds roughly 20× the market volume (exact wording uncertain). | [Doc], wording [Unverified] |
| Reset | No reset. Reset cards, revival features or equivalents mean disqualification. | [Doc] |
| Cloud Agents | Each task runs in its own container with no local OpenD. Moomoo MCP connects through a **Vault (MCP OAuth)**, and a session without the vault silently gets no Moomoo tools. Schedules support cron (5-field, **1-minute minimum**), interval and once, in an IANA time zone that **defaults to Asia/Shanghai** if not set. Each attempt **times out after 5 minutes by default**. Sessions are bounded turns, not daemons. Billing is by credits. | [Doc] handbook + docs.qoder.com |
| Credits | Free 1-month Pro plan (2,000 credits a month) plus 1,500 bonus credits. A typical complex tool-using turn costs about 20–50 credits. | [Doc] Luma + Qoder billing docs |
| Slack channel | The handbook says channel delivery "counts towards the 'automation' half of the usage score". | [Doc] |
| Risk-adjusted return | "Trading performance relative to risks taken." **No formula is given.** | [Unverified] |
| MCP tool list | The endpoint returns 401 without authentication, so the tool names and which order types the MCP exposes are **unknown until you list them in Qoder**. | [Unverified] |
| OAuth scope | The handbook pre-selects Market Data and Watchlists. A FAQ line, seen only in summarised text, reportedly says to grant Market Data only. That leaves open how cloud agents get trade permission. | Conflict [Unverified] |
| Briefing and kickoff slides | Both decks failed to load: one is JavaScript-only, the other exceeds 10 MB. **Their contents were not reviewed.** | Not read |

**Practical consequence:** any strategy that needs resting stop orders, sub-minute monitoring, or more than 2x gross exposure will be implemented differently from how it was tested. That fact drives the verdict.

---

## 3. Ranked comparison

The suitability score (1–10) is my judgement for *this* contest. It is not a backtested probability.

| # | Strategy | Net expectancy evidence | 8-session profit potential | Downside / tail | Feasibility on this stack | Judging strength | Score |
|---|---|---|---|---|---|---|---|
| **1** | **Noise-Area intraday momentum, 4-ETF basket** (winner) | SPY, 2007–Apr 2024: Sharpe **1.33** net of commission and $0.001/share slippage; MDD 25%; daily hit ratio 43%; trade hit ratio 37%; 1.8 trades a day [Author]. Sharpe **1.17** under the I-Star impact model [Author]. **Post-publication, May 2024–Aug 2025: +18%**, Sharpe ≈ 0.9 raw or ≈ 0.6 over cash [Calc on the author's monthly table]. Losing years: 2016 (−12.8%), 2017 (−6.9%), and a weak May–Aug 2025. Futures analogue: 33 markets, average Sharpe 0.60, diversified 1.65 [Author]. Underlying effect is peer-reviewed (Gao et al. 2018; Baltussen et al. 2021) | Low to moderate. Plausible range about ±1–2% of NAV; expected roughly +0.2–0.6% [Assume] | **Low**: intraday only, in the most liquid instruments. The main tail is a shock between half-hour checks | **High**: 12 decision ticks a day suits cron; market or limit orders suffice; about 120 cloud runs in total | **Strong**: one-line thesis, visual band chart, rules that can be shown wrong, few parameters | **8.5** |
| 2 | 5-min ORB on stocks in play, as published | 2016–2023: Sharpe **2.81**, IRR 41.6%, vol 14.8%, MDD 12%, worst day −1.61% [Author, in-sample]. Assumes **commission only ($0.0035/share), no slippage, 4x leverage**. Average +0.08R per trade when RVOL > 1, rising to +0.38R above 30x [Author]. QuantConnect replication: 2016 only, Sharpe 2.40, no costs. A forum user reports about a 17% trade win rate [Unverified]. No post-publication update found | Moderate to high (many bets a day) | Medium: single-name halts, squeezes, LULD pauses. Stops at 10% of ATR are only about 0.3–0.5% of price, so any monitoring lag turns −1R into −2R or −3R | **Low–medium**: no stop orders in paper, so it needs a local process watching every second plus a market-wide RVOL scan at 9:35 | Strong story, but judges can spot that the live version differs from the tested one | 6.5 |
| 3 | Your provisional ORB (liquid names, RVOL ≥ 2, VWAP confirm, 5-min ATR stop, 2R, 0.5% risk) | **Not established.** It drops the edge driver (letting winners run to the close) and the universe logic (raising the price filter above $50 "degraded performance" per a replication commenter [Unverified]) | Moderate | Medium | Same problems as #2 | Moderate: reads as hand-tuned | 4.5 |
| 4 | Regime-switching hybrid (momentum ↔ mean reversion by VIX/RSI) | No documented combined system. The switching rule would be fitted on a few regime episodes | Moderate | Medium | Medium | Good narrative, obvious overfit risk | 5.0 |
| 5 | Intraday mean reversion (RSI / VWAP fade) | Reversal profits net of costs are documented mostly at daily-to-weekly horizons and are cost-sensitive (de Groot–Huij–Zhou; Medhat–Schmeling). **No peer-reviewed net-of-cost intraday large-cap evidence found** | Moderate: high win rate, small wins | **High**: negative skew on trend days. The current regime is prone to trend days (VIX spike on 5 Nov) | High | Moderate | 4.5 |
| 6 | ETF / sector momentum rotation | Evidence sits at monthly horizons. 8 sessions give only 1–2 rebalances, so the edge has no room to show | Low | Medium: overnight gaps, 4.8–6% financing | High | Weak in this window | 4.0 |
| 7 | Pairs / stat-arb | Gatev–Goetzmann–Rouwenhorst: about 11% a year (1962–2002), then decaying from 0.86% to 0.37% a month (Do & Faff 2010). Convergence usually takes weeks | Low | Medium: divergence plus short costs | Medium | Moderate | 3.5 |
| 8 | Options, directional or volatility | No relevant net evidence for this window. Fees run about $0.97 per contract per side plus a $1.99 minimum. Spreads, IV crush and auto-exercise all apply, and historical options data via the MCP is unverified | High variance | **High** (premium decay or short-vol tails) | Medium–low | Weak: the FAQ says excessively risky strategies score lower | 3.0 |

**Excluded:**
- **15-, 30- and 60-minute ORB.** The same paper reports Sharpe 1.43, **0.21** (MDD 35%) and 0.40 [Author]. Such a steep drop with timeframe argues against treating ORB as robust to parameters.
- **ML signal filter and RL execution.** Neither can be validated in this window. RL execution adds nothing at a $25–40k ETF order size.
- **Last-30-minute market momentum (Gao et al.).** Not a separate entry: the winner already holds positions through the last half hour, which captures it.

---

## 4. Parameter sheet for the winner

All settings are **frozen before the first live trade**. Settings taken from the paper are [Author]; settings marked "heuristic" are mine and untuned.

| Parameter | Setting | Rationale / status |
|---|---|---|
| Universe | **SPY, QQQ** at full risk weight; **TLT, GLD** at half weight | SPY and Nasdaq are documented directly (futures analogues: Sharpe 1.32 / 1.23). Bonds and gold rest on analogue evidence (Baltussen; the authors' commodity futures), hence half weight. IWM is excluded (Russell analogue Sharpe 0.45). No leveraged ETFs |
| Session | RTH only. No pre-market, after-hours or overnight trading | Avoids gap risk and the 4.8% / 6% overnight financing |
| History | 30-minute RTH bars, **14-day lookback** | Paper default [Author]. The authors say 90 days and a volatility multiplier of 1.5 did better in-sample, which is exactly why the defaults are kept |
| Normal move | σ(HH:MM) = mean over the prior 14 days of \|Close(d, HH:MM) / Open(d, 9:30) − 1\| | [Author] formula. Prior days only, so no look-ahead |
| Bands | UB = max(Open_t, Close_t−1) × (1 + VM·σ); LB = min(Open_t, Close_t−1) × (1 − VM·σ); **VM = 1.0** | Gap-adjusted bands [Author] |
| VWAP | Cumulative RTH VWAP from 9:30, computed from 1- or 5-minute bars | [Author]: market-hours data only |
| Decision clock | **10:00, 10:30, …, 15:30 ET** (12 ticks). Cron `0,30 10-15 * * 1-5` with time zone **America/New_York** | [Author]: decisions only at HH:00 and HH:30. Set the time zone explicitly, because the default is Asia/Shanghai |
| Target position at each tick | Long if P > UB **and** P > VWAP; short if P < LB **and** P < VWAP; otherwise flat. Act on the change from the current position | Stateless form of the paper's rule (entry beyond the band, trailing stop at max/min(band, VWAP)). **Documented deviation:** it skips entries where price is beyond the band but on the wrong side of VWAP. Test both forms in T1 |
| Reversal | Crossing to the opposite band flips the position at the next tick | [Author] |
| Flatten | 15:55 ET with marketable limit orders, then market orders. A watchdog checks at 15:57 and 15:59 | Small departure from the paper's 16:00 exit, to absorb agent latency |
| Half day (Nov 27) | Ticks 10:00–12:30; flatten at 12:55; watchdog at 12:58 | The 13:00 close is also the final valuation |
| Base sizing | notional_i = NAV × m × min(40%, RB_i / σ_i,14d) | Scaled-down version of the paper's volatility targeting. σ_i,14d = standard deviation of the last 14 daily returns [Author definition] |
| Risk budget RB_i | SPY 0.40%, QQQ 0.40%, TLT 0.20%, GLD 0.20% of NAV per 1-σ daily move | Heuristic. Targets roughly 0.5% NAV daily strategy volatility |
| Regime tilt m | **m = 0.5** if SPY RSI(5) on daily closes > 80, **or** VIX < 13 (fallback: SPY σ14 × √252 < 13%). Otherwise m = 1.0 | Heuristic, but its direction is supported: the authors find returns fall as 5-day RSI rises (p = 0.001) and Sharpe rises with VIX [Author]. **Do not filter shorts by VIX or trend**: the authors found both filters hurt [Author] |
| Orders | Marketable limit at ask × 1.0005 (buys) or bid × 0.9995 (sells). If not filled within 45 s, cancel and replace once, then use a market order. DAY only | Paper supports market and limit only [Doc]; the marketable limit caps slippage |
| Trade cap | At most 6 entries per instrument per day; **no more than 40 orders a day in total** (rule limit is 200) | The paper averages 1.8 trades a day per instrument [Author] |
| Stand aside (no new entries; exits still allowed) | Last bar more than 2 minutes old; 14-day history incomplete; spread > 5 bps (SPY/QQQ) or > 10 bps (TLT/GLD); halt or LULD pause; market-wide circuit breaker; tick processed more than 5 minutes late; account check fails; NAV differs from the internal ledger by more than 0.25%; daily or drawdown lock active | Fail closed |
| Events | **Do not** stand aside for macro or earnings days (e.g. the NVDA report, 17 Nov per one source [Unverified]) | Intraday momentum is *stronger* on news and high-volume days (Gao et al.). Overnight gaps are already absorbed by the gap-adjusted bands |
| Overnight | Never | — |

**Shadow challenger (optional, not traded).** Log published-spec ORB-SIP signals daily (top-20 RVOL, 10% daily ATR stop, exit at close) with hypothetical fills from 1-minute bars. This makes "we compared it with ORB" a measured result rather than an opinion.

---

## 5. Risk manager specification

The risk gate is deterministic code that fails closed. A separate LLM risk auditor can only **veto**; it never creates orders. Every rejection is logged with a reason code.

| Gate | Rule (US$100,000 start) |
|---|---|
| G0: Account | `env == SIMULATE`. `account_id` equals the **pinned competition paper account ID**, and the account name contains "Moomoo x Qoder AI Paper Trading Agent Challenge". `market == US`. Only allowlisted commands or tools may run (e.g. `moomoo sim order …`); **any live-order tool or `moomoo order …` is rejected outright.** All checks run before every order, not once per session |
| G1: Instrument | Symbol in {SPY, QQQ, TLT, GLD}, not halted, spread within limits (see Section 4) |
| G2: Time | Entries only at scheduled ticks. Tick lag must be ≤ 5 minutes. No extended hours. Flatten at 15:55 (12:55 on Nov 27) |
| G3: Exposure | Per ETF ≤ 40% of NAV (cap applied to ETFs as the conservative reading). **SPY + QQQ on the same side ≤ 60% of NAV**, scaled down pro rata. Gross ≤ 120% of NAV. Net ≤ 80%. Margin used ≤ 50% of buying power |
| G4: Loss estimate | est_loss_i = notional_i × (\|P − stop_i\| / P + 2·σ_i,14d / √13). Each est_loss_i ≤ **0.50% of NAV ($500)**. Sum of open est_loss ≤ **1.0% of NAV ($1,000)**. If exceeded, scale down; never skip the check |
| G5: Daily loss | Mark-to-market P&L for the day ≤ **−1.25% of NAV (−$1,250)**: flatten everything and lock new entries until the next session |
| G6: Drawdown ladder | Measured from the high-water NAV. **−2.0% (−$2,000):** all risk budgets × 0.5. **−3.5% (−$3,500): kill switch.** Flatten, disable the executor and open an incident report. Re-enable only next session at 0.5×, after auditor review and a logged human sign-off. **−5.0% (−$5,000):** stop trading for the rest of the contest; signals continue in shadow mode only |
| G7: Orders | One working order per symbol. Idempotency key = date + tick + symbol. Cancel stale orders after 45 s. At most 40 orders a day |
| G8: State | Paper has **no fills query**, so reconcile through order status and positions at every tick. A mismatch blocks entries |
| G9: Overnight | Watchdog at 15:57 and 15:59. If any position remains at 16:01, open an incident and close with an extended-hours limit order if available. Never carry a position knowingly |
| G10: Change control | Parameters live in a versioned config, and its hash is logged with every decision. A change takes effect **next session only**, with a written reason. No intraday tuning |

**Sizing example.** Assume illustrative volatilities: SPY σ14 = 1.1%, QQQ 1.5%, TLT 0.8%, GLD 1.6%, with m = 1.

| ETF | Raw notional | Cluster cap applied | Est. loss (stop distance + 2σ₃₀ₘ) |
|---|---|---|---|
| SPY | 0.40 / 1.1 = 36.4% | 34.6% → $34.6k | (0.25% + 0.61%) × 34.6k ≈ **$298** |
| QQQ | 0.40 / 1.5 = 26.7% | 25.4% → $25.4k | (0.30% + 0.83%) × 25.4k ≈ **$287** |
| TLT | 0.20 / 0.8 = 25.0% | — → $25.0k | (0.15% + 0.44%) × 25.0k ≈ **$148** |
| GLD | 0.20 / 1.6 = 12.5% | — → $12.5k | (0.30% + 0.89%) × 12.5k ≈ **$149** |

- Gross if all four are active: 97.5%.
- Total estimated loss: $882 (0.88% of NAV), which passes G4.
- Round-trip fees for SPY: 2 × $0.99 plus about $0.01 activity fee ≈ $2, or about 0.6 bp of notional.

**Stops do not guarantee a maximum loss.** Exits are checked only at half-hour ticks and executed with agent latency. A fast market move between ticks can produce a larger loss than estimated, and so can a halt, a rejected order or a failed schedule run. G4 sizes for a 2σ half-hour adverse move, and G5/G6 bound the damage to the account.

---

## 6. Evidence and validation plan

### Expectancy definitions (use the same code in backtest and live)

**Per trade j:**
- Net P&L: PnL_j = side × q × (exit − entry) − fees_j, where fees_j = 0.99 × orders + 0.000166 × shares sold (capped at $8.30).
- R-multiple: R_j = PnL_j / est_loss_j, using est_loss from G4.

**Realised expectancy:**
- E = p·W̄ − (1 − p)·L̄, which equals the mean of PnL_j.
- Profit factor = ΣW / Σ|L|.
- Payoff ratio = W̄ / L̄.
- Break-even win rate p* = L̄ / (W̄ + L̄).

**Prospective expectancy:** the mean net R from the out-of-sample backtest (T1) after adding T3's lag and cost stress. Get confidence intervals with a **day-block bootstrap**, because trades within a day are correlated.

**Daily metrics:**
- Sharpe = mean(r_d) / sd(r_d) × √252, plus Sortino, return / MaxDD, and exposure-adjusted return.
- Report trade-level win rate separately from the share of days that were profitable. The paper's 43% "hit ratio" is a *daily* figure; its trade hit ratio is 37%.

### How many trades make a number meaningful

**Win rate:** n ≈ 1.96² · p(1 − p) / h², where h is the margin of error you want.
- With p ≈ 0.37, **±10 pp needs about 90 trades**.
- **±5 pp needs about 360 trades**.

**Expectancy:** n ≈ (1.96 · σ_R / μ_R)².
- With μ ≈ 0.1R and σ ≈ 1.5R [Assume], that is **about 860 trades**.

**Implication:** this contest will produce roughly 30–50 trades. That is enough to catch implementation failures, but **not enough to prove or disprove the edge.** Edge validation has to come from T1–T3 on historical data.

### Falsifiable tests: run T1–T3 before any full-size trading (Nov 17–18)

| Test | Method and minimum data | Abandon / modify rule |
|---|---|---|
| **T1: Out-of-sample replication** | Run the frozen rules (14-day lookback, VM 1.0, both target-rule variants) on **30-minute RTH bars for SPY, QQQ, TLT and GLD from 1 Sep 2025 to 16 Nov 2026**. That period comes after the authors' last published update, so it is true OOS. Fees as in Section 2, plus 2 bps per side slippage. **Benchmark:** SPY buy-and-hold at the same average gross exposure | If the basket's net Sharpe ≤ 0, or MDD exceeds 8% at contest sizing: **do not trade at full size.** Run at 25% size as a demonstration, and promote ORB-SIP only if it passes the same test with slippage |
| **T2: Does direction carry information?** | Same OOS period: keep timing and stops identical but randomise direction, 1,000 permutations | If the real strategy does not beat the 80th percentile of the permutations, the profit comes from volatility timing rather than direction. Modify or demote |
| **T3: Cost and lag stress** | Re-run T1 with a 3-minute execution delay and 3 bps per side | If Sharpe falls below half of T1's: move execution to a local `moomoo sim` process, or reduce to hourly ticks |
| **T4: Live fidelity** (during the contest) | At every tick, recompute the decision from the logged inputs and compare it with what was executed. Measure fill price against the price at the tick | If more than 5% of ticks mismatch, or mean adverse fill exceeds 3 bps: halt entries and fix before the next session |
| **T5: Live invalidation** | Daily P&L, drawdown, and whipsaw count (stop-outs within one tick of entry) | G5/G6 trigger automatically. If ≥ 3 consecutive sessions each have ≥ 3 whipsaws per instrument, run the next session at m = 0.5. **No parameter re-fitting** |

**Look-ahead hygiene:**
- Bands use only prior-day data.
- VWAP and price at tick t use only bars closed by t.
- Fills are simulated at the first price after t + lag, never at the tick close.
- Test a scenario with 50% partial fills, even though it is unlikely for ETFs at this size.

---

## 7. Agent architecture (Qoder Desktop + Cloud Agents)

```
                 ┌────────── Forward Schedules (America/New_York) ──────────┐
 08:45 premarket  │ 0,30 10-15 ticks │ 15:55 flatten │ 15:57/59 watchdog │ 16:10 EOD │
                 └─────────────────────────────┬────────────────────────────┘
                                   Coordinator (Cloud Agent)
       ┌───────────────┬──────────────┬────────┴─────┬──────────────────┬──────────────┐
  Research agent   Signal engine   Risk gate      Risk auditor       Executor        Reporter
  (MD-only vault)  (deterministic  (deterministic (LLM, read-only,   (only agent     (EOD metrics,
  σ14, bands, RSI, Python skill)   Python skill)  veto-only)         with trade      dashboard JSON,
  VIX, calendar                                                      scope, pinned   Slack, Git commit)
                                                                     paper account)
                     All decisions/rejections/orders → append-only JSONL in GitHub
```

- **Deterministic core, LLM orchestration.**
  - Signals and risk run as versioned Python skills: `noise-area-signal`, `risk-gate`, `paper-executor`, `eod-reporter`, `orb-shadow`.
  - Each is packaged per the handbook: one zip per skill, a top-level folder named after the SKILL.md `name`, under 50 MB, and the version recorded in the frontmatter.
  - LLMs fetch data, call skills, explain decisions and write reports. **LLMs never compute size or override a gate.**
- **Two vaults.**
  - `moomoo-md` (Market Data only) goes to the research, signal and auditor agents.
  - Trade scope goes only to the executor.
  - **Day-1 check:** list the MCP tools and confirm the trade tools can reach the *competition paper* account and nothing else.
  - If that can't be confirmed, run the executor locally through a Qoder Desktop automation using `moomoo sim order …`, which is a paper-only command group. Cloud agents keep the other roles.
- **Target-position ticks.**
  - Each tick reconciles actual positions to target positions, so a missed or timed-out run is fixed at the next tick.
  - Each run must finish well inside the 5-minute default timeout.
  - Sizing data such as σ14 and band coefficients is precomputed once pre-market, so each tick needs only one snapshot or bar call.
- **Budget.**
  - About 15 runs a day × 8 days ≈ 120 runs. At 10–20 credits each, that is about 1,200–2,400 credits, inside the roughly 3,500 available.
  - Add a credit monitor to the EOD report.
- **Holiday handling.** Cron won't skip Thanksgiving, so pause the schedule for Nov 26. Use once-triggers for the 12:55 and 12:58 runs on Nov 27.
- **Logs.**
  - One JSONL line per tick per symbol, holding: inputs hash, bands, VWAP, target, gate results with reason codes, auditor verdict, order IDs, reconciled position, NAV and config hash.
  - Rejections are logged too.
  - Commit to GitHub at EOD and keep the history (no force-pushes).
- **Dashboard.**
  - A local page following handbook §7: equity curve against SPY, intraday band chart per ETF, exposure gauges against the caps, gate-rejection counts, drawdown ladder state, credit burn.
  - Call the Cloud Agents API only from server-side code, with the token in a gitignored `.env`.

---

## 8. Failure scenarios and mitigations

| Scenario | Effect | Mitigation |
|---|---|---|
| Choppy, low-volume Thanksgiving week | Repeated small whipsaw losses; the 37% trade hit ratio feels worse over a short window | Regime tilt; daily lock; T5 rule; accept that this is the strategy's known weak regime (2016–17, May–Aug 2025) |
| Agent run fails or times out | Missed entry or exit | Stateless target reconciliation; watchdog flattens; retries capped at one |
| MCP trades the wrong account, or a live one | Trades don't count, or real money is at risk | G0 pinned ID plus env assertion before every order; tool allowlist; Market Data-only vault for non-executors; no live trade permission granted |
| No fills endpoint in paper | Ledger drifts from reality | Reconcile from order status and positions every tick; mismatch blocks entries |
| Fast move between ticks | Loss larger than estimated | Size to a 2σ half-hour move (G4); daily and drawdown locks; optional local 60-second catastrophe check that only flattens |
| Mid-contest tinkering | Overfitting; loses credibility with judges | G10 freeze; changes next session only, logged |
| 40% cap enforced differently on ETFs | Orders rejected or a rule breach | Already capped at 40% per ETF and 60% for the SPY+QQQ cluster |
| Credits run out | Schedules stop | Credit monitor; local Desktop automation fallback. Buying credits is your decision |
| Final-day timing | A position caught at the 13:00 close on Nov 27 | Half-day schedule; watchdog at 12:58 |
| Wrong time zone | Ticks fire at Shanghai times | Set America/New_York explicitly on every schedule; check run history on day 1 |

---

## 9. Judging strategy

| Weight | Official expectation [Doc] | How to show it (recommendations) |
|---|---|---|
| **30% Strategy & risk** | A clear thesis and edge, conditions that invalidate it, position limits, drawdown responses and kill switches. Excessive risk scores lower | A one-slide thesis with mechanism and citations. The evidence table from Section 3, showing in-sample and post-publication results separately. T1–T3 results with benchmark and permutation charts. Gates G0–G10 shown as code, plus a **kill-switch demo in a separate practice paper account** (the handbook advises practising outside the competition account). The ORB shadow comparison |
| **30% Qoder usage** | Multi-agent orchestration, automation, dashboards, reports, reusable skills. Slack counts toward automation | Coordinator plus role agents on Forward Schedules; run-history screenshots; vault separation; versioned skills in the Skill Center; Slack alerts for fills, gate rejections and kill events; AGENTS.md; the GitHub commit trail |
| **25% Risk-adjusted return** | "Performance relative to risks taken"; no formula given | Report several metrics, since the formula is unknown: Sharpe, Sortino, return/MaxDD, max intraday drawdown, exposure-adjusted return. Compare against SPY buy-and-hold and the ORB shadow over the same sessions. State plainly how much of an 8-day result is luck |
| **15% Reusability & docs** | Qoder-generated deck (required), runnable package, strategy explanation, risk limits, decision log, fills and results, demo of what worked and what failed | `README` quick start; `config/` with hash; `tests/` (signal unit tests against hand-computed bands, gate tests including a live-account rejection); skills zip; JSONL logs; an honest "what failed" section |

**Timeline:**
- **Nov 17 (today):** list the MCP tools; pin the account ID; confirm order types in the competition account; run T1–T3; build the signal and risk skills. No competition-account trades until the pipeline passes a dry run in the practice account.
- **Nov 18:** go live at 0.5× size.
- **Nov 19 onward:** move to 1.0× once T4 is clean.
- **Nov 27:** half-day schedule.
- **Nov 28–30:** deck and package. Submit early.

---

## 10. Where this differs from `moomoo_qoder_strategy_review.md`

I read only that file's header and verdict lines, to keep this review independent.

- **It recommends a regime-gated stock ORB. I recommend against trading ORB here.** The disagreement isn't about ORB's historical evidence. It is that this account cannot execute ORB as it was tested:
  - no stop orders;
  - about 2x margin;
  - agents on schedules of one minute or longer;
  - an edge of only 0.08R or more per trade, where R is about 0.3–0.5% of price, which friction can plausibly absorb.
- **On its claim that the SPY strategy "fell from 1.33 to 0.39 out-of-sample":** I could not find a source for that figure. The authors' own post-publication monthly table (May 2024–Aug 2025) gives about +18% and a Sharpe of roughly 0.9 raw or 0.6 over cash [Calc]. That is weaker than in-sample but positive. Either way, T1 tests the period after Aug 2025 directly.
- **We agree on:** a 2R cap is wrong for ORB, daily ATR is better than 5-minute ATR, size should be smaller than the paper's, and the risk gate should be independent.

---

## 11. Sources and uncertainty

**Official / platform**
- [Participant handbook, FAQ, T&Cs](https://moomoo-qoder-challenge-rgkelnyuzbm.qoder.website/). The setup sections were read verbatim. Rules, fees, limits and judging came through as summarised extraction, so re-check exact figures on the page.
- [Luma event and submission page](https://luma.com/oezyv35j): dates, deliverables, judging weights.
- [Moomoo Agent Hub](https://github.com/MoomooOpen/moomoo-agent-hub), including the `moomoo-sim-trading` and `moomoo-quote` references: sim commands, order types, K-line intervals.
- [Moomoo OpenAPI trade FAQ](https://openapi.futunn.com/futu-api-doc/en/qa/trade.html): paper supports market and limit orders only, DAY only, with no deal queries.
- Qoder docs: [Cloud Agents overview](https://docs.qoder.com/cloud-agents/overview), [schedules API](https://docs.qoder.com/cloud-agents/api/forward/schedules/create), [billing](https://docs.qoder.com/cloud-agents/billing), [pricing](https://docs.qoder.com/account/pricing).
- [NYSE holidays and hours](https://www.nyse.com/markets/hours-calendars).
- **Not readable:**
  - [strategy briefing](https://moomoo-strategy-briefing-j692pppjg7l.qoder.website/) (JavaScript only);
  - [kickoff slides](https://ngynckpj.qwenwork.page/) (over 10 MB);
  - [Moomoo MCP](https://mcp.moomoo.com/mcp) (returns 401).
  - No video recordings were opened.

**Research**
- Zarattini, Barbon, Aziz (2024), [A Profitable Day Trading Strategy for the U.S. Equity Market](https://concretumgroup.com/a-profitable-day-trading-strategy-for-the-u-s-equity-market/) (SSRN 4729284). Read in full: Tables 1–3, rules, costs.
- Zarattini, Aziz, Barbon (2024, revised Sep 2025), [Beat the Market: An Effective Intraday Momentum Strategy for SPY](https://concretumgroup.com/beat-the-market-an-effective-intraday-momentum-strategy-for-sp500-etf-spy/) (SSRN 4824172). Read in full: Tables 1–6 and FAQ Q12–Q24, including the post-publication table.
- [QuantConnect ORB replication thread](https://www.quantconnect.com/forum/discussion/18444/opening-range-breakout-for-stocks-in-play/): 2016 only, no costs; community comments are unverified.
- Gao, Han, Li, Zhou (2018), [Market Intraday Momentum, JFE 129](https://econpapers.repec.org/RePEc:eee:jfinec:v:129:y:2018:i:2:p:394-414).
- Baltussen, Da, Lammers, Martens (2021), [Hedging Demand and Market Intraday Momentum, JFE 142](https://econpapers.repec.org/RePEc:eee:jfinec:v:142:y:2021:i:1:p:377-403).
- Barber, Lee, Liu, Odean (2014), [The Cross-Section of Speculator Skill, JFM 18](https://econpapers.repec.org/RePEc:eee:finmar:v:18:y:2014:i:c:p:1-24): fewer than 1% of day traders are predictably profitable.
- Do & Faff (2010), [Does Simple Pairs Trading Still Work?, FAJ](https://econpapers.repec.org/RePEc:taf:ufajxx:v:66:y:2010:i:4:p:83-95).
- de Groot, Huij, Zhou, [Another Look at Trading Costs and Short-Term Reversal Profits](https://www.erim.eur.nl/research/events/detail/1992/).
- Market context (secondary sources; verify on live data): [Cboe, week of 11/9/2026](https://www.cboe.com/insights/posts/week-of-11-9-2026-no-contagion-yet-equity-vol-spikes-macro-vol-fades) and [weekly recap, 2–6 Nov 2026](https://tapeboard.com/blog/weekly-recap-2026-11-06). VIX went from 16.6 to 28.2 on 5 Nov and was about 19.9 by 9 Nov; the S&P 500 closed at 7,561.67 on 13 Nov.

**Key unknowns, to resolve on day 1:**
1. The MCP tool list, and whether its trade tools reach the competition paper account.
2. Whether the 40% cap applies to ETFs.
3. The risk-adjusted-return formula.
4. Whether short-selling ETFs works in the competition account.
5. Which OAuth scope the FAQ actually requires.
6. Quotas on historical K-line data (T1 needs about 300 sessions of 30-minute bars).
7. The exact NVDA report date.

**Caveat on the evidence:** every strategy performance figure here is author-reported or a backtest. **No strategy can guarantee a win or a positive return over 8 sessions.**
