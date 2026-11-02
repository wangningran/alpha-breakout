# Alpha Breakout Strategy Notes

> Goal: US small caps across all sectors. After a sharp run-up, watch first; only if the trend holds, try a small probe, and add after it is validated. Focus on payoff ratio, not win rate.
> This file records the conclusions from Q&A with the user; new sessions pick up from here.

## 1. Confirmed direction

| Item | Conclusion |
| --- | --- |
| Market | US stocks (Hong Kong and China A-shares are out for now) |
| Scope | Small caps across all sectors (expanded from "biotech / AI drug discovery" on 2026-10-10) |
| Main line | **surge → watch → probe → add** (replaced the original "first day of a base breakout" on 2026-10-10) |
| Second wave | Must also catch cases where the stock pulls back after the surge and then rises again some time later |
| Style | Low win rate (expected 20–35%), high payoff ratio; small positions, many attempts |
| Backtest universe | Only stocks still trading today; delisted stocks not needed (confirmed by the user, survivorship bias accepted) |

The user's own words: "When a small cap has a sharp run-up, we catch it and start researching it. Then if, say, it keeps trending up over some period, we can validate with a small position, and add after the validation succeeds."

## 2. Reference samples

| Ticker | Company | Result under v1 |
| --- | --- | --- |
| PRME | Prime Medicine | 2025-06-06 surge → 6-23 probe → 6-27 add → 7-30 exit, +52% (10.4R) |
| SDGR | Schrödinger | 2023-03-01 surge → day 56 (5-19) second-wave probe → 5-23 add → 7-25 exit, +34% (6.8R) |
| 1873.HK | Viva Biotech | Hong Kong stock, logic reference only |

The user has other samples not yet provided; when adding them, please include the period of the run-up.

## 3. Rules (current candidate v4, parameters in `configs/strategy_v4.yaml`)

All decisions use data after the close and are executed at the next day's open.

1. **Universe**: actual share price at the time ≥ $1; market cap $100 million–$3 billion (point-in-time estimate); 20-day average dollar volume ≥ $2 million.
2. **Surge enters the watchlist**: one-day gain ≥ 12% with volume ratio ≥ 3, or 5-day gain ≥ 30%.
3. **Watch**: at most 90 trading days. A close back below the pre-surge price invalidates it; watch for at least 10 days. During this phase, catalysts, offering risk, etc. are researched manually.
4. **Probe 1/3**: after the watch period, buy at the next day's open when all of the following hold:
   - close at a new high since the surge;
   - volume ratio ≥ 1.5;
   - close above the 20-day moving average, and the 20-day moving average is rising.
5. **Stop**: lowest low of the last 10 days, losing at most 15%.
6. **Add 2/3**: add the next day once the unrealized gain on the close reaches 1R; if the probe does not reach 1R within 10 days, exit (validation failed; changed from 20 days to 10 days as of v4).
7. **Exit**: stop hit; or a close below the 20-day moving average after the add; or 120 days held.
8. **Retry**: after a failed probe, as long as the surge is still valid, wait for the next new high; at most 2 attempts.
9. **Data cleaning (v2)**:
   - trading days with price data ≥ 120;
   - 20-day average dollar volume before the surge ≥ $1 million, zero-volume days ≤ 2;
   - 20-day average dollar volume on the probe day ≥ $2 million.
10. **Offering filter (v2)**: if there is a 424B1 / 424B4 / 424B5 / S-1 / F-1 filing from 5 days before the surge day through the surge day, the stock does not enter the watchlist. Handling offerings that appear during the watch period or while holding backtested worse and is off by default.
11. **Catalyst tags (v2)**: classified by 8-K items / 13D / offering filings around the surge, for statistics only. A category can be excluded in `filters.catalyst.exclude`, e.g. earnings.

12. **No probe before earnings (v4)**: the next earnings date is estimated from historical 8-K item 2.02; no probe within 14 calendar days before expected earnings.
13. **Fundamentals check (v4)**: before the probe, check with earnings already disclosed at the time; missing data is blocked:
    - latest quarter revenue YoY ≥ 20%;
    - gross margin up ≥ 2 percentage points vs. the same quarter last year.

    v3 used "revenue YoY ≥ 50% + cash runway ≥ 4 quarters"; see the v3 conclusions below.
