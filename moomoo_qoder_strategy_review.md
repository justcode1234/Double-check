# Moomoo × Qoder AI Paper Trading Challenge — Independent Strategy Review

**Date:** 17 November 2026  
**Scope:** U.S.-listed stocks, ETFs and options; competition paper account only; no live-money trading.  
**Bottom line:** The evidence supports the ORB *family*, not the exact provisional settings. Keep the 5-minute opening range and RVOL idea, replace the 2R target with a trailing exit, replace the 5-minute ATR stop with a daily ATR stop, cut size in the current risk-off regime, cap single-name exposure at 15%, and treat the eight sessions as a risk-controlled demonstration.

---

## 1. Verdict

**Trade a regime-gated, bidirectional 5-minute opening-range breakout on the 5–8 most liquid U.S. “stocks in play”:** RVOL ≥ 1.5, price > $5, market cap > $1B, breakout confirmed by a 5-minute close beyond the range and on the correct side of VWAP; risk 0.25–0.5% of equity per trade; stop 1× daily ATR(14) beyond the range; trail winners and exit by the close—**do not cap them at 2R**. The edge is the opening auction/attention imbalance that persists intraday. The stock-ORB paper reports 2.81 in-sample; the related SPY strategy fell from 1.33 to **0.39 out-of-sample**, so size for survival. It beats mean reversion (negative skew), rotation (too slow), pairs (too few events), options (fees/assignment), and RL/ML (unvalidatable in eight sessions).

---

## 2. Ranked comparison

| Rank | Strategy | Net expectancy evidence | 8-session profit potential | Downside / tail risk | Implementation | Judging strength | Score /10 |
|---|---|---|---|---|---|---|---|
| 1 | **Regime-gated, bidirectional 5-min ORB on stocks in play** | 2.81 Sharpe, >1,600% net 2016–2023, top-20 RVOL (author-reported, in-sample); no independent OOS for this exact stock version | High: 1–3 trades/day, multi-R winners on catalyst days | High: replication shows ~17% win rate, fat-tail dependent; halt/gap/slippage; short squeezes | Medium-high: 5-min bars, RVOL, ATR, software stops, partial fills | Very strong: clear edge, invalidation, risk gate, audit trail | **8.5** |
| 2 | Plain 5-min ORB, no regime/portfolio layer | Same in-sample evidence; QuantConnect 2016 replication 2.40 Sharpe, but other years questioned by users | High | Very high: correlated signals, full-size losses, no regime brake | Medium | Strong, but looks overfit without OOS | 7.5 |
| 3 | SPY/QQQ intraday momentum (30-min noise-area + VWAP) | 1.33 Sharpe in-sample 2007–2024; **0.39 OOS 2024–2026**, underperforms SPY, p<0.001 | Medium: low vol, leverage-dependent | Medium: many small losses, OOS decay | High: one ETF, very liquid | Medium: honest OOS story is a cautionary tale | 6 |
| 4 | Pairs / statistical arbitrage (ETF and large-cap) | Long-run literature exists but has decayed; **not independently established** for an 8-day window | Low-medium: few convergence events | Low-medium: borrow, crowding, leg risk | Low: data, hedging, short borrow | Strong sophistication, weak short-window proof | 5.5 |
| 5 | Mean reversion (RSI-2 / VWAP bands on liquid ETFs) | **Not independently established**; weak after costs | Medium: many trades | High: negative skew, trending-market drawdowns | High | Medium-low: crowded retail narrative | 5 |
| 6 | ETF/sector momentum + daily trend following | Robust monthly/long-horizon evidence; not 8 days | Low: 1–3 signals in 8 days | Medium: overnight gaps, whipsaw | High | Low for this brief | 4 |
| 7 | ML-filtered ORB / RL execution | ML cross-sectional evidence, not intraday; RL is execution alpha, not signal alpha | Unknown; overfit risk | Medium-high: model risk, complexity | Low | High on Qoder usage, weak on edge | 4 |
| 8 | Options directional / volatility | No robust directional edge; short-vol tail risk; historical options data still developing | High variance, not high expectancy | Very high: gamma/vega/assignment, fees | Low: multi-leg, margin, exercise | Medium: looks sophisticated, risks score | 3.5 |

