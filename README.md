# alpha-breakout

Strategy research on US small caps across all sectors: "surge → watch → probe → add". Strategy ideas and to-dos are in [STRATEGY.md](STRATEGY.md); phase reports are in `reports/`.

## Environment

```bash
pip install -r requirements.txt
export SEC_USER_AGENT="Your Name your@email.com"   # SEC EDGAR requires contact info
```

The network must allow `query1.finance.yahoo.com` (prices) and `data.sec.gov`, `www.sec.gov` (filings).

## Structure

```
configs/strategy_v4.yaml   Strategy v4 parameters (current default: revenue ≥ 20% + gross margin improvement + 3R partial take-profit + idle cash in IWM)
configs/strategy_v3.yaml   Strategy v3 parameters (revenue ≥ 50% + cash runway ≥ 4 quarters + 3R partial take-profit)
configs/strategy_v2.yaml   Strategy v2 parameters (v1 + data cleaning + offering filter + catalyst tags)
configs/strategy_v1.yaml   Strategy v1 parameters (surge / watch / probe / add / exit)
configs/scanner_v0.yaml    Old base-breakout scanner v0 parameters (replaced by v1)
configs/scanner_v0b.yaml   v0b: low-volatility condition changed to "occurred within 60 days"
configs/samples.yaml       Event-review samples (the surge-start day can be set manually via manual_day0)
src/alpha_breakout/
  data.py                  yfinance daily bars + local cache in data/cache/prices/ (with bulk download)
  universe.py              Full-market stock universe (SEC list) + point-in-time market cap estimate from share count
  strategy.py              State-machine backtest (staged scale-in / immediate control)
  filings.py               Full-market SEC filing index, offering forms, catalyst classification, 13D/13G counts
  fundamentals.py          SEC XBRL quarterly fundamentals (revenue, net income, cash), point-in-time values
  research.py              Pre-probe checks (13D/13G, fundamentals) and trade features
  portfolio.py             Portfolio-level simulation (capital, position caps, mark-to-market)
  insiders.py              Insider transactions (Form 4) listing and parsing
  scanner.py               Daily indicator computation + v0 rule evaluation
  event_study.py           Big-move detection (zigzag), day 0 alignment, window statistics
  sec.py                   SEC EDGAR filing list
scripts/build_universe.py  Build the stock universe and download full-market prices (about 25 minutes the first time)
scripts/fetch_filings.py   Fetch the full-market SEC filing index (about 15 minutes the first time)
scripts/fetch_fundamentals.py  Fetch full-market quarterly fundamentals (about 10 minutes the first time)
scripts/backtest.py        Full-market backtest (--config picks v1 / v2 / v3), output to reports/data/
scripts/portfolio.py       Portfolio simulation (reads trades from the backtest output; supports signal ordering, idle cash in IWM)
scripts/walk_forward.py    Walk-forward validation: pick filters in the training period, test in the test period
scripts/fetch_insiders.py  Fetch Form 4s in windows around backtest trades (about 50 minutes)
scripts/daily_scan.py      Daily scan: writes reports/daily/<date>.md and appends to the research journal
scripts/evaluate_journal.py  Evaluate the research journal (subsequent performance of the do / skip groups)
scripts/event_study.py     v0 sample review (phase 1)
```

## Usage

```bash
python scripts/build_universe.py
python scripts/fetch_filings.py
python scripts/fetch_fundamentals.py
python scripts/backtest.py --tag v4 --features        # defaults to configs/strategy_v4.yaml
python scripts/portfolio.py --tag v4
python scripts/daily_scan.py                         # after each close (scheduled task runs automatically every trading day at 17:47 US Eastern and pushes to main)
python scripts/daily_scan.py --bootstrap             # fresh environment: downloads only the data the scan needs, about 10 minutes
python scripts/evaluate_journal.py
python scripts/backtest.py --config configs/strategy_v1.yaml --tag v1
python scripts/backtest.py --tag v4_atr --set position.exit_mode=atr   # change a single parameter
python scripts/event_study.py --tag v0
python scripts/event_study.py --config configs/scanner_v0b.yaml --tag v0b
```

Using it from code:

```python
from alpha_breakout.data import get_daily
from alpha_breakout.scanner import scan
from alpha_breakout.config import load_config

cfg = load_config(overrides={"volume": {"min_volume_mult": 2.5}})
signals = scan(get_daily("SDGR"), get_daily("XBI"), cfg)
signals[signals["signal"]]
```
