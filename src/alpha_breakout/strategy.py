"""Strategy: surge -> watch -> probe -> add (per-ticker event backtest, also used by the daily scan).

State machine (independent per ticker; decided at the close, executed at the next open):
  idle  --surge-->  watch  --watch period over + new high since the surge-->  probe(1/3)  --open profit 1R-->  full
  watch: close falls back to the pre-surge price -> invalidated; beyond max_days -> expired
  probe: stop hit -> exit; no 1R within probe_max_days -> exit (failed confirmation)
         after an exit, if the surge is not yet invalidated / expired, return to watch for the next new high (up to max_attempts)
  full : stop hit, trailing exit (MA / ATR), or held for max_hold_days -> exit everything
         optional partial take-profit: once the close is up partial_r x R, sell partial_frac at the next open and trail the rest more loosely

mode=immediate is the control group: full position at the open after the surge, same stop / trailing rules, no watch or probe.

Optional modules from v2 on (each toggled in the config):
  clean   : data cleaning; requires liquidity before the surge, enough listing history, no stale quotes
  offering: offering filter; an offering filing from lookback_days before the surge through the surge day -> not admitted;
            during_watch / exit_on_offering control the handling during the watch period and while holding
  catalyst: tags each trade with the category of filings around the surge; categories can be excluded
  entry_filter: external pre-probe check (13D/13G, fundamentals, etc.); returning False skips this signal

Each trade records its fills in legs: (date, qty, price), with qty measured so that "target full position = 1 unit of capital",
positive for buys, negative for sells. The portfolio simulation scales trades to real capital using legs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

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
    partial_date: pd.Timestamp | None = None
    exit_date: pd.Timestamp | None = None
    exit_price: float = np.nan
    exit_reason: str = ""
    days_surge_to_signal: int = 0
    pullback_before_signal: float = 0.0     # max drawdown from the highest close since the surge, before entry
    surge_gain: float = 0.0
    mcap_at_surge: float = np.nan
    catalyst: str = ""
    legs: list = field(default_factory=list)


def indicators(df: pd.DataFrame, shares: pd.Series, cfg: dict) -> pd.DataFrame:
    u, s, e, p = cfg["universe"], cfg["surge"], cfg["entry"], cfg["position"]
    out = pd.DataFrame(index=df.index)
    c = df["Close"]
    out["o"], out["h"], out["l"], out["c"] = df["Open"], df["High"], df["Low"], c
    vol = df["Volume"]
    out["vol_ratio"] = vol / vol.rolling(s["vol_avg_window"], min_periods=20).mean().shift(1)
    out["ret1"] = c.pct_change()
    w = s["multi_day"]["window"]
    out["retN"] = c / c.shift(w) - 1
    ma = e.get("require_above_ma") or p["trail_ma"]
    out["ma"] = c.rolling(ma).mean()
    out["ma_trail"] = c.rolling(p["trail_ma"]).mean()
    apt = p.get("after_partial_trail_ma") or p["trail_ma"]
    out["ma_trail2"] = c.rolling(apt).mean()
    out["ma_rising"] = out["ma"] > out["ma"].shift(5)
    out["low_n"] = df["Low"].rolling(p["stop_lookback"]).min()
    tr = pd.concat([df["High"] - df["Low"], (df["High"] - c.shift()).abs(), (df["Low"] - c.shift()).abs()], axis=1).max(axis=1)
    out["atr"] = tr.rolling(p.get("atr_window", 14)).mean()
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
    cl = cfg.get("clean") or {}
    if cl.get("enabled"):
        pre_dv = (c * vol).rolling(20).mean().shift(1)
        zero = (vol <= 0).astype(float).rolling(20).sum().shift(1)
        out["eligible"] &= (
            (pd.Series(np.arange(len(df)), index=df.index) >= cl["min_history_days"])
            & (pre_dv >= cl["pre_surge_dollar_volume"])
            & (zero <= cl["max_zero_volume_days"])
        )
    return out


def run_ticker(ticker: str, df: pd.DataFrame, shares: pd.Series, cfg: dict,
               mode: str = "staged", stats: dict | None = None,
               market_ok: pd.Series | None = None,
               filings: pd.DataFrame | None = None,
               entry_filter: Callable[[pd.Timestamp, "Trade"], bool] | None = None,
               state_out: dict | None = None) -> list[Trade]:
    """Return completed trades (and those still open at period end).

    stats: accumulates funnel counts (surges / invalidated / expired / diluted / catalyst_excluded / filtered).
    state_out: if a dict is passed, the state after the last day's close is written into it for the daily scan.
    """
    from .filings import catalyst_label

    stats = stats if stats is not None else {}
    ind = indicators(df, shares, cfg)
    bt = cfg["backtest"]
    start = pd.Timestamp(bt["start"])
    end = pd.Timestamp(bt["end"]) if bt.get("end") else ind.index[-1]
    w, e, p = cfg["watch"], cfg["entry"], cfg["position"]
    max_attempts = w.get("max_attempts", 2)
    exit_mode = p.get("exit_mode", "ma")
    atr_mult = p.get("atr_mult", 3.0)
    partial_r = p.get("partial_r")
    partial_frac = p.get("partial_frac", 0.5)

    idx = ind.index
    o, h, l, c = (ind[k].to_numpy() for k in ("o", "h", "l", "c"))
    vr, ma = ind["vol_ratio"].to_numpy(), ind["ma"].to_numpy()
    ma_tr, ma_tr2, atr = ind["ma_trail"].to_numpy(), ind["ma_trail2"].to_numpy(), ind["atr"].to_numpy()
    ma_up, low_n = ind["ma_rising"].to_numpy(), ind["low_n"].to_numpy()
    kind, pre, elig = ind["surge_kind"].to_numpy(), ind["pre_close"].to_numpy(), ind["eligible"].to_numpy()
    mcap = ind["mcap"].to_numpy()
    mkt = (market_ok.reindex(idx).ffill().fillna(False).to_numpy(dtype=bool)
           if market_ok is not None else np.ones(len(idx), dtype=bool))
    dv = ind["dollar_vol"].to_numpy()
    clean_on = bool((cfg.get("clean") or {}).get("enabled"))
    min_dv = cfg["universe"]["min_dollar_volume"]

    # Offering filings: mapped to trading days (filed on a weekend / holiday counts as the next trading day)
    off_cfg = (cfg.get("filters") or {}).get("offering") or {}
    off_on = bool(off_cfg.get("enabled")) and filings is not None
    off_flag = np.zeros(len(idx), dtype=bool)
    off_dates = pd.DatetimeIndex([])
    if filings is not None and len(filings):
        forms = set(off_cfg.get("forms", [])) | (set(off_cfg.get("shelf_forms", [])) if off_cfg.get("include_shelf") else set())
        off_dates = pd.DatetimeIndex(filings.loc[filings["form"].isin(forms), "date"]).sort_values()
        pos = idx.searchsorted(off_dates)
        off_flag[pos[pos < len(idx)]] = True
    lookback = pd.Timedelta(days=off_cfg.get("lookback_days", 0))
    cat_excl = set(((cfg.get("filters") or {}).get("catalyst") or {}).get("exclude") or [])

    def offering_before(i):
        if not off_on or len(off_dates) == 0:
            return False
        k = off_dates.searchsorted(idx[i] - lookback)
        return k < len(off_dates) and off_dates[k] <= idx[i]

    trades: list[Trade] = []
    state = "idle"
    tr: Trade | None = None
    s_i = 0
    hi = 0.0
    max_dd = 0.0
    attempt = 0
    pending = None          # ("probe"/"add"/"partial"/"exit", reason) executed at the next open
    stop = np.nan
    held_qty = 0.0          # current position size (unit: target full position = 1 unit of capital)
    peak_close = 0.0
    partial_done = False
    n = len(idx)
    last_i = -1

    def new_trade(i):
        return Trade(ticker, idx[i], kind[i], float(pre[i]), attempt,
                     surge_gain=float(c[i] / pre[i] - 1), mcap_at_surge=float(mcap[i]),
                     catalyst=catalyst_label(filings, idx[i]) if filings is not None else "")

    def close_trade(i, price, why):
        nonlocal tr, state, attempt, held_qty
        tr.legs.append((idx[i], -held_qty, float(price)))
        tr.exit_date, tr.exit_price, tr.exit_reason = idx[i], float(price), why
        trades.append(tr)
        held_qty = 0.0
        prev = tr
        tr = None
        if mode == "staged" and why in ("stop", "probe_timeout") and attempt < max_attempts \
                and i - s_i <= w["max_days"]:
            attempt += 1
            tr = Trade(prev.ticker, prev.surge_date, prev.surge_kind, prev.pre_surge_close, attempt,
                       surge_gain=prev.surge_gain, mcap_at_surge=prev.mcap_at_surge, catalyst=prev.catalyst)
            state = "watch"
        else:
            state = "idle"

    for i in range(n):
        if idx[i] > end:
            break
        last_i = i
        # ---- First execute the action decided at yesterday's close (filled at the open) ----
        if pending is not None:
            act, why = pending
            pending = None
            if act == "probe":
                tr.entry_date, tr.entry = idx[i], o[i]
                tr.stop0 = max(low_n[i - 1] if not np.isnan(low_n[i - 1]) else 0.0,
                               o[i] * (1 - p["max_stop_pct"]))
                if tr.stop0 >= o[i]:  # gapped open below the stop
                    tr.stop0 = o[i] * (1 - p["max_stop_pct"])
                stop = tr.stop0
                peak_close, partial_done = o[i], False
                size = 1.0 if mode == "immediate" else p["probe_size"]
                held_qty = size / o[i]
                tr.legs.append((idx[i], held_qty, float(o[i])))
                state = "full" if mode == "immediate" else "probe"
            elif act == "add":
                q = p["add_size"] / o[i]
                held_qty += q
                tr.legs.append((idx[i], q, float(o[i])))
                tr.add_date, tr.add_price = idx[i], o[i]
                if p["breakeven_after_add"]:
                    stop = max(stop, tr.entry)
                state = "full"
            elif act == "partial":
                q = held_qty * partial_frac
                held_qty -= q
                tr.legs.append((idx[i], -q, float(o[i])))
                tr.partial_date = idx[i]
                partial_done = True
            elif act == "exit":
                close_trade(i, o[i], why)

        # ---- In position: stop (intraday) ----
        if state in ("probe", "full") and l[i] <= stop:
            close_trade(i, min(o[i], stop), "stop")
            continue

        if i + 1 >= n and state_out is None:  # backtest: no "next open" on the last day; the scan still needs the close decision
            continue

        # ---- Close decision ----
        if state == "idle":
            if idx[i] >= start and kind[i] and elig[i]:
                stats["surges"] = stats.get("surges", 0) + 1
                if offering_before(i):  # already issuing shares around the surge
                    stats["diluted"] = stats.get("diluted", 0) + 1
                    continue
                if cat_excl and filings is not None and catalyst_label(filings, idx[i]) in cat_excl:
                    stats["catalyst_excluded"] = stats.get("catalyst_excluded", 0) + 1
                    continue
                attempt = 1
                tr = new_trade(i)
                s_i, hi, max_dd = i, c[i], 0.0
                if mode == "immediate":
                    if mkt[i]:
                        tr.signal_date = idx[i]
                        pending = ("probe", "")
                        state = "pending_entry"
                    else:
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
            if off_on and off_cfg.get("during_watch", True) and off_flag[i]:  # offering filing during the watch period
                stats["diluted"] = stats.get("diluted", 0) + (attempt == 1)
                state, tr = "idle", None
                continue
            new_high = c[i] > hi * (1 - e["near_high_pct"]) if days > 0 else False
            ok = (
                days >= w["min_days"] and new_high and vr[i] >= e["min_vol_ratio"]
                and (e.get("max_vol_ratio") is None or vr[i] <= e["max_vol_ratio"])
                and (not e.get("require_above_ma") or c[i] > ma[i])
                and (not e.get("require_ma_rising") or ma_up[i])
                and mkt[i]
                and (not clean_on or dv[i] >= min_dv)
            )
            hi = max(hi, c[i])
            max_dd = min(max_dd, c[i] / hi - 1)
            if ok and entry_filter is not None and not entry_filter(idx[i], tr):
                stats["filtered"] = stats.get("filtered", 0) + 1
                ok = False
            if ok:
                tr.signal_date = idx[i]
                tr.days_surge_to_signal = days
                tr.pullback_before_signal = float(max_dd)
                pending = ("probe", "")
                state = "pending_entry"
        elif state in ("probe", "full"):
            peak_close = max(peak_close, c[i])
            r = tr.entry - tr.stop0
            held = i - idx.get_loc(tr.entry_date)
            if off_on and off_cfg.get("exit_on_offering") and off_flag[i]:
                pending = ("exit", "offering")
            elif state == "probe":
                if c[i] >= tr.entry + p["add_trigger_r"] * r:
                    pending = ("add", "")
                elif held >= p["probe_max_days"]:
                    pending = ("exit", "probe_timeout")
            else:
                if partial_r and not partial_done and mode == "staged" and c[i] >= tr.entry + partial_r * r:
                    pending = ("partial", "")
                elif exit_mode == "atr":
                    if not np.isnan(atr[i]):
                        stop = max(stop, peak_close - atr_mult * atr[i])
                    if held >= p["max_hold_days"]:
                        pending = ("exit", "max_hold")
                else:
                    trail = ma_tr2[i] if partial_done else ma_tr[i]
                    if c[i] < trail:
                        pending = ("exit", "trail_ma")
                    elif held >= p["max_hold_days"]:
                        pending = ("exit", "max_hold")

    if tr is not None and tr.entry_date is not None and state in ("probe", "full") and last_i >= 0:
        tr.legs.append((idx[last_i], -held_qty, float(c[last_i])))
        tr.exit_date, tr.exit_price, tr.exit_reason = idx[last_i], float(c[last_i]), "open"
        trades.append(tr)

    if state_out is not None and last_i >= 0:
        state_out.update({
            "state": state, "date": idx[last_i], "close": float(c[last_i]),
            "pending": pending[0] if pending else None, "pending_reason": pending[1] if pending else None,
            "trade": tr, "stop": float(stop) if state in ("probe", "full") else np.nan,
            "surge_high": float(hi) if tr is not None else np.nan,
            "days_since_surge": int(last_i - s_i) if tr is not None else None,
            "surge_today": bool(kind[last_i]) and bool(elig[last_i]),
            "vol_ratio": float(vr[last_i]) if not np.isnan(vr[last_i]) else np.nan,
        })
    return trades


def market_filter(bench: pd.DataFrame, cfg: dict) -> pd.Series | None:
    """Market filter: new positions only while the benchmark closes above its N-day MA. Returns None when cfg["market"]["enabled"]=false."""
    m = cfg.get("market") or {}
    if not m.get("enabled"):
        return None
    c = bench["Close"]
    return c > c.rolling(m.get("ma", 200)).mean()


def trade_pnl(t: Trade, cfg: dict, mode: str = "staged") -> dict:
    """Returns measured with "target full position = 1" (slippage included). r_multiple uses the risk taken at entry as 1R
    (staged mode = risk of the probe portion; control group = full position risk)."""
    s = cfg["position"]["slippage"]
    pnl = 0.0
    for _, q, px in t.legs:
        pnl += -q * px * (1 + s) if q > 0 else -q * px * (1 - s)
    risk_pct = (t.entry - t.stop0) / t.entry
    base = (1.0 if mode == "immediate" else cfg["position"]["probe_size"]) * risk_pct
    return {
        "pnl": pnl,
        "risk_pct": risk_pct,
        "r_multiple": pnl / base if base > 0 else np.nan,
        "added": t.add_date is not None or mode == "immediate",
        "hold_days": int(np.busday_count(t.entry_date.date(), t.exit_date.date())),
    }


def trades_frame(trades: list[Trade], cfg: dict, mode: str = "staged") -> pd.DataFrame:
    rows = []
    for t in trades:
        d = {k: v for k, v in t.__dict__.items() if k != "legs"}
        d["legs"] = ";".join(f"{dt.date()}|{q:.6g}|{px:.6g}" for dt, q, px in t.legs)
        d.update(trade_pnl(t, cfg, mode))
        rows.append(d)
    return pd.DataFrame(rows)


def parse_legs(s: str) -> list[tuple[pd.Timestamp, float, float]]:
    out = []
    for part in str(s).split(";"):
        if part:
            d, q, px = part.split("|")
            out.append((pd.Timestamp(d), float(q), float(px)))
    return out
