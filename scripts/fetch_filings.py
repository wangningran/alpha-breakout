"""Fetch the market-wide SEC filing index (for the offering filter and catalyst classification); about 15 minutes the first time, then only new companies.

Usage: PYTHONPATH=src python scripts/fetch_filings.py [--refresh]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from alpha_breakout.filings import fetch_all  # noqa: E402

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()
    u = pd.read_csv(ROOT / "data" / "cache" / "universe.csv")
    fetch_all(u["cik"].unique(), refresh=args.refresh)
