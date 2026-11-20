# Data inventory (offline backtest, generated)

Generated 2026-11-19T05:11:25+00:00. Source: Moomoo OpenAPI via `moomoo` CLI v0.5.0 `history-kline` (quote OAuth), cached under `data-cache/history/` (not in Git). Window 2020-11-02 .. 2026-11-17 (first 2 months = indicator warm-up only).

Intraday bars are RAW (autype 0), regular session only (extended_time 0); Moomoo `time_key` = bar END. Daily bars fetched raw (autype 0) and forward-adjusted (autype 1). No paid data was bought; nothing was interpolated or filled: a missing bar stays missing (stale data) and a session with any missing bar is flagged incomplete.

## Coverage

| symbol | bars | first_intraday | sessions_with_data | complete_sessions | missing_sessions_after_first | incomplete_sessions | bars_out_of_rth_dropped | duplicate_bars_dropped | daily_rows | sector |
|---|---|---|---|---|---|---|---|---|---|---|
| SPY | 1m | 2020-11-02 | 1518 | 1516 | 0 | 2 | 0 | 0 | 1981 | ETF |
| NVDA | 5m | 2020-11-02 | 1518 | 1518 | 0 | 0 | 0 | 0 | 1981 | Semiconductors |
| MU | 5m | 2020-11-02 | 1518 | 1518 | 0 | 0 | 0 | 0 | 1981 | Semiconductors |
| AMD | 5m | 2020-11-02 | 1518 | 1518 | 0 | 0 | 0 | 0 | 1981 | Semiconductors |
| SPCX | 5m | 2026-06-12 | 110 | 109 | 0 | 1 | 0 | 0 | 110 | Aerospace & Defense |
| SNDK | 5m | 2025-02-13 | 443 | 439 | 0 | 4 | 0 | 0 | 443 | Computer Hardware |
| TSLA | 5m | 2020-11-02 | 1518 | 1518 | 0 | 0 | 0 | 0 | 1981 | Auto Manufacturers |
| AAPL | 5m | 2020-11-02 | 1518 | 1518 | 0 | 0 | 0 | 0 | 1981 | Consumer Electronics |
| AMZN | 5m | 2020-11-02 | 1518 | 1518 | 0 | 0 | 0 | 0 | 1981 | Internet Retail |
| AVGO | 5m | 2020-11-02 | 1518 | 1518 | 0 | 0 | 0 | 0 | 1981 | Semiconductors |
| MSFT | 5m | 2020-11-02 | 1518 | 1518 | 0 | 0 | 0 | 0 | 1981 | Software - Infrastructure |
| GOOGL | 5m | 2020-11-02 | 1518 | 1518 | 0 | 0 | 0 | 0 | 1981 | Internet Content & Information |
| META | 5m | 2020-11-02 | 1518 | 1518 | 0 | 0 | 0 | 0 | 1981 | Internet Content & Information |
| TSM | 5m | 2020-11-02 | 1518 | 1516 | 0 | 2 | 0 | 0 | 1981 | Semiconductors |
| INTC | 5m | 2020-11-02 | 1518 | 1518 | 0 | 0 | 0 | 0 | 1981 | Semiconductors |
| UNH | 5m | 2020-11-02 | 1518 | 1517 | 0 | 1 | 0 | 0 | 1981 | Healthcare Plans |
| NBIS | 5m | 2024-10-21 | 521 | 521 | 0 | 0 | 0 | 0 | 1317 | Internet Content & Information |
| CRWV | 5m | 2025-03-28 | 413 | 412 | 0 | 1 | 0 | 0 | 413 | Software - Infrastructure |
| WMT | 5m | 2020-11-02 | 1518 | 1516 | 0 | 2 | 0 | 0 | 1981 | Discount Stores |
| LLY | 5m | 2020-11-02 | 1518 | 1518 | 0 | 0 | 0 | 0 | 1981 | Drug Manufacturers - General |
| MRVL | 5m | 2020-11-02 | 1518 | 1518 | 0 | 0 | 0 | 0 | 1981 | Semiconductors |
| PANW | 5m | 2020-11-02 | 1518 | 1518 | 0 | 0 | 0 | 0 | 1981 | Software - Infrastructure |
| TTWO | 5m | 2020-11-02 | 1518 | 1518 | 0 | 0 | 0 | 0 | 1981 | Electronic Gaming & Multimedia |
| BRK.B | 5m | 2020-11-02 | 1518 | 1518 | 0 | 0 | 0 | 0 | 1981 | Insurance - Diversified |
| TJX | 5m | 2020-11-02 | 1518 | 1517 | 0 | 1 | 0 | 0 | 1981 | Apparel Retail |
| MSTR | 5m | 2020-11-02 | 1518 | 1518 | 0 | 0 | 0 | 0 | 1981 | Software - Application |
| GEV | 5m | 2024-03-27 | 663 | 661 | 1 | 2 | 0 | 0 | 664 | Specialty Industrial Machinery |
| AES | 5m | 2020-11-02 | 1518 | 1518 | 0 | 0 | 0 | 0 | 1981 | Utilities - Diversified |
| BE | 5m | 2020-11-02 | 1518 | 1517 | 0 | 1 | 0 | 0 | 1981 | Electrical Equipment & Parts |
| CRWD | 5m | 2020-11-02 | 1518 | 1518 | 0 | 0 | 0 | 0 | 1870 | Software - Infrastructure |
| DELL | 5m | 2020-11-02 | 1518 | 1517 | 0 | 1 | 0 | 0 | 1981 | Computer Hardware |
| V | 5m | 2020-11-02 | 1518 | 1517 | 0 | 1 | 0 | 0 | 1981 | Credit Services |
| CAT | 5m | 2020-11-02 | 1518 | 1518 | 0 | 0 | 0 | 0 | 1981 | Farm & Heavy Construction Machinery |
| ASML | 5m | 2020-11-02 | 1518 | 1518 | 0 | 0 | 0 | 0 | 1981 | Semiconductor Equipment & Materials |
| PLTR | 5m | 2020-11-02 | 1518 | 1517 | 0 | 1 | 0 | 0 | 1541 | Software - Infrastructure |
| MDLN | 5m | 2025-12-17 | 231 | 230 | 0 | 1 | 0 | 0 | 231 | Medical Instruments & Supplies |
| TGT | 5m | 2020-11-02 | 1518 | 1517 | 0 | 1 | 0 | 0 | 1981 | Discount Stores |
| LITE | 5m | 2020-11-02 | 1518 | 1518 | 0 | 0 | 0 | 0 | 1981 | Communication Equipment |
| SKHY | 5m | 2026-07-10 | 92 | 91 | 0 | 1 | 0 | 0 | 92 | Semiconductors |
| CRM | 5m | 2020-11-02 | 1518 | 1518 | 0 | 0 | 0 | 0 | 1981 | Software - Application |
| STX | 5m | 2020-11-02 | 1518 | 1518 | 0 | 0 | 0 | 0 | 1981 | Computer Hardware |


