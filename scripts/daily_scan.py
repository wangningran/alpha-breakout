"""Daily scan: run once after the close; writes the day's watchlist and research journal template.

Usage:
    python scripts/daily_scan.py [--config configs/strategy_v4.yaml] [--no-update] [--equity 100000]
    python scripts/daily_scan.py --bootstrap     # fresh environment (cloud scheduled run): download only what the scan needs, about 20-25 minutes

Steps:
1. Prepare data:
   - normally: incremental price update for the whole market (about 10 minutes; --no-update skips it and uses the cache)
   - --bootstrap: build the universe, download about 14 months of prices and 3 years of quarterly fundamentals
     (no need to run build_universe and the other scripts first)
2. Run the strategy state machine for every ticker up to the latest trading day to get its current state
3. Refresh SEC filings for the companies on the watchlist (latest 8-K items, offerings, 13D/13G) and run again
4. Write reports/daily/<date>.md and append probe signals from the last 5 trading days to journal/research_log.csv
   (each ticker + signal date is recorded once; the decision column is left blank for you to fill in)

The report has five sections:
- Probe candidates: met the probe condition at today's close; by the rules, buy 1/3 at tomorrow's open
- Probe signals in the last 5 trading days: catches up on missed days and shows what happened under the rules
- Position management: hypothetical positions under the rules whose close today triggered an add / partial / exit
- New on watchlist: surged today
- Watching: within the watch period after a surge and not back below the pre-surge price
"""
from __future__ import annotations

import argparse
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from alpha_breakout import filings as fmod_f  # noqa: E402
from alpha_breakout import fundamentals as fmod  # noqa: E402
from alpha_breakout.config import load_config  # noqa: E402
from alpha_breakout.data import BENCHMARKS, adjust, download_bulk, load_raw, update_recent  # noqa: E402
from alpha_breakout.research import make_entry_filter, trade_features  # noqa: E402
from alpha_breakout.strategy import run_ticker  # noqa: E402
from alpha_breakout.universe import listed_common_stocks, shares_history, shares_series  # noqa: E402

JOURNAL = ROOT / "journal" / "research_log.csv"
JOURNAL_COLS = ["scan_date", "ticker", "surge_date", "signal_date", "close", "rule_stop", "catalyst",
                "new_13d", "amend_13d", "new_13g", "rev_yoy", "gm_change", "runway_q",
                "decision", "reason", "notes"]
_G = {}
_EMPTY = pd.DataFrame(columns=["cik", "date", "form", "items"])
RECENT_DAYS = 5
UNIVERSE = ROOT / "data" / "cache" / "universe.csv"


def _init(cfg, sh, fil, fund):
    _G.update(cfg=cfg, sh=sh, fil=fil, fund=fund, iwm=adjust(load_raw("IWM"))["Close"])


