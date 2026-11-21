# Ten current-regime strategies: weekly profit backtest

21 November 2026. The strategies come from *deep-research-report (8).md*. Each one is a separate USD 100,000 book that is **not compounded**, so every figure is **dollars per week on $100,000**. The data is local Moomoo bars from 4 January 2021 to Friday 20 November 2026: 307 weeks, of which 39 started with VIX ≥ 25 and 13 with VIX ≥ 30. VIX closed at 35.9 on 20 November.

## Bottom line

**With the report's own sizing, none of the ten makes meaningful money in a normal week.** That sizing risks $100–150 per trade with 10–20% notional caps.

- **2025–26 out of sample.** The best setting chosen on 2021–24 earns between −$42 and +$24 a week in 2025–26.
- **High-volatility weeks.** These are the closest match to the current regime, and here three strategies were positive under *every* parameter setting tried:
  - **Volume-conditioned gap reversal:** median +$173 a week when VIX ≥ 30.
  - **Noise-area momentum:** median +$103.
  - **VWAP continuation:** median +$42.
- **Small sample.** Those high-volatility figures rest on 13 weeks, so they are noisy.
- **Last week (16–20 November).**
  - **Trend strategies made money:**
    - VWAP continuation: median +$614 across its settings.
    - ORB: median +$227.
    - Noise-area: median +$108.
  - **Reversal strategies lost:**
    - Failed breakdown: median −$580.
    - ETF pairs: median −$316.

## Results

### Picked settings

Each strategy's setting is chosen on 2021–24 weekly P&L only, then shown on 2025–26, which played no part in the choice.

| Strategy | Settings tried | % profitable over the full period | Picked: 2021–24 $/wk | **2025–26 $/wk** | Worst week 2025–26 | VIX ≥ 25 weeks $/wk | VIX ≥ 30 weeks $/wk | Last week |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 7 ETF RSI-2 exhaustion | 24 | 62% | +12 | **+24** | −242 | +100 | +218 | −78 |
| 1 ORB breakout/breakdown | 216 | 3% | +1 | **+4** | −96 | +11 | +24 | −50 |
| 3 Failed-breakdown reversal | 18 | 0% | −5 | **+0** | −306 | −14 | −6 | −161 |
| 9 ETF pairs reversion | 9 | 0% | −7 | **−1** | −141 | −12 | +8 | −141 |
| 8 Last-hour momentum | 24 | 0% | −2 | **−7** | −344 | +2 | +13 | −8 |
| 5 Gap-and-go | 36 | 28% | +27 | **−10** | −649 | −21 | +35 | −247 |
| 4 Sector relative strength | 27 | 15% | +10 | **−15** | −624 | +68 | +156 | +42 |
| 10 Noise-area momentum | 36 | 100% | +48 | **−24** | −699 | +152 | +217 | +888 |
| 6 Volume gap reversal | 18 | 0% | +3 | **−38** | −575 | +39 | +184 | −2 |
| 2 VWAP trend continuation | 54 | 7% | +20 | **−42** | −898 | +9 | +13 | +248 |

### Across every setting tried

This is more robust than a single picked setting. Figures are medians in $/wk; percentages are the share of settings that were profitable.

| Strategy | Full period | 2025–26 | VIX ≥ 25 | VIX ≥ 30 | Last week |
|---|---:|---:|---:|---:|---:|
| 6 Volume gap reversal | −16 | −23 (6%) | +20 (100%) | **+173 (100%)** | +6 (61%) |
| 10 Noise-area momentum | +14 | −9 (19%) | **+63 (100%)** | **+103 (100%)** | +108 (50%) |
| 2 VWAP trend continuation | −10 | −42 (0%) | −54 (9%) | +42 (74%) | **+614 (100%)** |
| 7 ETF RSI-2 exhaustion | +4 | **+11 (96%)** | +13 (50%) | +35 (50%) | −78 (0%) |
| 8 Last-hour momentum | −13 | −15 (0%) | −2 (38%) | +20 (62%) | −85 (4%) |
| 5 Gap-and-go | −3 | −17 (3%) | −31 (6%) | +15 (75%) | −143 (6%) |
| 4 Sector relative strength | −6 | −25 (0%) | −3 (48%) | +5 (59%) | +27 (89%) |
| 9 ETF pairs reversion | −13 | −9 (0%) | −19 (0%) | +0 (56%) | −316 (0%) |
| 1 ORB breakout/breakdown | −27 | −29 (12%) | −36 (5%) | −12 (33%) | +227 (81%) |
| 3 Failed-breakdown reversal | −21 | −26 (6%) | −58 (0%) | −58 (6%) | −580 (0%) |

