# Independent Qoder Rubric Audit — Windows/Fox

**Auditor session:** `6d2430d9-0252-5a11-acd3-56796d7b845a`

**Completed:** 2026-11-27T06:25:20Z

**Mode:** Read-only; no code, config, private-state, service, broker, or order changes.

## Verification basis

- Windows local HEAD: `21cefb5`; origin/main observed as `4172396`.
- Results JSON arithmetic reconciles: `176.04 − 187.91 − 29.85 = −41.72`; cash + market value = NAV; 9/13 wins = 69.23%; profit factor 1.2345; drawdown −0.2157%; orders `2 + 9 + 8 + 11 = 30`.
- Parity evidence verified locally: 1,479 sessions, 575,010 checks, zero mismatches.
- Cloud reporter evidence verified locally.

## Critical finding

The claim matrix marks code claims **verified** that exist only in Claude's uncommitted Linux working tree, not in the public repository. At Windows HEAD/origin there is:

- no `long_sales_only_at_profit` guard;
- no twin-ETF shorts;
- no `account_cash` fix;
- no $20k lots / $100k book (public config remains $80k / $10k / four-lot);
- old risk ladder (3.5% / 1.25% / 2%, not 7% / 2.5% / 4%);
- no `test_long_sales_only_at_profit.py` or `test_short_cash.py`;
- no `exit_backtest/` or `submission_assets/`.

The tenth Noise vote lives in `vote_live.py`, not shared `vote_rules.py` (nine shared rules). A judge cloning the public repository today gets a different agent than the submission describes.

## Skeptical scores

| Category | Weight | Score | Strongest evidence | Gaps costing marks |
|---|---:|---:|---|---|
| Risk-adjusted return | 25% | 4/10 | Broker-reconciled NAV, 30/30 terminal orders, low drawdown, honest regime split | Net −0.04%; 13 trades / 5 sessions; 5 manual exits (38%); open loss −$187.91 exceeds realized +$176.04; loss deferral; full-period backtest negative; no computable Sharpe |
| Qoder / Cloud usage | 30% | 5/10 | Desktop built code/dashboard/tests/report; verified read-only reporter; cross-device manifests | Cloud near-decorative: one 28-second pre-competition run on empty shadow ledgers; auditor/watchdog zero runs; no schedule/Vault at audit time; 27 Nov automations still scheduled |
| Strategy / risk design | 30% | 7/10 | Deterministic gates, per-lot guard, twin shorts, provenance SHA-256, append-only fsynced outbox, invalidation criteria, honest failure analysis | Loss-lock/halt evaluation suppressed on 160 short-open records; fix uncommitted; scheduler −15 unreconciled; “ten independent rules” overstated (SPY 10 / QQQ 9, correlated indicators) |
| Reusability / documentation | 15% | 6/10 | Runnable repo, config-driven design, 51-claim matrix, HTML QA, hash-manifested package, safety contract | Package/deck draft; exit research and assets absent from public repo; matrix locators point to uncommitted code; 1,248-test claim self-reported; one DOM test fails |

**Weighted total: 5.5/10.**

## Five highest-impact improvements

1. **Push Claude's working tree** — guard, twins, `account_cash`, $20k lots, doubled ladder, both test files, exit backtest, and submission assets — then re-verify every matrix locator against the pushed commit. This is the single biggest fix.
2. **Generate real Cloud usage evidence now** — run auditor/watchdog read-only, capture run IDs and outputs, and fold the 27 Nov automation results into the report.
3. **Reconcile the 25 Nov scheduler −15 versus execution through 15:55 ET** and annotate the equity curve's 160-record risk-evaluation blind window.
4. **Complete the final-session refresh** — 27 Nov close NAV/P&L/logs, full test rerun with Node available, refreshed package and checksum.
5. **Strengthen return presentation** — per-session NAV table, computable risk metrics, prominent disclosure of 5/13 manual exits and loss deferral, and remove “independent” for correlated vote rules.

## Auditor conclusion

The submission's honesty is its strongest asset. The gaps are closable, but as audited the public repository did not yet substantiate the described agent.
