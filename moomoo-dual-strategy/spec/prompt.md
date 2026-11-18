# Qoder Implementation Prompt — Two Competing Paper-Trading Agents ($50,000 Each)

## Role and objective

Act as a **senior quantitative researcher, algorithmic-trading engineer, and Qoder Cloud Agents architect**. **Implement and run** a controlled, side-by-side comparison of two strategies using Moomoo paper trading, rather than merely writing another strategy proposal.

**Goal:** Give me **two genuinely comparable portfolios, each with a virtual budget of USD 50,000**, trading simultaneously where possible, so I can inspect **actual paper-order fills, realized/unrealized P&L, drawdowns, and risk-adjusted returns**. Build a working dashboard and retain all evidence for the **Moomoo × Qoder AI Paper Trading Agent Challenge**.

I want both candidates tested, not another debate about which strategy sounds better:

1. **Portfolio A — 5-minute Opening Range Breakout (ORB), stocks in play.** Based on Zarattini, Barbon & Aziz's published U.S. stock ORB method and Qoder's corrected validation response.
2. **Portfolio B — SPY Noise-Area Intraday Momentum.** Based on Zarattini, Aziz & Barbon's SPY intraday momentum method and Claude's revised v2 second opinion.

**Use Qoder Desktop for implementation, tests, monitoring and dashboard; use Qoder Cloud Agents for documented orchestration, schedules, decisions and reporting.** Financial signal computations and risk limits must be deterministic code, not LLM guesses.

Proceed with all non-destructive implementation work without repeatedly asking for permission. If a step genuinely requires an action in my Moomoo app, account authorization, or unavailable credentials, tell me **the exact single action needed**, while completing everything else you can.

---

## 1. Hard constraints: simulation only, account isolation and capital

### Account provisioning — verify capability; do not invent it

**First, try to set up TWO NEW, DISTINCT Moomoo U.S. practice paper-trading accounts under my existing authorized Moomoo user profile, if the platform legitimately supports this.** Do **not** create extra Moomoo user identities or attempt to circumvent eligibility/account limits.

- Proposed labels: `ORB_PRACTICE_50K` and `NOISE_AREA_PRACTICE_50K`.
- Desired initial economic capital: **$50,000 each**. If the paper broker forces a different starting balance, do **not** claim to have changed it. Instead enforce a **separately audited $50,000 strategy-level starting virtual NAV and buying-power budget**, and show the broker balance and strategy NAV as different figures.
- Discover the **actual account-creation capability** in the current Moomoo app/API/MCP. `sim_trade_account_list` / `get_acc_list` **lists** accounts; it is **not** evidence of a create-account API.
- Prove each account is `SIMULATE`/paper, U.S.-market enabled, and genuinely independent. Save its actual account identifier and environment in a secure, gitignored mapping. Never guess an ID.
- **Do not touch, reset, revive, or trade the official competition account as part of this experiment.** Its results are distinct from these practice accounts and it cannot be reset under competition rules. Do not assume two practice accounts equal two competition entries.

**If two new distinct practice accounts cannot legitimately be created:**

1. Report the real limitation and any short manual setup steps available in Moomoo.
2. Preferred fallback: use **one authorized, separate, noncompetition practice paper account** with **two segregated $50,000 internal sub-ledgers**, unique strategy/order tags, independently tracked cash/NAV/risk, and separate trade attribution. Keep Portfolio B in SPY and Portfolio A in **individual stocks only** to avoid instruments overlapping. Trades in this fallback can be described as **actual paper fills in one shared practice account**, **not** fills from two distinct broker accounts; account-level margin and netting remain shared.
3. If no suitable authorized paper account can accept orders, implement **two shadow-execution portfolios** using recorded market data and documented simulated fill assumptions. Label all such results **hypothetical, not actual Moomoo fills**. Do not manufacture broker confirmations.
4. In all cases, finish the source code, data ingest, backtests, risk gates, dashboard, tests, and ready-to-run schedules; surface the account-provisioning blocker plainly.

