"""Market-wide stock universe + point-in-time market cap estimates.

- Ticker list: SEC company_tickers_exchange.json, keeping only Nasdaq / NYSE common stock
  (warrants, units, preferred shares, etc. removed). Only currently traded companies; delisted stocks are not included.
- Shares outstanding: SEC XBRL frames API, fetched for the whole market once per quarter, giving each company's share count over time.
  Primary source dei:EntityCommonStockSharesOutstanding (cover-page shares, as-of date ~ filing date, visible after a 5-day lag);
  companies without cover-page shares (multiple share classes, etc.) are filled in from us-gaap:WeightedAverageNumberOfSharesOutstandingBasic
  (quarterly weighted average shares, visible 45 days after period end).
- Market cap ~ actual close of the day (RawClose) x the most recently disclosed share count at that time.
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
        t.str.contains(r"[-./^ ]", regex=True)                                   # preferred / class shares / warrants (NYSE notation)
        | ((t.str.len() == 5) & t.str[-1].isin(list("WURZ")))                    # Nasdaq five-letter warrants / units / rights
        | df["name"].str.contains(r"\b(?:ETF|Fund|Trust)\b", case=False, regex=True)
    )
    df = df[~bad].assign(ticker=t)
    return df.drop_duplicates("ticker").drop_duplicates("cik").reset_index(drop=True)


def shares_history(start_year: int = 2018, end: pd.Timestamp | None = None,
                   refresh: bool = False) -> pd.DataFrame:
    """Return [cik, end, shares, avail]; avail = earliest date this share count may be used."""
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
    """Align a company's share count to trading days, using only the latest value already visible by its avail date (no look-ahead)."""
    s = sh[sh["cik"] == cik]
    if s.empty:
        return pd.Series(float("nan"), index=index)
    s = pd.Series(s["shares"].values, index=s["avail"])
    s = s[~s.index.duplicated(keep="last")].sort_index()
    return s.reindex(index.union(s.index)).ffill().reindex(index)
