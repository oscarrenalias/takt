---
name: Core document handling for specs and ADRs
id: spec-b1b9c11d
description: One document core in takt for specs and ADRs; takt spec replaces spec.py; skills become docs-only wrappers
dependencies: null
priority: high
complexity: null
status: done
tags:
- documents
- specs
- adr
- cli
- skills
scope:
  in: null
  out: null
feature_root_id: B-36a6c19e
---
# Core document handling for specs and ADRs

## Objective

Specs and ADRs are both lifecycle-managed markdown documents with YAML frontmatter, a hash-derived ID, status folders and validation rules, but they are implemented twice: specs by a 1,560-line standalone script (`spec.py`, shipped as a skill in five identical copies and pulled into this repo from the external APM package `oscarrenalias/skill-spec-management`) and ADRs by `src/agent_takt/adr.py` + `takt adr`. The two implementations have different frontmatter parsers, ID resolution, error handling and commit behaviour, and takt re-parses spec files a third time for the plan-time scenario coverage check (`spec_requirements.py`). This spec introduces a single **document core** inside takt: one generic store parameterised by a per-type configuration, with specs and ADRs as two configurations of it. ADRs move onto the core with no user-visible change. Spec management moves into takt as `takt spec`, replacing `spec.py`. takt stops depending on the external APM package and owns its spec skill and `spec-reviewer` agent; the skills become documentation of `takt spec` / `takt adr` with no scripts. This is the foundation for a later spec that adds a search index and agent context on top of the same core; that work is out of scope here.

## Problems to Fix

1. **Two implementations of the same concept.** `spec.py` and `AdrStore` (`src/agent_takt/adr.py`) each implement frontmatter parse/write, ID allocation, resolution, status-to-folder moves, listing with filters and validation. Fixes land in one and not the other (B-16889ef8 fixed unquoted ADR descriptions; `spec.py` has its own stdlib YAML-subset parser `_fm_load`/`_fm_dump`).
2. **Five copies of `spec.py`**, identical (same md5): `.agents/skills/skill-spec-management/`, `.claude/skills/skill-spec-management/`, `.agents/skills/task/spec-management/`, `src/agent_takt/_data/claude_skills/skill-spec-management/`, `src/agent_takt/_data/agents_skills/task/spec-management/`.
3. **External ownership.** `apm.yml` depends on `oscarrenalias/skill-spec-management#v0.1.12`; `apm.lock.yaml` records it as deploying `.agents/skills/skill-spec-management`, `.claude/skills/skill-spec-management`, `.claude/agents/spec-reviewer.md`, `.codex/agents/spec-reviewer.toml`, `.github/agents/spec-reviewer.agent.md` (and `.github/skills/skill-spec-management`, not currently present). Any local change is overwritten by the next `apm install`.
4. **Spec parsing is duplicated a third time.** `src/agent_takt/spec_requirements.py` (`parse_requirement_scenarios`) re-parses `## Requirements` for `_check_scenario_coverage` in `cli/commands/misc.py`, separately from `spec.py`'s `_extract_requirements_section` / `_validate_requirements_section`, with slightly different rules.
5. **takt cannot resolve a spec by ID**, which blocks the follow-up agent-context work.
6. **Inconsistent UX.** `takt adr` has prefix resolution, `--field`, `--json`/`--plain` and per-transition commits; `spec.py` has none and a different invocation (`python3 <path>/spec.py`).

## Changes

### 1. Document core: `src/agent_takt/documents/`

