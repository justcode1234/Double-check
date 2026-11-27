# Independent Qoder Rubric Re-Audit — Windows/Fox

**Auditor session:** `6d2430d9-0252-5a11-acd3-56796d7b845a`

**Completed:** 2026-11-27T06:47:53Z

**Mode:** Read-only fetch and verification; no code, config, private-state, broker, order, or push changes.

## Repository verification

- `origin/two-vote-20k-stacking-dashboard` confirmed at `6b3ffa7c40908889a43f57df292ad1f4dfdd05da`.
- All 11 previously missing items verified present: long-sale guard, twin shorts, `account_cash`, $100k/$20k config, doubled risk ladder, both new test files, exit backtest, submission assets, report/deck/PDF/HTML, 51-claim matrix, and broker-order crosswalk.
- Claim-matrix locators matched the pushed branch.
- The critical public-repository mismatch was resolved on the feature branch.
- `main` was subsequently fast-forwarded to the same commit and pushed, resolving the default-branch mismatch as well.

## Re-scores

| Category | Weight | Previous | New | Basis |
|---|---:|---:|---:|---|
| Risk-adjusted return | 25% | 4 | 4.5 | Same −0.04% net and short sample, but fully evidenced with broker-ID trade log, equity curve, and public exit research. |
| Qoder / Cloud usage | 30% | 5 | 5.5 | Desktop plus completed peer-audit evidence; Cloud competition-period outputs remain unavailable. |
| Strategy / risk design | 30% | 7 | 8 | Gates, guard, twins, fix, tests, and locators are public; scheduler −15 and historical blind window remain disclosed. |
| Reusability / documentation | 15% | 6 | 7 | Public matrix, deck/PDF, package hashes, and watchdog remediation; final-session and manifest refresh remain. |

**Weighted total: 6.2/10**, up from 5.5/10.

## Remaining gaps

1. Complete the 27 November 13:00 ET final-session refresh across results, logs, charts, deck, package, and checksum.
2. Cloud automations remain `scheduled_outputs_not_exposed`; no completed Cloud output is claimed.
3. Reconcile or formally annotate the 25 November scheduler −15 process-history discrepancy.
4. Resolve or explicitly retain the Node-dependent DOM test limitation.
5. Complete the six user-owned form fields and consents before submission.
