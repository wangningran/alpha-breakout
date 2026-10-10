"""全市场回测：异动 → 观察 → 试仓 → 加仓，并和“异动次日直接满仓”对照。

用法：
    python scripts/backtest.py [--config configs/strategy_v3.yaml] [--tag v3]
        [--set position.exit_mode=atr ...] [--tickers SDGR,PRME] [--features]

前置：scripts/build_universe.py（行情）、scripts/fetch_filings.py（公告）、
      scripts/fetch_fundamentals.py（基本面，用到 fundamentals 过滤或 --features 时需要）。
输出到 reports/data/：trades_<tag>_staged.csv、trades_<tag>_immediate.csv、summary_<tag>.json
--features：给每笔分批交易加上试仓日的 13D/13G 和基本面特征列（用于分组研究）。
"""
from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from alpha_breakout import filings  # noqa: E402
from alpha_breakout import fundamentals as fmod  # noqa: E402
from alpha_breakout.research import make_entry_filter, trade_features  # noqa: E402
from alpha_breakout.config import load_config  # noqa: E402
from alpha_breakout.data import adjust, get_daily, load_raw  # noqa: E402
from alpha_breakout.strategy import market_filter, run_ticker, trades_frame  # noqa: E402
from alpha_breakout.universe import shares_history, shares_series  # noqa: E402

OUT = ROOT / "reports" / "data"
_EMPTY = pd.DataFrame(columns=["cik", "date", "form", "items"])
_G = {}


def _init(cfg, sh, fil, fund, want_features):
    _G["cfg"], _G["sh"], _G["fil"], _G["fund"], _G["feat"] = cfg, sh, fil, fund, want_features
    _G["iwm"] = adjust(load_raw("IWM"))["Close"]
    m = cfg.get("market") or {}
    _G["mkt"] = market_filter(get_daily(m.get("ticker", "IWM")), cfg) if m.get("enabled") else None


def _work(row):
    t, cik = row
    raw = load_raw(t)
    if raw is None or len(raw) < 80:
        return t, [], [], {}
    df = adjust(raw)
    s = shares_series(_G["sh"], cik, df.index)
    stats = {}
    mkt = _G["mkt"]
    fil = _G["fil"].get(cik) if _G["fil"] is not None else None
    if _G["fil"] is not None and fil is None:
        fil = _EMPTY
    ftab = fmod.company_table(_G["fund"], cik) if _G["fund"] is not None else None
    efilter = make_entry_filter(_G["cfg"], fil, ftab, df["Close"], _G["iwm"])
    staged = trades_frame(run_ticker(t, df, s, _G["cfg"], "staged", stats, mkt, fil, efilter), _G["cfg"], "staged")
    imm = trades_frame(run_ticker(t, df, s, _G["cfg"], "immediate", None, mkt, fil), _G["cfg"], "immediate")
    if _G["feat"] and len(staged):
        feats = [{**trade_features(fil, ftab, r.surge_date, r.signal_date),
                  **rel_strength(df["Close"], _G["iwm"], r.signal_date)} for r in staged.itertuples()]
        staged = pd.concat([staged, pd.DataFrame(feats, index=staged.index)], axis=1)
    return t, staged.to_dict("records"), imm.to_dict("records"), stats


def rel_strength(close: pd.Series, bench: pd.Series, date) -> dict:
    """试仓信号日的相对强度：个股 N 日涨幅 − IWM 同期涨幅。"""
    out = {}
    i = close.index.get_loc(pd.Timestamp(date))
    b = bench.reindex(close.index).ffill()
    for n in (20, 60):
        if i >= n and b.iloc[i - n] > 0:
            out[f"rs{n}"] = float(close.iloc[i] / close.iloc[i - n] - b.iloc[i] / b.iloc[i - n])
        else:
            out[f"rs{n}"] = np.nan
    return out


def parse_set(items):
    """--set a.b=1 → {"a": {"b": 1}}"""
    out = {}
    for it in items or []:
        k, v = it.split("=", 1)
        d = out
        keys = k.split(".")
        for kk in keys[:-1]:
            d = d.setdefault(kk, {})
        d[keys[-1]] = yaml.safe_load(v)
    return out


