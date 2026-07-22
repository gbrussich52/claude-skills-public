---
name: dynamic-workflows
description: Use when asked to "use a dynamic workflow", "build a workflow for this", "orchestrate sub-agents", "fan-out and synthesize", "run adversarial verification", "run a tournament", "create a harness for this" — or when a task is parallel, verification-heavy, or long-running: due diligence, idea evaluation, market/risk analysis, large refactors, deep verification, synthesizing large amounts of information. Decides when/how to use Claude Code dynamic workflows and crafts, ships, and runs them. Always save useful workflows for reuse.
license: MIT
compatibility: Requires Claude Code with the Workflow tool (dynamic workflows)
---

# Dynamic Workflows in Claude Code

Dynamic workflows let **Claude write and orchestrate its own custom multi-agent harness** (a JavaScript script run via the `Workflow` tool) tailored to the exact task, instead of relying on the default single-context harness.

They exist to combat three failure modes of long single contexts:
- **Agentic laziness** — stopping early on complex tasks
- **Self-preferential bias** — preferring its own prior output when verifying
- **Goal drift** — especially after compaction

Sub-agents each get a **clean context window and a focused goal**, which is what makes the orchestration worth its token cost.

## When to Use

- Complex research with multiple angles (market, financials, legal, ops, risks)
- Evaluating ideas or targets needing thorough, unbiased analysis
- Tasks prone to agentic laziness, self-preferential bias, or goal drift in a single context
- Parallel exploration: multiple hypotheses, screening at scale, idea tournaments, root-cause analysis
- Verification-heavy work: fact-checking claims, adversarial review, compliance checks
- Large refactors/migrations with many callsites (fan-out per change + adversarial review)
- Mining past sessions/incidents/data for patterns and turning them into rules

## How to Trigger

- Explicitly: "Use a dynamic workflow to research this business idea…"
- Trigger word: "ultracode [task]"
- Pattern-led: "Fan out across [aspects] and adversarially verify"

## The Real Primitives (Workflow script API)

A workflow is a JS script starting with `export const meta = { name, description, phases }`, then a body using:

- `agent(prompt, opts)` — spawn a sub-agent; returns its text, or a validated object when `opts.schema` is set. Key opts: `schema`, `model`, `effort`, `phase`, `label`, `isolation: 'worktree'` (isolated workspace — use only when agents mutate files in parallel), `agentType`.
- `pipeline(items, stage1, stage2, …)` — run each item through all stages independently, **no barrier** between stages. This is the **default** for multi-stage work.
- `parallel(thunks)` — run tasks concurrently with a **barrier** (awaits all). Use only when stage N genuinely needs all of stage N-1 (dedup, early-exit on zero, cross-item comparison).
- `phase(title)` / `log(msg)` — progress grouping and narration.
- `budget` — the turn's token target; loop on `budget.remaining()` to scale depth.

**Default to `pipeline()`**; reach for a `parallel()` barrier only when you need every prior result at once. Workflows **resume** if interrupted (same session, `resumeFromRunId`).

## Composable Patterns

1. **Fan-out + synthesize** — split into parallel sub-tasks (clean context each), synthesizer merges after. Prevents cross-contamination.
2. **Adversarial verification** — for each worker output, spawn a verifier (ideally N skeptics prompted to *refute*) that checks against an explicit rubric. Kill findings a majority refute.
3. **Tournament** — multiple agents attempt the *same* task different ways; pairwise judging picks a winner. Best for taste/subjective calls (naming, design, positioning).
4. **Generate + filter** — generate many options in parallel, then rank/dedupe/filter.
5. **Classify + act** — a classifier routes each item to the right specialized agent.
6. **Loop-until-done** — spawn rounds until a stop condition (no new findings, all tests pass). Best for unknown-scope research/debugging.

Compose them: fan-out research angles → adversarial verifier per angle → tournament on top recommendations → synthesize.

## Decision Framework — When to Escalate

**Strong yes:**
- Large refactors/migrations with many callsites (fan-out per change + adversarial review per fix)
- Deep verification of factual/technical claims against the actual codebase or sources
- Multi-perspective adversarial analysis (e.g., a business plan from investor/customer/competitor views)
- Tournaments / generate-and-filter for subjective, high-stakes choices
- Root-cause / post-mortem with multiple independent hypotheses + verifier panel
- Triage/classification at scale with dedup + action (quarantine untrusted input)
- Skill/feature evals against rubrics, agents in worktrees
- Loop-until-done research where scope is unknown
- Mining many sessions/artifacts for patterns + adversarial validation

**Usually no** (stick with a plan file + standard harness):
- Day-to-day coding and feature work
- Research/planning that fits comfortably in one context
- Anything where token + orchestration overhead isn't justified by reduced laziness/bias/drift risk

