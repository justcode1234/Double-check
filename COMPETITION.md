# Moo-Moo: Two-vote SPY/QQQ intraday strategy

Moomoo × Qoder paper-trading challenge write-up · competition paper account, $100,000 starting assets · 20–27 November 2026

## Summary

- **What runs:** an intraday **Two-vote** strategy on SPY and QQQ, on a $100,000 sizing book. The multi-day **H ETF** dip-buying sleeve was retired on 26 November and its $20,000 moved to Two-vote. Older sleeves (B3, Noise-area, VWAP, ORB) are exit-only.
- **Edge hypothesis:** when two or more independent, well-known technical rules agree on a 5-minute bar, short-term direction is more likely to persist than when any single rule fires. Ten rules vote, and a trade needs at least two to agree with fewer than two disagreeing.
- **How it is controlled:** every number is in [config/competition.yaml](moomoo-dual-strategy/config/competition.yaml), and every decision is deterministic Python. Risk limits sit inside the official 40% single-name rule. Every order is reconciled against Moomoo before the next one. From 25 November, **a long position is never sold at a loss**: it is sold only when its own entry lot nets a profit after fees, and otherwise it is kept, overnight if needed. Shorts can still be covered at a loss.
- **What we learned:** a 5.9-year replay shows the signal is **regime-dependent**. It loses after conservative costs in 2021–2024 and is profitable in the recent volatile regime. The best exit across every period was to **let winners run** (exit only on a 2+ opposite vote or the 15:55 ET close). That rule is live from 25 November.

## 1. Strategy

### Two-vote (SPY, QQQ)

Each rule is defined for the short side and mirrored for the long side. A rule's vote switches on at its first entry signal from the 09:55 ET bar onward and stays on until its own exit condition, which gives one window per rule, per direction, per session. Indicators run continuously over the previous 20 sessions of 5-minute bars plus today.

| # | Rule | Short entry (long is the mirror image) | Rule exit |
|---|---|---|---|
| 1 | Donchian | Close breaks below the prior 20-bar low | Close above the prior 10-bar high |
| 2 | Keltner | Close crosses below EMA20 − 2 × ATR10 | Close above EMA20 |
| 3 | RSI(2) | RSI(2) > 90 while close < SMA200 | Close below SMA5 |
| 4 | MACD (12, 26, 9) | MACD crosses below its signal while below zero | MACD crosses back above |
| 5 | DMI/ADX (14) | −DI crosses above +DI with ADX ≥ 25 | +DI back above −DI |
| 6 | Stochastic (14, 3) | %K crosses below %D from ≥ 80 | %K ≤ 20 |
| 7 | Chandelier (22, 3 × ATR) | Close crosses below the 22-bar high − 3 × ATR | Close above the 22-bar low + 3 × ATR |
| 8 | Turtle Soup | False breakout above the 20-bar high, confirmed by the next bar's open | Close back at the breakout high, or at the 20-bar mid-range |
| 9 | NR7 | Only after a narrowest-range-of-7 day: close below the first 15-minute low | Close at or above the first 15-minute high |
| 10 | Noise-area (SPY only) | At 10:30, 11:30, 13:30 and 14:30 ET: close below the noise band and below VWAP (long: above both) | Next decision time, or the close |

**Signal:** buy when 2 or more rules vote long and fewer than 2 vote short (sell is the mirror image). Rules: [vote_rules.py](moomoo-dual-strategy/src/dual_strategy/competition/vote_rules.py), live votes: [vote_live.py](moomoo-dual-strategy/src/dual_strategy/competition/vote_live.py).

**Entries (10:00–14:30 ET):**

