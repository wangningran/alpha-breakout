"""每日扫描：收盘后跑一次，输出当天的观察名单和研究日志模板。

用法：
    python scripts/daily_scan.py [--config configs/strategy_v3.yaml] [--no-update] [--equity 100000]

步骤：
1. 增量更新全市场行情（约 10 分钟；--no-update 跳过，直接用缓存）
2. 对每支票跑一遍策略状态机到最新交易日，得到当前状态
3. 对观察名单里的公司刷新 SEC 公告（拿到最新的 8-K item、增发、13D/13G）
4. 输出 reports/daily/<日期>.md，并把"可试仓"的票追加到 journal/research_log.csv（决定一栏留空，由你填写）

名单分四类：
- 新入池：今天刚出现异动
- 观察中：异动后还在观察期内，没跌回异动前价格
- 可试仓：今天收盘满足试仓条件，按规则明天开盘买入 1/3
- 持仓管理：规则下的假想持仓，今天收盘触发了加仓 / 分批止盈 / 出场
"""
from __future__ import annotations

import argparse
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from alpha_breakout import filings as fmod_f  # noqa: E402
from alpha_breakout import fundamentals as fmod  # noqa: E402
from alpha_breakout.config import load_config  # noqa: E402
from alpha_breakout.data import BENCHMARKS, adjust, load_raw, update_recent  # noqa: E402
from alpha_breakout.research import make_entry_filter, trade_features  # noqa: E402
from alpha_breakout.strategy import run_ticker  # noqa: E402
from alpha_breakout.universe import shares_history, shares_series  # noqa: E402

JOURNAL = ROOT / "journal" / "research_log.csv"
JOURNAL_COLS = ["scan_date", "ticker", "surge_date", "signal_date", "close", "rule_stop", "catalyst",
                "new_13d", "amend_13d", "new_13g", "rev_yoy", "runway_q",
                "decision", "reason", "notes"]
_G = {}
_EMPTY = pd.DataFrame(columns=["cik", "date", "form", "items"])


def _init(cfg, sh, fil, fund):
    _G.update(cfg=cfg, sh=sh, fil=fil, fund=fund)