**Borderline** — start with a *quick workflow* or a single adversarial verification step rather than full fan-out.

Always document the decision **and** token budget in the plan or workflow prompt.

## Partial-Failure Resilience at Fleet Scale

At 50+ agent() calls, expect a nonzero rate of transient failures — API connections dropping mid-response, or a schema-validated call exhausting its StructuredOutput retry cap on a complex/large output. This is normal, not a sign the workflow is broken. Design for it:

- **Prefer many small agent() calls over few giant ones** for any stage whose schema or prompt is complex — a smaller, more constrained call is less likely to hit the retry cap than one asked to synthesize a huge amount of structured output in one shot.
- **For fix/mutation stages (agents that edit files or run git commands): make each agent leave a clean, inspectable working-tree state as it goes**, not just at the very end. If the connection drops after the agent has made real edits but before its final report/commit, you should be able to `git status`/`git diff` the target directory and see exactly what it already did — don't rely on the agent's return value being the only record of its work.
- **Before assuming a failed pipeline() item needs a full re-run from scratch, check whether it left usable partial work** (uncommitted edits, partial output) and consider a targeted "review what's already here, finish it, and commit" follow-up agent instead of blindly repeating the original prompt — this avoids duplicate work and wasted tokens.
- **`pipeline()`/`parallel()` resolve a failed thunk to `null`, not a thrown error** — always `.filter(Boolean)` before using results, and treat a `null` in the results array as "needs manual follow-up," not "task skipped, safe to ignore."

## Token & Cost Discipline (non-negotiable)

- Set an explicit budget in every workflow prompt, sized to the tier of work: a single adversarial-verification step ~10–30k tokens; a modest fan-out (3–6 agents, one round) ~50–150k; a full multi-dimension research harness with adversarial verify commonly runs 500k–1M. Calibrate against measured runs, not wishful numbers.
- Prefer a quick adversarial step over full orchestration when the failure modes aren't clearly present.
- Sub-agents are powerful *because* they get clean contexts — but that power costs tokens. Spend it only where it buys real risk reduction.

## Crafting Effective Workflow Prompts

Be specific about: the goal + hard success criteria; which patterns and why; explicit rubrics for verification/judging; token budget; model routing (strongest model for synthesis, cheaper models for high-volume parallel work); worktree isolation when needed; loop stop conditions; how to handle partial results / resumption.

Example structure to hand the agent:
> "Use a dynamic workflow with fan-out + adversarial verification.
> 1. Fan-out: [sub-tasks].
> 2. For each output, spawn a verifier that checks against this rubric: [detailed rubric].
> 3. Synthesize only after all verifiers pass or flag issues.
> Token budget: 15k. Use worktrees for the parallel steps."

## Example: Deep Due Diligence (high-ROI use)

When a big evaluation lands ("research buying a laundromat chain", "evaluate this e-commerce brand"), use a workflow, not a single agent:

> "Research this acquisition idea with a dynamic workflow. Fan out parallel sub-agents for: market analysis, financial due diligence, operational risks, legal/compliance, competitive landscape, automation potential. Each produces structured output. Run adversarial verifiers on key claims. Tournament the top 3 opportunities/risks. Synthesize into a Go/No-Go report with specific action items. Save the workflow as a reusable template."

Tailor the fan-out to the target: a service business gets customer concentration, recurring revenue, owner dependence, systems maturity; a SaaS gets churn, LTV, tech debt, moat.

## Saving & Shipping Workflows Inside Skills

The highest-compounding move:
1. Create a useful workflow (the `Workflow` tool auto-persists its script; note the returned path).
2. Put the `.js` file in the relevant skill's folder (e.g. `workflows/examples/`).
3. Reference it in SKILL.md as a **template** the skill adapts — not a rigid script.
4. Document when/how to use each; version your best ones.

## Response Modes

- **Decision & recommendation** (default) — analyze against the framework; recommend plan-file vs workflow vs hybrid; if workflow, name the patterns, structure, token budget, and sample prompt.
- **Prompt crafting** — deliver ready-to-use prompt language with patterns, rubrics, budgets.
- **Review & audit** — flag where a workflow/pattern helps vs. is overkill; suggest a lighter alternative.
- **Skill shipping** — extract a workflow from a session and package it into a skill.

## When *Not* to Use

Simple/one-off tasks (default harness is cheaper), low-stakes work, or when you explicitly want a single coherent narrative (assembled workflows can feel stitched).

## Sources

- Announcement: https://x.com/trq212/status/2061907337154367865 (Thariq Shihipar, Anthropic)
- Blog: https://claude.com/blog/a-harness-for-every-task-dynamic-workflows-in-claude-code

Human judgment remains the scarce input. Even with sophisticated orchestration, your taste, mid-run steering, and final review are the highest-leverage part of the loop. Save good workflows — research quality and speed compound.
