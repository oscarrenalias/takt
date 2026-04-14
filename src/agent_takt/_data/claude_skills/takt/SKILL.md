---
name: takt
description: 'MANDATORY: Read this entire skill before taking any action on a takt project. Defines all required CLI commands (always `uv run takt ...`), bead lifecycle rules, spec management, scheduler operation, and merge workflow. Contains a list of forbidden operations — violating them corrupts pipeline state. Non-compliance is not acceptable.'
tools: Read, Write, Edit, Glob, Grep, Bash
user-invocable: false
---

# takt

`takt` is a multi-agent orchestration system that coordinates AI workers on a shared codebase using Git worktrees. Work is broken into **beads** — discrete, agent-sized units of work tracked in `.takt/beads/`.

All commands must be prefixed with `uv run`:

```bash
uv run takt <command>
```

---

## Bead Lifecycle

Beads move through these states:

```
open → ready → in_progress → done | blocked | handed_off
```

- **open** — created, not yet ready to schedule
- **ready** — all dependencies met; eligible for the scheduler
- **in_progress** — an agent is working on it
- **done** — completed successfully
- **blocked** — cannot proceed; needs intervention
- **handed_off** — delegated to a downstream agent

Agent types: `planner`, `developer`, `tester`, `documentation`, `review`. Only `developer`, `tester`, and `documentation` mutate code.

---

## Key CLI Commands

### Status and inspection

```bash
uv run takt summary                          # counts + next actionable beads
uv run takt summary --feature-root <id>     # scoped to one feature
uv run takt bead list --plain               # all beads as table
uv run takt bead list --label <label>       # filter by label
uv run takt bead show <id>                  # single bead details (JSON)
uv run takt bead graph                      # Mermaid diagram of all beads
uv run takt bead graph --feature-root <id> # scoped to one feature
uv run takt bead graph --output graph.md   # write diagram to file
uv run takt tui                             # interactive terminal UI
```

### Creating and managing beads

```bash
# Create a bead manually
uv run takt bead create --agent developer --title "My task" --description "..."

# Add labels to a bead (idempotent)
uv run takt bead label <id> urgent api

# Remove a label
uv run takt bead unlabel <id> urgent

# Delete a bead (must have no children; open/ready/blocked only without --force)
uv run takt bead delete <id>
uv run takt bead delete <id> --force   # in_progress/done require --force
```

### Running the scheduler

```bash
# Schedule and run all eligible beads to quiescence
uv run takt --runner claude run

# Multiple parallel workers
uv run takt --runner claude run --max-workers 4

# Retry a specific bead
uv run takt retry <bead_id>
uv run takt --runner claude retry <bead_id>
```

Runner is selected via `--runner` flag, `AGENT_TAKT_RUNNER` env var, or `config.default_runner`.

---

## Planning a Spec (Creating Beads)

```bash
# Dry run — prints bead graph as JSON, does NOT create beads
uv run takt plan specs/drafts/my-spec.md

# Persist — creates beads in storage (one-shot)
uv run takt plan --write specs/drafts/my-spec.md

# Staged workflow — run the LLM once, review, then persist separately
uv run takt plan --output plan.json specs/drafts/my-spec.md  # save plan JSON for review
uv run takt plan --from-file plan.json                        # persist without re-running LLM
rm plan.json                                                  # clean up when done
```

**Always use `--write` or `--from-file` to persist.** Without one of these, no beads are created.

Use the staged workflow (`--output` + `--from-file`) when you want to inspect or edit the bead graph before committing it. The operator owns the plan file and is responsible for cleaning it up. `--output` and `--from-file` are mutually exclusive with `--write`.

After persisting, use `spec.py` to transition the spec to `planned`:

```bash
python3 <spec-py> set status planned spec-a3f19c2b
```

Then commit both the beads and the spec status change together.

---

## Scheduler Workflow

```bash
# Check what's actionable
uv run takt summary

# Run one scheduler cycle (all eligible beads, up to max-workers in parallel)
uv run takt --runner claude run --max-workers 4

# After the cycle, check progress
uv run takt summary
uv run takt bead list --plain
```

The scheduler auto-creates `-test`, `-docs`, and `-review` child beads when a developer bead completes (unless it is a corrective bead or merge-conflict bead).

---

## Merge Workflow

Use `takt merge`, never `git merge` directly:

```bash
uv run takt merge <bead_id>
```

This does:
1. Merges `main` into the feature branch (conflict check)
2. If conflict: creates a `merge-conflict` bead, exits with instructions
3. Runs the configured `test_command`
4. If tests fail: creates a `merge-conflict` bead, exits with instructions
5. If all clear: `git merge --no-ff` into main

### When a merge-conflict bead is created

Do **not** resolve conflicts manually. Let the scheduler handle it:

```bash
uv run takt --runner claude run --max-workers 4
uv run takt merge <bead_id>   # retry after scheduler resolves the conflict bead
```

**Flags:**
- `--skip-rebase` — skip the main-into-feature sync step
- `--skip-tests` — skip the test gate

---

## Moving a Spec to Done

Conditions that must ALL be true:
1. `uv run takt summary --feature-root <id>` shows `ready=0, in_progress=0, blocked=0`
2. The feature branch has been merged to main via `takt merge <id>`
3. Tests pass on main

Then use `spec.py` to transition the spec:

```bash
python3 <spec-py> set status done spec-a3f19c2b
git add specs/
git commit -m "Move my-spec to done/ after merge"
```

---

## Branch and Worktree Conventions

- **Branch naming**: `feature/{feature_root_id_lowercase}` (e.g. `B-a7bc3f91` → `feature/b-a7bc3f91`)
- **Worktree paths**: `.takt/worktrees/{feature_root_id}` (not lowercased)
- **Bead IDs**: Root beads use `B-{first 8 hex chars}`; child beads append suffixes (`B-abc12def-test`, `B-abc12def-review`)

---

## Common Mistakes to Avoid

- **Running `takt plan` without `--write` or `--from-file`** — looks like it worked but nothing is persisted
- **Moving spec to `planned/` before beads exist** — confusing if beads are later found missing
- **Moving spec to `done/` before merging** — spec says done but code isn't on main
- **Using `git merge` instead of `takt merge`** — bypasses rebase + test gate
- **Manually resolving merge conflicts without user authorisation** — let the scheduler handle merge-conflict beads; manual git operations corrupt state
- **Using `mv` to move spec files** — use `spec.py set status` instead to keep frontmatter and filesystem in sync
- **Creating beads inside an already-merged feature tree** — those beads need their own merge cycle; use standalone beads (no `--parent-id`) for fixes to merged features
- **Invoking `takt` or `python` without `uv run`** — always prefix commands with `uv run`
- **Manually marking a developer bead `done`** — developer beads must go through the scheduler to trigger followup beads

---

## Configuration

Settings live in `.takt/config.yaml`. Key blocks: `common`, `codex`, `claude`.

```bash
# View current config
cat .takt/config.yaml
```

Key settings:
- `common.default_runner` — `claude` or `codex`
- `common.test_command` — run by `takt merge` before merging
- `claude.model_default` — model used for Claude Code workers
- `claude.timeout_seconds` — per-bead agent timeout

---

## Finding the Feature Root for a Spec

```bash
uv run takt bead list --plain | grep -i "<spec keyword>"
```

The feature root bead is the one where `bead_id == feature_root_id`. Use this ID with `--feature-root` flags and `takt merge`.
