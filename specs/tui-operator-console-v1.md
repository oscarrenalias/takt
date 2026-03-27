# TUI Operator Console V1

## Objective

Document the currently implemented TUI helper layer so the operator-console spec matches shipped behavior in `src/codex_orchestrator/tui.py`.

This spec describes the shared data model and formatting helpers that exist today. It does not imply that the interactive `orchestrator tui` command is already wired into the CLI.

## Current Status

Implemented now:

- deterministic bead loading and tree-row construction helpers
- stable selection recovery by bead id or previous cursor position
- shared filter constants and filter-to-status mappings
- detail-panel formatting for bead scope and handoff metadata
- footer formatting for filter state, row count, selection index, and per-status totals

Still pending:

- the interactive `orchestrator tui` command
- refresh-loop wiring
- keyboard handling
- merge-flow actions
- dependency checks and optional rendering-library integration

## Data Model

The helper layer currently exposes:

- `TreeRow` records with `bead`, `depth`, `has_children`, and `label`
- deterministic row construction sorted by `bead_id`
- feature-root-aware bead loading with optional filtering before row construction
- selection helpers that preserve the active bead when it remains visible and otherwise clamp to a valid row index

Rows are labeled as `<bead_id> · <title>` with two-space indentation per tree depth level.

## Filter Modes

Supported filter modes come from the shared filter constants in `src/codex_orchestrator/tui.py`.

Named filters:

- `default`: `open`, `ready`, `in_progress`, `blocked`, and `handed_off`
- `all`: every status in display order
- `actionable`: `open` and `ready`
- `deferred`: `handed_off`
- `done`: `done`

Per-status filters are also supported for:

- `open`
- `ready`
- `in_progress`
- `blocked`
- `handed_off`
- `done`

Status display order is:

- `open`
- `ready`
- `in_progress`
- `blocked`
- `handed_off`
- `done`

## Detail Panel Formatting

When a bead is selected, the detail formatter renders:

- bead id, title, status, bead type, and agent type
- parent bead and feature root ids
- dependencies
- acceptance criteria as a block list
- the effective block reason, preferring bead-level `block_reason` and then `handoff_summary.block_reason`
- bead scope fields:
  - `expected_files`
  - `expected_globs`
  - `touched_files`
  - `changed_files`
  - `updated_docs`
- handoff summary fields:
  - `completed`
  - `remaining`
  - `risks`
  - `next_action`
  - `next_agent`
  - `block_reason`
  - `touched_files`
  - `changed_files`
  - `expected_files`
  - `expected_globs`
  - `updated_docs`
  - `conflict_risks`

If no bead is selected, the panel renders:

- `No bead selected.`

For conflict risk display, the formatter prefers `handoff_summary.conflict_risks` and falls back to the bead-level `conflict_risks`.

## Footer Formatting

The footer formatter emits a single line with:

- active filter mode
- visible row count
- selected row number using a 1-based index
- per-status totals in display order

When nothing is selected, the `selected` field renders `-`.

Example footer output:

```text
filter=default | rows=1 | selected=1 | open=0 | ready=0 | in_progress=0 | blocked=1 | handed_off=0 | done=0
```

## Validation Source

The behavior described here is aligned to:

- `src/codex_orchestrator/tui.py`
- `tests/test_orchestrator.py`
- the README TUI helper documentation already present in this worktree

## Deliverables

This corrective documentation update is complete when:

1. README and spec language both describe the implemented helper layer rather than an already-shipped interactive TUI command.
2. Filter semantics match the shared constants in `src/codex_orchestrator/tui.py`.
3. Detail-panel and footer documentation match the formatting covered by regression tests.
