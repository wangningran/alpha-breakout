"""策略 v1：异动 → 观察 → 试仓 → 加仓（逐支票的事件回测）。

状态机（每支票独立，收盘判断、次日开盘执行）：
  idle  --异动-->  watch  --观察期满 + 创异动以来新高-->  probe(1/3)  --浮盈 1R-->  full
  watch：收盘跌回异动前价格 → 作废；超过 max_days → 过期
  probe：触及止损 → 出局；probe_max_days 内没到 1R → 出局（验证失败）
         出局后如果异动还没作废 / 过期，回到 watch 等下一次新高（最多 max_attempts 次）
  full ：触及止损（加仓后上移到试仓成本）或收盘跌破 20 日线 → 全部出场

mode=immediate 是对照组：异动次日开盘直接满仓，止损 / 跟踪规则相同，没有观察和试仓。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass
class Trade:
    ticker: str
    surge_date: pd.Timestamp
    surge_kind: str
    pre_surge_close: float
    attempt: int
    signal_date: pd.Timestamp | None = None
    entry_date: pd.Timestamp | None = None
    entry: float = np.nan
    stop0: float = np.nan
    add_date: pd.Timestamp | None = None
    add_price: float = np.nan
    exit_date: pd.Timestamp | None = None
    exit_price: float = np.nan
    exit_reason: str = ""
    days_surge_to_signal: int = 0
    pullback_before_signal: float = 0.0     # 入场前距异动以来最高收盘的最大回撤
    surge_gain: float = 0.0
    mcap_at_surge: float = np.nan
    extra: dict = field(default_factory=dict)


def indicators(df: pd.DataFrame, shares: pd.Series, cfg: dict) -> pd.DataFrame:
    u, s, e = cfg["universe"], cfg["surge"], cfg["entry"]
    out = pd.DataFrame(index=df.index)
    c = df["Close"]
    out["o"], out["h"], out["l"], out["c"] = df["Open"], df["High"], df["Low"], c
    vol = df["Volume"]
    out["vol_ratio"] = vol / vol.rolling(s["vol_avg_window"], min_periods=20).mean().shift(1)
    out["ret1"] = c.pct_change()
    w = s["multi_day"]["window"]
    out["retN"] = c / c.shift(w) - 1
    ma = e.get("require_above_ma") or cfg["position"]["trail_ma"]
    out["ma"] = c.rolling(ma).mean()
    out["ma_trail"] = c.rolling(cfg["position"]["trail_ma"]).mean()
    out["ma_rising"] = out["ma"] > out["ma"].shift(5)
    out["low_n"] = df["Low"].rolling(cfg["position"]["stop_lookback"]).min()
    out["dollar_vol"] = (c * vol).rolling(u["dollar_volume_window"]).mean()
    out["raw_close"] = df["RawClose"]
    out["mcap"] = df["RawClose"] * shares.reindex(df.index)
    one = (out["ret1"] >= s["one_day"]["min_ret"]) & (out["vol_ratio"] >= s["one_day"]["min_vol_ratio"])
    multi = out["retN"] >= s["multi_day"]["min_ret"]
    out["surge_kind"] = np.where(one & multi, "both", np.where(one, "one_day", np.where(multi, "multi_day", "")))
    out["pre_close"] = np.where(multi, c.shift(w), c.shift(1))
    out["eligible"] = (
        (out["raw_close"] >= u["min_price"])
        & (out["mcap"] >= u["min_mcap"]) & (out["mcap"] <= u["max_mcap"])
        & (out["dollar_vol"] >= u["min_dollar_volume"])
    )
    return out


def run_ticker(ticker: str, df: pd.DataFrame, shares: pd.Series, cfg: dict,
               mode: str = "staged", stats: dict | None = None,
               market_ok: pd.Series | None = None) -> list[Trade]:
    """stats 传入 dict 时，会累加漏斗计数：surges（入池次数）、invalidated、expired。"""
    stats = stats if stats is not None else {}
    ind = indicators(df, shares, cfg)
    bt = cfg["backtest"]
    start = pd.Timestamp(bt["start"])
    end = pd.Timestamp(bt["end"]) if bt.get("end") else ind.index[-1]
    w, e, p = cfg["watch"], cfg["entry"], cfg["position"]
    slip = p["slippage"]
    max_attempts = w.get("max_attempts", 2)

    idx = ind.index
    o, h, l, c = (ind[k].to_numpy() for k in ("o", "h", "l", "c"))
    vr, ma, ma_tr = ind["vol_ratio"].to_numpy(), ind["ma"].to_numpy(), ind["ma_trail"].to_numpy()
    ma_up, low_n = ind["ma_rising"].to_numpy(), ind["low_n"].to_numpy()
    kind, pre, elig = ind["surge_kind"].to_numpy(), ind["pre_close"].to_numpy(), ind["eligible"].to_numpy()
    mcap = ind["mcap"].to_numpy()
    mkt = (market_ok.reindex(idx).ffill().fillna(False).to_numpy(dtype=bool)
           if market_ok is not None else np.ones(len(idx), dtype=bool))

    trades: list[Trade] = []
    state = "idle"
    tr: Trade | None = None
    s_i = 0
    hi = 0.0
    max_dd = 0.0
    attempt = 0
    pending = None  # ("probe"/"add"/"exit", reason) 次日开盘执行
    n = len(idx)

    def new_trade(i):
        return Trade(ticker, idx[i], kind[i], float(pre[i]), attempt,
                     surge_gain=float(c[i] / pre[i] - 1), mcap_at_surge=float(mcap[i]))

    for i in range(n):
        if idx[i] > end:
            break
        # ---- 先执行昨天收盘定下的动作（开盘价成交）----
        if pending is not None:
            act, why = pending
            pending = None
            if act == "probe":
                tr.entry_date, tr.entry = idx[i], o[i]
                tr.stop0 = max(low_n[i - 1] if not np.isnan(low_n[i - 1]) else 0.0,
                               o[i] * (1 - p["max_stop_pct"]))
                if tr.stop0 >= o[i]:  # 跳空低开到止损下方
                    tr.stop0 = o[i] * (1 - p["max_stop_pct"])
                stop = tr.stop0
                state = "probe" if mode == "staged" else "full"
                if mode == "immediate":
                    tr.add_date, tr.add_price = idx[i], np.nan
            elif act == "add":
                tr.add_date, tr.add_price = idx[i], o[i]
                if p["breakeven_after_add"]:
                    stop = max(stop, tr.entry)
                state = "full"
            elif act == "exit":
                tr.exit_date, tr.exit_price, tr.exit_reason = idx[i], o[i], why
                trades.append(tr)
                tr = None
                state = _after_exit(why, i, s_i, w, attempt, max_attempts, mode)
                if state == "watch":
                    attempt += 1
                    tr = _clone_watch(trades[-1], attempt)

        # ---- 持仓中：止损（盘中）----
        if state in ("probe", "full") and tr.entry_date is not None and l[i] <= stop:
            tr.exit_date, tr.exit_price, tr.exit_reason = idx[i], min(o[i], stop), "stop"
            trades.append(tr)
            tr = None
            state = _after_exit("stop", i, s_i, w, attempt, max_attempts, mode)
            if state == "watch":
                attempt += 1
                tr = _clone_watch(trades[-1], attempt)
            # 当天不再做别的判断
            continue

        if i + 1 >= n:  # 最后一天，没有“次日开盘”
            continue

        # ---- 收盘判断 ----
        if state == "idle":
            if idx[i] >= start and kind[i] and elig[i]:
                stats["surges"] = stats.get("surges", 0) + 1
                attempt = 1
                tr = new_trade(i)
                s_i, hi, max_dd = i, c[i], 0.0
                if mode == "immediate" and mkt[i]:
                    tr.signal_date = idx[i]
                    pending = ("probe", "")
                    state = "pending_entry"
                elif mode == "immediate":
                    state, tr = "idle", None
                else:
                    state = "watch"
        elif state == "watch":
            days = i - s_i
            if c[i] < tr.pre_surge_close * (1 - w["invalidate_buffer"]):
                stats["invalidated"] = stats.get("invalidated", 0) + (attempt == 1)
                state, tr = "idle", None
                continue
            if days > w["max_days"]:
                stats["expired"] = stats.get("expired", 0) + (attempt == 1)
                state, tr = "idle", None
                continue
            new_high = c[i] > hi * (1 - e["near_high_pct"]) if days > 0 else False
            ok = (
                days >= w["min_days"] and new_high and vr[i] >= e["min_vol_ratio"]
                and (not e.get("require_above_ma") or c[i] > ma[i])
                and (not e.get("require_ma_rising") or ma_up[i])
                and mkt[i]
            )
            hi = max(hi, c[i])
            max_dd = min(max_dd, c[i] / hi - 1)
            if ok:
                tr.signal_date = idx[i]
                tr.days_surge_to_signal = days
                tr.pullback_before_signal = float(max_dd)
                pending = ("probe", "")
                state = "pending_entry"
        elif state == "probe":
            r = tr.entry - tr.stop0
            held = i - idx.get_loc(tr.entry_date)
            if c[i] >= tr.entry + p["add_trigger_r"] * r:
                pending = ("add", "")
            elif held >= p["probe_max_days"]:
                pending = ("exit", "probe_timeout")
        elif state == "full":
            held = i - idx.get_loc(tr.entry_date)
            if c[i] < ma_tr[i]:
                pending = ("exit", "trail_ma")
            elif held >= p["max_hold_days"]:
                pending = ("exit", "max_hold")
        if state == "watch" and tr is not None:
            hi = max(hi, c[i])

    if tr is not None and tr.entry_date is not None and state in ("probe", "full"):
        j = min(i, n - 1) if idx[min(i, n - 1)] <= end else i - 1
        tr.exit_date, tr.exit_price, tr.exit_reason = idx[j], c[j], "open"
        trades.append(tr)
    return trades


def market_filter(bench: pd.DataFrame, cfg: dict) -> pd.Series | None:
    """大盘过滤：基准收盘在 N 日均线上方才允许开新仓。cfg["market"]["enabled"]=false 时返回 None。"""
    m = cfg.get("market") or {}
    if not m.get("enabled"):
        return None
    c = bench["Close"]
    return c > c.rolling(m.get("ma", 200)).mean()


def _after_exit(why, i, s_i, w, attempt, max_attempts, mode):
    if mode == "staged" and why in ("stop", "probe_timeout") and attempt < max_attempts \
            and i - s_i <= w["max_days"]:
        return "watch"
    return "idle"


def _clone_watch(prev: Trade, attempt: int) -> Trade:
    return Trade(prev.ticker, prev.surge_date, prev.surge_kind, prev.pre_surge_close, attempt,
                 surge_gain=prev.surge_gain, mcap_at_surge=prev.mcap_at_surge)


def trade_pnl(t: Trade, cfg: dict, mode: str = "staged") -> dict:
    """收益按“目标满仓 = 1”计。r_multiple 以试仓承担的风险为 1R。"""
    p = cfg["position"]
    s = p["slippage"]
    x = t.exit_price * (1 - s)
    if mode == "immediate":
        sizes = [(1.0, t.entry)]
    else:
        sizes = [(p["probe_size"], t.entry)]
        if not np.isnan(t.add_price):
            sizes.append((p["add_size"], t.add_price))
    pnl = sum(sz * (x / (px * (1 + s)) - 1) for sz, px in sizes)
    risk_pct = (t.entry - t.stop0) / t.entry
    base = (1.0 if mode == "immediate" else p["probe_size"]) * risk_pct
    return {
        "pnl": pnl,
        "risk_pct": risk_pct,
        "r_multiple": pnl / base if base > 0 else np.nan,
        "added": not np.isnan(t.add_price) or mode == "immediate",
        "hold_days": int(np.busday_count(t.entry_date.date(), t.exit_date.date())),
    }


def trades_frame(trades: list[Trade], cfg: dict, mode: str = "staged") -> pd.DataFrame:
    rows = []
    for t in trades:
        d = {k: v for k, v in t.__dict__.items() if k != "extra"}
        d.update(trade_pnl(t, cfg, mode))
        rows.append(d)
    return pd.DataFrame(rows)
