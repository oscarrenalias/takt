---
name: spec-management
description: Working with specs in codex-agent-orchestration. Use when writing a new spec, moving specs between draft/planned/done, planning a spec (persisting beads), or reviewing what specs exist. Covers spec format, folder conventions, and the plan->persist workflow.
tools: Read, Write, Edit, Glob, Grep, Bash
user-invocable: false
---

# Spec Management for codex-agent-orchestration

## Folder Structure

```
specs/
  drafts/     # Spec written, not yet planned (no beads created)
  planned/    # Planner has run and beads are persisted; implementation in progress
  done/       # All beads merged to main; feature is live
```

**Rule:** A spec lives in exactly one folder. Move it forward only when the transition condition is met.

| Transition | Condition |
|---|---|
| drafts → planned | `orchestrator plan --write <spec>` has been run and beads created |
| planned → done | All beads in the feature tree are `done` AND the feature branch has been merged to main |

Never move a spec to `done/` before merging. Never move to `planned/` before beads are persisted.

---

## Writing a Spec

### Required sections

1. **Objective** — One paragraph: what problem this solves and why it matters
2. **Problems to Fix** — Numbered list of specific issues, with current state described concretely
3. **Changes** — What to build: new files, modified files, new behaviours. Be prescriptive — include function signatures, field names, config keys, CLI flags where known
4. **Files to Modify** — Table: file path → what changes
5. **Acceptance Criteria** — Bullet list of verifiable conditions the implementation must satisfy
6. **Pending Decisions** — Any open questions that must be resolved before planning. Mark resolved decisions inline (strikethrough + resolution)

### Good spec practices

- Be prescriptive: agents implement exactly what the spec says. Vague specs produce vague implementations.
- Include concrete examples (function signatures, config YAML, CLI output) where helpful
- Reference existing code by file:line when the change is to a specific location
- Keep acceptance criteria verifiable — avoid "the code is clean" or "it works correctly"
- Resolved pending decisions should stay in the spec with their resolution, not be deleted

### What NOT to put in a spec

- Implementation details the agent should decide (e.g. variable names, internal algorithm choice)
- Speculative future features — scope to what's actually being built
- Duplicate content from CLAUDE.md

---

## Planning a Spec (Persisting Beads)

```bash
# Dry run — prints bead graph as JSON, does NOT create beads
uv run orchestrator plan specs/drafts/my-spec.md

# Persist — creates beads in storage
uv run orchestrator plan --write specs/drafts/my-spec.md
```

**Always use `--write` to persist.** Without it, the planner output is printed but no beads are created.

After persisting, move the spec:
```bash
mv specs/drafts/my-spec.md specs/planned/
```

Then commit both the beads and the spec move together.

---

## Checking Spec / Bead Status

```bash
# Overall counts
uv run orchestrator summary

# Scoped to one feature
uv run orchestrator summary --feature-root <bead_id>

# All beads as table
uv run orchestrator bead list --plain

# Find the feature root ID for a spec
uv run orchestrator bead list --plain | grep -i "<spec keyword>"
```

To find which bead corresponds to a spec, search by title keyword. The feature root bead (where `bead_id == feature_root_id`) is the top-level planner bead.

---

## Moving a Spec to Done

Conditions that must ALL be true:
1. `uv run orchestrator summary --feature-root <id>` shows `ready=0, in_progress=0, blocked=0`
2. The feature branch has been merged to main via `orchestrator merge <id>`
3. Tests pass on main

Then:
```bash
mv specs/planned/my-spec.md specs/done/
git add specs/
git commit -m "Move my-spec to done/ after merge"
```

---

## Merging a Feature

Use `orchestrator merge`, never `git merge` directly:

```bash
uv run orchestrator merge <bead_id>
```

This now (as of the safe-merge feature) does:
1. Merges `main` into the feature branch (conflict check)
2. If conflict: creates a `merge-conflict` bead, exits with instructions
3. Runs `config.common.test_command` (currently: `uv run python -m unittest discover -s tests`)
4. If tests fail: creates a `merge-conflict` bead, exits with instructions
5. If all clear: `git merge --no-ff` into main

If a merge-conflict bead is created, run the scheduler then retry:
```bash
uv run orchestrator --runner claude run --once --max-workers 4
uv run orchestrator merge <bead_id>  # retry
```

**Flags:**
- `--skip-rebase` — skip the main-into-feature sync step
- `--skip-tests` — skip the test gate

---

## Current Drafts Quick Reference

Check `specs/drafts/` for specs awaiting planning. As of the last check:
- `pipeline-efficiency-improvements.md` — pytest parallelisation, prompt trimming, structured handoffs
- `project-onboarding.md` — `orchestrator init` interactive setup command
- `tui-observability-and-reactive-scheduler.md` — reactive scheduler, live worker view
- `codebase-refactoring.md` — module splitting (low priority)

Check `specs/planned/` for specs with active bead trees:
- `bead-graph-diagram.md` — `orchestrator bead graph` Mermaid command (B-0513c78c, pending merge)
- `safe-merge-with-rebase-and-tests.md` — safe merge flow (B-af576483, merged)

---

## Common Mistakes to Avoid

- **Running `orchestrator plan` without `--write`** — looks like it worked but nothing is persisted
- **Moving spec to `planned/` before beads exist** — confusing if beads are later found missing
- **Moving spec to `done/` before merging** — spec says done but code isn't on main
- **Using `git merge` instead of `orchestrator merge`** — bypasses rebase + test gate
- **Creating beads inside an already-merged feature tree** — those beads need their own merge cycle; use standalone beads (no `--parent-id`) for fixes to merged features
- **Adding new beads to B-af576483 or other merged roots** — same issue as above