def _state(row):
    t, cik = row
    raw = load_raw(t)
    if raw is None or len(raw) < 80:
        return None
    df = adjust(raw)
    fil = _G["fil"].get(cik, _EMPTY) if _G["fil"] is not None else None
    ftab = fmod.company_table(_G["fund"], cik) if _G["fund"] is not None else None
    st = {}
    run_ticker(t, df, shares_series(_G["sh"], cik, df.index), _G["cfg"], "staged",
               filings=fil, entry_filter=make_entry_filter(_G["cfg"], fil, ftab), state_out=st)
    if st.get("state") in (None, "idle") and not st.get("surge_today"):
        return None
    tr = st.get("trade")
    rec = {"ticker": t, "cik": cik, "state": st["state"], "date": st["date"], "close": st["close"],
           "pending": st["pending"], "pending_reason": st["pending_reason"], "stop": st["stop"],
           "days_since_surge": st["days_since_surge"], "surge_high": st["surge_high"],
           "vol_ratio": st["vol_ratio"], "low10": float(df["Low"].iloc[-10:].min())}
    if tr is not None:
        rec.update(surge_date=tr.surge_date, surge_kind=tr.surge_kind, surge_gain=tr.surge_gain,
                   pre_surge_close=tr.pre_surge_close, catalyst=tr.catalyst, attempt=tr.attempt,
                   entry=tr.entry, entry_date=tr.entry_date, add_date=tr.add_date,
                   mcap=tr.mcap_at_surge)
        rec.update(trade_features(fil, ftab, tr.surge_date, st["date"]))
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(ROOT / "configs" / "strategy_v3.yaml"))
    ap.add_argument("--no-update", action="store_true")
    ap.add_argument("--equity", type=float, help="账户规模，用于计算建议股数；默认取配置 portfolio.initial_equity")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    cfg = load_config(args.config)
    cfg["backtest"]["end"] = None
    # 只需要最近的状态：从一年前开始跑就够（观察期最多 90 天，指标最多需要 252 天）
    u = pd.read_csv(ROOT / "data" / "cache" / "universe.csv")
    if not args.no_update:
        update_recent(list(u["ticker"]) + list(BENCHMARKS))
    last_day = adjust(load_raw("IWM")).index[-1]
    cfg["backtest"]["start"] = str((last_day - pd.Timedelta(days=200)).date())

    sh = shares_history()
    f = fmod_f.load()
    keep = f["form"].isin(fmod_f.OFFERING_FORMS | {"S-3", "F-3"}) | f["form"].str.startswith(
        ("8-K", "SC 13D", "SCHEDULE 13D", "SC 13G", "SCHEDULE 13G"))
    fil = {int(k): g.reset_index(drop=True) for k, g in f[keep].groupby("cik")}
    fund = fmod.load()
    rows = list(zip(u["ticker"], u["cik"].astype(int)))

    def scan(rows_, fil_):
        with ProcessPoolExecutor(args.workers, initializer=_init, initargs=(cfg, sh, fil_, fund)) as ex:
            return [r for r in ex.map(_state, rows_, chunksize=25) if r is not None]

    recs = scan(rows, fil)
    # 观察名单里的公司刷新公告后再跑一遍，拿到最新的增发 / 8-K / 13D
    live = [r for r in recs if r["date"] == last_day]
    if live:
        ciks = sorted({r["cik"] for r in live})
        f = fmod_f.refresh_companies(ciks)
        keep = f["form"].isin(fmod_f.OFFERING_FORMS | {"S-3", "F-3"}) | f["form"].str.startswith(
            ("8-K", "SC 13D", "SCHEDULE 13D", "SC 13G", "SCHEDULE 13G"))
        fil = {int(k): g.reset_index(drop=True) for k, g in f[keep].groupby("cik")}
        recs = scan([(r["ticker"], r["cik"]) for r in live], fil)
    recs = [r for r in recs if r["date"] == last_day]
    df = pd.DataFrame(recs)
    write_report(df, cfg, last_day, fil, args.equity or (cfg.get("portfolio") or {}).get("initial_equity", 100000))


def _fmt_pct(x):
    return "—" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:+.0%}"


def _fmt_runway(x):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "—"
    return "盈利" if np.isinf(x) else f"{x:.1f} 季"


def _fund_ok(r, cfg) -> str:
    fu = (cfg.get("filters") or {}).get("fundamentals") or {}
    checks = []
    if fu.get("min_rev_yoy") is not None:
        v = getattr(r, "rev_yoy", np.nan)
        checks.append(None if pd.isna(v) else v >= fu["min_rev_yoy"])
    if fu.get("min_runway_q") is not None:
        v = getattr(r, "runway_q", np.nan)
        checks.append(None if pd.isna(v) else v >= fu["min_runway_q"])
    if not checks:
        return "—"
    if any(c is False for c in checks):
        return "✗"
    if any(c is None for c in checks):
        return "缺数据" if not fu.get("allow_missing", True) else "✓"
    return "✓"


def _recent_filings(fil, cik, since, n=6) -> str:
    g = fil.get(int(cik))
    if g is None or g.empty:
        return "—"
    g = g[g["date"] >= pd.Timestamp(since)].tail(n)
    if g.empty:
        return "—"
    return "; ".join(f"{d.date()} {fm}{'(' + it + ')' if it else ''}" for d, fm, it in zip(g["date"], g["form"], g["items"]))


