# Options data access and provenance (checked 2026-11-19)

Status key: **FREE** (used, no account) · **FREE-CREDIT** (needs your sign-up) · **PAID** · **INACCESSIBLE** (not obtainable as needed). Nothing was bought, registered or granted.

## Access status

| Source | Status | What it provides | Dates actually available to us | Usable for |
|---|---|---|---|---|
| [Free EOD mirror](https://github.com/anahatsingh-ui/options-dataset-hist) (preservation copy of Philipp Dubach's MIT dataset) | **FREE**, used | One snapshot per day "captured at market close": bid/ask, sizes, last, mark, volume, OI, BS IV and Greeks. Schema matches Alpha Vantage's historical-options output; upstream vendor unnamed | **2021-01-04 → 2025-12-12** (local copy; mirror advertises 2008–2025) | E and F (EOD). **Not** D's intraday +1¢ exit |
| Moomoo CLI (your existing quote login) | **FREE** with that account | SPY 1-min RTH bars, daily bars, VIX daily, SPY IV/HV (last 260 days only). **No expired option contracts** (`INVALID_SYMBOL`), and news is latest-page only | SPY 2020-11 → 2026-11; VIX 2019 → 2026-11 | Realised-vol forecasts (F), spot, expiry settlement |
| [GDELT 2.0 GKG](https://gdeltproject.org/data.html) | **FREE**, used (no registration; cite GDELT) | 15-minute global news metadata: URL, source, themes, tone. Not curated financial headlines | 2015-02-19 → now; we use the 15:30 ET batch on each session 2021-01-04 → 2025-12-12 | D's sentiment-quality test (daily resolution only) |
| [SEC EDGAR APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) | FREE, **not used** | Filings; acceptance time is in the filing header. Requests must carry a contact email in the User-Agent | — | Not relevant to SPY (no issuer 8-Ks). Using it would need a contact email from you |
| [Databento OPRA](https://databento.com/pricing) | **FREE-CREDIT** | $125 for new users, expires after 6 months; metered $/GB on uncompressed binary. Cost of a given request comes from `metadata.get_cost`, which needs an API key | Not checked: needs your account | Intraday SPY option quotes for D, if a narrow sample fits the credit (**unverified**) |
| [ThetaData](https://www.thetadata.net/docs/Articles/Getting-Started/Subscriptions.html) | **PAID** for intraday | Free = 1 year of EOD only (from 2023-06-01). 1-min quotes from 2020-01-01 on Value ($40/mo); tick from 2016 on Standard ($80/mo). Account and Java Theta Terminal required | — | Intraday D / E / F |
| [Cboe DataShop Option Quote Intervals](https://datashop.cboe.com/option-quote-intervals) | **PAID** (free sample) | 1-min to N-min NBBO intervals, 2012 → present; SPY 1-min ≈ 20 GB/yr compressed | Free sample inspected: **AAPL and ^SPX only, no SPY**; hourly snapshots for 2023-09-21 plus one 1-minute SPX snapshot (2022-02-02 09:39) | Format check only. Cannot test D |
| [OptionsDX](https://www.optionsdx.com/) | FREE EOD variant (checkout flow) | SPY EOD/intraday CSVs | SPY years 2010–2023 only | Nothing beyond the mirror |
| [Alpha Vantage NEWS_SENTIMENT](https://www.alphavantage.co/documentation/) | **FREE-CREDIT / PAID** | Historical `time_from`/`time_to` news with sentiment. Marked "premium"; free key needs email registration; 25 requests/day | Depth not stated | A curated alternative to GDELT, with your approval |

## Provenance of the dataset actually used (EOD mirror)

| Item | Finding |
|---|---|
| License | MIT (© 2025 Philipp D. Dubach); the mirror invites takedown requests |
| Integrity | Pinned commit `37f6c45`; all 9 downloaded files match the repo's git-blob SHA-1; sha256 in `data-cache/options_history_raw/dubach_mirror/manifest.json` |
| Quote timestamp | `date` only, no time-of-day; described as "captured at market close". Treated as a 16:00 ET snapshot. SPY options trade to 16:15, so a 16:00/16:15 ambiguity remains |
| Contract identifiers | OCC-style `contract_id` (e.g. `SPY240102C00402000`) + expiration/strike/type |
| Frequency | End of day, one row per contract per session |
| Bid/ask validity, 2021–2025 | 0 NaN · 0 negative · 0 crossed · 27–39 locked rows per year · zero-bid 4.7–8.8% (far OTM). ATM 5–15 DTE: median spread $0.02–0.05 (0.6–1.0% of mid), displayed size ~70–130 |
| Greeks / IV origin | Supplied by the dataset (BS, Newton–Raphson per its methodology). Our recomputation from mid: median abs diff 0.92 vol points, correlation 0.97; delta diff 0.013. ATM IV vs VIX: correlation 0.965 |
| Consistency | Underlying close identical to Moomoo SPY close on all 1,243 days. Put–call parity residual ≤ half-spread on 94% of expiry snapshots. Implied carry r−q by year −0.2 / +0.4 / +3.7 / +4.2 / +3.7%, consistent with T-bills minus dividend yield |
| Corporate actions | SPY had no splits in 2021–2025; quarterly ex-dividends (prices raw). Dividends enter only the early-assignment audit |
| Missing data / defects | Ends 2025-12-12 (no 2026). One junk row-date on 2024-01-15 (holiday, 2 rows; dropped). **`in_the_money` is wrong** (agrees 50%), so moneyness is recomputed from strike vs Moomoo close. `mark` ≠ mid on <1% of rows; bid/ask are used |
| Available at decision time? | Yes. A decision uses day d's snapshot and fills at day d+1's snapshot, so no fill uses information from before the decision |

## Do news and quotes align intraday?

**No.** GDELT is 15-minute, but the only free option quotes are once a day. They align only in one direction: news up to 15:30 ET is available before the 16:00 snapshot that day. Any test of D's intraday +1¢ exit needs minute-level option quotes (Databento, ThetaData or Cboe, all needing your account or money), so **D intraday evaluation is BLOCKED**.

## Hashes and commands

- `data-cache/options_history_raw/dubach_mirror/manifest.json`
- `data-cache/history_extra/manifest.json` (VIX, SPY IV)
- `data-cache/history_extra/gdelt_files_manifest.json`: sha256 of every GDELT batch used
- Validation: `PYTHONUTF8=1 .venv/Scripts/python.exe -m dual_strategy.research.options.validate_chains`, output in `reports/options_data_validation.json`
