# Phase 4: 13D/13G, fundamentals, exits, portfolio simulation, daily scan

- Date: 2026-10-10
- Universe: same as earlier phases, 4956 small caps across all sectors, 2019-06 to 2026-10-09, excluding delisted stocks.
- New data: SEC XBRL quarterly fundamentals (frames API), 766k records, 11505 companies, cached in `data/cache/fundamentals.parquet`.
  - Look-ahead guard: used only after period end + 75 days.
- New config: `configs/strategy_v3.yaml` (defaults to the candidate version chosen in this phase).
- Reproduce:
  - `python scripts/fetch_fundamentals.py`
  - `python scripts/backtest.py --tag v3 --features`
  - `python scripts/portfolio.py --tag v3`
- Evaluation standard:
  - Look at both expectancy in R and "expectancy with each trade capped at 20R";
  - Split into 2019-23 / 2024-26; it only counts if both periods are positive;
  - Finally, compare the portfolio simulation against "just holding IWM".

## 1. Conclusions

1. **13D/13G institutional stakes: not useful**. Grouped by large-holder filings around the probe date:
   - New 13D: negative in both periods;
   - 13D amendment: negative or flat in both periods;
   - New 13G: negative in 2024-26.

   Using "requires a 13D" as a filter does worse than the baseline. The "13D +1.45R" seen in the previous phase was a label on the surge day; once switched to the window before the probe it disappears, which shows it was small-sample noise.
2. **High revenue growth is currently the only feature that works in both periods**:
   - Trades with latest-quarter revenue YoY > 50% have capped expectancy of +0.10R and +0.12R in the two periods, about 300 trades each;
   - Declining revenue, no revenue, and cash runway < 4 quarters are worse in both periods;
   - Whether revenue growth is accelerating doesn't discriminate;
   - Raising the threshold from 20% to 50% improves the effect accordingly (capped expectancy -0.006 -> +0.037), a dose-response effect.
3. **Exits**:
   - An ATR trailing stop is worse than the 20-day MA; it gets shaken out by normal volatility too often;
   - "Sell half at +3R, trail the rest with the 50-day MA" improves both periods slightly, and the effect is more pronounced when combined with the fundamentals filter.
4. **Candidate version v3** (v2 + revenue YoY >= 50% + cash runway >= 4 quarters + 3R partial take-profit):
   - 440 trades, expectancy +0.34R, capped expectancy +0.14R, positive in both periods (capped +0.055R, +0.26R);
   - Positive in each of 2024, 2025, and 2026;
   - Max drawdown -126R, longest losing streak 13 trades (baseline: -700R, 56 trades).
5. **But it's not good enough yet to trade live**:
   - **Still depends on outliers**: the top 4 trades by return (top 1%) contributed 166R, more than the total (149R). Removing them, the average per trade is -0.04R.
   - **The portfolio simulation doesn't beat the index**: $100k account, 0.5% risk per trade, at most 10 concurrent names; the median across 20 random seeds is 10.3% annualized, -47% max drawdown, Sharpe 0.53. Holding IWM over the same period gives 10.6% annualized, -41% max drawdown.
   - **Returns are concentrated in 2020**: the portfolio made +95% in 2020, and only about +2% combined over the other 6 years.
   - **The parameters were chosen after looking at the data** (50% threshold, 4-quarter runway, 3R take-profit), so there is overfitting risk; it needs validation on future data.
6. **The baseline version loses a lot in a portfolio**: v3 without the fundamentals filter and partial take-profit gives -38% over 7 years in the portfolio, with -85% max drawdown. Slot limits mean 75% of signals can't be taken. The direct-chase control group is -58%. The small positive expectancy in the trade-by-trade statistics doesn't exist at all in a real account.
7. **The daily scan and research journal are ready to use**. The 2026-10-09 scan:
   - 2 new watchlist entries, including PRME;
   - 251 names under watch, 22 of which meet the fundamentals criteria;
   - No names ready to probe that day.

## 2. 13D/13G and fundamentals groups (baseline rules, grouped by features on the probe date, unit: expectancy capped at 20R)

