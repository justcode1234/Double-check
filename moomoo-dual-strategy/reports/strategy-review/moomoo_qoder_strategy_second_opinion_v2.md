# Second Opinion v2: Revised After Review

**Prepared:** 18 Nov 2026.
**Supersedes:** the recommendations in `moomoo_qoder_strategy_second_opinion.md` (v1). v1 is left unchanged because other documents cite it.
**Responds to:** `claude_moomoo_strategy_independent_review.md`. It also draws on `moomoo_qoder_strategy_validation_response.md` (Qoder).
**Scope:** The competition paper account only. Nothing here authorises live-money trading.
**Labels:** [Doc] official or primary source · [Author] reported by the strategy's own authors · [Indep] independent replication · [Calc] my own arithmetic · [Design] a risk-design choice I made, not a validated parameter.

---

## 1. Revised verdict

**Primary strategy: Noise-Area intraday momentum on SPY only.**
- It uses the published rules.
- Sizing is static: 1.0× NAV, or 0.4× if the 40% cap applies to ETFs.
- It decides only at :00 and :30 from 10:00 to 15:30 ET and holds nothing overnight.
- Risk overlays: a local sentinel that only ever closes positions, a drawdown state machine, and a daily lock.

**Trading at full size depends on passing tests A1, A2 and A4 (Section 6).**
- **QQQ** can be added only if the A3 ablation shows it helps in both out-of-sample windows.
- **TLT, GLD** and Qoder's published-baseline ORB run as logged **shadow** strategies: they are not funded.
- **If A2 fails,** the account runs at demonstration size. Its job is then to show the pipeline and risk controls, not to make a return.

**What changed from v1, and why:**
- **The out-of-sample evidence is weaker than v1 said.** An independent replication finds Sharpe **0.39** from May 2024 to Mar 2026, against **0.91** for buying and holding SPY.
- **The edge now rests on in-sample evidence only.** The most recent months available were negative. "Clear edge" therefore becomes "plausible but unproven in the recent period."
- **The execution argument still holds.** It is why this strategy stays ahead of stock ORB, which has no out-of-sample evidence at all.
- **The other review's critiques are accepted.**
  - The ETF basket is narrowed to SPY.
  - The drawdown restart logic is fixed.
  - The forecast return is removed.
  - Protection between half-hour ticks is added.
- **Untested heuristics are dropped:** the RSI/VIX regime tilt and the per-ETF volatility budgets.

**Honest expectation.** How much the result can swing is measurable; whether it is positive is not. At static 1×, the strategy's documented volatility is about 7.6% a year [Author]. Over 7–8 sessions that is a 1-σ range of about **±1.3% of NAV** [Calc]. If the true Sharpe lies between the out-of-sample 0.39 and the in-sample 1.23, the chance of finishing positive is about **53–59%** [Calc: Φ(SR·√(8/252))]. That is a conditional illustration, not a forecast. Test A2 replaces it with a bootstrap distribution.

---

## 2. Reconciling the out-of-sample evidence (the critique's top issue)

