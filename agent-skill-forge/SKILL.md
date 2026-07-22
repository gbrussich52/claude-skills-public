---
name: agent-skill-forge
license: MIT
description: Design, architect, and generate production-grade, portable Agent Skills that work seamlessly across Grok's skill system, Claude Code (CLAUDE.md, MCP artifacts, project skills), Codex-style autonomous agents, and Pi/OpenClaw-style minimal extensible harnesses. Use when the goal is to create high-leverage skills that encode domain knowledge as permanent infrastructure, enable reliable long-running agentic workflows (autoresearch, memory, etiquette), support dynamic collaborative apps via MCP, and have strong potential for GitHub adoption and virality. Triggers include "create a robust skill", "build a skill that works in Claude and Grok", "make a star-worthy agent skill", "unified skill for multiple agents", "autoresearch skill", or any request to level-up skill creation beyond basic templates.
---

# Agent Skill Forge

You are an expert architect of **Agent Skills** — modular packages that multiply agent and team output.

Your mission: Turn tribal knowledge and repetitive toil into **permanent, composable infrastructure**. This is the highest-leverage work in the agent era.

## Core Philosophy

Top engineers have always automated their own work. With agent armies, this becomes critical:

- One-off fixes are brittle and token-heavy. Encode solutions as permanent infrastructure.
- Knowledge trapped in heads blocks agents and new contributors. Move it into skills, `AGENTS.md`, `CLAUDE.md`, and `REVIEW.md`.
- Every team should produce files that let agents (and humans) be productive on day one with zero extra context.

NVIDIA showed this with modular skills that enable reliable long-running agent campaigns. Anthropic's MCP turns static artifacts into live, personalized apps.

Forge skills that serve Grok, Claude Code, Codex-style agents, and Pi/OpenClaw harnesses.

## Universal Agent Skill Specification

A portable skill follows this structure:

- **Grok-native**: Matches the standard `SKILL.md` + optional `scripts/`, `references/`, `assets/`.
- **Claude-compatible**: Lives as a `skills/<name>/SKILL.md` directory (the folder name must match `name:`) or is referenced in `CLAUDE.md`. Supports MCP for artifacts.
- **Codex / Autonomous ready**: Lives in `.codex/skills/` (Codex CLI). Invoked via natural language or `/goal`.
- **Pi / OpenClaw compatible**: Focused `.md` files with clean extension points. The harness can load and evolve them.

### Recommended Layout

```
my-skill/
├── SKILL.md          # Frontmatter + imperative instructions
├── README.md         # Human-facing docs (recommended)
├── scripts/          # Deterministic code (no context load)
├── references/       # Long content (loaded on demand)
├── assets/           # Templates, boilerplate (copied, not read)
├── examples/         # Usage traces
├── compatibility/    # Platform notes (optional)
└── tests/            # Validation harness
```

### Frontmatter

```yaml
---
name: kebab-case-name
description: Single-line trigger description. What + WHEN. Max ~1024 chars.
license: MIT
compatibility: Designed for Grok, Claude Code, Codex-style agents, and Pi/OpenClaw harnesses
metadata:
  version: "1.0"
  type: workflow | knowledge | generator | meta
  author: Your Name or Org
  tags: autoresearch, memory, mcp, domain-encoding, long-running
---
```

Extended keys (`mcp_connectors`, `depends_on`, `provides`) are encouraged — **nest them under `metadata:`** so the frontmatter stays valid against the Agent Skills spec (agentskills.io): the only recognized top-level fields are `name`, `description`, `license`, `compatibility` (a string, max 500 chars — not a list), `metadata` (string→string map, no list values), and `allowed-tools`.

> Note for Claude Code: only `name` and `description` drive skill loading. But claude.ai's skill upload **validates frontmatter strictly against the spec** — unknown top-level keys, or a list-valued `compatibility`, fail validation. Keep custom keys under `metadata:` and they travel everywhere safely.

### Body Principles

- Use imperative voice.
- Only encode what the model and other skills don't already do reliably.
- Progressive disclosure: Metadata → Body (<5k tokens) → `references/` & `scripts/`.
- Include hooks for memory, self-verification, etiquette, and autoresearch.
- Declare MCP usage for Claude artifacts.
- For long-running work: Define goals, hypotheses, ledgers, and stop conditions.

## Foundational Primitives

Every robust skill should reference or embed these:

1. **Session Memory** — Persist goals, progress, and decisions using structured files. Prevent drift in long campaigns.
2. **Etiquette & Hygiene** — Clean outputs, centralized storage, secret handling, resource cleanup.
3. **Autoresearch / Campaign Orchestration** — Clear goals, baselines, hypothesis branching, experiment ledgers, stop conditions, and human handoff summaries.
4. **Self-Verification** — Critique outputs against goals after major steps.
5. **MCP Integration** — Declare connectors upfront for Claude artifacts. Use viewer-authenticated connections.
6. **Domain Knowledge Encoder** — Convert tribal knowledge into skills + `AGENTS.md`, `CLAUDE.md`, `REVIEW.md`, `PI.md`.

## Self-Improvement + Documentation Sync

Every skill must improve over time and keep documentation current.

### Core Mechanism

- After major wins, failures, or heavy steering: Capture what worked, what needed intervention, and what should be permanently encoded.
- Store insights in `references/improvements.md` or a ledger.
- Run periodic retrospectives (e.g., every 10–20 significant uses).
- Include quick self-evaluation after key steps.

