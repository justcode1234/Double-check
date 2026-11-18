# Release-gate map — spec section 8 (as of 2026-11-18 ~15:16 UTC)

Activation rule: **no `PAPER_ACTIVE` unless every relevant gate demonstrably passes.** This map ties each
spec section-8 acceptance item to concrete evidence. "VERIFIED" means real evidence exists; "BLOCKED"
names the exact missing prerequisite. Passing unit tests are code-health evidence, not broker proof.

| # | Acceptance item | Status | Evidence / blocker |
|---|---|---|---|
| 1 | Paper account IDs/environments verified; no live account addressable | **VERIFIED** | 1 authorized ordinary U.S. practice account (SIMULATE, market 100, type 1) via authenticated Moomoo CLI; competition account (type 2) in deny list, never addressed. `private/accounts.json` (ID private). Executor + CliTransport enforce allowlist/SIMULATE/market/type guards. |
| 2 | Correct $50,000 beginning ledger, independently recalculated | **VERIFIED** | Two event-ledgers replayed at $50,000 each; `cli audit` chain/observations verified, 0 errors. Comparison ledger `private/ledger.sqlite3` untouched by validation. |
| 3 | Engines match hand-calculated signals/timestamps | **VERIFIED** | Deterministic signal/risk/calendar tests in full suite (859 passed, 55 subtests). |
| 4 | Historical backtests complete, costs + disclosed limitations | **BLOCKED (release)** | Research backtests complete (29 real-data hypothetical runs) but **release-quality validation blocked**: no point-in-time universe/metadata, corporate actions unverified, executable tick stops unavailable. External data prerequisite; cannot be fabricated. |
| 5 | Unsupported order types not used; ORB stop tested under adverse movement | **VERIFIED** | LIMIT-only executor (market/stop rejected). `tests/test_sentinel.py` adverse stop-loss, inclusive boundaries, correct-side close (30 tests). |
| 6 | Smallest-valid practice lifecycle: submit/cancel/fill/position/exit/reconcile | **VERIFIED (authenticated)** | `evidence/practice-lifecycle-cli.json`: BUY 1 SPY @732.15 → close-only SELL @732.09 → broker flat, 2 fills reconciled from broker history, `broker_lifecycle: true`. Separate submit→WORKING→CANCELLED (0 fills) also verified. Transport `AUTHENTICATED_CLI`. Provider origin still not independently authenticated. |
| 7 | Short entry / negative position / buy-to-cover tested | **NOT VERIFIED** | Shorts disabled (long-only variants). An inadvertent sim short from an ad-hoc sell was covered to flat; a formal separate short/cover acceptance lifecycle is not recorded. Required only if shorts are ever enabled. |
| 8 | Partial fills, timeouts, duplicate retry, stale data, restart, missing heartbeat | **PARTIAL** | Unit tests verified: timeout→UNKNOWN and **broker-error acknowledgement→UNKNOWN** (parametrized, grounded in the live SERVER_ERROR probe) with retained reservation and no blind retry, restart latching, stale/missing heartbeat closes, duplicate-intent prevention, partial-fill reconciliation (`test_execution.py`, `test_sentinel.py`). Live broker behavior probed (`evidence/broker-reject-probe.json`): the permissive sim accepts invalid/non-marketable prices as WORKING (no status-6 REJECTED for valid symbols); unknown symbols return an API error with no order created → the UNKNOWN acknowledgement path. **Remaining design prerequisite:** a never-created UNKNOWN order has no broker-history row, so it cannot reach a terminal reconciled state under the current audit contract (every paper status must link to broker history); resolving it requires a reviewed "confirmed-absent" absence-proof extension. Not implemented; fail-closed UNKNOWN behavior is correct and tested. |
| 9 | Daily loss / drawdown ladder / terminal on synthetic NAV; terminal cannot re-arm | **VERIFIED** | `test_risk.py` / `test_sentinel.py`: 1.25% daily lock, 2/3.5/5% ladder, hysteresis, terminal-no-rearm across restart/new day. |
| 10 | Cloud schedule, local sentinel, close flatten, early close tested; EOD report | **PARTIAL** | Cloud read-only reporter/auditor/watchdog deployed + 1 reporter run verified (`evidence/cloud-reporter-run.json`); XNYS calendar/early-close tests pass; EOD zero-trade report exists. Continuous close-only sentinel **ran bounded against the authenticated broker** (5 heartbeats, chain verified, correctly blocks entries in SHADOW; `evidence/sentinel-cli-run.json`). Measured loop rate ~0.24 Hz (~4.2 s/tick, CLI overhead) — **too slow for tight stop protection**; triggered-stop quote-to-order latency still unmeasured. |
| 11 | Dashboard reconciles to broker practice orders OR clearly labeled virtual | **VERIFIED** | SHADOW / HYPOTHETICAL labels; browser desktop/mobile QA; exports redacted. No shadow result labeled as actual execution. |

