#!/usr/bin/env python3
"""Fetch current price context for a ticker from free sources (no API key).

Data path: shared fetcher in data.py (Schwab if configured -> Yahoo -> Tiingo).
Purpose: give Alpha Hunter *real* recent levels instead of stale memorized ones.

Usage:
    python3 fetch_data.py NVDA
    python3 fetch_data.py btc.v        # explicit provider symbol (crypto/index/fx)
    python3 fetch_data.py NVDA --json

Output: latest OHLC, 20/50/200-day SMAs, 20-day high/low, distance from each,
and an explicit data-freshness timestamp. Free daily bars are end-of-day and
can lag intraday — the skill must label this as such.

Network note: needs egress to the data hosts (see references/data-sources.md).
Under the Claude Code sandbox they may not be allowlisted — add via /sandbox or
run unsandboxed. The script fails loudly (never silently) if the fetch fails.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

from data import fetch_daily, normalize_symbol  # shared robust fetcher


def sma(closes: list[float], n: int) -> float | None:
    return sum(closes[-n:]) / n if len(closes) >= n else None


def build_context(ticker: str, rows: list[dict]) -> dict:
    closes = [r["c"] for r in rows]
    last = rows[-1]
    window = rows[-20:]
    hi20 = max(r["h"] for r in window)
    lo20 = min(r["l"] for r in window)
    price = last["c"]

    def pct_from(level: float | None):
        return None if not level else round((price - level) / level * 100, 2)

    s20, s50, s200 = sma(closes, 20), sma(closes, 50), sma(closes, 200)
    return {
        "ticker": ticker.upper(),
        "stooq_symbol": normalize_symbol(ticker),
        "as_of_bar_date": last["date"],
        "fetched_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "data_freshness": "END-OF-DAY daily bars (may lag intraday). Re-confirm live before execution.",
        "last_ohlc": {k: last[k] for k in ("o", "h", "l", "c", "v")},
        "sma20": round(s20, 2) if s20 else None,
        "sma50": round(s50, 2) if s50 else None,
        "sma200": round(s200, 2) if s200 else None,
        "pct_vs_sma20": pct_from(s20),
        "pct_vs_sma50": pct_from(s50),
        "pct_vs_sma200": pct_from(s200),
        "high_20d": round(hi20, 2),
        "low_20d": round(lo20, 2),
        "bars_available": len(rows),
    }


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if not a.startswith("--")]
    as_json = "--json" in argv
    if not args:
        print(__doc__)
        return 2
    ctx = build_context(args[0], fetch_daily(args[0]))
    if as_json:
        print(json.dumps(ctx, indent=2))
        return 0
    print(f"=== {ctx['ticker']} ({ctx['stooq_symbol']}) ===")
    print(f"As-of bar: {ctx['as_of_bar_date']}   Fetched: {ctx['fetched_utc']}")
    print(f"! {ctx['data_freshness']}")
    o = ctx["last_ohlc"]
    print(f"Last OHLC: O {o['o']}  H {o['h']}  L {o['l']}  C {o['c']}  V {int(o['v']):,}")
    print(f"SMA20 {ctx['sma20']} ({ctx['pct_vs_sma20']}%)  "
          f"SMA50 {ctx['sma50']} ({ctx['pct_vs_sma50']}%)  "
          f"SMA200 {ctx['sma200']} ({ctx['pct_vs_sma200']}%)")
    print(f"20d high {ctx['high_20d']}   20d low {ctx['low_20d']}   bars {ctx['bars_available']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
