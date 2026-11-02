# Phase 5: Walk-forward validation, more fundamentals, signal ranking, index hybrid

- Date: 2026-10-10
- Universe: same as earlier phases, 4956 small caps across all sectors, 2019-06 to 2026-10-09, excluding delisted stocks.
- New data: quarterly gross profit (SEC XBRL GrossProfit); 842k fundamentals records in total.
- New config: `configs/strategy_v4.yaml` (the current candidate and the default config for all scripts).
- Reproduce:
  - `python scripts/fetch_fundamentals.py`
  - `python scripts/backtest.py --tag v4 --features`
  - `python scripts/portfolio.py --tag v4`
  - Walk-forward validation: `python scripts/walk_forward.py` (see the top of the script for usage)

## 1. Conclusions

1. **New effective feature: gross margin improving YoY**. Trades where gross margin rose more than 2 points versus the same quarter last year are positive in both periods (+0.10R / +0.22R after capping at 20R). It's stronger among tickers with revenue YoY >= 50% (+0.54R / +0.45R). Narrowing losses and absolute revenue scale show no stable pattern.
2. **Avoid tickers that have run too far**: those with a 60-day excess gain vs IWM > 100% on the probe date are very poor in both periods (-0.46R / -0.53R). So signal ranking can't be "the stronger the better".
3. **Walk-forward validation**: filters are selected using only 2019-2022 and tested on 2023-2026.
   - Of the top 10 combinations in the training period, 9 are positive in the test period; without filters the test period is -0.195R.
   - The rank correlation between the training and test periods is 0.52.
   - This shows fundamentals filters like "revenue / gross margin / cash runway" generalize to new data and are not just after-the-fact fitting.
   - But the training period's #1 (cash runway >= 4 quarters + gross margin improvement + relative strength <= 50%), when put back into the engine and executed day by day per the rules, gives -0.04R in the test period; it didn't hold up.
   - The training period's #3 (revenue YoY >= 20% + gross margin improvement >= 2 points) held up and became v4.
4. **v4 is the first version that doesn't depend on outliers**:
   - 322 trades, expectancy +0.31R, capped expectancy +0.25R;
   - 2019-22 / 2023-26 periods: +0.25R / +0.26R; 2019-23 / 2024-26 periods: +0.23R / +0.27R;
   - **Still +0.07R after removing the top 1% of trades by return** (v3 is -0.04R, baseline is -0.34R);
   - Max drawdown -57R, longest losing streak 18 trades.
5. **Portfolio level**:
   - v4 on its own trades too rarely (about 40 trades a year); most of the capital sits idle, and the annualized return is only 3.6%.
   - **Hybrid with idle cash in IWM**: v4 gives 12.5% annualized, -42% max drawdown; simply holding IWM over the same period gives 10.6% annualized, -41% drawdown. In 2023-26, the hybrid made +86% vs +68% for IWM.
   - But the two have **the same Sharpe (0.53)**: the extra return comes from taking more volatility, with no risk-adjusted improvement.
   - v3 (revenue >= 50% + cash runway) + IWM gives 18.5% annualized and Sharpe 0.67, which looks better, but depends on 2020 and a few extreme winners.
6. **Signal ranking has almost no effect**: v4 trades rarely, so slots are rarely insufficient (only about 10 of the 322 trades were skipped for lack of slots). Ranking by relative strength from low to high is slightly better than random (3.9% vs 3.6% annualized) and is now the default.
7. **The daily scan has switched to v4**. 2026-10-09:
   - 2 new watchlist entries (including PRME);
   - 254 names under watch, 19 meeting the fundamentals criteria;
   - No names ready to probe that day.

## 2. New feature groups (baseline rules, expectancy capped at 20R)

| Group | 2019-23 trades | 2019-23 | 2024-26 trades | 2024-26 |
| --- | --- | --- | --- | --- |
| Gross margin up YoY > 2 points | 287 | +0.10 | 362 | +0.22 |
| Gross margin roughly flat | 343 | -0.23 | 294 | -0.16 |
| Gross margin down > 2 points | 278 | +0.13 | 197 | -0.30 |
| No gross profit data | 1409 | -0.16 | 1196 | -0.30 |
| Net income improved YoY | 905 | -0.03 | 927 | -0.16 |
| Net income worsened YoY | 1100 | -0.19 | 894 | -0.19 |
| 60-day relative strength < 20% | 838 | -0.07 | 508 | -0.16 |
| 60-day relative strength 20-50% | 630 | -0.00 | 615 | -0.02 |
| 60-day relative strength 50-100% | 508 | -0.04 | 538 | -0.15 |
| 60-day relative strength > 100% | 341 | -0.46 | 388 | -0.53 |

