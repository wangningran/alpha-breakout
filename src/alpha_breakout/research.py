"""“研究”环节的量化部分：大股东申报（13D/13G）和基本面特征，以及据此生成的试仓前检查。

配置（strategy yaml 的 filters 节）：
  holders:
    require: none           # none / new_13d / any_13d / any_13d_or_13g
    before_days: 30         # 从异动前多少天开始算
  fundamentals:
    min_runway_q: null      # 现金至少能撑几个季度（亏损公司）；null = 不检查
    min_rev_yoy: null       # 最近季度营收同比下限，例如 0.2；null = 不检查
    require_accel: false    # 要求营收增速加快
    allow_missing: true     # 缺数据时是否放行
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import fundamentals as fmod
from .filings import holder_features


def trade_features(fil: pd.DataFrame | None, ftab: pd.DataFrame | None, surge_date, signal_date,
                   before_days: int = 30) -> dict:
    d = {}
    d.update(holder_features(fil, surge_date, signal_date, before_days))
    d.update(fmod.features_at(ftab, signal_date))
    return d


def make_entry_filter(cfg: dict, fil: pd.DataFrame | None, ftab: pd.DataFrame | None):
    f = cfg.get("filters") or {}
    h = f.get("holders") or {}
    fu = f.get("fundamentals") or {}
    need_h = h.get("require", "none") not in (None, "none")
    need_f = any(fu.get(k) not in (None, False) for k in ("min_runway_q", "min_rev_yoy", "require_accel"))
    if not need_h and not need_f:
        return None
    allow_missing = fu.get("allow_missing", True)

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
            if fu.get("require_accel"):
                v = x["rev_accel"]
                if np.isnan(v):
                    if not allow_missing:
                        return False
                elif v < 1:
                    return False
        return True

    return ok
