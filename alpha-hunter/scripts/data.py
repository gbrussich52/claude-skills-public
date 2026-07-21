#!/usr/bin/env python3
"""Shared daily-OHLC fetcher for Alpha Hunter. Free sources, $0.

Reliability strategy (the free-data landscape is hostile in 2026):
  1. Yahoo Finance chart API — keyless. Rotates query1<->query2 and retries with
     backoff on 429/5xx. Fine for light, single-ticker use.
  2. Tiingo fallback — if env var TIINGO_KEY is set (free tier, generous limits,
     2-min signup at tiingo.com). Reliable when Yahoo throttles.

Fails LOUDLY (SystemExit) if every source fails. Never returns fabricated data.
A row with any null OHLC is skipped, not invented.

Returns: list[dict] oldest->newest, each {date, o, h, l, c, v}.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone

# Let `import schwab` resolve regardless of the caller's cwd.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
YAHOO_HOSTS = ("query1.finance.yahoo.com", "query2.finance.yahoo.com")


def normalize_symbol(ticker: str) -> str:
    """Yahoo/Tiingo use the bare ticker (SPY, BTC-USD). Strip a legacy `.us`."""
    t = ticker.strip().upper()
    return t[:-3] if t.endswith(".US") else t


def range_for(years: float) -> str:
    for y, r in ((1, "1y"), (2, "2y"), (5, "5y"), (10, "10y")):
        if years <= y:
            return r
    return "max"


def _get(url: str, timeout: int = 20) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _fetch_yahoo(sym: str, rng: str, retries: int = 3) -> list[dict]:
    last_err = None
    for attempt in range(retries):
        host = YAHOO_HOSTS[attempt % len(YAHOO_HOSTS)]
        url = f"https://{host}/v8/finance/chart/{sym}?range={rng}&interval=1d"
        try:
            data = json.loads(_get(url).decode("utf-8", "replace"))
            chart = data.get("chart", {})
            if chart.get("error") or not chart.get("result"):
                raise ValueError(f"empty result ({chart.get('error')})")
            res = chart["result"][0]
            ts = res.get("timestamp") or []
            q = (res.get("indicators", {}).get("quote") or [{}])[0]
            o_, h_, l_, c_, v_ = (q.get(k) or [] for k in ("open", "high", "low", "close", "volume"))
            rows = []
            for i, t in enumerate(ts):
                o, h, l, c = o_[i], h_[i], l_[i], c_[i]
                if None in (o, h, l, c):
                    continue
                rows.append({"date": datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d"),
                             "o": float(o), "h": float(h), "l": float(l), "c": float(c),
                             "v": float(v_[i]) if i < len(v_) and v_[i] is not None else 0.0})
            if len(rows) >= 2:
                return rows
            raise ValueError("fewer than 2 usable rows")
        except Exception as e:  # noqa: BLE001 - rotate host and back off
            last_err = e
            if attempt < retries - 1:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"Yahoo failed after {retries} tries: {last_err}")


def _fetch_tiingo(sym: str, years: float) -> list[dict]:
    key = os.environ.get("TIINGO_KEY")
    if not key:
        raise RuntimeError("no TIINGO_KEY set")
    start_year = datetime.now(timezone.utc).year - int(years) - 1
    url = (f"https://api.tiingo.com/tiingo/daily/{sym}/prices"
           f"?startDate={start_year}-01-01&format=json&token={key}")
    data = json.loads(_get(url).decode("utf-8", "replace"))
    if not isinstance(data, list) or not data:
        raise RuntimeError("Tiingo returned no rows")
    rows = []
    for d in data:
        try:
            rows.append({"date": d["date"][:10], "o": float(d["open"]), "h": float(d["high"]),
                         "l": float(d["low"]), "c": float(d["close"]),
                         "v": float(d.get("volume") or 0.0)})
        except (KeyError, ValueError, TypeError):
            continue
    return rows


def fetch_daily(ticker: str, years: float = 15) -> list[dict]:
    """Daily OHLCV rows (oldest->newest). Tries Yahoo, then Tiingo if keyed."""
    sym = normalize_symbol(ticker)
    errors = []
    # 1) Schwab — primary/reliable, and the only source with real option Greeks.
    #    Only attempted when configured (creds + saved token); never a hard dep.
    try:
        import schwab
        if schwab.is_configured():
            return schwab.get_price_history(sym, years)
    except Exception as e:  # noqa: BLE001 - fall through to free sources
        errors.append(f"schwab: {e}")
    # 2) Yahoo — free keyless fallback.
    try:
        return _fetch_yahoo(sym, range_for(years))
    except Exception as e:  # noqa: BLE001
        errors.append(f"yahoo: {e}")
    try:
        return _fetch_tiingo(sym, years)
    except Exception as e:  # noqa: BLE001
        errors.append(f"tiingo: {e}")
    raise SystemExit(
        f"ERROR: could not fetch '{sym}' from any free source.\n  " + "\n  ".join(errors)
        + "\nHint: set TIINGO_KEY (free at tiingo.com) for a reliable fallback, "
          "or retry later — Yahoo rate-limits bursts."
    )


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    r = fetch_daily(sys.argv[1], float(sys.argv[2]) if len(sys.argv) > 2 else 5)
    print(f"{sys.argv[1].upper()}: {len(r)} rows, {r[0]['date']} -> {r[-1]['date']}, "
          f"last close {r[-1]['c']}")