**Absolute prohibitions:** no real-money trading, no live order tools, no real account permissions, no funds transfer, no reset cards, no leverage by default, no bypassing Moomoo rules, and no secret/token disclosure. Account authorization must be limited to the paper-trading functions that are genuinely required. If safe simulated order access cannot be verified, fail closed.

### Equal-budget comparison

- Portfolio A initial NAV = **$50,000**; Portfolio B initial NAV = **$50,000**.
- Calculate every position limit and risk cap using its own portfolio's **current strategy NAV**, not the full broker account or the other portfolio's assets.
- Both run on the **same market dates, market calendar, price timestamp convention, cost model, and reporting convention**.
- Report **both raw dollar profit and percentage return**, because their entry patterns and number of positions differ.
- Do not allow A to spend B's virtual cash or vice versa. If a shared paper account lacks enough genuine buying power for both, reject or reduce orders rather than silently treating the portfolios as independent.

---

## 2. Strategy A — 5-minute ORB on U.S. stocks

Implement a **faithful research baseline**, adapted only where required by paper-trading API constraints and the $50k risk budget. Log every deviation from the published paper. Do **not** silently reintroduce the earlier mistaken 1.0×ATR stop or a fixed 2R take-profit.

| Setting | Starting specification |
|---|---|
| Asset class | **U.S.-listed individual stocks only** (no SPY, QQQ, or other ETFs in A) |
| Price/liquidity universe | Opening price **> $5**, prior-14-session average daily volume **≥ 1,000,000 shares**, daily ATR(14) **> $0.50**, market cap **≥ $1B**, tradable and liquid |
| Opening range | First **5 minutes** of regular session: **09:30–09:35 America/New_York** |
| Direction | Opening candle green → **long breakout only**; red → **short breakout only**; doji → no order |
| Entry | After 09:35, trigger on a move beyond the opening-range high (long) / low (short). Use supported **paper market/limit orders** and log the difference from the research paper's resting-stop entry |
| Relative volume | Current first-five-minute volume divided by **mean** first-five-minute volume over the **previous 14 completed trading sessions**; **RVOL ≥ 1.0** |
| Selection | Rank eligible names by RVOL; trade up to **8 top-ranked** names, with sector/position/risk limits. Record that the original paper used top 20 |
| Stop distance | **0.10 × daily ATR(14)**, anchored to **actual executed entry**; use only prior completed daily bars for ATR |
| Exit | Stop-loss or scheduled end-of-day exit; **no fixed 2R target**, no automatic trailing stop in the baseline |
| Overnight | Flat before market closes; no overnight holding |
| Shorting | Only if account and symbol borrow/short eligibility are actually verified in practice paper trading; otherwise run a clearly labeled **long-only variant** and backtest it independently |

**Crucial execution constraint:** Moomoo paper trading may not offer a native stop order. Verify the **actual** order types exposed through the connected MCP or paper API. If there is no native stop order, implement and test a **local deterministic emergency exit monitor** that can only cancel/close positions, with measured quote-to-order latency, heartbeat/restart handling, and realistic simulated gap/slippage in backtests. Never claim a 0.10×ATR stop guarantees a maximum loss. **Do not enable ORB paper auto-execution unless its monitored stop process passes the acceptance tests.**

For A, start with **0.25% of A's NAV risk per trade** ($125 initially), max **15% of A's NAV per individual stock** ($7,500 initially), max 4 concurrent A positions, gross exposure ≤ 100% of A's NAV, sector concentration ≤ 30%, daily drawdown lock 1.25%, and terminal strategy drawdown halt 5%. Treat these as **competition design controls**, not published alpha parameters. Size as:

`qty = floor(min(risk_dollars / abs(entry - stop), 0.15 * strategy_NAV / entry, liquidity_cap))`

Use actual entry/stop direction for longs versus shorts. Reject the order if stop distance is invalid, too small relative to the executable spread, the order is stale, liquidity is insufficient, or the monitored exit cannot operate safely. Do not clamp an overly wide stop to a tighter number without retesting.

---

## 3. Strategy B — SPY Noise-Area Intraday Momentum

Implement the strategy's **published baseline** as closely as possible, using Claude's v2 as the implementation starting point, while clearly separating corrections, original research settings, and conservative paper-account adaptations.

