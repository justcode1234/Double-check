# Build spec: five-strategy competition runner, $20,000 each

Read [QODER_COMPETITION.md](QODER_COMPETITION.md) and [AGENTS.md](AGENTS.md) in the `moomoo-dual-strategy` folder before working on `main`. All existing rules still apply:

- The deterministic runner decides, sizes and sends orders. You build, test, launch and monitor it.
- Never place or edit orders by hand.
- The 3.5% account halt is permanent until the user clears it.

> **Correction (2026-11-21):** the earlier version of this file named the wrong three strategies (failed-breakdown reversal and ETF pairs). The user's picks are **Noise-area momentum, VWAP trend continuation and ORB breakout/breakdown**. Build only these. Strategies are referred to by **name**, never by number, to avoid that confusion again.

## User decisions (2026-11-21)

1. Add three strategies from the regime study ([reports/claude_regime/RESULTS.md](reports/claude_regime/RESULTS.md)) to the existing **B3** and **H**:
   - **Noise-area momentum** (sleeve `noise`);
   - **VWAP trend continuation** (sleeve `vwap`);
   - **ORB breakout/breakdown** (sleeve `orb`).
2. **$20,000 per strategy.** Five sleeves at `allocation: 0.20` each of the starting account NAV. Each sleeve's capital then changes only with its own P&L, as the runner already does for B3 and H.
3. **The dashboard shows five strategy cards instead of two.**

Backtest evidence for the new three, at the settings below. Report it truthfully; do not tune it away. Weekly P&L on a $100,000 book:

| Strategy | 2025–26 | VIX ≥ 30 weeks | Last week, 16–20 Nov |
|---|---:|---:|---:|
| Noise-area | −$24/wk | +$217/wk | +$888 (on 40% of $100k in SPY; about half that on a $20k sleeve) |
| VWAP | −$42/wk | +$13/wk | +$248 |
| ORB | +$4/wk | +$24/wk | −$50 |

All three were trend-followers that did better in last week's selloff than over 2025–26 as a whole. Noise-area works much like B3 (SPY noise band plus VWAP), so the account becomes more concentrated in SPY momentum.

## 1. Strategy rules

The reference implementation is [reports/claude_regime/rstrats.py](reports/claude_regime/rstrats.py): `s10_noise`, `s2_vwap` and `s1_orb` (universe `etf`). Data is in [reports/claude_regime/rdata.py](reports/claude_regime/rdata.py). Port the logic into `src/dual_strategy/competition/signals.py` as pure functions, and prove parity (section 6).

The settings below are the ones selected on 2021–24 data.

**Common to all three:**
- 5-minute bars are *completed* bars (Moomoo CLI ktype 6). A bar ending at T is usable only after T.
- VWAP = cumulative turnover / cumulative volume from 09:30.
- ATR14 = mean true range of the 14 prior daily bars.

### Noise-area momentum (`noise`)

- **Symbol:** US.SPY.
- **Decisions:** at the completed bars ending **10:30, 11:30, 13:30 and 14:30** ET.
  - The reference uses 1-minute closes at those minutes. 5-minute bars ending there give identical inputs: the same close and the same cumulative turnover and volume.
