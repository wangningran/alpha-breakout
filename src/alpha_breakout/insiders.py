"""Insider trades (SEC Form 4): open-market buys / sells.

Two steps:
1. form4_index(ciks): get each company's Form 4 list from the submissions API (filing date, accession number, primary document),
   cached in data/cache/form4_index.parquet.
2. form4_details(rows): download each Form 4 XML and parse non-derivative transactions, cached in data/cache/form4_tx.parquet.
   Only transaction codes P (open-market purchase) and S (open-market sale) are kept;
   records transaction date, shares, price, value, filer role (director / officer / 10% owner), and whether it was under a 10b5-1 plan.

SEC limit is 10 req/s; we use about 8 req/s.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
import requests

from . import sec
from .filings import _throttle as _throttle_base


def _throttle():
    _throttle_base(0.11)  # about 9 req/s, under the SEC's 10 req/s

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
    # early filings lack the aff10b5One element; a footnote mentioning 10b5-1 also counts as a plan trade
    if not who["plan_10b5_1"]:
        notes = " ".join((n.text or "") for n in root.findall("footnotes/footnote"))
        who["plan_10b5_1"] = "10b5-1" in notes
    return who, txs


def _fetch_one(row) -> list[dict]:
    cik, acc, doc = int(row.cik), row.accession, row.doc
    raw = doc.split("/")[-1]  # strip the xslF345X0x/ render directory to get the raw XML
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


def form4_details(rows: pd.DataFrame, workers: int = 8, save_every: int = 2000, log=print) -> pd.DataFrame:
    """Download and parse one by one, saving every save_every filings (a rerun after an interruption skips completed ones)."""
    old = pd.read_parquet(TX) if TX.exists() else None
    done = set(old["accession"]) if old is not None else set()
    todo = rows[~rows["accession"].isin(done)].drop_duplicates("accession")
    parts = [old] if old is not None else []
    recs = []

    def flush():
        nonlocal recs
        if recs:
            new = pd.DataFrame(recs).reindex(columns=TX_COLS)
            new["tx_date"] = pd.to_datetime(new["tx_date"], errors="coerce")
            parts.append(new)
            recs = []
        out = pd.concat(parts, ignore_index=True)
        out.to_parquet(TX)
        return out

    with ThreadPoolExecutor(workers) as ex:
        for k, res in enumerate(ex.map(_fetch_one, todo.itertuples())):
            recs += res
            if (k + 1) % save_every == 0:
                flush()
                log(f"  form4 xml {k + 1}/{len(todo)}")
    return flush()


def load_tx() -> pd.DataFrame | None:
    return pd.read_parquet(TX) if TX.exists() else None


def insider_features(tx: pd.DataFrame | None, start, end) -> dict:
    """Value and count of open-market buys / sells in [start, end] (by filing date); discretionary sells (excluding 10b5-1 plans) are counted separately."""
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
