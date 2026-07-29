# Claude Code Skills

Shareable editions of Claude Code skills I've authored and use daily. Each skill is a self-contained folder you can drop into your own setup.

## Install

Copy any skill folder into your Claude Code skills directory:

```bash
git clone https://github.com/gbrussich52/claude-skills-public.git
cp -r claude-skills-public/dynamic-workflows ~/.claude/skills/
```

Claude Code picks up new skills at the start of your next session. Trigger them naturally ("use a dynamic workflow for this") or explicitly with `/skill-name`.

## Skills

| Skill | What it does | Requires |
|-------|--------------|----------|
| [dynamic-workflows](dynamic-workflows/) | Decides when and how to orchestrate multi-agent workflows (fan-out, adversarial verification, tournaments, loop-until-done) instead of a single long context — and how to build them well: pipeline-first structure, partial-failure resilience, token budgeting. | Claude Code with the `Workflow` tool (dynamic workflows) |
| [alpha-hunter](alpha-hunter/) | Multi-asset trade analysis with honesty rails: multi-timeframe + regime-aware setups, confluence scoring, and a strict data-provenance protocol — every number is tagged LIVE / BACKTEST / BASE RATE / ESTIMATE, backed by bundled scripts for real price data, measured base rates (with confidence intervals), option Greeks via Schwab, and live Kalshi odds. | Python 3; optional free Tiingo key and Schwab developer app for full data |
| [agent-skill-forge](agent-skill-forge/) | Meta-skill for authoring production-grade, portable Agent Skills that validate against the Agent Skills spec (agentskills.io) and work across Claude Code, claude.ai, Grok, and Codex-style agents — layout, frontmatter discipline, progressive disclosure, self-improvement ledgers, and a validation checklist. | None |
| [pdf-forms](pdf-forms/) | Fills PDF forms end to end — extracts fields and their real checkbox states, OCRs scanned pages, fills, flattens so values can't be extracted or edited, and verifies the result actually rendered. Handles AcroForm, hybrid XFA (IRS W-9), and flat/printed forms with no fields. | [uv](https://docs.astral.sh/uv/); poppler for rendering |

**Not financial advice:** `alpha-hunter` is an analysis framework for educational use. Markets carry risk of loss; verify everything and make your own decisions.

More skills will be added as shareable editions are prepared.

## Philosophy

Skills accumulate two layers: **portable technique** (how the method works) and **personal context** (how it applies to your specific projects). These are the technique layers — the context layer stays home. When you install one, add your own project-specific guidance section at the bottom; that's where a skill earns compounding returns.

## License

MIT — see [LICENSE](LICENSE).
