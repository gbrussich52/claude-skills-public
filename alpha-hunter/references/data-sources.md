# Alpha Hunter — Data Sources & Provenance Rules

Loaded on demand. Free, no-API-key sources for live data and backtests, plus the
honesty rules for citing them. Cost-optimized: nothing here requires a paid feed.

## Rule Zero — never quote a level or a probability from memory

Training data is stale. Before stating any price, level, or statistic:
1. Pull it live (below), OR
2. Compute it (`scripts/backtest.py`), OR
3. Say you couldn't and label the number an estimate.

Every quoted number carries a **provenance tag**:
- `[LIVE <source> <timestamp>]` — freshly fetched
- `[BACKTEST n=… CI95=…]` — output of `scripts/backtest.py`
- `[BASE RATE: <citation>]` — a published, sourced statistic
- `[ESTIMATE]` — model reasoning, not measured. Never dress this as backtested.

## Sources (priority order)

`data.py` tries these in order for daily OHLC: Schwab → Yahoo → Tiingo.

| Asset | Source | Endpoint / Tool | Notes |
|---|---|---|---|
| Equities/ETFs/options (PRIMARY) | **Charles Schwab Trader API** | `scripts/schwab.py` (`api.schwabapi.com/marketdata/v1`) | Real quotes, OHLC history, **option chains w/ Greeks+IV**. Needs approved app + `login`. |
| Equities/ETFs (fallback, EOD daily) | Yahoo Finance chart API | `https://query{1,2}.finance.yahoo.com/v8/finance/chart/<TICKER>` (browser UA; rate-limits bursts) | Keyless. `data.py` rotates query1↔query2 + backs off. |
| Equities (reliable fallback) | Tiingo | `https://api.tiingo.com/tiingo/daily/<TICKER>/prices?token=…` | Free key (`TIINGO_KEY`). Used only if Yahoo fails. |
| ~~US equities EOD~~ | ~~Stooq~~ | **DEAD** — added a JavaScript proof-of-work anti-bot wall; unusable from scripts. | Removed 2026-07-16. |
| Crypto | CoinGecko | `https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd` | Keyless. |
| Prediction markets | **Kalshi** (`scripts/kalshi.py`) | `api.elections.kalshi.com/trade-api/v2` (keyless read; live-verified 2026-07-16) | YES price = implied probability. `search`/`series`/`market` commands. |
| Macro / rates | FRED | `https://fred.stlouisfed.org/graph/fredgraph.csv?id=<SERIES>` | Key optional. |
| Options IV / Greeks | **Schwab** (above) | `scripts/schwab.py chain <TICKER>` | The only real Greeks source here — else `[ESTIMATE]`. |

## Options data reality check

There is **no dependable free real-time options chain / IV surface**. Consequences:
- Greeks and IV percentile are usually **reasoned/estimated**, not measured — tag `[ESTIMATE]`.
- If the user has a broker/data terminal, ask them to paste the chain or IV rank; then it becomes `[LIVE user-provided]`.
- Do not invent an IV percentile. Say "IV context unavailable free — provide chain or treat directionally."

## Sandbox / network note (Claude Code)

The scripts need egress to the hosts in the table above (Yahoo Finance, Tiingo,
CoinGecko, Kalshi, FRED, and Schwab if configured). Under the Claude Code sandbox
those hosts may not be allowlisted, so a plain `python3 backtest.py` may fail with
a network error. Options, in order of preference:
1. Run the script with the sandbox disabled for that one command, or
2. Add the host via `/sandbox`, or
3. Use the harness `WebFetch` tool on the CSV URL and parse the returned text.

The scripts fail **loudly** on network errors — they never silently return fake data.

## Freshness discipline

- Stooq daily bars are **end-of-day**; they lag intraday. Label as such.
- After hours / pre-market: options spreads are wide and quotes thin. State it and
  recommend re-confirming during regular hours before execution.