| Group | 2019-23 trades | 2019-23 | 2024-26 trades | 2024-26 |
| --- | --- | --- | --- | --- |
| No large-holder filing | 1623 | -0.06 | 1172 | -0.16 |
| New 13D | 97 | -0.16 | 63 | -0.33 |
| 13D amendment | 241 | -0.36 | 324 | -0.01 |
| New 13G | 356 | -0.10 | 490 | -0.35 |
| Revenue YoY < 0 | 752 | -0.03 | 581 | -0.36 |
| Revenue YoY 0-20% | 424 | -0.42 | 435 | +0.04 |
| Revenue YoY 20-50% | 299 | -0.00 | 224 | -0.24 |
| **Revenue YoY > 50%** | 311 | **+0.10** | 264 | **+0.12** |
| No revenue data | 531 | -0.13 | 545 | -0.30 |
| Cash runway < 2 quarters | 271 | -0.13 | 313 | -0.30 |
| Cash runway 2-4 quarters | 214 | -0.12 | 213 | -0.47 |
| Cash runway 4-8 quarters | 298 | +0.36 | 352 | -0.09 |
| Cash runway > 8 quarters | 523 | -0.16 | 445 | -0.07 |
| Profitable companies | 753 | -0.18 | 524 | -0.05 |

Definitions:
- Large-holder filings: filings between 30 days before the surge and the probe date.
- Revenue: YoY for the most recent disclosed quarter.
- Cash runway: (cash + short-term investments) / quarterly loss.
- Starting in 2024 the SEC shortened the 13D/13G filing deadlines and renamed the forms (SC 13D -> SCHEDULE 13D), so filing counts in the two periods are not fully comparable.

## 3. Variant comparison (each changes one item or one group on top of the v3 baseline)

| Version | Trades | Win rate | Expectancy (R) | Capped 20R | 19-23 expectancy | 19-23 capped | 24-26 expectancy | 24-26 capped | Max drawdown (R) | Longest losing streak |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| v3_base (= v2) | 4390 | 25% | +0.035 | -0.141 | +0.184 | -0.101 | -0.134 | -0.186 | -700 | 56 |
| Revenue YoY >= 20% | 1161 | 25% | +0.109 | -0.006 | +0.143 | +0.011 | +0.066 | -0.027 | -213 | 20 |
| Revenue YoY >= 50% | 612 | 23% | +0.207 | +0.037 | +0.237 | +0.034 | +0.172 | +0.040 | -157 | 22 |
| Cash runway >= 4 quarters | 2966 | 26% | +0.105 | -0.088 | +0.246 | -0.084 | -0.062 | -0.093 | -416 | 65 |
| Revenue >= 50% + runway >= 4 quarters | 448 | 24% | +0.233 | +0.063 | +0.218 | +0.018 | +0.254 | +0.126 | -138 | 18 |
| Requires a 13D | 831 | 23% | -0.017 | -0.150 | -0.080 | -0.236 | +0.038 | -0.076 | -146 | 43 |
| ATRx3 trailing | 4611 | 23% | +0.078 | -0.165 | +0.150 | -0.120 | -0.003 | -0.216 | -719 | 100 |
| ATRx4 trailing | 4450 | 22% | +0.093 | -0.233 | +0.251 | -0.155 | -0.086 | -0.320 | -922 | 105 |
| Sell half at 3R + 50-day MA | 4349 | 26% | +0.005 | -0.091 | +0.098 | -0.034 | -0.101 | -0.156 | -700 | 55 |
| Sell half at 3R + ATRx3 | 4604 | 25% | +0.015 | -0.054 | +0.063 | -0.021 | -0.040 | -0.092 | -643 | 42 |
| Revenue >= 50% + 3R partial | 604 | 24% | +0.202 | +0.047 | +0.303 | +0.069 | +0.082 | +0.020 | -154 | 16 |
| **v3 candidate: revenue >= 50% + runway >= 4 quarters + 3R partial** | 440 | 25% | **+0.340** | **+0.139** | +0.351 | +0.055 | +0.324 | +0.260 | -126 | 13 |

**v3 candidate by year**:

| Year | Trades | Win rate | Expectancy (R) | Capped 20R |
| --- | --- | --- | --- | --- |
| 2019 (from June) | 5 | 20% | +0.25 | +0.25 |
| 2020 | 56 | 38% | +3.19 | +1.83 |
| 2021 | 59 | 24% | -0.02 | -0.02 |
| 2022 | 68 | 16% | -1.06 | -1.06 |
| 2023 | 70 | 29% | -0.23 | -0.23 |
| 2024 | 59 | 22% | +0.55 | +0.54 |
| 2025 | 72 | 25% | +0.09 | +0.09 |
| 2026 (through 10-09) | 51 | 24% | +0.39 | +0.18 |

Top trades by return: GRWG 2020 (+51R), MGNX 2020 (+48R), MARA 2020 (+36R), AAOI 2026 (+31R), RKLB 2024 (+21R).

## 4. Portfolio simulation