- `types.py` — `DocumentType` frozen dataclass:
  - `name` (`"spec"`, `"adr"`), `id_prefix` (`"spec-"`, `"ADR-"`), `root_dir` (`specs`, `adr`), filename pattern (spec `<slug>.md`, ADR `adr-<8hex>-<slug>.md`, as today).
  - `statuses`: status → folder (spec: `draft→drafts`, `planned→planned`, `done→done`; ADR: `draft→drafts`, `approved→approved`, `superseded→superseded`, `rejected→rejected`).
  - `allow_custom_statuses` (spec `true`: any `^[a-zA-Z0-9-]+$` status maps to a same-named folder, e.g. `cancelled`; ADR `false`), `reserved_statuses` (spec `{"current"}`; `specs/current/` is never listed as documents).
  - `transitions`: `None` = unrestricted (spec, as today); ADR: `draft→approved`, `draft→rejected`, `approved→superseded`.
  - `resolution`: `"id_prefix"` (ADR, as today) or `"id_prefix_or_filename"` (spec). See §3.
  - `frontmatter_style` (see `frontmatter.py`), `default_frontmatter` (ordered field defaults on create), `template`.
  - `auto_commit`: spec `false`, ADR `true` (preserves current behaviour).
  - Hooks: `on_create`, `before_transition`, `after_transition`, `validate_document` — spec hooks implement the requirements-delta validation and the `specs/current/` merge; ADR hooks implement `accepted_at`/`superseded_at`, supersession links and the required-body-section check.
  - `on_document_changed(doc_type, doc_id, path)`: post-write callback, default no-op, invoked **once per affected document after the write completes** (create, field update, status change, removal; for multi-document transitions, once per document after the whole transaction commits). Writes to `specs/current/` are not documents of a registered type and do not invoke it. (The follow-up indexing spec plugs in here.)
- `frontmatter.py` — the only frontmatter reader/writer. Reading uses `yaml.safe_load`; malformed YAML raises `FrontmatterError(path, message)`. Writing is driven by a per-type `FrontmatterStyle` so existing files keep their exact layout:
  - **ADR style** = current `adr.py` behaviour, unchanged: on any write the whole frontmatter is re-dumped with `yaml.dump(..., default_flow_style=False, sort_keys=False)` in `Adr.to_dict()` key order, file layout `---\n{frontmatter}---\n\n{body}` (blank line after the closing `---`). ADR files therefore keep exactly today's output, including today's re-rendering of template-written lines on `approve`/`reject`/`supersede`.
  - **Spec style** = line-preserving: the writer edits the changed key's line(s) in place and does **not** re-serialise the whole frontmatter; new keys are appended. Values it writes follow current `spec.py` output: `None` → `null`, block-style lists, keys in existing order (new keys appended), quoting only where YAML requires it, body immediately after the closing `---` line (no added blank line), no line wrapping of long scalars.
  - The body is never modified. A document is rewritten only when a field changes. For specs, **only the changed field's line(s) may differ** from the original file; for ADRs, the output must equal what `adr.py` produces today for the same operation.
- `store.py` — `DocumentStore(root, doc_type)`: `load_all()`, `list(status=, tags=, priority=)`, `resolve(query)`, `get(id) -> Document(id, type, status, path, frontmatter, body)`, `create(title, **fields)`, `set_field(id, field, value)`, `set_status(id, status)`, `remove(id)`, `validate(id=None) -> list[ValidationError(path, message, severity)]`, and `apply(changes)` — an all-or-nothing multi-document transition (each change = id, new status, field updates): validate everything first, write all new files, then remove originals, rolling back on any error. A malformed file yields a validation error, never a traceback, and does not stop validation of other files.
- `registry.py` — `DOCUMENT_TYPES` and `get_store(root, name)`.
- `spec_type.py`, `adr_type.py` — the two configurations and hooks. The `## Requirements` parser, delta validation and `specs/current/` merge are ported from `spec.py` into `spec_type.py`; `spec_requirements.py` keeps its public functions (`parse_requirement_scenarios`, `scenario_prefix`, `criterion_matches_prefix`, `reserved_delimiter_names`) but delegates parsing to it, so there is exactly one `## Requirements` parser.

**Unified `## Requirements` parsing rules** (where the two current parsers differ): only the first `## Requirements` heading starts the section and a repeated one is ignored as a heading; the section ends at the next `## ` heading outside a fenced code block; headings inside fenced code blocks are ignored; heading names are `rstrip()`ped; a `#### Scenario:` with an empty name is skipped by the coverage check and is a validation error for `takt spec validate` (**new behaviour**: `spec.py` accepts it today). All existing `tests/test_spec_requirements*.py` fixtures and the ported `test_spec_py.py` validation fixtures must pass against the unified parser.

### 2. ADRs on the core

- `AdrStore` becomes a thin wrapper over `DocumentStore(root, ADR_TYPE)` (the `Adr` dataclass may remain as a typed view). `approve --supersedes` and `supersede --by` use `DocumentStore.apply`.
- `takt adr new|list|show|approve|reject|supersede|validate`: **identical CLI surface, flags, output, error messages (e.g. `No ADR matches prefix: …`, `Ambiguous ADR prefix …: matches …`), exit codes, on-disk format and per-transition git commits**. ADRs keep prefix-only resolution (no filename matching). Existing ADR tests pass, changing only imports of moved internals.

