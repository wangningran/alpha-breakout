"""日线行情拉取（Yahoo Finance / yfinance），带本地 CSV 缓存。

缓存文件：data/cache/<TICKER>.csv，保存未复权 OHLC + Adj Close + Volume。
同一天内重复调用直接读缓存；传 refresh=True 强制重拉。
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = ROOT / "data" / "cache"

BENCHMARKS = ("XBI", "IWM")
COLUMNS = ["Open", "High", "Low", "Close", "Adj Close", "Volume"]


def _cache_path(ticker: str) -> Path:
    return CACHE_DIR / f"{ticker.upper().replace('/', '_')}.csv"


def _download(ticker: str) -> pd.DataFrame:
    import yfinance as yf

    df = yf.download(
        ticker,
        period="max",
        interval="1d",
        auto_adjust=False,
        actions=False,
        progress=False,
        threads=False,
    )
    if df is None or df.empty:
        raise RuntimeError(f"yfinance 没有返回 {ticker} 的数据")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df[COLUMNS].copy()
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    df.index.name = "Date"
    return df.dropna(subset=["Close"])


def get_daily(ticker: str, start: str | None = None, end: str | None = None,
              refresh: bool = False) -> pd.DataFrame:
    """返回 [start, end] 区间的日线 OHLCV（DatetimeIndex）。

    口径：Yahoo 的 Close/Volume 已做拆股调整；这里再用 Adj Close / Close
    把 Open/High/Low/Close 调成含分红的复权价（小盘生物科技基本不分红，影响很小）。
    注意：拆股调整后的历史价格不等于当时真实成交价，反向拆股的票用 price>2 过滤时要留意。
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _cache_path(ticker)
    fresh = path.exists() and dt.date.fromtimestamp(path.stat().st_mtime) == dt.date.today()
    if refresh or not fresh:
        try:
            _download(ticker).to_csv(path)
        except Exception:
            if not path.exists():
                raise
    df = pd.read_csv(path, index_col="Date", parse_dates=True)
    df = adjust(df)
    if start:
        df = df[df.index >= pd.Timestamp(start)]
    if end:
        df = df[df.index <= pd.Timestamp(end)]
    return df


def adjust(raw: pd.DataFrame) -> pd.DataFrame:
    factor = (raw["Adj Close"] / raw["Close"]).fillna(1.0)
    out = pd.DataFrame(index=raw.index)
    for col in ("Open", "High", "Low", "Close"):
        out[col] = raw[col] * factor
    out["Volume"] = raw["Volume"]
    return out


def get_many(tickers, start=None, end=None, refresh=False) -> dict[str, pd.DataFrame]:
    return {t: get_daily(t, start, end, refresh) for t in tickers}


def get_benchmarks(start=None, end=None, refresh=False) -> dict[str, pd.DataFrame]:
    return get_many(BENCHMARKS, start, end, refresh)
