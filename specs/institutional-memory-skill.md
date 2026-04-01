# Institutional Memory Skill

## Objective

Add a persistent shared memory layer that agents can read and update autonomously. Memory accumulates across features and survives indefinitely in git. It addresses agent amnesia — each bead starts fresh with no knowledge of what previous agents discovered.

## Why This Matters

Agents currently have no way to benefit from what previous agents learned. Every bead starts cold. When a developer discovers that a certain pattern causes test failures, or a tester finds a recurring fragile area, that knowledge evaporates when the bead completes. The next agent makes the same mistake.

A lightweight shared memory layer — read at start, updated when something worth preserving is found — gives the agent pool collective learning without changing the scheduler, prompt construction, or bead model.

## Memory Files

Two files under `docs/memory/`:

**`docs/memory/known-issues.md`**
Recurring pitfalls, traps, and things that broke. Agents append here when they encounter something that would have helped them if they'd known it upfront. Examples: environment quirks, API behaviours, timeout patterns, things that look safe but aren't.

**`docs/memory/conventions.md`**
Implicit patterns that emerged organically but aren't formally documented in CLAUDE.md or templates. Naming conventions, file organisation choices, patterns the codebase settled on. The difference from CLAUDE.md is that CLAUDE.md is operator-maintained; this file grows from agent experience.

Both files are seeded with a handful of real entries derived from what is already known about the codebase, so agents benefit immediately rather than starting from a blank slate.

## The Skill

One skill: `memory`. It describes both reading and writing in one place — splitting into separate read/write skills adds complexity without benefit since the distinction is about who may write, not a technical separation.

The skill instructs agents to:
- Read both files at the start of every bead, before touching any code
- Treat the content as ambient context that may or may not be relevant to the current task
- Append a new dated entry when they discover something project-wide, reusable, and not bead-specific
- Keep entries short (one or two sentences)
- Never rewrite or delete existing entries — memory is append-only

What qualifies as worth writing:
- Something that would have changed how the agent approached the task if known upfront
- A pattern or pitfall that is likely to recur across future beads
- A convention that is not obvious from reading the code

What does not qualify:
- Anything specific to the current bead only
- Information already in CLAUDE.md or guardrail templates
- Anything that belongs in a spec or design document

## Access Control

| Agent | Read | Write |
|-------|------|-------|
| Planner | yes | `conventions.md` only |
| Developer | yes | both |
| Tester | yes | both |
| Documentation | yes | no |
| Review | yes | no |

Access control is enforced through the skill instructions, not technically. The skill text for documentation and review agents explicitly says "read only — do not append entries".

## Implementation

### 1. Memory files

Create `docs/memory/known-issues.md` and `docs/memory/conventions.md`. Seed each with real entries from what is already known:

**known-issues.md seeds:**
- Running `unittest discover` takes 3+ minutes and often hits the agent timeout — always target a specific module instead
- Claude Code occasionally wraps JSON output in markdown code fences (` ```json ... ``` `), which can cause structured output parsing to fail
- Always `cd` back to the project root after any operation inside a worktree — running orchestrator commands from inside a worktree creates nested paths and corrupts state
- The `VIRTUAL_ENV` environment variable must be cleared before spawning agent subprocesses, otherwise `uv run` warns and may background long-running commands

**conventions.md seeds:**
- All orchestrator commands must be prefixed with `uv run`
- Bead IDs now use UUID format (`B-{8 hex chars}`); old sequential IDs (`B0001`) still coexist and both formats are valid
- Tests use `unittest`, not pytest — run with `uv run python -m unittest tests.<module> -v`
- The scheduler reads config at invocation time, not at startup — config changes take effect on the next bead without restarting

### 2. Skill files

Write `.claude/skills/memory/SKILL.md` with the instructions above, tailored for Claude Code agents.

Write `.agents/skills/memory/SKILL.md` with equivalent instructions for Codex agents.

### 3. Skill allowlist

Add `memory` to `AGENT_SKILL_ALLOWLIST` in `src/codex_orchestrator/skills.py` so it is bundled into every agent's isolated execution root automatically.

## Out of Scope

- `feature-summaries.md` — deferred until coalesced review beads exist (requires `planner-bead-sizing-and-coalesced-followups` to land first)
- ADRs and architect agent — separate future feature
- Technical enforcement of read/write rules — skill instructions are sufficient for now
- Changes to `prompts.py` or the scheduler — no prompt injection, agents invoke the skill themselves
- Memory compaction or summarisation — append-only for now; revisit when files grow large

## Acceptance Criteria

- `docs/memory/known-issues.md` exists with at least 4 seeded entries
- `docs/memory/conventions.md` exists with at least 4 seeded entries
- `.claude/skills/memory/SKILL.md` exists and describes read/write rules and file locations
- `.agents/skills/memory/SKILL.md` exists with equivalent content
- `memory` is in `AGENT_SKILL_ALLOWLIST` and is copied into isolated execution roots
- Skill instructions correctly restrict documentation and review agents to read-only
- All existing tests pass

## Files to Modify

| File | Change |
|------|--------|
| `docs/memory/known-issues.md` | Create with seeded entries |
| `docs/memory/conventions.md` | Create with seeded entries |
| `.claude/skills/memory/SKILL.md` | Create skill for Claude Code agents |
| `.agents/skills/memory/SKILL.md` | Create skill for Codex agents |
| `src/codex_orchestrator/skills.py` | Add `memory` to `AGENT_SKILL_ALLOWLIST` |