def _state(row):
    t, cik = row
    raw = load_raw(t)
    if raw is None or len(raw) < 80:
        return None
    df = adjust(raw)
    fil = _G["fil"].get(cik, _EMPTY) if _G["fil"] is not None else None
    ftab = fmod.company_table(_G["fund"], cik) if _G["fund"] is not None else None
    st = {}
    trades = run_ticker(t, df, shares_series(_G["sh"], cik, df.index), _G["cfg"], "staged",
                        filings=fil, entry_filter=make_entry_filter(_G["cfg"], fil, ftab, df["Close"], _G["iwm"]),
                        state_out=st)
    cutoff = _G["cfg"]["_recent_cutoff"]
    recent = []
    for tr0 in trades + ([st["trade"]] if st.get("trade") is not None else []):
        if tr0.signal_date is not None and tr0.signal_date >= cutoff and all(x["signal_date"] != tr0.signal_date for x in recent):
            recent.append({"signal_date": tr0.signal_date, "surge_date": tr0.surge_date, "entry_date": tr0.entry_date,
                           "entry": tr0.entry, "stop0": tr0.stop0, "add_date": tr0.add_date,
                           "exit_date": tr0.exit_date, "exit_price": tr0.exit_price, "exit_reason": tr0.exit_reason,
                           "catalyst": tr0.catalyst,
                           "close_at_signal": float(df["Close"].loc[tr0.signal_date]),
                           "low10_at_signal": float(df["Low"].loc[:tr0.signal_date].iloc[-10:].min()),
                           **trade_features(fil, ftab, tr0.surge_date, tr0.signal_date)})
    if st.get("state") in (None, "idle") and not st.get("surge_today") and not recent:
        return None
    tr = st.get("trade")
    rec = {"ticker": t, "cik": cik, "state": st["state"], "date": st["date"], "close": st["close"],
           "pending": st["pending"], "pending_reason": st["pending_reason"], "stop": st["stop"],
           "days_since_surge": st["days_since_surge"], "surge_high": st["surge_high"],
           "vol_ratio": st["vol_ratio"], "low10": float(df["Low"].iloc[-10:].min()), "recent": recent}
    if tr is not None:
        rec.update(surge_date=tr.surge_date, surge_kind=tr.surge_kind, surge_gain=tr.surge_gain,
                   pre_surge_close=tr.pre_surge_close, catalyst=tr.catalyst, attempt=tr.attempt,
                   entry=tr.entry, entry_date=tr.entry_date, add_date=tr.add_date,
                   mcap=tr.mcap_at_surge)
        rec.update(trade_features(fil, ftab, tr.surge_date, st["date"]))
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(ROOT / "configs" / "strategy_v4.yaml"))
    ap.add_argument("--no-update", action="store_true")
    ap.add_argument("--equity", type=float, help="account size for the suggested share count; defaults to portfolio.initial_equity in the config")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--bootstrap", action="store_true", help="fresh environment: download only the data the scan needs")
    args = ap.parse_args()

    cfg = load_config(args.config)
    cfg["backtest"]["end"] = None
    sh_start = pd.Timestamp.today().year - 2   # market cap only needs recent share counts
    if args.bootstrap:
        bootstrap(sh_start)
    u = pd.read_csv(UNIVERSE)
    if not args.no_update and not args.bootstrap:
        update_recent(list(u["ticker"]) + list(BENCHMARKS))
    iwm = adjust(load_raw("IWM"))
    last_day = iwm.index[-1]
    # only the current state is needed: run the state machine from 200 days back (watch period is at most 90 days); older data still feeds the indicators
    cfg["backtest"]["start"] = str((last_day - pd.Timedelta(days=200)).date())
    cfg["_recent_cutoff"] = iwm.index[-RECENT_DAYS]

    sh = shares_history(start_year=sh_start)
    fil = _filings_dict(fmod_f.load())
    fund = fmod.load()
    rows = list(zip(u["ticker"], u["cik"].astype(int)))

    def scan(rows_, fil_):
        with ProcessPoolExecutor(args.workers, initializer=_init, initargs=(cfg, sh, fil_, fund)) as ex:
            return [r for r in ex.map(_state, rows_, chunksize=25) if r is not None]

    recs = scan(rows, fil)
    # refresh filings for the watchlist companies and run again to pick up the latest offerings / 8-Ks / 13Ds
    # (without a filings cache the first pass skips the offering filter, so it only over-includes; the second pass corrects it)
    live = [r for r in recs if r["date"] == last_day]
    if live:
        ciks = sorted({r["cik"] for r in live})
        fil = _filings_dict(fmod_f.refresh_companies(ciks))
        recs = scan([(r["ticker"], r["cik"]) for r in live], fil)
    recs = [r for r in recs if r["date"] == last_day]
    df = pd.DataFrame(recs)
    write_report(df, cfg, last_day, fil, args.equity or (cfg.get("portfolio") or {}).get("initial_equity", 100000))


def _filings_dict(f: pd.DataFrame | None) -> dict:
    if f is None or f.empty:
        return {}
    keep = f["form"].isin(fmod_f.OFFERING_FORMS | {"S-3", "F-3"}) | f["form"].str.startswith(
        ("8-K", "SC 13D", "SCHEDULE 13D", "SC 13G", "SCHEDULE 13G"))
    return {int(k): g.reset_index(drop=True) for k, g in f[keep].groupby("cik")}


def bootstrap(sh_start: int):
    """Prepare the minimum data the scan needs in a fresh environment (cached parts are skipped)."""
    u = listed_common_stocks()
    sh = shares_history(start_year=sh_start)
    u = u[u["cik"].isin(set(sh["cik"]))]
    start = str((pd.Timestamp.today() - pd.Timedelta(days=430)).date())
    print(f"bootstrap: {len(u)} tickers, prices since {start}")
    ok = download_bulk(list(u["ticker"]) + list(BENCHMARKS), start=start)
    u = u[u["ticker"].isin(ok)]
    UNIVERSE.parent.mkdir(parents=True, exist_ok=True)
    u.to_csv(UNIVERSE, index=False)
    if fmod.load() is None:
        fmod.fetch(start_year=sh_start - 1)