### 3. `takt spec` command

New `src/agent_takt/cli/commands/spec.py`. It is dispatched in `cli/__init__.py:main()` **before `make_services()`**, alongside `init`/`upgrade`/`asset`, so it never creates `.takt/`. The root is `--root` (either placement) or the current directory. It works in any directory with a `specs/` folder, including feature worktrees.

| Command | Behaviour |
|---|---|
| `takt spec init` | Create `specs/`, `specs/drafts|planned|done/` and `specs/spec-template.md`; exit 1 with `specs/ already exists` if `specs/` exists (as `spec.py init`). |
| `takt spec create "<title>"` | Create `specs/drafts/<slug>.md` with a new `spec-<8hex>` ID, frontmatter defaults (`name`, `id`, `description`, `dependencies`, `priority`, `complexity`, `status: draft`, `tags: []`, `scope: {in, out}`, `feature_root_id`, in that order, empty values as `null`) and the body from `specs/spec-template.md` (with `{name}` substituted) or the built-in template; creates missing lifecycle folders; exit 1 if the file exists. Prints path and ID. |
| `takt spec list [--status S] [--tag T]... [--priority P] [--plain] [--json]` | Specs across all status folders except `current`. `--tag` is repeatable with AND semantics (as `takt adr list`). Specs without frontmatter, or with malformed frontmatter (warning on stderr), are listed with status `legacy`. |
| `takt spec show <spec> [--full] [--body-only] [--field PATH] [--json]` | As `spec.py show`: frontmatter plus the first 20 body lines (stopping at the first `---` line) unless `--full`; `--body-only` prints the body without frontmatter; a legacy spec prints a warning and the whole file. `--field` projects a frontmatter field (dotted path) as `takt adr show --field` does. |
| `takt spec set status <value> <spec>` | Validate the status (custom allowed, `current` rejected), move the file and update `status`. On transition to `done` from a non-`done` status, validate and merge `## Requirements` deltas into `specs/current/<capability>.md` first (exit 1, no file changes on invalid deltas); delete a capability file left with no requirements after a REMOVED delta; skip the merge if the spec is already `done`. Prints the new path and one `merged: <OP> <capability> / <requirement>` line per applied delta. |
| `takt spec set feature-root|tags|priority|description <value> <spec>` | As today; `priority` restricted to `high|medium|low`; `tags` comma-separated. On a legacy spec: exit 1 with `run 'takt spec migrate' first`. |
| `takt spec migrate <spec>` | Add frontmatter to a legacy spec, inferring name from the first `# ` heading or filename and status from the parent folder; exit 1 if it already has frontmatter. |
| `takt spec remove <spec> [--force]` | `draft` specs are deleted immediately; any other status (including custom ones) asks for confirmation unless `--force`. When stdin is not a TTY and `--force` is absent, exit 1 with `refusing to delete a <status> spec without --force`. |
| `takt spec validate [<spec> \| <path>]` | One spec, or every spec when no argument is given. Checks (errors unless noted): frontmatter parses; `id` present and unique across specs (an `id` not matching `^spec-[0-9a-f]{8}$` is a **warning**, e.g. the existing `s-a0f0124`); `name` present (missing `name` is a **warning**; the display name is inferred from the first `# ` heading, then the filename, as `spec.py` does); `status` equals the folder's status (`drafts`→`draft`, otherwise the folder name); legacy specs are a warning. `## Requirements`: syntax checks always; consistency checks against `specs/current/` ("already exists" / "not found") **only for specs not in `done`**, since a done spec's deltas have already been merged. Exit 1 on any error; `OK: <path> (<n> requirement deltas)` per valid spec as today. |

Spec resolution (`<spec>`): exact ID (case-insensitive) → unique ID prefix, with or without `spec-` → unique filename-stem substring. An ID prefix matching several specs is an error (no fall-through to filename matching). Errors go to stderr: `error: no spec matching "<query>"` or `error: "<query>" matches multiple specs: <id>, <id>, ...` listing every match; exit 1.