## Exact external data requirement for release-quality historical validation

The single gate that cannot be closed autonomously is release-quality historical validation (spec §5). To close
it, the following **point-in-time (PIT)** data is required for the sample period (and a longer history for
meaningful Sharpe/Sortino):

1. **PIT US-equity universe per session** — for each historical date: symbol, close price, prior-14-session
   average daily volume, daily ATR(14), **point-in-time market cap**, **sector classification**, tradable/halted
   status, and delisting/survivorship flags. This is needed to apply the strict ORB filters (price>$5, ADV≥1M,
   ATR>$0.50, cap≥$1B, sector≤30%) without look-ahead or survivorship bias.
2. **Verified corporate actions** — splits and cash dividends with ex-dates, to make OHLCV and previous-close
   bands action-aware (the current research explicitly does not assume no dividends/splits).
3. **Longer continuous history** — ≥60 usable sessions for annualized Sharpe/Sortino; the current 19-session
   sample suppresses them and cannot establish alpha.

**Why it is external:** the free Moomoo CLI exposes current `basicinfo`/`financials` and historical K-lines, but
**not** historical point-in-time market cap, sector, or a survivorship-free universe. Those require a licensed
PIT dataset (e.g. CRSP, Norgate, Polygon/Tiingo fundamentals) or exchange filings. They cannot be fabricated or
derived look-ahead-safely from the available free data. Until sourced, strict ORB stays blocked and no portfolio
is release-validated; supervised practice trading proceeds under the user's documented authorization only.

## Activation decision

**`PAPER_ACTIVE` remains DISABLED.** Both portfolios stay `SHADOW`, $50,000 each, $0 P&L, 0 strategy orders.

Blocking prerequisites for activation (in priority order):
1. **Release-quality historical validation (#4)** — point-in-time universe/metadata + verified corporate
   actions. External data; cannot be resolved autonomously or fabricated. This alone blocks activation.
2. **Continuous authenticated close-only sentinel running with measured stop latency (#10)** — service is
   built and unit-tested; must run against the authenticated transport and pass adverse/latency acceptance.
3. **Broker partial-fill/reject/UNKNOWN evidence (#8)** — requires a reviewed reconciliation extension and a
   safe broker-reject exercise.
4. **Short/cover lifecycle (#7)** — only if shorts are enabled; current variants are long-only.

## Authenticated transport (new this session)

- Moomoo CLI v0.5.0 installed (signed Windows installer); OAuth **quote-only** scope. Sim account reads and
  **sim order placement/cancellation work without live `trade:write`** (verified empirically).
- `execution/moomoo_cli.py` (`CliTransport`): guarded practice-only transport, fail-closed normalization.
- Two broker-data quirks handled and regression-tested: whole-second timestamp granularity grace at
  request/observation boundaries (strict sub-second ordering preserved); indicative `avg_fill_price`
  normalized to 0 when `cum_qty=0`.
- Sim fill latency for marketable limits observed ~4–6 s; the supervised script polls up to 30 s before a
  fallback cancel. Ad-hoc sells on a flat sim account open shorts (sim permits shorting) — only close-only
  sells against own longs are used.