# 2026 v4 rule backtest details (through 2026-10-09)

- Rules: `configs/strategy_v4.yaml`
  - Surge into watchlist -> watch 10-90 days -> new high since the surge, with revenue YoY >= 20%, gross margin up YoY >= 2 points, and outside the 14 days before expected earnings -> probe 1/3
  - Add at 1R (exit if not reached within 10 days) -> sell half at 3R -> exit on MA break / stop
- Two updates on 2026-10-11; this table has been recomputed under the latest rules:
  - Probe validation period 20 -> 10 days: follow-up testing found the effect is essentially noise; see STRATEGY.md for details;
  - New rule "no probe in the 14 days before expected earnings": the effect improves monotonically across 7 / 10 / 14 days, and both periods improve.
- Data source: `reports/data/trades_v4_staged.csv`, filtered to trades with a probe signal in 2026
- Returns are measured as "target full position = 100%" (including 0.5% one-way slippage); R uses the probe risk as 1R

## Summary

- 40 signals across 31 stocks; as of 10-09 all have exited, no open positions
- Win rate 38% (15 profitable trades); 11 reached the add
- Total +22.6R, average +0.57R per trade; average +0.30R with each trade capped at 20R
- One AAOI trade made +30.6R; without it, the other 39 trades total -8.0R
- Portfolio ($100k, 0.5% risk per trade, idle cash in IWM) is +18.5% this year, with a -17% max drawdown over the period; IWM +14.1% over the same period
- Evolution: original rules (20-day validation period, no earnings filter) 39 trades, +12.2R, portfolio +10.8% -> 10-day validation period 42 trades, +17.4R, +15.4% -> with earnings filter 40 trades, +22.6R, +18.5%

## Trade by trade