`--json` shapes: `list` → array of `{id, name, status, priority, tags, path}`; `show` → `{id, status, path, frontmatter: {...}, body}`.

Spec commands do not create git commits (`auto_commit: false`).

### 4. Skills and agents become takt-owned documentation

- Remove the APM dependency on `oscarrenalias/skill-spec-management`. It is the only APM dependency, so delete `apm.yml` and `apm.lock.yaml`. The external package itself is untouched and out of scope.
- Delete all five `spec.py` copies. Delete `tests/test_spec_py.py` after porting every case to `tests/test_cli_spec.py` / `tests/test_documents_*.py`.
- **Claude Code operator skills** (`src/agent_takt/_data/claude_skills/` + repo `.claude/skills/`): rewrite `skill-spec-management/SKILL.md` to document `uv run takt spec ...` only (same lifecycle guidance, no "locate spec.py" step; keep the skill name); add `skill-adr-management/SKILL.md` documenting `takt adr` (lifecycle, when to write an ADR vs a spec, body section conventions summarised from `docs/adr.md`).
- **Codex operator skills** (`src/agent_takt/_data/agents_skills/` + repo `.agents/skills/`): rewrite `task/spec-management/SKILL.md` the same way, delete its `spec.py`, keep `agents/openai.yaml` with an updated description; add `task/adr-management/SKILL.md` (+ `agents/openai.yaml`). Remove the repo copy `.agents/skills/skill-spec-management/` (APM-deployed; the Codex layout is `task/spec-management`).
- **spec-reviewer agent** is takt-owned: `src/agent_takt/_data/claude_agents/spec-reviewer.md` and the repo copies `.claude/agents/spec-reviewer.md`, `.codex/agents/spec-reviewer.toml`, `.github/agents/spec-reviewer.agent.md` reference `takt spec validate` instead of `spec.py validate`.
- Update `src/agent_takt/_data/claude_skills/takt/SKILL.md`, `.claude/skills/takt/SKILL.md`, `.agents/skills/takt/SKILL.md`, and `templates/skills/task/spec-intake/SKILL.md` (+ packaged copy) to reference `takt spec` and the ADR skill.
- `src/agent_takt/onboarding/assets.py`: install the new ADR skills for both backends via the existing install paths.
- `takt upgrade` in existing projects: removed bundled files (`spec.py`) are renamed to `.disabled` by the existing logic and new skill files are added; cover with a test. No new upgrade mechanism.

### 4a. Existing projects

Existing takt projects migrate with `takt upgrade`; there is no data migration because file formats do not change.

- Surveyed on 2026-11-08: all 10 registered projects other than takt have unmodified, manifest-tracked copies of `spec.py` and the spec `SKILL.md` files, so `takt upgrade` renames `spec.py` to `.disabled`, rewrites the skills and adds the ADR skills with no manual merge.
- Until `takt upgrade` runs, the old `spec.py` keeps working (it is standalone), so installing the new takt breaks nothing.
- Six projects have hand-written `CLAUDE.md` instructions that call `spec.py`; takt does not own those files. `takt upgrade` (and `--dry-run`) prints a non-blocking warning listing every `CLAUDE.md`/`AGENTS.md` line that mentions `spec.py`; it never edits them.
- Some projects have specs whose frontmatter lacks `name` or uses non-standard IDs (`spec-05-classification-engine`, `spec-09`, `spec-podcast-01`); these must list, resolve by exact ID and validate with warnings only.
- `docs/documents.md` gets a "Migrating an existing project" section describing these steps.

### 5. Documentation

- New `docs/documents.md`: the document core, both types (statuses, transitions, folders, resolution, validation, commit behaviour, frontmatter style), and how to add a type.
- Update `CLAUDE.md`, `AGENTS.md`, `README.md` (replace the external skill links), `docs/adr.md`, `docs/onboarding.md`, `specs/HOWTO.md` to use `takt spec` and remove `spec.py` references. Approved ADRs (`adr-1e74fc39`, `adr-b6447b2e`, `adr-a188353c`) are historical records and are **not** edited.

### 6. ADR (operator task, not a bead)

After merge the operator approves a new ADR that **restates ADR-a188353c's full decision** (specs describe changes, `specs/current/` records current behaviour via `## Requirements` deltas, ADRs record decisions) with spec management provided by `takt spec` on the shared document core, and supersedes ADR-a188353c. The specs/current and delta rules remain binding.

