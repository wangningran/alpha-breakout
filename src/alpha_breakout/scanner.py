"""Breakout scanner v0 (STRATEGY.md section 3).

compute_features() computes every indicator the rules use, day by day; scan() applies the thresholds on top,
producing a boolean column per condition and an overall trigger column `signal`. All "past N days" windows are shift(1),
i.e. only data before the signal day is used; on the signal day itself only the close / that day's price and volume are used.

Not yet implemented (needs fundamentals / SEC data): market cap 100M-3B, sector filter, cash runway, ATM offering filter.
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

    # liquidity
    out["dollar_vol_avg"] = (close * vol).rolling(c_liq["dollar_volume_window"]).mean().shift(1)

    # consolidation range: highs / lows and range over the past L days
    for L in c_cons["base_lookbacks"]:
        hi = level.rolling(L).max().shift(1)
        lo = low.rolling(L).min().shift(1)
        out[f"base_high_{L}"] = hi
        out[f"base_range_{L}"] = hi / lo - 1

    # 20-day volatility and its percentile over the past year (value from the day before the signal)
    logret = np.log(close).diff()
    rv = logret.rolling(c_cons["vol_window"]).std() * np.sqrt(252)
    out["rv20"] = rv.shift(1)
    out["rv20_pctile"] = (
        rv.rolling(c_cons["vol_rank_lookback"], min_periods=c_cons["vol_rank_lookback"] // 2)
        .rank(pct=True)
        .shift(1)
    )

    # 52-week high
    out["high_52w"] = level.rolling(c_bo["high_lookback"], min_periods=c_bo["high_lookback"] // 2).max().shift(1)

    # volume ratio / close location
    out["vol_avg50"] = vol.rolling(c_vol["avg_window"]).mean().shift(1)
    out["vol_ratio"] = vol / out["vol_avg50"]
    rng = (high - low).replace(0, np.nan)
    out["close_location"] = ((close - low) / rng).fillna(0.5)

    # relative strength vs XBI (signal day included)
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
    # vol_check = prev_day: the volatility percentile on the day before the signal must be low (original v0)
    # vol_check = min_in_base: low volatility occurred within the past vol_check_window days (allows expansion to start before the breakout)
    if c_cons.get("vol_check", "prev_day") == "min_in_base":
        pct = f["rv20_pctile"].rolling(c_cons.get("vol_check_window", 60), min_periods=1).min()
    else:
        pct = f["rv20_pctile"]
    f["ok_low_vol"] = pct <= c_cons["max_vol_percentile"]
    f["ok_consolidation"] = f["ok_tight_base"] & f["ok_low_vol"]

    # breakout above the consolidation range: the corresponding window itself must be "tight"
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
