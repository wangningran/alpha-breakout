# Phase 1: PRME / SDGR event review + v0 scanner check

- Date: 2026-10-10
- Data: Yahoo Finance daily bars (yfinance), through the 2026-10-09 close; PRME from its 2022-10-20 listing, SDGR from its 2020-02-06 listing; benchmark XBI.
- Filings: SEC EDGAR submissions API (only form types and 8-K item numbers were checked; the filing text was not read).
- Reproduce: `PYTHONPATH=src python scripts/event_study.py --tag v0` (for v0b add `--config configs/scanner_v0b.yaml --tag v0b`); details are in `reports/data/`.

## 1. Conclusions

1. **The v0 rule never fired once over the full history of either ticker** (PRME 996 trading days, SDGR 1599 trading days, 0 signals).
2. The main blocker is "20-day volatility is low on the day before the breakout". Across the full history, only 6 days satisfied "breakout + 3x volume + close in the top 1/3" at the same time, and on the day before each of those 6 days the volatility percentile was between 0.54 and 1.00, because the stock had already started moving in the days before the high-volume breakout.
3. Most "ignition days" are not base breakouts but reversals off a downtrend low: of the 10 valid events, only 1 satisfied the breakout condition on day 0 (SDGR 2020-11-23), only 5 had outperformed XBI over the 20 days before day 0, and 7 had underperformed XBI over the last 20 days as of day -1. v0 is a "confirmation" rule; by design it cannot catch the first day of a move, at best the second leg.
4. Changing just one thing (the low-volatility condition becomes "occurred within the past 60 days", i.e. v0b) produces 4 signals: SDGR 2023-05-22 (+42% to the leg high), SDGR 2025-01-22 (at most +7% within 40 days, max drawdown -18%, a failure), SDGR 2026-09-15 (at most +40% over the following 18 trading days, event still in progress), **PRME 2026-10-07 (this very week, outcome unknown)**. The sample is too small to draw conclusions.
5. Common traits before ignition: 8 of the 10 valid events had a 20-day volatility percentile <= 0.35 somewhere between day -60 and day 0, so the "contract first" condition holds up. Only 3 had a day-0 volume ratio >= 3, and 6 had >= 2, so the 3x volume threshold is on the strict side.
6. SDGR had 3 ignition days that came right after an earnings 8-K (item 2.02): 2023-03-01, 2023-11-02, 2026-08-06. On 2020-11-23 there was an item 1.01 (material agreement) 8-K that same day. No event had an S-3 / 424B5 within day -30 to +5.

## 2. Big up-legs and ignition days

The detection method is in `src/alpha_breakout/event_study.py`: run a 25% zigzag on closing prices and take up-legs with a low-to-high gain >= 60%. Day 0 is the first bar in the leg with "gain >= 8%, volume ratio >= 2, and >= 40% of room left to the leg high"; if there is no such bar, it is the first day the close is above low +15% (marked fallback in the table). This is a mechanical approximation; hand-picked ignition days can be written into `manual_day0` in `configs/samples.yaml`.

| Ticker | Low | day 0 | Rule | High | Low->High | day0 close->High | day0->High (trading days) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| PRME | 2024-04-25 | 2024-05-01 | fallback | 2024-05-21 | +87% | +52% | 14 |
| PRME | 2025-05-21 | **2025-06-06** | ignition | 2025-07-14 | +312% | +184% | 24 |
| PRME | 2025-09-02 | 2025-09-05 | fallback | 2025-10-02 | +114% | +79% | 19 |
| PRME | 2026-06-10 | 2026-06-17 | fallback | 2026-07-08 | +71% | +46% | 13 |
| SDGR | 2020-02-13 | 2020-02-18 | fallback | 2020-02-24 | +99% | +66% | 4 |
| SDGR | 2020-03-18 | 2020-03-19 | fallback | 2020-07-09 | +222% | +179% | 77 |
| SDGR | 2020-11-03 | **2020-11-23** | ignition | 2021-03-01 | +135% | +67% | 65 |
| SDGR | 2022-05-11 | 2022-05-17 | fallback | 2022-08-04 | +62% | +40% | 54 |
| SDGR | 2022-12-06 | 2022-12-13 | fallback | 2023-02-02 | +70% | +34% | 34 |
| SDGR | 2023-02-27 | **2023-03-01** | ignition | 2023-07-19 | +158% | +125% | 96 |
| SDGR | 2023-10-30 | **2023-11-02** | ignition | 2023-12-28 | +72% | +49% | 38 |
| SDGR | 2026-03-30 | **2026-08-06** | ignition | 2026-10-05* | +195% | +83% | 41 |

