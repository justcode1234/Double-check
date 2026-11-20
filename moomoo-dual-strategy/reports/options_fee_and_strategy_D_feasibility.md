# Strategy D: fee feasibility of the unchanged +$0.01 exit

**D, unchanged:** buy an option on a sentiment signal; sell when its quoted premium has risen **exactly $0.01**; **no stop-loss**. Nothing is altered here; the arithmetic is tested as stated.

Fee schedule: competition handbook T&C section 8, re-read verbatim 2026-11-19.

- Commission: $0.65/contract (premium > $0.10) or $0.15/contract (≤ $0.10), with a **$1.99 minimum per order**.
- Plus $0.30 platform and $0.015 regulatory per contract, and $0.00279 activity per contract on sells.

Code: `research/options/pricing.py`. Tests: `tests/test_options_ef.py::test_a_*`.

## 1. Gross target vs fees

A $0.01 move on one standard contract (×100) earns **$1.00 gross**.

| Contracts | Round-trip fees, premium > $0.10 | per contract | Net at +$0.01 | Round-trip fees, both legs ≤ $0.10 | per contract | Net at +$0.01 |
|---|---|---|---|---|---|---|
| 1 | **$4.61** | $4.61 | **−$3.61** | $4.61 | $4.61 | −$3.61 |
| 2 | $5.25 | $2.62 | −$3.25 | $5.25 | $2.62 | −$3.25 |
| 4 | $7.73 | $1.93 | −$3.73 | $6.51 | $1.63 | −$2.51 |
| 10 | $19.33 | $1.93 | −$9.33 | $10.31 | $1.03 | −$0.31 |
| **11** | $21.26 | $1.93 | −$10.26 | **$10.94** | $0.99 | **+$0.06** |
| 20 | $38.66 | $1.93 | −$18.66 | $18.66 | $0.93 | +$1.34 |
| 100 | $193.28 | $1.93 | −$93.28 | $93.28 | $0.93 | +$6.72 |

**Break-even premium change** = fees ÷ (100 × contracts):

- 1 contract: **$0.0461**. A one-contract order needs a +4.6¢ move just to break even; the +1¢ target can never be profitable.
- Premium > $0.10, ≥ 4 contracts: **$0.0193** per contract, always above $0.01. **Never profitable.**
- Entry at $0.10 exiting at $0.11 (crosses into the $0.65 tier): $0.0143–0.0178. **Never profitable.**
- Both legs ≤ $0.10 and **≥ 11 contracts**: $0.0099 down to $0.0093 per contract. **The only positive case**, at +$0.0007 to +$0.0067 per share.

## 2. Effect of the quoted spread

D buys at the ask. Which price must rise by $0.01?

| Reading of "quoted premium rises $0.01" | Gross per share when selling at the bid | Can net ever be > 0? |
|---|---|---|
| The **mid/last** rises $0.01 (sell at bid) | 0.01 − spread: **$0.00** at a 1¢ spread, −$0.01 at 2¢, −$0.04 at 5¢ | **No.** Gross ≤ 0 before fees for any spread ≥ 1¢ |
| The **bid** reaches entry ask + $0.01 (most favourable) | +$0.01 | Only in the ≤ $0.10, ≥ 11-contract case above |

In the real quotes (2021–2025, 1–10 DTE): 98.3% of SPY options with ask ≤ $0.09 are quoted **$0.01 wide**. The ATM median spread is $0.02–0.05. Under the favourable reading the bid must rise **two ticks** (e.g. $0.04 → $0.06 on a $0.05 ask, +50%).

## 3. The only feasible region needs near-certainty

With no stop, a missed target typically ends worthless: you lose the premium plus the entry fee and pay no exit fee.

| Contracts | Entry ask | Net per win | Loss if worthless | **Break-even win rate** |
|---|---|---|---|---|
| 11 | $0.02 | +$0.06 | $27.45 | 99.78% |
| 11 | $0.05 | +$0.06 | $60.45 | 99.90% |
| 20 | $0.02 | +$1.34 | $49.30 | 97.35% |
| 20 | $0.05 | +$1.34 | $109.30 | 98.79% |
| 100 | $0.09 | +$6.72 | $946.50 | 99.30% |

