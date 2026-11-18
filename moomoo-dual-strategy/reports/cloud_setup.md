# Cloud orchestration — READ-ONLY ROLES DEPLOYED (on-demand); pipelines NOT DEPLOYED

## Status and boundary

Local contracts unchanged: versioned `config/cloud_workflows.yaml`, `src/dual_strategy/agents/roles.yaml`, offline `specs.py`, and `tests/test_cloud_specs.py` remain **own schema, NOT API-ready payloads**; their orchestration pipelines/schedules are still inactive with empty tool allowlists. The deployed Cloud resources below are separate read-only roles, not those pipelines.

**2026-11-17T19:08Z — QCA discovery VERIFIED (fresh session):** `list_accessible_workspaces` → one personal workspace `ws_00vnsoy7hph4w8q5g6op` (`can_write: true`); `list_models` → 17 enabled models; `list_vaults` → **empty** (no Moomoo OAuth Vault). Pre-existing agents/environments/deployments: none.

**2026-11-17T19:09–19:10Z — read-only resources CREATED (real returned IDs):**

- Environment `env_00vsofk17gyypde68qh5` (`dual-strategy-readonly-env`): limited networking, `allow_mcp_servers=false`, no package managers, no allowed hosts.
- Agents (model `efficient`, version 1, tools Read/Glob/Grep only, no MCP servers, no skills): reporter `agent_00vsofk9gmsxsdkn9xzn`, auditor `agent_00vsofkg6v4sgevl8nuw`, watchdog `agent_00vsofkm5mbcw3wabuff`.
- Deployments (all `schedule: null` = manual-only, status active, no vaults): reporter `dep_00vsohxz2pds0rlsfz9m`, auditor `dep_00vsohy62x1j4nhmbtt8`, watchdog `dep_00vsohycvn3eo0rgykf0`.

**2026-11-17T19:11–19:12Z — one manual reporter run VERIFIED:** run `drun_00vsol73mss8x3gddi35`, session `sess_00vsol73uaha84oej553`, trigger manual, created 19:11:43Z; final `agent.message` `evt_00vsoltbka6m8bs6jv1o` processed 19:12:11.544Z; session ended `idle`, `error: null`; active ≈28.0 s; usage 0.23 credits. Output: a truthful zero-trade SHADOW report (no winner, account ID redacted, broker cash excluded) — full text and metadata in [evidence/cloud-reporter-run.json](../evidence/cloud-reporter-run.json). Auditor and watchdog deployments were created but **never run**. No schedule exists, so the XNYS Nov 26 closure / Nov 27 13:00 ET early close impose no firing risk; any future schedule must respect them.

Both orchestration portfolios remain `BLOCKED_SETUP`; underlying strategy configs remain `SHADOW`, USD 50,000 virtual allocation each. Ledger/execution/reporting integration and acceptance evidence are incomplete; paper transport remains disabled. The local SQLite ledger is initialized and independently replayed at $50,000 per strategy, with $0 P&L and no experiment orders. One ordinary practice account was verified and explicitly authorized; its last observed $1,000,000 broker NAV is not strategy capital. Cloud roles hold **no Moomoo tools or credentials**; they read only the redacted snapshots supplied in their deployment events. A Cloud container has no Moomoo credentials unless a Moomoo OAuth Vault is set up and attached — not done, and not required for these read-only roles.

## Responsibilities

Coordinator schedules separate envelopes; researcher explains supplied read-only snapshots. A generates deterministic stock-only ORB proposals; B independently generates SPY-only Noise-Area proposals. Risk supervisor and auditor may veto, never approve or override deterministic rejection. Watchdog only requests cancel/close/block/alert; no direct order tools. Reporter reads redacted evidence without changing trading state. Paper executor is a **disabled local boundary**, not Cloud discretion over money.

Cloud threads share environment/filesystem and session-bound Vaults: directory ownership is **not account isolation**. Exact private account mappings, per-strategy ledgers/NAV, no cross-subsidy, and broker guards remain mandatory; a shared practice account still shares margin/netting.

## Inactive schedule specifications

All times use `America/New_York`, guarded by XNYS, only November 17–28, 2026. Sessions: 17, 18, 19, 20, 23, 24, 25, 27. November 26 is closed; 27 closes at 13:00.