**Evidence notes.** The ORB paper’s 2.81 Sharpe and 1,600% return are **in-sample 2016–2023**; the QuantConnect replication only tested 2016 and users report weak other years. The strongest OOS evidence for the *family* is the SPY strategy: 19.6% annualized and 1.33 Sharpe in-sample, but 4.8% annualized, 0.39 Sharpe and 18.5% max drawdown OOS (May 2024–March 2026), underperforming buy-and-hold. Do not treat 2.81 as an expectation; treat it as an upper bound. For mean reversion, pairs, rotation, options, ML and RL, exact 8-day net expectancy is **not independently established**.

---

## 3. Recommended parameter sheet

| Area | Preliminary setting | Rationale |
|---|---|---|
| Account / venue | Competition paper account; US stocks/ETFs only; `market_id=100`; only `sim_trade_*` tools; never live `trading_*` | Paper-only rule; US margin account required for shorts |
| Sessions | Regular session 09:30–16:00 ET; entries 09:35–10:30; flat by 15:55; Nov 27 flat by 12:55; closed Nov 26 | Opening imbalance decays; final-day after-hours excluded; half-day close |
| Universe | Price ≥ $5; market cap ≥ $1B; 20-day ADV ≥ $50M; 20-day average 5-min dollar volume ≥ $2M; exclude halts/pending news | Competition floors are $1/$500M; buffers improve liquidity and borrow |
| Screener | `quote_stock_screen` for US price/mcap/turnover; then 5-min bars for RVOL | Dynamic “stocks in play” universe, not a static watchlist |
| ORB | High/low of 09:30–09:35 regular-session bars | 5-min is the best-documented window; test 15/30/60 as alternatives |
| RVOL | Today’s first-5-min volume ÷ median first-5-min volume of prior 14 sessions; ≥1.5; rank top 5–8 | Paper uses >1 and top 20; ≥1.5 avoids low-quality names |
| Confirmation | 5-min close beyond OR high/low; price on correct side of session VWAP; SPY above/below its OR/VWAP for longs/shorts | Avoids wick breakouts and fighting the tape; VWAP is a heuristic to test |
| Stop basis | 1.0 × **daily ATR(14)** beyond the breakout level; floor 0.35%, cap 2.5% of entry; skip if >2.5% | **Do not use 5-min ATR(14)**: it is too tight and will be noise-stopped. The paper uses daily ATR |
| Entry order | Marketable limit at breakout ±5 bps; 30-second validity; no chase >0.25×ATR; max 2 attempts/symbol/day | No native stop orders; limit controls slippage |
| Exit | Move stop to breakeven at +1R; trail at 1.5×ATR(5m) after +1.5R; time-stop at 12:00 if <+0.5R; exit all 15:55 | **No fixed 2R target**: ORB’s edge is the fat tail; a 2R cap truncates it |
| Risk/trade | 0.50% risk-on; 0.35% neutral; 0.25% risk-off; current regime = **risk-off** | Current SPY is 2.34% below its 20-day SMA with 22.9% annualized 20-day vol |
| Sizing | `qty = floor(min(risk$ / stop_distance, 0.15 × equity / entry, 0.05 × avg_5min_volume))`; ETFs 25% cap | Risk-based size can exceed the official 40% cap; always take the minimum |
| Portfolio caps | Max 5 positions; gross ≤100%; net beta ±30%; sector ≤30%; single stock ≤15%; single ETF ≤25% | 40% is a ceiling, not a target; correlation is the real risk |
| Daily limits | Max 3–5 new trades; ≤10 orders/day; daily loss 2% → flatten/halt; 3 consecutive stops → halve risk; 5 → halt | 200 orders/day is the official cap, not a goal |
| Drawdown | 6% from peak → flatten and halt; no reset | 8-session sprint; a 6% hole is hard to recover |
| Overnight | None; flat by the close every day | Avoids gap risk, financing, and final-day after-hours exclusion |
| Execution | Software stop checked every 5 seconds and on 1-min closes; log slippage; reconcile fills before retry | No native stop; stops do not guarantee a max loss |
| Regime | Risk-on: SPY > 20d SMA and vol < 20%; Neutral: mixed; Risk-off: SPY < 20d SMA and vol 20–30%; Crisis: vol > 30% or gap > 2% → 0.15% or stand aside | Current: risk-off; reduces size and trade count |
| Verification | `sim_trade_account_list` at start; confirm paper account, market ID, cash, positions; place a 1-share test order before session 1 | FAQ wording on OAuth permissions is ambiguous; verify sim trading works |

