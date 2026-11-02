"""Market-wide SEC filing index (dates, form types and 8-K items only), used for the offering filter and catalyst classification.

Cache: data/cache/filings.parquet, columns [cik, date, form, items].
Source: data.sec.gov/submissions/CIK##########.json (recent holds about 1000 filings at most;
when that does not reach the start date, the extra history pages are fetched too). SEC limit is 10 req/s; we stay around 8 req/s.
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

from . import sec

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "data" / "cache" / "filings.parquet"

# Offering-related: offering / pricing prospectuses (424B1/B4/B5), new registrations (S-1/S-3/F-1/F-3)
OFFERING_FORMS = {"424B1", "424B4", "424B5", "S-1", "S-3", "F-1", "F-3", "S-1/A", "F-1/A"}
# 8-K item -> catalyst category
ITEM_CATEGORY = [
    ("1.01", "agreement"),     # material agreement (partnership, licensing, M&A agreement, etc.)
    ("2.01", "acquisition"),   # completed acquisition / disposition
    ("2.02", "earnings"),      # earnings
    ("8.01", "other_event"),   # other material events (clinical data, products, etc. often go here)
    ("7.01", "reg_fd"),        # Reg FD disclosure (press releases, roadshow materials)
]

_lock = threading.Lock()
_last = [0.0]


def _throttle(min_interval: float = 0.125):
    with _lock:
        wait = _last[0] + min_interval - time.time()
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()


def _fetch_one(cik: int, since: str) -> pd.DataFrame:
    _throttle()
    d = sec._get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json")
    f = d.get("filings", {})
    frames = [pd.DataFrame(f.get("recent", {}))]
    for extra in f.get("files", []):
        if extra.get("filingTo", "9999") < since:
            continue
        _throttle()
        frames.append(pd.DataFrame(sec._get(f"https://data.sec.gov/submissions/{extra['name']}")))
    df = pd.concat(frames, ignore_index=True)
    if df.empty:
        return pd.DataFrame(columns=["cik", "date", "form", "items"])
    df = df[df["filingDate"] >= since]
    return pd.DataFrame({"cik": cik, "date": pd.to_datetime(df["filingDate"]),
                         "form": df["form"].astype(str),
                         "items": df.get("items", pd.Series("", index=df.index)).fillna("").astype(str)})


def fetch_all(ciks, since: str = "2018-06-01", refresh: bool = False, workers: int = 4, log=print) -> pd.DataFrame:
    old = pd.read_parquet(CACHE) if CACHE.exists() and not refresh else None
    have = set(old["cik"].unique()) if old is not None else set()
    todo = [int(c) for c in ciks if int(c) not in have]
    parts = [old] if old is not None else []
    done = 0
    with ThreadPoolExecutor(workers) as ex:
        for res in ex.map(lambda c: _safe(c, since), todo):
            parts.append(res)
            done += 1
            if done % 500 == 0:
                log(f"  filings {done}/{len(todo)}")
                _save(parts)
    out = _save(parts)
    log(f"filings: {out['cik'].nunique()} companies, {len(out)} rows")
    return out


def _safe(cik, since):
    try:
        return _fetch_one(cik, since)
    except Exception as e:  # one company failing does not affect the rest; the next run fills it in
        print(f"[warn] filings CIK {cik}: {e}")
        return pd.DataFrame(columns=["cik", "date", "form", "items"])


def _save(parts) -> pd.DataFrame:
    out = pd.concat([p for p in parts if p is not None and len(p)], ignore_index=True)
    out["cik"] = out["cik"].astype("int64")
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(CACHE)
    return out


def load() -> pd.DataFrame | None:
    return pd.read_parquet(CACHE) if CACHE.exists() else None


def catalyst_label(f: pd.DataFrame, surge_date, before_days: int = 3, after_days: int = 1) -> str:
    """Category of filings around the surge date (by ITEM_CATEGORY priority); returns none if there are none."""
    if f is None or f.empty:
        return "none"
    lo, hi = pd.Timestamp(surge_date) - pd.Timedelta(days=before_days), pd.Timestamp(surge_date) + pd.Timedelta(days=after_days)
    w = f[(f["date"] >= lo) & (f["date"] <= hi)]
    if w.empty:
        return "none"
    if w["form"].isin(OFFERING_FORMS).any():
        return "offering"
    if w["form"].str.startswith("SC 13D").any() or w["form"].str.startswith("SCHEDULE 13D").any():
        return "13d_stake"
    items = ",".join(w.loc[w["form"].str.startswith("8-K"), "items"])
    for code, name in ITEM_CATEGORY:
        if code in items:
            return name
    return "other_filing" if w["form"].str.startswith("8-K").any() else "none"


NEW_13D = {"SC 13D", "SCHEDULE 13D"}
AMEND_13D = {"SC 13D/A", "SCHEDULE 13D/A"}
NEW_13G = {"SC 13G", "SCHEDULE 13G"}


def holder_features(f: pd.DataFrame | None, surge_date, until, before_days: int = 30) -> dict:
    """Number of major-holder filings from before_days before the surge until `until` (usually the probe signal date).

    new_13d  : new 13D (>=5% stake, may seek to influence the company; activist)
    amend_13d: 13D amendment (stake increase, decrease or change of intent; the direction requires reading the text)
    new_13g  : new 13G (>=5% passive investor, e.g. a fund's first disclosure)
    """
    out = {"new_13d": 0, "amend_13d": 0, "new_13g": 0}
    if f is None or f.empty:
        return out
    lo = pd.Timestamp(surge_date) - pd.Timedelta(days=before_days)
    w = f[(f["date"] >= lo) & (f["date"] <= pd.Timestamp(until))]
    out["new_13d"] = int(w["form"].isin(NEW_13D).sum())
    out["amend_13d"] = int(w["form"].isin(AMEND_13D).sum())
    out["new_13g"] = int(w["form"].isin(NEW_13G).sum())
    return out


def refresh_companies(ciks, since: str = "2018-06-01", log=print) -> pd.DataFrame:
    """Re-fetch filings for the given companies (the daily scan only refreshes companies on the watch list) and merge them into the cache."""
    ciks = [int(c) for c in ciks]
    old = load()
    parts = [old[~old["cik"].isin(ciks)]] if old is not None else []
    for c in ciks:
        parts.append(_safe(c, since))
    out = _save(parts)
    log(f"filings refreshed for {len(ciks)} companies")
    return out


def earnings_dates(f: pd.DataFrame | None) -> pd.DatetimeIndex:
    """Historical earnings release dates: 8-K with item 2.02 (Results of Operations), deduplicated and sorted."""
    if f is None or f.empty:
        return pd.DatetimeIndex([])
    m = f["form"].str.startswith("8-K") & f["items"].str.contains("2.02", regex=False)
    return pd.DatetimeIndex(sorted(set(f.loc[m, "date"])))


def days_to_next_earnings(ed: pd.DatetimeIndex, date, default_gap: int = 91) -> float:
    """Project the next earnings date from those known as of `date` and return the calendar days until it (negative = expected date passed but not yet reported).

    Expected gap = median of the last 4 earnings gaps (91 days if there are too few). Returns NaN if there are no past earnings.
    """
    date = pd.Timestamp(date)
    past = ed[ed <= date]
    if len(past) == 0:
        return float("nan")
    gaps = pd.Series(past[-5:]).diff().dt.days.dropna()
    gaps = gaps[(gaps > 45) & (gaps < 200)]  # drop duplicate 2.02s within the same quarter (corrections, supplements)
    gap = float(gaps.median()) if len(gaps) else default_gap
    return float((past[-1] + pd.Timedelta(days=gap) - date).days)
