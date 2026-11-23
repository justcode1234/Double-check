# Setup and run

## Competition launcher

Install Python 3.12 or later, then run the launcher from the repository root. On Debian and Ubuntu, install the matching `python3-venv` package if Python's `venv` support is missing.

| Platform | Command |
| --- | --- |
| Linux | `python3 start.py` |
| Windows | `py start.py` |
| macOS | `python3 start.py` |

On first launch, the app creates a local `.venv`, installs the locked dependencies, and opens the H ETF and Two-vote dashboard in your browser. The first setup needs internet access. Later launches reuse the local environment and any running dashboard from the same checkout. Logs are in `private/launcher/` inside the project.

Each checkout can open the dashboard independently. To show broker positions and current market signals on a device, install the [official Moomoo CLI](https://github.com/MoomooOpen/moomoo-cli#install) and sign in on that device. The Two-vote sleeve publishes signals. For a headless launch, add `--no-browser` to print the loopback URL. The ordinary launcher starts the dashboard only on every platform.

For a console-free Windows launch, run `py start.py --install-shortcut` once. Open **Moo Moo Dashboard** from the desktop afterward. The shortcut uses the project’s `pythonw.exe`, and errors are recorded in `private/launcher/launcher.log`.

### Always-on dashboard on Linux

Run `python3 start.py --enable-background --no-browser` to install and start the `moomoo-dashboard.service` systemd user service. The service uses `http://127.0.0.1:8876/` and restarts the dashboard and its vote monitor if either exits unexpectedly. If a manually started dashboard already uses that port, stop it before installing the service. User lingering keeps the service running after logout and starts it at boot.

Use `systemctl --user status moomoo-dashboard.service` to inspect it and `journalctl --user -u moomoo-dashboard.service` for logs. To stop it and remove it from automatic startup, use `systemctl --user disable --now moomoo-dashboard.service`. The dashboard displays account data and signals. Paper execution has its own scheduler and preflight below.

## Linux migration

The Linux dashboard requires only the launcher setup above. When resuming an existing competition paper runner on Linux, preserve its saved execution state and let a single machine run that account.

```bash
git clone --depth 1 --branch main https://github.com/justcode1234/Moo-Moo.git
cd Moo-Moo
python3 start.py
```

Install the [official Moomoo CLI for Linux](https://github.com/MoomooOpen/moomoo-cli#install) and complete `moomoo auth login` as the Linux user that will run the service. A fresh checkout can serve the dashboard before sign-in. For paper execution, make the machine switch outside the trading session.

1. Disable scheduling on the old host and verify the trading loop has finished. Only one host may run the competition account.
2. Back up `private/` before pulling this branch into an existing checkout because older revisions tracked some local files. Privately copy `moomoo-dual-strategy/private/competition/account.json`, `live/`, and any existing `scheduler/YYYY-MM-DD.json` decisions from the old host. Preserve the state, outbox, audit history, and halts. Use owner-only permissions. The shallow clone above excludes the older revisions, and the Linux user creates their own OAuth session.
3. From `moomoo-dual-strategy/`, run `.venv/bin/python scripts/run_competition.py status --execute` as a read-only check. Verify the broker holdings match the saved sleeves and review any unresolved orders or halts before arming the timer.
4. From the repository root, run `moomoo-dual-strategy/.venv/bin/python start.py --enable-paper --no-browser`. This registers the `moomoo-competition-start.timer` user timer and reuses the scheduler’s authorized session dates, startup window, and broker reconciliation. Keep the Linux machine powered on and awake, with its systemd user manager running.

Inspect the timer with `systemctl --user list-timers moomoo-competition-start.timer`. Review startup with `journalctl --user -u moomoo-competition-start.service`. Disable future launches with `systemctl --user disable --now moomoo-competition-start.timer` and verify any active run has finished before switching hosts again.

## Practice research

**Scope:** Windows Git Bash or macOS, local research/dashboard only. Both portfolios are `SHADOW`; paper execution is disabled. The ordinary practice account is authorized, but broker lifecycle, independent supervision, historical release validation and Cloud deployment remain blocked. Read [AGENTS.md](AGENTS.md) first. Never address live or competition accounts, reset balances, or overwrite private state.

## 1. Environment

All commands below run from:

```bash
cd "C:<user>/Documents/Life/Coding/github/Moo Moo/moomoo-dual-strategy"
```

The existing `.venv` uses **Python 3.14.7**; reuse it. `pyproject.toml` declares Python ≥3.11, but the locked environment should be reproduced with the recorded Python version. Node.js is used by dashboard behavior tests, not by the dashboard server itself.

For a **fresh checkout only**, with Python 3.14.7 available as `python`:

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.lock
.venv/Scripts/python.exe -m pip install --no-deps -e .
mkdir -p private reports data-cache
```

**macOS fresh checkout** (Python 3.14 as `python3`). Everywhere below, use `.venv/bin/python` instead of `.venv/Scripts/python.exe`:

```bash
cd moomoo-dual-strategy
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip install --no-deps -e .
mkdir -p private reports
.venv/bin/python -m pytest -q
```

Install the macOS Moomoo CLI from the official repository and sign in on that machine (`moomoo auth status --format json` to verify); OAuth tokens do not transfer between machines. `private/` (account mapping, ledgers, sentinel state) is gitignored: copy it from the original machine out-of-band (never via Git) to preserve NAV/high-water/halt state. `data-cache/normalized/` (backtest inputs) is committed. Never run a trading runner on two machines at once.

`requirements.lock` pins runtime/test dependencies; editable installation uses `pyproject.toml`'s setuptools build backend. Record Python/build-tool versions as well when reproducing results; this is not a hash-locked offline build. Do not replace an existing environment or regenerate the lock merely to hide a failure. Installation was not rerun for this documentation update.

No virtual-environment activation is needed with `.venv/Scripts/python.exe`. `.env.example` is a checklist, not an implemented dotenv loader: the CLI does not read `DUAL_STRATEGY_DB` or enable trading from that file. Use `--db` for a database override; setting an execution environment variable is not activation.

## 2. Inspect or initialize local state

**This workspace is already initialized.** Start with the read-only commands, not a new database:

```bash
.venv/Scripts/python.exe -m dual_strategy.cli --help
.venv/Scripts/python.exe -m dual_strategy.cli status
.venv/Scripts/python.exe -m dual_strategy.cli audit
```

Defaults resolve to this source project and `private/ledger.sqlite3`. Global options go **before** the subcommand:

```bash
.venv/Scripts/python.exe -m dual_strategy.cli --project . --db private/ledger.sqlite3 status
```

Only for a new, deliberately selected experiment database with its parent directory already present:

```bash
.venv/Scripts/python.exe -m dual_strategy.cli init
```

`init` creates missing local $50,000 allocations and SHADOW states; it neither creates broker accounts nor enables orders. Existing initial capital/state is preserved. It is **not a reset or recovery command**. Never delete/replace the ledger to remove losses, uncertain orders or terminal locks.

Verified on 2026-11-17 UTC: each ledger NAV $50,000, P&L $0, no positions, no orders/fills, execution disabled, sentinel `NOT_RUNNING`, feed `NOT_CONNECTED`, Cloud `READ_ONLY_ROLES_DEPLOYED` (read-only roles only; orchestration pipelines not deployed). `audit` returned valid event/observation checks and no errors. An empty-order audit is **not** a broker lifecycle test. `status` and `audit` read local evidence; they do not refresh the broker account.

## 3. Dashboard and reports

```bash
.venv/Scripts/python.exe -m dual_strategy.cli serve --port 8876
```

Verified browser URL: **http://127.0.0.1:8876/**. Use this explicit port; do not interfere with unrelated local applications.

This starts only the loopback dashboard, not a signal runner, sentinel or order executor. Per-strategy Start is disabled and server-side blocked by the release boundary, with explanatory blockers; no resume/start activation endpoint exists. Stop persists an idempotent manual `RISK_HALTED` lock for the selected strategy, without resetting state or clearing terminal halts. It **does not send broker cancels/closes or kill the dashboard**. No independently connected sentinel exists. Controls require a same-origin `Origin`, a per-server CSRF token and small, exact JSON. Ctrl+C stops the dashboard process but does not flatten anything.

Available read-only routes:

- `/api/status` — current local snapshot.
- `/export/status.json` — JSON comparison export.
- `/export/audit.csv` — event export.
- `/export/equity.csv` — strategy equity export.

Generate a local report explicitly:

```bash
.venv/Scripts/python.exe -m dual_strategy.cli report
```

Writes/updates `reports/daily-<UTC-date>.json`, `reports/orb-equity.csv` and `reports/noise_area-equity.csv`. The filename uses the command's **UTC date**, not a selectable exchange session; there is no `report --date`. Preserve reports before rerunning if a prior snapshot must remain unchanged. Reporting is not proof of Cloud scheduling or broker reconciliation.

Verified browser QA: two $50k cards, disabled Start and usable Stop; successful Stop produced a scoped durable lock with one audit event on an isolated QA database. A synthetic failed POST produced an unconfirmed warning after one attempt, with no automatic retry; recovery worked. The forward database was untouched: both strategies remain `SHADOW`, $50,000 each, $0 P&L and 0 orders/fills.

**`evidence/dashboard.png` and `evidence/dashboard-mobile.png` are genuine desktop and 390px-viewport captures**, taken with isolated headless Edge and visually inspected. The mobile viewport was measured through browser device emulation: no document-level horizontal overflow; the audit table scrolls within its container. Browser exports, filters and stale/error recovery were checked; automated tests verify export redaction. Never modify the forward ledger to simulate UI failures, expose the server publicly, or include raw account IDs, credentials or private observations in screenshots/exports.

## 4. Calendar, holiday and early close

```bash
.venv/Scripts/python.exe -m dual_strategy.cli calendar --date 2026-11-17
.venv/Scripts/python.exe -m dual_strategy.cli calendar --date 2026-11-26
.venv/Scripts/python.exe -m dual_strategy.cli calendar --date 2026-11-27
```

These commands were checked. Expected results:

| Date | Session close | Flatten time | B decisions |
|---|---|---|---|
| Nov 17 | 16:00 ET | 15:55 ET | 10:00–15:30, half-hourly |
| Nov 26 | Closed; null bounds | None | Empty |
| Nov 27 | 13:00 ET | 12:55 ET | 10:00–12:30, half-hourly |

Use XNYS and `America/New_York`, never weekday-only cron or a fixed Malaysia/Singapore offset. Challenge-window sessions are Nov 17, 18, 19, 20, 23, 24, 25 and 27. Calendar output is a schedule calculation, **not an order or automatic flatten service**.

## 5. Research and tests

Offline research entry point:

```bash
.venv/Scripts/python.exe -m dual_strategy.cli backtest
```

It reads `config/backtest.yaml`, `data-cache/normalized/intraday_5m.csv` and `data-cache/normalized/daily.csv`. The data covers 33 full sessions: 14 warmup and 19 usable across predeclared in-sample (8), walk-forward (5) and holdout (6) partitions. The configured comparison dates span Oct 21–Nov 16, 2026. Parameters must not be tuned on the holdout.

The runner writes content-addressed `reports/backtest_<digest>/comparison.json`, per-run JSON files and `data-cache/backtest-<digest>.sqlite`, **separate from `private/ledger.sqlite3`**, and publishes `reports/backtest_comparison.json` for the dashboard. Content-addressed artifacts cannot be replaced with conflicting content. Independent full-code, read-only/Decimal review verified **29 real-data hypothetical runs, 100,595 events and 591 fills**, including report/hash/SQLite/source/raw-data consistency. `reports/backtest_b85841adb51f96b7/comparison.json` is byte-identical to the published dashboard comparison.

| Research comparison | Hypothetical P&L | Completed trades |
|---|---|---|
| Technical AAPL/NVDA | +$89.892463 | 7 |
| B40 | −$270.858452 | 13 |
| SPY | −$2,029.295168 | 1 |
| Strict ORB | Blocked | 0 |

These results do not approve release or establish a winner.

Strict historical ORB is blocked by missing point-in-time universe, sector/market-cap/tradability/delisting evidence and verified corporate actions. The AAPL/NVDA technical diagnostic is not that baseline. Authentic five-minute data still cannot verify executable spreads, tick paths or stop latency; executable tick stops are unavailable and SPY corporate-action treatment is also unverified. All research fills are hypothetical. See [README.md](README.md) for modeled fees, limits and sample-size restrictions. Predetermined stresses cover delays, partial fills, missed ticks, stale data, disconnection, sentinel failure and deliberately failed overnight/holiday protection; they are not observed broker failures or live safety tests.

Full local suite:

```bash
.venv/Scripts/python.exe -m pytest
```

Focused checks (existing test modules):

```bash
.venv/Scripts/python.exe -m pytest -q tests/test_signals.py tests/test_risk.py tests/test_sentinel.py
.venv/Scripts/python.exe -m pytest -q tests/test_ledger.py tests/test_execution.py tests/test_audit.py
.venv/Scripts/python.exe -m pytest -q tests/test_backtest.py tests/test_dividends.py
.venv/Scripts/python.exe -m pytest -q tests/test_calendar.py tests/test_cloud_specs.py
.venv/Scripts/python.exe -m pytest -q tests/test_dashboard.py tests/test_reporting.py
```

To suppress bytecode/cache artifacts, prefix with `PYTHONDONTWRITEBYTECODE=1`, add Python `-B`, and pytest `-p no:cacheprovider`. Tests use synthetic/temporary evidence where applicable; the dashboard tests include a Node DOM harness, which does not replace real-browser QA. **Full combined suite verified on 2026-11-17 UTC: 842 passed, 55 subtests passed, 4 dependency deprecation warnings.** No tests failed or were skipped. The warnings originate in exchange_calendars/pandas timezone conversion; passing tests do not certify broker transport, actual lifecycle or stop latency.

## 6. Account checks and credentials

No credentials are needed for local status/dashboard/research. Existing Desktop MCP authorization is **not** an independently authenticated Python service or Cloud Vault.

Before any future broker-connected release:

1. Inspect the current authenticated **paper-only** tool schemas. Read `sim_trade_account_list`; listing does not create accounts. Match the ordinary U.S. practice selection and explicit user authorization in `private/accounts.json`; deny live and competition accounts. Current source evidence is the simulated endpoint, Paper Trading title, market ID 100 and account type 1; it does not contain a literal broker `SIMULATE` field. The executor's SIMULATE guard must be backed by verified provenance, not a guessed value.
2. Refresh only that practice account with `sim_trade_cash_info`, `sim_trade_position_list` and `sim_trade_history_order_list`, using schema-required arguments. Retain timestamped raw responses privately. Reconcile exact mapping, working orders, cumulative fills, positions and available cash; do not infer freshness from the old report.
3. Keep $50k strategy budgets distinct from the last observed $1m broker balance and shared margin/netting. No balance changes, reset cards or duplicate identities. Additional same-market account creation remains unverified; ask Moomoo support rather than invent an app flow.
4. Verify supported market/limit types, a real smallest-valid practice submit/cancel/fill/position/exit lifecycle, and adverse stop/restart behavior before release. No native paper stop or dedicated paper fill/fee endpoint was exposed. Shorts remain disabled without a separate verified short/cover lifecycle.
5. Obtain a separately supervised, authenticated **practice-only** transport and continuous close-only service. Restrict secret material to private storage/server environment/Vault. Never place credentials in source, browser code, reports or logs. No such running service exists now.

The local executor persists intent before submission; ambiguous acknowledgement stays `UNKNOWN`. Applicable reserves remain held for `SUBMITTING`, `UNKNOWN`, `WORKING`, `PARTIAL` and `CANCEL_PENDING`; cancellation response alone is not final cancellation. Poll/reconcile before any retry, preserving the original identity. Broker order notes are not idempotency keys. Never blindly resend or release a reserve to make a discrepancy disappear.

## 7. Start, pause, kill and recovery

**Unavailable:** there is no trading `start`, `resume`, `rearm` or `PAPER_ACTIVE` activation CLI command, nor a resume/start activation endpoint. Dashboard Start remains disabled with server-side release blockers. State-machine APIs are not an operational activation procedure. `serve` cannot activate trading; do not edit YAML/state or fabricate gate evidence to bypass this boundary.

Dashboard Stop is a per-strategy, idempotent manual `RISK_HALTED` lock, not a broker stop or process shutdown. It preserves terminal locks and never resets state. Manual controls below also **change durable local risk state**; do not run them as harmless smoke tests:

```bash
.venv/Scripts/python.exe -m dual_strategy.cli pause --strategy all
```

`pause` sets a local `RISK_HALTED` lock (an existing terminal lock stays terminal). For an intentional terminal stop:

```bash
.venv/Scripts/python.exe -m dual_strategy.cli kill --strategy all
```

`kill` sets `TERMINATED`; the current state machine cannot rearm it. Either command accepts `orb`, `noise_area` or `all`. Both **lock local entries only**: neither sends broker cancels nor closes positions. `flat_verified` refers only to local positions/working orders; `broker_flat_verified` remains false.

Recovery checklist:

- Block entries and preserve SQLite state, raw observations, unresolved intents and configuration hashes. For backup, use a consistent SQLite backup or a quiesced database with its required journal/WAL files, not an arbitrary copy during writes.
- Run `status` and `audit`; a missing/corrupt database or hash mismatch requires investigation, not `init`, deletion, reset or automatic re-pinning.
- Refresh/reconcile authorized practice orders and positions before retrying anything. Unknown submission/cancel outcomes remain blocked with reserves retained.
- If actual practice exposure is discovered while supervision is unavailable, verify the selected ordinary practice account in the authorized app and arrange cancel/close-only handling; confirm broker-flat status separately. Do not claim these CLI controls handled it, and never touch the competition account.
- Preserve initial/day-start/high-water NAV and terminal state across restarts. Nonterminal recovery requires explicit next-session approval, recovery hysteresis and complete release evidence; no CLI rearm is available. Terminal stop overrides recovery and cannot be cleared by restarting.
- Revalidate fresh data/account/sentinel state, adverse stop latency, lifecycle reconciliation and early-close protection before considering activation. Restarting the dashboard restores views/local controls only; it neither clears locks nor activates execution.

The append-only hash chain/raw-observation checks provide application-level integrity checks, **not cryptographic tamper-proofing against the database owner**. Keep private evidence access-controlled and redact exports before publishing.

## 8. Cloud setup — READ-ONLY ROLES DEPLOYED (on-demand)

**Done 2026-11-17 (fresh session):** QCA tools verified callable; read-only reporter/auditor/watchdog agents (version 1, Read/Glob/Grep only, model `efficient`), environment `env_00vsofk17gyypde68qh5` and three manual-only deployments created in personal workspace `ws_00vnsoy7hph4w8q5g6op`; one manual reporter run verified (≈28 s active, 0.23 credits, truthful SHADOW output). Real IDs and run evidence: [reports/cloud_setup.md](reports/cloud_setup.md), [evidence/cloud-reporter-run.json](evidence/cloud-reporter-run.json). No schedules, no Vault, no Moomoo/trading tools in Cloud. Local YAML remains a project-owned schema, not a deployment payload; orchestration pipelines are NOT deployed.

Trigger a read-only run from a QCA-connected Qoder session (MCP tool, not a local CLI): `mcp__qca__run_deployment` with `deployment_id` `dep_00vsohxz2pds0rlsfz9m` (reporter), `dep_00vsohy62x1j4nhmbtt8` (auditor) or `dep_00vsohycvn3eo0rgykf0` (watchdog); then read `list_deployment_runs` / `list_session_events` (`agent.message`). Runs are bounded and read-only; they cannot trade.

A Moomoo OAuth Vault is required only for future broker-connected Cloud roles: Forward Mode → Vault → Create → Add custom MCP → MCP OAuth, URL `https://mcp.moomoo.com/mcp`; leave token/client credentials empty, complete OAuth and explicitly attach the Vault. Desktop login/plugin installation alone is insufficient. Not performed. Any future schedule needs explicit authorization and XNYS review (Nov 26 closed; Nov 27 closes 13:00 ET). See [reports/cloud_setup.md](reports/cloud_setup.md) for official text references and API-family distinctions.

Inspect local contracts without creating Cloud resources:

```bash
.venv/Scripts/python.exe -m dual_strategy.agents.specs
.venv/Scripts/python.exe -m dual_strategy.agents.specs --preview 2026-11-27
```

Future schedule specifications, all **disabled**, use XNYS / `America/New_York`:

| Purpose | Regular session | Nov 27 early close |
|---|---|---|
| Research | 09:00 | 09:00 |
| A proposals | 09:35–10:29, closed minutes | Same |
| B proposals | 10:00–15:30, closed half-hours | 10:00–12:30 |
| Risk audit / watchdog | Every 5 / 1 minute in-session | Truncated before 13:00 |
| Flatten / independent close verification | 15:55 / 15:58 | 12:55 / 12:58 |
| Read-only report | 16:10 | 13:10 |

Cloud startup/stop latency and cost are unmeasured. Runs are bounded to 60 seconds in the local spec, with one automatic attempt and expired-tick skipping; never catch up missed orders. Missing local heartbeat blocks entries. Cloud watchdogs cannot substitute for continuous local supervision. Model/platform/data fees and quotas must be checked before deployment; no zero-cost or latency guarantee is implied. Configuration hash drift requires review, not automatic acceptance.

## 9. Isolated decision-only forward runner

`dual_strategy.forward` is a bounded **decision journal**, not a paper executor, backtest, continuous feed or additional funded portfolio. It never imports broker execution, places orders or creates hypothetical fills. Its separate $50k reference balances do not alter the actual experiment allocations. Do not point it at `private/ledger.sqlite3`; existing unrelated ledgers are rejected rather than adopted.

```bash
.venv/Scripts/python.exe -m dual_strategy.forward --journal data-cache/shadow-decisions.sqlite3 init
.venv/Scripts/python.exe -m dual_strategy.forward --journal data-cache/shadow-decisions.sqlite3 import-bars --help
.venv/Scripts/python.exe -m dual_strategy.forward --journal data-cache/shadow-decisions.sqlite3 tick --help
```

Imports require canonical UTF-8 CSV columns `symbol,end,open,high,low,close,volume`, optionally `turnover`, at most 5 MiB. Supply explicit price/turnover/volume divisors, 1- or 5-minute interval, start/end timestamp semantics, adjustment policy, source identifier and `RECORDED` or `SYNTHETIC` provenance. Prefer aware ISO timestamps; naive timestamps require an explicit source timezone. The research CSV's epoch timestamps and extra `minutes` column must be explicitly converted, not passed off as this canonical schema. Imported bytes, raw/normalized hashes and conventions are immutable ledger events. Neither provenance label authenticates provider origin.

Use `tick --strategy noise_area --tick <aware-ISO-closed-tick> --observation <hash>` with one `--observation` per nonoverlapping prior-history/current-prefix import. The process uses its actual reception time, requires causal data, expires new ticks after 60 seconds and will not catch up missed orders. B uses the existing 14-completed-session, closed-half-hour, genuine-turnover VWAP engine. Its bar-close quantity is **diagnostic only**: corporate actions remain unverified, the zero-dividend assumption is explicit, approved quantity is always zero, and no shared broker buying power has been checked. Strict A records a rejection until point-in-time universe and metadata inputs are implemented and verified; stock candles alone cannot manufacture eligibility.

MARK, signal, risk rejection and the immutable decision/restart cursor commit atomically. Identical retries return the persisted decision, never another action; changed input references for the same tick, backwards ticks, fabricated evidence, state corruption and version drift fail closed. Source/configuration/locked-dependency hashes pin each journal. Unsupported parameter changes are rejected, not silently interpreted. Preserve journals after code changes rather than re-pinning or resetting them. No automatic rearm exists.

Read a decision journal with `dual_strategy.cli --db <journal> status` or `serve --port <unused-loopback-port>`. Existing local `pause`/`kill` controls use its pinned decision configuration and preserve terminal state; they still do not send broker operations. Trading `init` refuses decision journals. Raw CSV bodies are omitted from public dashboard/export audit rows; proof hashes and provenance remain visible.

The [current K-line documentation](https://open.moomoo.com/api/quote/realtime/cur-kline.md), checked 2026-11-17, explicitly says the latest candle's close uses the **current price** and does not specify start/end timestamp semantics. A successful current-candle request is therefore not closed-bar proof. No current-candle poller or authenticated streaming feed is claimed. Fresh synthetic observations show `SYNTHETIC`, recorded observations show `RECORDED_UNVERIFIED`, stale valid data shows `STALE`, and bad/missing proof shows `UNVERIFIED`; none is labeled a connected live feed.

`init` builds and verifies the journal in a temporary sibling file, then publishes it with a same-directory hard link that never replaces an existing file; an interrupted initialization leaves no destination and can be retried. The digest refuses to describe a project root other than the loaded package source. Verified exercises, none of them provider evidence or trades:

- `evidence/forward-recorded-qa.json`: recorded-history CLI run on an isolated journal. A rejected (`strict_orb_point_in_time_universe_and_metadata_unavailable`), B `not_decision_tick`; 14 events, 0 orders/fills, both $50,000 / $0 P&L; identical retries; actual forward ledger unchanged.
- `evidence/forward-browser-qa.json`: isolated synthetic browser QA showing `SYNTHETIC`, `RECORDED_UNVERIFIED`, `STALE` and `UNVERIFIED` badges, decision-only provenance label, redacted exports, scoped Stop (16→17 events), Start HTTP 409 without mutation.

## Supervised practice-order lifecycle (agent-relayed)

`scripts/supervised_lifecycle.py` drives one 1-share US.SPY round trip through the real `PaperExecutor` on the isolated `private/lifecycle/validation.sqlite3` ledger. It writes `private/lifecycle/relay/NN-request.json`; the supervising Qoder session calls exactly that paper-only Moomoo MCP tool and writes `NN-response.json` (240 s expiry; an expired request is never executed). It refuses a non-empty relay directory, so each run needs a fresh directory and ledger.

Verified 2026-11-17 ([evidence/practice-lifecycle.json](evidence/practice-lifecycle.json)): BUY limit 732.67 filled 1 @ 731.04, close-only SELL limit 729.66 filled 1 @ 731.11, both reconciled from stored history observations; broker SPY qty 0 afterwards; 9 hashed observations, chain/consistency/lifecycle audit OK. Gross +$0.07, common challenge fee model $1.980166, validation-ledger realized −$1.910166; the sim tools reported no broker fees. Provider origin is not independently authenticated, and partial/reject/cancel/UNKNOWN paths were not exercised against the broker. The strategy comparison ledger still has 0 orders/fills.

## Release gates still blocking autonomous paper execution

- Release-quality historical validation for each precise variant, including action-aware data; strict ORB PIT universe evidence.
- Long-only supervised practice lifecycle (fill round trip) and one WORKING→CANCELLED broker cancel are verified (agent-relayed; `scripts/supervised_lifecycle.py --cancel` uses `private/lifecycle-cancel/`); still required: authenticated-transport lifecycle, partial-fill/reject/UNKNOWN broker evidence and, if ever enabled, verified short/cover lifecycle.
- Independently authenticated transport, supervised close-only sentinel, measured adverse stop/heartbeat/restart/timeout behavior.
- Current hand-calculated signal, risk/drawdown/terminal, partial-fill and duplicate-prevention test evidence tied to configuration hashes.
- Verified Cloud IDs/Vault/run history and functioning holiday, early-close flatten/verification and reporting integration.
- End-to-end authenticated execution/dashboard reconciliation remains unverified. Local full-suite, desktop/mobile layout, outage recovery, export redaction and isolated Stop success/failure/recovery checks are verified; they are not broker lifecycle or stop-latency evidence.

Account authorization and the two verified initial ledgers resolve only those prerequisites. They do not satisfy the remaining gates. Use text notes, transcripts, slides and public documentation only; never process original workshop videos.

**2026-11-17 session assessment:** no remaining broker-side gate was closed this session. Authenticated Python transport and the continuous close-only sentinel are blocked on practice-scoped Moomoo credentials; the exact single action needed is to complete Moomoo OAuth (or run OpenD locally) and place the practice-scoped token only in private storage/server environment, never in source or reports. Partial-fill/reject/UNKNOWN broker evidence was not obtained and is not obtainable through the current supervised machinery without code changes: the executor fails closed locally on invalid price/qty/symbol-prefix and cash exhaustion, and reconciliation only supports instruments {US.AAPL, US.SPY} (`execution/broker_history.py`, `read_history_order`), so a deliberate broker-side reject requires a reviewed extension of the reconciliation allowlist plus UNKNOWN→REJECTED handling and adverse-path tests — not a probe order that bypasses guards. Short-side probes remain prohibited. Forward shadow runner integration is unchanged; its existing isolated QA evidence stands. Cloud read-only deployment/run evidence was added (section 8); orchestration, Vault and schedules remain absent. No gate was marked passed without evidence.

**2026-11-18 update (supersedes the transport blocker above):** authenticated practice-only transport is established via the official Moomoo CLI (v0.5.0, signed installer; **quote-only** OAuth — sim order placement/cancellation were verified to need no live `trade:write`). `execution/moomoo_cli.py` (`CliTransport`) and `scripts/supervised_lifecycle_cli.py` produced an authenticated, broker-reconciled 1-share SPY round trip (BUY @732.15 → close-only SELL @732.09 → flat) and a submit→WORKING→CANCELLED lifecycle on isolated ledgers (`evidence/practice-lifecycle-cli.json`); the practice account was left flat after every run. Two broker-data quirks were fixed and regression-tested: whole-second timestamp granularity grace at request/observation boundaries (strict sub-second ordering preserved), and indicative `avg_fill_price` normalized to 0 when `cum_qty=0`. A continuous close-only sentinel service (`risk/sentinel_service.py`) is built and unit-tested but not yet running against the broker. **Activation remains blocked** by release-quality historical validation (point-in-time data), a running authenticated sentinel with measured stop latency, and broker partial-fill/reject/UNKNOWN evidence. Full map: `reports/release_gates.md`. The competition account was never addressed; no gate was marked passed without evidence.

## 10. Supervised practice trading (tool-driven, user-authorized)

Under the documented user authorization (`reports/supervised_trading_authorization.md`), the **deterministic
tool** trades Portfolio B (SPY Noise-Area) on an isolated supervised ledger; the agent only supervises. This is
**not** a `PAPER_ACTIVE` transition of the main comparison ledger, which remains gated and untouched.

```bash
# Dry-run (prints decisions, places no orders):
.venv/Scripts/python.exe scripts/run_supervised_b.py --ticks 1 --work-dir private/supervised-b-dry
# Live supervised execution, bounded by ticks or duration:
.venv/Scripts/python.exe scripts/run_supervised_b.py --execute --ticks 40 --work-dir private/supervised-b
.venv/Scripts/python.exe scripts/run_supervised_b.py --execute --minutes 60 --work-dir private/supervised-b
# Watch the supervised ledger:
.venv/Scripts/python.exe -m dual_strategy.cli --db private/supervised-b/supervised.sqlite3 serve --port 8877
```

The runner computes the baseline Noise-Area signal (5-minute bars, 14 prior sessions + today's closed bars,
turnover VWAP), applies deterministic gates (40% instrument cap, ≤100% gross exposure, 1.25% daily-loss lock,
2/3.5/5% drawdown ladder), executes via the guarded `PaperExecutor` + authenticated `CliTransport`, reconciles
fills from broker history, and runs the close-only `SentinelService` backstop. It enters only on a genuine
band + VWAP breakout and flattens before close. The signal is flat most ticks; no trade is forced.

Caveats: the CLI sentinel backstop runs at the loop cadence (~30 s heartbeat; ~4 s processing/tick), suitable for
B's half-hour cadence but **not** sub-second stop protection; A (tight 0.10×ATR stops) remains deferred until a
faster transport exists. Sim fills are actual paper fills in the shared practice account, not competition ranking.
The historical-validation release gate is relaxed by user decision for supervised practice only; full autonomous
activation still requires point-in-time historical data and the remaining gates in `reports/release_gates.md`.
