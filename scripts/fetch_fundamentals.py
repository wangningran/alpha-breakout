"""拉全市场季度基本面（SEC XBRL frames），首次约 5 分钟。

用法：python scripts/fetch_fundamentals.py [--refresh]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from alpha_breakout import fundamentals  # noqa: E402

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true")
    df = fundamentals.fetch(refresh=ap.parse_args().refresh)
    print(f"fundamentals: {len(df)} rows, {df['cik'].nunique()} companies")
