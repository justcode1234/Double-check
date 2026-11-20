# Qoder hand-off: B3 80% + H 20% competition runner

Paste this file into Qoder, opened in the `moomoo-dual-strategy` folder. The deterministic runner (`scripts/run_competition.py`) decides, sizes, gates and sends orders. You launch it, monitor it and stop it. **Never place, size or edit orders yourself**, and never override a rejection.

## 0. One decision belongs to the user, not to you

[AGENTS.md](AGENTS.md) and [NEXT_SESSION_PROMPT.md](NEXT_SESSION_PROMPT.md) still say *never touch the competition account*. Do not run anything with `--execute` until the user has done both of these:

- edited those two files to authorize the competition account;
- set `execution_enabled: true` in [config/competition.yaml](config/competition.yaml).

Until then, use PLAN mode only. PLAN sends nothing.

## 1. What is ready

| Part | Where | Status |
|---|---|---|
| Rules and parameters | [config/competition.yaml](config/competition.yaml) | B3 80% / H 20% of account NAV. B3 targets its whole sleeve (80% of NAV) but is capped at the $40,000 per-symbol purchase cap, so about 40% of a $100k account. 3.5% account halt. Ships with `execution_enabled: false`. |
| Signals | [src/dual_strategy/competition/signals.py](src/dual_strategy/competition/signals.py) | B3 matches the research engine on all 7,421 hourly decisions from 2021 to 2025. H's universe and entries match on all 1,243 sessions. |
| Risk and sizing | [src/dual_strategy/competition/planner.py](src/dual_strategy/competition/planner.py) | Account halt (permanent), daily lock, half size, sleeve ownership of shares, $40k per-name cap, no leverage, long-only |
| Broker gate | [src/dual_strategy/competition/broker.py](src/dual_strategy/competition/broker.py) | Refuses unless all 3 locks are open (section 3); allowlists exactly one account; LIMIT orders only; reconciles every order from broker history |
| Runner | [scripts/run_competition.py](scripts/run_competition.py) | `status`, `plan-h`, `h-open`, `b3`, `flatten`, `loop`; PLAN by default |
| Dashboard | `python -m dual_strategy.cli serve --port 8876`, then http://127.0.0.1:8876/competition | Read-only: sleeves, halt level, intents and outcomes, research preview |
| Tests | `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider` | 947 passed on 2026-11-20, including 13 in `tests/test_competition.py` |

The research preview, built from hypothetical fills, is in [reports/competition/backtest_preview.json](reports/competition/backtest_preview.json). It was computed with B3 at 32% of NAV; the capped live size of about 40% scales B3's gains, losses and cost sensitivity by roughly 1.25×.

- 2021–2025: +7.54%, max drawdown −1.47%, halt never hit.
- 2024–25: +0.04%.
- At 5 bp per side, 2021–2025 falls to −2.62%.
- Next 5 sessions: median about 0%, 5th–95th percentile −0.23% to +0.36%.

## 2. Pre-flight (PLAN only, safe at any time)

```bash
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_competition.py
moomoo auth status --format json            # must say "logged in"
.venv/Scripts/python.exe scripts/run_competition.py plan-h                 # H plan from the last completed close
.venv/Scripts/python.exe scripts/run_competition.py b3 --day 2026-11-19 --at 10:00   # replay a past B3 decision
.venv/Scripts/python.exe scripts/run_competition.py status
```

## 3. The three locks (all required for orders)

1. `execution_enabled: true` in `config/competition.yaml`. **The user sets this.**
2. `private/competition/account.json`, which is gitignored and must never be committed. Create it **with the user**, from `moomoo sim account list --format json`, choosing the account the user confirms is the competition account:
   ```json
   {"account_id": "<competition account id>", "account_type": 2, "market_id": 100, "confirmed_competition_account": true}
   ```
   The runner re-checks that the broker lists this ID exactly once, as `account_type` 2. Never guess an ID.
3. `--execute` on the command line.

PLAN and EXECUTE keep separate state: `private/competition/plan/` and `private/competition/live/`. The live high-water mark, halt and share ownership persist across restarts. Never delete or reset them.

## 4. Running a session (America/New_York)

Remaining challenge sessions: Fri 20 (if ready before 09:30 ET), Mon 23, Tue 24, Wed 25 and Fri 27 November. Friday 27 November closes at 13:00, so B3 decides at 10:00–12:00 and flattens at 12:55. Start before 09:30 on one machine only:

```bash
.venv/Scripts/python.exe scripts/run_competition.py loop --execute
```

The loop does the following (if the live state has no H plan for the previous close, it builds one before the open):

| Time | Action |
|---|---|
| 09:30:10 | **H open:** sells planned exits, then buys planned entries (10% of the H sleeve per ETF, 30% in total) |
| 10:00–15:00, hourly, about 15 s after each bar closes | **B3:** goes long its whole sleeve (80% of NAV, capped at $40,000 of SPY across both sleeves) if the bar closes above the noise band and VWAP; otherwise sells what B3 owns |
| 15:55 (12:55 on half-days) | **B3 flatten** |
| Every 30 s | **Risk check** from broker NAV, computed as cash + quantity × quote. Breaching the 3.5% halt or the 1.25% daily lock closes everything owned. |
| About 16:15 | **H plan** for the next open, from today's close |

Orders are marketable LIMIT orders at the quote ± 5 bp. Any order not filled within 30 s is cancelled and reconciled.

## 5. Stopping and failures

- **Stop the runner:** Ctrl+C. This **does not** close positions.
- **Close everything:**
  ```bash
  .venv/Scripts/python.exe scripts/run_competition.py flatten --sleeve all --execute
  ```
- **The halt is permanent by design.** Only the user may clear it, by setting `"halted": null` in `private/competition/live/state.json` after reviewing why it fired.
- **Order errors.** On any order error or ambiguous acknowledgement, the runner records a halt and stops. Reconcile with broker order history. Make `sleeves` in the state file match the broker position, but only with the user. Do not retry blindly.
- **Missing or stale data, quotes or bar gaps** block entries automatically. That is expected, not a bug to work around.

## 6. Known limits (tell the user, do not paper over)

- **Unverified broker fields.** Funds, position and order-history field names come from the practice-account transport. On the first `--execute` start, check that `status` shows a sensible NAV before 09:30.
- **H data source.** H uses Moomoo daily bars (adjusted with autype 1). The backtest used Yahoo adjusted closes; those agree within 0.1 bp for SPY and QQQ, but the sector ETFs were not cross-checked.
- **Costs.** B3 is cost-sensitive: the 2021–2025 gain disappears beyond about 3–5 bp per side.
- **Sample.** B3's 2024–25 result was −0.88%; the mix's edge rests mainly on H. These are research results, not a promise.