## Requirements

### ADDED: document-management / Single document core
Specs and ADRs SHALL be loaded, resolved, created, updated, moved between status folders and validated by one `DocumentStore` implementation parameterised by a `DocumentType`, and the codebase MUST contain exactly one `## Requirements` parser, used by both `takt spec validate` and the `takt plan` scenario coverage check.

#### Scenario: Both types share the store
- **WHEN** `get_store(root, "spec")` and `get_store(root, "adr")` are called
- **THEN** both return `DocumentStore` instances whose `doc_type.name` is `spec` and `adr` respectively

#### Scenario: Fenced scenarios are ignored consistently
- **WHEN** a spec's `## Requirements` section contains a `#### Scenario:` line inside a fenced code block
- **THEN** neither `takt spec validate` nor the `takt plan --write` coverage check treats it as a scenario

### ADDED: document-management / Spec resolution by ID, prefix or filename
`takt spec` SHALL resolve a spec from its full ID, a unique ID prefix with or without `spec-`, or a unique filename-stem substring, and MUST exit 1 with an error on stderr listing every match when the query is ambiguous.

#### Scenario: Resolve a spec by short prefix
- **WHEN** `takt spec show 1cdb` is run and exactly one spec ID starts with `spec-1cdb`
- **THEN** that spec is shown

#### Scenario: Ambiguous query
- **WHEN** `takt spec show refactor` matches several spec filenames
- **THEN** the command exits 1 and stderr contains `matches multiple specs:` followed by every matching ID

### ADDED: document-management / Status changes keep folder and frontmatter in sync
Changing a document's status SHALL update the `status` field and move the file to the folder mapped to that status in one operation, and MUST reject statuses and transitions the document type does not allow.

#### Scenario: Spec moves to planned
- **WHEN** `takt spec set status planned <spec>` is run on a draft spec
- **THEN** the file is in `specs/planned/` with `status: planned`, its body is byte-identical and no other line changed

#### Scenario: Custom spec status
- **WHEN** `takt spec set status cancelled <spec>` is run
- **THEN** the file is in `specs/cancelled/` with `status: cancelled`

#### Scenario: Reserved status rejected
- **WHEN** `takt spec set status current <spec>` is run
- **THEN** the command exits 1 and the file is unchanged

#### Scenario: Invalid ADR transition
- **WHEN** `takt adr approve` is run on an ADR whose status is `rejected`
- **THEN** the command exits 1 and the file is unchanged

### ADDED: document-management / Requirements merge on spec completion
`takt spec set status done` SHALL validate the spec's `## Requirements` deltas and merge them into `specs/current/<capability>.md` before moving the spec, and MUST leave all files unchanged and exit 1 when any delta is invalid.

#### Scenario: Valid deltas merged
- **WHEN** `takt spec set status done <spec>` is run on a planned spec with a valid ADDED requirement for capability `widgets`
- **THEN** `specs/current/widgets.md` contains the requirement with a `<!-- source: <spec-id> -->` line, the spec is in `specs/done/`, and stdout contains `merged: ADDED widgets / <requirement>`

#### Scenario: Invalid delta blocks completion
- **WHEN** `takt spec set status done <spec>` is run on a spec whose ADDED requirement has no `SHALL`/`MUST` sentence
- **THEN** the command exits 1, the spec stays in its folder and `specs/current/` is unchanged

#### Scenario: Done spec validates after merge
- **WHEN** `takt spec validate` is run on a done spec whose ADDED requirement already exists in `specs/current/`
- **THEN** it reports the spec OK and exits 0

### ADDED: document-management / Frontmatter round-trip
Writing a document SHALL preserve every unchanged line of the file and quote values so that any string round-trips unchanged, and validation MUST report malformed frontmatter as an error naming the file without a traceback.

#### Scenario: Description with a colon
- **WHEN** `takt spec set description "Gates: preflight, tests" <spec>` is run
- **THEN** `takt spec show <spec> --field description` prints `Gates: preflight, tests` and only the `description` line of the file changed

#### Scenario: Malformed spec frontmatter
- **WHEN** `takt spec validate` is run and one spec file has invalid YAML frontmatter
- **THEN** stderr names that file, the other specs are still validated, and the exit code is 1

