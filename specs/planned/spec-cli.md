---
name: Spec CLI
id: spec-b2f91a3c
description: Standalone CLI for creating and managing spec frontmatter and lifecycle transitions.
dependencies:
priority: medium
complexity: small
status: draft
tags: [cli, specs]
scope:
  in: spec create, list, show, set subcommands; frontmatter read/write; file moves between draft/planned/done
  out: orchestrator planner integration, validation of transition rules, spec content editing
feature_root_id:
---

# Spec CLI

## Objective

Provide a lightweight CLI tool for managing spec files and their frontmatter. The primary consumer is a Claude Code agent via a companion skill, but it is also usable directly by the operator. The tool handles spec creation with correctly-structured frontmatter, metadata updates, status transitions (including moving files between `specs/drafts/`, `specs/planned/`, and `specs/done/`), and listing specs across all folders.

---

## Problems to Fix

1. **No standard way to create a spec** — authors start from scratch or copy an existing file, producing inconsistent frontmatter.
2. **Status transitions are manual** — moving a file between `drafts/`, `planned/`, `done/` and updating the `status` field are two separate error-prone steps with no tool to automate them.
3. **No queryable index of specs** — finding specs by status, tag, or priority requires grepping files manually.

---

## Changes

### 1. Script: `skills/spec-management/spec.py`

A standalone Python script co-located with the `spec-management` skill so the skill directory is self-contained and portable to other projects. No dependency on `codex_orchestrator` internals — it only reads/writes YAML frontmatter and moves files.

The script has a `#!/usr/bin/env python3` shebang and is committed as executable (`chmod +x`). Agents invoke it as:

```bash
python3 skills/spec-management/spec.py <subcommand>
```

In projects using `uv`, `uv run python` also works. The skill documents both forms.

**Subcommands:**

#### `spec init`

Initialises the specs folder structure in the current directory. Must be run once before any other subcommand can be used in a project that has no specs yet.

```bash
python3 skills/spec-management/spec.py init
# → creates specs/
# → creates specs/drafts/, specs/planned/, specs/done/
# → prints: "Initialised specs/ in <cwd>"
```

Exits 1 if `specs/` already exists:

```
error: specs/ already exists — remove it manually if you want to reinitialise
```

This is intentionally strict: the command never modifies an existing `specs/` tree. If only some subdirectories are missing (e.g. `specs/done/` was deleted), the command still exits 1 — the operator must resolve the inconsistency manually.

All other subcommands exit 1 with a distinct message if `specs/` is absent:

```
error: specs/ not found — run 'spec init' to initialise, or check you are in the project root
```

This replaces the previous generic "specs directory not found" error and makes the remediation step explicit.

#### `spec create <title>`

Creates a new spec file in `specs/drafts/` with fully-populated blank frontmatter.

```bash
python3 skills/spec-management/spec.py create "Bead Telemetry Analysis"
# → creates specs/drafts/bead-telemetry-analysis.md
# → prints the path and generated ID
```

Frontmatter generated:

```yaml
---
name: Bead Telemetry Analysis
id: spec-<8-char random hex>
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

# Bead Telemetry Analysis

## Objective

## Problems to Fix

## Changes

## Files to Modify

| File | Change |
|---|---|

## Acceptance Criteria

## Pending Decisions
```

Filename is derived from the title: lowercased, spaces replaced with `-`, non-alphanumeric characters removed.

#### `spec list [--status STATUS] [--tag TAG] [--priority PRIORITY]`

Prints a table of all specs found across `specs/drafts/`, `specs/planned/`, and `specs/done/`.

```bash
python3 skills/spec-management/spec.py list
python3 skills/spec-management/spec.py list --status draft
python3 skills/spec-management/spec.py list --tag cli
```

Output:

```
id               status    priority  complexity  name
spec-b2f91a3c    draft     medium    small       Spec CLI
spec-a0f0124     draft     —         medium      Bead Telemetry Analysis
spec-c3d92b11    planned   high      large       Pipeline Efficiency Improvements
```

#### `spec show <id-or-filename>`

Prints the frontmatter of a spec as YAML, followed by the first section body (up to the first `---` separator or 20 lines, whichever comes first).

```bash
python3 skills/spec-management/spec.py show spec-b2f91a3c
python3 skills/spec-management/spec.py show bead-telemetry-analysis   # partial filename match
```

