---
name: spec-management
description: This skill supports managing spec files, as part of spec-driven development. The skill exposes functions for creating, listing, showing, and updating spec files. This skill must always be used to create and manage specs, and to update metadata for spec files (description, dependencies, priority, complexity, status, tags, and scope). Editing of the content of spec files, except for the frontmatter fields that the skill manages for the metadata, is outside the scope of this skill and should be done by agents directly in the file. Agents should not edit any frontmatter fields directly in the file, but should use the provided functions to ensure the filesystem and frontmatter stay in sync.
tools: Read, Write, Edit, Glob, Grep, Bash
license: MIT
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

This creates `specs/`, `specs/drafts/`, `specs/planned/`, and `specs/done/`. Only needs to be done once per project.

> **Note:** If `specs/` already exists but is missing its lifecycle subdirectories, the `create` subcommand will create them automatically. `spec init` is only required to create the top-level `specs/` directory itself.

"drafts", "planned", and "done" are the default lifecycle stages, but custom status values are supported. The skill enforces that a spec file lives in the folder matching its `status` frontmatter field, so if you use custom status values, the skill script will enforce the correct folder structure automatically.

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

If `specs/` exists but the lifecycle subdirectories (`drafts/`, `planned/`, `done/`) are absent, `create` creates them automatically. Running `spec init` beforehand is not required when `specs/` already exists.

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

Prints the frontmatter and first 20 lines of body for a spec. Use `--full` to print the entire body. Use `--body-only` to print the body without frontmatter — always use this when passing a spec to the `spec-reviewer` agent or any other agent that should not see frontmatter fields.

```bash
python3 <spec-py> show spec-a3f19c2b
python3 <spec-py> show my-feature
python3 <spec-py> show --full spec-a3f19c2b
python3 <spec-py> show --body-only spec-a3f19c2b
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

Custom status values are supported. The skill will create the necessary folders as needed and enforce that the `status` field matches the folder name.

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

### `remove <spec>` — Delete a spec file

Permanently deletes the spec file. Behaviour depends on status:

- **draft** specs are deleted immediately with no confirmation.
- **planned** or **done** specs prompt for confirmation (`[y/N]`) to prevent accidental deletion.

```bash
python3 <spec-py> remove spec-a3f19c2b
python3 <spec-py> remove my-feature
```

Use `--force` to skip the confirmation prompt (useful in automated or non-interactive contexts):

```bash
python3 <spec-py> remove --force spec-a3f19c2b
```

> **Note for agents:** always use `--force` when deleting specs non-interactively, otherwise the confirmation prompt will block execution.

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

---

## Writing a Spec

### Template

A default template is used when creating a new spec with `spec.py create`. The template includes only markdown sections but no frontmatter with metadata, as those will be managed automatically by the skill when initializing the skill.

The default template is `specs/spec-template.md`. You or the user can edit this file to change the default content for new specs depending on the project's needs. The template can include any content but the default version is a good starting point.

### Good spec practices

- Be prescriptive: agents implement exactly what the spec says. Vague specs produce vague implementations.
- Include concrete examples (function signatures, config YAML, CLI output) where helpful
- Reference existing code by file:line when the change is to a specific location
- Keep acceptance criteria verifiable — avoid "the code is clean" or "it works correctly"
- Resolved pending decisions should stay in the spec with their resolution, not be deleted

### What NOT to put in a spec

- Implementation details the agent should decide (e.g. variable names, internal algorithm choice)
- Speculative future features — scope to what's actually being built
- Duplicate content from project-level instructions