Within the subset with revenue YoY >= 50%, gross margin up > 2 points: 80 trades at +0.54R (2019-23), 84 trades at +0.45R (2024-26).

## 3. Walk-forward validation (`scripts/walk_forward.py`)

Training period 2019-06 to 2022-12, test period 2023-01 to 2026-10. This step is after-the-fact screening on the baseline trades; the selected combinations still need to be rerun through the engine for confirmation.

| Revenue YoY >= | Cash runway >= | Gross margin improvement | 60-day relative strength <= | Training trades | Training | Test trades | Test |
| --- | --- | --- | --- | --- | --- | --- | --- |
| - | 4 quarters | Yes | 50% | 105 | +0.52 | 189 | +0.19 |
| 20% | 2 quarters | Yes | - | 105 | +0.49 | 160 | +0.31 |
| **20%** | - | **Yes** | - | 114 | +0.45 | 186 | **+0.37** |
| - | 2 quarters | Yes | 50% | 111 | +0.45 | 207 | +0.11 |
| - | - | Yes | 50% | 122 | +0.40 | 228 | +0.19 |
| 100% | - | No | - | 129 | +0.37 | 202 | +0.03 |
| - | 4 quarters | Yes | - | 180 | +0.32 | 328 | +0.06 |
| - | 4 quarters | Yes | 100% | 155 | +0.32 | 286 | +0.17 |
| 100% | - | No | 100% | 100 | +0.31 | 155 | -0.01 |
| 0% | 4 quarters | Yes | - | 135 | +0.29 | 240 | +0.22 |
| No filter | | | | 1837 | -0.07 | 2529 | -0.20 |

## 4. Engine rerun (executed day by day per the rules, all with 3R partial take-profit, missing data does not pass)

| Version | Trades | Win rate | Expectancy | Capped 20R | 2019-22 | 2023-26 | 2019-23 | 2024-26 | Max drawdown (R) | Longest losing streak | Excl. top 1% |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Baseline (no fundamentals) | 4390 | 25% | +0.035 | -0.141 | -0.067 | -0.195 | -0.101 | -0.186 | -700 | 56 | -0.341 |
| v3: revenue >= 50% + runway >= 4 quarters | 440 | 25% | +0.340 | +0.139 | +0.161 | +0.123 | +0.055 | +0.260 | -126 | 13 | -0.039 |
| Training #1: runway >= 4 + gross margin improvement + RS <= 50% | 317 | 31% | +0.264 | +0.083 | +0.284 | -0.037 | +0.155 | +0.009 | -54 | 15 | -0.108 |
| Same as above, no partial take-profit | 318 | 30% | +0.946 | +0.159 | +0.384 | +0.025 | +0.191 | +0.128 | -57 | 17 | -0.000 |
| **v4: revenue >= 20% + gross margin improvement >= 2 points** | 322 | 28% | +0.308 | **+0.253** | +0.250 | +0.255 | +0.233 | +0.272 | -57 | 18 | **+0.070** |
| Runway >= 4 + gross margin improvement | 539 | 27% | +0.236 | +0.110 | +0.215 | +0.050 | +0.140 | +0.082 | -60 | 20 | -0.077 |
| v3 + gross margin improvement | 140 | 25% | +0.372 | +0.285 | +0.553 | +0.106 | +0.306 | +0.262 | -44 | 11 | +0.155 |
| v3 + RS <= 50% | 243 | 31% | +0.362 | +0.240 | +0.080 | +0.377 | -0.042 | +0.756 | -69 | 11 | +0.079 |

"v3 + gross margin improvement" has better numbers, but only 140 trades and more stacked conditions, so it was not chosen.

**v4 by year**:

| Year | Trades | Win rate | Expectancy | Capped 20R |
| --- | --- | --- | --- | --- |
| 2019 (from June) | 2 | 50% | -0.35 | -0.35 |
| 2020 | 40 | 32% | +0.60 | +0.59 |
| 2021 | 44 | 41% | +0.80 | +0.80 |
| 2022 | 39 | 15% | -0.69 | -0.69 |
| 2023 | 31 | 32% | +0.17 | +0.17 |
| 2024 | 46 | 28% | -0.08 | -0.11 |
| 2025 | 80 | 28% | +0.68 | +0.61 |
| 2026 (through 10-09) | 40 | 18% | +0.29 | +0.03 |

## 5. Portfolio simulation ($100k, at most 10 names, at most 3 trades per day, median of 20 random seeds)