def write_report(df: pd.DataFrame, cfg: dict, day: pd.Timestamp, fil: dict, equity: float):
    out_dir = ROOT / "reports" / "daily"
    out_dir.mkdir(parents=True, exist_ok=True)
    p = cfg["position"]
    pcfg = cfg.get("portfolio") or {}
    risk_amt = equity * pcfg.get("risk_per_trade", 0.005)
    lines = [f"# 每日扫描 {day.date()}", "",
             f"- 配置：v3（{cfg['surge']['one_day']['min_ret']:.0%} 单日放量或 {cfg['surge']['multi_day']['min_ret']:.0%} 五日涨幅入池；"
             f"观察 {cfg['watch']['min_days']}–{cfg['watch']['max_days']} 天；创异动以来新高试仓 1/3）",
             f"- 账户 {equity:,.0f} 美元，每笔试仓风险 {risk_amt:,.0f} 美元（1R）",
             "- 价格为复权收盘价。规则只负责找票和管风险，做不做由你研究后决定，并记在 journal/research_log.csv。", ""]

    if df.empty:
        lines.append("今天没有符合条件的票。")
    sig = df[(df["state"] == "pending_entry")] if not df.empty else df
    lines += ["## 可试仓（明天开盘按规则买入 1/3）", ""]
    if sig.empty:
        lines += ["无", ""]
    else:
        lines += ["| 代码 | 收盘 | 异动日 | 第几天 | 异动涨幅 | 规则止损 | 止损幅度 | 建议试仓股数 | 催化剂 | 13D新/改/13G | 营收同比 | 现金跑道 | 近期公告 |",
                  "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
        for r in sig.sort_values("ticker").itertuples():
            stop = max(r.low10, r.close * (1 - p["max_stop_pct"]))
            risk = (r.close - stop) / r.close
            shares = int(risk_amt / max(r.close - stop, 1e-9))
            lines.append(
                f"| {r.ticker} | {r.close:.2f} | {pd.Timestamp(r.surge_date).date()} | {r.days_since_surge} | {r.surge_gain:+.0%} | "
                f"{stop:.2f} | {risk:.0%} | {shares} | {r.catalyst} | {int(r.new_13d)}/{int(r.amend_13d)}/{int(r.new_13g)} | "
                f"{_fmt_pct(r.rev_yoy)} | {_fmt_runway(r.runway_q)} | {_recent_filings(fil, r.cik, pd.Timestamp(r.surge_date) - pd.Timedelta(days=10))} |")
        lines.append("")
        lines.append("止损按今天收盘估算（近 10 日最低点，最多 -15%）；实际以明天开盘价重新计算。")
        lines.append("")
        _append_journal(sig, day, p)

    pos = df[df["state"].isin(["probe", "full"])] if not df.empty else df
    lines += ["## 持仓管理（规则下的假想持仓）", ""]
    if pos.empty:
        lines += ["无", ""]
    else:
        lines += ["| 代码 | 状态 | 入场日 | 入场价 | 收盘 | 止损 | 明天动作 |", "| --- | --- | --- | --- | --- | --- | --- |"]
        act_name = {"add": "加仓到满仓", "partial": "分批止盈", "exit": "清仓", None: "持有"}
        for r in pos.sort_values("ticker").itertuples():
            act = act_name.get(r.pending, r.pending) + (f"（{r.pending_reason}）" if r.pending_reason else "")
            lines.append(f"| {r.ticker} | {'试仓' if r.state == 'probe' else '满仓'} | {pd.Timestamp(r.entry_date).date()} | "
                         f"{r.entry:.2f} | {r.close:.2f} | {r.stop:.2f} | {act} |")
        lines.append("")

    new = df[(df["state"] == "watch") & (df["days_since_surge"] == 0)] if not df.empty else df
    lines += [f"## 新入池（{len(new)}）", ""]
    if new.empty:
        lines += ["无", ""]
    else:
        lines += ["| 代码 | 收盘 | 异动类型 | 涨幅 | 量比 | 市值(亿美元) | 催化剂 | 近期公告 |", "| --- | --- | --- | --- | --- | --- | --- | --- |"]
        for r in new.sort_values("surge_gain", ascending=False).itertuples():
            lines.append(f"| {r.ticker} | {r.close:.2f} | {r.surge_kind} | {r.surge_gain:+.0%} | {r.vol_ratio:.1f} | "
                         f"{r.mcap / 1e8:.1f} | {r.catalyst} | {_recent_filings(fil, r.cik, day - pd.Timedelta(days=10), 4)} |")
        lines.append("")

    watch = df[(df["state"] == "watch") & (df["days_since_surge"] > 0)] if not df.empty else df
    lines += [f"## 观察中（{len(watch)}）", "", "按距异动以来最高收盘的距离排序，越接近 0% 越接近试仓条件。", ""]
    if not watch.empty:
        watch = watch.assign(dist=watch["close"] / watch["surge_high"] - 1).sort_values("dist", ascending=False)
        n_ok = sum(_fund_ok(r, cfg) == "✓" for r in watch.itertuples())
        lines.insert(len(lines) - 1, f"基本面达标（营收同比、现金跑道）的有 {n_ok} 支，只有这些创新高时才会出现在“可试仓”里。")
        lines += ["| 代码 | 收盘 | 异动日 | 第几天 | 距最高 | 距作废线 | 基本面 | 营收同比 | 现金跑道 | 催化剂 | 13D新/改/13G |",
                  "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
        watch = watch.assign(fund_ok=[_fund_ok(r, cfg) for r in watch.itertuples()])
        watch = watch.sort_values(["fund_ok", "dist"], ascending=[False, False], key=lambda c: c.map({"✓": 2, "缺数据": 1, "✗": 0}) if c.name == "fund_ok" else c)
        for r in watch.itertuples():
            lines.append(f"| {r.ticker} | {r.close:.2f} | {pd.Timestamp(r.surge_date).date()} | {r.days_since_surge} | "
                         f"{r.dist:+.0%} | {r.close / r.pre_surge_close - 1:+.0%} | {r.fund_ok} | {_fmt_pct(r.rev_yoy)} | "
                         f"{_fmt_runway(r.runway_q)} | {r.catalyst} | {int(r.new_13d)}/{int(r.amend_13d)}/{int(r.new_13g)} |")
        lines.append("")
    path = out_dir / f"{day.date()}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    df.to_csv(out_dir / f"{day.date()}.csv", index=False)
    print(f"saved {path}  (可试仓 {len(sig)}, 持仓 {len(pos)}, 新入池 {len(new)}, 观察中 {len(watch)})")


def _append_journal(sig: pd.DataFrame, day: pd.Timestamp, p: dict):
    JOURNAL.parent.mkdir(parents=True, exist_ok=True)
    old = pd.read_csv(JOURNAL) if JOURNAL.exists() else pd.DataFrame(columns=JOURNAL_COLS)
    rows = []
    for r in sig.itertuples():
        key = (str(day.date()), r.ticker)
        if ((old["scan_date"].astype(str) == key[0]) & (old["ticker"] == key[1])).any():
            continue
        rows.append({"scan_date": key[0], "ticker": r.ticker, "surge_date": str(pd.Timestamp(r.surge_date).date()),
                     "signal_date": key[0], "close": round(r.close, 4),
                     "rule_stop": round(max(r.low10, r.close * (1 - p["max_stop_pct"])), 4),
                     "catalyst": r.catalyst, "new_13d": r.new_13d, "amend_13d": r.amend_13d, "new_13g": r.new_13g,
                     "rev_yoy": r.rev_yoy, "runway_q": r.runway_q, "decision": "", "reason": "", "notes": ""})
    if rows:
        pd.concat([old, pd.DataFrame(rows)], ignore_index=True)[JOURNAL_COLS].to_csv(JOURNAL, index=False)


if __name__ == "__main__":
    main()
