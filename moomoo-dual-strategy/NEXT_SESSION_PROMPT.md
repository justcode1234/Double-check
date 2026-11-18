# Next-session prompt — continue the dual-strategy paper-trading goal

Paste everything below the line into a new Qoder chat opened in the `moomoo-dual-strategy` folder (Windows PC or the Mac mini).

---

Continue this project. The authoritative specification is `spec/prompt.md` ("Qoder Implementation Prompt — Two Competing Paper-Trading Agents ($50,000 Each)"); the current official challenge handbook takes precedence when documents disagree. Answer the user concisely: yes/no or the result first, a few lines at most.

## Read first (current state lives in the repo, not in chat history)

- `AGENTS.md`, `README.md`, `SETUP_AND_RUN.md` (section 1 has macOS setup)
- `reports/final_audit.md`, `reports/release_gates.md`, `reports/supervised_trading_authorization.md`, `reports/cloud_setup.md`
- `evidence/practice-lifecycle-cli.json`, `evidence/sentinel-cli-run.json`, `evidence/broker-reject-probe.json`, `evidence/supervised-b-decisions.json`

Python: `.venv/Scripts/python.exe` (Windows) or `.venv/bin/python` (macOS). Full suite: `python -m pytest -q -p no:cacheprovider` — last result 2026-11-18: **861 passed, 55 subtests, 4 dependency warnings**.

## Verified state as of 2026-11-18 ~17:15 UTC

- **Strategies:** A = 5-minute stock ORB, 0.10×prior-daily-ATR stop, no fixed target. B = SPY Noise-Area, 14-session lookback, VWAP, 40% exposure. Independent $50,000 sub-ledgers. Main ledger `private/ledger.sqlite3` is still **SHADOW** ($50k each, $0 P&L, 0 orders); PAPER_ACTIVE has never been enabled.
- **Account:** 1 authorized shared ordinary U.S. practice account (SIMULATE, market 100, account_type 1). Its ID is only in gitignored `private/accounts.json`.
- **Transport:** authenticated Moomoo CLI v0.5.0 (`src/dual_strategy/execution/moomoo_cli.py`, quote-only OAuth is enough for `sim` orders). OAuth must be redone on each machine.
- **PoC (2 paper trades), done:** `evidence/practice-lifecycle-cli.json` — BUY 1 SPY → close-only SELL, broker flat, both fills reconciled; plus a WORKING→CANCELLED path.
- **Sentinel:** `src/dual_strategy/risk/sentinel_service.py`, run against the broker: ~4.2 s/tick via CLI polling — fine for B, too slow for ORB's tight stops.
- **Backtest (research only, hypothetical fills):** `reports/backtest_comparison.json`, 19 usable sessions (2026-10-21 to 2026-11-16), A on AAPL/NVDA, B on SPY. B −0.54%, A-technical +0.18% (7 trades), SPY buy-hold −4.06%. `release_validated: false`; too short to prove alpha. Inputs are committed in `data-cache/normalized/`.
- **Supervised B paper trading:** authorized by the user 2026-11-18 (`reports/supervised_trading_authorization.md`). The tool trades, not the agent: `scripts/run_supervised_b.py --execute` on isolated `private/supervised-b/supervised.sqlite3`; dashboard `python -m dual_strategy.cli --db private/supervised-b/supervised.sqlite3 serve --port 8877`.
- **Cloud:** read-only roles (reporter, auditor, watchdog) deployed on-demand; pipelines not deployed (`reports/cloud_setup.md`).
- **External blockers:** strict-A/release validation needs point-in-time universe, market cap, sector and survivorship data, verified corporate actions and ≥60 sessions.

## Next actions

1. On a new machine: follow `SETUP_AND_RUN.md` section 1, sign in to the Moomoo CLI, and run the full suite. `private/` does not come via Git; copy it out-of-band to keep ledger state, never reset it.
2. Never run a trading runner on two machines at once.
3. Continue supervised tool-driven B paper trading within the authorization; keep the main ledger gated.
4. Keep docs, evidence and dashboard labels truthful; re-run the suite after changes.

## Hard constraints (do not relax)

- Paper only:
  - no real money, live-order tools or real-account permissions
  - no funds transfers, reset/revive cards or leverage
  - no duplicate identities and no bypassing Moomoo rules
- **Never touch, trade, reset or revive the competition account** ("Moomoo x Qoder AI Paper Trading Agent Challenge", account_type 2).
- Never guess account IDs. Never reveal secrets/tokens or the practice account ID in public artifacts. Secrets belong only in Vault or server environment.
- A must never spend B's virtual cash or vice versa. If shared broker buying power is short, reject or reduce orders.
- Never reintroduce a 1.0×ATR stop or a fixed 2R target. Never claim the 0.10×ATR stop guarantees a maximum loss.
- Gating:
  - No ORB paper auto-execution unless its monitored stop process passes acceptance tests.
  - **No PAPER_ACTIVE transition unless all relevant account, historical-validation, order-lifecycle and safety gates demonstrably pass.** If tests fail, report the failure.
- The dashboard must never label shadow/backtest/validation results as actual strategy executions.
- Do not open, download, transcribe or process workshop videos; use text sources only.
- **Do not invent tests, accounts, deployed agents, schedules, fills or performance.**
- Proceed autonomously with non-destructive work. If a step needs a Moomoo app action, account authorization or unavailable credentials, state the exact single action required and continue everything else.
- Do not mark the goal complete until a full completion audit verifies every requirement.