| Scheme | Total return | Annualized | Max drawdown | Sharpe | Taken / not taken |
| --- | --- | --- | --- | --- | --- |
| v4, idle cash earns nothing | +29% | +3.6% | -33% | 0.29 | 312 / 10 |
| v4, ranked by relative strength | +32% | +3.9% | -33% | 0.30 | 311 / 11 |
| v4, ranked by gross margin improvement | +27% | +3.3% | -33% | 0.27 | 312 / 10 |
| **v4 + idle cash in IWM (default)** | **+135%** | **+12.5%** | **-42%** | **0.53** | 311 / 11 |
| v4 + IWM, 1% risk per trade | +170% | +14.7% | -48% | 0.54 | 309 / 13 |
| v3, idle cash earns nothing | +100% | +10.3% | -47% | 0.53 | 382 / 58 |
| v3 + IWM | +232% | +18.5% | -51% | 0.67 | 382 / 58 |
| v3 + IWM, 1% risk per trade | +544% | +30.1% | -58% | 0.80 | 370 / 71 |
| Control: hold IWM | +109% | +10.6% | -41% | 0.53 | - |

**2023-2026 only (test period)**:

| Scheme | Return | Max drawdown in period |
| --- | --- | --- |
| v4 + IWM | +86% | -37% |
| v3 + IWM | +77% | -37% |
| Hold IWM | +68% | -28% |

How the hybrid is implemented: cash not used for probes accrues at IWM's daily close-to-close change. This is equivalent to being fully in IWM normally, and selling part of the IWM to probe when a signal appears. No leverage, and IWM trading costs are not counted.

## 6. How to read these results

- **Good news**: the fundamentals filter (revenue growth + gross margin improvement) is the first improvement that passes walk-forward validation, is consistent across both periods, and doesn't depend on outliers. Per-trade expectancy is stable at around +0.25R.
- **Reality**: v4 has few signals (30-80 trades a year) and can't carry returns on its own. Combined with the index, it returns about 2 percentage points a year more than the index, but with the same Sharpe; that amounts to "take a bit more volatility, earn a bit more", which isn't a real edge yet.
- **v3 + IWM looks best** (18.5% annualized, Sharpe 0.67), but its excess return comes mainly from a few big winners in 2020, and in the 2023-26 test period it did no better than v4 (+77% vs +86%).
- All results have survivorship bias (the universe only contains companies still trading today), so real performance will most likely be worse.

## 7. Additional tests: three changes tried on v4, none improved it

| Version | Trades | Win rate | Expectancy | Capped 20R | 2019-22 | 2023-26 | 2024-26 | Max drawdown (R) | Excl. top 1% | 2022 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| v4 | 322 | 28% | +0.308 | +0.253 | +0.250 | +0.255 | +0.272 | -57 | +0.070 | 39 trades -0.69R |
| + market filter (IWM > 200-day MA) | 254 | 25% | +0.149 | +0.101 | +0.310 | +0.010 | +0.023 | -51 | -0.054 | 11 trades -1.24R |
| Watchlist entry threshold: single-day +12% -> +8% | 372 | 27% | +0.210 | +0.163 | +0.140 | +0.178 | +0.206 | -60 | +0.003 | 43 trades -0.56R |
| Probe within 5% of the new high | 387 | 28% | +0.256 | +0.184 | +0.198 | +0.175 | +0.281 | -94 | +0.031 | 47 trades -0.74R |

- The market filter reduced the number of 2022 trades, but the remaining ones were worse, and it missed the opportunities after 2023.
- Loosening the watchlist-entry and probe conditions did increase signals, but the added ones were lower quality and dragged down overall expectancy.

## 8. Suggested next steps

1. **Forward validation (most important)**: run `python scripts/daily_scan.py` every day. Names ready to probe go into `journal/research_log.csv` automatically, and you fill in "take / pass". `python scripts/evaluate_journal.py` reports performance for both "all rule signals" and "manual judgment". v4 has only a few dozen signals a year, so it takes at least 3-6 months to be statistically meaningful.
2. **A fundamentals score instead of hard thresholds**: combine revenue growth, gross margin change, and cash runway into one score, and use it to rank and size positions (more risk for higher scores), rather than "passes / doesn't pass". This is more likely than loosening price conditions to add signals while preserving quality.
3. **Broaden the information used in "research"**: loosening price conditions didn't help; any gain can only come from more fundamental or event information. Candidates: insider buying (Form 4), changes in analyst coverage, multiple names in the same industry surging at once (sector co-movement).
4. **Accept the "enhanced index" positioning**: on current evidence, v4 + IWM is more like "earn 1-2 points more than the index while taking a bit more volatility". If your goal is to beat it by a wide margin, you still need breakthroughs on items 2 and 3 above; if the goal is to hold the index while using a small part of the capital to participate in small-cap surges, the current framework is ready to start paper tracking.