| Ticker | Surge date | Surge gain | Probe signal date | Entry price | Initial stop | Path | Exit date | Return | R | Revenue YoY | Gross margin change (pts) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| AEVA | 2025-12-03 | +31% | 2026-01-06 | 17.25 | 14.66 | add -> broke MA | 2026-01-26 | -16.2% | -3.2 | +59% | +44.1 |
| OSS | 2025-11-26 | +32% | 2026-01-07 | 8.61 | 7.32 | add -> broke MA | 2026-02-03 | -10.5% | -2.1 | +37% | +48.2 |
| UAMY | 2025-12-24 | +40% | 2026-01-12 | 8.72 | 7.41 | probe -> stop | 2026-01-14 | -5.3% | -1.1 | +238% | +6.7 |
| RCAT | 2025-12-24 | +32% | 2026-01-13 | 12.71 | 10.80 | add -> broke MA | 2026-02-03 | -3.4% | -0.7 | +646% | +36.9 |
| UAMY | 2025-12-24 | +40% | 2026-01-20 | 9.45 | 8.03 | probe -> stop | 2026-01-29 | -5.3% | -1.1 | +238% | +6.7 |
| FCEL | 2025-12-08 | +32% | 2026-01-22 | 10.13 | 8.61 | probe -> stop | 2026-01-30 | -5.3% | -1.1 | +97% | +164.5 |
| TYGO | 2026-01-20 | +127% | 2026-02-04 | 3.65 | 3.10 | probe -> stop | 2026-02-11 | -5.3% | -1.1 | +115% | +30.2 |
| ESTA | 2025-11-05 | +22% | 2026-02-13 | 74.09 | 66.07 | probe -> stop | 2026-02-24 | -3.9% | -1.1 | +34% | +6.2 |
| AAOI | 2025-11-28 | +37% | 2026-02-20 | 50.52 | 42.94 | add -> sold half at 3R -> broke MA | 2026-06-18 | +152.6% | +30.6 | +82% | +3.6 |
| TYGO | 2026-01-20 | +127% | 2026-02-25 | 4.06 | 3.45 | probe -> stop | 2026-03-02 | -5.3% | -1.1 | +115% | +30.2 |
| LXU | 2026-02-26 | +15% | 2026-03-12 | 15.20 | 12.92 | probe -> no 1R in 10 days, exited | 2026-03-30 | +2.3% | +0.5 | +42% | +23.7 |
| LWLG | 2026-02-26 | +30% | 2026-03-12 | 7.04 | 5.98 | add -> sold half at 3R -> broke MA | 2026-05-29 | +42.5% | +8.5 | +27% | +8.2 |
| RCAT | 2026-03-02 | +17% | 2026-03-18 | 16.18 | 13.76 | probe -> stop | 2026-03-20 | -5.3% | -1.1 | +646% | +36.9 |
| PAHC | 2026-02-05 | +22% | 2026-03-25 | 55.89 | 47.65 | probe -> no 1R in 10 days, exited | 2026-04-13 | +0.5% | +0.1 | +21% | +2.5 |
| LXU | 2026-02-26 | +15% | 2026-03-30 | 15.59 | 14.00 | probe -> stop | 2026-04-08 | -5.0% | -1.5 | +42% | +23.7 |
| AMPX | 2026-03-05 | +19% | 2026-04-21 | 21.86 | 18.58 | probe -> no 1R in 10 days, exited | 2026-05-07 | -4.5% | -0.9 | +173% | +80.4 |
| UAMY | 2026-03-02 | +38% | 2026-04-30 | 11.79 | 10.02 | probe -> stop | 2026-05-12 | -5.3% | -1.1 | +238% | +6.7 |
| MXL | 2026-04-17 | +13% | 2026-05-01 | 77.74 | 66.08 | add -> broke MA | 2026-06-02 | -1.4% | -0.3 | +56% | +2.5 |
| MNTN | 2026-02-11 | +36% | 2026-05-05 | 10.63 | 9.30 | probe -> stop | 2026-05-06 | -4.5% | -1.1 | +23% | +7.2 |
| QUBT | 2026-04-15 | +36% | 2026-05-06 | 10.05 | 8.55 | add -> broke MA | 2026-05-19 | -14.8% | -3.0 | +280% | +23.9 |
| EOSE | 2026-04-14 | +41% | 2026-05-08 | 7.88 | 6.69 | probe -> no 1R in 10 days, exited | 2026-05-27 | +3.4% | +0.7 | +3473% | +2805.7 |
| FCEL | 2026-04-20 | +19% | 2026-05-08 | 13.63 | 11.59 | add -> sold half at 3R -> broke MA | 2026-07-14 | +29.3% | +5.9 | +61% | +164.5 |
| HIVE | 2026-04-17 | +15% | 2026-05-11 | 2.91 | 2.47 | add -> sold half at 3R -> broke MA | 2026-07-02 | +25.7% | +5.1 | +219% | +16.6 |
| VUZI | 2026-04-30 | +21% | 2026-05-18 | 3.55 | 3.02 | add -> broke MA | 2026-06-08 | -4.6% | -0.9 | +76% | +406.2 |
| OUST | 2026-05-14 | +42% | 2026-05-29 | 44.99 | 38.24 | probe -> stop | 2026-06-09 | -5.3% | -1.1 | +41% | +3.8 |
| SVCO | 2026-03-19 | +94% | 2026-06-04 | 13.07 | 11.11 | probe -> stop | 2026-06-05 | -5.3% | -1.1 | +70% | +3.3 |
| CLPT | 2026-04-17 | +36% | 2026-06-17 | 17.70 | 15.04 | probe -> no 1R in 10 days, exited | 2026-07-07 | +0.5% | +0.1 | +43% | +3.5 |
| HYLN | 2026-05-11 | +31% | 2026-06-18 | 8.20 | 6.97 | probe -> stop | 2026-06-23 | -5.5% | -1.1 | +479% | +5.0 |
| BFLY | 2026-02-26 | +53% | 2026-06-18 | 8.62 | 7.33 | probe -> stop | 2026-06-22 | -5.3% | -1.1 | +25% | +5.9 |
| MRVI | 2026-05-08 | +30% | 2026-06-25 | 6.11 | 5.19 | probe -> no 1R in 10 days, exited | 2026-07-14 | +1.6% | +0.3 | +41% | +34.7 |
| PAYS | 2026-03-25 | +60% | 2026-06-26 | 8.06 | 6.85 | probe -> no 1R in 10 days, exited | 2026-07-15 | +2.6% | +0.5 | +51% | +2.1 |
| MRVI | 2026-05-08 | +30% | 2026-07-14 | 6.47 | 5.67 | probe -> no 1R in 10 days, exited | 2026-07-30 | +0.3% | +0.1 | +41% | +34.7 |
| SLN | 2026-06-24 | +37% | 2026-07-22 | 11.75 | 9.99 | probe -> no 1R in 10 days, exited | 2026-08-07 | +0.3% | +0.1 | +197% | +35.4 |
| URGN | 2026-05-11 | +35% | 2026-08-05 | 46.14 | 39.22 | probe -> no 1R in 10 days, exited | 2026-08-21 | +0.4% | +0.1 | +152% | +3.4 |
| SLN | 2026-06-24 | +37% | 2026-08-10 | 15.97 | 13.57 | probe -> stop | 2026-08-12 | -5.3% | -1.1 | +197% | +35.4 |
| UMAC | 2026-05-27 | +38% | 2026-08-14 | 32.55 | 27.67 | probe -> stop | 2026-08-19 | -5.3% | -1.1 | +296% | +8.5 |
| ETON | 2026-07-17 | +14% | 2026-08-14 | 59.15 | 50.28 | probe -> no 1R in 10 days, exited | 2026-09-01 | +0.4% | +0.1 | +40% | +3.7 |
| PAYS | 2026-08-06 | +35% | 2026-08-21 | 14.15 | 12.29 | probe -> stop | 2026-08-31 | -4.7% | -1.1 | +51% | +2.1 |
| SENS | 2026-08-07 | +40% | 2026-08-21 | 9.57 | 8.13 | probe -> no 1R in 10 days, exited | 2026-09-09 | -1.4% | -0.3 | +87% | +35.2 |
| FWDI | 2026-07-02 | +36% | 2026-08-21 | 5.55 | 4.72 | add -> broke MA | 2026-09-17 | +0.0% | +0.0 | +315% | +75.7 |

Note: EOSE's gross margin change is very large because gross profit in the same quarter last year was deeply negative; the number itself is meaningless, but it still meets the "improvement >= 2 points" condition.