def summarize(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"trades": 0}
    closed = df[df["exit_reason"] != "open"]
    r = closed["r_multiple"]
    pnl = closed["pnl"]
    wins = pnl > 0
    # R 曲线按出场日排序：每笔交易承担相同风险（1R）的累计收益
    curve = closed.sort_values("exit_date")["r_multiple"].cumsum()
    dd = (curve - curve.cummax()).min() if len(curve) else 0
    streak = max_streak(closed.sort_values("exit_date")["pnl"] <= 0)
    return {
        "trades": int(len(df)),
        "closed": int(len(closed)),
        "open": int((df["exit_reason"] == "open").sum()),
        "win_rate": round(float(wins.mean()), 3),
        "added_rate": round(float(closed["added"].mean()), 3),
        "avg_pnl_pct": round(float(pnl.mean()) * 100, 2),
        "median_pnl_pct": round(float(pnl.median()) * 100, 2),
        "avg_win_pct": round(float(pnl[wins].mean()) * 100, 2) if wins.any() else None,
        "avg_loss_pct": round(float(pnl[~wins].mean()) * 100, 2) if (~wins).any() else None,
        "expectancy_R": round(float(r.mean()), 3),
        "expectancy_R_cap20": round(float(r.clip(upper=20).mean()), 3),
        "median_R": round(float(r.median()), 3),
        "avg_win_R": round(float(r[wins].mean()), 2) if wins.any() else None,
        "avg_loss_R": round(float(r[~wins].mean()), 2) if (~wins).any() else None,
        "profit_factor": round(float(pnl[wins].sum() / -pnl[~wins].sum()), 2) if (~wins).any() else None,
        "share_R_from_top5pct": round(float(r.nlargest(max(1, len(r) // 20)).sum() / r[r > 0].sum()), 3)
        if (r > 0).any() else None,
        "total_R": round(float(r.sum()), 1),
        "max_drawdown_R": round(float(dd), 1),
        "max_losing_streak": int(streak),
        "avg_hold_days": round(float(closed["hold_days"].mean()), 1),
    }


def max_streak(b: pd.Series) -> int:
    best = cur = 0
    for x in b:
        cur = cur + 1 if x else 0
        best = max(best, cur)
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(ROOT / "configs" / "strategy_v4.yaml"))
    ap.add_argument("--tag", default="v4")
    ap.add_argument("--set", nargs="*")
    ap.add_argument("--tickers")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--catalyst", action="store_true", help="没开增发过滤时也加载公告，给交易打催化剂标签")
    ap.add_argument("--universe", default=str(ROOT / "data" / "cache" / "universe.csv"))
    ap.add_argument("--features", action="store_true")
    args = ap.parse_args()

    cfg = load_config(args.config, parse_set(args.set))
    u = pd.read_csv(args.universe)
    if args.tickers:
        u = u[u["ticker"].isin(args.tickers.split(","))]
    sh = shares_history()
    fil = None
    flt = cfg.get("filters") or {}
    need_holders = ((flt.get("holders") or {}).get("require") or "none") != "none"
    need_fund = any((flt.get("fundamentals") or {}).get(k) not in (None, False)
                    for k in ("min_runway_q", "min_rev_yoy", "require_accel", "min_gm_change"))
    if (flt.get("offering") or {}).get("enabled") or args.catalyst or args.features or need_holders:
        f = filings.load()
        if f is None:
            sys.exit("缺少 data/cache/filings.parquet，先跑 scripts/fetch_filings.py")
        keep = f["form"].isin(filings.OFFERING_FORMS | {"S-3", "F-3"}) | f["form"].str.startswith(
            ("8-K", "SC 13D", "SCHEDULE 13D", "SC 13G", "SCHEDULE 13G"))
        fil = {int(k): g.reset_index(drop=True) for k, g in f[keep].groupby("cik")}
    fund = None
    if need_fund or args.features:
        fund = fmod.load()
        if fund is None:
            sys.exit("缺少 data/cache/fundamentals.parquet，先跑 scripts/fetch_fundamentals.py")
        fund = fund[fund["cik"].isin(set(u["cik"].astype(int)))]
    rows = list(zip(u["ticker"], u["cik"].astype(int)))

    staged, imm, funnel = [], [], {}
    with ProcessPoolExecutor(args.workers, initializer=_init, initargs=(cfg, sh, fil, fund, args.features)) as ex:
        for k, (t, s, i, st) in enumerate(ex.map(_work, rows, chunksize=20)):
            staged += s
            imm += i
            for kk, v in st.items():
                funnel[kk] = funnel.get(kk, 0) + v
            if k % 500 == 0:
                print(f"  {k}/{len(rows)}")

    OUT.mkdir(parents=True, exist_ok=True)
    sdf, idf = pd.DataFrame(staged), pd.DataFrame(imm)
    sdf.to_csv(OUT / f"trades_{args.tag}_staged.csv", index=False, float_format="%.4f")
    idf.to_csv(OUT / f"trades_{args.tag}_immediate.csv", index=False, float_format="%.4f")

    summary = {"config": cfg, "universe_tickers": len(rows), "funnel": funnel,
               "staged": summarize(sdf), "immediate": summarize(idf)}
    if not sdf.empty:
        sdf["entry_year"] = pd.to_datetime(sdf["entry_date"]).dt.year
        sdf["timing"] = np.where(sdf["days_surge_to_signal"] <= 20, "10-20d 持续型",
                                 np.where(sdf["days_surge_to_signal"] <= 45, "21-45d", "46-90d 二波"))
        summary["staged_by_year"] = {str(y): summarize(g) for y, g in sdf.groupby("entry_year")}
        sdf["period"] = np.where(sdf["entry_year"] <= 2023, "2019-2023", "2024-2026")
        summary["staged_by_period"] = {k: summarize(g) for k, g in sdf.groupby("period")}
        if not idf.empty:
            iy = pd.to_datetime(idf["entry_date"]).dt.year
            summary["immediate_by_period"] = {k: summarize(g) for k, g in
                                              idf.groupby(np.where(iy <= 2023, "2019-2023", "2024-2026"))}
        summary["staged_by_timing"] = {k: summarize(g) for k, g in sdf.groupby("timing")}
        summary["staged_by_attempt"] = {str(k): summarize(g) for k, g in sdf.groupby("attempt")}
        summary["staged_by_kind"] = {k: summarize(g) for k, g in sdf.groupby("surge_kind")}
        if "catalyst" in sdf and sdf["catalyst"].astype(bool).any():
            summary["staged_by_catalyst"] = {k: summarize(g) for k, g in sdf.groupby("catalyst")}
            summary["staged_by_catalyst_period"] = {f"{k[0]}|{k[1]}": summarize(g)
                                                    for k, g in sdf.groupby(["catalyst", "period"])}
    (OUT / f"summary_{args.tag}.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
    print(json.dumps({k: summary[k] for k in ("universe_tickers", "funnel", "staged", "immediate")},
                     indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