\* The high for SDGR's 2026 leg is as of 2026-10-09 and may not be the final high. SDGR's two 2020 legs are less than 120 trading days after its listing (short_history), have no complete day -60 window, and are excluded from the statistics below.

## 3. Signal behavior from day -60 to day 0 (10 valid events)

Fields: base range = max/min of closes over day -60 to -1, minus 1; rv percentile = percentile of 20-day volatility within the past 252 days; volume ratio = volume / prior 50-day average volume; RS = last 20-day return - XBI last 20-day return.

| Ticker | day 0 | Prior 60d range | Prior 60d min rv pct | Prior 60d max vol ratio | d0 120d range | d0 rv pct | d0 vol ratio | d0 close position | d0 RS | v0 conditions not met on day 0 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| PRME | 2024-05-01 | 118% | 0.28 | 9.7 | 140% | 0.60 | 1.2 | 0.57 | -5% | base, low vol, breakout, volume, close position, RS |
| PRME | 2025-06-06 | 77% | 0.77 | 6.0 | 223% | 0.91 | 2.5 | 0.94 | +20% | base, low vol, breakout, volume (plus liquidity at the $2 threshold) |
| PRME | 2025-09-05 | 229% | 0.32 | 5.8 | 366% | 0.40 | 0.6 | 1.00 | -11% | base, low vol, breakout, volume, RS |
| PRME | 2026-06-17 | 47% | 0.004 | 2.1 | 79% | 0.45 | 1.8 | 0.72 | +7% | low vol, breakout, volume |
| SDGR | 2020-11-23 | 33% | 0.008 | 3.1 | 115% | 0.41 | 2.6 | 0.69 | +8% | low vol, volume |
| SDGR | 2022-05-17 | 69% | 0.50 | 3.4 | 102% | 0.88 | 1.1 | 0.86 | 0% | base, low vol, breakout, volume, RS |
| SDGR | 2022-12-13 | 69% | 0.03 | 3.8 | 122% | 0.26 | 5.3 | 0.61 | -3% | base, breakout, close position, RS |
| SDGR | 2023-03-01 | 70% | 0.22 | 5.3 | 90% | 0.58 | 3.5 | 0.02 | +8% | base, low vol, breakout, close position |
| SDGR | 2023-11-02 | 85% | 0.004 | 1.5 | 185% | 0.29 | 3.4 | 0.49 | -5% | base, breakout, close position, RS |
| SDGR | 2026-08-06 | 46% | 0.008 | 2.7 | 63% | 0.04 | 2.2 | 0.58 | +12% | breakout, volume, close position |

Segment averages (rv percentile / volume ratio / RS, grouped by day -60 to -41, -40 to -21, -20 to -6, -5 to -1, and 0) can be computed directly from `reports/data/window_*_v0.csv`. Two patterns stand out:

