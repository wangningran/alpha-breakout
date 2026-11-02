# Phase 3: Data cleaning, offering filter, catalyst classification

- Date: 2026-10-10
- Basis: v1 rules and universe unchanged (4956 small caps across all sectors, 2019-06 to 2026-10-09, excluding delisted stocks).
- New data: SEC full-market filings index, 3.11 million records across 4956 companies, using only the date, form type, and 8-K item numbers.
  - Cached in `data/cache/filings.parquet`;
  - Fetch script `scripts/fetch_filings.py`, about 15 minutes the first time.
- New config: `configs/strategy_v2.yaml` (v1 plus two sections, clean / filters).
- Reproduce: `python scripts/backtest.py --config configs/strategy_v2.yaml --tag v2` (the script was originally named backtest_v1.py); other variants use `--set`.
- Evaluation standard: every change is checked in both 2019-23 and 2024-26. A change that only improves one period doesn't count as effective.

## 1. Conclusions

1. **All three filters are sensible risk controls, but none of them adds an edge**. In 2024-26, every version has an expectancy around -0.13R, the same as v1.
2. **Results got slightly worse after cleaning** (+0.048R -> +0.027R). This shows part of v1's return came from tickers that couldn't actually be bought, such as MGRT: before the surge it traded only a few thousand shares a day. That return should never have counted.
3. **The offering filter needs to be looked at in parts**:
   - Surges with an offering filing right around the surge: after cleaning and without the offering filter, these trades were -0.50R and -0.55R in the two periods, consistently losing money. Filtering them out is right.
   - Also filtering out "an offering during the watch period": the results actually got worse (+0.035R -> +0.015R), so it's off by default. A possible reason is that when a company issues shares into price strength, the stock doesn't necessarily turn bad afterwards.
   - Selling when an offering appears while holding: also worse (-0.033R).
4. In **catalyst classification**, only two categories point the same direction in both periods, and both are negative:
   - Offering-type surges: as noted above, consistently losing money; already filtered.
   - Earnings-driven surges: -0.26R and -0.10R in the two periods, sample of 1100+ trades. Excluding them improves the full period (+0.035R -> +0.078R), but 2024-26 is still -0.137R, so they are not excluded by default and are kept as an option.
   - 13D large-holder stake: +1.45R in 2024-26 (74 trades), but -0.34R in 2019-23 (37 trades); small sample and inconsistent direction, so no conclusion yet.
5. **The most important diagnosis: the problem is in the tickers selected, not in position management**. I took the 4443 probe signals from v2_clean and looked at how the stock itself moved after entry:
   - The median underperforms IWM by about 3% after 20 days, and by 6-8% after 60 days;
   - Share outperforming IWM after 60 days: 43% in 2019-23, 36% in 2024-26;
   - The mean is close to 0, propped up by a few explosive movers.

   This matches the "lottery stock effect" in the academic literature (MAX effect): small caps with a big single-day jump tend to underperform afterwards as a group. A purely price-based rule mostly selects "lottery tickets".
6. **Holding longer captures more of the right tail, but it's not stable**. After changing the trailing MA from 20 to 50 days:
   - Full period +0.18R, 2024-26 +0.02R, the only variant that turns positive;
   - But after removing the top 1% of trades by return, the average per trade is -0.39R; 2025 is +0.54R and 2026 is -0.68R, so year-to-year swings are large;
   - Max drawdown widens to -957R.

## 2. New rules (`configs/strategy_v2.yaml`)

| Module | Rule | Default |
| --- | --- | --- |
| Data cleaning | Trading days with price data >= 120; 20-day average dollar volume before the surge >= $1M; zero-volume days in the 20 days before the surge <= 2; 20-day average dollar volume on the probe day >= $2M | On |
| Offering filter | 424B1 / 424B4 / 424B5 / S-1 / F-1 (including /A) from 5 days before the surge through the surge day -> not added to the watchlist | On |
| Offering filter: watch period | Any of the above filings during the watch period -> surge invalidated | Off (worse in backtest) |
| Offering filter: shelf | S-3 / F-3 also count | Off |
| Offering filter: holding | Offering filing while holding -> sell at the next day's open | Off (worse in backtest) |
| Catalyst label | Filings from 3 days before to 1 day after the surge, classified by priority: offering > 13D > 8-K item (1.01 agreement / 2.01 acquisition / 2.02 earnings / 8.01 other events / 7.01 Reg FD) > other 8-K > no filing | Label only |
| Catalyst exclusion | `filters.catalyst.exclude`, e.g. `[earnings]` | Empty |

NXH's +1140% trade in 2020 has been verified and is a real move: NXH is Overstock (later renamed Beyond, Bed Bath & Beyond), not a data error caused by ticker reuse.

## 3. Comparison (one change at a time; R uses the probe risk as 1R)