#### `spec set status <draft|planned|done> <id-or-filename>`

Updates the `status` field in frontmatter and moves the file to the corresponding folder. No transition validation.

```bash
python3 skills/spec-management/spec.py set status planned spec-a0f0124
# → moves specs/drafts/bead-telemetry-analysis.md → specs/planned/bead-telemetry-analysis.md
# → updates status: planned in frontmatter
# → prints new path
```

#### `spec set feature-root <bead-id> <id-or-filename>`

Sets the `feature_root_id` field in frontmatter.

```bash
python3 skills/spec-management/spec.py set feature-root B-0a1b2c3d spec-a0f0124
```

#### `spec set tags <tag1,tag2,...> <id-or-filename>`

Replaces the `tags` list in frontmatter.

```bash
python3 skills/spec-management/spec.py set tags "cli,observability" spec-a0f0124
```

#### `spec set priority <high|medium|low> <id-or-filename>`

Sets the `priority` field.

```bash
python3 skills/spec-management/spec.py set priority high spec-a0f0124
```

#### `spec set description <text> <id-or-filename>`

Sets the `description` field.

```bash
python3 skills/spec-management/spec.py set description "CLI subcommand for bead telemetry" spec-a0f0124
```

**ID/filename resolution:**

All subcommands accept either the full `id` field value (e.g. `spec-a0f0124`) or a partial filename (e.g. `bead-telemetry`). Search is case-insensitive across all three spec folders. If multiple files match, the command prints the matches and exits with an error.

**Error handling:**

All errors print a short message to stderr and exit non-zero. The script must handle:

| Condition | Exit code | Message |
|---|---|---|
| Spec not found | 1 | `error: no spec matching "<query>"` |
| Ambiguous match | 1 | `error: "<query>" matches multiple specs: <list of ids>` |
| `specs/` does not exist (any subcommand except `init`) | 1 | `error: specs/ not found — run 'spec init' to initialise, or check you are in the project root` |
| `spec init` and `specs/` already exists | 1 | `error: specs/ already exists — remove it manually if you want to reinitialise` |
| `spec create` and target file already exists | 1 | `error: spec file already exists: <path>` |
| Malformed frontmatter in an existing spec (unparseable YAML) | 1 | `error: could not parse frontmatter in <path>: <reason>` |
| `spec set status` and file move fails (e.g. permission error) | 1 | `error: could not move <src> to <dst>: <reason>` |
| `spec set *` called on a spec with no frontmatter | 1 | `error: <file> has no frontmatter — run 'spec migrate <id>' first` |

No tracebacks should reach the user — all exceptions are caught at the top level and reported as clean error messages.

**Legacy specs (no frontmatter):**

Specs without a YAML frontmatter block are valid and must not be rejected. Behaviour by subcommand:

- `spec list` — includes the spec; displays `—` for all metadata fields; infers `name` from the first `# Heading` in the file, falling back to the filename
- `spec show` — prints the raw file content with a leading warning line: `warning: no frontmatter found in <file>`
- `spec set *` — exits 1 with the error above, pointing to `spec migrate`
- `spec migrate` — see subcommand below

#### `spec migrate <id-or-filename>`

Adds frontmatter to a spec that has none. Safe to run on any spec file; exits 1 if frontmatter already exists.

```bash
python3 skills/spec-management/spec.py init specs/drafts/old-spec.md
python3 skills/spec-management/spec.py init old-spec   # partial filename match
```

Behaviour:
- Infers `name` from the first `# Heading` in the file; falls back to the filename (title-cased, hyphens replaced with spaces)
- Generates a new `spec-XXXXXXXX` id
- Sets `status` based on the folder the file currently lives in (`drafts` → `draft`, `planned` → `planned`, `done` → `done`)
- Leaves all other fields (`description`, `priority`, `complexity`, `tags`, `dependencies`, `scope`, `feature_root_id`) blank for the operator to fill in
- Prepends the frontmatter block to the file without modifying any existing content
- Prints the generated id and path on success

### 2. Skill: `skills/spec-management/spec-cli.md`

A reference section added to (or included from) the existing spec-management skill, documenting:

- All subcommands with examples
- ID/filename resolution rules
- When to use `spec set status` vs manually moving files (answer: always use the CLI)

### 3. Tests: `skills/spec-management/tests/test_spec.py`

