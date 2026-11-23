# Claude Code workspace handoff

Read the [project's repository instructions](AGENTS.md).

## Current task — review of the Mac competition runner

Claude stopped after the user asked to pull the Mac changes and double-check them. Codex fetched `origin/competition-b3-h` and verified that the checkout matched it at `6a89baa`. Review covered B3, H, Noise-area momentum, VWAP trend continuation and ORB breakout/breakdown. Each sleeve starts with 20% of verified initial NAV, or $20,000 on $100,000. The [five-strategy spec](QODER_FIVE_STRATEGIES.md) contains the corrected strategy selection.

The offline review reproduced three execution defects with synthetic account data and a fake broker. Treat them as blockers to certifying the runner for paper execution.

1. [Shared-account flatten and sleeve closes](src/dual_strategy/competition/planner.py) use sleeve quantities even when the broker nets opposite positions. Seed B3 long 10 SPY and Noise short 20. With broker net short 10, `plan_flatten` issues BUY 20 first, crossing to net long 10. A failed second leg can leave the account exposed. `plan_close` can also increase exposure from net flat. [Broker transport](src/dual_strategy/competition/broker.py) submits an ordinary LIMIT and omits the internal `close_only` field. Cover mixed long/short attribution, the broker net before and after each fill, and second-leg failure in regression tests.
2. [Noise reversals](src/dual_strategy/competition/planner.py) bypass `entry_block` when the sleeve holds shares. A held long of 20 SPY produced a short-opening SELL 40 under each of an order-error halt, a daily loss lock, stale quotes, position mismatch and reserved order budget. [Runner.send](scripts/run_competition.py) sent the opening order even when the prior close was cancelled with zero fill. The reproduced sleeve finished short 20 versus a target short of 40. Require the close to reconcile before planning the opening order from a fresh account and risk check.
3. [Runner._close](scripts/run_competition.py) marks an ORB half exit complete when the close order fills zero shares. Seed a long of 10 shares, entry 500, stop 495 and target 510. Cancel the five-share sale with zero fills. The runner still holds ten shares, sets `half_done` and moves the stop to 500. Track confirmed cumulative quantity before switching exit rules. Include partial fills and one-share positions in the regression.

[Dashboard JavaScript](src/dual_strategy/dashboard/competition.js) defines `renderResearch` twice. The preview function shadows the new regime table renderer. Chrome at 1,366 px and 390 px rendered five cards and the historical preview. The regime table displayed zero Noise-area/VWAP/ORB rows when the response contained three. The HTTP 503 and stale snapshot warnings preserved the prior display at both widths. Give the renderers separate names and assert that both tables populate.

The checked-in [AGENTS.md](AGENTS.md) names ORB's 0.10 daily ATR and no target as current. [Competition YAML](config/competition.yaml) and the five-strategy spec use 0.35 ATR and a 2R half exit. Resolve which rule the user wants before changing ORB strategy parameters. Runner audit provenance also omits the new `intraday.py` strategy source.

Verification on Windows at `6a89baa`

- `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_competition_five.py tests/test_competition.py tests/test_dashboard.py` passed 151 tests, including the mocked after-close report test.
- The full suite reported 1,069 passes, six failures in POSIX process-list parsing fixtures with Windows paths, and 61 scheduler setup errors because `fcntl` is unavailable on Windows. The same dashboard process parser misses an unquoted macOS checkout path containing spaces in a synthetic listing. Run native scheduler and notification verification on macOS with suitable fixtures.
- `scripts/competition_parity_regime.py` reproduced the archived results in memory across 1,479 local sessions and 575,010 incremental VWAP/ORB slot checks with zero mismatches. The harness exercises research signal and fill logic. Runner order behavior needs its own tests.

All verification was offline and the sole workspace edit was this handoff. If the user asks for implementation next, begin with regression tests for the three order defects, then fix the dashboard renderer and verify on macOS before considering activation.

## Earlier A–J research

Open the sibling [Moo Moo - Codex Research worktree](<../../Moo Moo - Codex Research/moomoo-dual-strategy>) on branch **research/codex-a-j-2026-11-20**. Its [AGENT.md](<../../Moo Moo - Codex Research/moomoo-dual-strategy/AGENT.md>) records the checkpoint and reproduction commands for that task.
