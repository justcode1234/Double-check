# Build spec: five-strategy competition runner, $20,000 each

You're working in the `moomoo-dual-strategy` folder on branch `competition-b3-h`. Read [QODER_COMPETITION.md](QODER_COMPETITION.md) and [AGENTS.md](AGENTS.md) first. All existing rules still apply:

- The deterministic runner decides, sizes and sends orders. You build, test, launch and monitor it.
- Never place or edit orders by hand.
- The 3.5% account halt is permanent until the user clears it.

## User decisions (2026-11-21)

1. Add three strategies from the regime study ([reports/claude_regime/RESULTS.md](reports/claude_regime/RESULTS.md)):
   - **S2, VWAP trend continuation**
   - **S3, failed-breakdown reversal**
   - **S9, ETF pairs reversion**

   They join the existing **B3** and **H**.
2. **$20,000 per strategy.** Five sleeves at `allocation: 0.20` each of the starting account NAV. Each sleeve's capital then changes only with its own P&L, as the runner already does for B3 and H.
3. **The dashboard shows five strategy cards instead of two.**

Backtest evidence for the three new strategies is weak. Report it truthfully; do not tune it away. Weekly P&L on a $100,000 book, at the settings below:

| Strategy | 2025–26 | VIX ≥ 30 weeks | Last week |
|---|---:|---:|---:|
| S2 | −$42/wk | +$13/wk | +$248 |
| S3 | $0/wk | −$6/wk | −$161 |
| S9 | −$1/wk | +$8/wk | −$141 |

With a $20,000 sleeve the dollar amounts are smaller.

## 1. Strategy rules

The reference implementation is [reports/claude_regime/rstrats.py](reports/claude_regime/rstrats.py) (`s2_vwap`, `s3_failed_breakdown`, `s9_pairs`), with data in [reports/claude_regime/rdata.py](reports/claude_regime/rdata.py). Port the logic into `src/dual_strategy/competition/signals.py` as pure functions, and prove parity (section 6).

The settings below are the ones selected on 2021–24 data.

**Common to all three:**
- 5-minute bars are *completed* bars (Moomoo CLI ktype 6). A bar ending at T is usable only after T.
- VWAP = cumulative turnover / cumulative volume from 09:30.
- ATR14 = mean true range of the 14 prior daily bars.

### S2: VWAP trend continuation (`vwap`)

- **Symbols:** US.SPY, US.QQQ.
- **When:** each completed bar from the one ending 10:10 to the one ending 15:35.
- **Side:** long if close > VWAP; short if close < VWAP. Shorts need section 3.
- **Filters:**
  - VWAP slope over the last 3 bars has the same sign as the side.
  - Fewer than 3 crossings of close versus VWAP in the last 12 bars.
  - Structure (long): high > previous high and low > previous low. Shorts mirror this.
  - Pullback (long): bar low − VWAP ≤ **0.10 × ATR14** and close > VWAP. Shorts mirror this.
