# SPY Noise-Area ablation

The independent replay rebuilt 14-session same-time moves from one-minute bars, anchored the bands to the current open and dividend-adjusted prior close, and used each completed 30-minute decision tick with turnover VWAP. It replayed next-bar opens and daily flattening. The frozen portfolio's planned leverage is min(4, 2% / prior 14-day daily volatility). The following OOS runs reset at $50,000 unless stated.

| Cumulative change | Trades | Net return |
|---|---:|---:|
| Frozen B0 | 645 | +33.031% |
| Replace vol-target leverage with fixed 100% exposure | 645 | +9.356% |
| Reduce fixed exposure to 40%, paper costs | 645 | +3.742% |
| Challenge commission/platform/sale fees, zero price slippage | 645 | +1.609% |
| Add one bp/side slippage, B2 | 645 | −3.566% |
| Require exactly the last 14 sessions, with no late ticks after relevant half-days | 627 | −3.561% |
| Flatten five minutes before close | 627 | −3.170% |
| Add the B1 intraday 1.25% loss lock, zero extra latency | 627 | −3.170% |
| Add one extra bar of latency to this configuration | 627 | −4.444% |
| B1 long-only at baseline timing | 333 | −1.772% |

The original runner also ran each change on top of B0 alone and exported both sequences. B1's worst modeled intraday mark was −0.524%, so the 1.25% daily lock never triggered in this OOS window. Longer monitoring, rejection, delay and capacity stress cases were reproduced. The competition's separate broker-level forced-liquidation rule and trade-by-trade volume matching are outside the bar model. The B0 comparison uses the author's 2% target and 4× cap with this dataset's turnover VWAP and next-minute fills. An author-style HLC3 VWAP and decision-close sensitivity returned +32.353% and 659 trades. The exact academic sample and vendor data are unavailable locally.

All 627 B1 entries are present in B0. B0 contains 645 entries, of which 18 are absent in B1. Of the 627 shared entries, 209 exit differently. B0 and B2 share 645 entries and the same exit rules. B0's mean raw gross return was 1.645 bp/trade across all 645 trades and 0.097 bp/trade in 2026. B1 costs average 3.017 bp/trade. At the B1 fee schedule zero slippage yields +1.846%, one bp/side −3.170% and five bp/side −20.637%. Historical NBBO, queue state and time-stamped execution are needed to determine which assumptions were achievable.

The handbook starts with $100,000 and describes a 40% per-stock/ETF cap using initial account assets. Applying a $40,000 SPY cap and $100,000 starting cash in the independent replay yielded −1.864%. The handbook also describes the cap in a later example in terms of account value, so this is a sensitivity to the stated initial-assets rule. The comparison preserves the historical strategy's risk and execution assumptions. [Challenge handbook](https://moomoo-qoder-challenge-rgkelnyuzbm.qoder.website/).

B0 lost 1.783% over the reset 2025–26 window and B0-100 gained 2.593%. B0's full-window 95% block-bootstrap total-return interval includes zero. The original paper's sample overlaps the early part of the local 2024 window. The independent IQFeed replication's repository reports out-of-sample Sharpe 0.39 for a different period. Its licensed input data are unavailable in the public repository. [Author's SPY paper](https://concretumgroup.com/wp-content/uploads/2026/02/Beat-the-Market.pdf) · [External IQFeed replication](https://github.com/PazSheimy/spy-intraday-momentum-oos) · [Original ablation CSV](reproduction/six_strategy/B_step_attribution.csv) · [Independent trades](stock_BC_trade_comparison.csv).