- **Noise:** mean of |close at that time / 09:30 open − 1| over the **14** most recent prior sessions that have a bar at that time (skip half-days), × band **1.0**.
- **Bands:** upper = max(today's 09:30 open, prior close) × (1 + noise); lower = min(open, prior close) × (1 − noise).
- **Target:**
  - **long** if close > upper and close > VWAP;
  - **short** if close < lower and close < VWAP (needs section 3);
  - otherwise **flat**.
- **Orders:** act only when the target changes; execute at the next minute's open as marketable LIMITs. Reuse B3's `b3_decision` machinery and add the lower band.
- **Size:** 100% of the sleeve's capital ($20,000) at each entry, divided by max(price, 09:30 open). The report's own "allocation 20%" is the same amount.
- **Flatten:** 15:55 ET (on half-days, 5 minutes before the close).

### VWAP trend continuation (`vwap`)

- **Symbols:** US.SPY, US.QQQ.
- **When:** each completed bar from the one ending 10:10 to the one ending 15:35.
- **Side:** long if close > VWAP; short if close < VWAP. Shorts need section 3.
- **Filters:**
  - VWAP slope over the last 3 bars has the same sign as the side.
  - Fewer than 3 crossings of close versus VWAP in the last 12 bars.
  - Structure (long): high > previous high and low > previous low. Shorts mirror this.
  - Pullback (long): bar low − VWAP ≤ **0.10 × ATR14** and close > VWAP. Shorts mirror this.
- **Entry:** a stop trigger at the setup bar's high (long) or low (short), valid only during the next bar. When price trades through it, send a marketable LIMIT; if it doesn't trigger, cancel.
- **Stop:** VWAP at the setup bar − **0.25 × ATR14** (long), mirrored for shorts.
- **Target:** entry + **3 ×** the stop distance.
- **Flatten:** 15:45 ET (on half-days, 15 minutes before the close).
- **Limits:** at most 3 trades per symbol per day; at most 2 open positions.
- **Size:** risk 0.15% of account NAV per trade, never more notional than the sleeve's capital or the remaining per-symbol cap.

### ORB breakout/breakdown (`orb`)

- **Symbols:** US.SPY, US.QQQ, US.IWM (ETFs only).
- **Opening range:** the first **15 minutes** (three 5-minute bars).
- **Skip the symbol for the day if either:**
  - the range is wider than **1.25 × ATR14**; or
  - first-15-minute volume is below **2.0 ×** the average first-15-minute volume of the prior 20 sessions.
- **Trigger:** the first completed bar, from the one ending 09:50, that closes above the range high *and* above VWAP (long), or below the range low *and* below VWAP (short). Shorts need section 3.
  - Enter at the next bar's open. No new entries in the last 30 minutes (after 15:30, or 12:30 on half-days).
- **Stop:** distance = min(entry − breakout bar's low, **0.35 × ATR14**) for longs, mirrored for shorts.
- **Exits:**
  - half at **2 ×** the stop distance;
  - the other half: move its stop to breakeven, then exit at the next open when a 5-minute bar closes below the previous bar's low (longs; mirrored for shorts);
  - flatten at 15:55 ET.
- **Limits:** one entry per symbol per day; at most 3 open.
- **Size:** risk 0.15% of account NAV; notional ≤ $10,000 per trade (the reference's 10% cap) and ≤ the remaining per-symbol cap.

## 2. Capital and sizing

- `config/competition.yaml`:
  - `allocation: {b3: 0.20, h_etf: 0.20, noise: 0.20, vwap: 0.20, orb: 0.20}`;
  - new `noise:`, `vwap:` and `orb:` sections holding the parameters above;
  - B3 keeps `position_fraction_of_sleeve: 1.0`, which now means $20,000 of SPY.
- `planner.SLEEVES` becomes all five. Extend `capital_limits`, `apply_fill`, `attribution_errors`, `plan_flatten`, `sleeve_performance` and the report to every sleeve.
- **Per-symbol cap.** SPY can now be held by B3, H, Noise-area, VWAP and ORB together, so `risk.name_cap_usd` ($40,000) applies to the *sum* across sleeves, longs and shorts by absolute value. If B3 and Noise-area both want SPY, the cap is shared first-come; the later sleeve gets the remainder or skips.
- **Existing holdings.** The H positions (XLV and XLP) stay owned by `h_etf`. Do not reset or re-attribute anything in `private/competition/live/`.

## 3. Short selling (needed by all three new strategies' short side)

- **Today the runner is long-only.** `risk.long_only: true`, and the broker gate refuses any sell that isn't close-only.
- **Verify before enabling shorts.** First check, with the Moomoo MCP/CLI and read-only calls, that the competition account allows short sales of SPY, QQQ and IWM, and what the borrow terms are.
- **If shorting is allowed,** enable it only for the `noise`, `vwap` and `orb` sleeves:
  - a SELL opens a short only when its intent is marked `opens_short`; the cover is a close-only BUY;
  - reserve short collateral in cash checks;
  - every halt, lock and scheduled flatten covers shorts;
  - attribution holds negative quantities per sleeve;
  - never carry a short overnight.
- **If shorting is not allowed:** run all three long-only and tell the user. Their backtests used both directions, so long-only results will differ.

## 4. Execution and schedule

- **Intraday exits.** The transport supports LIMIT orders only, so VWAP and ORB stops, targets and trails must be monitored by the runner. Poll quotes for open positions every ~10 s and act on completed 5-minute bars for the ORB trail. Keep the 30-second account risk loop.
- **Market data cost per 5 minutes.** One 5-minute kline call each for SPY, QQQ and IWM, plus quotes for open positions only. Keep the existing pacing and retry.
- **Additions to the loop:**
  - every completed 5-minute bar: ORB (from the bar ending 09:50), VWAP (from 10:10), Noise-area (at 10:30, 11:30, 13:30 and 14:30);
  - 15:45: VWAP flatten;
  - 15:55: ORB, Noise-area and B3 flatten;
  - H is unchanged.
- **Errors.** Every step runs through `guarded()`. Every order goes through the existing audit, outbox, order-error budget and halt path.
- **Wednesday 25 November.** PCE and GDP are released before the open. Per the report, no new Noise-area, VWAP or ORB entries before 10:30 ET that day. Make this a dated config item.
- **Friday 27 November closes at 13:00.** VWAP flattens at 12:45. ORB and Noise-area flatten at 12:55. No new entries after 12:00.

## 5. Dashboard

On the `/competition` page, show **five strategy cards**: B3, H, Noise-area, VWAP and ORB. Each card shows:

- a $20,000 allocation and current capital (allocation + own P&L);
- holdings, longs and shorts;
- today's decisions or blocked reasons;
- open stops, targets and trails;
- realized and unrealized P&L.

Also:
- the risk strip (high-water mark, 3.5% halt level, daily lock) stays account-wide;
- the intents table gets a sleeve filter;
- the research section lists each strategy's backtest from [reports/claude_regime/results/summary.csv](reports/claude_regime/results/summary.csv) (rows `10 Noise-area momentum`, `2 VWAP trend continuation`, `1 ORB breakout/breakdown`), labeled by name, with the evidence caveat above;
- verify the page in a browser at desktop and narrow widths, including the failed and stale states.

## 6. Tests and parity (all required before any live run)

- **Signal parity.** For every session from 2021-01-04 to 2026-11-20, the live Noise-area, VWAP and ORB signal functions must produce the same entries, sides, stops, targets and exits as `s10_noise`, `s2_vwap` and `s1_orb` (`universe: etf`) on the same bars (`data-cache/regime_extra` + `data-cache/history`). Report mismatches with dates. Missing-data days may block (fail closed) where the reference trades nothing.
- **Unit tests:**
  - $20,000 caps;
  - the shared per-symbol cap across five sleeves;
  - shorts gating, collateral and covers;
  - the VWAP/ORB stop, target and trail monitor;
  - Noise-area target changes, long ↔ short;
  - halts and locks flattening every sleeve, shorts included;
  - attribution with negative quantities.
- **Full suite** green, then report the exact count.

## 7. Rollout

1. PLAN dry runs with `--ephemeral`, replaying 16–20 November. On a $100,000 book at the reference sizes, P&L should match the backtest: Noise-area +$888 (40% allocation), VWAP +$248, ORB −$50.
2. `status --execute` before 09:30 ET. Confirm five sleeves at $20,000 initial capital, existing H holdings attributed, and no attribution errors.
3. Restart the single loop before 09:30 ET Monday 23 November. Remaining sessions: 23, 24, 25 and 27 November.
4. Commit and push your changes, never `private/`, and report the commit, test count, parity results, the short-selling finding and the first-day P&L per sleeve.
5. If anything fails, diagnose and retry once. If it fails again, stop and ask the user.
