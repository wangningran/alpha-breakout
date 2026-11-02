"""Point-in-time fundamentals (SEC XBRL frames API, fetched for the whole market by quarter).

One request per concept per quarter returns the values for the whole market:
  revenue (quarterly): Revenues -> RevenueFromContractWithCustomerExcludingAssessedTax -> SalesRevenueNet, in priority order
  net income (quarterly): NetIncomeLoss; the loss is a proxy for "burn rate" (includes non-cash items, conservative)
  gross profit (quarterly): GrossProfit (many companies do not report it separately; often missing)
  cash (point in time): CashAndCashEquivalentsAtCarryingValue (falls back to the measure including restricted cash)
              + short-term investments / marketable securities (biotechs keep much of their cash here)

No look-ahead: every value counts as available only LAG_DAYS days after the period end (10-Q usually filed within 40-45 days,
10-K within 60-90 days; a flat 75 days is used here, conservative).

Cache: data/cache/fundamentals.parquet, columns [cik, concept, end, val, avail].
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
                if key == "rev_3" and y >= 2020:  # SalesRevenueNet is basically retired after 2019 and the frame does not exist; skip to avoid repeated 404s
                    continue
                period = f"CY{y}Q{q}" + ("I" if key in INSTANT else "")
                try:
                    d = sec._cached_json(f"frame_{concept}_{period}.json",
                                         URL.format(concept=concept, period=period), refresh, static=not recent)
                except Exception as e:  # some concepts have no frame in early quarters; just skip
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
    """Reshape one company's long table into a wide table by period end: revenue, net_income, cash_total."""
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
    """Fundamental features visible as of `date`. Missing fields are NaN.

    rev_yoy        : revenue YoY growth for the latest quarter
    rev_yoy_prev   : revenue YoY growth for the previous quarter
    rev_accel      : rev_yoy > rev_yoy_prev (revenue growth accelerating)
    revenue_q      : latest quarterly revenue (USD)
    gross_margin   : latest quarterly gross margin
    gm_change      : gross margin change vs the same quarter last year (percentage points, 0.05 = up 5 points)
    ni_improve     : 1 if latest quarterly net income improved vs the same quarter last year (smaller loss or larger profit)
    runway_q       : cash / quarterly loss (in quarters); inf for profitable companies
    profitable     : latest quarterly net income > 0
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
    """Growth of the `end` quarter vs the same quarter last year (allows +/-20 days of period-end mismatch)."""
    target = end - pd.DateOffset(years=1)
    near = rev[(rev.index >= target - pd.Timedelta(days=20)) & (rev.index <= target + pd.Timedelta(days=20))]
    if near.empty or near.iloc[-1] <= 0:
        return np.nan
    return float(rev.loc[end] / near.iloc[-1] - 1)
