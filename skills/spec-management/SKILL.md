---
name: spec-management
description: Canonical workflow for spec lifecycle operations using spec.py — initialization, creation, querying, metadata updates, and status transitions.
---

# spec-management

`spec.py` is the canonical tool for managing spec files. Always use it for spec lifecycle operations; do not use `mv`, manual frontmatter edits, or direct file manipulation.

## Invocation

Run from the project root (where `specs/` lives):

```bash
python3 skills/spec-management/spec.py <subcommand> [args]
# or
uv run python skills/spec-management/spec.py <subcommand> [args]
```

Both forms are equivalent. Use `uv run python` when working inside the orchestration project to ensure the correct environment.

## Subcommands

### `init` — Initialize the specs directory

Creates `specs/drafts/`, `specs/planned/`, and `specs/done/` in the current directory.

```bash
python3 skills/spec-management/spec.py init
```

Fails if `specs/` already exists.

---

### `create <title>` — Create a new spec

Creates a new spec file in `specs/drafts/` with a generated ID and a standard template.

```bash
python3 skills/spec-management/spec.py create "My Feature Title"
```

Output: path to the created file and its generated ID (e.g. `spec-a3f19c2b`).

The filename is derived from the title as a slug (lowercase, hyphens, `.md` extension).

---

### `list` — List all specs

Lists specs across all lifecycle folders with their ID, status, priority, complexity, and name.

```bash
python3 skills/spec-management/spec.py list
python3 skills/spec-management/spec.py list --status draft
python3 skills/spec-management/spec.py list --status planned
python3 skills/spec-management/spec.py list --tag backend
python3 skills/spec-management/spec.py list --priority high
```

Filters can be combined. Legacy specs (no frontmatter) are shown with status `legacy`.

---

### `show <spec>` — Show spec details

Prints the frontmatter and first 20 lines of body for a spec.

```bash
python3 skills/spec-management/spec.py show spec-a3f19c2b
python3 skills/spec-management/spec.py show my-feature
```

See [ID and filename resolution](#id-and-filename-resolution) for how `<spec>` is matched.

---

### `set status <value> <spec>` — Transition lifecycle status

Updates the `status` field in frontmatter **and moves the file** to the matching lifecycle folder. This is the only supported way to change a spec's lifecycle stage.

```bash
python3 skills/spec-management/spec.py set status planned spec-a3f19c2b
python3 skills/spec-management/spec.py set status done    spec-a3f19c2b
python3 skills/spec-management/spec.py set status draft   spec-a3f19c2b
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
python3 skills/spec-management/spec.py set feature-root B-a7bc3f91 spec-a3f19c2b
```

---

### `set tags <tag1,tag2,...> <spec>` — Replace tags

Replaces the `tags` list in frontmatter. Provide tags as a comma-separated string.

```bash
python3 skills/spec-management/spec.py set tags "backend,auth" spec-a3f19c2b
```

---

### `set priority <value> <spec>` — Set priority

Sets the `priority` field. Valid values: `high`, `medium`, `low`.

```bash
python3 skills/spec-management/spec.py set priority high spec-a3f19c2b
```

---

### `set description <text> <spec>` — Set description

Sets the `description` frontmatter field to a single-line text value.

```bash
python3 skills/spec-management/spec.py set description "Adds OAuth login support" spec-a3f19c2b
```

---

### `migrate <spec>` — Add frontmatter to a legacy spec

Adds a standard frontmatter block to a spec file that has none. Infers the name from the first `# Heading` or the filename, generates a new ID, and infers the status from the file's current folder.

```bash
python3 skills/spec-management/spec.py migrate old-spec-filename
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