- $100,000 sizing book across all its tickers (it was $80,000 until H's $20,000 moved over on 26 November). Realized gains are banked outside the book; losses reduce it. No leverage: new positions must also fit in NAV minus open gross exposure.
- $20,000 per entry lot. There is no fixed lot count: lots are added while the ticker stays within its cap, which is $40,000 and at most 39.5% of live NAV (just inside the official 40% single-name limit). A top-up smaller than $5,000 is skipped. Older $10,000 lots count toward the cap at their market value, so a ticker can hold $10k + $20k + a smaller top-up.
- While an agreement holds, each completed bar may add one lot, and the strategy re-enters after an exit if the agreement still holds.
- It never re-enters on the bar where an opposite agreement just closed the position, and never while an earlier exit order is still working.

**Exits:**

- When 2 or more rules agree on the opposite side, the strategy covers every short lot of that ticker, and sells each long lot only if that lot is in profit.
- Moomoo holds one net position per ticker, so a ticker held long cannot also be shorted (its `max_sell_short` is 0). From 25 November, a 2-vote sell agreement on a ticker held long opens the short in a twin ETF on the same index instead: IVV for SPY, QQQM for QQQ. The long lots are kept. The twin short stacks like any lot, follows its ticker's votes and is covered on a 2-vote buy agreement, the 15:55 ET flatten, a lock or a halt.
- A scheduled flatten at 15:55 ET covers every short and sells every long lot that is in profit. A long lot at a loss is kept overnight and exits under the same rules in a later session. The 24 November lots (SPY 13, QQQ 30) were the first carried this way. The scheduler lets Two-vote **long** lots carry; any other intraday sleeve, or any short, still blocks the next start.
- **Long sales only at a profit.** Each entry lot is judged alone, at the worst possible fill (the sell limit, 5 bp under the quote), net of the $0.99 entry fee and the sale's $0.99 order fee plus the activity fee. Each lot therefore has its own sell floor: the old $10,000 lots need a slightly larger move per share than the $20,000 lots, because the fixed fees are spread over fewer shares. The rule is checked twice: when the exit is planned, and again in the order sender just before any order leaves, whatever asked for the sale. A sale it blocks is recorded as a decision (`long_sale_held`), not as an execution error.
- There is no profit target and no stop-loss. That choice is backed by the exit backtest in section 3. That backtest closed every position at 15:55 ET; holding losing longs overnight was not backtested.

### H ETF (multi-day) — retired 26 November

Retired by user decision on 26 November: it found no entry on 23–25 November, and its $20,000 moved to the Two-vote book. It holds nothing, makes no nightly plan and sends no orders. The rules below describe how it traded on 20 November (XLV and XLP, +$12.38 realized gross).

- **Universe:** SPY, QQQ, IWM, DIA, plus the 5 most liquid sector ETFs.
- **Entry:** RSI(2) ≤ 10 on daily adjusted closes, with the close above its 100-session mean.
- **Exit:** RSI(2) ≥ 70, after a 3-session maximum hold, or at a gross profit target of $20 per position ($5 for positions opened under the earlier $2,000 sizing). Like every long, an H position is sold only at a profit after fees; otherwise it is kept and the exit is retried.
- **Sizing:** $20,000 sleeve book, $10,000 per entry.

## 2. Risk management

| Control | Setting | Action |
|---|---|---|
| Single-name cap (official: ≤ 40% of assets; breaches caused only by price moves are exempt) | $40,000 per ticker and at most 39.5% of live NAV, summed across all sleeves, longs and shorts by absolute value | Entries are sized down to the remaining room; a top-up under $5,000 is skipped |
| Long sales only at a profit | A long lot is sold only when its sell limit nets more than $0 after fees | Losing longs are kept, overnight if needed; shorts are still covered at a loss |
| Daily loss | Account NAV ≤ 97.5% of the day-start NAV | Cover shorts and sell longs in profit; no entries for the rest of the day |
| Drawdown, reduce | NAV ≤ 96% of the high-water mark | New entries at half size until NAV is back above 96.5% |
| Drawdown, halt (kill switch) | NAV ≤ 93% of the high-water mark | Cover shorts and sell longs in profit (a losing long is sold as soon as it turns profitable); entries stay blocked until a human clears the halt |
| Orders | ≤ 200 per day (the official cap); 3 order errors in a day halt trading | Human reconciliation required |
| Execution | Marketable limit at the quote ± 5 bp; quotes ≤ 15 s old; unfilled after 30 s → cancel and reconcile | Partial fills are verified against Moomoo positions and cash |
| Shorts | Only in short-enabled sleeves; never overnight. A Two-vote short against a held long goes to the twin ETF (IVV for SPY, QQQM for QQQ), which has its own 40% cap | A close-only buy covers, at a loss if needed |
| Startup gates | Each session starts only between 09:00 and 09:25 ET, and only after read-only preflight: confirmed competition account, valid capital, no halt or lock, intraday sleeves flat (except Two-vote long lots), no shorts carried, prior sessions completed or reviewed, no unreviewed execution errors | The runner does not start |
| Provenance | Every runner records the exact config and SHA-256 of its source files before its first decision | Each decision is traceable to the exact code that made it |

**Invalidation criteria (reviewed manually each day).**

- Pause new Two-vote entries if live expectancy turns negative over 20 or more closed trades.
- Pause them if the halt fires.
- Pause them if independently recomputed votes stop matching the runner's.

## 3. Validation

**Tests.** 1,208 automated tests cover signals, sizing, risk gates, order reconciliation, the scheduler and the dashboard. They run with `.venv/bin/python -m pytest`.

**Independent signal check.** All 10 rules were re-implemented separately, without importing the strategy code, and fed Moomoo's own 5-minute bars. On the runner's live bars, they matched the runner's recorded votes on every rule and every bar: 62 bars × 2 tickers on 23 November and 52 bars × 2 tickers on 24 November, with 0 mismatches. Against Moomoo's later history the only differences came from cent-level bar revisions. One Chandelier vote hinged on a single cent.

**Exit backtest.**

- **Source and code:** [scripts/competition_exit_backtest.py](moomoo-dual-strategy/scripts/competition_exit_backtest.py), with results in [reports/competition/exit_backtest/](moomoo-dual-strategy/reports/competition/exit_backtest/).
- **Data:** SPY and QQQ 5-minute bars, January 2021 to 20 November 2026, 1,477 sessions, using the live rules and sizing.
- **Fills:** at bar opens. Target and stop levels are checked against each bar's high and low; when a bar touches both, the stop is assumed to hit first. The book resets to $80,000 each session.
- **Costs:** 1 bp slippage plus 0.12 bp half-spread per side, plus per-order fees. A fees-only sensitivity run reflects how paper fills behaved.

Expectancy per trade (net P&L in brackets):

| Exit rule (all with $20k lots, add + re-enter) | 2021–2026 | 2025–2026 | Last 3 months | Last 3 months, fees only |
|---|---|---|---|---|
| **H: no target, no stop (vote flip + 15:55 close), live from 25 Nov** | **−$1.72** | **+$0.81 ($2,754)** | **+$12.48 ($6,105)** | **+$16.89 ($8,258)** |
| A: $100 profit target | −$3.26 | −$0.20 (−$835) | +$8.32 ($5,086) | +$12.72 ($7,775) |
| D: $200 target + $100 stop | −$2.99 | −$2.30 | +$9.43 | +$13.83 |
| B: $100 target + $100 stop | −$4.08 | — | — | — |
| F: $150 trailing stop | −$3.06 | −$3.66 | +$9.50 | +$13.91 |
| I: $100 target, no vote-flip exit | −$3.67 | −$5.94 | +$10.27 | +$14.67 |
| K: rules before 25 Nov ($10k lots, $50 target, fresh agreements only) | −$2.71 | −$2.30 | +$1.30 | +$3.46 |

What it shows:

1. **Exits cannot create an edge the entries lack.** No exit variant is profitable over 2021–2026 after conservative costs. The signal works in trending, volatile regimes, and the recent months are one.
2. **Capping winners hurts.** Fixed profit targets reduce the average win more than they improve the win rate.
3. **Stops hurt.** Tight stops turn noise into realized losses.
4. **The vote-flip exit is the valuable exit.** Removing it is worse in every period.
5. **Requiring 3 votes instead of 2** roughly halves the number of trades. Over 2021–2026 it is far more defensive: for H, the loss falls from −$18,725 to −$1,133. But in the last 3 months net P&L falls from $6,105 to $2,562. The strategy stays at 2 votes for the current regime, and 3 votes is the fallback if the regime turns.

## 4. Live paper results (snapshot)

As of 03:37 SGT, 25 November 2026. The 24 November US session was still in progress; its Two-vote long lots were later held through the close into 25 November. Final figures come from the dashboard export.

| Measure | Value |
|---|---|
| Account NAV | $100,017.50 (+$17.50, +0.02%) on $100,000 |
| High-water mark / drawdown | $100,174.35 / −0.16% |
| Benchmark from the 20 Nov open | SPY +0.53% ($713.56 → $717.33) · QQQ −0.27% ($638.84 → $637.10) |
| Closed trades (all sleeves) | 7 · win rate 85.7% · average win $31.38 · average loss −$76.20 |
| Expectancy / profit factor | +$16.01 per closed trade / 2.47 |
| Realized gross | Two-vote $99.71 · H ETF $12.38; fees and other cash flows −$21.17 |

Notable sessions:

- **23 Nov:** Two-vote bought QQQ at 10:55 ET on a Donchian + RSI(2) agreement, then SPY and QQQ again at 11:45 and 11:50. The positions were closed at a profit.
  - Moomoo reported a 20-share partial fill as a cancellation with zero fills. The runner correctly halted, and the remaining lots were sold manually and reconciled from Moomoo's position and cash records.
  - Fix: per-lot share tracking, and partial-close reconciliation cross-checked against positions and cash.
- **24 Nov:**
  - At 10:30 ET Two-vote opened a QQQ short on a Donchian + Keltner agreement and took +$54.38.
  - A second short opened at 11:55 ET. When three rules flipped to buy at 12:30 ET, the vote-flip exit closed it at −$76.20. The independent check confirmed every vote. Holding on would have reached −$90.45 by 12:46 ET, and the +$50 target was never reached.
  - That loss prompted the exit backtest above.

## 5. Architecture and agents

```text
systemd timer (60 s) ─▶ scheduler (09:00–09:25 ET window, read-only preflight gates)
                          └─▶ runner loop (deterministic Python, one process)
                                ├─ Moomoo OpenAPI via the official Moomoo CLI (competition sim account, quotes + sim orders)
                                ├─ risk marks every 30 s · take-profit/monitor every 10 s · votes each completed 5-min bar
                                └─ private ledgers: outbox (intent → acknowledgement → outcome) · audit records · provenance
dashboard service (read-only) ─▶ http://127.0.0.1:8876  votes · positions · trades · signal tracker · risk · audit
```

- **Qoder Desktop** built the original project and the competition dashboard on this machine.
- **Qoder Cloud Agents:** read-only reporter, auditor and watchdog agents are deployed in an isolated environment with no network, no MCP servers and no trading tools. One manual reporter run is verified, and the IDs and output are in [evidence/cloud-reporter-run.json](moomoo-dual-strategy/evidence/cloud-reporter-run.json). Cloud agents cannot place orders by design. All order decisions stay in deterministic local code, and an agent may only explain or veto.

## 6. Reproduce

```bash
git clone https://github.com/justcode1234/Moo-Moo.git && cd Moo-Moo
python3 start.py                                   # builds the venv, opens the dashboard
cd moomoo-dual-strategy
.venv/bin/python -m pytest                         # 1,208 tests
PYTHONPATH=src .venv/bin/python scripts/competition_exit_backtest.py /tmp/exit_backtest.json
PYTHONPATH=src .venv/bin/python scripts/competition_vote_replay.py   # two-vote replay on cached bars
```

The backtests read the committed `data-cache`. Live trading needs the Moomoo CLI signed in to the competition account. See [README.md](README.md) for setup on Linux, Windows and macOS. Earlier research notes are archived in [HISTORY.md](HISTORY.md).

## 7. Lessons

1. **Measure the payoff shape, not just the win rate.** An 86% win rate hid a loss 2.4 times the average win.
2. **Validate exits on years of data before changing them live.** Seven trades are an anecdote.
3. **Broker reports can be wrong.** Reconcile every fill against positions and cash before the next order.
4. **Settings apply when the runner starts.** The dashboard shows the settings of the running process and flags saved changes that take effect at the next start.
5. **Live and historical bars differ by cents.** Verify signals on the bars the runner actually saw.
6. **"Never sell at a loss" moves risk, it does not remove it.** From 25 November a losing long is kept rather than cut, so the daily-loss lock and the drawdown halt now stop new entries and cover shorts but cannot shrink a losing long book. Exposure stays bounded by the per-ticker cap (at most about $80,000 across SPY and QQQ) and by the no-leverage cash rule. The trade-off is deliberate: avoid realizing losses and give positions time to recover, at the cost of carrying overnight gap risk that the backtest did not model.
7. **Check the risk checks while a short is open.** Moomoo's sim `balance` withholds 160% of the short market value (proceeds plus 60% margin). Reading it as cash put the runner NAV about 1.6× the short below the broker total, so on 23–24 November every mark with a short open (148 of 148) failed the NAV check: entries were blocked and the loss lock and halt were not evaluated. Fixed 25 November: with shorts open, cash = total_asset − long_mv + |short_mv|, used only when it equals balance + 1.6 × |short_mv| to the dollar.
