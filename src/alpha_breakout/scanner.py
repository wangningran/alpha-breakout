"""突破扫描器 v0（STRATEGY.md 第 3 节）。

compute_features() 逐日算出所有规则用到的指标，scan() 在其上套用阈值，
输出每个条件的布尔列和总触发列 `signal`。所有“过去 N 日”窗口都 shift(1)，
即只用信号日之前的数据，信号日当天只看收盘价 / 当日量价。

尚未实现（需要基本面 / SEC 数据）：市值 1–30 亿、行业过滤、现金跑道、ATM 增发过滤。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import load_config


def compute_features(df: pd.DataFrame, bench: pd.DataFrame | None, cfg: dict) -> pd.DataFrame:
    c_cons, c_bo, c_vol, c_rs, c_liq = (cfg["consolidation"], cfg["breakout"], cfg["volume"],
                                        cfg["relative_strength"], cfg["liquidity"])
    out = pd.DataFrame(index=df.index)
    close, high, low, vol = df["Close"], df["High"], df["Low"], df["Volume"]
    level = df[c_bo.get("price_field", "High")]

    out["close"] = close
    out["ret_1d"] = close.pct_change()

    # 流动性
    out["dollar_vol_avg"] = (close * vol).rolling(c_liq["dollar_volume_window"]).mean().shift(1)

    # 横盘区：过去 L 日的高低点与振幅
    for L in c_cons["base_lookbacks"]:
        hi = level.rolling(L).max().shift(1)
        lo = low.rolling(L).min().shift(1)
        out[f"base_high_{L}"] = hi
        out[f"base_range_{L}"] = hi / lo - 1

    # 20 日波动率及其在过去一年中的分位（取信号日前一天的值）
    logret = np.log(close).diff()
    rv = logret.rolling(c_cons["vol_window"]).std() * np.sqrt(252)
    out["rv20"] = rv.shift(1)
    out["rv20_pctile"] = (
        rv.rolling(c_cons["vol_rank_lookback"], min_periods=c_cons["vol_rank_lookback"] // 2)
        .rank(pct=True)
        .shift(1)
    )

    # 52 周高点
    out["high_52w"] = level.rolling(c_bo["high_lookback"], min_periods=c_bo["high_lookback"] // 2).max().shift(1)

    # 量比 / 收盘位置
    out["vol_avg50"] = vol.rolling(c_vol["avg_window"]).mean().shift(1)
    out["vol_ratio"] = vol / out["vol_avg50"]
    rng = (high - low).replace(0, np.nan)
    out["close_location"] = ((close - low) / rng).fillna(0.5)

    # 相对 XBI 强度（含信号日）
    w = c_rs["window"]
    out["ret_20d"] = close.pct_change(w)
    if bench is not None:
        b = bench["Close"].reindex(df.index).ffill()
        out["bench_ret_20d"] = b.pct_change(w)
        out["rs_20d"] = out["ret_20d"] - out["bench_ret_20d"]
    else:
        out["bench_ret_20d"] = np.nan
        out["rs_20d"] = np.nan
    return out


def scan(df: pd.DataFrame, bench: pd.DataFrame | None = None, cfg: dict | None = None) -> pd.DataFrame:
    cfg = cfg or load_config()
    f = compute_features(df, bench, cfg)
    c_cons, c_bo, c_vol, c_rs, c_liq = (cfg["consolidation"], cfg["breakout"], cfg["volume"],
                                        cfg["relative_strength"], cfg["liquidity"])
    Ls = c_cons["base_lookbacks"]

    f["ok_liquidity"] = (f["close"] > c_liq["min_price"]) & (f["dollar_vol_avg"] > c_liq["min_dollar_volume"])

    tight = pd.concat([f[f"base_range_{L}"] <= c_cons["max_base_range"] for L in Ls], axis=1)
    f["ok_tight_base"] = tight.any(axis=1)
    # vol_check = prev_day：信号前一天的波动率分位要低（v0 原文）
    # vol_check = min_in_base：过去 vol_check_window 日内出现过低波动（允许突破前已开始放大）
    if c_cons.get("vol_check", "prev_day") == "min_in_base":
        pct = f["rv20_pctile"].rolling(c_cons.get("vol_check_window", 60), min_periods=1).min()
    else:
        pct = f["rv20_pctile"]
    f["ok_low_vol"] = pct <= c_cons["max_vol_percentile"]
    f["ok_consolidation"] = f["ok_tight_base"] & f["ok_low_vol"]

    # 突破横盘上沿：要求对应窗口本身是“紧”的
    brk_base = pd.concat(
        [(f["close"] > f[f"base_high_{L}"]) & (f[f"base_range_{L}"] <= c_cons["max_base_range"]) for L in Ls],
        axis=1,
    ).any(axis=1)
    brk_52w = f["close"] > f["high_52w"]
    f["ok_breakout"] = (brk_base & c_bo["use_base_high"]) | (brk_52w & c_bo["use_52w_high"])

    f["ok_volume"] = f["vol_ratio"] >= c_vol["min_volume_mult"]
    f["ok_close_loc"] = f["close_location"] >= c_vol["min_close_location"]
    f["ok_rs"] = f["rs_20d"] > c_rs["min_excess_return"]

    conds = ["ok_liquidity", "ok_consolidation", "ok_breakout", "ok_volume", "ok_close_loc", "ok_rs"]
    f["n_conditions"] = f[conds].sum(axis=1)
    f["signal"] = f[conds].all(axis=1)
    return f


CONDITIONS = ["ok_liquidity", "ok_tight_base", "ok_low_vol", "ok_breakout",
              "ok_volume", "ok_close_loc", "ok_rs"]
