# Strategy C - SPY intraday VWAP mean reversion (PRE-REGISTERED HYPOTHESIS, SIMULATED)

Code: `src/dual_strategy/research/strat_c.py`. Frozen parameters: `config/c_frozen.json` (sha256 inside the file;
the runner refuses to start if the file was edited after freezing).

**There is no published paper or independent backtest of this exact rule.** It is a hypothesis written down before
testing, so every result below is a first test, not a replication.

## Rule

| Item | Rule |
|---|---|
| Signal | d = (close_5m - VWAP_5m_HLC3) / ATR14, where ATR14 uses the 14 prior daily sessions only |
| Setup | on a finalized 5m bar ending at or after 10:00: long setup when d <= -band; short setup when d >= +band |
| Trigger | a later finalized 5m close back inside the band (d > -band long / d < +band short) within 6 bars; otherwise the setup expires and must fully reset (d back inside the band) before re-arming |
| Entry | market at the next 1m open (+delay); no entries after 14:30 (half-days: close - 150 min) |
| Stop | entry -/+ stop_atr x ATR14, checked on every 1m bar *before* the target (stop assumed first when one bar touches both) |
| Target | a 1m close back at or through the latest finalized 5m VWAP -> market exit next 1m open |
| Time exits | max hold (minutes from entry); hard exit 15:50 (half-days: close - 10 min) |
| Position | one at a time; after an exit both sides must reset before re-arming |

Preregistered default: band 0.30, stop 0.25 ATR, hold 60 minutes.

## Parameter freeze (before any validation / OOS run)

Development-only grid (2021-01-04 .. 2022-12-30): band {0.20, 0.30, 0.40} x stop {0.20, 0.25, 0.35} ATR x hold
{30, 60, 90} min, competition sizing, 5 bps. Selection rule: highest development daily Sharpe among cells with >= 100
trades; if no cell has a positive Sharpe, freeze the preregistered default and record a development FAIL. All 27
development results are in `config/c_frozen.json` and `ablations.md`. The 2023 validation year is reported but never
used to re-select. Walk-forward re-applies the same rule on rolling 2-year windows and tests the next 6 months.

## Variants

- **C0:** 100% NAV, paper costs ($0.0035 + $0.001/share), native stops, no loss lock.
- **C1 (competition):** shares = min(0.25% equity / stop distance, 40% equity / price, buying power); competition
  fees + 5 bps; software stops (+5 bps); daily loss lock -1.25%.
- **C1-LO:** long-only fallback.
- Ablations: turnover VWAP; expiry 3 / 12 bars; native stops; no loss lock; preregistered cell (if different).

## Prior evidence (why this is only a hypothesis)

- Gao, Han, Li & Zhou (2018), "Market intraday momentum", *Journal of Financial Economics* 129(2): the first
  half-hour return predicts the last half-hour return of SPY - momentum, not reversion.
- Baltussen, Da, Lammers & Martens (2021), "Hedging demand and market intraday momentum", *JFE* 142(1): intraday
  momentum across 60+ futures markets, linked to gamma hedging.
- Zarattini & Aziz (2023), "Volume Weighted Average Price (VWAP): The Holy Grail for Day Trading Systems", SSRN:
  VWAP used as a *trend* filter on QQQ/TQQQ, again the opposite sign.
- Heston, Korajczyk & Sadka (2010), "Intraday patterns in the cross-section of stock returns", *Journal of Finance*
  65(4): periodicity in half-hour returns, mostly cross-sectional, not an index VWAP-reversion rule.
- Berkowitz, Logue & Noser (1988), "The total cost of transactions on the NYSE", *Journal of Finance* 43(1): VWAP as
  an execution benchmark - the origin of VWAP as an institutional anchor price, not evidence of a tradable edge.

The literature therefore offers no direct support and some contrary evidence (index intraday momentum). C is
expected to have a small gross edge at best, so its viability depends heavily on costs.
