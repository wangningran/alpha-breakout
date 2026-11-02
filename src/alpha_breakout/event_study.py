"""Event review: find big rallies, locate the first day of the move (day 0), and report signal behavior from day -60 to day 0.

Rally detection:
1. run a zigzag on closes (reversal threshold `reversal`) to get alternating troughs / peaks;
2. an up leg with a trough-to-peak gain >= `min_gain` counts as a "rally";
3. day 0 = the first "ignition bar" in the leg: one-day gain >= `ignite_ret` and volume ratio >= `ignite_vol`,
   with at least `min_gain_after` left from its close to the leg peak (excludes chase days near the end of the leg);
   if there is none, fall back to the first close at or above trough x (1+`fallback_gain`).
day 0 is an approximation from mechanical rules; the sample config can override it with a manual start date.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class RallyParams:
    reversal: float = 0.25       # zigzag reversal threshold
    min_gain: float = 0.60       # at least +60% from trough to peak
    ignite_ret: float = 0.08     # one-day gain on the ignition day
    ignite_vol: float = 2.0      # ignition-day volume ratio (vs 50-day average volume)
    min_gain_after: float = 0.40  # at least +40% left from the ignition-day close to the leg peak
    fallback_gain: float = 0.15
    min_history: int = 120       # minimum trading days before the trough, otherwise flagged short_history


def zigzag(close: pd.Series, reversal: float) -> list[tuple[pd.Timestamp, float, str]]:
    """Return [(date, price, 'L'/'H'), ...], alternating."""
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
    """Align on day0 = 0, take the feature rows in [-pre, +post], and add a rel_day column."""
    idx = features.index.get_loc(pd.Timestamp(day0))
    lo, hi = max(0, idx - pre), min(len(features) - 1, idx + post)
    w = features.iloc[lo: hi + 1].copy()
    w.insert(0, "rel_day", np.arange(lo - idx, hi - idx + 1))
    return w


def window_summary(w: pd.DataFrame) -> dict:
    """Consolidation / volatility / volume profile for day -60..-1, plus the day 0 values."""
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
