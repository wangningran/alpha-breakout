"""SEC EDGAR 公告列表（submissions API），带本地缓存。

SEC 要求 User-Agent 带联系方式（"名称 邮箱"），通过环境变量 SEC_USER_AGENT 设置。
"""
from __future__ import annotations

import datetime as dt
import json
import os
import time
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = ROOT / "data" / "cache" / "sec"
DEFAULT_UA = "alpha-breakout research contact@example.com"

# 策略关心的表格类型
CATALYST_FORMS = {"8-K", "SC 13D", "SC 13D/A", "SCHEDULE 13D", "SCHEDULE 13D/A"}
DILUTION_FORMS = {"S-3", "S-3ASR", "424B5", "424B3", "S-1", "S-3/A"}


def _get(url: str) -> dict:
    ua = os.environ.get("SEC_USER_AGENT", DEFAULT_UA)
    for attempt in range(4):
        r = requests.get(url, headers={"User-Agent": ua}, timeout=30)
        if r.status_code == 200:
            return r.json()
        time.sleep(2 ** attempt)
    r.raise_for_status()
    return {}


def _cached_json(name: str, url: str, refresh: bool) -> dict:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / name
    fresh = path.exists() and dt.date.fromtimestamp(path.stat().st_mtime) == dt.date.today()
    if refresh or not fresh:
        path.write_text(json.dumps(_get(url)))
        time.sleep(0.2)  # SEC 限速 10 req/s
    return json.loads(path.read_text())


def ticker_to_cik(ticker: str, refresh: bool = False) -> int:
    data = _cached_json("company_tickers.json", "https://www.sec.gov/files/company_tickers.json", refresh)
    for row in data.values():
        if row["ticker"].upper() == ticker.upper():
            return int(row["cik_str"])
    raise KeyError(f"SEC 没有找到 {ticker} 的 CIK")


def filings(ticker: str, refresh: bool = False) -> pd.DataFrame:
    """返回该公司全部公告：filingDate, form, accessionNumber, primaryDocument, items, url。"""
    cik = ticker_to_cik(ticker, refresh)
    sub = _cached_json(f"CIK{cik:010d}.json", f"https://data.sec.gov/submissions/CIK{cik:010d}.json", refresh)
    frames = [pd.DataFrame(sub["filings"]["recent"])]
    for f in sub["filings"].get("files", []):
        frames.append(pd.DataFrame(_cached_json(f["name"], f"https://data.sec.gov/submissions/{f['name']}", refresh)))
    df = pd.concat(frames, ignore_index=True)
    df["filingDate"] = pd.to_datetime(df["filingDate"])
    acc = df["accessionNumber"].str.replace("-", "")
    df["url"] = (f"https://www.sec.gov/Archives/edgar/data/{cik}/" + acc + "/" + df["primaryDocument"])
    cols = ["filingDate", "form", "items", "accessionNumber", "primaryDocument", "url"]
    return df[[c for c in cols if c in df.columns]].sort_values("filingDate").reset_index(drop=True)


def filings_between(df: pd.DataFrame, start, end, forms=None) -> pd.DataFrame:
    m = (df["filingDate"] >= pd.Timestamp(start)) & (df["filingDate"] <= pd.Timestamp(end))
    if forms:
        m &= df["form"].isin(forms)
    return df[m]
