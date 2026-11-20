# Moomoo dual-strategy practice experiment

A deterministic Python comparison of **A: stock-only five-minute Opening Range Breakout (ORB)** and **B: SPY Noise-Area intraday momentum**, with independent **$50,000 virtual allocations**. This is a practice-paper research project, not an official competition-account executor or evidence of profitable trading.

## Current status

Snapshot checked using local `status` and `audit` on **2026-11-17 UTC**. Account discovery evidence is in [the capability report](reports/account_capability_report.md).

| Item | Status | Evidence / limitation |
|---|---|---|
| New broker accounts | **BLOCKED — 0 created** | No account-creation operation exposed; a second same-profile U.S. practice account is unverified. Listing is not creation. |
| Ordinary U.S. practice account | **VERIFIED — 1**, explicitly authorized | Competition account excluded; exact mapping stays private. |
| Independent strategy ledgers | **CREATED / VERIFIED** | Two event-ledgers in `private/ledger.sqlite3`; initial capital independently replayed. |
| Engines, risk, execution/sentinel libraries, reporting | **CREATED** | Local code is not proof of a running broker-connected system. |
| Historical release validation | **BLOCKED** | Independent review verified 29 real-data hypothetical research runs; strict ORB still lacks point-in-time universe/metadata and verified corporate actions. Research is not release approval. |
| Supervised practice-order lifecycle | **VERIFIED — agent-relayed + authenticated** | 2026-11-17 (agent-relayed): BUY 1 US.SPY @731.04, close-only SELL @731.11, broker flat; plus BUY LIMIT 700 WORKING→CANCELLED (0 fills): [evidence/practice-lifecycle.json](evidence/practice-lifecycle.json). 2026-11-18 (**authenticated CLI**): BUY 1 US.SPY @732.15 → close-only SELL @732.09 → broker flat, 2 fills reconciled, `broker_lifecycle: true`; plus submit→WORKING→CANCELLED: [evidence/practice-lifecycle-cli.json](evidence/practice-lifecycle-cli.json). Isolated validation ledgers only, not strategy performance. Provider origin not independently authenticated. |
| Autonomous paper execution / stop latency | **BLOCKED (activation)** | Authenticated practice-only transport now exists (Moomoo CLI v0.5.0, quote-only OAuth; sim orders need no live trade scope). Continuous close-only sentinel service built + unit-tested (`risk/sentinel_service.py`) but **not running**; real stop latency unmeasured; broker partial/reject/UNKNOWN evidence pending. Full gate map: [reports/release_gates.md](reports/release_gates.md). |
| Cloud roles and schedules | **READ-ONLY ROLES DEPLOYED — 1 RUN VERIFIED; orchestration NOT DEPLOYED** | Real Cloud IDs and one verified manual reporter run: [cloud_setup.md](reports/cloud_setup.md), [evidence/cloud-reporter-run.json](evidence/cloud-reporter-run.json). On-demand only: no schedules, no Vault, no trading tools. Local orchestration specs remain inactive. |
| Dashboard | **DESKTOP / MOBILE CONTROL QA VERIFIED** | `http://127.0.0.1:8876/`; genuine `evidence/dashboard.png` captured and visually inspected. Start is blocked; Stop locks local entries only. |

| Portfolio | Runtime | Initial / current NAV | Realized / unrealized / total P&L | Experiment orders / fills |
|---|---|---|---|---|
| A — ORB | `SHADOW` | $50,000 / $50,000 | $0 / $0 / $0 | 0 / 0 |
| B — SPY Noise-Area | `SHADOW` | $50,000 / $50,000 | $0 / $0 / $0 | 0 / 0 |

**All execution is disabled.** The forward database was untouched by QA/research; there are also zero hypothetical fills in these forward ledgers. The label `SHADOW / HYPOTHETICAL FILLS` describes provenance/mode, not evidence that trades occurred. Initial capital and 0% return are not strategy performance; no winner or competition recommendation is established.

Separate research: independent full-code, read-only/Decimal review verified **29 real-data hypothetical runs, 100,595 events and 591 fills**, with report/hash/SQLite/source/raw-data consistency. There are 33 full sessions, 14 warmup and 19 usable (IS 8 / walk-forward 5 / holdout 6). Hypothetical P&L / completed trades: technical AAPL/NVDA **+$89.892463 / 7**, B40 **−$270.858452 / 13**, SPY **−$2,029.295168 / 1**; strict ORB **blocked / 0 trades**. `reports/backtest_b85841adb51f96b7/comparison.json` is byte-identical to published `reports/backtest_comparison.json`; the runner publishes the dashboard summary. Strict PIT metadata, verified corporate actions and executable tick stops remain unavailable; **no release approval or winner**.

