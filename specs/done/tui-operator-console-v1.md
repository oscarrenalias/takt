# TUI Operator Console V1

## Objective

Document the shipped `orchestrator tui` entrypoint and interactive runtime so the operator-console spec matches the behavior implemented in `src/codex_orchestrator/cli.py` and `src/codex_orchestrator/tui.py`.

This spec describes the currently implemented CLI wiring, runtime state, panel formatting, and operator interactions. It is limited to the behavior that exists today and should not be read as a roadmap for additional TUI features.

## Current Status

Implemented now:

- CLI parser support for `orchestrator tui`
- runtime dispatch from `command_tui(...)` to `run_tui(...)`
- `textual` dependency loading with a non-zero exit and retry hint when unavailable
- refresh-loop wiring and three-panel screen rendering
- keyboard handling for selection, filter changes, refresh, quit, scheduler actions, retry, status updates, and merge confirmation
- one-shot scheduler execution from the TUI via the existing `run --once` path
- continuous scheduler execution on timed refreshes when auto-run mode is enabled
- blocked-bead retry via the existing CLI retry path
- keyboard-driven bead status updates with confirmation and validation
- merge initiation for selected `done` beads via the existing CLI merge path
- deterministic bead loading and tree-row construction helpers
- stable selection recovery by bead id or previous cursor position
- shared filter constants and filter-to-status mappings
- detail-panel formatting for bead scope and handoff metadata
- status-panel formatting for current status, latest activity, last action, and last result timestamp
- footer formatting for filter state, run mode, row count, selection index, and per-status totals

Still pending:

- richer non-keyboard controls or alternate layouts
- any broader operator workflows beyond the current single-bead and single-cycle actions

## CLI Entry Point

The CLI defines a `tui` subcommand with:

- `--feature-root <bead_id>` to scope the screen to one feature tree
- `--refresh-seconds <n>` to control the background refresh interval

`--refresh-seconds` defaults to `3` and rejects values below `1`.
`--feature-root` must reference a valid feature-root bead; unknown ids and descendant bead ids are rejected before the TUI starts.

The published console entrypoint is:

- `orchestrator = "codex_orchestrator.cli:main"`

At runtime, `command_tui(...)` delegates to `run_tui(...)`, passing the repository storage handle, optional feature-root scope, refresh interval, and console stream.

## Dependency Handling

The runtime attempts to import `textual` before launching the app.

If `textual` is unavailable:

- the command returns exit code `1`
- the console stream receives the error plus `Hint: install project dependencies so textual is available.`
- repository bead state remains unchanged
- there is no degraded fallback mode for `orchestrator tui`; operators must install dependencies and retry

`pyproject.toml` currently declares `textual>=0.85,<1` as a project dependency, so a standard dependency install should satisfy the runtime requirement.

## Screen Layout And Controls

The app renders:

- a left-side bead tree panel
- a right-side bead detail panel
- a bottom status panel

The title is `Orchestrator TUI`. The subtitle is the selected feature root id when scoped, otherwise `all features`.

Supported key bindings:

- `q`: quit
- `j` / `Down`: move selection down
- `k` / `Up`: move selection up
- `f`: next filter
- `Shift+f`: previous filter
- `r`: manual refresh, or choose `ready` during the status update flow
- `s`: run one scheduler cycle for the current scope
- `S`: toggle continuous scheduler runs on timed refreshes
- `t`: request retry for the selected blocked bead
- `u`: start the status update flow for the selected bead
- `b`: choose `blocked` during the status update flow
- `d`: choose `done` during the status update flow
- `y`: confirm a pending retry or status update
- `n`: cancel a pending merge, retry, or status update
- `m`: request merge for the selected bead
- `Enter`: confirm a pending merge

