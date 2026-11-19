# Strategy B - SPY Noise-Area intraday momentum (SIMULATED backtest spec)

Source: Zarattini, Aziz & Barbon (2024), *Beat the Market: An Effective Intraday Momentum Strategy for S&P500 ETF
(SPY)*, SSRN. Code: `src/dual_strategy/research/strat_b.py`; cross-checked bar-for-bar against the repo's live
signal code `noise_area.compute_noise_area` (sigma, VWAP, dividend-adjusted previous close, bands) in
`tests/test_research_engine.py`.

## Signal (all variants)

| Item | Rule |
|---|---|
| Data | SPY 1-minute RTH bars, raw prices (bar = END time, as Moomoo stamps it) |
| sigma(t, HH:MM) | mean over the 14 prior sessions of abs(close at HH:MM / 09:30 open - 1). B0: the 14 most recent sessions that have that bar (half-days skipped); B1: the exact 14 prior sessions must all have it (repo baseline) |
| Bands | UB = max(open_0930, prev_close - dividend) x (1 + sigma); LB = min(...) x (1 - sigma) |
| VWAP | cumulative session VWAP from Moomoo turnover / volume (HLC3 proxy as ablation) |
| Decisions | on the 1m bar ending 10:00, 10:30, ..., 15:30 |
| Target | long if close > UB and > VWAP; short if close < LB and < VWAP; otherwise flat |
| Fill | market order at the next 1m open (+delay bars). The paper filled at the decision price itself, so every variant here is one bar later than the paper |

The stateless target equals the paper's trailing stop (max(UB, VWAP) for longs) evaluated at decision times. A
literal stateful trail is the `B1-stateful-trail` ablation.

## B0 - published spec (replication)

Long and short; size = min(4, 2% / sigma_SPY14) x equity, where sigma_SPY14 = sample std of the 14 prior daily total
returns; exit at the 16:00 closing price; costs $0.0035/share commission + $0.001/share slippage; native fills;
no loss lock. `B0-100` uses 100% NAV instead of vol-targeted leverage (the 4x cap exceeds any retail account here).

## B1 - competition

40% of start-of-day equity per position (handbook exposure cap); long and short (B1-LO: long-only); flatten 5
minutes before close; daily loss lock at -1.25% (checked every `monitor_every` minutes, flatten next bar + delay);
competition fees + 5 bps slippage per fill; partial fills limited to 20x bar volume; deterministic reject/miss
stress available.

## Stress and ablations

Emergency-exit / monitoring delay: `monitor_every` 5 / 15 / 30 minutes; order delay 1 / 2 / 5 bars; stop modes;
rejects and missed decisions 2%; participation 0.05x; HLC3 VWAP; half-day-skipping sigma; no loss lock; close exit;
competition fees on B0; slippage 0-15 bps.

## Literature baseline

The paper reports a 2007-2024 Sharpe of 1.33 for the published rule, with fills at the decision price. This run is
an independent test with one-bar-later fills and explicit costs. The repo's earlier audit (`reports/final_audit.md`)
is unchanged and separate. QQQ was optional and was **NOT RUN** (intraday history not fetched).