## Corporate actions

Splits come from `moomoo corporate-actions` and are verified against every >3% jump of the raw/adjusted daily close ratio (`verify_splits`). Intraday prices stay raw; only cross-session features (ATR14, ADV14, RVOL baseline) are split-adjusted, using splits between the two sessions only.

| symbol | splits_listed | unmatched_observed | unmatched_listed | verified |
|---|---|---|---|---|
| SPY | - | - | - | PASS |
| NVDA | 2000-06-27 x2; 2001-09-12 x2; 2006-04-07 x2; 2007-09-11 x1.5; 2021-07-20 x4; 2024-06-10 x10 | - | - | PASS |
| MU | 2000-05-02 x2 | - | - | PASS |
| AMD | 2000-08-22 x2 | - | - | PASS |
| SPCX | - | - | - | PASS |
| SNDK | - | - | - | PASS |
| TSLA | 2020-08-31 x5; 2022-08-25 x3 | - | - | PASS |
| AAPL | 1987-06-16 x2; 2000-06-21 x2; 2005-02-28 x2; 2014-06-09 x7; 2020-08-31 x4 | - | - | PASS |
| AMZN | 1998-06-02 x2; 1999-01-05 x3; 1999-09-02 x2; 2022-06-06 x20 | - | - | PASS |
| AVGO | 2024-07-15 x10 | - | - | PASS |
| MSFT | 1998-02-23 x2; 1999-03-29 x2; 2003-02-18 x2 | - | - | PASS |
| GOOGL | 2014-04-03 x1.998; 2022-07-18 x20 | - | - | PASS |
| META | - | - | - | PASS |
| TSM | 1998-08-26 x1.45; 2000-05-15 x1.28; 2001-06-26 x1.4 | - | - | PASS |
| INTC | 1993-06-07 x2; 1995-06-19 x2; 1997-07-14 x2; 1999-04-12 x2; 2000-07-31 x2 | - | - | PASS |
| UNH | 2000-12-26 x2; 2003-06-19 x2; 2005-05-31 x2 | - | - | PASS |
| NBIS | - | - | - | PASS |
| CRWV | - | - | - | PASS |
| WMT | 1999-04-20 x2; 2024-02-26 x3 | - | - | PASS |
| LLY | 1995-12-21 x2; 1997-10-16 x2 | - | - | PASS |
| MRVL | 2004-06-29 x2; 2006-07-25 x2 | - | - | PASS |
| PANW | 2022-09-14 x3; 2024-12-16 x2 | - | - | PASS |
| TTWO | 2005-04-12 x1.5 | - | - | PASS |
| BRK.B | 2010-01-21 x50 | - | - | PASS |
| TJX | 1997-06-27 x2; 1998-06-26 x2; 2002-05-09 x2; 2012-02-03 x2; 2018-11-07 x2 | - | - | PASS |
| MSTR | 2000-01-27 x2; 2002-07-31 x0.1; 2024-08-08 x10 | - | - | PASS |
| GEV | - | - | - | PASS |
| AES | 1997-08-29 x2; 2000-06-02 x2 | - | - | PASS |
| BE | - | - | - | PASS |
| CRWD | 2026-07-02 x4 | - | - | PASS |
| DELL | 2021-11-02 x1.977 | - | - | PASS |
| V | 2015-03-19 x4 | - | - | PASS |
| CAT | 1997-07-14 x2; 2005-07-14 x2 | - | - | PASS |
| ASML | 1997-05-12 x2; 1998-05-08 x2; 2000-04-17 x3; 2007-10-01 x0.8889; 2012-11-29 x0.77 | - | - | PASS |
| PLTR | - | - | - | PASS |
| MDLN | - | - | - | PASS |
| TGT | 1998-05-01 x2; 2000-07-20 x2 | - | - | PASS |
| LITE | - | - | - | PASS |
| SKHY | - | - | - | PASS |
| CRM | 2013-04-18 x4 | - | - | PASS |
| STX | - | - | - | PASS |


SPY dividends in window: 49 ex-dates (subtracted from the prior close for the Noise-Area gap, as in the paper).


## Known data limitations

- **No point-in-time stock universe.** The 40 stocks are today's top Moomoo turnover names (`config/research_universe.json`), i.e. survivorship- and look-ahead-biased. Faithful A0 is BLOCKED - DATA; every A result is RESTRICTED-UNIVERSE EXPLORATORY.
- Market-cap >= $1B (spec) is not checked historically (no point-in-time market caps).
- Sector labels are today's Moomoo INDUSTRY plate, not point-in-time.
- Stock bars are 5-minute only: intrabar order of entry vs stop is unknown (handled pessimistically, with an optimistic ablation).
- QQQ intraday was not fetched (optional; skipped to save API budget). QQQ variants NOT RUN.
- Quote bars, not trades/quotes: no bid/ask, so spread is modelled through bps slippage.


## Hashes

`data-cache/history/manifest.json`: 415 files, content hash (sha256 of the per-file sha256 map) `4e0bb91dcad4080c2fc93a0c58d79c696eaa16a9ade77c9417cb17f9a8764820`.
