---
name: spec-management
description: Canonical workflow for spec lifecycle operations using spec.py — initialization, creation, querying, metadata updates, and status transitions. Also covers writing specs, planning beads, and merging features in codex-agent-orchestration.
tools: Read, Write, Edit, Glob, Grep, Bash
user-invocable: false
---

# spec-management

`spec.py` is the canonical tool for managing spec files. Always use it for spec lifecycle operations; do not use `mv`, manual frontmatter edits, or direct file manipulation.

## Getting Started

Before using any spec commands, complete these two steps.

### Step 1 — Locate spec.py

`spec.py` is installed alongside this `SKILL.md`. Find its path with:

```bash
find . -name "spec.py" -path "*/skills/*"
```

Use the result as `<spec-py>` in all commands below. If `spec.py` is not found under `skills/`, check the project root:

```bash
find . -maxdepth 2 -name "spec.py"
```

### Step 2 — Initialise specs/ if absent

Check whether the project already has a specs directory:

```bash
test -d specs && echo "exists" || echo "missing"
```

If missing, initialise it before doing anything else:

```bash
python3 <spec-py> init
```

This creates `specs/drafts/`, `specs/planned/`, and `specs/done/`. Only needs to be done once per project.

## Invocation

Run from the project root (where `specs/` lives):

```bash
python3 <spec-py> <subcommand> [args]
```

`<spec-py>` is the path to `spec.py` resolved in Step 1 above. `spec.py` has no external dependencies and runs with the Python standard library only.

## Subcommands

### `create <title>` — Create a new spec

Creates a new spec file in `specs/drafts/` with a generated ID and a standard template.

```bash
python3 <spec-py> create "My Feature Title"
```

Output: path to the created file and its generated ID (e.g. `spec-a3f19c2b`).

The filename is derived from the title as a slug (lowercase, hyphens, `.md` extension).

---

### `list` — List all specs

Lists specs across all lifecycle folders with their ID, status, priority, complexity, and name.

```bash
python3 <spec-py> list
python3 <spec-py> list --status draft
python3 <spec-py> list --status planned
python3 <spec-py> list --tag backend
python3 <spec-py> list --priority high
```

Filters can be combined. Legacy specs (no frontmatter) are shown with status `legacy`.

---

### `show <spec>` — Show spec details

Prints the frontmatter and first 20 lines of body for a spec.

```bash
python3 <spec-py> show spec-a3f19c2b
python3 <spec-py> show my-feature
```

