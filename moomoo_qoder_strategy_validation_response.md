# Moomoo × Qoder — Strategy Review: Validation Response

**Response to:** `qoder_strategy_validation_challenges.md`
**Reviewed report:** `moomoo_qoder_strategy_review.md` (17 November 2026)
**Primary source verified directly:** Zarattini, Barbon & Aziz, *A Profitable Day Trading Strategy for the U.S. Equity Market* ([PDF](https://concretumgroup.com/wp-content/uploads/2026/02/A-Profitable-Day-Trading-Strategy-For-The-U.S.-Equity-Market.pdf); [SSRN 4729284](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4729284))

## Bottom line

Two **critical errors** in the earlier review are confirmed: the tested stop is **0.10 × ATR(14) from the executed entry**, not **1.0 × daily ATR beyond the breakout**, and the stop rule must **reject** an over-wide stop, not clamp it. The 17% win rate and 30% win-rate gate are also withdrawn: the 17% is a reader comment, while the paper reports a 48.4% “Hit Ratio” that it does not define.

No orders were placed, no permissions were changed, and no cloud agents were modified. The 2.81 Sharpe and 1,637% return remain attributes of the **paper’s original configuration**, not of the competition variant.

---

## 1. Correction table

| Issue | Your verdict | Exact primary-source evidence with URL/page/code | Corrected rule | Backtest or runtime test needed |
|---|---|---|---|---|
| **P1. ATR stop multiplier** | **Confirmed error** | [Paper §2.1, p.9](https://concretumgroup.com/wp-content/uploads/2026/02/A-Profitable-Day-Trading-Strategy-For-The-U.S.-Equity-Market.pdf#page=9): “we placed a stop loss order at a 10% ATR distance from the executed entry price.” [Fig. 2, p.8](https://concretumgroup.com/wp-content/uploads/2026/02/A-Profitable-Day-Trading-Strategy-For-The-U.S.-Equity-Market.pdf#page=8): “a stop is added at 10%ATR from the entry.” [QC 18444](https://www.quantconnect.com/research/18444/opening-range-breakout-for-stocks-in-play/): `_stopLossAtrDistance = 0.1m`; `PlaceTrade(OpeningBar.High, OpeningBar.High - _stopLossAtrDistance * ATR)`. QC therefore anchors the stop at the **OR boundary**, not the fill; the paper anchors it at the **executed entry**. | Use `stop_distance = 0.10 × ATR(14)`; reference price = **actual fill**. If mimicking QC, reference = OR boundary and disclose the slippage difference. My `1.0×` was a **10× multiplier change** and is not paper-tested. | Ablate `0.10 / 0.25 / 0.50 / 1.0 × ATR`; entry-anchored vs OR-anchored; same data, costs, and fills; report `E_R`, PF, MDD, stop-hit rate, and slippage. |
| **P2. Clamp vs skip** | **Confirmed error** | My formula `min(max(1.0×ATR14, 0.0035×entry), 0.025×entry)` clamps; the prose says skip. The paper has **no 2.5% cap** and **no 0.0035×entry stop floor**; `$0.0035` is the **per-share commission** ([Paper p.10](https://concretumgroup.com/wp-content/uploads/2026/02/A-Profitable-Day-Trading-Strategy-For-The-U.S.-Equity-Market.pdf#page=10)). | Compute `raw_stop = k × ATR14` first. Reject the trade if `raw_stop / fill > 0.025` or `raw_stop < 1 tick`. Do **not** clamp. Unit tests: fill `$100`, ATR `$3`, `k=0.10` → `$0.30` (0.30%) accept; `k=1.0` → `$3.00` (3.0%) reject. Remove `0.0035×entry` entirely. | Unit tests for the `$100 / $3` cases; backtest the rejection rate and confirm sizing always uses the validated raw stop. |
| **P3. RVOL denominator, threshold, count** | **Confirmed error** | [Paper p.14](https://concretumgroup.com/wp-content/uploads/2026/02/A-Profitable-Day-Trading-Strategy-For-The-U.S.-Equity-Market.pdf#page=14): `RVOL_{t,j} = ORVolume_{t,j} / ((1/14) Σ_{i=1}^{14} ORVolume_{t-i,j})` — the **mean** of the prior 14 sessions. [p.15](https://concretumgroup.com/wp-content/uploads/2026/02/A-Profitable-Day-Trading-Strategy-For-The-U.S.-Equity-Market.pdf#page=15): “Relative Volume was at least 100%” and “top 20.” [QC](https://www.quantconnect.com/research/18444/opening-range-breakout-for-stocks-in-play/): `s.RelativeVolume > 1`, `MaxPositions = 20`, `_universeSize = 1000`. | Baseline: **mean**, `≥ 1.0`, **top 20**. Competition variant: mean, `≥ 1.0`, **top 8–10** for position caps. Median, `1.5`, and top 5–8 are **experiments**, not paper parameters. | Mean vs median; thresholds `1.0 / 1.5 / 2.0`; counts `5 / 8 / 10 / 20`; report candidate counts, fill rates, and `E_R`. |
| **P4. Entry confirmation** | **Defensible but untested** | [Paper p.9](https://concretumgroup.com/wp-content/uploads/2026/02/A-Profitable-Day-Trading-Strategy-For-The-U.S.-Equity-Market.pdf#page=9): after the first five minutes, place a **stop order at the OR high/low** in the direction of the opening bar; no closed-candle confirmation, VWAP, or SPY filter. [QC](https://www.quantconnect.com/research/18444/opening-range-breakout-for-stocks-in-play/) decides at 09:35 from `OpeningBar`. | Baseline: **09:35 stop order** at OR high/low. Variant: **09:40 five-minute close confirmation**. VWAP/SPY are separate experiments. State explicitly that the confirmation is **not** the paper’s tested rule. | Trigger entry vs closed-candle entry; timestamps 09:35 / 09:36 / 09:40; realistic fills; report missed trades, delay, and `E_R`. |
| **P5. Trailing exits** | **Defensible but untested** | [Paper p.9](https://concretumgroup.com/wp-content/uploads/2026/02/A-Profitable-Day-Trading-Strategy-For-The-U.S.-Equity-Market.pdf#page=9): exit at the stop or “at the end of the trading session (i.e., 4:00 pm ET).” [Fig. 2, p.8](https://concretumgroup.com/wp-content/uploads/2026/02/A-Profitable-Day-Trading-Strategy-For-The-U.S.-Equity-Market.pdf#page=8): “A profit target is set at the EOD.” No breakeven, trailing stop, noon exit, or 2R target. | Baseline: **stop or EOD close**. Treat breakeven/trail/noon as an experiment; do not claim superiority without net `E_R`, PF, MDD, and tail-dependence results. | Baseline vs fixed 2R vs BE at +1R + 1.5× five-minute ATR trail + noon stop; same entries and costs; report tail dependence and profit factor. |
| **P6. “5-second software stop”** | **Confirmed error** | Moomoo schema: `sim_trade_input_order.order_type` enum is **[1 = Limit, 3 = Market]** only — **no native stop**. A Cloud Agent **schedule is periodic**, not an always-running five-second monitor; no observed cadence, heartbeat, or latency was supplied. | Withdraw the 5-second claim unless demonstrated. Baseline: **one-minute close-based software exit**, with disclosed gap risk. Optional: a local always-on 5-second watchdog only after measured p95 latency and heartbeat recovery. No max-loss guarantee. | Measure quote→decision→order→ack→fill p50/p95; kill/restart; missed heartbeat; duplicate-order reconciliation; assert no unprotected interval beyond the stated bound. |
| **P7. 17% win rate / 30% gate** | **Confirmed error** | The 17% figure is a **reader comment** on [QC 18444](https://www.quantconnect.com/research/18444/opening-range-breakout-for-stocks-in-play/), not a reported result. [Paper Table 3, p.19](https://concretumgroup.com/wp-content/uploads/2026/02/A-Profitable-Day-Trading-Strategy-For-The-U.S.-Equity-Market.pdf#page=19) reports **48.4% “Hit Ratio”**, but the text does not define whether it is trade-level or daily. | Delete the 17% attribution and the arbitrary 30% gate. Use **trade-level wins/trades from our own fills**, plus net `E_R > 0`, PF ≥ 1.3, average win/loss, and bootstrap CI. Report the paper’s 48.4% only as “reported Hit Ratio, definition not stated.” | Compute trade-level stats from our fills; if only a portfolio hit ratio exists, do not call it a win rate. |
| **P8. No OOS for this variant** | **Confirmed error** | [Paper Table 3, p.19](https://concretumgroup.com/wp-content/uploads/2026/02/A-Profitable-Day-Trading-Strategy-For-The-U.S.-Equity-Market.pdf#page=19): 1,637%, 2.81 Sharpe, 12% MDD for **5m-ORB + Rel Vol, 2016–2023**. [QC 18444](https://www.quantconnect.com/research/18444/opening-range-breakout-for-stocks-in-play/) shows a **2016-only** backtest. The 0.39 Sharpe SPY study is a **different strategy**. | Attribute 2.81 / 1,637% only to the paper baseline. Run the **unchanged baseline** and the **full variant** on the same untouched OOS/walk-forward period; report separately. | Walk-forward 2016–2023; OOS 2024–2026 if data permits; 17–28 Nov paper window; no attribution across models. |
| **P9. “Bar >2 minutes old”** | **Confirmed error** | A five-minute bar covering 09:30–09:35 is **60 seconds old at 09:36**; that is normal bar formation, not staleness. The paper has no such rejection rule. My rule conflated **bar age** with **data latency**. | Define `age = now − expected_bar_close`. At 09:35:05 the 09:30–09:35 bar is complete. Reject only if the expected bar is missing, its timestamp is wrong, or the last quote is older than 60 seconds. Test 09:36, 09:39, 09:41. | Synthetic timestamp tests; assert no false rejection at 09:36/09:39/09:41; verify missing-bar detection. |
| **P10. “Current regime = risk-off”** | **Confirmed error** | A 16 Nov 2026 close is a **point-in-time snapshot**, not a persistent fact. The handbook does not prescribe a regime. | Recompute each session from **prior close**: SPY close vs 20-day SMA; optional 20-day realized vol for sizing. If data is missing, default to **risk-off / no new entries**. Never hard-code the regime. | Replay daily; data-outage test; confirm the same rule produces the 16 Nov label without storing it. |
| **P11. Official vs recommended** | **Not an issue for dual tooling; confirmed error for labeling** | [Handbook FAQ](https://moomoo-qoder-challenge-rgkelnyuzbm.qoder.website/): “Must I use both local Qoder and Cloud Agents?” — **“Yes, you must use both of them.”** [Judging criteria](https://moomoo-qoder-challenge-rgkelnyuzbm.qoder.website/): Risk-adjusted return 25%, Qoder/Cloud Agent usage 30%, Strategy & risk 30%, Reusability & documentation 15%. | The **mandatory** items are both tools, the dedicated competition paper account, paper-only trading, and submission. Subagent orchestration, a 15-minute schedule, dashboards, and all our risk limits are **recommended or self-imposed**, not official rules. | Documentation audit: label every “official” claim with a handbook quote; relabel the rest. |
| **P12. MCP / paper-account permissions** | **Confirmed error** | `sim_trade_account_list` discovers accounts; `sim_trade_input_order` requires `acc_id`, `market`, `symbol`, `order_type`, `order_side`, `qty`; `order_type` is **1/3 only**; the tool is **non-idempotent** — after a timeout, check today’s orders before resubmitting. `market_id=100` is documented for U.S. margin/short accounts. [Handbook](https://moomoo-qoder-challenge-rgkelnyuzbm.qoder.website/): select **Market Data**; do **not** select live trading execution or account-order permissions; use a **separate paper account** for tests; the competition account **cannot be reset**. | Discover `acc_id`/`market` every session; never hard-code the account ID. Confirm the dedicated competition account against the moomoo Papertrade section. Test submission, fill, cancel, exit, and short separately. Never enable live permissions. | Account-list snapshot; 1-share non-marketable order + cancel; 1-share marketable fill; close; short 1 share; log latencies and rejects. |
| **A1. Risk budget consistency** | **Confirmed error (ambiguous)** | My `$95k` halt (5% from initial) and `6% peak-to-trough` drawdown can trigger in either order: after a run-up, a 6% trailing drawdown can occur **above** `$95k`, so the trailing limit fires first; from initial capital, 5% fires first. Unrealized P&L treatment was unspecified. | Use **one ruler**: mark-to-market equity including unrealized P&L, measured from **initial $100k**. At `$95k` (−5%): stop new entries, cancel working orders. At `$94k` (−6%): flatten and stop for the day. If a trailing-peak limit is added, define it separately and use the **tighter** of the two. | Equity-path simulation; verify trigger order; gap scenario; confirm unrealized P&L is included. |
| **A2. “No chase >0.25×ATR”** | **Confirmed error (ambiguous)** | My review did not state **daily vs five-minute ATR** or the reference price. | Use **daily ATR(14) through the prior close**. For longs, reject if fill > `OR_high + 0.10 × ATR_daily`; inverse for shorts. The `0.10×` cap keeps entry within roughly 1R of the breakout when the stop is entry-anchored. Label untested. | Ablate caps `0 / 0.10 / 0.25 × ATR`; measure missed trades and `E_R`. |
| **A3. Portfolio exposure** | **Defensible but untested** | Self-imposed; the paper has **no** 15% per stock, 30% per sector, gross 100%, or beta ±30% constraints. | With 5–8 names, 15% per name and gross ≤100% is feasible but tight: 7 names × 15% = 105%, so the gross cap binds. Compute ex-ante beta from **prior 60 daily returns**; if net beta ±30% is infeasible, **cut gross**, do not force a hedge. Sector cap 30% means at most 2 names per sector. | Candidate-set feasibility; beta/correlation using prior data only; stress test. |
| **A4. Short selling** | **Unverified** | The paper trades both long and short ([p.9](https://concretumgroup.com/wp-content/uploads/2026/02/A-Profitable-Day-Trading-Strategy-For-The-U.S.-Equity-Market.pdf#page=9)); the API has `order_side=2` (Sell), but borrow eligibility, locate, fees, and sell-before-buy semantics were not verified. | Verify the account is a U.S. margin/short account; verify the stock is shortable; test a 1-share short in a **separate paper account**; confirm the position goes negative and can be covered. If not verified, run **long-only**. | Short 1 share; confirm position, borrow fee, cover; record rejects. |
| **A5. Tail-dependence metric** | **Confirmed error** | “Top five trades ≤ 50% of net P&L” is unstable when net P&L is near zero: a small denominator makes the ratio explode. | Use **top-five share of gross absolute P&L**: `Σ top 5 |PnL_i| / Σ all |PnL_i|`, plus **net P&L excluding the top five winners**. Report fees and slippage explicitly. | Simulated trade logs with near-zero net; compare metrics; bootstrap. |
| **A6. Research comparability** | **Confirmed error (attribution)** | Paper: `$25,000`, 4× max leverage, ~7,000 stocks, top 20, `$0.0035/share`, no explicit slippage or borrow model ([pp.9–10](https://concretumgroup.com/wp-content/uploads/2026/02/A-Profitable-Day-Trading-Strategy-For-The-U.S.-Equity-Market.pdf#page=10)). Competition: `$100,000`, 5–8 names, 15%/30%/gross caps. | Do not carry 2.81 or 1,637% into the competition. Run the **constrained baseline** and the variant separately; publish an attribution table. | Constrained baseline vs paper; cost/slippage sensitivity; state all differences. |
| **A7. No-look-ahead** | **Confirmed error (needs specification)** | Paper uses the **finalized 09:30–09:35 bar** and **prior 14 completed sessions** ([pp.9, 14](https://concretumgroup.com/wp-content/uploads/2026/02/A-Profitable-Day-Trading-Strategy-For-The-U.S.-Equity-Market.pdf#page=9)). My 09:40 confirmation necessarily uses the **09:35–09:40 bar**, which is later information. | At 09:35: use the finalized 09:30–09:35 bar; RVOL denominator = prior 14 completed sessions; ATR = prior 14 daily bars through the prior close; SPY regime = prior close. If using 09:40 confirmation, entry is earliest **09:40:00** and the rule is a labeled deviation. | Replay with timestamps; assert no data after the decision time; audit 09:35 and 09:40 decisions. |

---

## 2. Faithful published-ORB baseline

| Element | Published baseline | Source |
|---|---|---|
| Universe | U.S. NYSE/Nasdaq stocks, ~7,000 names; opening price > $5; average daily volume over prior 14 days ≥ 1,000,000 shares; ATR(14) > $0.50 | [Paper p.9](https://concretumgroup.com/wp-content/uploads/2026/02/A-Profitable-Day-Trading-Strategy-For-The-U.S.-Equity-Market.pdf#page=9) |
| Opening range | 09:30–09:35 ET, five minutes | [Paper p.9](https://concretumgroup.com/wp-content/uploads/2026/02/A-Profitable-Day-Trading-Strategy-For-The-U.S.-Equity-Market.pdf#page=9) |
| Direction | Green opening bar → long only; red opening bar → short only; doji → no order | [Paper p.9](https://concretumgroup.com/wp-content/uploads/2026/02/A-Profitable-Day-Trading-Strategy-For-The-U.S.-Equity-Market.pdf#page=9) |
| Entry | Stop order at the OR high (long) or OR low (short), placed after 09:35; triggered on a subsequent breakout | [Paper p.9](https://concretumgroup.com/wp-content/uploads/2026/02/A-Profitable-Day-Trading-Strategy-For-The-U.S.-Equity-Market.pdf#page=9) |
| Stop | **0.10 × ATR(14) from the executed entry price** | [Paper p.9](https://concretumgroup.com/wp-content/uploads/2026/02/A-Profitable-Day-Trading-Strategy-For-The-U.S.-Equity-Market.pdf#page=9) |
| QC implementation | `_stopLossAtrDistance = 0.1m`; stop at `OpeningBar.High − 0.1 × ATR` (long) / `OpeningBar.Low + 0.1 × ATR` (short) — anchored at the **OR boundary**, not the fill | [QC 18444](https://www.quantconnect.com/research/18444/opening-range-breakout-for-stocks-in-play/) |
| Exit | Stop loss, otherwise close at **16:00 ET**; no fixed profit target, trailing stop, or noon exit | [Paper p.9](https://concretumgroup.com/wp-content/uploads/2026/02/A-Profitable-Day-Trading-Strategy-For-The-U.S.-Equity-Market.pdf#page=9) |
| Sizing / leverage | Loss if stopped = **1% of capital deployed** per position; maximum **4× leverage**; starting capital **$25,000** | [Paper p.10](https://concretumgroup.com/wp-content/uploads/2026/02/A-Profitable-Day-Trading-Strategy-For-The-U.S.-Equity-Market.pdf#page=10) |
| Selection | RVOL ≥ **100%** of the mean prior-14-session opening-range volume; take the **top 20** by RVOL | [Paper pp.14–15](https://concretumgroup.com/wp-content/uploads/2026/02/A-Profitable-Day-Trading-Strategy-For-The-U.S.-Equity-Market.pdf#page=14) |
| RVOL formula | `RVOL_{t,j} = ORVolume_{t,j} / ((1/14) × Σ_{i=1}^{14} ORVolume_{t-i,j})` | [Paper p.14](https://concretumgroup.com/wp-content/uploads/2026/02/A-Profitable-Day-Trading-Strategy-For-The-U.S.-Equity-Market.pdf#page=14) |
| Costs | `$0.0035` per share; **no explicit slippage or borrow-cost model** in the reviewed text | [Paper p.10](https://concretumgroup.com/wp-content/uploads/2026/02/A-Profitable-Day-Trading-Strategy-For-The-U.S.-Equity-Market.pdf#page=10) |
| Reported results | 1,637% total, 41.6% IRR, 14.8% volatility, **2.81 Sharpe**, 48.4% Hit Ratio, 12% MDD, 35.8% alpha, beta 0.00; 2016–2023 | [Paper Table 3, p.19](https://concretumgroup.com/wp-content/uploads/2026/02/A-Profitable-Day-Trading-Strategy-For-The-U.S.-Equity-Market.pdf#page=19) |
| Not tested | VWAP, SPY regime, 09:40 close confirmation, 2.5% stop cap, breakeven/trailing/noon exits, 5-second software stops, 15%/30%/beta caps, 5–8 names, $100k, 5%/6% halts | Paper, reviewed text |

**Correction to the earlier review:** the paper’s stop is **not** “1.0 × daily ATR beyond the range.” It is **0.10 × ATR(14) from the executed entry**. The `0.0035` in the old formula was almost certainly the per-share commission, not a stop floor.

---

## 3. Competition-specific variant

Every row below that is not “paper-tested” is a **competition modification**, not a result attributable to the paper.

| Element | Competition variant | Status vs paper |
|---|---|---|
| Universe | U.S. stocks/ETFs; price > $5; prior-14-day average volume ≥ 1M; ATR(14) > $0.50; liquidity/dollar-volume screen | Mostly paper-tested; ETF inclusion is a deviation |
| Opening range | 09:30–09:35 ET, five minutes | Paper-tested |
| Direction | Green → long; red → short; doji → no trade | Paper-tested |
| Entry | **09:35 stop order** at OR high/low | Paper-tested |
| Entry experiment | **09:40 five-minute close** beyond the OR level | Not paper-tested |
| Stop | `0.10 × ATR(14)` from actual fill; reject if stop > 2.5% of fill or < 1 tick | Paper-tested distance; rejection rule is a competition risk control |
| Stop experiments | `0.25 / 0.50 × ATR`; OR-anchored vs entry-anchored | Not paper-tested |
| Exit | Stop or **16:00 ET** close; **13:00 ET** on the Thanksgiving half-day | Paper-tested concept; half-day handling is operational |
| Exit experiments | Fixed 2R; breakeven + trailing; noon time stop | Not paper-tested |
| Selection | RVOL **mean**, `≥ 1.0`, **top 8–10**; sector and per-name caps | Paper-tested formula; count/caps are deviations |
| Sizing | Risk **0.25% of equity** per trade in risk-off, **0.50%** in risk-on; max 8 positions; 15% per name; gross ≤ 100% | Not paper-tested; paper risks 1% of deployed capital with 4× leverage |
| Regime | `risk_on = SPY prior close > 20-day SMA`; risk-off halves risk or blocks new entries; missing data → risk-off | Not paper-tested |
| Portfolio | Sector ≤ 30%; net beta ±30% only if feasible from prior 60-day betas; otherwise cut gross | Not paper-tested |
| Execution | **One-minute close-based software exit** as the conservative baseline; optional local 5-second watchdog only after measured latency and heartbeat proof | Not paper-tested; API has no native stop |
| Costs | `$0.0035/share` + **5 bps/side slippage** base case; test 0/5/10 bps | Paper reports commission only; slippage is a competition assumption |
| Shorting | Only after a 1-share short test confirms borrow/eligibility; otherwise **long-only** | Unverified |
| Account | Dedicated competition paper account; discovered via `sim_trade_account_list`; no live permissions | Official requirement |
| Window | 17–28 Nov 2026; no overnight positions; submission 30 Nov 2026 11:59 PM SGT | Official timeline |

### Risk hierarchy on $100k

- Equity = cash + mark-to-market positions, including unrealized P&L; sample at least every minute, preferably every 5 seconds by the local watchdog.
- Per-trade risk: **$250** risk-off, **$500** risk-on; `qty = floor(risk_$ / stop_distance)`; cap by `15% × equity / price`; round down.
- Position limits: max 8 open; 15% per name; 30% per sector; gross ≤ 100%; net beta ±30% only if feasible.
- Daily controls measured from **initial $100k**: at **$98,000** (−2%) stop new entries; at **$95,000** (−5%) cancel working orders and flatten; at **$94,000** (−6%) hard kill and stop for the day.
- Time controls: no new entries after **15:55 ET**; flatten by **15:58 ET**; on 2026-11-26 (Thanksgiving) no trading; on 2026-11-27 (early close) no new entries after **12:55 ET**, flatten by **12:58 ET**.
- Data outage: if the quote is older than 60 seconds, no new entries; if a position is open and data is stale for more than 5 minutes, flatten when data resumes and alert.
- Order reconciliation: after a timeout, query today’s orders before resubmitting; the API is non-idempotent.
- No averaging down, no overnight, no live permissions.

---

## 4. Minimal ablation/backtest matrix

All rows must use the **same minute-bar dataset**, same universe filters, same commission (`$0.0035/share`), same base slippage (`5 bps/side`), same fill assumptions, and the same walk-forward/OOS split. “Paper” means the exact published rule; “variant” means the competition modification.

| ID | Variant | RVOL | Selection | Stop | Entry | Filters | Exit | What it isolates |
|---|---|---|---|---|---|---|---|---|
| **B0** | Paper baseline | Mean, ≥ 1.0 | Top 20 | 0.10× ATR from fill | 09:35 stop order | None | Stop or EOD | Faithful replication |
| **B1** | Baseline + competition caps | Mean, ≥ 1.0 | Top 8 | 0.10× ATR from fill | 09:35 stop order | None | Stop or EOD | Cost of position caps |
| **R1** | RVOL median | Median, ≥ 1.0 | Top 8 | 0.10× ATR | 09:35 | None | Stop or EOD | Mean vs median |
| **R2** | RVOL ≥ 1.5 | Mean, ≥ 1.5 | Top 8 | 0.10× ATR | 09:35 | None | Stop or EOD | Threshold effect |
| **R3** | Top 5 / top 10 / top 20 | Mean, ≥ 1.0 | Varies | 0.10× ATR | 09:35 | None | Stop or EOD | Selection breadth |
| **S1** | Stop 0.25× ATR | Mean, ≥ 1.0 | Top 8 | 0.25× ATR from fill | 09:35 | None | Stop or EOD | Stop width |
| **S2** | Stop 0.50× ATR | Mean, ≥ 1.0 | Top 8 | 0.50× ATR from fill | 09:35 | None | Stop or EOD | Stop width |
| **S3** | Stop 1.0× ATR | Mean, ≥ 1.0 | Top 8 | 1.0× ATR from fill | 09:35 | None | Stop or EOD | Tests the old review’s claim |
| **S4** | OR-anchored stop | Mean, ≥ 1.0 | Top 8 | 0.10× ATR from OR boundary | 09:35 | None | Stop or EOD | Paper vs QC anchoring |
| **E1** | Closed-candle entry | Mean, ≥ 1.0 | Top 8 | 0.10× ATR | 09:40 close | None | Stop or EOD | Confirmation delay |
| **F1** | VWAP filter | Mean, ≥ 1.0 | Top 8 | 0.10× ATR | 09:35 | Long above VWAP; short below | Stop or EOD | VWAP hypothesis |
| **F2** | SPY regime | Mean, ≥ 1.0 | Top 8 | 0.10× ATR | 09:35 | Risk-on only | Stop or EOD | Regime hypothesis |
| **X1** | Trailing/BE/noon | Mean, ≥ 1.0 | Top 8 | 0.10× ATR | 09:35 | None | BE + trail + noon | Exit hypothesis |
| **C1** | Cost sensitivity | Mean, ≥ 1.0 | Top 8 | 0.10× ATR | 09:35 | None | Stop or EOD | 0/5/10 bps slippage |
| **C2** | Long-only | Mean, ≥ 1.0 | Top 8 | 0.10× ATR | 09:35 | None | Stop or EOD | Short-leg contribution |

Report for every row: number of trades, trade-level win rate, average win/loss, `E_R`, profit factor, Sharpe, max drawdown, stop-hit rate, average slippage, fees, and bootstrap 5th-percentile `E_R`. Do **not** compare rows run on different data or cost assumptions.

---

## 5. Go/no-go conclusion

| Gate | Pass condition | Fail action |
|---|---|---|
| **G1. Baseline replication** | The paper-rule baseline, run on our data, has **positive net `E_R` after costs** and a plausible PF; results are directionally consistent with the paper, not necessarily 2.81 Sharpe | Stop; the data or implementation is not comparable |
| **G2. Variant OOS** | The full variant has **net `E_R > 0`**, PF ≥ 1.3, bootstrap 5th-percentile `E_R > 0`, and max drawdown ≤ 6% on an untouched OOS/walk-forward period | No-go, or reduce risk and retest |
| **G3. Execution** | Every open position is covered by a documented exit process; if using the 5-second watchdog, p95 quote→fill latency is measured and heartbeat recovery is tested; otherwise use one-minute exits and disclose the gap risk | If 5 seconds cannot be demonstrated, **remove the 5-second claim**; do not imply a guaranteed max loss |
| **G4. Account/shorting** | The dedicated competition paper account is identified; a 1-share short test succeeds; sim tools are available without live permissions | Long-only variant, or no-go for the short leg |
| **G5. Data/look-ahead** | The 09:35 decision uses only the finalized 09:30–09:35 bar, prior-14-session RVOL, prior-14-day ATR, and prior-close SPY data; timestamp audit passes | No-go until the audit passes |

### What would falsify the proposal

- The faithful baseline has **negative net `E_R`** after realistic costs on our data.
- The competition variant has **negative OOS `E_R`**, PF < 1.1, or a 5th-percentile `E_R` ≤ 0.
- Max drawdown exceeds **6%**, or a single gap produces a loss **> 1.5R** after slippage.
- The 5-second stop cannot be demonstrated, and one-minute exits produce unacceptable tail losses.
- The top RVOL names are not shortable, not fillable under the 15% cap, or the data has look-ahead.
- Any live trading permission is enabled, or the competition account is used for experiments.

### What remains unverified

- The paper’s exact fill, slippage, borrow, halt, and delisting assumptions; it reports commission but no explicit slippage/borrow model.
- The competition variant’s OOS performance; no result exists yet for this exact configuration.
- The actual competition account permissions and whether `sim_trade_*` is available under the permitted authentication.
- Shortability, borrow fees, and sell-before-buy semantics for the candidate universe.
- The 5-second watchdog’s real latency, coverage, restart behavior, and fill reconciliation.
- Cloud Agent schedule reliability and heartbeat recovery.
- Minute-bar data coverage, corporate actions, and delisted-name survivorship.
- Half-day and market-halt handling.
- A verified **trade-level** win rate for our variant; the paper’s 48.4% “Hit Ratio” is undefined, and the 17% figure is only a reader comment.

### Statistical reality of the competition window

17–28 Nov 2026 contains roughly **9 trading sessions** (26 Nov closed; 27 Nov early close). At ≤ 8 trades/day, that is **≤ 72 trades**. A 95% confidence interval around a 48% win rate is roughly **±12–15 percentage points**. The two-week window can test execution, reconciliation, and process discipline; it **cannot** statistically prove a 30% win-rate gate or a 0.1R edge. Use the backtest for the edge and the paper window for operational validation.

### Verdict

**Go** on paper-testing the faithful baseline with conservative competition caps. **No-go** on the earlier 1.0× ATR stop, the clamp, the 17% attribution, the 30% gate, the unqualified 5-second stop, and any claim that the paper’s 2.81 Sharpe or 1,637% return belongs to this variant. The variant is **not validated** until the ablation matrix is run on identical data and costs.

---

## Sources

- [Zarattini, Barbon & Aziz — A Profitable Day Trading Strategy for the U.S. Equity Market (PDF)](https://concretumgroup.com/wp-content/uploads/2026/02/A-Profitable-Day-Trading-Strategy-For-The-U.S.-Equity-Market.pdf)
- [SSRN 4729284](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4729284)
- [Authors' research summary (Concretum)](https://concretumgroup.substack.com/p/a-profitable-day-trading-strategy)
- [QuantConnect: Opening Range Breakout for Stocks in Play](https://www.quantconnect.com/research/18444/opening-range-breakout-for-stocks-in-play/)
- [CXO Advisory methodological summary](https://www.cxoadvisory.com/individual-investing/intraday-trading-of-overactive-stocks-via-opening-range-breakout/)
- [Official Moomoo × Qoder participant handbook](https://moomoo-qoder-challenge-rgkelnyuzbm.qoder.website/)
- Moomoo MCP tool schemas: `sim_trade_input_order`, `sim_trade_account_list`
