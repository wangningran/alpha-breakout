"""拉全市场 SEC 公告索引（增发过滤、催化剂分类用），首次约 15 分钟，之后只补新公司。

用法：PYTHONPATH=src python scripts/fetch_filings.py [--refresh]
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
