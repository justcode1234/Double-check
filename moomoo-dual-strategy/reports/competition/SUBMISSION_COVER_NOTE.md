# Moo-Moo submission cover note

## One sentence

Moo-Moo is a deterministic two-vote SPY/QQQ paper-trading agent with ten technical signals, per-lot profit-only exits, twin-ETF shorts, and broker-reconciled risk controls.

## Project description

Moo-Moo is a Qoder-built, deterministic paper-trading agent for the Moomoo × Qoder challenge. Ten technical rules vote on each completed five-minute SPY/QQQ bar (Noise-area is SPY-only); the agent acts only when at least two agree and fewer than two oppose. The indicators are correlated, so the threshold is a consensus filter rather than ten independent alpha sources. Entries use $20,000 lots inside a $100,000 book, a $40,000/39.5%-of-NAV single-name cap, no leverage, a 2.5% daily loss lock, 4% reduced sizing and a 7% terminal drawdown halt. Long lots are never sold at a loss; short exposure uses IVV/QQQM when the primary ETF is already long and is always covered by the close, lock or halt.

Every order is persisted as intent → acknowledgement → outcome and reconciled against Moomoo before the next order. The post-liquidation snapshot is NAV $100,393.24 (+$393.24, +0.3932%), drawdown -0.0704%, fully flat, with 33/33 broker orders matched, no open orders, no halt and no daily lock. The submission includes the strategy and invalidation rules, risk/compliance matrix, 1,477-session exit research, 1,479-session parity evidence, 1,251 passing tests with zero failures using Qoder's bundled Node runtime, a 51-claim evidence matrix, redacted decision/fill logs, dashboard captures, and a runnable package with SHA-256 manifest.

Qoder Desktop built the code, tests, dashboard, report and deck. Read-only Cloud reporter/auditor/watchdog deployments are documented with their limits: one reporter run is verified; the 17 November auditor/watchdog deployments had zero runs and no Vault. Two additional one-time Cloud automations were created on 27 November, but their outputs are not exposed to the local agent and are not claimed as completed evidence. Independent Windows and local Qoder audits did complete. The report explicitly covers what worked, what failed and why, including the 23 November partial-fill anomaly, 24–25 November short-cash NAV bug, and 25 November scheduler/restart incident. Independent Windows follow-up verified the public package and scored it 6.4/10; the lightweight package reproduces the local suite exactly in a clean directory. Final 27 November settlement refresh is pending.

## Links and uploads

- **Presentation link:** https://moo-moo-competition-7amelju7q1y.qoder.website
- **Editable deck:** `moomoo-dual-strategy/reports/competition/Moo-Moo-Submission-Draft.pptx`
- **PDF:** `moomoo-dual-strategy/reports/competition/Moo-Moo-Submission-Draft.pdf`
- **Public source repository:** https://github.com/justcode1234/Double-check
- **Runnable package (public, lightweight):** `moomoo-dual-strategy/reports/competition/dist/Moo-Moo-Runnable-Package.zip`
- **Public package manifest:** `moomoo-dual-strategy/reports/competition/dist/Moo-Moo-Runnable-Package-Manifest.json`
- **Public package checksum:** `moomoo-dual-strategy/reports/competition/dist/Moo-Moo-Runnable-Package.sha256`
- **Full evidence package:** `moomoo-dual-strategy/reports/competition/Moo-Moo-Submission-Draft-Package.zip`
- **Package manifest:** `moomoo-dual-strategy/reports/competition/Moo-Moo-Submission-Draft-Package-Manifest.json`
- **Checksum:** `moomoo-dual-strategy/reports/competition/Moo-Moo-Submission-Draft-Package.sha256`
- **Public runnable package:** `moomoo-dual-strategy/reports/competition/dist/Moo-Moo-Runnable-Package.zip`

## User-completed fields

- Full name
- Business email
- Country
- Phone number
- Consent to share project details with judges
- Marketing contact consent

## Final pre-submit checks

- [x] Host the presentation and paste the public URL: https://moo-moo-competition-7amelju7q1y.qoder.website
- [x] Incorporate Claude's final source/report diff; current `main` is public and aligned.
- [ ] Refresh after the 27 November regular-market close.
- [x] Re-run tests and update the exact count: 1,251 passed, 0 skipped, 0 failures, 59 subtests passed.
- [x] Rebuild both packages, include HISTORY.md, verify every packaged link and SHA-256, and neutralize the four non-packaged HTML links.
- [ ] Submit before **30 November 2026, 23:59 SGT**.
