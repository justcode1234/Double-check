# Supervised practice-trading authorization (user decision)

**Date:** 2026-11-18 ~15:29 UTC (10:29 ET, regular XNYS session)
**Decision by:** the user, explicitly, in this session.

## What was authorized

The user authorized **supervised practice paper trading** on the single authorized ordinary U.S. practice
account (SIMULATE, market 100, type 1), starting with **Portfolio B (SPY Noise-Area)**. This is a deliberate,
documented **relaxation of the release-quality historical-validation gate** for supervised practice only. It is
**not** a claim that the gate passed; the point-in-time universe/metadata and verified corporate-action data
remain unavailable. The relaxation is recorded here as the user's informed decision.

## Scope and limits (unchanged hard constraints)

- **Practice account only.** The official competition account (type 2) is never touched, traded, reset or revived.
- **Supervised, not autonomous.** This is not a `PAPER_ACTIVE` runtime transition. Orders are placed under
  session supervision through the authenticated practice-only transport, with deterministic risk gates and the
  close-only sentinel as a backstop. The forward comparison ledger runtime remains gated; actual paper fills are
  broker-reconciled and labeled as such.
- **B first.** SPY only, long-only current variant, 40% of B's own $50,000 NAV notional, half-hour closed-bar
  decisions (10:00–15:30 ET; truncated before the Nov 27 13:00 early close). A (stock ORB) is deferred until a
  faster stop monitor exists.
- **Independent $50k sub-ledgers.** A cannot spend B's virtual cash; broker cash/margin/netting remain shared.
- Daily loss lock 1.25% of B's day-start NAV; drawdown ladder 2/3.5/5% of B's high-water NAV; terminal cannot re-arm.
- Flatten before 16:00 ET (13:00 ET on Nov 27); no overnight holding; no leverage; shorts disabled.

## Known caveat accepted by the user

The authenticated CLI sentinel polls at ~0.24 Hz (~4.2 s/tick). This is a catastrophe backstop suitable for B's
half-hour cadence, **not** sub-second stop protection. It is insufficient for A's tight 0.10×ATR stops; A remains
deferred. Real stop latency is not guaranteed.

## Provenance

Results from this supervised practice trading are **actual paper fills in one shared practice account**, distinct
from shadow/hypothetical research and distinct from official competition ranking. No performance claim or winner
is implied by starting trading.

## Live tool-driven execution (started 2026-11-18 ~15:45 UTC)

The trading is performed by the **deterministic tool**, not by the agent manually placing orders. The agent
supervises (launch/monitor/stop) only.

- Runner: `scripts/run_supervised_b.py --execute` (signal engine → deterministic risk gates → guarded
  `PaperExecutor` + authenticated `CliTransport` → broker-history reconciliation → close-only `SentinelService`).
- Isolated ledger: `private/supervised-b/supervised.sqlite3` (B $50,000; A untouched). The main comparison ledger
  `private/ledger.sqlite3` is **not** modified; no `PAPER_ACTIVE` transition of the main ledger.
- Live dashboard: `http://127.0.0.1:8877/` (`cli --db private/supervised-b/supervised.sqlite3 serve --port 8877`).
- Signal data: 5-minute SPY bars, autype 0, 14 prior complete sessions + today's closed bars; 5-minute turnover
  VWAP (documented deviation from preferred 1-minute).
- First decision 10:30 ET: flat (`inside_noise_or_vwap_filter`) → hold; no trade forced. The runner enters a
  ~40%-NAV SPY long only on a genuine band + VWAP breakout at a subsequent closed tick, and flattens before close.
- Sentinel backstop ~0.24 Hz (~4 s/tick, CLI overhead) — accepted for B's half-hour cadence; not sub-second stops.
- Dry-run verification preceded live launch; full test suite 859 passed.