Picked settings, chosen on 2021–24:

| Strategy | Setting | Trades per week |
|---|---|---:|
| ORB | 15-minute range, RVOL ≥ 2, 0.35 ATR stop, 2R target, both directions, ETFs only | 0.5 |
| VWAP continuation | pullback 0.10 ATR, stop 0.25 ATR, 3R target | 9.2 |
| Failed breakdown | 1.5% drop, VWAP confirmation, VWAP target | 0.6 |
| Sector RS | top 3 sectors, 0.75 ATR stop, 5-day relative strength only | 6.8 |
| Gap-and-go | gap 1.5%, RVOL 1.5, 5-minute range, hold to the close | 4.1 |
| Gap reversal | gap 1.25%, RVOL 1.75, exit 12:00 | 4.7 |
| RSI-2 | RSI-2 ≤ 5, ≥ 1 ATR below the 5-day high, 15-minute-high break, 2R target | 0.5 |
| Last-hour momentum | 60-minute window, top and bottom 10%, RVOL 1.5, long/short | 1.1 |
| Pairs | 90-day fit, enter at \|z\| ≥ 2.75 | 1.8 |
| Noise-area | band 1.0, decisions at 10:30, 11:30, 13:30 and 14:30, 40% allocation, long/short | 2.6 |

## How to read the dollar figures

- **Small by design.** The report risks 0.10–0.15% of equity per trade and caps notional at 10–20%. A $150 planned loss on a trade cannot produce more than a few hundred dollars in a typical week.
- **P&L scales linearly with size.** Doubling risk doubles both the averages and the worst weeks.
- **"Winning weeks" counts weeks with a profit.** Weeks with no trade count as non-winning, so strategies that trade rarely, such as ORB at about one trade every two weeks, show low percentages.

## Method and data limits

| Item | Detail |
|---|---|
| Fills | Next bar open. Stops and targets fill at their price when touched; if both are touched in one bar, the stop is assumed first. |
| Costs | 1 bp slippage + 0.12 bp half-spread per side; $0.99 per order + $0.000166/share on sells; at most 1% of the prior bar's volume |
| Bars | SPY 1-minute; QQQ, IWM and 40 stocks 5-minute; sector ETFs 60-minute; VIX daily. Triggers use 5-minute closes rather than the report's 1-minute closes. |
| Strategy 4 | Ranked at 10:30 on 60-minute bars (the report says 10:00) |
| Strategy 9 | Daily bars (enter at the next open, exit at that session's close) |
| Not modeled | No pre-market volume or news catalysts (gap-and-go uses opening volume instead), no breadth (failed breakdown and sector RS), no spreads |
| Stocks | Strategies 1 (with stocks), 5, 6 and 8 trade the 40-name research universe chosen in 2026, which inflates long results through survivorship |
| Risk gates | No portfolio risk gates; each book is pure strategy P&L |
| Sample | The VIX ≥ 30 sample is 13 weeks, and last week is a single week |

## Reproduce

From the research worktree's `moomoo-dual-strategy` folder:

```bash
python reports/claude_regime/fetch_extra.py           # read-only Moomoo bars to 2026-11-20 into data-cache/regime_extra
python reports/claude_regime/run_grid.py 1            # likewise "2 3 7", "4 9 10", "5 6 8"; writes results/grid_*.csv
python reports/claude_regime/summarize.py             # results/summary.csv and summary.md
```
