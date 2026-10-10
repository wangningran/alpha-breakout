"""前后分段验证：只用训练期挑选过滤条件，看它们在测试期的表现。

用法：
    python scripts/backtest.py --tag v3b_feat --features --config configs/strategy_v4.yaml \
        --set filters.fundamentals.min_rev_yoy=null filters.fundamentals.min_gm_change=null \
              filters.fundamentals.allow_missing=true position.partial_r=null position.after_partial_trail_ma=null
    python scripts/walk_forward.py --tag v3b_feat [--split 2023-01-01] [--min-train 100]

读取不带任何基本面过滤的基准交易（带 --features 特征列），对过滤条件网格做事后筛选：
营收同比下限 × 现金跑道下限 × 毛利率是否提升 ≥ 2 个点 × 60 日相对强度上限。
按训练期（split 之前入场）的封顶 20R 期望排序，输出前 10 名在测试期的表现。

注意：这是事后筛选，和引擎里真正逐日执行过滤不完全一样（引擎里被过滤掉的信号之后还可能再次触发），
挑出来的组合要再用 backtest.py 跑一遍确认。
"""
from __future__ import annotations

import argparse
import itertools
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="v3b_feat")
    ap.add_argument("--split", default="2023-01-01")
    ap.add_argument("--min-train", type=int, default=100)
    a = ap.parse_args()

    d = pd.read_csv(ROOT / "reports" / "data" / f"trades_{a.tag}_staged.csv", parse_dates=["entry_date"])
    d = d[d.exit_reason != "open"].copy()
    d["rc"] = d.r_multiple.clip(upper=20)
    train = d.entry_date < a.split
    test = ~train
    grid = []
    for rev, run, gm, rs in itertools.product([None, 0.0, 0.2, 0.5, 1.0], [None, 2, 4, 8], [False, True], [None, 1.0, 0.5]):
        m = pd.Series(True, index=d.index)
        if rev is not None:
            m &= d.rev_yoy >= rev
        if run is not None:
            m &= d.runway_q >= run
        if gm:
            m &= d.gm_change >= 0.02
        if rs is not None:
            m &= d.rs60 <= rs
        tr, te = d[m & train], d[m & test]
        grid.append(dict(rev=rev, run=run, gm=gm, rs=rs, n_train=len(tr), train_cap=tr.rc.mean(),
                         n_test=len(te), test_cap=te.rc.mean(), test_raw=te.r_multiple.mean()))
    g = pd.DataFrame(grid)
    ok = g[g.n_train >= a.min_train].sort_values("train_cap", ascending=False)
    pd.set_option("display.width", 200)
    print(f"训练期 < {a.split}，测试期 ≥ {a.split}；按训练期封顶期望排序的前 10 名：")
    print(ok.head(10).round(3).to_string(index=False))
    base = g[g.rev.isna() & g.run.isna() & ~g.gm & g.rs.isna()]
    print("\n不加过滤：")
    print(base.round(3).to_string(index=False))
    print(f"\n训练期与测试期排名的相关系数（Spearman，{len(ok)} 个组合）：",
          round(ok[["train_cap", "test_cap"]].corr(method="spearman").iloc[0, 1], 3))
    print("前 10 名中测试期为正的比例：", (ok.head(10).test_cap > 0).mean())


if __name__ == "__main__":
    main()
