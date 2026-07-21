#!/usr/bin/env python3
"""Charles Schwab Trader API provider for Alpha Hunter.

The primary, reliable data source once the Schwab developer app is approved.
Gives real quotes, daily OHLC history (for backtests), and option chains WITH
Greeks + IV — the one dataset free sources cannot provide, so IV/Greeks graduate
from [ESTIMATE] to [LIVE].

CONFIG (never hard-code secrets):
  Set env vars, or put them in a local `.env` beside this script (gitignored):
      SCHWAB_APP_KEY=...
      SCHWAB_APP_SECRET=...
      SCHWAB_CALLBACK=https://127.0.0.1     # optional; must match the app's callback
  Tokens cache in `.schwab_tokens.json` (gitignored, chmod 600).
  Access token lives ~30 min (auto-refreshed). Refresh token lives 7 days;
  after that, re-run `login`.

USAGE:
  python3 schwab.py login             # one-time / weekly browser OAuth handshake
  python3 schwab.py quote NVDA
  python3 schwab.py history NVDA --years 15
  python3 schwab.py chain NVDA        # ATM Greeks + IV, nearest expiration

Endpoints follow developer.schwab.com. If Schwab changes them, update the three
URLs below. This module does no network I/O at import time (safe to probe with
is_configured()).
"""
from __future__ import annotations

import base64
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(HERE, ".env")
TOKEN_FILE = os.path.join(HERE, ".schwab_tokens.json")

AUTH_URL = "https://api.schwabapi.com/v1/oauth/authorize"
TOKEN_URL = "https://api.schwabapi.com/v1/oauth/token"
MARKET = "https://api.schwabapi.com/marketdata/v1"
REFRESH_MAX_AGE = 7 * 24 * 3600  # Schwab refresh token dies after 7 days


# --- config -----------------------------------------------------------------
def _load_env() -> None:
    """Populate os.environ from a local .env if present (does not override real env)."""
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())


def _cfg(name: str, default: str | None = None, required: bool = False) -> str | None:
    _load_env()
    val = os.environ.get(name, default)
    if required and not val:
        raise SystemExit(f"ERROR: {name} not set. Put it in env or {ENV_FILE}.")
    return val


def is_configured() -> bool:
    """True only if credentials AND a saved token file exist — safe to try."""
    _load_env()
    return bool(os.environ.get("SCHWAB_APP_KEY")) and os.path.exists(TOKEN_FILE)


# --- auth -------------------------------------------------------------------
def _basic_auth() -> str:
    key = _cfg("SCHWAB_APP_KEY", required=True)
    secret = _cfg("SCHWAB_APP_SECRET", required=True)
    return base64.b64encode(f"{key}:{secret}".encode()).decode()


