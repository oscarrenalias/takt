# document-management

## Requirement: Single document core
<!-- source: spec-b1b9c11d -->
Specs and ADRs SHALL be loaded, resolved, created, updated, moved between status folders and validated by one `DocumentStore` implementation parameterised by a `DocumentType`, and the codebase MUST contain exactly one `## Requirements` parser, used by both `takt spec validate` and the `takt plan` scenario coverage check.

### Scenario: Both types share the store
- **WHEN** `get_store(root, "spec")` and `get_store(root, "adr")` are called
- **THEN** both return `DocumentStore` instances whose `doc_type.name` is `spec` and `adr` respectively

### Scenario: Fenced scenarios are ignored consistently
- **WHEN** a spec's `## Requirements` section contains a `#### Scenario:` line inside a fenced code block
- **THEN** neither `takt spec validate` nor the `takt plan --write` coverage check treats it as a scenario

## Requirement: Spec resolution by ID, prefix or filename
<!-- source: spec-b1b9c11d -->
`takt spec` SHALL resolve a spec from its full ID, a unique ID prefix with or without `spec-`, or a unique filename-stem substring, and MUST exit 1 with an error on stderr listing every match when the query is ambiguous.

### Scenario: Resolve a spec by short prefix
- **WHEN** `takt spec show 1cdb` is run and exactly one spec ID starts with `spec-1cdb`
- **THEN** that spec is shown

### Scenario: Ambiguous query
- **WHEN** `takt spec show refactor` matches several spec filenames
- **THEN** the command exits 1 and stderr contains `matches multiple specs:` followed by every matching ID

## Requirement: Status changes keep folder and frontmatter in sync
<!-- source: spec-b1b9c11d -->
Changing a document's status SHALL update the `status` field and move the file to the folder mapped to that status in one operation, and MUST reject statuses and transitions the document type does not allow.

### Scenario: Spec moves to planned
- **WHEN** `takt spec set status planned <spec>` is run on a draft spec
- **THEN** the file is in `specs/planned/` with `status: planned`, its body is byte-identical and no other line changed

### Scenario: Custom spec status
- **WHEN** `takt spec set status cancelled <spec>` is run
- **THEN** the file is in `specs/cancelled/` with `status: cancelled`

### Scenario: Reserved status rejected
- **WHEN** `takt spec set status current <spec>` is run
- **THEN** the command exits 1 and the file is unchanged

### Scenario: Invalid ADR transition
- **WHEN** `takt adr approve` is run on an ADR whose status is `rejected`
- **THEN** the command exits 1 and the file is unchanged

## Requirement: Requirements merge on spec completion
<!-- source: spec-b1b9c11d -->
`takt spec set status done` SHALL validate the spec's `## Requirements` deltas and merge them into `specs/current/<capability>.md` before moving the spec, and MUST leave all files unchanged and exit 1 when any delta is invalid.

### Scenario: Valid deltas merged
- **WHEN** `takt spec set status done <spec>` is run on a planned spec with a valid ADDED requirement for capability `widgets`
- **THEN** `specs/current/widgets.md` contains the requirement with a `<!-- source: <spec-id> -->` line, the spec is in `specs/done/`, and stdout contains `merged: ADDED widgets / <requirement>`

### Scenario: Invalid delta blocks completion
- **WHEN** `takt spec set status done <spec>` is run on a spec whose ADDED requirement has no `SHALL`/`MUST` sentence
- **THEN** the command exits 1, the spec stays in its folder and `specs/current/` is unchanged

### Scenario: Done spec validates after merge
- **WHEN** `takt spec validate` is run on a done spec whose ADDED requirement already exists in `specs/current/`
- **THEN** it reports the spec OK and exits 0

## Requirement: Frontmatter round-trip
<!-- source: spec-b1b9c11d -->
Writing a document SHALL preserve every unchanged line of the file and quote values so that any string round-trips unchanged, and validation MUST report malformed frontmatter as an error naming the file without a traceback.

### Scenario: Description with a colon
- **WHEN** `takt spec set description "Gates: preflight, tests" <spec>` is run
- **THEN** `takt spec show <spec> --field description` prints `Gates: preflight, tests` and only the `description` line of the file changed

### Scenario: Malformed spec frontmatter
- **WHEN** `takt spec validate` is run and one spec file has invalid YAML frontmatter
- **THEN** stderr names that file, the other specs are still validated, and the exit code is 1

## Requirement: ADR behaviour preserved
<!-- source: spec-b1b9c11d -->
`takt adr` subcommands SHALL keep their existing flags, output, error messages, exit codes, on-disk format and per-transition git commits after moving onto the document core.

### Scenario: ADR lifecycle unchanged
- **WHEN** `takt adr new "X"` is run, the body sections are filled in and `takt adr approve <id>` is run
- **THEN** the ADR is in `adr/approved/` with `accepted_at` set, the approval created exactly one git commit, and the file has a blank line after the closing `---`

### Scenario: Approve with supersedes is atomic
- **WHEN** `takt adr approve <new> --supersedes <old>` is run and writing the superseded ADR fails
- **THEN** both ADR files are left exactly as they were before the command

## Requirement: Spec management needs no standalone script
<!-- source: spec-b1b9c11d -->
Spec lifecycle management SHALL be provided by `takt spec`, and takt MUST NOT ship or depend on a `spec.py` script.

### Scenario: Package contains no spec.py
- **WHEN** the built wheel's file list is inspected
- **THEN** no file named `spec.py` is present under `agent_takt/_data/`

### Scenario: Upgrade disables the old script
- **WHEN** `takt upgrade` runs in a project whose assets manifest lists an unmodified `.claude/skills/skill-spec-management/spec.py`
- **THEN** that file is renamed to `spec.py.disabled` and `.claude/skills/skill-spec-management/SKILL.md` is updated to the `takt spec` version

### Scenario: Spec commands do not create takt state
- **WHEN** `takt spec list` is run in a git repository that has `specs/` but no `.takt/` directory
- **THEN** the specs are listed and no `.takt/` directory is created