The authorized fallback is **SHARED PRACTICE ACCOUNT — TWO VIRTUAL $50K SUB-LEDGERS**, not two broker accounts. Broker cash/NAV was **$1,000,000** before the supervised lifecycle validation and **$999,999.36** total assets mid-cycle (broker position profit +$0.07 after the round trip); it is **not strategy capital**. Broker cash, margin and netting remain shared. A cannot spend B's allocation; A uses individual stocks, B only SPY. Broker-reconciled paper-fill labeling is permitted only after actual supported orders and reconciliation.

## Strategy and risk design

| Control | A — ORB | B — Noise-Area |
|---|---|---|
| Signal | 09:30–09:35 ET range; green candle long breakout, red short only if verified; doji skips | Prior 14-session mean absolute open-to-slot move; bands anchored to today's open / previous close, VWAP confirmation |
| History / selection | Prior 14 completed sessions; open > $5, ADV ≥ 1m, ATR > $0.50, cap ≥ $1B, RVOL ≥ 1; top 8 rather than published top 20 | SPY only, VM 1.0; closed half-hour decisions 10:00–15:30 ET, truncated before early close |
| Current variant | Long-only; entries before 10:30 ET | Long-only target-position/VWAP interpretation, not a claim of exact published reproduction |
| Sizing | 0.25% own NAV risk ($125 initially); ≤15% per stock, ≤4 concurrent positions, ≤30% per sector | 40% own NAV notional ($20,000 initially); no assumed ETF exemption |
| Exit | 0.10 × prior daily ATR from executed entry; no fixed profit target or automatic trailing stop | Signal exits plus required independent catastrophe supervision |

Shared controls: own-NAV gross exposure ≤100%, no leverage, daily loss lock at 1.25% of own day-start NAV ($625 initially); halve exposure at 2% drawdown, halt at 3.5%, terminal stop at 5% of own high-water NAV. Reduced exposure has 1.5% recovery hysteresis; nonterminal rearming requires explicit next-session approval and release evidence. Terminal state cannot rearm. These are code/configuration controls, **not proven live protection**.

Only paper market/limit types were exposed; the executor currently constructs limit orders. No native paper stop was exposed. A 0.10×ATR stop does not guarantee a maximum loss. Five-second freshness thresholds and dashboard refresh are not measured stop latency. Short selling remains disabled pending separate short/cover verification.

**Costs:** comparison model charges $0.99 per order plus $0.000166 per share sold, capped at $8.30 per sell order. Model spread/slippage separately. Research config uses 1 bp spread and 1 bp normal / 10 bp stressed slippage, next-bar-open execution and a 1% closed-bar-volume participation proxy. These are assumptions, not executable quotes. Broker fees are unavailable where not observed; never substitute modeled fees for broker-recorded charges or silently mix the two P&L bases. No Cloud/data-service cost has been measured.

## Local use

Run from this project root in **Windows Git Bash**, using the existing virtual environment:

```bash
.venv/Scripts/python.exe -m dual_strategy.cli status
.venv/Scripts/python.exe -m dual_strategy.cli audit
.venv/Scripts/python.exe -m dual_strategy.cli serve --port 8876
```

**Backtest A/B/C (offline, SIMULATED):** `PYTHONUTF8=1 .venv/Scripts/python.exe -m dual_strategy.research.run` (~7 min) rebuilds `reports/backtest_v3/` from the committed `data-cache/history/` bars; view it at **http://127.0.0.1:8876/backtest**. Six-strategy study (A–F, Monte Carlo, B reconciliation, D/E/F options blockers): `PYTHONUTF8=1 .venv/Scripts/python.exe -m dual_strategy.research.six`, verdict in `reports/final_six_strategy_verdict.md`. Costs: competition fees + 1 bp/side for SPY, 3 bp/side for stocks, with a 0–15 bp sweep and break-even slippage per strategy.

Open the verified **http://127.0.0.1:8876/**. This loopback-only command starts a dashboard, **not trading**. Per-strategy **Start is disabled and server-side blocked by the release boundary**, with explanatory blockers; there is no resume/start activation endpoint. **Stop persists an idempotent manual `RISK_HALTED` lock**, without resetting state or clearing terminal halts. It neither sends broker cancels/closes nor kills the dashboard; no independently connected sentinel exists. Controls require a same-origin `Origin`, a per-server CSRF token and small, exact JSON. Status views and CSV/JSON exports remain read-only.