| Setting | Starting specification |
|---|---|
| Instrument | **SPY ETF only** |
| Market session | Regular U.S. trading hours only |
| Lookback | **14 prior completed sessions** |
| Decision times | **10:00, 10:30, 11:00, …, 15:30 ET**, each at a closed-bar timestamp |
| Intraday noise | For each time slot, mean absolute return from 09:30 open to that slot over the prior 14 sessions |
| Upper band | `max(today_open, previous_close) × (1 + sigma_slot)` |
| Lower band | `min(today_open, previous_close) × (1 - sigma_slot)` |
| VWAP | Cumulative regular-session VWAP; use genuine intraday price/volume data (preferably 1-minute bars) |
| Target exposure | Long when price > upper band **and** above VWAP; short when price < lower band **and** below VWAP; otherwise flat — compare with the literal stateful/trailing-exit interpretation in an ablation test |
| Position sizing | Start with a **conservative 40% of B's NAV notional** ($20,000 initially), pending confirmation that larger SPY ETF exposure is permitted. Do not assume ETFs are exempt from the single-security restriction |
| Exits | Signal-driven/reconciliation exits at decision ticks; **independent emergency liquidation monitor** between ticks; flatten ahead of close |
| Overnight | None |
| Shorts | Enable only after verified paper shortability and a successful separate-practice-account test; otherwise use a separately backtested long-only variant |

Use the 14-day default and `VM = 1.0`; do not add unvalidated VIX/RSI filtering or a four-ETF basket to the live comparison. QQQ, TLT and GLD can be optional **shadow-only** research candidates, not part of the B portfolio's realized comparison.

For B, start with the same **1.25% of B's day-start NAV daily loss lock** and **5% peak-to-trough terminal drawdown halt**. Its local catastrophe monitor must close, never add, and must use its own B-only positions, NAV and account mapping. Half-hour scheduled checks alone are **not sufficient** to guarantee loss protection.

---

## 4. Shared controls and honest execution

Implement a **deterministic shared risk library** with separated per-portfolio state, checked before every simulated order and continually while positions are held:

1. **Account guard:** paper environment, exact allowlisted practice account ID, U.S. market, strategy ID; reject all live tools and mismatches.
2. **Capital guard:** independent $50k virtual budgets; no cross-subsidy; portfolio gross exposure ≤ 100%; no margin/leverage initially.
3. **Concentration guard:** enforce portfolio-specific limits plus any stricter actual Moomoo limits (official competition limit is up to 40% in one stock; apply a conservative 40% instrument limit to both strategies pending ETF clarification).
4. **Time guard:** only regular session; U.S. exchange holiday/early-close calendar; entry deadlines and pre-close liquidation.
5. **Freshness guard:** expected completed bar availability, correct timestamp, fresh quotes and account state; fail closed if missing.
6. **Daily loss:** mark-to-market NAV change from **each session's opening strategy NAV**, inclusive of unrealized P&L; at **−1.25%** cancel pending orders, flatten, and lock new entries for that day.
7. **Maximum drawdown:** calculate from each strategy's **own high-water NAV**; reduce exposure at −2%, halt/flatten at −3.5%, terminal disable at −5%. Implement a tested **state machine with hysteresis/re-arming**, so a next-day restart cannot immediately retrigger or bypass the terminal rule. Document whether terminal stop overrides all recovery states.
8. **Execution protection:** independent, deterministic local sentinel (close-only) with heartbeat and restart recovery; cloud watchdog must detect absence of sentinel and block entries; measure actual frequency and latency instead of promising 5-second stops.
9. **Order handling:** unique strategy+date+symbol+signal ID, one live working order per symbol per account where applicable, partial-fill support, poll order/position status after any timeout **before retrying**; avoid duplicate non-idempotent simulated orders.
10. **Short selling:** verify support, borrow/eligibility, costs, correct negative positions, and covering behavior in a practice account. If not verified, fail closed on short signals.
11. **No reset:** never reset, revive, or reinitialize the official competition account; never create duplicate competition identities.
12. **Log every decision:** signal, no-trade, risk rejection, order request, acknowledgement, partial fill, final fill, cancellation, stop, emergency exit, realized P&L, fee and slippage; attach timestamps and config hashes.

