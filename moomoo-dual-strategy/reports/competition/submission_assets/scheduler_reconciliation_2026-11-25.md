# 25 November 2026 Scheduler / Runner Reconciliation

**Status:** Operationally reconciled; exact restart initiator not recorded in available evidence.

## Finding

The native scheduler recorded `unexpected_exit` with return code `-15` at **10:57:04 ET**. The execution process did not disappear for the rest of the session. Audit provenance shows three runner lifetimes, followed by a completed daily report and `loop: session over` at the close.

| Evidence | Timestamp (ET) | Interpretation |
|---|---:|---|
| First provenance for session | 09:00:38 | Initial runner starts and writes provenance once. |
| Last audit before wrapper exit | 10:56:33 | Initial runner's final audit record. |
| Scheduler record ends | 10:57:04 | Scheduler wrapper receives SIGTERM (`-15`). |
| Second provenance starts | 10:57:22 | A new runner process starts after source hashes changed. |
| Last audit in second lifetime | 11:05:47 | Second runner stops writing. |
| Third provenance starts | 11:06:28 | A new runner process starts after the broker source hash changes. |
| Last audit in final lifetime | 16:15:29 | Final runner continues through the close. |
| Daily report generated | 16:20:06 | Report records 11 filled orders, zero order errors and zero runner errors. |
| Runner loop ends | after daily report | `loop: session over`. |

## Continuity checks

- **Broker orders:** no outbox event occurred in either restart interval; all 11 session fills remain attributed to terminal broker orders.
- **Risk-audit coverage:** two short telemetry gaps remain: **48.8 seconds** and **40.7 seconds**. No broker order was pending in either interval.
- **Source provenance:** restart 1 changed `run_competition.py` and `planner.py`; restart 2 changed `broker.py`. New provenance IDs prove new runner processes because provenance is cached for the lifetime of one runner.
- **Service model:** the systemd unit uses `KillMode=process`; terminating the scheduler wrapper does not itself prove that a detached runner child was killed.
- **Closeout:** the final runner completed the 15:55 ET flatten and daily report. The 25 November ledger and broker order crosswalk reconcile.

## Residual limitation

The available user journal and shell history do not identify who or what initiated the two runner restarts. The submission therefore does not claim unattended, gap-free supervision for 25 November. It claims the narrower verified result: no missing broker order, two disclosed audit gaps, and a completed closeout.

## Evidence locators

- `private/competition/scheduler/2026-11-25.json`
- `private/competition/live/audit/provenance/44cd7db2edfd44068915d4bb3113494b.json`
- `private/competition/live/audit/provenance/2006674bbb104c2f951987d4f3d2f66d.json`
- `private/competition/live/audit/provenance/e7cd253a2a78419e998287637605019e.json`
- `private/competition/live/audit/2026-11-25/*.json`
- `private/competition/live/outbox/2026-11-25.jsonl`
- `private/competition/runner-2026-11-25.log`
- `moomoo-dual-strategy/reports/competition/submission_assets/scheduler_reconciliation_2026-11-25.json`