| Purpose | Regular session | November 27 |
|---|---|---|
| Research | 09:00 | 09:00 |
| A closed-minute proposals | 09:35–10:29 | Same |
| B closed-half-hour proposals | 10:00–15:30 | 10:00–12:30 |
| Risk audit / watchdog | Every 5 / 1 minute during session | Stop before 13:00 |
| Flatten / independent verification | 15:55 / 15:58 | 12:55 / 12:58 |
| Read-only report | 16:10 | 13:10 |

Flatten/verification are **close-relative**, not an unconditional 15:55 cron. Verification failure alerts and blocks; never claim flat without reconciled evidence. After-close reporting cannot trade.

Cloud startup/tool latency is unmeasured. Runs expire after at most 60 seconds; skip expired ticks, block entries, alert. No catch-up orders. The independently supervised **local sentinel cannot be replaced by Cloud**; missing heartbeat blocks entries. These timings promise no stop latency.

## Deterministic validation, not execution

Raw SHA-256 pins cover both strategies, risk config and role contracts; drift requires explicit review/re-pinning, never automatic acceptance. A canonical aggregate hash accompanies snapshots. Signals require the expected completed bar and quote/account/sentinel/bar ages ≤5/15/5/90 seconds; future/naive timestamps fail. Symbol metadata alone does not prove instrument classification.

Keys include strategy/date/symbol/signal/workflow/original scheduled time/config hash. Retries reuse keys and reconcile first. Persistent atomic deduplication, account/symbol concurrency and cross-config duplicate checks are **not implemented here**. Validation success never approves trading.

From the project root, run locally:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider tests/test_cloud_specs.py
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/Scripts/python.exe -B -m dual_strategy.agents.specs
```

Results: **105 specification tests passed**; adding `tests/test_calendar.py`: **113 passed, 10 subtests passed, 4 dependency deprecation warnings**. CLI validation passed. No lifecycle fills or deployed behavior tested.

## Manual blocker and later prerequisites

**Done 2026-11-17:** Qoder Cloud Agents plugin installed; QCA tools verified callable in a fresh session; read-only roles deployed and one reporter run verified (real IDs above). Availability is not authentication.

**Remaining prerequisite (only for future broker-connected Cloud roles):** Forward Mode → Vault → Create → Add custom MCP → MCP OAuth, URL `https://mcp.moomoo.com/mcp`; leave token/client credentials empty, complete OAuth, explicitly attach the Vault. Desktop login is not Cloud authorization. Secrets belong only in server environment/Vault, never browser application code, source, logs or reports ([authentication](https://docs.qoder.com/cloud-agents/api/conventions/authentication.md), [Vaults](https://docs.qoder.com/cloud-agents/vaults.md)). Not performed; the current read-only roles need no Vault. No schedule may be registered without explicit authorization and XNYS calendar review (Nov 26 closed; Nov 27 closes 13:00 ET).

## API distinctions and release gate

[Managed](https://docs.qoder.com/cloud-agents/multi-agents.md): `POST /api/v1/cloud/agents`; pin numeric child versions in `multiagent.agents`. [Deployment creation](https://docs.qoder.com/cloud-agents/api/deployments/create.md): `POST /api/v1/cloud/deployments`, required `name`, `agent`, `environment_id`, `initial_events`. Omit `schedule` for manual-only; adding one starts firing immediately. [Manual run](https://docs.qoder.com/cloud-agents/api/deployments/run.md): `POST /api/v1/cloud/deployments/{id}/run`.

[Forward](https://docs.qoder.com/cloud-agents/api/forward/schedules/create.md) is DISTINCT: `/api/v1/forward/schedules`, Identity/Template/environment; omitted trigger policy is manual. Never mix families. [CLI](https://docs.qoder.com/cli/cloud-mode.md) `/remote-env` is interactive; `qoder --remote` launches Cloud work. [Local CLI schedules](https://docs.qoder.com/cli/scheduled-reference.md) expire after seven days and have jitter: unsuitable safety protection.

Future activation needs explicit authorization, verified returned IDs/versions/Vault attachment/run history, account isolation, deterministic strategy/backtest/risk tests, adverse stop/heartbeat/restart tests, reconciled practice lifecycle, trustworthy reporting, and holiday/early-close evidence. Pause/kill must block entries locally while retaining close-only supervision; restart must preserve NAV/high-water/terminal state and reconcile. Never transition to `PAPER_ACTIVE` on these spec tests alone.
