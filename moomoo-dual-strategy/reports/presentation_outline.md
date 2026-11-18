# Challenge presentation outline

**Format:** six slides, approximately five minutes; text sources and genuine evidence only. Snapshot: 2026-11-17 UTC. Do not present proposed schedules, test fixtures or hypothetical fills as deployed behavior or actual trades.

## 1. A controlled comparison, not a profit claim

- A: individual-stock five-minute ORB. B: SPY Noise-Area intraday momentum.
- Equal independent $50,000 virtual allocations; common calendar and modeled fees.
- **0 new accounts; 1 verified, explicitly authorized ordinary U.S. practice account.** Competition account excluded.
- Shared-account fallback shares broker cash/margin/netting. The observed $1m broker balance is not either strategy's capital.

Evidence: `reports/account_capability_report.md`, `evidence/capabilities.json`; no raw account identifiers.

## 2. Strategy and risk design — 30%

- ORB: prior-session ATR/RVOL, stock-only selection, top 8 candidate adaptation; 0.10×daily ATR stop from executed entry, no fixed profit target.
- Noise-Area: SPY only, prior 14-session bands, closed half-hour decisions, target-position/VWAP interpretation, 40% NAV notional.
- Both long-only/unlevered; independent NAV limits, 1.25% daily loss lock, 2%/3.5%/5% drawdown ladder with persistent hysteresis/terminal state.
- No native paper stop exposed. Local close-only supervision is required but not running; stop loss/latency is not guaranteed.

Evidence: `config/orb.yaml`, `config/noise_area.yaml`, `config/risk.yaml`, deterministic strategy/risk code. Show controls as implementation, not proven broker protection.

## 3. Qoder workflow and boundaries — 30%

- Desktop: implementation, source inspection, deterministic tests, ledger audit and local dashboard with read-only exports and guarded local Stop controls.
- Versioned local Cloud role contracts: coordinator, researcher, signal engines, veto-only risk/auditor, disabled paper-executor boundary, watchdog and reporter (still inactive local specifications).
- **READ-ONLY ROLES DEPLOYED 2026-11-17:** reporter/auditor/watchdog agents v1 plus three on-demand deployments (real IDs in `reports/cloud_setup.md`); one manual reporter run verified (≈28 s active, 0.23 credits) producing a truthful zero-trade SHADOW report (`evidence/cloud-reporter-run.json`). No schedules, no Vault, no trading tools in Cloud; orchestration pipelines remain local-only.
- Remaining: Moomoo OAuth Vault for any future broker-connected Cloud role; schedule registration needs explicit authorization and XNYS calendar review (Nov 26 closed; Nov 27 closes 13:00 ET).

Evidence: `src/dual_strategy/agents/roles.yaml`, `config/cloud_workflows.yaml`, `reports/cloud_setup.md`. Show no invented agent URL or deployment screenshot.

## 4. Results and research honesty — 25%

| Forward ledger | Current NAV | Total P&L / return | Orders / fills | Mode |
|---|---|---|---|---|
| ORB | $50,000 | $0 / 0% | 0 / 0 | SHADOW |
| SPY Noise-Area | $50,000 | $0 / 0% | 0 / 0 | SHADOW |

- Initial capital is not performance. No win rate, risk-adjusted winner or competition recommendation is supportable.
- Strict ORB remains blocked (0 completed trades): strict point-in-time metadata is unavailable, corporate actions are unverified and executable tick stops are unavailable. No release approval.
- Independent full-code, read-only/Decimal review verified 29 real-data **hypothetical** runs, 100,595 events and 591 fills, with report/hash/SQLite/source/raw-data consistency. Sample: 33 full sessions / 14 warmup / 19 usable (IS 8 / walk-forward 5 / holdout 6).
- Hypothetical P&L / completed trades: technical AAPL/NVDA **+$89.892463 / 7**; B40 **−$270.858452 / 13**; SPY **−$2,029.295168 / 1**. Short-sample research is not a winner or actual broker performance; published statistics are not our results.
- Explain common fees ($0.99/order plus capped sell levy), spread/slippage assumptions and unknown broker fees. Annualized ratios are suppressed below 60 observed sessions.

Evidence: local `status`/`audit` output and `reports/daily-2026-11-17.json`. Verified `reports/backtest_b85841adb51f96b7/comparison.json` is byte-identical to published `reports/backtest_comparison.json`; the runner publishes the dashboard summary. Forward ledgers were untouched by research/QA and remain separate.

## 5. Demonstration and audit evidence

- Verified demo URL: **http://127.0.0.1:8876/**, served with `.venv/Scripts/python.exe -m dual_strategy.cli serve --port 8876` from the project root.
- Show both $50k NAV cards, SHADOW/zero-fill labels, unavailable sample statistics, feed not connected and Cloud read-only-roles-only (no orchestration, no schedules). No independently connected sentinel exists.
- Per-strategy Start is disabled and server-side release-blocked, with explanatory blockers; no resume/start activation endpoint exists. Stop persists an idempotent manual `RISK_HALTED` lock without resetting state or clearing terminal halts. It sends **no broker cancel/close** and does **not** kill the dashboard.
- Controls require same-origin `Origin`, a per-server CSRF token and small, exact JSON. JSON/CSV exports remain read-only; explain durable intents, UNKNOWN/cancel-pending reserves and reconciliation before retrying.
- Browser QA verified both cards, disabled Start, usable Stop and a scoped durable lock with one audit event on an isolated QA database. Synthetic failed POST: unconfirmed warning, one attempt/no automatic retry; recovery worked.
- Append-only hashes detect inconsistency; they are not cryptographically tamper-proof against the file owner.
- **`evidence/dashboard.png` — genuine updated desktop capture**, taken with isolated headless Edge and visually inspected. `evidence/dashboard-mobile.png` is a verified 390px-viewport capture without document-level horizontal overflow. Demonstrate failures only with isolated fixtures, never by altering the forward ledger.

Do not demo Stop or `pause`/`kill` casually: they alter durable local state, do not close broker positions, and terminal kill cannot rearm. No trading start/activation command exists.

## 6. Reusability, documentation and next gates — 15%

- `README.md` and `SETUP_AND_RUN.md`: locked dependencies, exact commands, tests, account checks, private-evidence handling and recovery without reset.
- Calendar evidence: Nov 26 closed; Nov 27 closes 13:00 ET, flatten specification 12:55 and verification 12:58. These schedules are not deployed services.
- **Full suite: 842 tests and 55 subtests passed; 4 dependency warnings, no failed/skipped tests.** These tests do not verify a real paper lifecycle. Browser layouts passed at 320/390/768/1440px; exports, filters and failed/stale recovery were verified.
- **Supervised practice lifecycle verified once (agent-relayed):** BUY 1 SPY @ 731.04 then close-only SELL @ 731.11 on the authorized ordinary practice account, both reconciled from hashed broker history; broker flat afterwards. Plus one non-marketable BUY LIMIT 700 reconciled WORKING → cancelled → CANCELLED, 0 fills. Isolated validation ledgers, not strategy performance (`evidence/practice-lifecycle.json`).
- Next gates: release-quality history, forward runner integration, authenticated supervised transport, partial-fill/reject/UNKNOWN broker evidence, measured adverse stop/restart tests, and Cloud orchestration/Vault evidence (read-only Cloud run evidence now exists). Desktop/mobile screenshots and isolated Stop success/failure/recovery evidence are verified.

**Closing message:** two genuine local $50k ledgers and an auditable research implementation; autonomous practice execution remains blocked. No live trading, competition-account activity, reset, fabricated fill or unverified performance claim.