**Formulas.**

- `RVOL = V(09:30–09:35 today) / median[V(09:30–09:35, prior 14 sessions)]`.
- `TR = max(high − low, |high − prior close|, |low − prior close|)`; `ATR(14) = mean(last 14 daily TRs)`.
- `stop_distance = min(max(1.0 × ATR14, 0.0035 × entry), 0.025 × entry)`.
- `qty = floor(min(risk$ / stop_distance, 0.15 × equity / entry, 0.05 × avg_5min_volume))`.
- `risk$ = equity × risk_pct`; verify `qty × stop_distance ≤ 1.05 × risk$`.
- `E = p·avgWin − (1−p)·avgLoss − avgCost`; in R terms: `E_R = p·avgWin_R − (1−p)·avgLoss_R − cost_R`.
- `profit factor = (p·avgWin) / ((1−p)·avgLoss)`.

---

## 4. Risk manager specification

**Hard pre-trade gates (deterministic code, not LLM judgment):**

| Gate | Trigger | Action |
|---|---|---|
| Account | Not the competition paper account; `market_id ≠ 100`; equity < $95k; live tool selected | Reject all orders; alert |
| Time | Outside 09:35–10:30 entries; after 15:55; Nov 27 after 12:55; Nov 26 | Reject |
| Data | Last 5-min bar > 2 minutes old; SPY/QQQ data missing; quote stale | Reject; pause schedule |
| Universe | Price < $5; mcap < $1B; ADV < $50M; halted; pending news; short not borrowable | Reject |
| Signal | No 5-min close beyond OR; wrong side of VWAP; SPY filter fails; stop distance outside 0.35–2.5% | Reject |
| Portfolio | >5 positions; single stock >15%; single ETF >25%; gross >100%; net beta outside ±30%; sector >30% | Reject or resize |
| Order budget | >10 orders/day (self-imposed); duplicate decision ID; >200 official executed orders | Reject |
| Loss | Daily realized + unrealized ≤ −2% ($2,000); total drawdown ≤ −6% ($6,000) | Flatten; halt |
| Streak | 3 consecutive stop-outs | Halve risk for the day |
| Streak | 5 consecutive stop-outs; 5 rejected orders; API error rate >20% | Flatten; halt |
| Reconciliation | Cash/positions mismatch >1% vs expected | Halt; investigate |
| Manual | Operator presses kill switch | Flatten; halt |

**Sizing on a $100,000 account.**

- Risk-off: 0.25% = **$250** risk per trade.
- Entry $100; daily ATR $2.00; stop distance $2.00 (2.0%); qty = 125 shares; position $12,500 (12.5%); max loss ≈ $250 before slippage.
- If stop distance is $1.00 (1.0%), qty = 250 shares ($25,000, 25%) but the 15% single-stock cap binds → 150 shares ($15,000), max loss ≈ $150.
- If stop distance is $3.00 (3.0%) > 2.5% cap → **skip the trade**.

**Correlated exposure.**

- Net beta = Σ(position weight × 60-day beta vs SPY), capped at ±30%.
- Sector exposure ≤ 30%; semis/megacap-tech cluster ≤ 30%.
- If all signals are short, cap net short beta at −30%; do not let a single market-direction bet consume the portfolio.
- If two names are >0.8 correlated, treat them as one position for the 15%/30% caps.

**Kill switch.** Cancel all open orders; flatten with marketable limits; pause the cloud schedule; write an incident log with timestamp, trigger, positions, P&L, fills, and screenshots; notify Slack; require human review before resuming. The competition account cannot be reset, so the kill switch must trigger **before** the 6% drawdown becomes unrecoverable.

**Account-verification guardrails.**