Observed at closes (EOD lower bound, *not* a backtest of D): sub-$0.10 calls reached entry ask + $0.01 on **2–7%** of trades (2021–2025), against the **97–99.9%** required. Intraday touches would raise that hit rate, but nothing in the data suggests by a factor of 14 or more.

## 4. Verdict on D's mandatory exit

- **One contract:** fees ($4.61) exceed the $1 gross on every win, so D **always loses**, whatever the signal quality.
- **Any premium above $0.10:** every win loses $0.93 per contract or more.
- **Positive per win only** with sub-$0.10 options, ≥ 11 contracts, an exit at bid ≥ ask + $0.01, and a win rate ≥ 97–99.9% with no stop-loss.
- **Classification: NO-GO for trading.** D's intraday exit evaluation is **BLOCKED** (no minute-level option quotes). D is never approved for unattended trading: with no stop, its loss is up to 100% of premium per position.

These conclusions are pure fee arithmetic on official fees and real quoted spreads. They do not depend on whether sentiment predicts anything, which is tested separately below.

## 5. Separate question: does news sentiment predict direction?

**Signal (free, timestamped):** GDELT 2.0 GKG, using the 15-minute batch stamped **15:30 ET** on each session, articles tagged `ECON_STOCKMARKET`.

- Syndication removed: duplicate URLs, then identical tone vectors.
- Coverage: a median of 68 articles per batch; 1,230 of 1,243 sessions covered.
- Missing: 2023-03-22, and a GDELT outage from 2025-06-12 to 2025-07-01. Missing days are skipped, never filled.
- Signal: z = (tone − trailing 20-day mean) / trailing std, past data only. Positive z means bullish (buy a call); negative means bearish (buy a put).

**Timing:** the news batch (15:30) precedes the 16:00 quote used for entry.

**Outcome measures:**

- SPY's next-day direction.
- A **1-day EOD option round trip**: buy the ATM option at day t's ask, sell at day t+1's bid, one contract, with fees. This is *not* D's exit rule, which EOD data cannot test.

| Segment | Days | Up-day rate | Sentiment direction hit rate | Permutation p | corr(z, next-day return) | 1-day option net, sentiment side | always call | always put | random side (expected) |
|---|---|---|---|---|---|---|---|---|---|
| Dev 2021–22 | 488 | 50.2% | **55.5%** | 0.007 | −0.05 | −$1.23 | −$9.57 | −$10.36 | −$9.97 |
| Val 2023 | 249 | 55.8% | 49.4% | 0.68 | −0.06 | −$33.42 | −$2.31 | −$33.53 | −$17.92 |
| OOS 2024–25-12 | 478 | 58.4% | 52.7% | 0.27 | +0.03 | −$3.56 | −$4.42 | −$30.08 | −$17.25 |

**Reading:**

- The 2021–22 hit rate (55.5%) did not persist: 49.4% in 2023 and 52.7% in 2024–25.
- Both later rates are **below** simply betting "up" (55.8% and 58.4% up days). Correlation with returns is about 0 in every segment.
- Even on the sentiment side, the 1-day option round trip loses money after spread and fees in every segment.

So this free sentiment proxy has no reliable predictive value, and D's exit fails on fees regardless.

**Caveats:**

- GDELT is broad news metadata with coarse theme tagging, not a curated financial-headline feed.
- One 15-minute batch per day.
- A curated, timestamped source (e.g. Alpha Vantage NEWS_SENTIMENT, premium) could differ but needs your registration.

Code: `research/options/gdelt_tone.py`, `research/options/d_sentiment.py`. Output: `reports/options_ef/d_sentiment.json`. File hashes: `data-cache/history_extra/gdelt_files_manifest.json`.
