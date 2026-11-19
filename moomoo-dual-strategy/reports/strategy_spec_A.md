# Strategy A - 5-minute Opening Range Breakout on "Stocks in Play" (SIMULATED backtest spec)

Source: Zarattini, Barbon & Aziz (2024), *A Profitable Day Trading Strategy for the U.S. Equity Market*, SSRN.
Code: `src/dual_strategy/research/strat_a.py`. Competition rules: `spec/prompt.md` section 2, `config/orb.yaml`.

## Status

- **A0 (faithful replication): BLOCKED - DATA.** The paper ranks *all* US stocks each morning by opening relative
  volume. We have no point-in-time universe, no historical market caps and no 1-minute stock bars. The A0-R row is
  the paper's rules run on our 40-stock list and is **EXPLORATORY** only.
- **A1 (competition): RESTRICTED-UNIVERSE EXPLORATORY.** The universe is the 40 highest-turnover US stocks on
  2026-11-18 (`config/research_universe.json`). That list is survivorship- and look-ahead-biased: it holds today's
  winners, which were by construction heavily traded. Nothing on this universe can support a GO.

## Common rules (both variants)

| Item | Rule |
|---|---|
| Bars | Moomoo 5-minute RTH bars, raw prices; bar j ends 09:35 + 5j |
| Eligibility (session t) | first-bar open > $5; ADV14 >= 1,000,000 shares; ATR14 > $0.50; RVOL >= 1.0; all three from the 14/15 *prior* sessions, split-adjusted to session-t shares; the session's 5m bars must be complete |
| RVOL | first-5-minute volume_t / mean(first-5-minute volume, 14 prior sessions) |
| Direction | 09:30-09:35 candle: close > open long, close < open short, doji skipped |
| Ranking | top N by RVOL (ties by symbol) |
| Stop | 10% of ATR14 from the *actual* entry price (QuantConnect variant: from the opening-range level, ablation) |
| Exit | stop, or end of session |

## A0-R (paper rules, exploratory)

Resting stop entry at the opening-range high (long) / low (short), fill at max(open, level); top 20;
size = min(1% equity risk / stop distance, 4x equity / 20 / price); $0.0035/share commission, no extra slippage;
native stops; exit at the 16:00 close; no daily loss lock, no concurrency or sector caps.

## A1 (competition)

| Item | Rule |
|---|---|
| Entry | a finalized 5m bar trades beyond the opening range -> market order at the next 5m open (+delay); no fills after 10:30 |
| Size | min(0.25% equity risk / stop distance, 15% equity per name), then gross <= 100% equity, sector <= 30% equity, max 4 concurrent, max 1% of the last closed bar's volume |
| Stops | software stop: native stop price worsened 5 bps (polling latency) |
| Exit | stop, daily loss lock at -1.25% of start-of-day equity (flatten next bar), flatten 5 minutes before close |
| Costs | $0.99/order + $0.000166/share sold (cap $8.30) + 5 bps adverse slippage per fill |
| A1-LO | long-only |

**Not implemented:** market cap >= $1B (no point-in-time caps). The competition drawdown ladder (2% / 3.5% / 5%)
is defined for a 4-week contest, so it is reported (`backtest_breakdowns.md`), not applied to multi-year runs.

## Intrabar ambiguity

5m bars do not reveal whether the entry or the stop came first inside the bar that triggered a resting-stop
entry. Baseline: if that bar also reaches the stop, the trade is stopped (pessimistic). The ablation
`A0-R-optimistic` assumes the reverse. For bar-close-market entries the ambiguity does not arise.

## Ablations

QuantConnect range-anchored stop; resting-stop and touch-market entries for A1; RVOL 0 / 1.5 / 2; top 4 / 20;
stop 0.05 / 0.20 / 0.25 ATR; no entry deadline; no loss lock; universe breadth (20 vs 40 names); competition
fees on A0-R; slippage 0-15 bps; execution stress.
