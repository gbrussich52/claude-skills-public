#!/usr/bin/env python3
"""Kalshi prediction-market provider for Alpha Hunter (public data, no API key).

Kalshi market prices ARE crowd-implied probabilities: a binary YES contract
trades in dollars 0.00-1.00, so $0.63 = a 63% implied chance. This is the
"Prediction Market Mispricing" leg — compare Kalshi's implied odds to your own.

Public market data (markets / events) is keyless. Placing trades would need
auth (RSA-signed) — NOT included here; this is read-only.

Base: https://api.elections.kalshi.com/trade-api/v2  (verified live 2026-07-16;
the plain api.kalshi.com host does not resolve).

USAGE:
  python3 kalshi.py market <TICKER>     # one market: yes/no, implied prob, volume
  python3 kalshi.py series <SERIES>     # all markets in a series, e.g. KXFED, KXCPI
  python3 kalshi.py search "<term>"     # find open EVENTS whose title matches
"""
from __future__ import annotations

import json
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

BASE = "https://api.elections.kalshi.com/trade-api/v2"
UA = "alpha-hunter/1.0"


def _get(path: str, params: dict | None = None, retries: int = 3) -> dict:
    url = f"{BASE}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read().decode())
        except Exception as e:  # noqa: BLE001 - IncompleteRead is common on large responses; retry
            last_err = e
            if attempt < retries - 1:
                time.sleep(1.5 * (attempt + 1))
    raise SystemExit(f"ERROR: Kalshi request failed after {retries} tries ({path}): {last_err}")


def _f(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_market(m: dict) -> dict:
    """Normalize a Kalshi market; implied prob = last trade, else yes bid/ask midpoint."""
    yes_bid, yes_ask = _f(m.get("yes_bid_dollars")), _f(m.get("yes_ask_dollars"))
    last = _f(m.get("last_price_dollars"))
    if last and last > 0:
        prob = last
    elif yes_bid is not None and yes_ask is not None and (yes_bid + yes_ask) > 0:
        prob = (yes_bid + yes_ask) / 2
    else:
        prob = None
    return {
        "ticker": m.get("ticker"), "title": m.get("title"),
        "yes_sub_title": m.get("yes_sub_title"),
        "status": m.get("status"), "close_time": m.get("close_time"),
        "yes_bid": yes_bid, "yes_ask": yes_ask,
        "no_bid": _f(m.get("no_bid_dollars")), "no_ask": _f(m.get("no_ask_dollars")),
        "last_price": last,
        "implied_prob_pct": round(prob * 100, 1) if prob is not None else None,
        "volume": int(_f(m.get("volume_fp")) or 0),
        "volume_24h": int(_f(m.get("volume_24h_fp")) or 0),
        "open_interest": int(_f(m.get("open_interest_fp")) or 0),
        "fetched_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def get_market(ticker: str) -> dict:
    js = _get(f"/markets/{ticker.upper()}")
    return parse_market(js.get("market") or js)


def list_series(series_ticker: str) -> list[dict]:
    """All markets in a series (the reliable topic lookup, e.g. KXFED)."""
    js = _get("/markets", {"series_ticker": series_ticker.upper(), "limit": 200})
    return [parse_market(m) for m in js.get("markets", [])]


def search_events(term: str, max_pages: int = 5, page: int = 200) -> list[dict]:
    """Best-effort: scan open EVENTS for a title match. Returns event + series tickers.

    Events are more curated than the raw markets feed (which is dominated by
    multivariate sports parlays), so title search works well here. For an exact
    price, follow up with `series <series_ticker>` or `market <ticker>`.
    """
    term_l = term.lower()
    out, cursor = [], None
    for _ in range(max_pages):
        params = {"status": "open", "limit": page}
        if cursor:
            params["cursor"] = cursor
        js = _get("/events", params)
        for e in js.get("events", []):
            if term_l in (e.get("title") or "").lower():
                out.append({"event_ticker": e.get("event_ticker"),
                            "series_ticker": e.get("series_ticker"),
                            "title": e.get("title")})
        cursor = js.get("cursor")
        if not cursor:
            break
    return out


def _fmt(r: dict) -> str:
    p = f"{r['implied_prob_pct']}%" if r["implied_prob_pct"] is not None else "  n/a"
    return f"{p:>6}  vol {r['volume']:>8}  {r['ticker']}\n        {r['title']}"


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2
    cmd, arg = argv[1], argv[2]
    if cmd == "market":
        print(json.dumps(get_market(arg), indent=2))
    elif cmd == "series":
        res = list_series(arg)
        if not res:
            print(f"No markets in series '{arg.upper()}'.")
            return 0
        print(f"{len(res)} markets in {arg.upper()} (implied prob = yes price):\n")
        for r in sorted(res, key=lambda x: x["volume"], reverse=True):
            print(_fmt(r))
    elif cmd == "search":
        res = search_events(arg)
        if not res:
            print(f"No open events matched '{arg}'. Try a broader term or a known series.")
            return 0
        print(f"{len(res)} open events match '{arg}':\n")
        for e in res[:20]:
            print(f"{e['series_ticker']:<22} {e['title']}")
        print("\nNext: python3 kalshi.py series <SERIES_TICKER>")
    else:
        print("Unknown command:", cmd)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