- **Contract-then-expand** (PRME 2026-06, SDGR 2020-11, 2023-11, 2026-08): the volatility percentile is pressed down to 0.01-0.3 over day -60 to -21, starts rising around day -5, and volume expands on day 0. v0's direction is right; the problem is the timing of the check.
- **Sharp-drop reversal** (PRME 2024-05, 2025-06, 2025-09, SDGR 2022-05): the prior 60-day range is 70%-230%, volatility stays high throughout, and day 0 is a rebound from oversold. v0 can't catch these, and perhaps shouldn't (they don't follow "breakout" logic).

## 4. Does the v0 scanner fire near the events?

| Version | Signals over full history | Fired inside a big up-leg (day -60 to leg high) |
| --- | --- | --- |
| v0 (original) | 0 | none |
| v0b (low vol changed to "occurred within 60 days") | 4 | SDGR 2023-03-01 leg: 2023-05-22 (day +57, close 38.95, +42% to the high); SDGR 2026-08-06 leg: 2026-09-15 (day +27, close 23.25, +40% to the current high) |

All v0b signals and their follow-through (`reports/data/scanner_signals_v0b.csv`):

| Ticker | Date | Close | Vol ratio | +10d | +20d | +40d | Max gain within 40d | Max drawdown within 40d |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| SDGR | 2023-05-22 | 38.95 | 6.0 | -2% | +21% | +34% | +42% | -14% |
| SDGR | 2025-01-22 | 24.34 | 4.9 | +7% | -1% | -9% | +7% | -18% |
| SDGR | 2026-09-15 | 23.25 | 3.7 | +31% | n/a | n/a | +40% (only 18 days) | +3% |
| PRME | 2026-10-07 | 3.90 | 3.6 | n/a | n/a | n/a | only 2 days | - |

## 5. Filings cross-check (day -30 to +5, form types only)

| Ticker | day 0 | Filings |
| --- | --- | --- |
| PRME | 2025-06-06 | 05-08 8-K(2.02 earnings); 05-19 8-K(5.02 officer change, 7.01, 8.01); 06-05 8-K(5.07 shareholder meeting) |
| PRME | 2025-09-05 | 08-07 8-K(2.02); 08-11 SCHEDULE 13D/A; 09-08 8-K(7.01, after day 0) |
| PRME | 2026-06-17 | 06-08 8-K(5.07) |
| SDGR | 2020-11-23 | 11-12 8-K(2.02); **11-23 8-K(1.01 material agreement), same day as day 0** |
| SDGR | 2022-05-17 | 05-04 8-K(2.02); 05-19 8-K(5.02) |
| SDGR | 2023-03-01 | **02-28 8-K(2.02), the day before day 0** |
| SDGR | 2023-11-02 | **11-01 8-K(2.02), the day before day 0** |
| SDGR | 2026-08-06 | **08-05 8-K(2.02), the day before day 0** |

PRME 2024-05-01 and SDGR 2022-12-13 had none of these filing types in their windows. The specific catalyst for PRME's 2025-06 leg (partnership, data, equity investment) can't be seen from 8-K item numbers; the filing text needs to be read.

## 6. Limitations

- Only 2 tickers and 10 valid events; all statistics are descriptive only and cannot be treated as evidence; there is no control group yet.
- Day 0 comes from a mechanical rule; the fallback ones (especially PRME 2024-05 and 2025-09) have fuzzy ignition points.
- The market-cap, sector, cash runway, and ATM offering filters are not implemented yet (they need SEC XBRL financial data).
- Prices are split-adjusted. The price threshold has been relaxed from $2 to $1 (see Section 8).

## 7. Suggested next steps

1. Add samples (see STRATEGY.md Section 2): get to at least 10 tickers and 30+ events before tuning parameters.
2. Change the low-volatility condition to "contraction occurred within the base" (v0b), and run a sensitivity test on the volume-ratio threshold between 2x and 3x.
3. Study a separate "reversal" rule (a long high-volume green bar after a sharp drop), with statistics kept apart from the breakout type.
4. Build a control group: run v0b over a biotech universe and count all signals, instead of looking only at the sample tickers.
5. Add "the day after an earnings 8-K (2.02)" as an event feature; 3 of SDGR's ignitions came the day after earnings.

## 8. Update: price threshold $2 -> $1 (2026-10-10)

`min_price` in both `configs/scanner_v0.yaml` and `scanner_v0b.yaml` was changed to 1.0, with all other parameters unchanged, and everything was rerun.

- **The signal count did not change**: v0 still has 0, and v0b still has the same 4 (SDGR 2023-05-22, 2025-01-22, 2026-09-15, PRME 2026-10-07).
- PRME's 2025-06-06 day 0 is no longer blocked by the liquidity condition, but it still fails the base, low-vol, breakout, and volume conditions.
- In PRME's 2025-05 to 2025-10 leg, none of the days with a gain >= 8% or a volume ratio >= 3 (26 days in total) satisfied ok_tight_base: the 60-day range before ignition was already above 90%, and the 120-day range exceeded 200%. This leg is a reversal after a sharp drop, not a base breakout, so relaxing the price threshold won't make it fire either.
- Conclusion: what blocks "low-priced bet" samples like PRME is the base and low-volatility conditions, not price. Catching these tickers would need the separate "reversal" rule described in Section 7, item 3.