13. **Partial take-profit**: when the unrealized gain on the close reaches 3R, sell half at the next day's open and trail the rest with the 50-day moving average.
14. **Portfolio rules**:
    - each probe risks 0.5% of the account, a single stock ≤ 20%, at most 10 at once, at most 3 new trades per day;
    - when slots are short, lower 60-day relative strength goes first;
    - **buy IWM with idle cash** (index + strategy hybrid).

### v4 conclusions (details in `reports/2026-10-10_phase5_validation_hybrid.md`)

- **Walk-forward validation**: filters were chosen using only 2019–2022; 9 of the top 10 in the training period were positive in 2023–2026, so fundamental filters generalize to new data. v4 (revenue ≥ 20% + gross margin improvement) was No. 3 in the training period and held up when rerun by the engine under the rules; No. 1 did not hold up.
- **v4 is the first version that does not depend on extreme values**: 322 trades, expectancy capped at 20R +0.25R; every time split falls between +0.23R and +0.27R; still +0.07R after removing the top 1% of trades.
- **Portfolio**: v4 + idle cash in IWM, 12.5% annualized, -42% drawdown, Sharpe 0.53. Holding IWM is 10.6%, -41%, 0.53. Higher returns, but on a risk-adjusted basis it is on par with the index.
- **Tried but not useful**: market filter (IWM > 200-day moving average), looser watchlist entry threshold (one-day +8%), probing within 5% of the new high, ordering by gross margin.
- **Probe validation period 20 days → 10 days (adopted 2026-10-11, but the benefit is unproven)**: initially only 5 / 10 days were tested, both better than 20 days, which was misread as "shorter is better".
  After also testing 7 / 15 days, capped expectancy was 5 days +0.26R, 7 days +0.18R, 10 days +0.32R, 15 days +0.22R, 20 days +0.25R, jumping around with no monotonic pattern;
  the standard error of per-trade capped expectancy is about ±0.19R, so all these differences are within error. Conclusion: the length of the validation period is not a source of edge; the 10-day "improvement" is very likely luck, and the realistic expectation remains about +0.25R.
  Why 10 days rather than 5: 5 days would kick out early 21 probes that later became winners; trading is more frequent, and with 1% slippage 5 days drops to +0.16R (10 days +0.21R);
  one week of price action is too short for small caps. Why 10 days rather than 20: the difference between them is noise, and 10 days turns capital over faster and gives faster research-journal feedback.
- **No probe within 14 days before expected earnings (adopted 2026-10-11)**: the next earnings date is estimated from historical 8-K item 2.02 release dates.
  In the large sample, entries 0–7 days before earnings were clearly worse in both periods (-0.54R / -0.95R). v4 engine rerun: 7 / 10 / 14 days capped expectancy +0.33 / +0.36 / +0.39R (no filter +0.32R), improving monotonically;
  21 days falls back to +0.30R (only +0.13R in 2019–22), showing that avoiding too early blocks good stocks. 14 days: 2019–22 +0.40R, 2023–26 +0.38R, +0.21R after removing the top 1%,
  total R +127 → +143 (only 17 trades removed, almost all losers), portfolio annualized 17.0% → 18.6%, Sharpe 0.65 → 0.70, +18.5% in 2026.
  14 calendar days ≈ the 10-trading-day probe validation period, so validation finishes before earnings. "Also avoid when overdue and not yet released" was worse and is not adopted.
- **Insider transactions (Form 4) have no predictive power (tested 2026-10-11, not adopted)**: parsed 23,600 Form 4s (from 10 days before the surge to the probe day), separating 10b5-1 plan sales from discretionary sales.
  In the large sample, stocks with no sales at all were actually the worst (-0.10R / -0.26R), large discretionary sales (≥ $1 million) -0.18R / -0.11R, and purchases did not separate consistently either; within v4 the direction of each group flipped between the two periods.
  The data and code are kept (`src/alpha_breakout/insiders.py`, `scripts/fetch_insiders.py`) as reference information for manual research.
