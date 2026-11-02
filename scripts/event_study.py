"""Sample event review: rally detection + signal behavior over day -60..0 + v0 scanner trigger check.

Usage:
    PYTHONPATH=src python scripts/event_study.py [--refresh] [--config configs/scanner_v0.yaml] [--tag v0]

Output to reports/data/:
    rallies_<tag>.csv        each rally (trough / day0 / peak / gain / whether the scanner triggered)
    window_<T>_<day0>_<tag>.csv   daily indicators from day -60 to day 0
    scanner_signals_<tag>.csv all historical scanner trigger days + returns 10/20/40 days later (simple comparison)
    filings_near_day0.csv    SEC filings around day0 (8-K / 13D / S-3 / 424B5)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from alpha_breakout import sec  # noqa: E402
from alpha_breakout.config import load_config  # noqa: E402
from alpha_breakout.data import get_daily  # noqa: E402
from alpha_breakout.event_study import (SNAPSHOT_COLS, event_window, find_rallies,  # noqa: E402
                                        window_summary)
from alpha_breakout.scanner import CONDITIONS, scan  # noqa: E402

OUT = ROOT / "reports" / "data"


def forward_stats(close: pd.Series, d) -> dict:
    i = close.index.get_loc(d)
    p = close.iloc[i]
    res = {}
    for n in (10, 20, 40):
        res[f"ret_{n}d"] = round(close.iloc[i + n] / p - 1, 3) if i + n < len(close) else None
    fut = close.iloc[i + 1: i + 41]
    res["max_gain_40d"] = round(fut.max() / p - 1, 3) if len(fut) else None
    res["max_dd_40d"] = round(fut.min() / p - 1, 3) if len(fut) else None
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--config", default=None)
    ap.add_argument("--samples", default=str(ROOT / "configs" / "samples.yaml"))
    ap.add_argument("--no-sec", action="store_true")
    ap.add_argument("--tag", default="v0", help="output file name suffix, to tell parameter versions apart")
    args = ap.parse_args()
    tag = args.tag

    cfg = load_config(args.config)
    samples = yaml.safe_load(open(args.samples, encoding="utf-8"))
    start = samples.get("start")
    OUT.mkdir(parents=True, exist_ok=True)

    bench = get_daily(cfg["relative_strength"]["benchmark"], refresh=args.refresh)
    rally_rows, sig_rows, filing_rows = [], [], []

    for s in samples["samples"]:
        t = s["ticker"]
        df = get_daily(t, refresh=args.refresh)
        feats = scan(df, bench, cfg)
        feats = feats[feats.index >= pd.Timestamp(start)] if start else feats
        px = df.loc[feats.index]

        # full-history scanner signals
        for d in feats.index[feats["signal"]]:
            sig_rows.append({"ticker": t, "date": d.date(), "close": round(feats.at[d, "close"], 2),
                             "vol_ratio": round(feats.at[d, "vol_ratio"], 2),
                             "rv20_pctile": round(feats.at[d, "rv20_pctile"], 3),
                             **forward_stats(px["Close"], d)})

        rallies = find_rallies(px)
        manual = [pd.Timestamp(x).date() for x in s.get("manual_day0") or []]
        for m in manual:
            rallies = pd.concat([rallies, pd.DataFrame([{"day0": m, "day0_rule": "manual"}])], ignore_index=True)

        fil = None
        if not args.no_sec:
            try:
                fil = sec.filings(t, refresh=args.refresh)
            except Exception as e:  # if it can't be fetched, just record that
                print(f"[warn] SEC {t}: {e}")

        for _, r in rallies.iterrows():
            day0 = pd.Timestamp(r["day0"])
            w = event_window(feats, day0, pre=60, post=0)
            enough = (w["rel_day"].min() == -60) and not r.get("short_history", False)
            w[["rel_day", *SNAPSHOT_COLS, *CONDITIONS, "n_conditions", "signal"]].to_csv(
                OUT / f"window_{t}_{day0.date()}_{tag}.csv", float_format="%.4f")

            # scanner check: day -60 through the leg peak (or day0 + 40 if there is no peak)
            end = pd.Timestamp(r["peak_date"]) if pd.notna(r.get("peak_date")) else day0 + pd.Timedelta(days=60)
            chk = feats.loc[w.index[0]: end]
            idx0 = feats.index.get_loc(day0)
            hits = [(d, feats.index.get_loc(d) - idx0) for d in chk.index[chk["signal"]]]
            d0row = feats.loc[day0]
            failed = [c for c in CONDITIONS if not bool(d0row[c])]

            row = {"ticker": t, **{k: r.get(k) for k in rallies.columns}, "has_60d_history": enough,
                   **window_summary(w),
                   "d0_failed_conditions": ",".join(failed) or "-",
                   "scanner_hits": ",".join(f"{d.date()}({k:+d})" for d, k in hits) or "-",
                   "first_hit_rel_day": hits[0][1] if hits else None}
            if hits:
                hd = hits[0][0]
                row["entry_close"] = round(feats.at[hd, "close"], 2)
                if pd.notna(r.get("peak")):
                    row["entry_to_peak"] = round(r["peak"] / feats.at[hd, "close"] - 1, 3)
            rally_rows.append(row)

            if fil is not None:
                near = sec.filings_between(fil, day0 - pd.Timedelta(days=30), day0 + pd.Timedelta(days=5),
                                           sec.CATALYST_FORMS | sec.DILUTION_FORMS)
                for _, f in near.iterrows():
                    filing_rows.append({"ticker": t, "day0": day0.date(), "filingDate": f["filingDate"].date(),
                                        "form": f["form"], "items": f.get("items", ""), "url": f["url"]})

    rallies_df = pd.DataFrame(rally_rows)
    rallies_df.to_csv(OUT / f"rallies_{tag}.csv", index=False)
    pd.DataFrame(sig_rows).to_csv(OUT / f"scanner_signals_{tag}.csv", index=False)
    pd.DataFrame(filing_rows).to_csv(OUT / "filings_near_day0.csv", index=False)

    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 50)
    print(rallies_df.T.to_string())
    print(pd.DataFrame(sig_rows).to_string())
    print(pd.DataFrame(filing_rows).drop(columns=["url"], errors="ignore").to_string())


if __name__ == "__main__":
    main()
