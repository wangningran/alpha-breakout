"""全市场 SEC 公告索引（只要日期、表格类型、8-K item），用于增发过滤和催化剂分类。

缓存：data/cache/filings.parquet，列 [cik, date, form, items]。
来源：data.sec.gov/submissions/CIK##########.json（recent 最多约 1000 条；
不够覆盖起始日期时再拉附加的历史分页）。SEC 限速 10 req/s，这里控制在约 8 req/s。
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

# 增发相关：发行 / 定价说明书（424B1/B4/B5）、新注册（S-1/S-3/F-1/F-3）
OFFERING_FORMS = {"424B1", "424B4", "424B5", "S-1", "S-3", "F-1", "F-3", "S-1/A", "F-1/A"}
# 8-K item → 催化剂类别
ITEM_CATEGORY = [
    ("1.01", "agreement"),     # 重大协议（合作、授权、并购协议等）
    ("2.01", "acquisition"),   # 完成收购 / 处置
    ("2.02", "earnings"),      # 财报
    ("8.01", "other_event"),   # 其他重大事件（临床数据、产品等常放这里）
    ("7.01", "reg_fd"),        # Reg FD 披露（新闻稿、路演材料）
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
    except Exception as e:  # 个别公司失败不影响整体，下次运行会补
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
    """异动日前后公告的类别（按 ITEM_CATEGORY 的优先级），无公告返回 none。"""
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
    """异动前 before_days 天到 until（通常是试仓信号日）之间的大股东申报次数。

    new_13d  ：新的 13D（持股 ≥5% 且可能谋求影响公司，主动型）
    amend_13d：13D 修订（增持、减持或意图变化，方向需要看正文）
    new_13g  ：新的 13G（持股 ≥5% 的被动投资者，比如基金首次举牌）
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
    """重新拉指定公司的公告（每日扫描时只刷新观察名单里的公司），合并进缓存。"""
    ciks = [int(c) for c in ciks]
    old = load()
    parts = [old[~old["cik"].isin(ciks)]] if old is not None else []
    for c in ciks:
        parts.append(_safe(c, since))
    out = _save(parts)
    log(f"filings refreshed for {len(ciks)} companies")
    return out