def _fmt_pct(x):
    return "—" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:+.0%}"


def _fmt_pt(x):
    return "—" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x * 100:+.1f} pt"


def _fmt_dte(x):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "—"
    return f"~{x:.0f} d" if x >= 0 else f"overdue {-x:.0f} d"


def _fmt_runway(x):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "—"
    return "profitable" if np.isinf(x) else f"{x:.1f} q"


def _fund_parts(cfg) -> str:
    fu = (cfg.get("filters") or {}).get("fundamentals") or {}
    parts = []
    if fu.get("min_rev_yoy") is not None:
        parts.append(f"revenue YoY >= {fu['min_rev_yoy']:.0%}")
    if fu.get("min_gm_change") is not None:
        parts.append(f"gross margin up >= {fu['min_gm_change'] * 100:.0f} pt YoY")
    if fu.get("min_runway_q") is not None:
        parts.append(f"cash runway >= {fu['min_runway_q']} quarters")
    return ", ".join(parts) if parts else "none"


def _fund_rule(cfg) -> str:
    return "fundamentals: " + _fund_parts(cfg)


def _fund_ok(r, cfg) -> str:
    fu = (cfg.get("filters") or {}).get("fundamentals") or {}
    checks = []
    if fu.get("min_rev_yoy") is not None:
        v = getattr(r, "rev_yoy", np.nan)
        checks.append(None if pd.isna(v) else v >= fu["min_rev_yoy"])
    if fu.get("min_runway_q") is not None:
        v = getattr(r, "runway_q", np.nan)
        checks.append(None if pd.isna(v) else v >= fu["min_runway_q"])
    if fu.get("min_gm_change") is not None:
        v = getattr(r, "gm_change", np.nan)
        checks.append(None if pd.isna(v) else v >= fu["min_gm_change"])
    if not checks:
        return "—"
    if any(c is False for c in checks):
        return "✗"
    if any(c is None for c in checks):
        return "missing" if not fu.get("allow_missing", True) else "✓"
    return "✓"


def _recent_filings(fil, cik, since, n=6) -> str:
    g = fil.get(int(cik))
    if g is None or g.empty:
        return "—"
    g = g[g["date"] >= pd.Timestamp(since)].tail(n)
    if g.empty:
        return "—"
    return "; ".join(f"{d.date()} {fm}{'(' + it + ')' if it else ''}" for d, fm, it in zip(g["date"], g["form"], g["items"]))