**Trading hours:** use `America/New_York`, not a fixed Malaysia/Singapore clock offset. Competition window is **17–28 Nov 2026**: eight U.S. trading sessions (17, 18, 19, 20, 23, 24, 25, and half-day 27). **Nov 26 Thanksgiving is closed; Nov 27 closes at 13:00 ET.** Use an exchange calendar, not just weekday cron. Flatten before 16:00 ET normally and before 13:00 ET on Nov 27; build an independent close-verification schedule. Do not trade after the simulated close.

**Fees:** use the **actual published challenge paper fee model** for both comparison ledgers: **$0.99 per stock/ETF order**, plus **$0.000166 per share sold (maximum $8.30 per trade)**; add modeled spread, slippage and partial-fill effects separately. If actual practice-account fees differ, show **broker-recorded** versus **competition-adjusted** P&L as separate columns; never mix them silently.

---

## 5. Historical validation before activating paper orders

Build repeatable backtests for **both strategies** using realistic historical U.S. intraday bars, look-ahead-safe indicator computation, proper U.S. market calendars, corporate-action-aware data, transaction costs, entry delays and conservative execution assumptions.

**Do not represent any published Sharpe ratio as our own result.** The published ORB in-sample Sharpe (2.81) and published SPY Noise-Area in-sample Sharpe (~1.33) belong to different research configurations. SPY Noise-Area's independent out-of-sample replication reported materially weaker results (~0.39 Sharpe); the exact competition variants of both strategies remain unproven.

Backtests must produce:

- Net P&L, return %, annualized Sharpe/Sortino where sample duration supports them, maximum drawdown, daily worst loss, trade-level win rate, average win/loss, expectancy in dollars and R, profit factor, time in market, gross/net exposure, order count, costs and slippage.
- Separate historical **in-sample / walk-forward / untouched holdout** reports. Use only completed historical bars available at each decision time; do not tune parameters on the holdout.
- Cost scenarios with normal and stressed slippage, delayed orders, partial fills, missed schedule ticks, stale market data, disconnections, overnight/holiday gaps and local-sentinel failures.
- For A: baseline published ORB versus constrained $50k implementation; optional 0.10/0.25/0.50×daily-ATR stop sensitivity, but do not silently select the best in-sample variant.
- For B: published baseline versus the precise target-position/VWAP implementation; long-only and short-enabled versions if necessary.
- **An identical, clearly labeled comparison period** for A and B and a simple SPY buy-and-hold benchmark. Report any unequal data coverage or exposure explicitly.

If historical data are missing or the results fail sensible safety/edge checks, **do not pretend the test passed**. Leave that portfolio in shadow mode, explain the blocker, and continue developing the other independent pipeline.

---

## 6. Qoder architecture — build, not just describe

Create a version-controlled working project, preferably Python for deterministic quant/risk execution, with separate configurations and strategy states:

```text
moomoo-dual-strategy/
  README.md
  AGENTS.md
  pyproject.toml
  .env.example                  # never store secrets in Git
  config/
    accounts.example.yaml
    orb.yaml
    noise_area.yaml
    risk.yaml
  src/
    data/                       # Moomoo feed, cache, market calendar
    strategies/orb.py
    strategies/noise_area.py
    risk/gate.py
    risk/state_machine.py
    risk/sentinel.py
    execution/moomoo_paper.py
    execution/reconciliation.py
    portfolio/ledger.py
    agents/                     # Cloud coordinator / researcher / auditor / executor / reporter
    backtest/
    reporting/
    dashboard/
  tests/
  logs/
    orb/
    noise_area/
  reports/
  evidence/
```

Implement these agent roles:

- **Coordinator (Cloud):** schedules the two pipelines and maintains per-portfolio state without mixing accounts.
- **Market-data/research agent:** pulls intraday prices, volume, calendars, history; explains detected setups.
- **Signal engines (deterministic skills):** evaluate ORB and Noise-Area independently.
- **Risk gates (deterministic skills):** approve/reject per-account orders using hard caps.
- **Read-only Risk Auditor agent:** reviews and can veto but never place or increase orders.
- **Paper Executor:** can only access the approved practice paper account(s) and simulated order tools.
- **Local sentinel and Cloud watchdog:** monitor risk independently; **close-only** permissions for emergency path.
- **Reporter:** daily P&L/reconciliation, strategy comparison, dashboard data export, and notification summaries.

Configure Qoder Cloud schedules explicitly for `America/New_York`; account for cloud-session startup and tool-call latency. Treat scheduled runs as bounded executions, **not continuous sub-second daemons**. Where the stop mechanism needs faster monitoring, use a supervised local deterministic service with tested recovery, and disable strategy A if its risk protection cannot be maintained.

---

## 7. Dashboard: let me see actual results side by side

Produce a usable local browser dashboard, with automatically refreshing **read-only** strategy views and at least the following:

| Metric | ORB ($50k) | Noise-Area SPY ($50k) |
|---|---|---|
| Initial strategy capital | $50,000 | $50,000 |
| Current marked-to-market strategy NAV | Live from independent ledger | Live from independent ledger |
| Net realized P&L | Actual paper fills if available | Actual paper fills if available |
| Unrealized P&L | Account-position reconciled | Account-position reconciled |
| Total return % | Calculated | Calculated |
| Maximum drawdown % | Calculated | Calculated |
| Daily loss / risk limits remaining | Calculated | Calculated |
| Trades and fill ratio | Measured | Measured |
| Win rate / expectancy / profit factor | Measured, with sample size | Measured, with sample size |
| Sharpe / Sortino | Show only when meaningful | Show only when meaningful |
| Gross exposure / cash available | Isolated | Isolated |
| Risk rejections / emergency stops | Logged | Logged |
| Agent, sentinel and data-feed status | Health check | Health check |

Also provide **overlaid normalized equity curves starting at $50,000**, drawdown curves, comparison against SPY buy-and-hold, a detailed per-trade/order audit table, timestamps for every event, raw export to CSV/JSON, and a daily comparison report.

**Every portfolio must be visibly labeled:**

- `REAL MOOMOO PRACTICE PAPER FILLS` only after actual supported paper orders are placed and reconciled;
- `SHARED PRACTICE ACCOUNT — TWO VIRTUAL $50K SUB-LEDGERS` when using the shared-account fallback;
- `SHADOW / HYPOTHETICAL FILLS` when no actual broker paper orders occurred.

The dashboard must **never label a shadow backtest as actual executed results**.

---

## 8. Required acceptance tests and release gates

Before autonomous paper execution, demonstrate and record:

- [ ] Actual paper account IDs and environments verified; **no live account can be addressed by the executor**.
- [ ] Correct $50,000 beginning ledger for each strategy, independently recalculated from events.
- [ ] Both strategy engines match hand-calculated example signals and timestamps.
- [ ] Historical backtests complete, with transaction costs and clearly disclosed limitations.
- [ ] Unsupported trade order types are not used; ORB stop implementation tested under adverse movement.
- [ ] One-share or smallest-valid **practice-only** order lifecycle verified: submit, cancel, actual fill, position, exit, and reconciliation.
- [ ] If shorting is used, practice short entry, negative position verification, and buy-to-cover tested.
- [ ] Partial fills, order timeouts, duplicate retry prevention, stale data, restart recovery and missing heartbeat tests pass.
- [ ] Daily loss, drawdown ladder and terminal stop tested on synthetic NAV sequences; terminal stop cannot re-arm without explicit new authorization.
- [ ] Cloud schedule, local sentinel, market-close flatten and early-close handling tested; an EOD report appears.
- [ ] Dashboard figures reconcile to broker practice orders **or** are clearly labeled as virtual/hypothetical.

Use an explicit per-portfolio runtime state: `BLOCKED_SETUP`, `SHADOW`, `PAPER_READY`, `PAPER_ACTIVE`, `RISK_HALTED`, `TERMINATED`. **No transition to `PAPER_ACTIVE` unless all relevant tests pass.** If tests fail, report the failure instead of enabling execution.