- **Entry:** a stop trigger at the setup bar's high (long) or low (short), valid only during the next bar. When price trades through it, send a marketable LIMIT; if it doesn't trigger, cancel. Entry price = max(that bar's open, trigger) for longs.
- **Stop:** VWAP at the setup bar − **0.25 × ATR14** (long), mirrored for shorts.
- **Target:** entry + **3 ×** the stop distance.
- **Flatten:** 15:45 ET (on half days, 15 minutes before the close).
- **Limits:** at most 3 trades per symbol per day; at most 2 open S2 positions.
- **Size:** risk 0.15% of account NAV per trade (stop distance + entry friction), never more notional than the sleeve's capital or the remaining per-symbol cap.

### S3: failed-breakdown reversal (`fbr`, long only)

- **Symbols:** US.SPY, US.QQQ, US.IWM. One trade per symbol per day; at most 3 open.
- **Setup, on completed bars from the 4th bar onward:**
  1. A new session low at least **1.5% below the session open**. This resets the setup; remember the low of the 3 bars before it (the "prior 15-minute low").
  2. A later close above that prior 15-minute low.
  3. A later close above VWAP.
  4. A later bar whose high exceeds the previous bar's high: buy at the next bar's open. No entries after 14:55.
- **Stop:** the session low − $0.01.
- **Target:** max(VWAP at the signal bar, entry + 0.5 × stop distance).
- **Flatten:** 15:55 ET.
- **Size:** risk 0.10% of account NAV; notional ≤ sleeve capital and the remaining per-symbol cap.

### S9: ETF pairs reversion (`pairs`)

- **Pairs (leg a / leg b):** SPY/QQQ, SPY/IWM, XLK/SPY, XLF/SPY.
- **Signal, after each close (with the H plan, about 16:15 ET):**
  - Use daily adjusted closes (Moomoo autype 1).
  - Fit β, α and σ from **90** sessions ending the *previous* close, using rolling covariance and variance with a shift.
  - z = (log a − α − β log b) / σ at today's close.
  - Signal if |z| ≥ **2.75** and β > 0.
- **Trade, the next session:**
  - At about 09:30:10, if z > 0, short a and buy b; if z < 0, buy a and short b.
  - Notional per pair = sleeve capital / 4, split 1 : β between a and b.
  - Close both legs at 15:55 ET, the same session. Maximum hold is one session.
- **If only one leg fills:** close it immediately.

## 2. Capital and sizing

- `config/competition.yaml`:
  - `allocation: {b3: 0.20, h_etf: 0.20, vwap: 0.20, fbr: 0.20, pairs: 0.20}`;
  - new `vwap:`, `fbr:` and `pairs:` sections holding the parameters above;
  - B3 keeps `position_fraction_of_sleeve: 1.0`, which now means $20,000 of SPY.
- `planner.SLEEVES` becomes all five. Extend `capital_limits`, `apply_fill`, `attribution_errors`, `plan_flatten` and `sleeve_performance` to every sleeve.
- **Per-symbol cap.** SPY can now be held by B3, H, S2, S3 and S9 together, so `risk.name_cap_usd` ($40,000) applies to the *sum* across sleeves, longs and shorts by absolute value.
- **Existing holdings.** The H positions (XLV and XLP) stay owned by `h_etf`. Do not reset or re-attribute anything in `private/competition/live/`.

## 3. Short selling (needed by S2's short side and by S9)

- **Today the runner is long-only.** `risk.long_only: true`, and the broker gate refuses any sell that isn't close-only.
- **Verify before enabling shorts.** First check, with the Moomoo MCP/CLI and read-only calls, that the competition account allows short sales and what the borrow terms are.
- **If shorting is allowed,** enable it only for the `vwap` and `pairs` sleeves:
  - SELL orders open a short only when an intent is marked `opens_short`; the cover is a close-only BUY;
  - reserve short collateral in cash checks;
  - flatten covers shorts on every halt, lock and scheduled exit;
  - attribution holds negative quantities per sleeve;
  - never carry a short overnight.
- **If shorting is not allowed:**
  - run S2 long-only;
  - S9 cannot run as designed. **Do not substitute a long-only version. Ask the user.**

## 4. Execution and schedule

- **Intraday exits.** The transport supports LIMIT orders only, so S2/S3 stops and targets must be monitored by the runner. Poll quotes for open S2/S3 positions every ~10 s and send a marketable LIMIT on a stop or target touch. Keep the 30-second account risk loop as it is.
- **Market data cost per 5 minutes.** S2/S3 need one 5-minute kline call each for SPY, QQQ and IWM, plus quotes for open positions only. Keep the existing pacing and retry.
- **Additions to the loop:**
  - every completed 5-minute bar: S3, then S2;
  - 09:30:10: S9 entries from the previous close's signal, after H's open orders;
  - 15:45: S2 flatten;
  - 15:55: S3, S9 and B3 flatten;
  - about 16:15: S9 signals, alongside the H plan.
- **Errors.** Every step runs through `guarded()`. Every order goes through the existing audit, outbox, order-error budget and halt path.
- **Wednesday 25 November.** PCE and GDP are released before the open. Per the report, S2 and S3 take no new entries before 10:30 ET that day. Make this a dated config item, not a code change.
- **Friday 27 November closes at 13:00.** No S9 entries; S2 and S3 flatten 15 minutes before the close.

## 5. Dashboard

On the `/competition` page, show **five strategy cards**: B3, H, S2 (VWAP), S3 (FBR) and S9 (Pairs). Each card shows:

- a $20,000 allocation and current capital (allocation + own P&L);
- holdings, longs and shorts;
- today's decisions or blocked reasons;
- open stops and targets;
- realized and unrealized P&L.

Also:
- the risk strip (high-water mark, 3.5% halt level, daily lock) stays account-wide;
- the intents table gets a sleeve filter;
- the research section lists each strategy's backtest from [reports/claude_regime/results/summary.csv](reports/claude_regime/results/summary.csv) with the evidence caveat above;
- verify the page in a browser at desktop and narrow widths, including the failed and stale states.

## 6. Tests and parity (all required before any live run)

- **Signal parity.** For every session from 2021-01-04 to 2026-11-20, the live S2/S3/S9 signal functions must produce the same entries, sides, stops and targets as `rstrats.py` on the same bars (`data-cache/regime_extra` + `data-cache/history`). Report any mismatches with dates. Missing-data days may block (fail closed) where the reference trades nothing.
- **Unit tests:**
  - $20,000 caps;
  - the per-symbol cap across five sleeves;
  - shorts gating, collateral and covers;
  - S9 one-leg-fill unwind;
  - S2/S3 stop and target monitor;
  - halts and locks flattening every sleeve, shorts included;
  - attribution with negative quantities.
- **Full suite** green, then report the exact count.

## 7. Rollout

1. PLAN dry runs with `--ephemeral`, including an S9 signal from the 20 November close and S2/S3 replays of 16–20 November. The P&L should match the backtest's last-week figures: S2 +$248, S3 −$161, S9 −$141 on a $100,000 book.
2. `status --execute` before 09:30 ET. Confirm five sleeves at $20,000 initial capital, existing H holdings attributed, and no attribution errors.
3. Restart the single loop before 09:30 ET Monday 23 November. Remaining sessions: 23, 24, 25 and 27 November.
4. Commit and push your changes, never `private/`, and report the commit, test count, parity results, the short-selling finding and the first-day P&L per sleeve.
5. If anything fails, diagnose and retry once. If it fails again, stop and ask the user.