Rules (`src/alpha_breakout/portfolio.py`):
- Starting capital $100k; each probe carries 0.5% of account risk (= 1R);
- A single name's full-position market value is capped at 20% of the account;
- At most 10 concurrent positions, at most 3 new probes per day;
- When a day has more signals than slots, pick at random; run 20 random seeds;
- Mark to market daily at the close; fills include 0.5% slippage.

| Version | Total return (median) | Annualized | Max drawdown | Sharpe | Signals taken / not taken |
| --- | --- | --- | --- | --- | --- |
| v3_base scaled | -38% | -6.5% | -85% | -0.02 | 1077 / 3313 |
| v3_base direct chase (full-position risk 0.5%) | -58% | -11.1% | -67% | -0.27 | 1678 / 10114 |
| Revenue >= 50% + 3R partial | +82% | +8.8% | -51% | 0.45 | 475 / 129 |
| **v3 candidate** | **+100%** | **+10.3%** | **-47%** | **0.53** | 382 / 58 |
| v3 candidate, 0.25% per trade, at most 20 names | +41% | +5.0% | -33% | 0.44 | 424 / 16 |
| Control: hold IWM | +109% | +10.6% | -41% | - | - |

**v3 candidate portfolio returns by year (median)**:

| 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| -3.5% | +95% | +28% | -28% | +2% | +16% | -9% | +8% |

The portfolio made +28% in 2021, while the trade-by-trade statistics for 2021 are -0.02R. The difference comes from big winners entered in late 2020 and sold only in 2021 (e.g. MARA), which the portfolio marks to market by year and counts in 2021.

**IWM by year over the same period**:

| 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| +14% | +20% | +15% | -21% | +17% | +11% | +13% | +14% |

## 5. Daily scan and research journal

- `python scripts/daily_scan.py`: run after the close, about 15 minutes (add `--no-update` to skip the price update). It does the following in order:
  1. Incrementally update full-market prices;
  2. Run the state machine once to get each ticker's current state;
  3. Refresh SEC filings for companies on the watchlist;
  4. Output `reports/daily/<date>.md`.
- The report has four sections:
  - Ready to probe: with the rule stop, suggested share count, catalyst, 13D/13G, revenue YoY, cash runway, and recent filings;
  - Position management: hypothetical positions under the rules;
  - New watchlist entries;
  - Under watch: flagged by whether fundamentals meet the criteria, with qualifying names listed first.
- Names that are "ready to probe" are automatically appended to `journal/research_log.csv`. You fill in decision (take / pass) and reason.
- `python scripts/evaluate_journal.py`: groups by "take / pass" and computes actual performance over the following 5 / 20 / 60 days, to judge whether manual research adds value.
- See `reports/daily/2026-10-09.md` for the 2026-10-09 scan results.

## 6. Limitations

- Survivorship bias: the universe only contains companies still trading today, so results are biased optimistic, possibly especially so for "survivor" traits like high revenue growth.
- Fundamentals come from XBRL frames:
  - They only cover companies using us-gaap tags; foreign companies (IFRS) are mostly absent;
  - Fourth-quarter single-quarter data is often missing, in which case an earlier quarter is used;
  - The loss figure uses net income to approximate cash burn, including non-cash items.
- Several parameters of the candidate version were chosen after looking at the data, with only 440 trades; it needs validation on future data (which is what the daily scan and research journal are for).
- In the portfolio simulation, signals are picked at random when a day has many. In practice you would pick manually, which is exactly the part the research journal is meant to evaluate.

## 7. Suggested next steps

1. **Use the daily scan for forward validation (most important)**: run it every day starting now and keep the research journal, with no real money or only very small positions. After three months use `evaluate_journal.py` to check two things: whether the candidate rule is still positive out of sample, and whether your manual judgment adds value.
2. **Look for more fundamental features that work in both periods**: high revenue growth works, which shows the "business is really improving" direction is right. Next, look at gross margin improvement, absolute revenue scale (to drop "high growth" off a tiny base), and earnings inflection (narrowing losses).
3. **Relative-strength ranking**: when several signals arrive on the same day, rank by strength relative to IWM and the sector instead of picking at random. In the portfolio simulation 13% of signals were skipped for lack of slots.
4. **Comparison with IWM**: for the strategy to be meaningful it must beat the index on a risk-adjusted basis. Consider a hybrid of "hold IWM normally, and use part of the capital to probe when signals appear", and see whether portfolio drawdown and return can both improve.
5. (Long term) Add insider buying (Form 4), and a data source that includes delisted stocks.