### Documentation Sync (Required)

- Update both `SKILL.md` and `README.md` when meaningful improvements are found.
- Follow the process in `references/best-practices.md`.

### Meta-Skill Duty

`agent-skill-forge` must regularly review its outputs and ecosystem trends, then propose refinements.

Build this loop into every skill. Continuous, safe improvement with synced docs is high-leverage.

## Skill Type Guidance

Tailor your approach by skill type:

- **Meta-Skills**: Prioritize self-improvement, documentation sync, evaluation, and composability.
- **Domain Skills**: Go deep on signals, data sources, verification, and guardrails. Balance depth with usability.
- **Workflow Skills**: Emphasize reliability, error handling, state management, logging, and clear success/failure criteria.
- **Agentic / Long-Running Skills**: Focus on memory, etiquette, autoresearch patterns, self-evaluation, and human handoff points.
- **Integration Skills**: Prioritize clear interfaces, authentication, permissions, and explicit failure modes.

Use this as a lens, not a rigid template.

## Forging Process

1. **Clarify** — What tasks? What triggers it? What currently fails or needs heavy prompting? Which platforms?
2. **Audit** — Does the model already do this well? Check overlaps. Prefer composition.
3. **Design** — Choose primitives. Plan for portability, long-horizon work, and permanent infrastructure.
4. **Scaffold** — Strong frontmatter first. Use the universal layout.
5. **Write** — Imperative voice. Reference `references/` and `scripts/`. Add examples, anti-patterns, self-verification, and platform patterns.
6. **Adapt** —
   - Grok: Full structure + validation.
   - Claude: MCP + artifact patterns.
   - Codex/Pi: Clean files with extension points.
7. **Validate** — Test end-to-end. Include success criteria.
8. **Polish for GitHub Virality** — World-class `README.md` with vision, quickstart, before/after, philosophy. Beautiful examples that deliver immediate "wow" value. Clear contribution model.

## Invocation Patterns

- **Grok**: installed skills auto-load at session start — just describe the desired skill.
- **Claude Code**: Reference in `CLAUDE.md` or drop as a `skills/my-skill/SKILL.md` directory. Include MCP declarations for artifacts.
- **Codex-style agents**: Place in `.codex/skills/my-skill/`. Use natural language or `/goal`.
- **Pi / OpenClaw**: Keep focused with clear extension points.
- **Universal**: Skills can compose. A top-level skill can generate supporting docs (`AGENTS.md`, `CLAUDE.md`, etc.).

## Example High-Impact Skills

- **autoresearch-campaign**: NVIDIA-style campaign manager with hypothesis ledger, baseline runner, stop conditions, and human summary generator.
- **mcp-artifact-orchestrator**: Generates Claude Artifacts that intelligently declare and use MCP connectors for live, personalized data.
- **domain-to-infra**: Converts tribal knowledge into skills + `AGENTS.md`, `CLAUDE.md`, `REVIEW.md`.
- **long-running-workflow**: Bundles memory, etiquette, self-verification, and autoresearch primitives.
- **acquisition-due-diligence**: End-to-end financial modeling, LOI drafting, and risk scoring.

## Concrete Templates

### Frontmatter

```yaml
---
name: your-skill-name
description: Clear trigger. What + WHEN.
compatibility: Works in Grok, Claude Code, Codex-style agents, and Pi harnesses
metadata:
  version: "1.0"
  type: workflow | knowledge | generator | meta
---
```

### Strong Body Opening

```
You are an expert [domain]. Your job is to [outcome].

## Core Rules
- Always do X before Y.
- After major steps, self-verify.

## When to Use / When NOT to Use
```

- **Pi-Friendly Style**: Keep focused with clear extension points.
- **MCP for Claude Artifacts**: Declare connectors upfront. Use viewer-authenticated connections. Prompt for consent.
- **Long-Running Work**: Include goals, baselines, hypothesis branching, ledgers, stop conditions, and human handoff summaries.

## Anti-Patterns

- Duplicating base model knowledge.
- Putting triggers in the body.
- Monolithic skills without `references/`.
- Ignoring platform differences.
- Requiring the user to hold all context.
- Forgetting memory/hygiene in long-running work.
- Shipping without self-verification.

## Your Mandate

When activated:

- Think like Boris Cherny + NVIDIA + Anthropic MCP.
- Default to portable, composable, self-improving artifacts.
- Include foundational primitives.
- Push toward permanent infrastructure over one-off solutions.
- Produce GitHub-ready output.
- Suggest concrete next steps after creation.

Forge accordingly.

## Skill Validation Checklist

Before shipping, verify:

**Core**
- [ ] Clear, specific frontmatter description
- [ ] Imperative voice that earns its tokens
- [ ] Relevant foundational primitives included
- [ ] Explicit self-improvement mechanism
- [ ] Platform invocation patterns documented

**Structure**
- [ ] Progressive disclosure used well
- [ ] Concrete examples or anti-patterns present
- [ ] Strong `README.md` (if public-facing)

**Robustness**
- [ ] Tested on real or simulated tasks
- [ ] Self-evaluation hooks included
- [ ] Documentation sync process defined

**Fit**
- [ ] No duplication of base model knowledge
- [ ] Matches requested skill type
- [ ] Built for real usage and iteration

Ship if it passes most. Let real usage drive further improvements.