See [ID and filename resolution](#id-and-filename-resolution) for how `<spec>` is matched.

---

### `set status <value> <spec>` — Transition lifecycle status

Updates the `status` field in frontmatter **and moves the file** to the matching lifecycle folder. This is the only supported way to change a spec's lifecycle stage.

```bash
python3 <spec-py> set status planned spec-a3f19c2b
python3 <spec-py> set status done    spec-a3f19c2b
python3 <spec-py> set status draft   spec-a3f19c2b
```

Valid values: `draft`, `planned`, `done`.

| Status | Folder |
|--------|--------|
| `draft` | `specs/drafts/` |
| `planned` | `specs/planned/` |
| `done` | `specs/done/` |

Output: new path of the file after the move.

> **Do not use `mv`** to move spec files between folders. `set status` keeps the frontmatter and filesystem location in sync atomically.

---

### `set feature-root <bead-id> <spec>` — Link to a feature root bead

Sets the `feature_root_id` frontmatter field.

```bash
python3 <spec-py> set feature-root B-a7bc3f91 spec-a3f19c2b
```

---

### `set tags <tag1,tag2,...> <spec>` — Replace tags

Replaces the `tags` list in frontmatter. Provide tags as a comma-separated string.

```bash
python3 <spec-py> set tags "backend,auth" spec-a3f19c2b
```

---

### `set priority <value> <spec>` — Set priority

Sets the `priority` field. Valid values: `high`, `medium`, `low`.

```bash
python3 <spec-py> set priority high spec-a3f19c2b
```

---

### `set description <text> <spec>` — Set description

Sets the `description` frontmatter field to a single-line text value.

```bash
python3 <spec-py> set description "Adds OAuth login support" spec-a3f19c2b
```

---

### `migrate <spec>` — Add frontmatter to a legacy spec

Adds a standard frontmatter block to a spec file that has none. Infers the name from the first `# Heading` or the filename, generates a new ID, and infers the status from the file's current folder.

```bash
python3 <spec-py> migrate old-spec-filename
```

Fails if the spec already has frontmatter.

---

## ID and Filename Resolution

Every subcommand that takes a `<spec>` argument resolves it as follows:

1. **Exact ID match** — if the query equals the `id` field (e.g. `spec-a3f19c2b`) in any spec's frontmatter, that spec is returned immediately.
2. **Partial filename match** — if the query is a substring of a spec's filename stem (case-insensitive), the matching files are collected.
   - Exactly one match → that spec is used.
   - Zero matches → `error: no spec matching "<query>"` (exit 1).
   - Multiple matches → `error: "<query>" matches multiple specs: <id-list>` (exit 1). Use a more specific query or the full ID.

Exact ID match always takes priority over filename matching.

## Frontmatter Schema

New specs are created with this template:

```yaml
---
name: <title>
id: spec-<8-hex-chars>
description:
dependencies:
priority:
complexity:
status: draft
tags: []
scope:
  in:
  out:
feature_root_id:
---
```

Edit the body freely; `spec.py` preserves body content when updating frontmatter fields.

---

## Folder Structure and Lifecycle

```
specs/
  drafts/     # Spec written, not yet planned (no beads created)
  planned/    # Planner has run and beads are persisted; implementation in progress
  done/       # All beads merged to main; feature is live
```

**Rule:** A spec lives in exactly one folder. Move it forward only when the transition condition is met.

| Transition | Condition |
|---|---|
| drafts → planned | `takt plan --write <spec>` has been run and beads created |
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
uv run takt plan specs/drafts/my-spec.md

# Persist — creates beads in storage
uv run takt plan --write specs/drafts/my-spec.md
```

**Always use `--write` to persist.** Without it, the planner output is printed but no beads are created.

After persisting, use `spec.py` to transition the spec to `planned`:

```bash
python3 <spec-py> set status planned spec-a3f19c2b
```

Then commit both the beads and the spec status change together.

---

## Checking Spec / Bead Status

```bash
# Overall counts
uv run takt summary

# Scoped to one feature
uv run takt summary --feature-root <bead_id>

# All beads as table
uv run takt bead list --plain

# Find the feature root ID for a spec
uv run takt bead list --plain | grep -i "<spec keyword>"
```

To find which bead corresponds to a spec, search by title keyword. The feature root bead (where `bead_id == feature_root_id`) is the top-level planner bead.

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

## Merging a Feature

Use `takt merge`, never `git merge` directly:

```bash
uv run takt merge <bead_id>
```

This does:
1. Merges `main` into the feature branch (conflict check)
2. If conflict: creates a `merge-conflict` bead, exits with instructions
3. Runs `config.common.test_command` (currently: `uv run python -m unittest discover -s tests`)
4. If tests fail: creates a `merge-conflict` bead, exits with instructions
5. If all clear: `git merge --no-ff` into main

If a merge-conflict bead is created, run the scheduler then retry:
```bash
uv run takt --runner claude run --once --max-workers 4
uv run takt merge <bead_id>  # retry
```

**Flags:**
- `--skip-rebase` — skip the main-into-feature sync step
- `--skip-tests` — skip the test gate

---

## Common Mistakes to Avoid

- **Running `takt plan` without `--write`** — looks like it worked but nothing is persisted
- **Moving spec to `planned/` before beads exist** — confusing if beads are later found missing
- **Moving spec to `done/` before merging** — spec says done but code isn't on main
- **Using `git merge` instead of `takt merge`** — bypasses rebase + test gate
- **Using `mv` to move spec files** — use `spec.py set status` instead to keep frontmatter and filesystem in sync
- **Creating beads inside an already-merged feature tree** — those beads need their own merge cycle; use standalone beads (no `--parent-id`) for fixes to merged features