### ADDED: document-management / ADR behaviour preserved
`takt adr` subcommands SHALL keep their existing flags, output, error messages, exit codes, on-disk format and per-transition git commits after moving onto the document core.

#### Scenario: ADR lifecycle unchanged
- **WHEN** `takt adr new "X"` is run, the body sections are filled in and `takt adr approve <id>` is run
- **THEN** the ADR is in `adr/approved/` with `accepted_at` set, the approval created exactly one git commit, and the file has a blank line after the closing `---`

#### Scenario: Approve with supersedes is atomic
- **WHEN** `takt adr approve <new> --supersedes <old>` is run and writing the superseded ADR fails
- **THEN** both ADR files are left exactly as they were before the command

### ADDED: document-management / Spec management needs no standalone script
Spec lifecycle management SHALL be provided by `takt spec`, and takt MUST NOT ship or depend on a `spec.py` script.

#### Scenario: Package contains no spec.py
- **WHEN** the built wheel's file list is inspected
- **THEN** no file named `spec.py` is present under `agent_takt/_data/`

#### Scenario: Upgrade disables the old script
- **WHEN** `takt upgrade` runs in a project whose assets manifest lists an unmodified `.claude/skills/skill-spec-management/spec.py`
- **THEN** that file is renamed to `spec.py.disabled` and `.claude/skills/skill-spec-management/SKILL.md` is updated to the `takt spec` version

#### Scenario: Spec commands do not create takt state
- **WHEN** `takt spec list` is run in a git repository that has `specs/` but no `.takt/` directory
- **THEN** the specs are listed and no `.takt/` directory is created

## Files to Modify

| File | Change |
|---|---|
| `src/agent_takt/documents/__init__.py`, `types.py`, `frontmatter.py`, `store.py`, `registry.py`, `spec_type.py`, `adr_type.py` | NEW — document core and type configurations |
| `src/agent_takt/adr.py` | Thin wrapper over the core |
| `src/agent_takt/cli/commands/adr.py` | Call the core; behaviour unchanged |
| `src/agent_takt/cli/commands/spec.py` | NEW — `takt spec` |
| `src/agent_takt/cli/parser.py`, `src/agent_takt/cli/__init__.py`, `src/agent_takt/cli/commands/__init__.py` | Register `spec`; dispatch before `make_services()` |
| `src/agent_takt/spec_requirements.py` | Delegate parsing to the core parser; public functions kept |
| `src/agent_takt/cli/commands/misc.py` | Only if needed for the coverage check to use the core parser |
| `apm.yml`, `apm.lock.yaml` | Delete (the only dependency was `skill-spec-management`) |
| `.agents/skills/skill-spec-management/` | Delete (APM-deployed duplicate) |
| `.claude/skills/skill-spec-management/`, `src/agent_takt/_data/claude_skills/skill-spec-management/` | Delete `spec.py`; rewrite `SKILL.md` for `takt spec` |
| `.agents/skills/task/spec-management/`, `src/agent_takt/_data/agents_skills/task/spec-management/` | Delete `spec.py`; rewrite `SKILL.md`; update `agents/openai.yaml` |
| `.claude/skills/skill-adr-management/`, `src/agent_takt/_data/claude_skills/skill-adr-management/` | NEW — ADR skill (Claude Code) |
| `.agents/skills/task/adr-management/`, `src/agent_takt/_data/agents_skills/task/adr-management/` | NEW — ADR skill (Codex) with `agents/openai.yaml` |
| `src/agent_takt/_data/claude_agents/spec-reviewer.md`, `.claude/agents/spec-reviewer.md`, `.codex/agents/spec-reviewer.toml`, `.github/agents/spec-reviewer.agent.md` | `spec.py validate` → `takt spec validate` |
| `src/agent_takt/_data/claude_skills/takt/SKILL.md`, `.claude/skills/takt/SKILL.md`, `.agents/skills/takt/SKILL.md` | Reference `takt spec` and the ADR skill |
| `templates/skills/task/spec-intake/SKILL.md`, `src/agent_takt/_data/templates/skills/task/spec-intake/SKILL.md` | `spec.py validate` → `takt spec validate` |
| `src/agent_takt/onboarding/assets.py` | Install the ADR skills; drop spec.py special-casing if any |
| `pyproject.toml` | Package data for new skills; no `spec.py` |
| `tests/test_spec_py.py` | Delete after porting all cases |
| `tests/test_cli_spec.py`, `tests/test_documents_store.py`, `tests/test_documents_frontmatter.py` | NEW |
| `tests/test_onboarding_assets.py` | Replace the spec.py install/subprocess tests (≈ lines 282-406) with assertions that the skills document `takt spec`, ADR skills are installed, and no `spec.py` is installed |
| `tests/test_cli_adr.py`, `tests/test_adr*.py`, `tests/test_spec_requirements*.py`, upgrade tests | Adjust imports only; add upgrade `.disabled` test |
| `docs/documents.md` | NEW |
| `CLAUDE.md`, `AGENTS.md`, `README.md`, `docs/adr.md`, `docs/onboarding.md`, `specs/HOWTO.md` | Use `takt spec`; remove `spec.py` and external-skill references |

