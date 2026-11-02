# Phase 2: Strategy v1 "surge -> watch -> probe -> add" full-market backtest

- Date: 2026-10-10
- Universe: common stocks from the SEC list that are currently listed on Nasdaq / NYSE and have SEC share-count data, 4956 in total. All sectors, **excluding delisted stocks** (per the user's request).
- Prices: Yahoo Finance daily bars, 2018-06 to 2026-10-09. The backtest period starts 2019-06-01.
- Market cap: estimated point-in-time, = that day's actual close (split adjustment reversed) x the most recent share count disclosed at the time (SEC XBRL; cover-page shares are used with a 5-day lag, weighted-average shares with a 45-day lag).
- Reproduce:
  - `python scripts/build_universe.py`: builds the universe and downloads prices, about 25 minutes the first time;
  - `python scripts/backtest.py --config configs/strategy_v1.yaml --tag v1`: runs the backtest (the script was originally named backtest_v1.py and was renamed in Phase 4), about 75 seconds;
  - Parameter variants use `--set key=value`.
- Details:
  - `reports/data/trades_v1_staged.csv`, `trades_v1_immediate.csv`: trade-by-trade results for v1 and the control group;
  - `reports/data/summary_*.json`: the summary for each variant.

## 1. Conclusions

1. **Scaling in is much better than "chasing right after the surge"**. On the same set of surges, buying a full position at the next day's open (control group) gave: 12795 trades, expectancy -0.09R, cumulative -1196R, losing in both time periods. v1 gave: 4675 trades, expectancy +0.05R, cumulative +221R. The watch period and the probe step kept a large share of chase-the-high losses out.
2. **But v1 itself does not yet have a stable edge**, mainly on two pieces of evidence:
   - Nearly all of the return comes from 2020 (+819R). 2021, 2022, 2023, and 2026 were all losing years, and 2024-2026 combined had an expectancy of -0.07R.
   - Returns depend heavily on a few extreme winners: the top 1% of trades by return contributed +1781R, while the total is only +221R. Removing that 1% gives an expectancy of -0.34R; capping each trade at 20R gives -0.14R.
3. **All 11 parameter variants are negative in 2024-26** (see Section 3). All are positive in 2019-23, but that is the period with the largest survivorship bias (see Section 6). This says the problem is not the parameters but that the rules still lack information that separates "real trends" from "pump and dumps".
4. **The "second wave" the user cares about can be caught, but overall the second wave has the lowest expectancy**:
   - After SDGR's 2023-03-01 surge, on day 56 (2023-05-19) it made a new high since the surge, triggering a probe; add on 5-23, exit on 7-25, +34% (6.8R).
   - But across the full market, there were 1427 second-wave trades entered 46-90 days after the surge, with an expectancy of -0.13R. There were 1781 sustained trades that made a new high 10-20 days after the surge, with an expectancy of +0.20R.
5. **The win rate and payoff structure match the "low win rate, high payoff" idea, just not positive enough yet**:
   - Win rate 25%, average win +4.1R, average loss -1.27R;
   - Only 1/3 of probes made it to the add step;
   - Max drawdown -719R, longest losing streak 60 trades. At 1R per trade = 0.25% of account risk, the drawdown is roughly -180%, **so it cannot be traded live yet**.

## 2. v1 rules

All parameters are in `configs/strategy_v1.yaml`. All decisions use post-close data and execute at the next day's open.

| Stage | Rule |
| --- | --- |
| Universe | Actual share price at the time >= $1; market cap $100M-$3B; 20-day average dollar volume >= $2M |
| Surge into watchlist | Single-day gain >= 12% and volume ratio >= 3, or 5-day gain >= 30% |
| Watch | At most 90 trading days. Invalidated if the close falls back to the pre-surge price; watch for at least 10 days |
| Probe (1/3) | After the minimum watch period, all of: close at a new high since the surge, volume ratio >= 1.5, close above the 20-day MA, 20-day MA rising. Buy at the next open |
| Stop | Lowest low of the last 10 days, but a loss of at most 15% (in practice most trades land on 15%) |
| Add (2/3) | Once the closing open profit reaches 1R, add at the next day's open. If the probe hasn't reached 1R within 20 days, exit (validation failed) |
| Exit | Stop hit; or close below the 20-day MA after adding; or held for 120 days |
| Retry | After a failed probe, as long as the surge hasn't been invalidated, wait for the next new high; at most 2 attempts |
| Costs | 0.5% one-way slippage per fill |

Definition of R: 1R is the risk carried by the 1/3 probe position. The control group enters with a full position, and 1R is the risk carried by the full position.

**Funnel**:
- 9175 surges entered the watchlist;
- of those, 4246 fell back to the pre-surge price during the watch period and were invalidated;
- 1002 had no entry signal within 90 days and expired;
- in the end 4675 probes were generated (including second attempts).

## 3. Parameter sensitivity (one change at a time)

| Version | Change | Trades | Win rate | Expectancy (R) | Total R | Max drawdown (R) | Longest losing streak | 2019-23 expectancy | 2024-26 expectancy |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| v1 | Baseline | 4675 | 25% | +0.048 | +221 | -719 | 60 | +0.153 | -0.072 |
| v1_wait45 | Max watch period 90 -> 45 days | 3506 | 23% | +0.081 | +283 | -640 | 62 | +0.232 | -0.088 |
| v1_att1 | Only 1 attempt | 3929 | 24% | +0.041 | +160 | -653 | 58 | +0.151 | -0.084 |
| v1_vol25 | Entry volume ratio 1.5 -> 2.5 | 2583 | 23% | +0.032 | +83 | -385 | 36 | +0.159 | -0.099 |
| v1_be | Raise stop to breakeven after adding | 4813 | 24% | +0.030 | +142 | -710 | 71 | +0.121 | -0.074 |
| v1_obs5 | Min watch 10 -> 5 days | 5511 | 24% | +0.013 | +70 | -920 | 66 | +0.127 | -0.114 |
| v1_onestep | No scaling, full position at once | 4675 | 35% | +0.011 | +50 | -403 | 48 | +0.061 | -0.046 |
| v1_mkt | Market filter (IWM > 200-day MA) | 3802 | 24% | +0.005 | +19 | -577 | 109 | +0.165 | -0.132 |
| v1_obs20 | Min watch 10 -> 20 days | 3616 | 25% | -0.006 | -23 | -584 | 79 | +0.112 | -0.140 |
| v1_stop10 | Max stop 15% -> 10% | 4929 | 18% | -0.030 | -147 | -1163 | 80 | +0.090 | -0.163 |
| v1_ma10 | Trailing MA 20 -> 10 days | 4707 | 27% | -0.141 | -658 | -766 | 54 | -0.202 | -0.072 |
| Control group | Full position the day after the surge | 12795 | 28% | -0.094 | -1196 | -1306 | 70 | -0.075 | -0.116 |

Full position at once (v1_onestep) and scaling in (v1) have exactly the same entry points, but their expectancy in R can't be compared directly because 1R is defined differently in each. The scaled version has smaller per-trade losses and a longer right tail, which is closer to the design intent of "validate with a small position, add on success".

## 4. By year (v1)

| Year | Trades | Win rate | Expectancy (R) | Total R |
| --- | --- | --- | --- | --- |
| 2019 (from June) | 125 | 22% | +0.37 | +46 |
| 2020 | 907 | 30% | +0.90 | +819 |
| 2021 | 577 | 25% | -0.22 | -128 |
| 2022 | 353 | 15% | -0.73 | -257 |
| 2023 | 503 | 25% | -0.20 | -102 |
| 2024 | 649 | 24% | +0.02 | +13 |
| 2025 | 917 | 25% | -0.04 | -37 |
| 2026 (through 10-09) | 644 | 21% | -0.21 | -132 |

## 5. By entry timing and surge type (v1)

| Group | Trades | Expectancy (R) | Total R |
| --- | --- | --- | --- |
| Entered 10-20 days after the surge (sustained) | 1781 | +0.20 | +361 |
| 21-45 days | 1467 | +0.04 | +51 |
| 46-90 days (second wave) | 1427 | -0.13 | -190 |
| 1st attempt | 3731 | +0.08 | +307 |
| 2nd attempt | 944 | -0.09 | -86 |
| Entered on a single-day high-volume long green bar | 1788 | +0.02 | +26 |
| Entered on a 5-day 30% gain | 1793 | +0.18 | +322 |
| Both conditions met | 1094 | -0.12 | -127 |

Among these groups, only "sustained" looks acceptable in both time periods, but once the sample is sliced this finely the noise is large. I did not change the rules based on this, to avoid overfitting.

**Sample tickers under v1:**

| Ticker | Surge date | Probe signal | Entry | Add | Exit | Result |
| --- | --- | --- | --- | --- | --- | --- |
| PRME | 2025-06-06 | 2025-06-23 (day 10) | 2.28 | 2025-06-27 @ 2.70 | 2025-07-30 @ 3.90, broke the 20-day MA | +52%, 10.4R |
| SDGR | 2023-03-01 | 2023-05-19 (day 56, second wave) | 33.26 | 2023-05-23 @ 38.67 | 2023-07-25 @ 49.69, broke the 20-day MA | +34%, 6.8R |
| SDGR | 2022-12-13 | 2023-01-12 | 23.11 | 2023-02-03 @ 26.83 | 2023-02-10 @ 22.98 | -11%, -2.1R |
| SDGR | 2023-11-02 / 2025-01-22 / 2026-09-15 | - | - | no add | stop | about -1.1R to -1.5R each |
| PRME | 2024-05-02 / 2025-09-09 | - | - | no add | stop | -1.06R each |

## 6. Limitations (where results are biased optimistic)

- **Survivorship bias**: the universe only contains companies that still trade today. Tickers that were delisted or acquired after a surge in 2019-2023 are not in it, and the bias grows the earlier the year. 2019-23 being positive and 2024-26 being negative may partly be caused by this bias.
- **Outliers and data quality**: among the top trades are NXH's 2020 trade (+1140%, suspected "ticker reuse", needs verification) and MGRT's 2026 trade (average daily volume only about 30k shares, very hard to actually fill). No float filter or ticker-change check has been done yet.
- Stops are computed as "filled at the stop price on an intraday touch", and at the open on a gap down. Actual slippage on small caps may be larger than 0.5%.
- Statistics are currently trade-by-trade, with no portfolio-level capital constraints (number of concurrent positions, maximum new trades per day).
- Foreign companies (20-F filers) mostly lack SEC share-count data and are not in the universe.

## 7. Suggested next steps

v1 shows that the "watch + probe" structure greatly reduces chase-the-high losses, but it still lacks a filter that separates "real trends" from "pump and dumps". Each of the items below corresponds to the "start research" step in your process, in priority order:

1. **Offering filter**: small caps very commonly file an S-3 / 424B5 / S-1 offering right after a big run-up. Rule: skip if there is an offering filing between the surge and the probe. The data comes from the SEC and has a clear logical basis, so this isn't parameter tuning.
2. **Data cleaning**: daily share volume / float thresholds and ticker-reuse checks; remove untradeable outliers first, then look at the results.
3. **Catalyst type**: use 8-K item numbers to separate partnership / M&A (1.01), earnings (2.02), other events (8.01), and no filing, and see which type of surge has the best follow-through.
4. **Sector co-movement**: when several tickers in the same industry (SIC) surge in the same period, check whether the follow-through is better.
5. **Portfolio-level simulation**: set risk per trade and maximum positions, and compute the real equity curve and drawdown.