- Call `sim_trade_account_list`; confirm the paper account and `market_id=100`.
- Compare `sim_trade_cash_info` and `sim_trade_position_list` to expected values.
- Place a 1-share limit order far from the market, confirm it appears in `sim_trade_history_order_list`, then cancel it.
- Use `sim_trade_max_buy_sell` before shorts to confirm sellable quantity.
- Never call `trading_*` or live `account_*` tools. All orders use `sim_trade_input_order`.
- OAuth caveat: the handbook setup lists Market Data + Watchlists (+ Accounts & Orders, Trade Execution), while the FAQ warns not to select live trading execution. This wording is ambiguous; verify the sim order path with the 1-share test before relying on automation. Do not enable live trading.

---

## 5. Evidence & validation plan

**What the evidence actually says.**

- Stock ORB: 7,000+ U.S. stocks, 2016–2023, top-20 RVOL, >1,600% net, 2.81 Sharpe, 36% annualized alpha (author-reported, in-sample). QuantConnect’s 2016-only replication reports 2.396 Sharpe and beta −0.042, but users report weak other years, a ~17% win rate, and material cost sensitivity.
- SPY intraday momentum: 1.33 Sharpe in-sample 2007–2024; **0.39 OOS 2024–2026**, 4.8% annualized, 18.5% max drawdown, underperforming SPY; the decline is statistically significant (p<0.001). This is a different strategy but the same intraday-momentum family. Treat it as a warning, not a direct refutation.
- Current regime (16 Nov 2026 close, Moomoo MCP): SPY 739.95 (−1.91% on the day), 2.34% below its 20-day SMA, 20-day realized vol 22.9% annualized, −4.0% over 22 sessions; QQQ 661.75 (−2.67%), 4.46% below its 20-day SMA, −8.75% from its 21 Oct close. 5 Nov was −4.41% for SPY and −6.49% for QQQ. This is a high-vol, risk-off tape: bidirectional ORB is appropriate; long-only breakouts are not.

**Five falsifiable tests.**

1. **OOS expectancy:** on 2024–2026 5-min data, net `E_R > 0.10` after costs. If ≤ 0, abandon the strategy.
2. **Profit factor / win rate:** ≥ 200 trades, PF ≥ 1.3, win rate ≥ 30%. If PF < 1.2, abandon. Do not require a 50% win rate; ORB is designed to win small often and lose small often, with a few large winners.
3. **Tail dependence:** top 5 trades ≤ 50% of net P&L. If > 80%, the result is a lottery; reduce size or abandon.
4. **Regime filter value:** walk-forward Sharpe and max drawdown better than plain ORB. If not, remove the filter; simpler is better.
5. **Execution reality:** live paper slippage ≤ 2× backtest assumption and fill rate ≥ 90%. If not, halve size or stop.

Add a **placebo test**: random entries with the same stop/exit must produce at least 0.2R lower expectancy. If the strategy cannot beat random entries, there is no edge.

**Data and method.**

- 5-min bars 2019–2026, 60-name universe, regular session only; 14-day RVOL/ATR; daily SPY regime.
- Costs: $0.99/order + $0.000166/share sold (cap $8.30) + 5–15 bps slippage per side; stop fills at next bar open ±10 bps; partial fills at 20× volume.
- Walk-forward: 12-month train, 3-month test, roll; final holdout 2024–2026; no parameter changes on the holdout.
- No look-ahead: use closed bars only; signal at the 09:35 close, fill at the next bar; never use the same bar’s high/low to trigger and fill; point-in-time universe; include delisted names if possible.
- Benchmarks: SPY buy-and-hold, random-entry, plain ORB, equal-weight stocks in play.

**How many trades are needed?** `n ≈ p(1−p)/SE²`. For `p=0.4`: SE 0.10 → 24 trades; SE 0.05 → 96; SE 0.03 → 267. Eight sessions at 1–3 trades/day gives 8–24 trades, so the 95% confidence interval on win rate is roughly ±20–30 percentage points. **The contest sample cannot validate the edge**; use 200+ historical trades and treat the eight sessions as a live demonstration, not proof. Report bootstrap confidence intervals and a deflated Sharpe ratio for multiple testing.

---

## 6. Judging strategy

