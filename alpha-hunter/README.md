# Alpha Hunter

**The complete, regime-aware multi-asset trading system.**

Alpha Hunter is a high-precision skill for identifying high-conviction, executable trades across stocks, futures, prediction markets (Kalshi/Polymarket), and crypto. It combines deep multi-timeframe analysis, balanced technical + fundamental research, options Greeks, volatility surfaces, event/catalyst handling, and rigorous data verification.

## Core Capabilities

- **Multi-Timeframe Analysis** (Daily → Secular/Decade)
- **Balanced Technical + Fundamental Integration**
- **Current Market Regime Awareness** (including insider, political, and government activity)
- **Comprehensive Signal Recognition** (price action, order flow, options flow, on-chain, macro, etc.)
- **Advanced Options Framework**
  - Full Greeks (Delta, Gamma, Vega, Theta, Vanna/Charm)
  - IV & IV Percentile/Rank
  - Skew and Term Structure
  - Real options execution (specific DTE + strike recommendations)
- **Event & Catalyst Playbook** (earnings, FOMC, CPI, geopolitical shocks, CEO changes, etc.)
- **Data Verification Protocol** (waterfall approach — verify what you can, when you can)
- **Precise Trade Construction** with clear entry, stop, targets, risk/reward, and position sizing tiers
- **Self-Improvement & Ledger System**

## Best For

- Swing to positional trading across multiple asset classes
- High-conviction setups with defined risk
- Traders who want a structured, multi-angle approach rather than single-indicator or single-timeframe analysis
- Options-aware decision making (especially in elevated volatility environments)

## Key Principles

- Higher timeframes dominate.
- Confluence across technicals, fundamentals, flows, and regime beats conviction.
- Post-event reaction quality is often more important than the headline.
- Always verify current data — never rely on stale memorized levels.
- Use options deliberately (defined-risk when appropriate, especially after hours or into binary events).

## Usage

Simply describe the ticker or market you want analyzed (e.g., "Run alpha-hunter on NVDA" or "Analyze MRVL with current options context").

The skill will return:
- Regime context
- Multi-timeframe thesis
- Key signals and indicators (including Greeks, IV, RSI, skew)
- Exact trade recommendation (entry, stop, targets)
- Position sizing guidance
- Hedging / real options execution ideas (with DTE and strike guidance when relevant)
- Monitoring plan

## Real Data & Backtested Base Rates

This skill does not quote prices or win rates from memory. It ships two scripts (`scripts/`):

- **`fetch_data.py TICKER`** — pulls real recent levels (last OHLC, 20/50/200 SMAs, 20-day high/low) with a timestamp.
- **`backtest.py TICKER`** — computes *measured* historical base rates (gap-fill, breakout follow-through, oversold bounce), each with sample size and a 95% Wilson confidence interval. A rate with a small `n` or wide CI is flagged **not tradable** — no false precision.

Every number the skill states carries a provenance tag: `[LIVE]`, `[BACKTEST n=… CI95=…]`, `[BASE RATE: source]`, or `[ESTIMATE]`. It never dresses an estimate up as a backtest.

**Data sources (auto priority):** **Charles Schwab Trader API** (primary — real quotes, OHLC history, and option chains *with Greeks/IV*) → **Yahoo Finance** (free keyless fallback) → **Tiingo** (if `TIINGO_KEY` set). Schwab activates after a one-time `python3 scripts/schwab.py login`; until then the skill runs on Yahoo. Schwab is the only source of real option Greeks — free real-time options data does not exist.

**Qualitative layer:** for names, it reads the user's logged-in WSJ / Barron's / IBD via the browser for catalysts and IBD proprietary ratings (Composite, RS, EPS, Acc/Dis) — one-off reads, tagged `[RESEARCH]`, never bulk-scraped. See `references/research-sources.md` and `references/data-sources.md`.

## After-Hours / Pre-Market Note

When analysis is performed outside regular market hours, the skill will clearly state data limitations and recommend re-confirming options pricing and liquidity during market hours before execution.

## Philosophy

Alpha Hunter exists to give you **every reasonable edge** without unnecessary complexity. It checks relevant angles (technical, fundamental, options, macro, events, flows, regime, data quality) and only includes what moves the needle for decision quality.

It is designed to be sharp, decisive, and continuously improvable through its built-in self-review and ledger system.
