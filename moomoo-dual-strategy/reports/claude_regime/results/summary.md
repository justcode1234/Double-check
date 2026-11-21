| Strategy | Settings tried | % settings profitable | Typical setting $/wk | Picked on 2021-24: $/wk then | → 2025-26 $/wk | 2025-26 winning weeks | Worst week 2025-26 | VIX≥25 weeks $/wk | VIX≥30 weeks $/wk | Last week (Nov 16-20) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 7 ETF RSI-2 exhaustion | 24 | 62% | +4 | +12 | **+24** | 15% | -242 | +100 (39 wks) | +218 (13 wks) | -78 |
| 1 ORB breakout/breakdown | 216 | 3% | -27 | +1 | **+4** | 4% | -96 | +11 (39 wks) | +24 (13 wks) | -50 |
| 3 Failed breakdown reversal | 18 | 0% | -21 | -5 | **+0** | 15% | -306 | -14 (39 wks) | -6 (13 wks) | -161 |
| 9 ETF pairs reversion | 9 | 0% | -13 | -7 | **-1** | 13% | -141 | -12 (39 wks) | +8 (13 wks) | -141 |
| 8 Last-hour momentum | 24 | 0% | -13 | -2 | **-7** | 12% | -344 | +2 (39 wks) | +13 (13 wks) | -8 |
| 5 Gap-and-go momentum | 36 | 28% | -3 | +27 | **-10** | 47% | -649 | -21 (39 wks) | +35 (13 wks) | -247 |
| 4 Sector relative strength | 27 | 15% | -6 | +10 | **-15** | 49% | -624 | +68 (39 wks) | +156 (13 wks) | +42 |
| 10 Noise-area momentum | 36 | 100% | +14 | +48 | **-24** | 37% | -699 | +152 (39 wks) | +217 (13 wks) | +888 |
| 6 Volume gap reversal | 18 | 0% | -16 | +3 | **-38** | 36% | -575 | +39 (39 wks) | +184 (13 wks) | -2 |
| 2 VWAP trend continuation | 54 | 7% | -10 | +20 | **-42** | 47% | -898 | +9 (39 wks) | +13 (13 wks) | +248 |

Picked settings (chosen on 2021-24 weekly P&L only):
- 1 ORB breakout/breakdown: {'or_min': 15, 'rvol': 2.0, 'stop_atr': 0.35, 'target_r': 2.0, 'dir': 'both', 'universe': 'etf'} | 0.48 trades/week
- 4 Sector relative strength: {'top': 3, 'stop_atr': 0.75, 'weights': [0.0, 1.0, 0.0]} | 6.82 trades/week
- 9 ETF pairs reversion: {'lookback': 90, 'entry': 2.75, 'exit': 0.75} | 1.8 trades/week
- 10 Noise-area momentum: {'band': 1.0, 'times': 'report', 'alloc': 0.4, 'shorts': True} | 2.63 trades/week
- 2 VWAP trend continuation: {'pullback_atr': 0.1, 'stop_atr': 0.25, 'target_r': 3.0, 'need_struct': True} | 9.24 trades/week
- 3 Failed breakdown reversal: {'drop': 0.015, 'confirm': 'vwap', 'target': 'vwap'} | 0.56 trades/week
- 7 ETF RSI-2 exhaustion: {'rsi': 5, 'atr_below': 1.0, 'trigger': 'orh', 'target_r': 2.0, 'risk_size': False} | 0.52 trades/week
- 5 Gap-and-go momentum: {'gap': 0.015, 'rvol': 1.5, 'or_min': 5, 'target': 'eod'} | 4.14 trades/week
- 6 Volume gap reversal: {'gap': 0.0125, 'rvol': 1.75, 'exit': '12:00'} | 4.73 trades/week
- 8 Last-hour momentum: {'window': 60, 'tail': 0.1, 'rvol': 1.5, 'shorts': True} | 1.14 trades/week