"""拉内部人交易（Form 4）：只解析回测交易相关时间窗口内的申报。

用法：python scripts/fetch_insiders.py --trades reports/data/trades_v3b_feat_staged.csv [--pre-days 10]

1. 对交易涉及的公司拉 Form 4 列表（data/cache/form4_index.parquet）
2. 只下载每笔交易 [异动日 - pre_days, 试仓信号日] 内申报的 Form 4，解析公开市场买卖（data/cache/form4_tx.parquet）
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from alpha_breakout import insiders  # noqa: E402

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--trades", nargs="+", default=[str(ROOT / "reports" / "data" / "trades_v3b_feat_staged.csv")])
    ap.add_argument("--pre-days", type=int, default=10)
    a = ap.parse_args()
    u = pd.read_csv(ROOT / "data" / "cache" / "universe.csv")
    t2c = dict(zip(u["ticker"], u["cik"].astype(int)))
    tr = pd.concat([pd.read_csv(p, parse_dates=["surge_date", "signal_date"]) for p in a.trades])
    tr["cik"] = tr["ticker"].map(t2c)
    tr = tr.dropna(subset=["cik", "signal_date"])
    idx = insiders.form4_index(tr["cik"].astype(int).unique())
    print(f"form4 index: {len(idx)} filings, {idx['cik'].nunique()} companies")
    need = []
    by = {c: g for c, g in idx.groupby("cik")}
    for r in tr.itertuples():
        g = by.get(int(r.cik))
        if g is None:
            continue
        lo = r.surge_date - pd.Timedelta(days=a.pre_days)
        need.append(g[(g["filing_date"] >= lo) & (g["filing_date"] <= r.signal_date)])
    need = pd.concat(need).drop_duplicates("accession")
    print(f"form4 to parse: {len(need)}")
    tx = insiders.form4_details(need)
    print(f"form4 tx rows: {len(tx)}; codes: {tx['code'].value_counts().to_dict()}")