- **None of the price features for "avoiding likely losers" held up (tested 2026-10-11)**: too large a surge gain, too deep a drawdown, low-priced stocks, high volatility, extreme volume on the probe day, market filters, etc.
  reduce stops on the large sample without the fundamentals filter, but on v4 they also cut the big winners; volume ratio caps of 3.0 / 3.6 / 4.5 were all worse in engine reruns;
  for the market filter only IWM > 50-day moving average looked good, while 20 / 100 / 200-day were all worse, so it was judged to be chance and not adopted.
- **Tightening the stop did not help either (tested 2026-10-11)**: max stop 15% → 10% / 8% cut capped expectancy from +0.25R to -0.22R / -0.24R and portfolio annualized return from 12.3% to 3.8% / 6.8%;
  a stop at the 5-day low (+0.13R) and moving the stop to cost after the add (+0.13R) were also worse; widening to 20% is roughly the same as 15% (+0.24R).
  Reason: with risk-based position sizing, the closer the stop, the larger the position, so the money lost per stop stays about the same (always 1R), but the number of times normal volatility shakes you out rises sharply (win rate 28% → 17–18%).
- New features: YoY gross margin improvement works; 60-day relative strength > 100% (run too far) is clearly worse; narrowing losses and revenue size show no pattern.

### v3 backtest conclusions (details in `reports/2026-10-10_phase4_research_portfolio.md`)

- **13D/13G is not useful**: grouped by the pre-probe window, neither period shows a consistent positive effect, and "require a 13D" is worse than the baseline.
- **Revenue YoY > 50% is the only feature that works in both periods** (+0.10R / +0.12R after capping at 20R). Companies with cash runway < 4 quarters or no revenue are worse in both periods.
- **ATR trailing is worse**. 3R partial take-profit + 50-day moving average is slightly better.
- **v3 candidate**: 440 trades, expectancy +0.34R, capped +0.14R, positive in both periods, positive every year in 2024–26.
- **But not ready for live trading yet**:
  - after removing the 4 largest trades, expectancy becomes -0.04R;
  - portfolio simulation 10.3% annualized, -47% drawdown, versus holding IWM over the same period at 10.6% annualized, -41% drawdown, so it did not beat it;
  - returns are concentrated in 2020;
  - the parameters were chosen after looking at the data.
- **Next step**: use the daily scan and research journal for 3 months of forward validation, while continuing to look for fundamental features that work in both periods.

### v2 backtest conclusions (details in `reports/2026-10-10_phase3_filters.md`)

- Cleaning, the offering filter and catalyst exclusions are all sensible risk controls, but **2024–26 expectancy is still about -0.13R**, and they bring no edge.
- Only two categories are consistent in direction across both periods, and both are negative: those with an offering at the time of the surge, and those driven by earnings.
- **Root diagnosis**: after entry, the median stock underperforms IWM by 6–8% over 60 days, and fewer than half outperform after 60 days. Pure price signals pick "lottery stocks" (MAX effect), and the positive expectancy relies entirely on a few big winners (every version is negative after capping each trade at 20R).
- Holding longer (50-day moving average trailing) is the only change that turned 2024–26 positive (+0.02R), but it also depends on extreme values and is not stable.
- Conclusion: the next step is to add information beyond price (insider buying, institutional stakes, fundamental inflections) to tell "real trends" from "lottery tickets".

### v1 backtest conclusions (details in `reports/2026-10-10_phase2_v1_backtest.md`)

- Compared with "chasing the day after the surge", staged entry is much better: v1 expectancy +0.05R, direct chasing -0.09R.
- v1 itself does not yet have a stable edge: returns are concentrated in 2020 and a few extreme winners, with 2024–26 expectancy -0.07R. All 11 parameter variants are negative in 2024–26, so it cannot be traded live.
- The second wave can be caught (SDGR 2023), but across the full market, second-wave entries have the lowest expectancy (-0.13R); the sustained type that makes a new high 10–20 days after the surge is the best (+0.20R).
- What's missing are filters for the "research" step: offerings, catalyst type, float / tradability, sector co-movement.

## 4. Version history

