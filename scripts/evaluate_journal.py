"""评估研究日志：比较你标记“做”和“不做”的票，之后的实际表现。

用法：python scripts/evaluate_journal.py [--journal journal/research_log.csv]

对每条记录，从信号日次日开盘价算 5 / 20 / 60 个交易日后的收益，以及期间最大涨幅、最大回撤。
分三组汇总：
- 规则全部信号：日志里的每一条，不管你填没填 decision（= 规则自己的前瞻表现）
- 做 / 不做：你填了 decision 的记录（= 人工判断有没有增益）
数据不够的天数显示为空。先跑一次 daily_scan.py --no-update 更新行情再评估。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from alpha_breakout.data import adjust, load_raw  # noqa: E402


def outcome(ticker: str, signal_date) -> dict:
    raw = load_raw(ticker)
    if raw is None:
        return {}
    px = adjust(raw)
    i = px.index.searchsorted(pd.Timestamp(signal_date), side="right")  # 信号日的下一个交易日
    if i >= len(px):
        return {}
    entry = px["Open"].iloc[i]
    res = {"entry_open": entry}
    for n in (5, 20, 60):
        res[f"ret_{n}d"] = px["Close"].iloc[i + n] / entry - 1 if i + n < len(px) else np.nan
    fut = px["Close"].iloc[i: i + 61]
    res["max_gain_60d"] = fut.max() / entry - 1
    res["max_dd_60d"] = fut.min() / entry - 1
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--journal", default=str(ROOT / "journal" / "research_log.csv"))
    a = ap.parse_args()
    if not Path(a.journal).exists():
        print("还没有研究日志：先跑 scripts/daily_scan.py，出现“可试仓”的票后会自动生成。")
        return
    j = pd.read_csv(a.journal)
    if j.empty:
        print("日志是空的。")
        return
    res = pd.DataFrame([outcome(r.ticker, r.signal_date) for r in j.itertuples()], index=j.index)
    j = j.join(res)
    dec = j["decision"].fillna("").astype(str).str.strip()
    j["group"] = np.where(dec == "", "未填", np.where(dec == "做", "做", "不做"))
    cols = ["ret_5d", "ret_20d", "ret_60d", "max_gain_60d", "max_dd_60d"]
    pd.set_option("display.width", 200)
    print("== 规则全部信号")
    print(j[cols].agg(["count", "mean", "median"]).round(3).to_string())
    filled = j[j["group"] != "未填"]
    if filled.empty:
        print("\n还没有填写 decision 的记录，无法比较人工判断。")
    else:
        print("\n== 按你的判断分组")
        print(filled.groupby("group")[cols].agg(["count", "mean", "median"]).round(3).to_string())
    out = ROOT / "journal" / "evaluation.csv"
    j.to_csv(out, index=False)
    print(f"明细：{out}")


if __name__ == "__main__":
    main()