def _post_token(payload: dict) -> dict:
    data = urllib.parse.urlencode(payload).encode()
    req = urllib.request.Request(
        TOKEN_URL, data=data,
        headers={"Authorization": f"Basic {_basic_auth()}",
                 "Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def _save(tok: dict) -> None:
    tok["_obtained_at"] = time.time()
    with open(TOKEN_FILE, "w") as f:
        json.dump(tok, f)
    os.chmod(TOKEN_FILE, 0o600)  # tokens are sensitive — owner-only


def _load() -> dict:
    if not os.path.exists(TOKEN_FILE):
        raise SystemExit("ERROR: not authenticated. Run: python3 schwab.py login")
    with open(TOKEN_FILE) as f:
        return json.load(f)


def login() -> None:
    """One-time / weekly manual OAuth handshake (needs a browser + copy/paste)."""
    key = _cfg("SCHWAB_APP_KEY", required=True)
    cb = _cfg("SCHWAB_CALLBACK", "https://127.0.0.1")
    url = f"{AUTH_URL}?client_id={urllib.parse.quote(key)}&redirect_uri={urllib.parse.quote(cb)}"
    print("1) Open this URL, log in to Schwab, and approve:\n\n" + url + "\n")
    print("2) Your browser redirects to a page that WON'T load (starts with your")
    print("   callback). Copy the FULL URL from the address bar.\n")
    redirected = input("3) Paste the full redirected URL here:\n> ").strip()
    code = urllib.parse.parse_qs(urllib.parse.urlparse(redirected).query).get("code", [None])[0]
    if not code:
        raise SystemExit("ERROR: no ?code= found in that URL.")
    _save(_post_token({"grant_type": "authorization_code", "code": code, "redirect_uri": cb}))
    print("OK: tokens saved to", TOKEN_FILE)


def _access_token() -> str:
    tok = _load()
    age = time.time() - tok.get("_obtained_at", 0)
    if age > REFRESH_MAX_AGE:
        raise SystemExit("ERROR: Schwab refresh token expired (>7 days). "
                         "Re-run: python3 schwab.py login")
    if age > (tok.get("expires_in", 1800) - 120):  # refresh a bit early
        tok = _post_token({"grant_type": "refresh_token", "refresh_token": tok["refresh_token"]})
        _save(tok)
    return tok["access_token"]


def _get(path: str, params: dict) -> dict:
    url = f"{MARKET}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {_access_token()}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


# --- market data ------------------------------------------------------------
def get_price_history(ticker: str, years: float = 15) -> list[dict]:
    """Daily OHLC rows oldest->newest. Shape matches data.py's fetch_daily()."""
    period = min(20, max(1, int(years)))  # Schwab caps the 'year' period at 20
    js = _get("/pricehistory", {
        "symbol": ticker.upper(), "periodType": "year", "period": period,
        "frequencyType": "daily", "frequency": 1, "needExtendedHoursData": "false",
    })
    rows = []
    for c in js.get("candles", []):
        rows.append({
            "date": datetime.fromtimestamp(c["datetime"] / 1000, timezone.utc).strftime("%Y-%m-%d"),
            "o": float(c["open"]), "h": float(c["high"]), "l": float(c["low"]),
            "c": float(c["close"]), "v": float(c.get("volume") or 0.0),
        })
    if not rows:
        raise RuntimeError(f"Schwab returned no candles for {ticker}")
    return rows


def get_quote(ticker: str) -> dict:
    js = _get("/quotes", {"symbols": ticker.upper()})
    return js.get(ticker.upper(), js)


def get_option_chain(ticker: str, strike_count: int = 6) -> dict:
    """ATM Greeks + IV for the nearest expiration — real [LIVE] Greeks."""
    js = _get("/chains", {
        "symbol": ticker.upper(), "contractType": "ALL", "strikeCount": strike_count,
        "includeUnderlyingQuote": "true", "strategy": "SINGLE",
    })
    underlying = js.get("underlyingPrice")
    out = {
        "ticker": ticker.upper(), "underlying_price": underlying,
        "fetched_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "nearest": {},
    }

    def nearest_atm(expmap: dict):
        if not expmap:
            return None, None
        exp = sorted(expmap.keys())[0]  # soonest expiration
        contracts = [o for arr in expmap[exp].values() for o in arr]
        if not contracts:
            return exp, None
        atm = min(contracts, key=lambda o: abs(float(o.get("strikePrice", 0)) - (underlying or 0)))
        return exp, atm

    for side, key in (("call", "callExpDateMap"), ("put", "putExpDateMap")):
        exp, atm = nearest_atm(js.get(key, {}))
        if not atm:
            continue
        out["nearest"][side] = {
            "expiration": exp, "strike": atm.get("strikePrice"),
            "delta": atm.get("delta"), "gamma": atm.get("gamma"), "theta": atm.get("theta"),
            "vega": atm.get("vega"), "rho": atm.get("rho"),
            "iv": atm.get("volatility"),  # Schwab reports IV as 'volatility'
            "bid": atm.get("bid"), "ask": atm.get("ask"), "open_interest": atm.get("openInterest"),
        }
    return out


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    cmd = argv[1]
    if cmd == "login":
        login()
        return 0
    if len(argv) < 3:
        print("ERROR: need a ticker, e.g. python3 schwab.py quote NVDA")
        return 2
    t = argv[2]
    if cmd == "quote":
        print(json.dumps(get_quote(t), indent=2))
    elif cmd == "history":
        yrs = float(argv[argv.index("--years") + 1]) if "--years" in argv else 15
        rows = get_price_history(t, yrs)
        print(f"{t.upper()}: {len(rows)} bars {rows[0]['date']}->{rows[-1]['date']} last {rows[-1]['c']}")
    elif cmd == "chain":
        print(json.dumps(get_option_chain(t), indent=2))
    else:
        print("Unknown command:", cmd)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
