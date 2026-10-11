"""内部人交易（SEC Form 4）：公开市场买入 / 卖出。

两步：
1. form4_index(ciks)：从 submissions API 拿到每家公司的 Form 4 列表（申报日、文件编号、主文档），
   缓存 data/cache/form4_index.parquet。
2. form4_details(rows)：逐份下载 Form 4 XML，解析非衍生品交易，缓存 data/cache/form4_tx.parquet。
   只保留交易代码 P（公开市场买入）和 S（公开市场卖出）；
   记录交易日、股数、价格、金额、申报人身份（董事 / 高管 / 10% 股东）、是否按 10b5-1 计划交易。

SEC 限速 10 req/s，这里约 8 req/s。
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
import requests

from . import sec
from .filings import _throttle

ROOT = Path(__file__).resolve().parents[2]
INDEX = ROOT / "data" / "cache" / "form4_index.parquet"
TX = ROOT / "data" / "cache" / "form4_tx.parquet"
TX_COLS = ["cik", "accession", "filing_date", "tx_date", "code", "shares", "price", "value",
           "is_director", "is_officer", "is_10pct", "plan_10b5_1"]


def _index_one(cik: int, since: str) -> pd.DataFrame:
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
    if df.empty or "form" not in df:
        return pd.DataFrame(columns=["cik", "filing_date", "accession", "doc"])
    df = df[(df["form"] == "4") & (df["filingDate"] >= since)]
    return pd.DataFrame({"cik": cik, "filing_date": pd.to_datetime(df["filingDate"]),
                         "accession": df["accessionNumber"], "doc": df["primaryDocument"]})


def form4_index(ciks, since: str = "2019-01-01", workers: int = 4, log=print) -> pd.DataFrame:
    old = pd.read_parquet(INDEX) if INDEX.exists() else None
    have = set(old["cik"].unique()) if old is not None else set()
    todo = [int(c) for c in ciks if int(c) not in have]
    parts = [old] if old is not None else []

    def safe(c):
        try:
            return _index_one(c, since)
        except Exception as e:
            print(f"[warn] form4 index CIK {c}: {e}")
            return pd.DataFrame(columns=["cik", "filing_date", "accession", "doc"])

    with ThreadPoolExecutor(workers) as ex:
        for k, res in enumerate(ex.map(safe, todo)):
            parts.append(res)
            if (k + 1) % 500 == 0:
                log(f"  form4 index {k + 1}/{len(todo)}")
    out = pd.concat([p for p in parts if p is not None and len(p)], ignore_index=True)
    out["cik"] = out["cik"].astype("int64")
    INDEX.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(INDEX)
    return out


def _txt(el, path):
    x = el.find(path)
    return x.text.strip() if x is not None and x.text else None


def parse_form4(xml_text: str) -> tuple[dict, list[dict]]:
    root = ET.fromstring(xml_text)
    rel = root.find("reportingOwner/reportingOwnerRelationship")
    who = {
        "is_director": _txt(rel, "isDirector") in ("1", "true") if rel is not None else False,
        "is_officer": _txt(rel, "isOfficer") in ("1", "true") if rel is not None else False,
        "is_10pct": _txt(rel, "isTenPercentOwner") in ("1", "true") if rel is not None else False,
        "plan_10b5_1": _txt(root, "aff10b5One") in ("1", "true"),
    }
    txs = []
    for t in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
        code = _txt(t, "transactionCoding/transactionCode")
        if code not in ("P", "S"):
            continue
        sh = float(_txt(t, "transactionAmounts/transactionShares/value") or 0)
        px = float(_txt(t, "transactionAmounts/transactionPricePerShare/value") or 0)
        txs.append({"tx_date": _txt(t, "transactionDate/value"), "code": code,
                    "shares": sh, "price": px, "value": sh * px})
    # 早期没有 aff10b5One 元素，脚注里提到 10b5-1 也算计划内交易
    if not who["plan_10b5_1"]:
        notes = " ".join((n.text or "") for n in root.findall("footnotes/footnote"))
        who["plan_10b5_1"] = "10b5-1" in notes
    return who, txs


def _fetch_one(row) -> list[dict]:
    cik, acc, doc = int(row.cik), row.accession, row.doc
    raw = doc.split("/")[-1]  # 去掉 xslF345X0x/ 渲染目录，拿原始 XML
    url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/{raw}"
    _throttle()
    import os
    r = requests.get(url, headers={"User-Agent": os.environ.get("SEC_USER_AGENT", sec.DEFAULT_UA)}, timeout=30)
    if r.status_code != 200:
        return [{"cik": cik, "accession": acc, "filing_date": row.filing_date, "code": "ERR"}]
    try:
        who, txs = parse_form4(r.text)
    except ET.ParseError:
        return [{"cik": cik, "accession": acc, "filing_date": row.filing_date, "code": "ERR"}]
    if not txs:
        return [{"cik": cik, "accession": acc, "filing_date": row.filing_date, "code": "NONE", **who}]
    return [{"cik": cik, "accession": acc, "filing_date": row.filing_date, **who, **t} for t in txs]


def form4_details(rows: pd.DataFrame, workers: int = 4, log=print) -> pd.DataFrame:
    old = pd.read_parquet(TX) if TX.exists() else None
    done = set(old["accession"]) if old is not None else set()
    todo = rows[~rows["accession"].isin(done)].drop_duplicates("accession")
    recs = []
    with ThreadPoolExecutor(workers) as ex:
        for k, res in enumerate(ex.map(_fetch_one, todo.itertuples())):
            recs += res
            if (k + 1) % 1000 == 0:
                log(f"  form4 xml {k + 1}/{len(todo)}")
    new = pd.DataFrame(recs).reindex(columns=TX_COLS)
    out = pd.concat([old, new], ignore_index=True) if old is not None else new
    out["tx_date"] = pd.to_datetime(out["tx_date"], errors="coerce")
    out.to_parquet(TX)
    return out


def load_tx() -> pd.DataFrame | None:
    return pd.read_parquet(TX) if TX.exists() else None


def insider_features(tx: pd.DataFrame | None, start, end) -> dict:
    """[start, end] 期间（按申报日）公开市场买卖的金额和笔数；排除 10b5-1 计划内卖出另算。"""
    out = {"ins_sell_value": 0.0, "ins_sell_n": 0, "ins_sell_value_discr": 0.0,
           "ins_buy_value": 0.0, "ins_buy_n": 0}
    if tx is None or tx.empty:
        return out
    w = tx[(tx["filing_date"] >= pd.Timestamp(start)) & (tx["filing_date"] <= pd.Timestamp(end))]
    s = w[w["code"] == "S"]
    b = w[w["code"] == "P"]
    out["ins_sell_value"] = float(s["value"].sum())
    out["ins_sell_n"] = int(s["accession"].nunique())
    out["ins_sell_value_discr"] = float(s.loc[~s["plan_10b5_1"].fillna(False).astype(bool), "value"].sum())
    out["ins_buy_value"] = float(b["value"].sum())
    out["ins_buy_n"] = int(b["accession"].nunique())
    return out
