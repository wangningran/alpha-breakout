"""全市场股票池 + 按时点估算的市值。

- 代码列表：SEC company_tickers_exchange.json，只留 Nasdaq / NYSE 的普通股
  （去掉权证、单位、优先股等）。只包含目前仍在交易的公司，不含退市股。
- 股本：SEC XBRL frames API，按季度拉全市场一次，得到每家公司的股本时间序列。
  主来源 dei:EntityCommonStockSharesOutstanding（封面股本，截止日≈申报日，滞后 5 天可见）；
  没有封面股本的公司（多类股等）用 us-gaap:WeightedAverageNumberOfSharesOutstandingBasic
  （季度加权平均股本，期末后 45 天才可见）补上。
- 市值 ≈ 当日真实收盘价（RawClose）× 当时最近一次披露的股本。
"""
from __future__ import annotations

import pandas as pd

from . import sec

FRAME_URL = "https://data.sec.gov/api/xbrl/frames/dei/EntityCommonStockSharesOutstanding/shares/{}.json"
FRAME_URL_WA = "https://data.sec.gov/api/xbrl/frames/us-gaap/WeightedAverageNumberOfSharesOutstandingBasic/shares/{}.json"
LAG_DAYS = {"dei": 5, "wavg": 45}
EXCHANGES = {"Nasdaq", "NYSE"}


def listed_common_stocks(refresh: bool = False) -> pd.DataFrame:
    d = sec._cached_json("company_tickers_exchange.json",
                         "https://www.sec.gov/files/company_tickers_exchange.json", refresh)
    df = pd.DataFrame(d["data"], columns=d["fields"])
    df = df[df["exchange"].isin(EXCHANGES) & df["ticker"].notna()]
    t = df["ticker"].str.upper()
    bad = (
        t.str.contains(r"[-./^ ]", regex=True)                                   # 优先股 / 分级 / 权证 (NYSE 写法)
        | ((t.str.len() == 5) & t.str[-1].isin(list("WURZ")))                    # Nasdaq 五字母权证 / 单位 / 权利
        | df["name"].str.contains(r"\b(?:ETF|Fund|Trust)\b", case=False, regex=True)
    )
    df = df[~bad].assign(ticker=t)
    return df.drop_duplicates("ticker").drop_duplicates("cik").reset_index(drop=True)


def shares_history(start_year: int = 2018, end: pd.Timestamp | None = None,
                   refresh: bool = False) -> pd.DataFrame:
    """返回 [cik, end, shares, avail]，avail = 这条股本数据可以被使用的最早日期。"""
    end = end or pd.Timestamp.today()
    rows = []
    for y in range(start_year, end.year + 1):
        for q in range(1, 5):
            if pd.Timestamp(year=y, month=3 * q, day=1) > end:
                break
            recent = (end - pd.Timestamp(year=y, month=3 * q, day=1)).days < 200
            for src, name, url in (("dei", f"CY{y}Q{q}I", FRAME_URL), ("wavg", f"CY{y}Q{q}", FRAME_URL_WA)):
                try:
                    d = sec._cached_json(f"frame_{src}_{name}.json", url.format(name), refresh, static=not recent)
                except Exception as e:
                    print(f"[warn] SEC frame {src} {name}: {e}")
                    continue
                rows += [(r["cik"], r["end"], r["val"], src) for r in d.get("data", [])]
    out = pd.DataFrame(rows, columns=["cik", "end", "shares", "src"])
    out["end"] = pd.to_datetime(out["end"])
    has_dei = set(out.loc[out["src"] == "dei", "cik"])
    out = out[(out["src"] == "dei") | ~out["cik"].isin(has_dei)]
    out["avail"] = out["end"] + pd.to_timedelta(out["src"].map(LAG_DAYS), unit="D")
    return out.drop_duplicates(["cik", "end"]).sort_values(["cik", "end"]).reset_index(drop=True)


def shares_series(sh: pd.DataFrame, cik: int, index: pd.DatetimeIndex) -> pd.Series:
    """把某公司的股本对齐到交易日：只用 avail 日期之前已经可见的最近值（避免前视）。"""
    s = sh[sh["cik"] == cik]
    if s.empty:
        return pd.Series(float("nan"), index=index)
    s = pd.Series(s["shares"].values, index=s["avail"])
    s = s[~s.index.duplicated(keep="last")].sort_index()
    return s.reindex(index.union(s.index)).ffill().reindex(index)