Browser QA verified two $50k cards, disabled Start, usable Stop, a scoped durable lock with one audit event on an isolated QA database, and a synthetic failed POST showing an unconfirmed warning after one attempt/no automatic retry; recovery worked. The genuine desktop screenshot `evidence/dashboard.png` was captured with isolated headless Edge and visually inspected. `evidence/dashboard-mobile.png` is a verified 390px-viewport capture; 320/390/768/1440px layouts passed browser measurements without document-level horizontal overflow. `evidence/dashboard-full.png` includes charts and the audit table. **The full combined suite passed: 842 tests and 55 subtests, with 4 dependency deprecation warnings.**

The UI refreshes every five seconds and displays independent NAV, provenance, health, audit events and available metrics; unsupported/sample-insufficient values remain unavailable. Equity is indexed to initial NAV = 100, corresponding to $50,000 each. No forward SPY benchmark is currently supplied.

See [SETUP_AND_RUN.md](SETUP_AND_RUN.md) for locked dependencies, exact CLI commands, tests, calendar, account checks and recovery. **There is no `start` command or `PAPER_ACTIVE` activation command.** `pause` and `kill` lock local entries only; they do not cancel or close broker positions.

## Architecture and evidence boundaries

- `src/dual_strategy/data/`, `strategies/`, `risk/`: deterministic data/calendar checks, signals, sizing and risk states; agents cannot override rejections.
- `forward.py`, `data/evidence.py`: separate, version-pinned decision-only journals with immutable CSV input evidence, atomic signals/risk/restart cursors and no order or fill creation. B computes diagnostic long-only targets; strict A logs missing PIT prerequisites. Neither recorded nor synthetic evidence authenticates a live feed. Initialization publishes atomically without replacing files. Isolated recorded-history and browser-label exercises are in `evidence/forward-recorded-qa.json` and `evidence/forward-browser-qa.json` (0 orders/fills). See the forward-runner section in [SETUP_AND_RUN.md](SETUP_AND_RUN.md).
- `execution/`, `portfolio/`: guarded paper-executor boundary, reconciliation, independent balances and durable order intents. `SUBMITTING` / `UNKNOWN` / `CANCEL_PENDING` retain applicable cash reservations; **reconcile before retrying**, never blindly resubmit a non-idempotent order.
- `backtest/`, `config/backtest.yaml`: isolated offline research; no writes to the forward ledger. Five-minute bars cannot verify tick paths, actual stop latency or corporate-action correctness. The short sample cannot establish alpha; annualized Sharpe/Sortino are suppressed below 60 observed sessions.
- `agents/roles.yaml`, `config/cloud_workflows.yaml`: **inactive local contracts**, not API-ready payloads or deployed agents. [Cloud setup](reports/cloud_setup.md) records the boundary and future requirements.
- `reporting/`, `dashboard/`: read-only ledger views and CSV/JSON exports, plus guarded local Stop controls. `reports/daily-2026-11-17.json` is a zero-trade snapshot, not an execution-success report.

SQLite events and raw broker observations use append-only triggers and SHA-256 checks. This detects inconsistency under the application contract; it is **not cryptographically tamper-proof against the file owner**, who can replace the database or recompute hashes. A successful empty-order audit proves local consistency, not a broker lifecycle. Raw observations and exact account mapping stay in gitignored private storage; inspect/redact exports before sharing. Never reset/reinitialize state to remove losses, unresolved orders or terminal halts.

## Challenge materials and sources

[Presentation outline](reports/presentation_outline.md) maps evidence to the 25% risk-adjusted return / 30% Qoder usage / 30% strategy-risk design / 15% reusability rubric. All performance and deployment gaps remain visible.

Cloud status: the Qoder Cloud Agents plugin is installed and QCA tools were verified callable in the 2026-11-17 fresh session; read-only reporter/auditor/watchdog deployments were created (on-demand, no schedules) and one manual reporter run was verified. A Moomoo OAuth Vault is still **not** attached; it is a prerequisite only for future broker-connected Cloud roles, not for these read-only roles. Installation is not Cloud/Vault authentication.

Use text documentation, prepared notes/transcripts and slides only; never fetch or process original workshop videos. The [official handbook](https://moomoo-qoder-challenge-rgkelnyuzbm.qoder.website/) takes precedence for challenge rules. Research references: [ORB paper](https://concretumgroup.com/wp-content/uploads/2026/02/A-Profitable-Day-Trading-Strategy-For-The-U.S.-Equity-Market.pdf), [SPY Noise-Area paper](https://concretumgroup.com/beat-the-market-an-effective-intraday-momentum-strategy-for-sp500-etf-spy/), [independent SPY replication](https://github.com/PazSheimy/spy-intraday-momentum-oos). Published statistics are not this project's results. Practice results do not count toward official competition ranking; that account remains out of scope.
