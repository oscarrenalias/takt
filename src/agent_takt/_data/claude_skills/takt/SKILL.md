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

Agent types: `planner`, `developer`, `tester`, `documentation`, `review`, `defect`. Only `developer`, `tester`, `documentation`, and `defect` mutate code.

---

## Key CLI Commands

### Status and inspection

```bash
uv run takt summary                          # counts + next actionable beads
uv run takt summary --feature-root <id>     # scoped to one feature
uv run takt bead list --plain               # all beads as table
uv run takt bead list --label <label>       # filter by label (repeatable, AND)
uv run takt bead list --status <status>     # filter by lifecycle status (repeatable, OR)
uv run takt bead list --agent <agent>       # filter by agent type (repeatable, OR)
uv run takt bead list --feature-root <id>   # scope to one feature tree
uv run takt bead show <id>                  # full bead JSON
uv run takt bead show <id> --field <path>   # project a single field (e.g. status, handoff_summary.completed)
uv run takt bead history <id>               # formatted execution history (one line per entry)
uv run takt bead history <id> --limit 5     # last N entries
uv run takt bead history <id> --event failed --event retried   # filter by event type (repeatable)
uv run takt bead graph                      # Mermaid diagram of all beads
uv run takt bead graph --feature-root <id> # scoped to one feature
uv run takt bead graph --output graph.md   # write diagram to file
uv run takt tui                             # interactive terminal UI
```

Use `bead show <id> --field <path>` whenever you only need a specific value (status, handoff verdict, last event, block reason). It avoids the full JSON dump and is the canonical replacement for `cat .takt/beads/<id>.json | python3 -c "import json, sys; d=json.load(sys.stdin); print(d['handoff_summary']['completed'])"` and similar inline-parsing patterns. The `--field` syntax supports nested paths (`handoff_summary.verdict`) and array indexing including negatives (`execution_history[-1].event`).

### Reading bead state

When you need to inspect a bead's state, prefer the dedicated CLI commands over reading `.takt/beads/<id>.json` directly or shelling out to `python3 -c '...'` for JSON parsing:

| Goal | Use |
|------|-----|
| Lifecycle log (chronological) | `uv run takt bead history <id>` |
| One specific field | `uv run takt bead show <id> --field <path>` |
| Full JSON for piping | `uv run takt bead show <id>` |
| Beads matching status / agent | `uv run takt bead list --status ... --agent ... --plain` |
| Beads in one feature tree | `uv run takt bead list --feature-root <id> --plain` |

`--field` paths use Python-style dotted access with bracket-style array indexing including negative indices: `--field handoff_summary.verdict`, `--field execution_history[-1].event`, `--field expected_files[0]`. Missing paths exit non-zero with a clear stderr message; null values exit zero with an empty line (legitimate "field unset"). Lists and dicts render as pretty JSON; scalars render bare without quotes — clean for shell capture.

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

The scheduler auto-creates `-test`, `-docs`, and `-review` child beads when a developer bead completes (unless it is a corrective bead, merge-conflict bead, or defect bead). Defect beads spawn only a single `-review` followup.

**Exception — planner-managed feature trees:** When `takt plan` persists a graph that already contains shared tester, documentation, and review beads covering the whole tree, the scheduler recognises the planner-managed structure and suppresses the per-developer auto-followups. The beads the planner emitted are the final total — they do not seed additional `-test`/`-docs`/`-review` children. Auto-followup creation still runs for standalone developer beads outside a planner-owned tree.

**Recovering from session-limit 429s.** Anthropic session/quota limits (error strings like `session limit`, `usage limit`, and some 429 responses) are **not** in the default `transient_block_patterns`. When one hits mid-run, the affected bead is marked `blocked` and the scheduler spawns a `-corrective` bead that will immediately hit the same limit and burn through `max_corrective_attempts`. Recovery:

```bash
# Identify the spurious correctives and delete them
uv run takt bead list --status blocked --plain
uv run takt bead delete <corrective-id> --force

# After the quota window resets, requeue the originals
uv run takt retry <original-bead-id>
uv run takt --runner claude run --max-workers 4
```

Do **not** add the session-limit string to `transient_block_patterns` — rapid auto-retry won't help a limit that resets hours later; it just burns the retry budget faster. The proper fix (a deferrable-failure class with `not_before` scheduling) is a planned enhancement; manual requeue-after-reset is the clean recovery in the meantime.

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

## Filing a Defect

Use defect beads for post-merge bug fixes. Unlike full specs, defects are filed directly as beads — no planning step required. The defect agent bundles fix + regression test in one bead; the only auto-spawned followup is a `-review` child.

```bash
# File a defect bead
uv run takt bead create \
  --agent defect \
  --type defect \
  --title "Fix off-by-one in pagination" \
  --description "Page 2 returns results starting at index 20 instead of 10."

# Then run the scheduler to dispatch it
uv run takt --runner claude run
```

**CLI validation rules (both flags required together):**
- `--agent defect` requires `--type defect`
- `--type defect` requires `--agent defect`
- Any mismatch exits non-zero with a clear error

The defect agent is permitted to run focused tests for the affected module. It must add a regression test that fails without the fix and passes with it. It must not run the full test suite — that happens at `takt merge` time.

On completion, exactly one `-review` bead is auto-created. No `-test` or `-docs` children are spawned.

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