| Weight | Category | What to submit | Official requirement vs recommendation |
|---|---|---|---|
| 30% | Strategy & risk design | One-page spec: edge hypothesis, exact rules, invalidation criteria, sizing formula, exposure caps, drawdown response, kill switch, failure modes; rejected-order log; post-mortem; pre-registered parameters; OOS test | **Official:** clear edge, invalidation, sizing, exposure/drawdown control, kill switch. **Recommendation:** show the 2.81 in-sample vs 0.39 OOS honestly; state that stops do not guarantee a maximum loss |
| 30% | Qoder + Cloud Agent usage | Architecture diagram: local Qoder for research/backtest/dashboard; Cloud coordinator + researcher + risk auditor + executor + reporter; schedule API; Vault; Channels; `AGENTS.md`; versioned skills; logs; screenshots; metrics | **Official:** use both local Qoder and Cloud Agents. **Recommendation:** a coordinator on a schedule, not every agent; pin skill versions; export the agent config with timestamp, Agent ID and QCA version |
| 25% | Risk-adjusted return | Net return, daily equity curve, Sharpe/Sortino, max drawdown, profit factor, expectancy, win rate, average win/loss, exposure, turnover, costs; compare to SPY/QQQ and random-entry; state confidence intervals | **Official:** risk-adjusted return. **Recommendation:** report process and statistical uncertainty; do not overclaim an eight-session result |
| 15% | Reusability & documentation | GitHub repo, README, `AGENTS.md`, skill zip, config, tests, backtest notebook, daily reports, deck; one-command reproduction; lessons learned | **Official:** runnable package + report. **Recommendation:** versioned skill + reproducibility + what worked/failed/why |

**Evidence to collect during the eight sessions:** daily signal/rejection/fill/slippage/P&L logs; 100% automated trade count; agent run count, decision count, rejection count; dashboard screenshots; Slack alert screenshots; schedule run history; pre/post-market reports; a final post-mortem. The 30% agent-usage score is won by **visible orchestration**, not by claiming the agent was smart.

---

## 7. Sources and uncertainty

**Primary sources checked.**

- Official challenge handbook: [moomoo-qoder-challenge-rgkelnyuzbm.qoder.website](https://moomoo-qoder-challenge-rgkelnyuzbm.qoder.website/)
- Moomoo Agent Hub: [github.com/MoomooOpen/moomoo-agent-hub](https://github.com/MoomooOpen/moomoo-agent-hub)
- Qoder Cloud: [qoder.com/cloud](https://qoder.com/cloud)
- Stock ORB paper: [SSRN 4729284](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4729284)
- SPY intraday momentum paper: [SSRN 4824172](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4824172)
- Independent OOS replication: [github.com/PazSheimy/spy-intraday-momentum-oos](https://github.com/PazSheimy/spy-intraday-momentum-oos)
- Serpa OOS paper: [SSRN 7290621](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=7290621) (403 when fetched; figures taken from the GitHub README and Swedroe summary)
- QuantConnect ORB replication: [quantconnect.com/research/18444](https://www.quantconnect.com/research/18444/opening-range-breakout-for-stocks-in-play/)
- Gao, Han, Li & Zhou, “Market intraday momentum”: [ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/S0304405X18301351)

**Inaccessible or non-text sources.**

- Qoder competition kickoff slides: response exceeded 10 MB; not processed.
- Moomoo strategy briefing: client-side slide renderer; no text content extracted.
- SSRN 7290621: 403 Forbidden; used the public GitHub replication and Swedroe’s summary instead.
- Historical options data: official FAQ says still in development; no options backtest was attempted.

**Assumptions and unknowns.**

- Moomoo 5-min bars are reliable and sufficient for RVOL/ATR; no tick-level queue position is modeled.
- Paper fills follow the published volume × 20 model; partial fills are possible.
- Short borrow is available for selected names; verify with `sim_trade_max_buy_sell`.
- The stock ORB edge may not survive 2024–2026 OOS; the $500M market-cap floor and borrow constraints may remove the small-cap component that drives the paper’s returns.
- The sim OAuth/order permission path is unverified; the handbook and FAQ conflict. Test with a 1-share order before session 1.
- Cloud Agent scheduling latency (5-minute interval) may be too slow for 5-minute ORB in fast markets; test the end-to-end signal-to-fill time.
- The 40% single-stock cap may be computed on total account assets including margin; use 15% to stay well inside it.
- The eight-session calendar assumes standard U.S. holidays; verify with `quote_trade_days`.

**No strategy can guarantee a win or a positive short-term return.** This plan maximizes the probability of a good risk-adjusted result and a strong rubric score, not certainty.
