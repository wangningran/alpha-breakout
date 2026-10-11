"""基本面时点数据（SEC XBRL frames API，全市场按季度拉取）。

每个概念、每个季度一次请求，得到全市场的值：
  营收（季度）：Revenues → RevenueFromContractWithCustomerExcludingAssessedTax → SalesRevenueNet，按优先级取
  净利润（季度）：NetIncomeLoss，亏损额作为“烧钱速度”的近似（含非现金项目，偏保守）
  毛利（季度）：GrossProfit（很多公司不单独报，缺失较多）
  现金（时点）：CashAndCashEquivalentsAtCarryingValue（缺失时用含受限现金的口径）
              + 短期投资 / 可售证券（生物科技公司大量现金放在这里）

防前视：所有数值在报告期末 + LAG_DAYS 天之后才视为可用（10-Q 一般 40–45 天内披露，
10-K 60–90 天，这里统一按 75 天，偏保守）。

缓存：data/cache/fundamentals.parquet，列 [cik, concept, end, val, avail]。
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from . import sec

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "data" / "cache" / "fundamentals.parquet"
LAG_DAYS = 75

DURATION = {
    "rev_1": "Revenues",
    "rev_2": "RevenueFromContractWithCustomerExcludingAssessedTax",
    "rev_3": "SalesRevenueNet",
    "net_income": "NetIncomeLoss",
    "gross_profit": "GrossProfit",
}
INSTANT = {
    "cash_1": "CashAndCashEquivalentsAtCarryingValue",
    "cash_2": "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    "sti_1": "ShortTermInvestments",
    "sti_2": "MarketableSecuritiesCurrent",
    "sti_3": "AvailableForSaleSecuritiesDebtSecuritiesCurrent",
}
URL = "https://data.sec.gov/api/xbrl/frames/us-gaap/{concept}/USD/{period}.json"


def fetch(start_year: int = 2017, refresh: bool = False, log=print) -> pd.DataFrame:
    today = pd.Timestamp.today()
    rows = []
    for y in range(start_year, today.year + 1):
        for q in range(1, 5):
            q_end = pd.Timestamp(year=y, month=3 * q, day=1) + pd.offsets.MonthEnd(0)
            if q_end > today:
                break
            recent = (today - q_end).days < 200
            for key, concept in list(DURATION.items()) + list(INSTANT.items()):
                if key == "rev_3" and y >= 2020:  # SalesRevenueNet 2019 年后基本停用，frame 不存在，跳过避免反复 404
                    continue
                period = f"CY{y}Q{q}" + ("I" if key in INSTANT else "")
                try:
                    d = sec._cached_json(f"frame_{concept}_{period}.json",
                                         URL.format(concept=concept, period=period), refresh, static=not recent)
                except Exception as e:  # 有些概念在早期季度没有 frame，跳过即可
                    log(f"[warn] {concept} {period}: {e}")
                    continue
                rows += [(r["cik"], key, r["end"], r["val"]) for r in d.get("data", [])]
        log(f"  fundamentals {y} done, {len(rows)} rows")
    df = pd.DataFrame(rows, columns=["cik", "concept", "end", "val"])
    df["end"] = pd.to_datetime(df["end"])
    df["avail"] = df["end"] + pd.Timedelta(days=LAG_DAYS)
    df = df.drop_duplicates(["cik", "concept", "end"])
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(CACHE)
    return df


def load() -> pd.DataFrame | None:
    return pd.read_parquet(CACHE) if CACHE.exists() else None


def company_table(df: pd.DataFrame, cik: int) -> pd.DataFrame:
    """把一家公司的长表整理成按报告期末的宽表：revenue, net_income, cash_total。"""
    d = df[df["cik"] == cik]
    if d.empty:
        return pd.DataFrame()
    w = d.pivot_table(index="end", columns="concept", values="val", aggfunc="last").sort_index()
    out = pd.DataFrame(index=w.index)
    out["revenue"] = _first(w, ["rev_1", "rev_2", "rev_3"])
    out["net_income"] = w.get("net_income")
    out["gross_profit"] = w.get("gross_profit")
    cash = _first(w, ["cash_1", "cash_2"])
    sti = w[[c for c in ("sti_1", "sti_2", "sti_3") if c in w]].max(axis=1) if any(c in w for c in ("sti_1", "sti_2", "sti_3")) else 0.0
    out["cash_total"] = cash + (sti.fillna(0.0) if isinstance(sti, pd.Series) else 0.0)
    out["avail"] = out.index + pd.Timedelta(days=LAG_DAYS)
    return out


def _first(w: pd.DataFrame, cols: list[str]) -> pd.Series:
    s = pd.Series(np.nan, index=w.index)
    for c in cols:
        if c in w:
            s = s.fillna(w[c])
    return s


def features_at(tab: pd.DataFrame, date) -> dict:
    """date 当天可见的基本面特征。缺数据的字段为 NaN。

    rev_yoy        ：最近一个季度营收同比
    rev_yoy_prev   ：上一个季度的营收同比
    rev_accel      ：rev_yoy > rev_yoy_prev（营收增速加快）
    revenue_q      ：最近季度营收（美元）
    gross_margin   ：最近季度毛利率
    gm_change      ：毛利率比去年同季度的变化（百分点，0.05 = 提高 5 个点）
    ni_improve     ：最近季度净利润比去年同季度改善（亏损收窄或盈利增加）为 1
    runway_q       ：现金 / 季度亏损额（季度数）；盈利公司记为 inf
    profitable     ：最近季度净利润 > 0
    """
    nan = {"rev_yoy": np.nan, "rev_yoy_prev": np.nan, "rev_accel": np.nan,
           "runway_q": np.nan, "profitable": np.nan, "revenue_q": np.nan,
           "gross_margin": np.nan, "gm_change": np.nan, "ni_improve": np.nan}
    if tab is None or tab.empty:
        return nan
    vis = tab[tab["avail"] <= pd.Timestamp(date)]
    if vis.empty:
        return nan
    out = dict(nan)
    rev = vis["revenue"].dropna()
    if len(rev):
        out["revenue_q"] = float(rev.iloc[-1])
        out["rev_yoy"] = _yoy(rev, rev.index[-1])
        if len(rev) >= 2:
            out["rev_yoy_prev"] = _yoy(rev, rev.index[-2])
        if not np.isnan(out["rev_yoy"]) and not np.isnan(out["rev_yoy_prev"]):
            out["rev_accel"] = float(out["rev_yoy"] > out["rev_yoy_prev"])
    gp = vis["gross_profit"].dropna() if "gross_profit" in vis else pd.Series(dtype=float)
    if len(gp) and len(rev):
        e = gp.index[-1]
        if e in rev.index and rev.loc[e] > 0:
            out["gross_margin"] = float(gp.loc[e] / rev.loc[e])
            prev = _same_q_last_year(gp, e)
            if prev is not None and prev in rev.index and rev.loc[prev] > 0:
                out["gm_change"] = out["gross_margin"] - float(gp.loc[prev] / rev.loc[prev])
    ni = vis["net_income"].dropna()
    cash = vis["cash_total"].dropna()
    if len(ni):
        prev = _same_q_last_year(ni, ni.index[-1])
        if prev is not None:
            out["ni_improve"] = float(ni.iloc[-1] > ni.loc[prev])
    if len(ni):
        last_ni = float(ni.iloc[-1])
        out["profitable"] = float(last_ni > 0)
        if len(cash):
            out["runway_q"] = np.inf if last_ni >= 0 else float(cash.iloc[-1]) / -last_ni
    return out


def _same_q_last_year(s: pd.Series, end):
    target = end - pd.DateOffset(years=1)
    near = s[(s.index >= target - pd.Timedelta(days=20)) & (s.index <= target + pd.Timedelta(days=20))]
    return near.index[-1] if len(near) else None


def _yoy(rev: pd.Series, end) -> float:
    """end 那个季度对去年同季度的增速（允许 ±20 天的期末日期偏差）。"""
    target = end - pd.DateOffset(years=1)
    near = rev[(rev.index >= target - pd.Timedelta(days=20)) & (rev.index <= target + pd.Timedelta(days=20))]
    if near.empty or near.iloc[-1] <= 0:
        return np.nan
    return float(rev.loc[end] / near.iloc[-1] - 1)
