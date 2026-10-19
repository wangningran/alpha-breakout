"""每日扫描：收盘后跑一次，输出当天的观察名单和研究日志模板。

用法：
    python scripts/daily_scan.py [--config configs/strategy_v4.yaml] [--no-update] [--equity 100000]
    python scripts/daily_scan.py --bootstrap     # 全新环境（云端定时任务）：只下载扫描需要的数据，约 20–25 分钟

步骤：
1. 准备数据：
   - 平时：增量更新全市场行情（约 10 分钟；--no-update 跳过，直接用缓存）
   - --bootstrap：建股票池、下载最近约 14 个月的行情、最近 3 年的季度基本面（不需要事先跑 build_universe 等脚本）
2. 对每支票跑一遍策略状态机到最新交易日，得到当前状态
3. 对观察名单里的公司刷新 SEC 公告（拿到最新的 8-K item、增发、13D/13G），再跑一遍
4. 输出 reports/daily/<日期>.md，并把最近 5 个交易日内的试仓信号追加到 journal/research_log.csv
   （同一支票同一信号日只记一次；决定一栏留空，由你填写）

名单分五类：
- 可试仓：今天收盘满足试仓条件，按规则明天开盘买入 1/3
- 最近 5 个交易日的信号：哪天漏跑也能补上，并显示规则下的后续状态
- 持仓管理：规则下的假想持仓，今天收盘触发了加仓 / 分批止盈 / 出场
- 新入池：今天刚出现异动
- 观察中：异动后还在观察期内，没跌回异动前价格
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
from alpha_breakout.data import BENCHMARKS, adjust, download_bulk, load_raw, update_recent  # noqa: E402
from alpha_breakout.research import make_entry_filter, trade_features  # noqa: E402
from alpha_breakout.strategy import run_ticker  # noqa: E402
from alpha_breakout.universe import listed_common_stocks, shares_history, shares_series  # noqa: E402

JOURNAL = ROOT / "journal" / "research_log.csv"
JOURNAL_COLS = ["scan_date", "ticker", "surge_date", "signal_date", "close", "rule_stop", "catalyst",
                "new_13d", "amend_13d", "new_13g", "rev_yoy", "gm_change", "runway_q",
                "decision", "reason", "notes"]
_G = {}
_EMPTY = pd.DataFrame(columns=["cik", "date", "form", "items"])
RECENT_DAYS = 5
UNIVERSE = ROOT / "data" / "cache" / "universe.csv"


def _init(cfg, sh, fil, fund):
    _G.update(cfg=cfg, sh=sh, fil=fil, fund=fund, iwm=adjust(load_raw("IWM"))["Close"])


def _state(row):
    t, cik = row
    raw = load_raw(t)
    if raw is None or len(raw) < 80:
        return None
    df = adjust(raw)
    fil = _G["fil"].get(cik, _EMPTY) if _G["fil"] is not None else None
    ftab = fmod.company_table(_G["fund"], cik) if _G["fund"] is not None else None
    st = {}
    trades = run_ticker(t, df, shares_series(_G["sh"], cik, df.index), _G["cfg"], "staged",
                        filings=fil, entry_filter=make_entry_filter(_G["cfg"], fil, ftab, df["Close"], _G["iwm"]),
                        state_out=st)
    cutoff = _G["cfg"]["_recent_cutoff"]
    recent = []
    for tr0 in trades + ([st["trade"]] if st.get("trade") is not None else []):
        if tr0.signal_date is not None and tr0.signal_date >= cutoff and all(x["signal_date"] != tr0.signal_date for x in recent):
            recent.append({"signal_date": tr0.signal_date, "surge_date": tr0.surge_date, "entry_date": tr0.entry_date,
                           "entry": tr0.entry, "stop0": tr0.stop0, "add_date": tr0.add_date,
                           "exit_date": tr0.exit_date, "exit_price": tr0.exit_price, "exit_reason": tr0.exit_reason,
                           "catalyst": tr0.catalyst,
                           "close_at_signal": float(df["Close"].loc[tr0.signal_date]),
                           "low10_at_signal": float(df["Low"].loc[:tr0.signal_date].iloc[-10:].min()),
                           **trade_features(fil, ftab, tr0.surge_date, tr0.signal_date)})
    if st.get("state") in (None, "idle") and not st.get("surge_today") and not recent:
        return None
    tr = st.get("trade")
    rec = {"ticker": t, "cik": cik, "state": st["state"], "date": st["date"], "close": st["close"],
           "pending": st["pending"], "pending_reason": st["pending_reason"], "stop": st["stop"],
           "days_since_surge": st["days_since_surge"], "surge_high": st["surge_high"],
           "vol_ratio": st["vol_ratio"], "low10": float(df["Low"].iloc[-10:].min()), "recent": recent}
    if tr is not None:
        rec.update(surge_date=tr.surge_date, surge_kind=tr.surge_kind, surge_gain=tr.surge_gain,
                   pre_surge_close=tr.pre_surge_close, catalyst=tr.catalyst, attempt=tr.attempt,
                   entry=tr.entry, entry_date=tr.entry_date, add_date=tr.add_date,
                   mcap=tr.mcap_at_surge)
        rec.update(trade_features(fil, ftab, tr.surge_date, st["date"]))
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(ROOT / "configs" / "strategy_v4.yaml"))
    ap.add_argument("--no-update", action="store_true")
    ap.add_argument("--equity", type=float, help="账户规模，用于计算建议股数；默认取配置 portfolio.initial_equity")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--bootstrap", action="store_true", help="全新环境：只下载扫描需要的数据")
    args = ap.parse_args()

    cfg = load_config(args.config)
    cfg["backtest"]["end"] = None
    sh_start = pd.Timestamp.today().year - 2   # 市值只需要最近的股本
    if args.bootstrap:
        bootstrap(sh_start)
    u = pd.read_csv(UNIVERSE)
    if not args.no_update and not args.bootstrap:
        update_recent(list(u["ticker"]) + list(BENCHMARKS))
    iwm = adjust(load_raw("IWM"))
    last_day = iwm.index[-1]
    # 只需要最近的状态：从 200 天前开始跑状态机（观察期最多 90 天）；指标用到的更早数据照常参与计算
    cfg["backtest"]["start"] = str((last_day - pd.Timedelta(days=200)).date())
    cfg["_recent_cutoff"] = iwm.index[-RECENT_DAYS]

    sh = shares_history(start_year=sh_start)
    fil = _filings_dict(fmod_f.load())
    fund = fmod.load()
    rows = list(zip(u["ticker"], u["cik"].astype(int)))

    def scan(rows_, fil_):
        with ProcessPoolExecutor(args.workers, initializer=_init, initargs=(cfg, sh, fil_, fund)) as ex:
            return [r for r in ex.map(_state, rows_, chunksize=25) if r is not None]

    recs = scan(rows, fil)
    # 观察名单里的公司刷新公告后再跑一遍，拿到最新的增发 / 8-K / 13D
    # （没有公告缓存时第一遍不做增发过滤，候选只多不少，第二遍再按公告修正）
    live = [r for r in recs if r["date"] == last_day]
    if live:
        ciks = sorted({r["cik"] for r in live})
        fil = _filings_dict(fmod_f.refresh_companies(ciks))
        recs = scan([(r["ticker"], r["cik"]) for r in live], fil)
    recs = [r for r in recs if r["date"] == last_day]
    df = pd.DataFrame(recs)
    write_report(df, cfg, last_day, fil, args.equity or (cfg.get("portfolio") or {}).get("initial_equity", 100000))


def _filings_dict(f: pd.DataFrame | None) -> dict:
    if f is None or f.empty:
        return {}
    keep = f["form"].isin(fmod_f.OFFERING_FORMS | {"S-3", "F-3"}) | f["form"].str.startswith(
        ("8-K", "SC 13D", "SCHEDULE 13D", "SC 13G", "SCHEDULE 13G"))
    return {int(k): g.reset_index(drop=True) for k, g in f[keep].groupby("cik")}


def bootstrap(sh_start: int):
    """全新环境下准备扫描所需的最少数据（已有缓存的部分会跳过）。"""
    u = listed_common_stocks()
    sh = shares_history(start_year=sh_start)
    u = u[u["cik"].isin(set(sh["cik"]))]
    start = str((pd.Timestamp.today() - pd.Timedelta(days=430)).date())
    print(f"bootstrap: {len(u)} tickers, prices since {start}")
    ok = download_bulk(list(u["ticker"]) + list(BENCHMARKS), start=start)
    u = u[u["ticker"].isin(ok)]
    UNIVERSE.parent.mkdir(parents=True, exist_ok=True)
    u.to_csv(UNIVERSE, index=False)
    if fmod.load() is None:
        fmod.fetch(start_year=sh_start - 1)


def _fmt_pct(x):
    return "—" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:+.0%}"


def _fmt_pt(x):
    return "—" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x * 100:+.1f} 点"


def _fmt_dte(x):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "—"
    return f"约 {x:.0f} 天" if x >= 0 else f"逾期 {-x:.0f} 天"


def _fmt_runway(x):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "—"
    return "盈利" if np.isinf(x) else f"{x:.1f} 季"


def _fund_rule(cfg) -> str:
    fu = (cfg.get("filters") or {}).get("fundamentals") or {}
    parts = []
    if fu.get("min_rev_yoy") is not None:
        parts.append(f"营收同比 ≥ {fu['min_rev_yoy']:.0%}")
    if fu.get("min_gm_change") is not None:
        parts.append(f"毛利率同比提升 ≥ {fu['min_gm_change'] * 100:.0f} 个点")
    if fu.get("min_runway_q") is not None:
        parts.append(f"现金跑道 ≥ {fu['min_runway_q']} 季")
    return "基本面要求：" + ("、".join(parts) if parts else "无")


def _fund_ok(r, cfg) -> str:
    fu = (cfg.get("filters") or {}).get("fundamentals") or {}
    checks = []
    if fu.get("min_rev_yoy") is not None:
        v = getattr(r, "rev_yoy", np.nan)
        checks.append(None if pd.isna(v) else v >= fu["min_rev_yoy"])
    if fu.get("min_runway_q") is not None:
        v = getattr(r, "runway_q", np.nan)
        checks.append(None if pd.isna(v) else v >= fu["min_runway_q"])
    if fu.get("min_gm_change") is not None:
        v = getattr(r, "gm_change", np.nan)
        checks.append(None if pd.isna(v) else v >= fu["min_gm_change"])
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
             f"- 规则：{cfg['surge']['one_day']['min_ret']:.0%} 单日放量或 {cfg['surge']['multi_day']['min_ret']:.0%} 五日涨幅入池；"
             f"观察 {cfg['watch']['min_days']}–{cfg['watch']['max_days']} 天；创异动以来新高试仓 1/3；{_fund_rule(cfg)}；"
             f"预计财报前 {((cfg.get('filters') or {}).get('earnings') or {}).get('avoid_before_days')} 天内不试仓",
             f"- 账户 {equity:,.0f} 美元，每笔试仓风险 {risk_amt:,.0f} 美元（1R）",
             "- 价格为复权收盘价。规则只负责找票和管风险，做不做由你研究后决定，并记在 journal/research_log.csv。", ""]

    if df.empty:
        lines.append("今天没有符合条件的票。")
    sig = df[(df["state"] == "pending_entry")] if not df.empty else df
    lines += ["## 可试仓（明天开盘按规则买入 1/3）", ""]
    if sig.empty:
        lines += ["无", ""]
    else:
        lines += ["| 代码 | 收盘 | 异动日 | 第几天 | 异动涨幅 | 规则止损 | 止损幅度 | 建议试仓股数 | 营收同比 | 毛利率变化 | 距下次财报 | 现金跑道 | 催化剂 | 13D新/改/13G | 近期公告 |",
                  "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
        for r in sig.sort_values("ticker").itertuples():
            stop = max(r.low10, r.close * (1 - p["max_stop_pct"]))
            risk = (r.close - stop) / r.close
            shares = int(risk_amt / max(r.close - stop, 1e-9))
            lines.append(
                f"| {r.ticker} | {r.close:.2f} | {pd.Timestamp(r.surge_date).date()} | {r.days_since_surge} | {r.surge_gain:+.0%} | "
                f"{stop:.2f} | {risk:.0%} | {shares} | {_fmt_pct(r.rev_yoy)} | {_fmt_pt(r.gm_change)} | {_fmt_dte(r.days_to_earnings)} | {_fmt_runway(r.runway_q)} | "
                f"{r.catalyst} | {int(r.new_13d)}/{int(r.amend_13d)}/{int(r.new_13g)} | "
                f"{_recent_filings(fil, r.cik, pd.Timestamp(r.surge_date) - pd.Timedelta(days=10))} |")
        lines.append("")
        lines.append("止损按今天收盘估算（近 10 日最低点，最多 -15%）；实际以明天开盘价重新计算。")
        lines.append("")

    rec_rows = [dict(ticker=r.ticker, cik=r.cik, **x) for r in df.itertuples() for x in (r.recent or [])] \
        if not df.empty and "recent" in df else []
    rec_df = pd.DataFrame(rec_rows)
    lines += [f"## 最近 {RECENT_DAYS} 个交易日的试仓信号（{len(rec_df)}）", ""]
    if rec_df.empty:
        lines += ["无", ""]
    else:
        lines += ["| 代码 | 信号日 | 异动日 | 信号日收盘 | 入场日 | 入场价 | 初始止损 | 规则下的状态 | 营收同比 | 毛利率变化 |",
                  "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
        for r in rec_df.sort_values(["signal_date", "ticker"], ascending=[False, True]).itertuples():
            if pd.isna(r.entry_date):
                status = "明天开盘试仓"
            elif pd.notna(r.exit_date) and r.exit_reason != "open":
                status = f"已出局 {pd.Timestamp(r.exit_date).date()} @ {r.exit_price:.2f}（{r.exit_reason}）"
            elif pd.notna(r.add_date):
                status = "已加仓，持有中"
            else:
                status = "试仓中"
            entry_d = "—" if pd.isna(r.entry_date) else str(pd.Timestamp(r.entry_date).date())
            entry_p = "—" if pd.isna(r.entry) else f"{r.entry:.2f}"
            stop_p = "—" if pd.isna(r.stop0) else f"{r.stop0:.2f}"
            lines.append(f"| {r.ticker} | {pd.Timestamp(r.signal_date).date()} | {pd.Timestamp(r.surge_date).date()} | "
                         f"{r.close_at_signal:.2f} | {entry_d} | {entry_p} | {stop_p} | {status} | "
                         f"{_fmt_pct(r.rev_yoy)} | {_fmt_pt(r.gm_change)} |")
        lines.append("")
        _append_journal(rec_df, day, p)

    pos = df[df["state"].isin(["probe", "full"])] if not df.empty else df
    lines += ["## 持仓管理（规则下的假想持仓）", ""]
    if pos.empty:
        lines += ["无", ""]
    else:
        lines += ["| 代码 | 状态 | 入场日 | 入场价 | 收盘 | 止损 | 明天动作 |", "| --- | --- | --- | --- | --- | --- | --- |"]
        act_name = {"add": "加仓到满仓", "partial": "分批止盈", "exit": "清仓", None: "持有"}
        for r in pos.sort_values("ticker").itertuples():
            pend = None if pd.isna(r.pending) else r.pending  # DataFrame 里 None 会变成 NaN
            reason = "" if pd.isna(r.pending_reason) else r.pending_reason
            act = act_name.get(pend, pend) + (f"（{reason}）" if reason else "")
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
        lines.insert(len(lines) - 1, f"基本面达标（{_fund_rule(cfg)[6:]}）的有 {n_ok} 支，只有这些创新高时才会出现在“可试仓”里。")
        lines += ["| 代码 | 收盘 | 异动日 | 第几天 | 距最高 | 距作废线 | 基本面 | 营收同比 | 毛利率变化 | 距下次财报 | 现金跑道 | 催化剂 | 13D新/改/13G |",
                  "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
        watch = watch.assign(fund_ok=[_fund_ok(r, cfg) for r in watch.itertuples()])
        watch = watch.sort_values(["fund_ok", "dist"], ascending=[False, False], key=lambda c: c.map({"✓": 2, "缺数据": 1, "✗": 0}) if c.name == "fund_ok" else c)
        for r in watch.itertuples():
            lines.append(f"| {r.ticker} | {r.close:.2f} | {pd.Timestamp(r.surge_date).date()} | {r.days_since_surge} | "
                         f"{r.dist:+.0%} | {r.close / r.pre_surge_close - 1:+.0%} | {r.fund_ok} | {_fmt_pct(r.rev_yoy)} | "
                         f"{_fmt_pt(r.gm_change)} | {_fmt_dte(r.days_to_earnings)} | {_fmt_runway(r.runway_q)} | {r.catalyst} | {int(r.new_13d)}/{int(r.amend_13d)}/{int(r.new_13g)} |")
        lines.append("")
    path = out_dir / f"{day.date()}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    df.drop(columns=["recent"], errors="ignore").to_csv(out_dir / f"{day.date()}.csv", index=False)
    print(f"saved {path}  (可试仓 {len(sig)}, 近 {RECENT_DAYS} 日信号 {len(rec_df)}, 持仓 {len(pos)}, "
          f"新入池 {len(new)}, 观察中 {len(watch)})")


def _append_journal(rec_df: pd.DataFrame, day: pd.Timestamp, p: dict):
    """最近的试仓信号追加进研究日志；同一支票同一信号日只记一次。"""
    JOURNAL.parent.mkdir(parents=True, exist_ok=True)
    old = pd.read_csv(JOURNAL) if JOURNAL.exists() else pd.DataFrame(columns=JOURNAL_COLS)
    seen = set(zip(old["ticker"].astype(str), old["signal_date"].astype(str)))
    rows = []
    for r in rec_df.itertuples():
        sd = str(pd.Timestamp(r.signal_date).date())
        if (r.ticker, sd) in seen:
            continue
        rows.append({"scan_date": str(day.date()), "ticker": r.ticker, "surge_date": str(pd.Timestamp(r.surge_date).date()),
                     "signal_date": sd, "close": round(r.close_at_signal, 4),
                     "rule_stop": round(max(r.low10_at_signal, r.close_at_signal * (1 - p["max_stop_pct"])), 4),
                     "catalyst": r.catalyst, "new_13d": r.new_13d, "amend_13d": r.amend_13d, "new_13g": r.new_13g,
                     "rev_yoy": r.rev_yoy, "gm_change": r.gm_change, "runway_q": r.runway_q,
                     "decision": "", "reason": "", "notes": ""})
    if rows:
        pd.concat([old, pd.DataFrame(rows)], ignore_index=True)[JOURNAL_COLS].to_csv(JOURNAL, index=False)


if __name__ == "__main__":
    main()