The 0.39 figure comes from [PazSheimy/spy-intraday-momentum-oos](https://github.com/PazSheimy/spy-intraday-momentum-oos) (S. Paz Serpa, June 2026). The replication's setup:
- 1-minute IQFeed SPY data.
- The published Noise-Area rules: 14-day lookback, gap-adjusted bands, VM = 1.0, decisions every 30 minutes, entry one bar after the signal.
- Sizing at 2% vol target with a 4× cap.
- Costs of $0.0035 + $0.001 per share and a $0.35 minimum per order.
- **No risk-free rate subtracted**; Sharpe annualised with √252 [Indep].

| Source | Period | Result |
|---|---|---|
| Authors, paper FAQ Q24 (dynamic sizing) | May 2024–Aug 2025 | About **+18%**; Sharpe ≈ 0.9 raw [Author table, Calc] |
| Independent replication, in-sample | Jan 2015–Apr 2024 | Sharpe **1.34** (paper: 1.33), MDD 26.2% [Indep]. **This validates the replication's code** |
| Independent replication, out-of-sample | May 2024–Mar 2026 (480 days) | **+9.4%** total, 4.8% a year, vol 14.7%, **Sharpe 0.39**, MDD 18.5%. SPY buy-and-hold over the same period: +29.5%, Sharpe 0.91 [Indep] |
| Overlap check | 2024 calendar year | Authors 32.2% vs replication 30.1%. Replication May–Dec 2024 ≈ +16.0% vs authors +16.9% [Calc] |
| By year, replication | 2025: **−2.7%** (Sharpe −0.10). 2026 Jan–Mar: **−3.1%** (SPY −4.6%) | [Indep] |
| **Implied gap** | **Sep 2025–Mar 2026** | About **−7%** [Calc; indicative only, mixes two implementations] |

**What follows:**
1. **The two sources don't conflict.** They agree where they overlap. The 0.39 reflects seven extra months (Sep 2025–Mar 2026) that were poor for the strategy. v1 was wrong to set the 0.39 aside.
2. **The recent data neither confirm nor rule out the edge.**
   - The out-of-sample mean daily return is not significantly above zero (t = 0.54, p = 0.59) [Indep].
   - The fall from in-sample is not significant either. The replication reports p < 0.001, but that test appears to use the *daily* observation count with an *annualised* Sharpe.
   - Lo's (2002) standard error, SE ≈ √((1 + SR²/2)/T_years), gives z = (1.34 − 0.39) / √(0.205 + 0.565) ≈ **1.08, p ≈ 0.28** [Calc].
   - The cross-check above is my arithmetic from the replication's published figures. I have not re-run its code.
3. **Neither study covers Apr–Nov 2026.** That untouched window is what test A2 is for.
4. **ORB on stocks in play has no out-of-sample evidence at all** (2016–2023 in-sample only, commission-only costs). The SPY strategy is weakly supported; ORB's recent performance is unknown, and it has the larger execution gap on this stack. The ranking holds, but by a smaller margin.

**Score revisions** (subjective suitability scores, not probabilities):

| Strategy | Score change | Reason |
|---|---|---|
| Noise-Area | 8.5 → **7.0** | Recent out-of-sample is weak |
| ORB on stocks in play | 6.5 → **6.0** | Qoder's own validation confirms the MCP order tool supports `order_type` 1 or 3 only (Limit, Market) and withdraws its 5-second stop claim |
| Other candidates | Unchanged | — |

---

## 3. Response to each critique point

| Critique | Response | Change in v2 |
|---|---|---|
| **Critical:** out-of-sample not reconciled | **Accept** | Section 2: source found, overlap checked, significance re-derived. The evidence label is downgraded |
| **Critical:** no protection between 30-minute checks | **Accept** | A local, deterministic **sentinel** that can only close positions, plus a cloud watchdog. Both send heartbeats and both fail closed. Remaining risk stated (Section 5) |
| **High:** four-ETF basket not validated | **Accept** | SPY only. QQQ only through the A3 ablation, at no extra exposure. TLT and GLD are shadow only |
| **High:** drawdown restart re-triggers | **Accept (a real bug in v1)** | Explicit state machine with a re-arm floor and a terminal stop (Section 5.2) |
| **High:** +0.2–0.6% forecast is an assumption | **Accept** | Removed. Replaced with a block bootstrap of 8-session returns from A2 |
| **High:** stateless signal ≠ published rule | **Partly accept** | The independent replication uses exactly this combined condition (long if P > UB and P > VWAP; short if P < LB and P < VWAP; flat otherwise) and reproduces the paper's in-sample Sharpe (1.34 vs 1.33). It stays primary because it has independent evidence. The literal stateful wording runs as an A3 ablation |
| **Medium:** agent stack assumed to work | **Accept** | A4 lifecycle tests in a **separate practice paper account** before any competition order |
| **Medium:** thresholds are heuristic | **Accept** | Dropped: RSI(5)/VIX tilt and per-ETF volatility budgets. Sizing reverts to the paper's documented **static 100% row** (Sharpe 1.24, vol 7.7%, MDD 12%, 2007–Apr 2024 [Author]). The remaining limits are labelled [Design] and tested on synthetic equity paths, not fitted |

**Corrections that also apply to the Qoder documents:**
- **The ORB paper's 48.4% "Hit Ratio" is a daily figure.** Qoder calls it undefined, but in Tables 1–3 the same column gives **54.9% for S&P 500 buy-and-hold**. A buy-and-hold position has no trades, so the column can only mean the share of positive days [inference from the table]. v1's argument built on a 17% trade win rate is also withdrawn.
- **The window has 8 sessions, not about 9.** They are Nov 17, 18, 19, 20, 23, 24 and 25, plus Nov 27 (closes 13:00 ET) [Doc: NYSE].

---

## 4. Parameter sheet (frozen before the first competition order)

| Parameter | Setting | Status |
|---|---|---|
| Instrument | **SPY** | [Author] direct evidence |
| Bands | σ(HH:MM) = mean over the prior 14 days of \|Close(d, HH:MM) / Open(d, 9:30) − 1\|. UB = max(Open_t, Close_t−1)·(1 + σ). LB = min(Open_t, Close_t−1)·(1 − σ) | [Author] / [Indep] same form |
| VWAP | Cumulative from 9:30, RTH only, typical price × volume | [Author] / [Indep] |
| Decision ticks | 10:00, 10:30, …, 15:30 ET. Cron `0,30 10-15 * * 1-5`, time zone **America/New_York** (the default is Asia/Shanghai) | [Author] |
| Signal | Target = +1 if P > UB and P > VWAP; −1 if P < LB and P < VWAP; otherwise 0 | [Indep] form; stateful ablation in A3 |
| Execution | Reconcile actual position to the target. Marketable limit (±0.05%). If unfilled after 45 s, cancel and replace once, then market. DAY only. **After any timeout, query today's orders before resubmitting** (the order tool is not idempotent [Doc: Qoder MCP schema notes]) | [Design] |
| End of day | Flatten at 15:55 ET; watchdog at 15:57 and 15:59. On Nov 27: last tick 12:30, flatten 12:55, watchdog 12:58. No ticks on Nov 26 (pause the schedule) | [Design]; the paper exits at 16:00, so 15:55 vs 16:00 is an A3 ablation |
| Sizing | Notional = NAV × L × m. **L = 1.0** (paper's static 100% row), or **0.4** if organisers confirm the 40% cap covers ETFs. m comes from the state machine (1.0 or 0.5) | [Author] row; m is [Design] |
| QQQ (only if promoted by A3) | SPY and QQQ split L 50/50. Combined same-side exposure ≤ L. It adds diversification across instruments, not extra exposure | [Design] |
| Overnight / extended hours | Never | [Design] |
| Stand aside (no entries; exits allowed) | Expected bar missing or wrongly stamped; last quote > 60 s old; spread > 5 bps; halt or LULD; market-wide circuit breaker; tick processed > 5 minutes late; sentinel heartbeat missing; any lock active | [Design]. Staleness is measured against the *expected* bar close, per Qoder's P9 fix |
| Change control | Config hash logged with every decision. Changes take effect next session only, with a written reason | [Design] |

---

## 5. Risk controls

### 5.1 Two independent monitors

**Local sentinel (Qoder Desktop, deterministic Python, not an LLM)**
- **What it can do:** cancel orders and flatten positions. It can never open or add to a position.
- **Polling:** positions, SPY quote and NAV every 15–30 seconds. Set the interval after A4 measures rate limits.
- **It flattens when any of these occur:**
  1. A state-machine HALT or STOP threshold (Section 5.2).
  2. The daily lock: mark-to-market P&L ≤ **−1.25% of session-open NAV**.
  3. A single position's unrealised loss reaches **1.0% of NAV**. At 1× SPY that is about a 3σ half-hour move, so this is a catastrophe cap, not the strategy's exit.
  4. The coordinator's heartbeat is more than 35 minutes old while a position is open.
  5. The time is 15:57 ET (12:57 on Nov 27) and a position is still open.
  6. Its own quotes have been stale for more than 2 minutes with a position open. It flattens at the first fresh quote and raises an alert.
- **Afterwards:** it writes a lock flag that the coordinator must read before acting, and it alerts Slack.

**Cloud watchdog (separate Cloud Agent schedule)**
- Runs at 15:57 and 15:59 (12:58 on Nov 27).
- Checks the sentinel's heartbeat at every tick, and blocks new entries if the heartbeat is missing.
- This covers a failure of the local machine.

**Remaining risk, stated plainly**
- If the local machine and a cloud run fail at the same time, a position can go unmanaged until the next successful run.
- Paper fills come from Moomoo's simulator. The paper accounts offer no stop orders [Doc].
- At 1× sizing, the paper's worst single day was **−4.8%** [Author, Table A1, 2007–Apr 2024].
- **No mechanism here guarantees a maximum loss.**

### 5.2 Drawdown state machine (replaces v1's ladder)

**How NAV and the high-water mark are measured**
- NAV is mark-to-market, including unrealised P&L, as sampled by the sentinel.
- The high-water mark (HWM) is the highest sampled NAV.
- The **contest floor** is the tighter of 0.95 × HWM and $95,000.

| State | Size m | New entries | Transition |
|---|---|---|---|
| **ACTIVE** | 1.0 | Yes | → REDUCED when NAV ≤ 0.98·HWM |
| **REDUCED** | 0.5 | Yes | → ACTIVE when NAV ≥ 0.99·HWM (hysteresis); → HALTED when NAV ≤ 0.965·HWM |
| **HALTED** | 0 (flatten) | No | Also entered on a sentinel catastrophe, a reconciliation failure or an account-check failure. → REVIEWED once a post-mortem, the auditor's verdict and a human sign-off are logged, and not before the next session opens |
| **REVIEWED** | 0 | No | At the next session open: → REARMED only if NAV ≥ floor / 0.99. Otherwise → STOPPED |
| **REARMED** | 0.5 | Yes | Sets **rearm_floor = 0.99 × NAV at re-arm**, which replaces the 0.965·HWM trigger. → ACTIVE when NAV ≥ 0.99·HWM; → **STOPPED** when NAV ≤ rearm_floor (a second halt) |
| **STOPPED** (terminal) | 0 | No | Entered on NAV ≤ contest floor from any state, or on a second halt. Signals keep running in shadow mode only |

**Daily lock.** This is a separate flag: P&L ≤ −1.25% of session-open NAV flattens everything and blocks entries until the next session. It changes the state machine only through NAV.

**Why the old re-trigger loop can't happen.** REARMED never uses the 0.965·HWM trigger again. Re-arming is allowed only when the rearm_floor sits above the contest floor.

**Worked example on $100,000.**
1. NAV falls to $97,900 (−2.1%): **REDUCED**, size 0.5.
2. NAV falls to $96,400: **HALTED**, flatten.
3. Next session: REVIEWED → REARMED, since 96,400 ≥ 95,000 / 0.99 = 95,960. rearm_floor = $95,436.
4. NAV falls to $95,400: **STOPPED**.
5. If NAV had instead recovered to $99,000, the state would have returned to ACTIVE.

---

## 6. Acceptance tests (run in order; A1, A2 and A4 must pass before full size)

| Test | Method | Pass rule | If it fails |
|---|---|---|---|
| **A1: Code validation** | Re-run on May 2024–Mar 2026 with the replication's sizing and costs: 1-minute SPY bars, or 30-minute bars with a documented VWAP approximation | Sharpe within ±0.15 of 0.39 and total return within ±3 pp of 9.4% | The code or data isn't comparable. Demonstration size only |
| **A2: Fresh holdout** | **1 Apr–17 Nov 2026** (about 160 sessions), frozen rules, **competition fees** ($0.99 per order plus $0.000166 per share sold), 2 bps per side, 1-bar and 3-minute lag variants, 1× static sizing. Report net expectancy, profit factor, Sharpe, MDD, trade count, trade-level win rate, and a **block bootstrap of 8-session returns** | Full size if the net mean daily return is ≥ 0 **and** MDD ≤ 6% at 1× | Demonstration size: L × 0.25 |
| **A3: Ablations** | Same data and costs: SPY vs SPY+QQQ (50/50 within L) vs four ETFs; combined vs stateful signal; 15:55 vs 16:00 exit | Promote QQQ only if SPY+QQQ improves **both** Sharpe and MDD in **both** May 2024–Mar 2026 and the A2 window | Keep QQQ in shadow |
| **A4: Execution lifecycle** (separate practice paper account) | Account discovery and name check; 1-share limit away from the market, then cancel; 1-share marketable fill; close; 1-share short and cover; timeout followed by order-query reconciliation; schedule→quote→order→ack→fill p50/p95 over ≥ 20 cycles; **sentinel kill drill** (synthetic breach); heartbeat-loss drill; confirm no live-order tool is available | Kill drill flattens within 60 s; p95 tick-to-fill ≤ 3 minutes; zero duplicate orders | Fix before any competition order. If shorting fails, run long-only and disclose it |
| **A5: State machine** | Synthetic NAV paths: steady decline, V-shaped recovery, decline–recover–decline, a gap through two thresholds at once, stale data | Every transition matches Section 5.2; no re-trigger loop | Fix the code |

**Limits of A2.** About 160 sessions give an annualised-Sharpe standard error of roughly 1.26 [Calc]. A2 can catch a clear failure, but it **cannot confirm an edge**. The pass rule is deliberately set to "not clearly broken" instead of a Sharpe target that would only measure noise.

**Benchmarks to report alongside every result:**
- SPY buy-and-hold at the same average exposure, which beat the strategy out-of-sample from May 2024 to Mar 2026.
- A random-direction version with the same ticks and exits (1,000 permutations), to check that the signal's direction carries information.

---

## 7. Architecture and judging (changes from v1 only)

- **Both tools are used, as the handbook FAQ requires ("you must use both").**
  - **Qoder Desktop** runs the local sentinel and the dashboard.
  - **Cloud Agents** run the scheduled pipeline: pre-market research, then the coordinator at each tick, which calls the signal skill, the risk gate, the veto-only LLM auditor and the executor. An end-of-day reporter, the watchdog and Slack alerts complete it.
  - Credit use falls to about 15 runs a day.
- **Permissions:** grant Market Data scope only, as the handbook advises. Use the `sim_trade_*` tools: `sim_trade_account_list`, and `sim_trade_input_order` with `order_type` 1 or 3. Find the account ID at the start of every session and match the account name. **Reject any live-order tool.**
- **Shadow book:** log QQQ, TLT and GLD Noise-Area signals and Qoder's ORB baseline B0 with hypothetical fills. The deck then compares measured results instead of opinions.
- **Judging evidence that v2 strengthens:**
  - The out-of-sample reconciliation and the downgraded claim (30% strategy and risk).
  - The sentinel kill drill and state-machine tests (30% strategy and risk, and 30% Qoder).
  - Honest benchmark comparisons (25% return).
  - Frozen configs, logged rejections, and a what-failed section (15% documentation).

**Timeline**
- **18 Nov:** A1–A5, built in parallel with the skills. No competition orders.
- **19 Nov:** go live at m = 0.5 if A1, A2 and A4 pass.
- **20 Nov onward:** move to m = 1.0 once live fills match the modelled fills (ticks within 3 bps).
- **28–30 Nov:** write up and submit early.

---

## 8. Sources

**Replication and papers**
- [Independent out-of-sample replication: PazSheimy/spy-intraday-momentum-oos](https://github.com/PazSheimy/spy-intraday-momentum-oos), covering its README, the code in `src/` and the paper source. Data is licensed and not included, so the numbers could not be reproduced here.
- Zarattini, Aziz & Barbon, [Beat the Market: An Effective Intraday Momentum Strategy for SPY](https://concretumgroup.com/beat-the-market-an-effective-intraday-momentum-strategy-for-sp500-etf-spy/) (Sep 2025 version: Tables 2–4, A1, FAQ Q22–Q24).
- Zarattini, Barbon & Aziz, [A Profitable Day Trading Strategy for the U.S. Equity Market](https://concretumgroup.com/wp-content/uploads/2026/02/A-Profitable-Day-Trading-Strategy-For-The-U.S.-Equity-Market.pdf) (Tables 1–3).
- Lo (2002), "The Statistics of Sharpe Ratios," *Financial Analysts Journal* 58(4), the standard-error formula used in Section 2. Not re-read this session.

**Competition and platform**
- [Official handbook and FAQ](https://moomoo-qoder-challenge-rgkelnyuzbm.qoder.website/).
- [Moomoo OpenAPI trade FAQ](https://openapi.futunn.com/futu-api-doc/en/qa/trade.html): paper accounts allow market and limit orders only, DAY only.
- [NYSE hours](https://www.nyse.com/markets/hours-calendars).
- `moomoo_qoder_strategy_validation_response.md`: MCP tool schema notes and the handbook FAQ quote.

**Still unverified**
- Whether the 40% cap covers ETFs.
- The formula for risk-adjusted return.
- Whether the `sim_trade_*` tools work under Market Data-only OAuth.
- The sentinel's achievable polling rate.
- Strategy performance from Apr to Nov 2026, which is what A2 tests.
