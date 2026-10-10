"""组合层面模拟：读取 backtest.py 输出的逐笔交易，放进有资金和仓位上限的账户里跑资金曲线。

用法：
    python scripts/portfolio.py --tag v3 [--mode staged|immediate] [--set portfolio.max_positions=20 ...]

组合参数默认取该 tag 回测时的配置（summary_<tag>.json 里的 portfolio 节），可以用 --set 覆盖，
例如 --set portfolio.order=rs60_asc portfolio.idle_asset=IWM --label rs_iwm。
输出：reports/data/portfolio_<tag>_<mode>[_label].json、equity_<tag>_<mode>[_label].csv（中位数种子的资金曲线）
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from alpha_breakout.config import _merge  # noqa: E402
from alpha_breakout.data import adjust, load_raw  # noqa: E402
from alpha_breakout.portfolio import simulate  # noqa: E402
from backtest import parse_set  # noqa: E402

OUT = ROOT / "reports" / "data"


def run(tag: str, mode: str = "staged", overrides: dict | None = None, seeds: int | None = None,
        label: str = "", log=print) -> dict:
    summary = json.loads((OUT / f"summary_{tag}.json").read_text())
    cfg = _merge(summary["config"], overrides or {})
    pcfg = cfg.get("portfolio") or {}
    defaults = {"initial_equity": 100000, "risk_per_trade": 0.005, "max_positions": 10,
                "max_new_per_day": 3, "max_position_pct": 0.2, "seeds": 20}
    pcfg = {**defaults, **pcfg}
    trades = pd.read_csv(OUT / f"trades_{tag}_{mode}.csv", parse_dates=["entry_date", "exit_date"])
    closes = {}
    for t in trades["ticker"].unique():
        raw = load_raw(t)
        if raw is not None:
            closes[t] = adjust(raw)["Close"]
    cal = adjust(load_raw("IWM")).index
    cal = cal[(cal >= trades["entry_date"].min()) & (cal <= trades["exit_date"].max())]
    probe = 1.0 if mode == "immediate" else cfg["position"]["probe_size"]
    idle = None
    if pcfg.get("idle_asset"):
        idle = adjust(load_raw(pcfg["idle_asset"]))["Close"]
    results = []
    n = seeds or pcfg["seeds"]
    for seed in range(n):
        res = simulate(trades, closes, cal, pcfg, probe, cfg["position"]["slippage"], seed, idle)
        results.append((res.stats(), res.equity))
    keys = ["total_return", "cagr", "max_drawdown", "sharpe", "trades_taken", "trades_skipped"]
    agg = {k: {"median": float(np.nanmedian([r[0][k] for r in results])),
               "p10": float(np.nanpercentile([r[0][k] for r in results], 10)),
               "p90": float(np.nanpercentile([r[0][k] for r in results], 90))} for k in keys}
    years = sorted({y for r in results for y in r[0]["yearly_return"]})
    agg["yearly_return_median"] = {y: float(np.median([r[0]["yearly_return"].get(y, np.nan) for r in results]))
                                   for y in years}
    order = np.argsort([r[0]["total_return"] for r in results])
    med = results[order[len(order) // 2]]
    out = {"tag": tag, "mode": mode, "portfolio": pcfg, "seeds": n, "summary": agg}
    suffix = f"{tag}_{mode}" + (f"_{label}" if label else "")
    (OUT / f"portfolio_{suffix}.json").write_text(json.dumps(out, indent=2, ensure_ascii=False, default=str))
    med[1].rename("equity").to_csv(OUT / f"equity_{suffix}.csv", float_format="%.2f")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="v4")
    ap.add_argument("--mode", default="staged", choices=["staged", "immediate"])
    ap.add_argument("--set", nargs="*")
    ap.add_argument("--seeds", type=int)
    ap.add_argument("--label", default="", help="输出文件名后缀，区分不同组合参数")
    a = ap.parse_args()
    out = run(a.tag, a.mode, parse_set(a.set), a.seeds, a.label)
    print(json.dumps(out["summary"], indent=2, ensure_ascii=False))