---

## 9. What you must deliver

Create these **actual files and working outputs**, not just prose:

1. A repository/project with runnable Python code, reproducible dependencies, modular strategy/risk/execution packages, unit/integration tests, and clear configuration.
2. Moomoo paper-account capability report: two distinct practice accounts if truly supported; otherwise the verified fallback and precise limitations.
3. A **$50k ORB strategy** and a **$50k SPY Noise-Area strategy** built and tested independently.
4. Historical backtest reports and a same-period, same-fee comparison. Include an explicit statement of what is *not* validated.
5. Qoder Desktop + Cloud Agent workflow with scheduled research, risk audit, simulated execution and reporting; versioned agent/skill configurations.
6. Local read-only comparison dashboard with equity curves, actual-vs-shadow labels, audit trail and CSV/JSON exports.
7. A working paper-order execution log **if safe authorized practice trading is possible**, otherwise a truthful explanation and shadow trade log.
8. Daily reports and a final reproducible comparison: **which strategy earned more net dollars, which produced better returns per unit risk, which had fewer execution failures, and which should be considered for the one official competition account**.
9. A `SETUP_AND_RUN.md` containing exact local commands, Cloud schedule setup, account-check procedure, start/pause/kill controls, credentials checklist and recovery steps.
10. A concise Qoder-generated presentation outline plus screenshots/evidence suitable for the challenge's **25% risk-adjusted return, 30% Qoder usage, 30% strategy/risk design and 15% reusability/documentation** rubric.

**Execution priority:** Account discovery & safety → implementation of BOTH engines → backtesting → account isolation → order lifecycle/risk tests → enable genuine practice-paper trading if verified → dashboard and daily measurement. Work on both strategies in parallel where feasible. Do not postpone one strategy until the other becomes profitable.

At the end, provide a factual table stating **CREATED / VERIFIED / BLOCKED**, actual paper account count, the two initial $50k allocations, which mode each portfolio uses (distinct paper account / shared practice account / shadow), tests passed or failed, current portfolio NAV/P&L, and the exact files and URLs for the running dashboard and agents. **Do not invent completed tests, accounts, actual trades, or performance.**

---

## Reference sources

**Official competition, rules and platform:**
- Participant handbook: https://moomoo-qoder-challenge-rgkelnyuzbm.qoder.website/
- Moomoo strategy slides: https://moomoo-strategy-briefing-j692pppjg7l.qoder.website/#s=1
- Qoder kickoff: https://ngynckpj.qwenwork.page/
- Moomoo Agent Hub: https://github.com/MoomooOpen/moomoo-agent-hub
- Moomoo API — paper trading: https://open.moomoo.com/cli/sim
- Moomoo API — list accounts: https://openapi.moomoo.com/moomoo-api-doc/en/trade/get-acc-list.html

**Primary research:**
- ORB study: https://concretumgroup.com/wp-content/uploads/2026/02/A-Profitable-Day-Trading-Strategy-For-The-U.S.-Equity-Market.pdf
- ORB replication: https://www.quantconnect.com/research/18444/opening-range-breakout-for-stocks-in-play/
- SPY Noise-Area research: https://concretumgroup.com/beat-the-market-an-effective-intraday-momentum-strategy-for-sp500-etf-spy/
- Independent SPY out-of-sample replication: https://github.com/PazSheimy/spy-intraday-momentum-oos

**Existing context documents, if available in this Qoder project:**
- `moomoo_qoder_strategy_review.md` — Qoder's initial ORB recommendation.
- `moomoo_qoder_strategy_validation_response.md` — Qoder's corrected ORB research baseline.
- `moomoo_qoder_strategy_second_opinion.md` — Claude's initial alternative.
- `moomoo_qoder_strategy_second_opinion_v2.md` — Claude's revised SPY-only alternative.

**Source constraint:** Do **not** open, download, transcribe or process any original workshop videos. Use only prepared notes, transcripts, slides and public text documentation. Verify current official rules and tool schemas; the handbook takes precedence when documents disagree.
