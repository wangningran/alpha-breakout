"""Portfolio-level simulation: put the per-trade backtest results into an account with capital and position limits.

Rules:
- Each probe risks risk_per_trade of the account (1R). Target full position capital T = 1R amount / (probe fraction x stop distance),
  capped at max_position_pct of the account; every fill (probe, add, partial sell, exit) is scaled proportionally from legs.
- Open positions <= max_positions, new probes per day <= max_new_per_day; shrink or skip when cash runs short.
- When a day has more signals than slots, pick by order: random, rs60_asc (60-day relative strength low to high, smaller gainers first),
  gm_change_desc (larger gross margin improvement first), rev_yoy_desc (higher revenue growth first); missing values go last, ties are random.
  Repeat with multiple random seeds to see the distribution of results.
- idle_asset=IWM: unused cash earns the IWM daily close-to-close return, i.e. "hold the index normally, sell it to fund a probe on a signal".
- Equity and drawdown are marked to market at each daily close. Fills include slippage (buy x(1+s), sell x(1-s)).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .strategy import parse_legs


@dataclass
class PortfolioResult:
    equity: pd.Series
    taken: pd.DataFrame
    skipped: int

    def stats(self) -> dict:
        eq = self.equity
        rets = eq.pct_change().dropna()
        years = (eq.index[-1] - eq.index[0]).days / 365.25
        cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / years) - 1 if years > 0 else np.nan
        dd = eq / eq.cummax() - 1
        yearly = eq.resample("YE").last()
        yearly = yearly / yearly.shift(1).fillna(eq.iloc[0]) - 1
        return {
            "final_equity": round(float(eq.iloc[-1]), 0),
            "total_return": round(float(eq.iloc[-1] / eq.iloc[0] - 1), 3),
            "cagr": round(float(cagr), 3),
            "max_drawdown": round(float(dd.min()), 3),
            "sharpe": round(float(rets.mean() / rets.std() * np.sqrt(252)), 2) if rets.std() > 0 else None,
            "trades_taken": int(len(self.taken)),
            "trades_skipped": int(self.skipped),
            "yearly_return": {str(k.year): round(float(v), 3) for k, v in yearly.items()},
        }


ORDERS = {"rs60_asc": ("rs60", True), "gm_change_desc": ("gm_change", False), "rev_yoy_desc": ("rev_yoy", False)}


def simulate(trades: pd.DataFrame, closes: dict[str, pd.Series], calendar: pd.DatetimeIndex,
             pcfg: dict, probe_size: float, slippage: float, seed: int = 0,
             idle_series: pd.Series | None = None) -> PortfolioResult:
    rng = np.random.default_rng(seed)
    t = trades.dropna(subset=["entry_date"]).copy()
    t["entry_date"] = pd.to_datetime(t["entry_date"])
    t["plegs"] = t["legs"].map(parse_legs)
    t["rnd"] = rng.random(len(t))
    order = pcfg.get("order", "random")
    if order in ORDERS:
        col, asc = ORDERS[order]
        if col not in t:
            raise ValueError(f"ordering requires a {col} column in the trades file (run the backtest with --features)")
        t["key"] = t[col] if asc else -t[col]
        t["key"] = t["key"].fillna(np.inf)
        by_entry = {d: g.sort_values(["key", "rnd"]) for d, g in t.groupby("entry_date")}
    else:
        by_entry = {d: g.sort_values("rnd") for d, g in t.groupby("entry_date")}
    idle = idle_series.reindex(calendar).ffill() if idle_series is not None else None
    prev_idle = None

    cash = float(pcfg["initial_equity"])
    positions: dict[int, dict] = {}   # trade row id -> {ticker, shares, legs, scale}
    equity_hist = []
    taken, skipped = [], 0
    last_close: dict[str, float] = {}

    def equity_now():
        return cash + sum(p["shares"] * last_close.get(p["ticker"], p["last_px"]) for p in positions.values())

    for day in calendar:
        # 0) idle cash earns the index return (previous close to today's close)
        if idle is not None:
            px_idle = idle.loc[day]
            if prev_idle is not None and cash > 0 and px_idle > 0:
                cash *= px_idle / prev_idle
            prev_idle = px_idle
        # 1) today's fills for existing positions (add / partial sell / exit), sells before buys
        todays = []
        for pid, p in positions.items():
            for d, q, px in p["legs"]:
                if d == day:
                    todays.append((q, pid, px))
        for q, pid, px in sorted(todays, key=lambda x: x[0]):   # sells (q<0) first
            p = positions[pid]
            if q < 0:
                sell = min(p["shares"], -q * p["scale"])
                cash += sell * px * (1 - slippage)
                p["shares"] -= sell
            else:
                want = q * p["scale"]
                cost = want * px * (1 + slippage)
                if cost > cash:  # not enough cash: add only what we can afford
                    want = max(0.0, cash / (px * (1 + slippage)))
                    cost = want * px * (1 + slippage)
                cash -= cost
                p["shares"] += want
            p["last_px"] = px
        for pid in [pid for pid, p in positions.items() if p["exit_date"] == day]:
            p = positions.pop(pid)
            if p["shares"] > 1e-9:  # legs should already be flat; this is a safety net
                cash += p["shares"] * p["last_px"] * (1 - slippage)

        # 2) new probes
        if day in by_entry:
            eq = equity_now()
            new_today = 0
            for row in by_entry[day].itertuples():
                if len(positions) >= pcfg["max_positions"] or new_today >= pcfg["max_new_per_day"]:
                    skipped += 1
                    continue
                risk_pct = row.risk_pct if row.risk_pct > 0 else np.nan
                if np.isnan(risk_pct):
                    skipped += 1
                    continue
                target = eq * pcfg["risk_per_trade"] / (probe_size * risk_pct)
                target = min(target, eq * pcfg["max_position_pct"])
                legs = row.plegs
                d0, q0, px0 = legs[0]
                cost = q0 * target * px0 * (1 + slippage)
                if cost > cash:
                    if cash < 0.25 * cost:
                        skipped += 1
                        continue
                    target *= cash / cost
                    cost = cash
                cash -= cost
                positions[row.Index] = {"ticker": row.ticker, "shares": q0 * target, "legs": legs[1:],
                                        "scale": target, "last_px": px0,
                                        "exit_date": pd.Timestamp(row.exit_date)}
                taken.append(row.Index)
                new_today += 1
                pos = positions[row.Index]
                for d, q, px in [x for x in pos["legs"] if x[0] == day]:   # stopped out on the entry day
                    if q < 0:
                        sell = min(pos["shares"], -q * pos["scale"])
                        cash += sell * px * (1 - slippage)
                        pos["shares"] -= sell
                        pos["last_px"] = px
                if pos["exit_date"] == day:
                    positions.pop(row.Index)

        # 3) mark to market at the close
        for p in positions.values():
            s = closes.get(p["ticker"])
            if s is not None and day in s.index:
                last_close[p["ticker"]] = float(s.loc[day])
        equity_hist.append((day, equity_now()))

    eq = pd.Series(dict(equity_hist)).sort_index()
    return PortfolioResult(eq, t.loc[taken], skipped)
