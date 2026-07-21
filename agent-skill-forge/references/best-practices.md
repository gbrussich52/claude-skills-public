# Agent Skill Forge — Best Practices & Documentation-Sync Process

Loaded on demand. This is the process `SKILL.md` points to for keeping skills healthy and their docs in sync.

## Documentation-Sync Process (the one SKILL.md references)

When a meaningful improvement to a skill is discovered:

1. **Land the change in `SKILL.md` first.** That file is what the agent actually loads — it is the source of truth.
2. **Reconcile `README.md` in the same edit.** Anything user-facing that changed (new platform, new capability, new primitive, changed invocation) must be reflected. The README is a story *about* the skill; it must not describe a version that no longer exists.
3. **Log it in `references/improvements.md`.** One dated line: what changed, why, and what triggered it (a failure, heavy steering, a new ecosystem pattern).
4. **Re-run the Validation Checklist** (bottom of `SKILL.md`). If the change broke an item, fix it before shipping.

A skill whose `SKILL.md` and `README.md` disagree is a bug, not a style nit.

## Frontmatter discipline

- `name` is kebab-case and matches the folder name.
- `description` is the trigger. Front-load *when to use it*; keep under ~1024 chars. Longer descriptions dilute match accuracy.
- In Claude Code, only `name` and `description` drive loading — but claude.ai's skill upload validates frontmatter strictly against the Agent Skills spec (agentskills.io). Recognized top-level keys: `name`, `description`, `license`, `compatibility` (a string, max 500 chars — never a list), `metadata`, `allowed-tools`. Put extended keys (`mcp_connectors`, `depends_on`, `provides`, versions, tags) **under `metadata:`** so the skill validates everywhere; never depend on Claude acting on them.

## Body discipline

- Imperative voice. Every line should earn its tokens.
- Progressive disclosure: keep the body under ~5k tokens; push long content to `references/` and deterministic logic to `scripts/`.
- Prefer composition (`depends_on`) over duplicating another skill.
- Do not encode what the base model already does reliably.

## Scripts vs. references vs. assets

- `scripts/` — deterministic code the agent runs *without loading into context* (fetchers, backtests, validators). Pure-stdlib where possible; no surprise dependencies.
- `references/` — long docs loaded only when needed. One level deep.
- `assets/` — templates copied/modified, not read into context.

## Retrospective cadence

Every ~10–20 significant uses, review `references/improvements.md`: which guidance actually fired, what still required heavy steering, what should be promoted from a one-off fix into the skill body.
