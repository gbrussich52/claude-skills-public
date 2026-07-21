#!/usr/bin/env python3
"""Compute REAL historical base rates for common setups from free daily data.

This is what makes "backtested probability" honest: instead of asserting a
win-rate, it measures one on a specific instrument's history and reports it
with a sample size and a 95% Wilson confidence interval. A rate with a tiny n
or a wide CI is NOT tradable edge — the output makes that visible.

Source: shared fetcher in data.py (Schwab if configured -> Yahoo -> Tiingo).

Usage:
    python3 backtest.py NVDA
    python3 backtest.py NVDA --years 10 --gap 1.0 --json
    python3 backtest.py spy.us --breakout-window 20 --horizon 5

Setups measured:
  - Gap fill: how often an opening gap (>= --gap %) is filled (price returns to
    the prior close) same-day and within 5 days, split by up/down gaps.
  - Breakout follow-through: after a close above the prior N-day high, how often
    price is higher --horizon days later, and the average forward return.
  - Oversold bounce: after K consecutive down closes, P(next day up) + avg return.

Limitations (state these when quoting results):
  - Daily bars only; ignores intraday path, slippage, fees, dividends, splits
    handling depends on the provider's adjustment. Base rates != your realized edge.
  - Unconditional single-name history. Not regime-segmented. Past != future.
"""
from __future__ import annotations

import json
import math
import sys

from data import fetch_daily, normalize_symbol  # shared robust fetcher


def wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for a binomial proportion. Robust at small n."""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
    return (round(max(0.0, center - half) * 100, 1), round(min(1.0, center + half) * 100, 1))


def rate(k: int, n: int) -> dict:
    lo, hi = wilson_ci(k, n)
    return {"hits": k, "n": n, "rate_pct": round(k / n * 100, 1) if n else None,
            "ci95_pct": [lo, hi], "tradable": n >= 30 and (hi - lo) <= 25}


def analyze_gaps(rows: list[dict], gap_pct: float) -> dict:
    up_same = up_5 = up_n = 0
    dn_same = dn_5 = dn_n = 0
    for i in range(1, len(rows)):
        prev_c = rows[i - 1]["c"]
        g = (rows[i]["o"] - prev_c) / prev_c * 100
        fwd = rows[i:i + 5]
        if g >= gap_pct:
            up_n += 1
            if rows[i]["l"] <= prev_c:
                up_same += 1
            if min(d["l"] for d in fwd) <= prev_c:
                up_5 += 1
        elif g <= -gap_pct:
            dn_n += 1
            if rows[i]["h"] >= prev_c:
                dn_same += 1
            if max(d["h"] for d in fwd) >= prev_c:
                dn_5 += 1
    return {
        "gap_up_fill_same_day": rate(up_same, up_n),
        "gap_up_fill_within_5d": rate(up_5, up_n),
        "gap_down_fill_same_day": rate(dn_same, dn_n),
        "gap_down_fill_within_5d": rate(dn_5, dn_n),
    }


def analyze_breakouts(rows: list[dict], window: int, horizon: int) -> dict:
    wins = n = 0
    rets: list[float] = []
    for i in range(window, len(rows) - horizon):
        prior_high = max(r["h"] for r in rows[i - window:i])
        if rows[i]["c"] > prior_high:
            n += 1
            fwd = (rows[i + horizon]["c"] - rows[i]["c"]) / rows[i]["c"]
            rets.append(fwd)
            if fwd > 0:
                wins += 1
    avg = round(sum(rets) / len(rets) * 100, 2) if rets else None
    out = rate(wins, n)
    out["avg_fwd_return_pct"] = avg
    out["window"] = window
    out["horizon_days"] = horizon
    return {"breakout_follow_through": out}


def analyze_oversold(rows: list[dict], streak: int = 3) -> dict:
    wins = n = 0
    rets: list[float] = []
    for i in range(streak, len(rows) - 1):
        if all(rows[j]["c"] < rows[j - 1]["c"] for j in range(i - streak + 1, i + 1)):
            n += 1
            fwd = (rows[i + 1]["c"] - rows[i]["c"]) / rows[i]["c"]
            rets.append(fwd)
            if fwd > 0:
                wins += 1
    out = rate(wins, n)
    out["avg_next_day_return_pct"] = round(sum(rets) / len(rets) * 100, 2) if rets else None
    out["down_streak"] = streak
    return {"oversold_bounce": out}


def main(argv: list[str]) -> int:
    flags = {a.split("=")[0]: (a.split("=")[1] if "=" in a else True) for a in argv if a.startswith("--")}
    pos = [a for a in argv[1:] if not a.startswith("--")]
    if not pos:
        print(__doc__)
        return 2
    ticker = pos[0]

    def fval(name, default):
        i = argv.index(name) if name in argv else -1
        return float(argv[i + 1]) if i >= 0 and i + 1 < len(argv) else default

    years = fval("--years", 15)
    gap = fval("--gap", 1.0)
    bw = int(fval("--breakout-window", 20))
    hz = int(fval("--horizon", 5))

    rows = fetch_daily(ticker, years)
    if years and len(rows) > int(years * 252):
        rows = rows[-int(years * 252):]
    if len(rows) < 60:
        raise SystemExit(f"ERROR: only {len(rows)} bars — too few for base rates.")

    result = {
        "ticker": ticker.upper(),
        "symbol": normalize_symbol(ticker),
        "bars": len(rows),
        "date_range": [rows[0]["date"], rows[-1]["date"]],
        "params": {"gap_pct": gap, "breakout_window": bw, "horizon_days": hz},
        **analyze_gaps(rows, gap),
        **analyze_breakouts(rows, bw, hz),
        **analyze_oversold(rows),
        "note": "Rates flagged tradable=false (n<30 or CI wider than 25pts) are NOT edge. "
                "Daily bars only; excludes fees/slippage; unconditional (not regime-split).",
    }

    if flags.get("--json"):
        print(json.dumps(result, indent=2))
        return 0

    print(f"=== BACKTEST {result['ticker']} ({result['symbol']}) ===")
    print(f"{result['bars']} bars  {result['date_range'][0]} -> {result['date_range'][1]}  "
          f"gap>={gap}%  breakout={bw}d  horizon={hz}d\n")
    for key, val in result.items():
        if not isinstance(val, dict) or "n" not in val:
            continue
        star = "" if val["tradable"] else "  (NOT tradable: low n / wide CI)"
        extra = ""
        for k in ("avg_fwd_return_pct", "avg_next_day_return_pct"):
            if k in val and val[k] is not None:
                extra = f"  avg {val[k]:+}%"
        print(f"{key:32s} {val['rate_pct']}%  n={val['n']}  "
              f"CI95 [{val['ci95_pct'][0]}, {val['ci95_pct'][1]}]{extra}{star}")
    print(f"\n! {result['note']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
