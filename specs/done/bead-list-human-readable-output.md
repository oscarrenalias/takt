# Bead List Human-Readable Output

## Objective

Add a human-readable output mode for `orchestrator bead list` so operators can quickly inspect bead state without parsing JSON.

## Why This Matters

`bead list` currently emits JSON only. This is useful for automation but slow for interactive terminal use.

A compact text/table mode improves day-to-day operability while preserving JSON as the default for scripts.

## Scope

In scope:

- add `--plain` flag to `orchestrator bead list`
- print deterministic, aligned, human-readable rows
- keep current JSON output as default behavior
- add tests for `--plain` output and backward compatibility

Out of scope:

- colorized output
- paging/fuzzy search/filtering
- replacing JSON default output

## Functional Requirements

### 1. CLI Interface

Update `orchestrator bead list` to support:

- `orchestrator bead list` (unchanged JSON output)
- `orchestrator bead list --plain` (new human-readable output)

### 2. Plain Output Format

`--plain` should print:

- one header row
- one row per bead
- stable sort by `bead_id`

Required columns:

- `BEAD_ID`
- `STATUS`
- `AGENT`
- `TYPE`
- `TITLE`
- `FEATURE_ROOT`
- `PARENT`

Formatting rules:

- use plain ASCII text only
- no truncation for `TITLE` (allow long titles)
- missing values render as `-`
- no extra JSON when `--plain` is passed

### 3. Empty State

If no beads exist:

- print `No beads found.`
- exit code remains `0`

### 4. Determinism

For consistent tests and operator expectations:

- rows sorted by `bead_id` ascending
- fixed column order as specified above
- deterministic spacing/alignment across runs

## Non-Functional Requirements

- keep implementation small and local to existing CLI path
- do not change bead storage schema
- avoid adding third-party table dependencies

## Acceptance Criteria

1. `orchestrator bead list` still returns the existing JSON array.
2. `orchestrator bead list --plain` prints a readable table-like view with required columns.
3. Output order is deterministic by `bead_id`.
4. Empty state prints `No beads found.` with exit code `0`.
5. Tests verify JSON backward compatibility and `--plain` behavior.

## Example

Given:

- `B0001` done planner epic
- `B0002` ready developer feature

`orchestrator bead list --plain` should resemble:

BEAD_ID  STATUS  AGENT      TYPE     TITLE            FEATURE_ROOT  PARENT
B0001    done    planner    epic     Epic Title       -             -
B0002    ready   developer  feature  Feature Root     B0002         B0001

## Deliverables

- CLI `--plain` support for `bead list`
- tests for output format and empty state
- short README usage note for the new flag
