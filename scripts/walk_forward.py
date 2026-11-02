"""Walk-forward check: pick filter conditions using only the training period, then see how they do in the test period.

Usage:
    python scripts/backtest.py --tag v3b_feat --features --config configs/strategy_v4.yaml \
        --set filters.fundamentals.min_rev_yoy=null filters.fundamentals.min_gm_change=null \
              filters.fundamentals.allow_missing=true position.partial_r=null position.after_partial_trail_ma=null
    python scripts/walk_forward.py --tag v3b_feat [--split 2023-01-01] [--min-train 100]

Reads the baseline trades with no fundamental filters (with the --features columns) and screens a grid of filter conditions after the fact:
revenue YoY floor x cash runway floor x gross margin up >= 2 points x 60-day relative strength cap.
Ranks by capped-at-20R expectancy in the training period (entries before split) and prints the top 10's test-period results.

Note: this is an after-the-fact screen, not exactly the same as the engine applying filters day by day (a signal filtered out in the engine can trigger again later),
so rerun the chosen combinations through backtest.py to confirm.
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
    print(f"train < {a.split}, test >= {a.split}; top 10 by capped training-period expectancy:")
    print(ok.head(10).round(3).to_string(index=False))
    base = g[g.rev.isna() & g.run.isna() & ~g.gm & g.rs.isna()]
    print("\nNo filters:")
    print(base.round(3).to_string(index=False))
    print(f"\nSpearman correlation of train vs test ranks ({len(ok)} combinations):",
          round(ok[["train_cap", "test_cap"]].corr(method="spearman").iloc[0, 1], 3))
    print("Share of the top 10 with a positive test period:", (ok.head(10).test_cap > 0).mean())


if __name__ == "__main__":
    main()
