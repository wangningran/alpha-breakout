"""Quantitative part of the "research" step: major-holder filings (13D/13G) and fundamental features, plus the pre-probe check built from them.

Config (the filters section of the strategy yaml):
  holders:
    require: none           # none / new_13d / any_13d / any_13d_or_13g
    before_days: 30         # how many days before the surge to start counting
  fundamentals:
    min_runway_q: null      # minimum quarters of cash (loss-making companies); null = no check
    min_rev_yoy: null       # minimum latest-quarter revenue YoY, e.g. 0.2; null = no check
    require_accel: false    # require accelerating revenue growth
    min_gm_change: null     # minimum YoY gross margin improvement (0.02 = 2 percentage points); null = no check
    allow_missing: true     # whether to let it through when data is missing
  momentum:
    max_rs60: null          # cap on the 60-day excess return vs IWM on the probe date (0.5 = 50%), excludes names that ran too far; null = no check
  earnings:
    avoid_before_days: null # no probe within N calendar days before the expected next earnings (projected from past 8-K item 2.02); null = no check
    overdue_days: 0         # also no probe for the first N days past the expected earnings date while not yet reported (earnings could land any time)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import fundamentals as fmod
from .filings import days_to_next_earnings, earnings_dates, holder_features


def trade_features(fil: pd.DataFrame | None, ftab: pd.DataFrame | None, surge_date, signal_date,
                   before_days: int = 30) -> dict:
    d = {}
    d.update(holder_features(fil, surge_date, signal_date, before_days))
    d.update(fmod.features_at(ftab, signal_date))
    d["days_to_earnings"] = days_to_next_earnings(earnings_dates(fil), signal_date)
    return d


def rs_at(close: pd.Series, bench: pd.Series, date, n: int = 60) -> float:
    i = close.index.get_loc(pd.Timestamp(date))
    if i < n:
        return np.nan
    b = bench.reindex(close.index).ffill()
    if not b.iloc[i - n] > 0:
        return np.nan
    return float(close.iloc[i] / close.iloc[i - n] - b.iloc[i] / b.iloc[i - n])


def make_entry_filter(cfg: dict, fil: pd.DataFrame | None, ftab: pd.DataFrame | None,
                      close: pd.Series | None = None, bench: pd.Series | None = None):
    f = cfg.get("filters") or {}
    h = f.get("holders") or {}
    fu = f.get("fundamentals") or {}
    mo = f.get("momentum") or {}
    need_h = h.get("require", "none") not in (None, "none")
    need_f = any(fu.get(k) not in (None, False) for k in ("min_runway_q", "min_rev_yoy", "require_accel", "min_gm_change"))
    need_m = mo.get("max_rs60") is not None and close is not None and bench is not None
    ea = f.get("earnings") or {}
    need_e = ea.get("avoid_before_days") is not None
    ed = earnings_dates(fil) if need_e else None
    if not need_h and not need_f and not need_m and not need_e:
        return None
    allow_missing = fu.get("allow_missing", True)
    bench_aligned = bench.reindex(close.index).ffill() if need_m else None

    def ok(date, trade) -> bool:
        if need_h:
            x = holder_features(fil, trade.surge_date, date, h.get("before_days", 30))
            req = h["require"]
            if req == "new_13d" and x["new_13d"] == 0:
                return False
            if req == "any_13d" and x["new_13d"] + x["amend_13d"] == 0:
                return False
            if req == "any_13d_or_13g" and x["new_13d"] + x["amend_13d"] + x["new_13g"] == 0:
                return False
        if need_f:
            x = fmod.features_at(ftab, date)
            if fu.get("min_runway_q") is not None:
                v = x["runway_q"]
                if np.isnan(v):
                    if not allow_missing:
                        return False
                elif v < fu["min_runway_q"]:
                    return False
            if fu.get("min_rev_yoy") is not None:
                v = x["rev_yoy"]
                if np.isnan(v):
                    if not allow_missing:
                        return False
                elif v < fu["min_rev_yoy"]:
                    return False
            if fu.get("min_gm_change") is not None:
                v = x["gm_change"]
                if np.isnan(v):
                    if not allow_missing:
                        return False
                elif v < fu["min_gm_change"]:
                    return False
            if fu.get("require_accel"):
                v = x["rev_accel"]
                if np.isnan(v):
                    if not allow_missing:
                        return False
                elif v < 1:
                    return False
        if need_m:
            v = rs_at(close, bench_aligned, date, 60)
            if not np.isnan(v) and v > mo["max_rs60"]:
                return False
        if need_e:
            v = days_to_next_earnings(ed, date)
            if not np.isnan(v) and -ea.get("overdue_days", 0) <= v <= ea["avoid_before_days"]:
                return False
        return True

    return ok