def write_report(df: pd.DataFrame, cfg: dict, day: pd.Timestamp, fil: dict, equity: float):
    out_dir = ROOT / "reports" / "daily"
    out_dir.mkdir(parents=True, exist_ok=True)
    p = cfg["position"]
    pcfg = cfg.get("portfolio") or {}
    risk_amt = equity * pcfg.get("risk_per_trade", 0.005)
    lines = [f"# Daily scan {day.date()}", "",
             f"- Rules: enter the watchlist on a {cfg['surge']['one_day']['min_ret']:.0%} one-day surge on volume or a {cfg['surge']['multi_day']['min_ret']:.0%} five-day gain; "
             f"watch {cfg['watch']['min_days']}-{cfg['watch']['max_days']} days; probe 1/3 on a new high since the surge; {_fund_rule(cfg)}; "
             f"no probe within {((cfg.get('filters') or {}).get('earnings') or {}).get('avoid_before_days')} days before expected earnings",
             f"- Account ${equity:,.0f}, probe risk per trade ${risk_amt:,.0f} (1R)",
             "- Prices are adjusted closes. The rules only find names and manage risk; whether to trade is your call after research, recorded in journal/research_log.csv.", ""]

    if df.empty:
        lines.append("No names met the conditions today.")
    sig = df[(df["state"] == "pending_entry")] if not df.empty else df
    lines += ["## Probe candidates (buy 1/3 at tomorrow's open per the rules)", ""]
    if sig.empty:
        lines += ["None", ""]
    else:
        lines += ["| Ticker | Close | Surge date | Day | Surge gain | Rule stop | Stop % | Probe shares | Rev YoY | GM change | Next earnings | Cash runway | Catalyst | 13D new/amend/13G | Recent filings |",
                  "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
        for r in sig.sort_values("ticker").itertuples():
            stop = max(r.low10, r.close * (1 - p["max_stop_pct"]))
            risk = (r.close - stop) / r.close
            shares = int(risk_amt / max(r.close - stop, 1e-9))
            lines.append(
                f"| {r.ticker} | {r.close:.2f} | {pd.Timestamp(r.surge_date).date()} | {r.days_since_surge} | {r.surge_gain:+.0%} | "
                f"{stop:.2f} | {risk:.0%} | {shares} | {_fmt_pct(r.rev_yoy)} | {_fmt_pt(r.gm_change)} | {_fmt_dte(r.days_to_earnings)} | {_fmt_runway(r.runway_q)} | "
                f"{r.catalyst} | {int(r.new_13d)}/{int(r.amend_13d)}/{int(r.new_13g)} | "
                f"{_recent_filings(fil, r.cik, pd.Timestamp(r.surge_date) - pd.Timedelta(days=10))} |")
        lines.append("")
        lines.append("Stop estimated from today's close (10-day low, at most -15%); recalculated from tomorrow's open.")
        lines.append("")

    rec_rows = [dict(ticker=r.ticker, cik=r.cik, **x) for r in df.itertuples() for x in (r.recent or [])] \
        if not df.empty and "recent" in df else []
    rec_df = pd.DataFrame(rec_rows)
    lines += [f"## Probe signals in the last {RECENT_DAYS} trading days ({len(rec_df)})", ""]
    if rec_df.empty:
        lines += ["None", ""]
    else:
        lines += ["| Ticker | Signal date | Surge date | Signal close | Entry date | Entry | Initial stop | Status under rules | Rev YoY | GM change |",
                  "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
        for r in rec_df.sort_values(["signal_date", "ticker"], ascending=[False, True]).itertuples():
            if pd.isna(r.entry_date):
                status = "probe at tomorrow's open"
            elif pd.notna(r.exit_date) and r.exit_reason != "open":
                status = f"exited {pd.Timestamp(r.exit_date).date()} @ {r.exit_price:.2f} ({r.exit_reason})"
            elif pd.notna(r.add_date):
                status = "added, holding"
            else:
                status = "probing"
            entry_d = "—" if pd.isna(r.entry_date) else str(pd.Timestamp(r.entry_date).date())
            entry_p = "—" if pd.isna(r.entry) else f"{r.entry:.2f}"
            stop_p = "—" if pd.isna(r.stop0) else f"{r.stop0:.2f}"
            lines.append(f"| {r.ticker} | {pd.Timestamp(r.signal_date).date()} | {pd.Timestamp(r.surge_date).date()} | "
                         f"{r.close_at_signal:.2f} | {entry_d} | {entry_p} | {stop_p} | {status} | "
                         f"{_fmt_pct(r.rev_yoy)} | {_fmt_pt(r.gm_change)} |")
        lines.append("")
        _append_journal(rec_df, day, p)

    pos = df[df["state"].isin(["probe", "full"])] if not df.empty else df
    lines += ["## Position management (hypothetical positions under the rules)", ""]
    if pos.empty:
        lines += ["None", ""]
    else:
        lines += ["| Ticker | State | Entry date | Entry | Close | Stop | Tomorrow |", "| --- | --- | --- | --- | --- | --- | --- |"]
        act_name = {"add": "add to full", "partial": "partial take-profit", "exit": "exit", None: "hold"}
        for r in pos.sort_values("ticker").itertuples():
            pend = None if pd.isna(r.pending) else r.pending  # None becomes NaN in the DataFrame
            reason = "" if pd.isna(r.pending_reason) else r.pending_reason
            act = act_name.get(pend, pend) + (f" ({reason})" if reason else "")
            lines.append(f"| {r.ticker} | {'probe' if r.state == 'probe' else 'full'} | {pd.Timestamp(r.entry_date).date()} | "
                         f"{r.entry:.2f} | {r.close:.2f} | {r.stop:.2f} | {act} |")
        lines.append("")

    new = df[(df["state"] == "watch") & (df["days_since_surge"] == 0)] if not df.empty else df
    lines += [f"## New on watchlist ({len(new)})", ""]
    if new.empty:
        lines += ["None", ""]
    else:
        lines += ["| Ticker | Close | Surge type | Gain | Vol ratio | Mkt cap ($M) | Catalyst | Recent filings |", "| --- | --- | --- | --- | --- | --- | --- | --- |"]
        for r in new.sort_values("surge_gain", ascending=False).itertuples():
            lines.append(f"| {r.ticker} | {r.close:.2f} | {r.surge_kind} | {r.surge_gain:+.0%} | {r.vol_ratio:.1f} | "
                         f"{r.mcap / 1e6:,.0f} | {r.catalyst} | {_recent_filings(fil, r.cik, day - pd.Timedelta(days=10), 4)} |")
        lines.append("")

    watch = df[(df["state"] == "watch") & (df["days_since_surge"] > 0)] if not df.empty else df
    lines += [f"## Watching ({len(watch)})", "", "Sorted by distance from the highest close since the surge; the closer to 0%, the closer to a probe signal.", ""]
    if not watch.empty:
        watch = watch.assign(dist=watch["close"] / watch["surge_high"] - 1).sort_values("dist", ascending=False)
        n_ok = sum(_fund_ok(r, cfg) == "✓" for r in watch.itertuples())
        lines.insert(len(lines) - 1, f"{n_ok} names pass fundamentals ({_fund_parts(cfg)}); only these appear under Probe candidates when they make a new high.")
        lines += ["| Ticker | Close | Surge date | Day | From high | Above invalidation | Fundamentals | Rev YoY | GM change | Next earnings | Cash runway | Catalyst | 13D new/amend/13G |",
                  "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
        watch = watch.assign(fund_ok=[_fund_ok(r, cfg) for r in watch.itertuples()])
        watch = watch.sort_values(["fund_ok", "dist"], ascending=[False, False], key=lambda c: c.map({"✓": 2, "missing": 1, "✗": 0}) if c.name == "fund_ok" else c)
        for r in watch.itertuples():
            lines.append(f"| {r.ticker} | {r.close:.2f} | {pd.Timestamp(r.surge_date).date()} | {r.days_since_surge} | "
                         f"{r.dist:+.0%} | {r.close / r.pre_surge_close - 1:+.0%} | {r.fund_ok} | {_fmt_pct(r.rev_yoy)} | "
                         f"{_fmt_pt(r.gm_change)} | {_fmt_dte(r.days_to_earnings)} | {_fmt_runway(r.runway_q)} | {r.catalyst} | {int(r.new_13d)}/{int(r.amend_13d)}/{int(r.new_13g)} |")
        lines.append("")
    path = out_dir / f"{day.date()}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    df.drop(columns=["recent"], errors="ignore").to_csv(out_dir / f"{day.date()}.csv", index=False)
    print(f"saved {path}  (probe candidates {len(sig)}, signals in last {RECENT_DAYS} days {len(rec_df)}, positions {len(pos)}, "
          f"new on watchlist {len(new)}, watching {len(watch)})")


def _append_journal(rec_df: pd.DataFrame, day: pd.Timestamp, p: dict):
    """Append recent probe signals to the research journal; each ticker + signal date is recorded once."""
    JOURNAL.parent.mkdir(parents=True, exist_ok=True)
    old = pd.read_csv(JOURNAL) if JOURNAL.exists() else pd.DataFrame(columns=JOURNAL_COLS)
    seen = set(zip(old["ticker"].astype(str), old["signal_date"].astype(str)))
    rows = []
    for r in rec_df.itertuples():
        sd = str(pd.Timestamp(r.signal_date).date())
        if (r.ticker, sd) in seen:
            continue
        rows.append({"scan_date": str(day.date()), "ticker": r.ticker, "surge_date": str(pd.Timestamp(r.surge_date).date()),
                     "signal_date": sd, "close": round(r.close_at_signal, 4),
                     "rule_stop": round(max(r.low10_at_signal, r.close_at_signal * (1 - p["max_stop_pct"])), 4),
                     "catalyst": r.catalyst, "new_13d": r.new_13d, "amend_13d": r.amend_13d, "new_13g": r.new_13g,
                     "rev_yoy": r.rev_yoy, "gm_change": r.gm_change, "runway_q": r.runway_q,
                     "decision": "", "reason": "", "notes": ""})
    if rows:
        pd.concat([old, pd.DataFrame(rows)], ignore_index=True)[JOURNAL_COLS].to_csv(JOURNAL, index=False)


if __name__ == "__main__":
    main()
