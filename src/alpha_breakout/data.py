"""Daily price fetching (Yahoo Finance / yfinance) with a local parquet cache.

Cache files: data/cache/prices/<TICKER>.parquet, storing the raw Yahoo fields
(split-adjusted OHLC, Adj Close, Volume, Stock Splits).
- get_daily(): single ticker; repeat calls on the same day read the cache; refresh=True forces a re-download.
- download_bulk(): bulk download for the whole market; tickers already cached are skipped (unless refresh).
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = ROOT / "data" / "cache" / "prices"

BENCHMARKS = ("XBI", "IWM")
COLUMNS = ["Open", "High", "Low", "Close", "Adj Close", "Volume", "Stock Splits"]


def _cache_path(ticker: str) -> Path:
    return CACHE_DIR / f"{ticker.upper().replace('/', '_')}.parquet"


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    df = df.reindex(columns=COLUMNS).copy()
    df["Stock Splits"] = df["Stock Splits"].fillna(0.0)
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    df.index.name = "Date"
    return df.dropna(subset=["Close"]).astype("float64")


def _download(ticker: str, start: str | None = None) -> pd.DataFrame:
    import yfinance as yf

    kw = {"start": start} if start else {"period": "max"}
    df = yf.download(ticker, interval="1d", auto_adjust=False, actions=True,
                     progress=False, threads=False, **kw)
    if df is None or df.empty:
        raise RuntimeError(f"yfinance returned no data for {ticker}")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return _clean(df)


def download_bulk(tickers, start: str = "2018-06-01", batch: int = 200,
                  refresh: bool = False, log=print) -> list[str]:
    """Bulk download and write the cache; returns the list of tickers that succeeded."""
    import yfinance as yf

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    todo = [t for t in tickers if refresh or not _cache_path(t).exists()]
    ok = [t for t in tickers if t not in set(todo)]
    for i in range(0, len(todo), batch):
        chunk = todo[i: i + batch]
        try:
            df = yf.download(chunk, start=start, interval="1d", auto_adjust=False, actions=True,
                             progress=False, threads=True, group_by="ticker")
        except Exception as e:  # log a failed batch; the next run fills it in
            log(f"[warn] batch {i}: {e}")
            continue
        got = set(df.columns.get_level_values(0)) if isinstance(df.columns, pd.MultiIndex) else set()
        for t in chunk:
            if t not in got:
                continue
            sub = _clean(df[t])
            if len(sub) < 30:
                continue
            sub.to_parquet(_cache_path(t))
            ok.append(t)
        log(f"  {min(i + batch, len(todo))}/{len(todo)} downloaded, {len(ok)} ok")
    return ok


def load_raw(ticker: str) -> pd.DataFrame | None:
    p = _cache_path(ticker)
    return pd.read_parquet(p) if p.exists() else None


def get_daily(ticker: str, start: str | None = None, end: str | None = None,
              refresh: bool = False) -> pd.DataFrame:
    """Return daily bars in [start, end] (DatetimeIndex); see adjust() for the fields."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _cache_path(ticker)
    fresh = path.exists() and dt.date.fromtimestamp(path.stat().st_mtime) == dt.date.today()
    if refresh or not fresh:
        try:
            _download(ticker).to_parquet(path)
        except Exception:
            if not path.exists():
                raise
    df = adjust(pd.read_parquet(path))
    if start:
        df = df[df.index >= pd.Timestamp(start)]
    if end:
        df = df[df.index <= pd.Timestamp(end)]
    return df


def adjust(raw: pd.DataFrame) -> pd.DataFrame:
    """Output fields:
    - Open/High/Low/Close: adjusted prices (splits + dividends), used for returns and indicators;
    - Volume: split-adjusted share count;
    - RawClose: actual traded price at the time (split adjustment undone), used for price thresholds and market cap estimates.
    """
    factor = (raw["Adj Close"] / raw["Close"]).fillna(1.0)
    out = pd.DataFrame(index=raw.index)
    for col in ("Open", "High", "Low", "Close"):
        out[col] = raw[col] * factor
    out["Volume"] = raw["Volume"]
    splits = raw["Stock Splits"].replace(0, np.nan).fillna(1.0) if "Stock Splits" in raw else pd.Series(1.0, raw.index)
    # product of all split ratios after a given day: actual price = split-adjusted price x that product
    after = splits[::-1].cumprod()[::-1].shift(-1).fillna(1.0)
    out["RawClose"] = raw["Close"] * after
    return out


def get_many(tickers, start=None, end=None, refresh=False) -> dict[str, pd.DataFrame]:
    return {t: get_daily(t, start, end, refresh) for t in tickers}


def get_benchmarks(start=None, end=None, refresh=False) -> dict[str, pd.DataFrame]:
    return get_many(BENCHMARKS, start, end, refresh)


def update_recent(tickers, period: str = "1mo", batch: int = 200, log=print) -> int:
    """Incremental update: fetch recent bars and merge them into the existing cache (new data overwrites overlapping dates). Returns the number of tickers updated."""
    import yfinance as yf

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tickers = list(tickers)
    updated = 0
    for i in range(0, len(tickers), batch):
        chunk = tickers[i: i + batch]
        try:
            df = yf.download(chunk, period=period, interval="1d", auto_adjust=False, actions=True,
                             progress=False, threads=True, group_by="ticker")
        except Exception as e:
            log(f"[warn] update batch {i}: {e}")
            continue
        got = set(df.columns.get_level_values(0)) if isinstance(df.columns, pd.MultiIndex) else set()
        for t in chunk:
            if t not in got:
                continue
            new = _clean(df[t])
            if new.empty:
                continue
            old = load_raw(t)
            if old is not None:
                # a new split not yet in the cache: Yahoo rewrites all historical prices, so re-download the whole range
                split_days = new.index[new["Stock Splits"] > 0]
                if any(d not in old.index or old.at[d, "Stock Splits"] == 0 for d in split_days):
                    try:
                        _download(t, start=str(old.index.min().date())).to_parquet(_cache_path(t))
                        updated += 1
                    except Exception as e:
                        log(f"[warn] split refresh {t}: {e}")
                    continue
                new = pd.concat([old[old.index < new.index.min()], new])
            new.to_parquet(_cache_path(t))
            updated += 1
        log(f"  updated {min(i + batch, len(tickers))}/{len(tickers)}")
    return updated