Co-located with the script so the skill directory is fully self-contained and portable. Run with:

```bash
python3 -m unittest discover -s skills/spec-management/tests/
```

Unit tests covering:

- `spec init` creates `specs/drafts/`, `specs/planned/`, `specs/done/` in an empty directory
- `spec init` exits 1 if `specs/` already exists
- All subcommands except `setup` exit 1 with the correct message when `specs/` is absent
- `spec create` generates correct frontmatter with a valid `id` and moves to `specs/drafts/`
- `spec list` finds specs across all three folders and respects `--status` / `--tag` filters
- `spec show` resolves by ID and by partial filename
- `spec set status planned` moves file and updates frontmatter
- `spec set status done` moves file and updates frontmatter
- `spec set feature-root` updates `feature_root_id` field
- `spec set tags` replaces tags list
- Ambiguous partial filename match exits 1 and lists candidates
- No-match lookup exits 1 with clear message
- `spec create` on an existing filename exits 1
- Malformed frontmatter exits 1 with parse error
- `spec set *` on a spec with no frontmatter exits 1 pointing to `spec migrate`
- `spec list` includes legacy specs with `—` for missing fields; name inferred from heading
- `spec show` on a legacy spec prints raw content with a warning line
- `spec migrate` adds frontmatter, infers name and status, generates id
- `spec migrate` on a spec that already has frontmatter exits 1
- No tracebacks in any error path — all exceptions caught and reported cleanly

---

## Files to Modify

| File | Change |
|---|---|
| `skills/spec-management/spec.py` | New file — spec CLI (executable, `#!/usr/bin/env python3` shebang) |
| `skills/spec-management/SKILL.md` | Rewrite lifecycle sections to use `spec.py` as the canonical tool; replace manual `mv` instructions with `spec set status`; add spec creation, listing, and metadata update instructions; document both `python3` and `uv run python` invocation forms |
| `skills/spec-management/tests/test_spec.py` | New file — unit tests, co-located with the script |

---

## Acceptance Criteria

- `python3 skills/spec-management/spec.py create "My Feature"` creates a valid spec file in `specs/drafts/` with all frontmatter fields present and a unique `spec-XXXXXXXX` id
- `python3 skills/spec-management/spec.py list` shows all specs across all three folders
- `python3 skills/spec-management/spec.py list --status draft` shows only drafts
- `python3 skills/spec-management/spec.py set status planned <id>` moves the file and updates frontmatter atomically
- `python3 skills/spec-management/spec.py show <partial-name>` resolves and prints frontmatter
- All `spec set` subcommands update only the targeted frontmatter field, leaving all other content unchanged
- Ambiguous or missing lookups exit non-zero with a clear error message
- All tests in `skills/spec-management/tests/test_spec.py` pass
- `python3 skills/spec-management/spec.py --help` lists all subcommands
- `spec init` in an empty directory creates all three subdirectories
- `spec init` in a directory where `specs/` exists exits 1 without modifying anything
- Any subcommand except `init` in a directory without `specs/` exits 1 pointing to `spec init`
- `spec migrate` on a legacy spec without frontmatter adds correct frontmatter without modifying the existing content
- `spec list` and `spec show` work on legacy specs without frontmatter
- `spec set *` on a legacy spec exits 1 with a message pointing to `spec migrate`
- `SKILL.md` no longer references manual `mv` for status transitions — all lifecycle operations go through `spec.py`

---

## Pending Decisions

### 1. Skill test integration with the pipeline tester bead

~~Should the skill tests in `skills/spec-management/tests/` be run by the pipeline tester bead?~~

**Resolved: No (option 3 — separate test execution).** The skill is a standalone, portable tool. Coupling its tests to the orchestrator pipeline undermines the portability goal. The tester bead will run the full orchestrator suite (`uv run python -m unittest discover -s tests`) as a regression check — this will pass since no `src/` files are changed — but it will not exercise `spec.py`. Skill tests must be run manually:

```bash
python3 -m unittest discover -s skills/spec-management/tests/
```

This limitation resolves naturally if/when the `pipeline-efficiency-improvements` spec is implemented, as pytest can discover across multiple directories: `pytest tests/ .claude/skills/`.

The tester bead for this feature should explicitly note in its acceptance criteria that the orchestrator suite passing is a regression check only, and direct the operator to run the skill tests manually before merging.