| Version | Change | Trades | Win rate | Expectancy (R) | Total R | Max drawdown (R) | Longest losing streak | 2019-23 expectancy | 2024-26 expectancy |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| v1 | Baseline | 4675 | 25% | +0.048 | +221 | -719 | 60 | +0.153 | -0.072 |
| v2_clean | + data cleaning | 4443 | 25% | +0.027 | +119 | -718 | 55 | +0.168 | -0.132 |
| **v2** | + offering filter at surge (default) | 4390 | 25% | +0.035 | +151 | -696 | 56 | +0.183 | -0.133 |
| v2 + watch-period offering filter | | 4124 | 25% | +0.015 | +62 | -643 | 52 | +0.147 | -0.133 |
| v2 + watch-period filter + shelf counts as offering | | 4000 | 25% | +0.034 | +134 | -623 | 52 | +0.147 | -0.094 |
| v2 + watch-period filter + sell on offering while holding | | 4104 | 26% | -0.033 | -136 | -657 | 56 | +0.066 | -0.145 |
| v2 + watch-period filter + 30-day offering lookback | | 3958 | 25% | +0.012 | +49 | -584 | 51 | +0.121 | -0.110 |
| v2 + exclude earnings surges | | 3767 | 24% | +0.078 | +293 | -587 | 54 | +0.265 | -0.137 |
| v2_clean + 50-day trailing | Looser exit | 4345 | 23% | +0.177 | +764 | -957 | 79 | +0.316 | +0.019 |
| v2_clean + add only at 2R | Later add | 4623 | 31% | +0.026 | +120 | -608 | 63 | +0.137 | -0.098 |
| Control: v2 chase directly | | 11792 | 29% | -0.068 | -802 | -962 | 67 | -0.049 | -0.091 |

Note: the "watch-period filter" rows were run while the watch-period offering filter was on by default; it was only switched to off by default later. Details are in `summary_v2_shelf.json`, `summary_v2_exitoff.json`, `summary_v2_lb30.json`. The JSON for "v2 + watch-period offering filter" itself was overwritten by the final `summary_v2.json`; its numbers come from the run output at the time.

**Robustness check** (each trade capped at 20R, to see whether results depend only on outliers):

| Version | Full period | Excluding 2020 | 2024-26 |
| --- | --- | --- | --- |
| v1 | -0.14R | -0.16R | -0.16R |
| v2_clean | -0.15R | -0.18R | -0.18R |
| v2 + exclude earnings + offering filter at surge | -0.12R | -0.16R | -0.20R |
| v2_clean + 50-day trailing | -0.20R | -0.18R | -0.28R |

After capping, every version is negative. In other words, all of the current positive expectancy comes from a very small number of big winners.

## 4. Catalyst classification (v2 default config)

| Category | 2019-23 trades | 2019-23 expectancy | 2024-26 trades | 2024-26 expectancy |
| --- | --- | --- | --- | --- |
| No filing | 1364 | +0.28R | 1076 | -0.25R |
| Earnings (2.02) | 528 | -0.26R | 639 | -0.10R |
| Material agreement (1.01) | 103 | +0.73R | 81 | -0.61R |
| 13D large-holder stake | 37 | -0.34R | 74 | +1.45R |
| Other events (8.01) | 108 | -0.05R | 74 | +0.26R |
| Reg FD (7.01) | 72 | +1.27R | 54 | -0.06R |
| Offering / shelf (not filtered: filed the day after the surge, or S-3 / F-3) | 32 | +0.04R | 30 | -0.64R |

Apart from earnings and offerings, every category flips direction between the two periods, which shows this layer of information (form types only, without reading the text) doesn't discriminate well enough.

## 5. Post-entry price performance (v2_clean's 4443 probes, measured from the entry open, excluding position management)

| Period | Metric | 5 days | 20 days | 60 days | 20 days vs IWM | 60 days vs IWM |
| --- | --- | --- | --- | --- | --- | --- |
| 2019-23 | Mean | -0.3% | +1.4% | +6.9% | -0.1% | +2.9% |
| 2019-23 | Median | -1.2% | -2.0% | -2.8% | -3.7% | -6.0% |
| 2024-26 | Mean | +0.3% | +0.5% | +2.3% | -0.4% | -0.9% |
| 2024-26 | Median | -1.1% | -2.3% | -6.3% | -3.1% | -8.4% |

In 2020 the 60-day mean was +29%; most other years were negative.

## 6. Suggestions for the next iteration

Price signals pick "lottery stocks", so the focus of the next step is **adding information beyond price** to pick out the few tickers with a real fundamental change. This also corresponds to the "start research" step in your process. In priority order:

1. **Insider buying (Form 4, transaction code P)**: executives buying with their own money on the open market during the watch period after a surge is the classic "insider endorsement" signal. The SEC has the data; it needs Form 4 XML parsing, only for filings by surging companies during their watch periods, so the volume is small.
2. **13D / 13G institutional stakes**: 74 trades at +1.45R in 2024-26, but the earlier sample is negative, so it needs to be looked at together with item 1. Your PRME sample also includes a "big pharma takes a stake" case.
3. **Fundamental inflection**: pull revenue growth, gross margin, and cash runway from SEC XBRL, to separate "the business is really improving" from "pure hype". Two conditions to look at: accelerating revenue YoY, and cash lasting 12+ months.
4. **Portfolio-level simulation**: set risk per trade (e.g. 0.25% of the account), maximum concurrent positions, and maximum new trades per day, and compute the real equity curve. Only then can we judge whether "relying on a few big winners" is capturable in reality (whether you still have a position and capital when the big winner shows up).
5. **Revisit exits**: the right tail matters (50-day trailing turned 2024-26 positive), but it's not stable yet. Try ATR trailing and partial take-profit (sell half at +3R, trail the rest loosely). Change the evaluation metric to "expectancy after capping at 20R" to avoid being misled by outliers.
6. **Daily scan + research journal**: build a script that runs after each close and outputs the day's new watchlist entries, names under watch, and names ready to probe, plus a filings summary. After your manual research you log "take / pass + reason". After a few months we can evaluate whether manual research actually adds value. The quant rules find names and manage risk; judgment is left to the human.
7. (Long term) **Delisted-stock data**: if you plan to invest seriously, consider a paid data source (including delisted stocks) to eliminate survivorship bias.

I suggest doing 1 + 2 + 4 first: low cost, clear logic, and they answer "is there information that separates lottery stocks from real trends".
