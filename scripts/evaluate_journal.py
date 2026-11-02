"""Evaluate the research journal: compare how the names you marked "do" and "skip" performed afterwards.

Usage: python scripts/evaluate_journal.py [--journal journal/research_log.csv]

For each entry, compute the return 5 / 20 / 60 trading days after the open of the day after the signal,
plus the maximum gain and maximum drawdown over that window.
Three groups are summarized:
- all rule signals: every journal entry, whether or not you filled in decision (= the rule's own forward performance)
- do / skip: entries where you filled in decision (= whether human judgment adds value)
Horizons without enough data are left blank. Run daily_scan.py --no-update first to refresh prices.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from alpha_breakout.data import adjust, load_raw  # noqa: E402


def outcome(ticker: str, signal_date) -> dict:
    raw = load_raw(ticker)
    if raw is None:
        return {}
    px = adjust(raw)
    i = px.index.searchsorted(pd.Timestamp(signal_date), side="right")  # the trading day after the signal
    if i >= len(px):
        return {}
    entry = px["Open"].iloc[i]
    res = {"entry_open": entry}
    for n in (5, 20, 60):
        res[f"ret_{n}d"] = px["Close"].iloc[i + n] / entry - 1 if i + n < len(px) else np.nan
    fut = px["Close"].iloc[i: i + 61]
    res["max_gain_60d"] = fut.max() / entry - 1
    res["max_dd_60d"] = fut.min() / entry - 1
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--journal", default=str(ROOT / "journal" / "research_log.csv"))
    a = ap.parse_args()
    if not Path(a.journal).exists():
        print("No research journal yet: run scripts/daily_scan.py; it is created once a probe candidate appears.")
        return
    j = pd.read_csv(a.journal)
    if j.empty:
        print("The journal is empty.")
        return
    res = pd.DataFrame([outcome(r.ticker, r.signal_date) for r in j.itertuples()], index=j.index)
    j = j.join(res)
    dec = j["decision"].fillna("").astype(str).str.strip().str.lower()
    j["group"] = np.where(dec == "", "blank", np.where(dec == "do", "do", "skip"))
    cols = ["ret_5d", "ret_20d", "ret_60d", "max_gain_60d", "max_dd_60d"]
    pd.set_option("display.width", 200)
    print("== All rule signals")
    print(j[cols].agg(["count", "mean", "median"]).round(3).to_string())
    filled = j[j["group"] != "blank"]
    if filled.empty:
        print("\nNo entries with a decision yet; cannot compare human judgment.")
    else:
        print("\n== Grouped by your decision")
        print(filled.groupby("group")[cols].agg(["count", "mean", "median"]).round(3).to_string())
    out = ROOT / "journal" / "evaluation.csv"
    j.to_csv(out, index=False)
    print(f"Details: {out}")


if __name__ == "__main__":
    main()
