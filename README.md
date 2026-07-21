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

More skills will be added as shareable editions are prepared.

## Philosophy

Skills accumulate two layers: **portable technique** (how the method works) and **personal context** (how it applies to your specific projects). These are the technique layers — the context layer stays home. When you install one, add your own project-specific guidance section at the bottom; that's where a skill earns compounding returns.

## License

MIT — see [LICENSE](LICENSE).