## Acceptance Criteria

- `src/agent_takt/documents/` provides `DocumentStore`, `DocumentType`, one frontmatter module with per-type styles, `apply()` and `on_document_changed`; `grep -rn "_fm_load\|_fm_dump" src/` returns nothing.
- There is one `## Requirements` parser; `spec_requirements.py` delegates to it and existing coverage-check tests pass.
- All pre-existing ADR tests pass (import changes only); `takt adr validate` reports all 18 ADRs in this repo valid; `takt adr show <word>` that is not an ID prefix still fails with `No ADR matches prefix: <word>`.
- `takt spec list` shows every spec in this repo with the same IDs and statuses `spec.py list` reported before the change; `takt spec validate` with no argument exits 0 on this repo (with a warning for `s-a0f0124`).
- Every `spec.py` behaviour listed in the §3 table has a `takt spec` equivalent, and every test case in `tests/test_spec_py.py` has a ported equivalent before that file is deleted.
- Golden-file tests: `takt spec set` on one field of an existing spec changes only that field's line(s); `takt adr approve`/`reject`/`supersede` on fixture ADRs produce byte-identical output to the pre-change `adr.py` implementation.
- `takt spec remove` on a non-draft spec without `--force` and without a TTY exits 1 without deleting.
- Malformed frontmatter in any spec or ADR is reported per file by `validate` with exit 1 and no traceback.
- `on_document_changed` is invoked exactly once per affected document for create, field update, status change, removal and `apply()` (recording-callback test), and not for `specs/current/` writes.
- `takt spec` commands never create `.takt/`.
- No `spec.py` exists in the repository or built package; `apm.yml` and `apm.lock.yaml` are deleted; the spec and ADR skills exist for both Claude Code and Codex layouts and are installed by `takt init`/`takt upgrade`; `takt upgrade` renames an unmodified installed `spec.py` to `.disabled` (test).
- `docs/documents.md` exists; `CLAUDE.md`, `AGENTS.md`, `README.md`, `docs/adr.md`, `docs/onboarding.md`, `specs/HOWTO.md`, the takt skills, spec-intake and all spec-reviewer copies contain no `spec.py` references.

## Pending Decisions

1. ~~Keep `spec.py` as a portable fallback?~~ **Resolved: no.** takt owns `takt spec` and its own skills/agent; the external APM package `oscarrenalias/skill-spec-management` is dropped as a dependency and left untouched for non-takt projects.
2. ~~Should spec commands auto-commit like ADRs?~~ **Resolved: no, preserve per-type behaviour** (`auto_commit` spec=false, ADR=true).
3. ~~`takt spec` vs `takt specs`?~~ **Resolved: `takt spec`**, consistent with `takt bead` / `takt adr`.
4. ~~Include the search index?~~ **Resolved: no.** Only the `on_document_changed` hook is added.
5. ~~Migrate existing files?~~ **Resolved: no format change.** Existing files load and validate as-is. Spec writes are line-preserving; ADR writes keep today's full re-dump, so ADR output is identical to the current implementation.
6. ~~Should `takt adr` gain filename resolution?~~ **Resolved: no.** ADRs keep prefix-only resolution and their current messages.
7. ~~Supersede ADR-a188353c entirely?~~ **Resolved:** the replacement ADR restates its full decision with the tooling change, so the specs/current rules stay binding.
