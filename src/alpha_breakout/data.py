"""日线行情拉取（Yahoo Finance / yfinance），带本地 parquet 缓存。

缓存文件：data/cache/prices/<TICKER>.parquet，保存 Yahoo 原始字段
（拆股调整后的 OHLC、Adj Close、Volume、Stock Splits）。
- get_daily()：单支票，同一天内重复调用直接读缓存；refresh=True 强制重拉。
- download_bulk()：全市场批量下载，已有缓存的跳过（除非 refresh）。
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
        raise RuntimeError(f"yfinance 没有返回 {ticker} 的数据")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return _clean(df)


def download_bulk(tickers, start: str = "2018-06-01", batch: int = 200,
                  refresh: bool = False, log=print) -> list[str]:
    """批量下载并写缓存，返回成功的代码列表。"""
    import yfinance as yf

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    todo = [t for t in tickers if refresh or not _cache_path(t).exists()]
    ok = [t for t in tickers if t not in set(todo)]
    for i in range(0, len(todo), batch):
        chunk = todo[i: i + batch]
        try:
            df = yf.download(chunk, start=start, interval="1d", auto_adjust=False, actions=True,
                             progress=False, threads=True, group_by="ticker")
        except Exception as e:  # 整批失败就记一下，下一次运行会补
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
    """返回 [start, end] 区间的日线（DatetimeIndex），见 adjust() 的字段说明。"""
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
    """输出字段：
    - Open/High/Low/Close：复权价（拆股 + 分红），用来算收益和指标；
    - Volume：拆股调整后的股数；
    - RawClose：当时真实成交价（把拆股调整还原），用来做价格门槛和估算市值。
    """
    factor = (raw["Adj Close"] / raw["Close"]).fillna(1.0)
    out = pd.DataFrame(index=raw.index)
    for col in ("Open", "High", "Low", "Close"):
        out[col] = raw[col] * factor
    out["Volume"] = raw["Volume"]
    splits = raw["Stock Splits"].replace(0, np.nan).fillna(1.0) if "Stock Splits" in raw else pd.Series(1.0, raw.index)
    # 某日之后所有拆股比例的乘积：真实价 = 拆股调整价 × 该乘积
    after = splits[::-1].cumprod()[::-1].shift(-1).fillna(1.0)
    out["RawClose"] = raw["Close"] * after
    return out


def get_many(tickers, start=None, end=None, refresh=False) -> dict[str, pd.DataFrame]:
    return {t: get_daily(t, start, end, refresh) for t in tickers}


def get_benchmarks(start=None, end=None, refresh=False) -> dict[str, pd.DataFrame]:
    return get_many(BENCHMARKS, start, end, refresh)


def update_recent(tickers, period: str = "1mo", batch: int = 200, log=print) -> int:
    """增量更新：拉最近一段行情，合并进已有缓存（新数据覆盖重叠日期）。返回更新的代码数。"""
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
                # 缓存里还没有的新拆股：Yahoo 会改写全部历史价格，这种情况直接整段重拉
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