Manual refresh clears pending actions, refreshes bead state from storage, and updates the status text to `Refreshed bead state.`. Inside the status update flow, the same `r` key is repurposed to select the `ready` target instead of refreshing immediately.
Timed refreshes keep the current selection when possible, keep a pending merge confirmation bound to the originally requested bead, clear that confirmation if the bead is no longer mergeable, and update the activity message with the current time. When continuous mode is enabled, each timed refresh runs one scheduler cycle instead of a read-only refresh.
The one-shot scheduler action calls the same `command_run(...)` path used by `orchestrator run --once`, with `max_workers=1` and the active `feature_root_id` when the TUI is scoped.
Retry is allowed only for selected `blocked` beads, requires an explicit `y` confirmation after `t`, can be cancelled with `n`, and surfaces validation errors in the status panel without mutating bead state. Status updates are limited to `ready`, `blocked`, and `done`, require an explicit target plus `y` confirmation, and surface validation errors in the status panel without mutating bead state.
Merge failures, retry failures, scheduler failures, status validation failures, and early exits from the existing CLI action paths are reported in the status/activity panels and do not terminate the TUI runtime.

## Data Model

The helper layer currently exposes:

- `TreeRow` records with `bead`, `depth`, `has_children`, and `label`
- deterministic row construction sorted by `bead_id`
- feature-root-aware bead loading that keeps the requested feature root bead plus descendants before row construction
- selection helpers that preserve the active bead when it remains visible, default to the first row when there is no prior selection, and otherwise clamp to a valid row index

Rows are labeled as `<bead_id> · <title>` with two-space indentation per tree depth level.

## Filter Modes

Supported filter modes come from the shared filter constants in `src/codex_orchestrator/tui.py`. Cycling follows the declaration order returned by `supported_filter_modes()`.

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

When `feature_root_id` is set, the requested feature-root bead remains in the visible set even if its status is excluded by the active filter. This keeps the tree anchored at the selected feature root while descendants are filtered normally.

## Detail Panel Formatting

When a bead is selected, the detail formatter renders:

- bead id, title, status, bead type, and agent type
- parent bead and feature root ids
- dependencies
- acceptance criteria as a block list, rendering `  -` when the list is empty
- the effective block reason, preferring bead-level `block_reason` and then `handoff_summary.block_reason`
- a `Files:` section with `expected`, `expected_globs`, `touched`, `changed`, and `updated_docs`
- a `Handoff:` section with `completed`, `remaining`, `risks`, `next_action`, `next_agent`, `block_reason`, `touched_files`, `changed_files`, `expected_files`, `expected_globs`, `updated_docs`, and `conflict_risks`

If no bead is selected, the panel renders:

- `No bead selected.`

For conflict risk display, the formatter prefers `handoff_summary.conflict_risks` and falls back to the bead-level `conflict_risks`.

## Footer Formatting

The footer formatter emits a single line with:

- active filter mode
- current run mode (`manual` or `continuous`)
- visible row count
- selected row number using a 1-based index
- per-status totals in display order

When nothing is selected, the `selected` field renders `-`.

Example footer output:

```text
filter=default | run=manual | rows=1 | selected=1 | open=0 | ready=0 | in_progress=0 | blocked=1 | handed_off=0 | done=0 | ? help
```

The status panel prepends:

- `Status: <current status message>`
- `Activity: <latest activity message>`
- `Last Action: <action name>`
- `Last Result: <result> @ <HH:MM:SS>`

## Validation Source

The behavior described here is aligned to:

- `src/codex_orchestrator/cli.py`
- `src/codex_orchestrator/tui.py`
- `tests/test_orchestrator.py`
- `tests/test_tui.py`
- `README.md`

## Deliverables

This corrective documentation update is complete when:

1. README and spec language both describe the shipped `orchestrator tui` CLI entrypoint and runtime behavior.
2. Filter semantics match the shared constants in `src/codex_orchestrator/tui.py`.
3. Detail-panel, status-panel, and footer documentation match the formatting covered by regression tests.
4. Missing-dependency, scheduler-action, retry, status-update, and merge-confirmation behavior are documented without inventing additional runtime features.
