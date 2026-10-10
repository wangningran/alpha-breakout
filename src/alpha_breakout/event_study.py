"""事件复盘：找大涨段、定起涨首日（day 0），输出 day -60 ~ day 0 的信号表现。

大涨段识别：
1. 在收盘价上做 zigzag（反转阈值 `reversal`），得到交替的低点 / 高点；
2. 低点→高点涨幅 ≥ `min_gain` 的上升段视为“大涨段”；
3. day 0 = 段内第一根“点火 K 线”：单日涨幅 ≥ `ignite_ret` 且量比 ≥ `ignite_vol`，
   并且从它的收盘到段高点还有 ≥ `min_gain_after` 的空间（排除段末尾的追涨日）；
   找不到就退化为第一次收盘站上低点 ×(1+`fallback_gain`) 的那天。
day 0 是机械规则给的近似，样本配置里可以手工指定起涨日覆盖它。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class RallyParams:
    reversal: float = 0.25       # zigzag 反转阈值
    min_gain: float = 0.60       # 低点到高点至少 +60%
    ignite_ret: float = 0.08     # 点火日单日涨幅
    ignite_vol: float = 2.0      # 点火日量比（对 50 日均量）
    min_gain_after: float = 0.40  # 点火日收盘到段高点至少还有 +40%
    fallback_gain: float = 0.15
    min_history: int = 120       # 低点之前至少要有这么多交易日，否则标记 short_history


def zigzag(close: pd.Series, reversal: float) -> list[tuple[pd.Timestamp, float, str]]:
    """返回 [(日期, 价格, 'L'/'H'), ...]，交替出现。"""
    pivots = []
    trend = None
    ext_i, ext_p = close.index[0], close.iloc[0]
    lo_i, lo_p, hi_i, hi_p = ext_i, ext_p, ext_i, ext_p
    for i, p in close.items():
        if trend is None:
            if p > hi_p:
                hi_i, hi_p = i, p
            if p < lo_p:
                lo_i, lo_p = i, p
            if hi_p / lo_p - 1 >= reversal:
                if lo_i < hi_i:
                    pivots.append((lo_i, lo_p, "L"))
                    trend, ext_i, ext_p = "up", hi_i, hi_p
                else:
                    pivots.append((hi_i, hi_p, "H"))
                    trend, ext_i, ext_p = "down", lo_i, lo_p
            continue
        if trend == "up":
            if p > ext_p:
                ext_i, ext_p = i, p
            elif p / ext_p - 1 <= -reversal / (1 + reversal):
                pivots.append((ext_i, ext_p, "H"))
                trend, ext_i, ext_p = "down", i, p
        else:
            if p < ext_p:
                ext_i, ext_p = i, p
            elif p / ext_p - 1 >= reversal:
                pivots.append((ext_i, ext_p, "L"))
                trend, ext_i, ext_p = "up", i, p
    pivots.append((ext_i, ext_p, "H" if trend == "up" else "L"))
    return pivots


def find_rallies(df: pd.DataFrame, params: RallyParams | None = None) -> pd.DataFrame:
    params = params or RallyParams()
    close = df["Close"]
    vol_ratio = df["Volume"] / df["Volume"].rolling(50, min_periods=20).mean().shift(1)
    ret = close.pct_change()
    piv = zigzag(close, params.reversal)
    rows = []
    for (d0, p0, k0), (d1, p1, k1) in zip(piv, piv[1:]):
        if k0 != "L" or k1 != "H" or p1 / p0 - 1 < params.min_gain:
            continue
        leg = close.loc[d0:d1]
        ign = [d for d in leg.index[1:]
               if ret[d] >= params.ignite_ret and vol_ratio[d] >= params.ignite_vol
               and p1 / close[d] - 1 >= params.min_gain_after]
        if ign:
            day0, how = ign[0], "ignition"
        else:
            above = leg[leg >= p0 * (1 + params.fallback_gain)]
            day0, how = above.index[0], "fallback"
        rows.append({
            "trough_date": d0.date(), "trough": round(p0, 2),
            "day0": day0.date(), "day0_rule": how,
            "day0_ret": round(ret[day0], 4), "day0_vol_ratio": round(vol_ratio[day0], 2),
            "peak_date": d1.date(), "peak": round(p1, 2),
            "gain_trough_to_peak": round(p1 / p0 - 1, 3),
            "gain_day0_close_to_peak": round(p1 / close[day0] - 1, 3),
            "days_day0_to_peak": int(close.index.get_loc(d1) - close.index.get_loc(day0)),
            "ignition_days": ",".join(str(d.date()) for d in ign),
            "short_history": bool(close.index.get_loc(d0) < params.min_history),
        })
    return pd.DataFrame(rows)


SNAPSHOT_COLS = ["close", "ret_1d", "base_range_60", "base_range_90", "base_range_120",
                 "rv20", "rv20_pctile", "vol_ratio", "close_location", "rs_20d", "dollar_vol_avg"]


def event_window(features: pd.DataFrame, day0, pre: int = 60, post: int = 0) -> pd.DataFrame:
    """以 day0 为 0 对齐，截取 [-pre, +post] 的特征行，加一列 rel_day。"""
    idx = features.index.get_loc(pd.Timestamp(day0))
    lo, hi = max(0, idx - pre), min(len(features) - 1, idx + post)
    w = features.iloc[lo: hi + 1].copy()
    w.insert(0, "rel_day", np.arange(lo - idx, hi - idx + 1))
    return w


def window_summary(w: pd.DataFrame) -> dict:
    """day -60~-1 的横盘 / 波动 / 量能概况 + day 0 当天的数值。"""
    pre = w[w["rel_day"] < 0]
    d0 = w[w["rel_day"] == 0].iloc[0]
    return {
        "pre_range_60d": round(pre["close"].max() / pre["close"].min() - 1, 3),
        "pre_rv20_min": round(pre["rv20"].min(), 3),
        "pre_rv20_pctile_min": round(pre["rv20_pctile"].min(), 3),
        "pre_vol_ratio_max": round(pre["vol_ratio"].max(), 2),
        "pre_days_vol_ratio_ge2": int((pre["vol_ratio"] >= 2).sum()),
        "pre_rs20_last": round(pre["rs_20d"].iloc[-1], 3),
        "d0_base_range_60": round(d0["base_range_60"], 3),
        "d0_base_range_120": round(d0["base_range_120"], 3) if pd.notna(d0["base_range_120"]) else np.nan,
        "d0_rv20_pctile": round(d0["rv20_pctile"], 3),
        "d0_vol_ratio": round(d0["vol_ratio"], 2),
        "d0_close_location": round(d0["close_location"], 2),
        "d0_rs_20d": round(d0["rs_20d"], 3),
    }
