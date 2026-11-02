"""Build the market-wide stock universe and bulk-download daily bars (about 15 minutes the first time, then only fills gaps).

Usage:
    PYTHONPATH=src python scripts/build_universe.py [--refresh] [--start 2018-06-01]

Output: data/cache/universe.csv (ticker, cik, name, exchange); prices go to data/cache/prices/.
Only companies with SEC share count data are kept (otherwise market cap cannot be estimated; mostly foreign private issuers).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from alpha_breakout.data import BENCHMARKS, download_bulk  # noqa: E402
from alpha_breakout.universe import listed_common_stocks, shares_history  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--start", default="2018-06-01")
    args = ap.parse_args()

    u = listed_common_stocks(args.refresh)
    sh = shares_history(refresh=args.refresh)
    u = u[u["cik"].isin(set(sh["cik"]))]
    print(f"universe: {len(u)} tickers with SEC shares data")
    ok = download_bulk(list(u["ticker"]) + list(BENCHMARKS), start=args.start, refresh=args.refresh)
    u = u[u["ticker"].isin(ok)]
    out = ROOT / "data" / "cache" / "universe.csv"
    u.to_csv(out, index=False)
    print(f"saved {len(u)} tickers -> {out}")


if __name__ == "__main__":
    main()
