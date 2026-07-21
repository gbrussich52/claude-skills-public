# Alpha Hunter — Qualitative Research via Logged-In Subscriptions

Loaded on demand. The brokerage API gives the *numbers*; the user's paid
subscriptions give the *narrative and ratings* — read through the browser the
user is already logged into (Claude-in-Chrome). This is the qualitative layer:
catalysts, analyst view, and IBD's proprietary ratings.

## How to read them (Claude-in-Chrome)

The user is logged into WSJ (and typically Barron's — same Dow Jones account —
and IBD) in Chrome. Use the browser tools to open the relevant page and read it:

1. `tabs_context_mcp` first, then `tabs_create_mcp` a fresh tab.
2. `navigate` to the source URL for the ticker.
3. `get_page_text` / `read_page` to extract the content. Summarize; do not dump.

### Per-source starting points (verify paths live; sites change)

| Source | URL pattern | What to pull |
|---|---|---|
| WSJ | `https://www.wsj.com/market-data/quotes/<TICKER>` | Latest news/catalysts, analyst ratings summary, key dates |
| Barron's | `https://www.barrons.com/market-data/stocks/<ticker>` | Thesis/analysis pieces, sentiment |
| IBD | `https://research.investors.com/stock-checkup/nasdaq-<company>-<ticker>.aspx` (verified live 2026-07-16; e.g. `nasdaq-nvidia-nvda.aspx`) | Composite, EPS, RS, SMR, Acc/Dis ratings; Group RS; industry rank; next-earnings date |

**IBD login note:** Stock Checkup needs an **IBD Digital** subscription and a live investors.com session in Chrome (separate login from WSJ/Barron's — do NOT store the password anywhere; it's a browser cookie). If you see an "Unlock this article with IBD Digital" modal, the session isn't authenticated — stop and ask the user to sign in; never try to bypass it. The page renders ratings as normal DOM, but screenshots are the reliable read (same as WSJ).

## Technical note (learned 2026-07-16, verified live on NVDA)

WSJ market-data quote pages render the **live quote block on `<canvas>`** — `get_page_text`
returns "No text content found" even though the page is fully loaded and you ARE
logged in. Do **not** conclude it's paywalled. Read these pages via **`screenshot`**
(use `zoom` for small figures). The right-hand **KEY STOCK DATA / SHORT INTEREST**
sidebar and the **News** headline list are normal DOM and show up fine in screenshots;
the big quote number is canvas, so screenshot it rather than relying on text extraction.

## What to extract (feeds the skill's regime + catalyst legs)

- **Catalysts & dates**: earnings date, guidance, product/event, M&A chatter.
- **IBD proprietary ratings** (their real edge): Composite, Relative Strength (RS),
  EPS, Accumulation/Distribution, industry-group strength.
- **Analyst/sentiment tone** from WSJ/Barron's — as context, not gospel.

## Provenance & honesty

- Tag anything sourced here `[RESEARCH: <source>, <date>]`. It is qualitative
  context, not a measured statistic — never convert a headline into a probability.
- IBD ratings are proprietary composites; report them as-is, attributed to IBD.

## Rules of the road (ToS / ethics)

- These are the **user's own logged-in sessions**, read for the user's own
  decisions — one-off, on-demand reads. Do **not** bulk-scrape, crawl, cache
  full paywalled text to disk, or redistribute article bodies.
- Store only short summaries and the specific data points needed; never commit
  paywalled full text to the repo or memory.
- If a page needs a fresh login or hits a paywall wall, say so and ask the user
  rather than trying to circumvent it.