- **v0 base-breakout scanner** (`configs/scanner_v0.yaml`, `src/alpha_breakout/scanner.py`): requires 60–120 days of consolidation, low volatility and a breakout on 3x volume. PRME and SDGR triggered 0 times across their entire history; see `reports/2026-10-10_phase1_event_study.md`. Replaced by v1; code kept for reference.

## 5. Research method

1. Full-market event backtest: every surge runs through the state machine, and all results are counted, with no cherry-picking of samples.
   Evaluation also looks at "expectancy with each trade capped at 20R" to avoid being misled by a few extreme values.
2. Control group: chase the same set of surges directly (immediate mode) to test whether watching and probing actually help.
3. Guarding against overfitting: change only one parameter at a time; look at 2019–2023 / 2024–2026 separately, and it only counts if both periods point the same way.
4. Metrics: win rate, average win/loss in R, expectancy, max consecutive losses, max drawdown (R), and how much returns depend on extreme values.

## 6. Data sources

| Purpose | Source | Domain |
| --- | --- | --- |
| Daily prices | Yahoo Finance (yfinance) | `query1.finance.yahoo.com` (allowed) |
| Stock list, share count | SEC `company_tickers_exchange.json`, XBRL frames API | `www.sec.gov`, `data.sec.gov` (allowed) |
| Filings (8-K, S-3, 424B5, 13D) | SEC EDGAR submissions API | Same as above |
| ~~Backup prices~~ | ~~Stooq~~ (unreachable from the cloud environment, not used) | — |

> SEC requires a User-Agent with contact info, set via the `SEC_USER_AGENT` environment variable.

## 7. To-do

- [x] Open network access (Yahoo Finance, SEC EDGAR)
- [x] Data fetch module: yfinance with a local cache, supports full-market bulk download
- [x] Full-market stock universe: 4956 stocks, point-in-time market cap estimate
- [x] v0 base-breakout scanner + PRME / SDGR event review (phase 1, replaced by v1)
- [x] v1 "surge → watch → probe → add" full-market backtest + control group + 11 parameter variants (phase 2)
- [x] Full-market SEC filing index (`scripts/fetch_filings.py`, 3.11 million entries)
- [x] Offering filter (on at the surge; filtering during the watch period / while holding backtested worse, off by default)
- [x] Data cleaning (untradable stocks like MGRT excluded; NXH verified as genuine Overstock prices)
- [x] Catalyst classification (phase 3 report, section 4)
- [x] 13D / 13G institutional stakes (ineffective in both periods, not adopted)
- [x] Fundamental inflection: revenue YoY ≥ 50% + cash runway ≥ 4 quarters (adopted, v3 candidate)
- [x] Exit re-research: ATR worse; 3R partial take-profit + 50-day moving average slightly better (adopted); "expectancy capped at 20R" added to the evaluation metrics
- [x] Portfolio-level simulation (`scripts/portfolio.py`)
- [x] Daily scan + research journal (`scripts/daily_scan.py`, `journal/`, `scripts/evaluate_journal.py`)
- [x] Automated daily scan: a scheduled task runs `daily_scan.py` every trading day at 17:47 US Eastern and commits the report and research journal to main (since 2026-10-11)
- [ ] **Forward validation**: check `reports/daily/<date>.md` every day, fill in "do / skip" in `journal/research_log.csv`, and evaluate with `evaluate_journal.py` after 3–6 months (out-of-sample performance, value added by human judgment)
- [x] More fundamental features: gross margin improvement (works, included in v4); revenue size, narrowing losses (ineffective)
- [x] Signal ordering (v4 has few signals, minimal impact; default is relative strength from low to high)
- [x] Hybrid with IWM (idle cash in IWM, v4 default)
- [x] Walk-forward validation (`scripts/walk_forward.py`)
- [ ] Fundamental scoring instead of hard thresholds (rank and size positions by score)
- [ ] Broaden information: insider buying, changes in analyst coverage, sector co-movement
- [x] Insider buying/selling: parse Form 4 (P / S, separating 10b5-1), no predictive power, not adopted
- [x] No probe before earnings (14 days, adopted)
- [ ] Sector co-movement: multiple surges in the same SIC industry at the same time
- [ ] Add user samples
