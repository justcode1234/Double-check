# Dual strategy safety contract

The competition paper account is authorized by the user on 2026-11-20 for scripts/run_competition.py only; reset/revive cards and live accounts remain forbidden. Existing strategy-review documents describe older settings; the current configuration uses ORB 0.10 daily ATR, no profit target, and SPY-only Noise-Area.

Use independent USD 50,000 event-ledgers. Broker cash is not strategy capital. Never mix hypothetical data or fills with broker-reconciled results. Account IDs and credentials belong only in gitignored private storage; redact exported evidence. Do not commit or publish without the user's request.

All financial decisions, sizing, calendar checks and risk gates are deterministic Python. LLM agents may explain or veto, never override a rejection. Before paper activation require verified practice selection, historical validation, real paper lifecycle reconciliation and independently supervised close-only monitoring. Default transport is disabled. Missing data, heartbeat, account state or ambiguous order acknowledgements must block entries. Reconcile before any retry.

Cloud roles and schedules are deployment specifications until actual returned Cloud IDs, versions, run histories and OAuth Vault attachment have been verified. A Desktop session or local process is not a Cloud Agent. Schedules are bounded executions, never a substitute for continuous local risk supervision.

Preserve initial capital, day-start NAV, high-water NAV and terminal halts across restarts. Do not reset state to make a test pass. Keep research datasets, test fixtures and forward execution ledgers separate. Never fetch or process original workshop videos.

Run tests with `.venv/Scripts/python.exe -m pytest` on Windows or `.venv/bin/python -m pytest` on macOS. Only one machine may run a trading runner against the practice account at a time. Verify the browser dashboard and failed/stale states before calling a UI change complete. Treat short samples as demonstrations, not established alpha.

Communication: be concise. Lead with a direct yes/no or the result, then at most a few lines of context. No long reports or repeated status walls; the user wants brief answers.
