# TUI Operator Console V1

## Objective

Build a simple terminal UI that gives operators continuous visibility into bead execution and basic control actions without switching between multiple CLI commands.

The first iteration should be intentionally small, deterministic, and fully driven by existing orchestrator CLI/storage behavior.

## Why This Matters

Current operations require repeatedly running commands such as `bead list`, `bead show`, and `run --once`, then manually merging completed feature work.

A lightweight TUI improves day-to-day flow by:

- showing relevant bead state continuously
- making bead inspection faster
- allowing immediate merge actions for completed work
- preserving a single-screen operational loop

## Scope

In scope:

- multi-panel TUI with bead list, bead details, and activity/status footer
- default list view focused on actionable states (`ready`, `in_progress`, `blocked`, `deferred`)
- filter control for `all` or a specific state
- tree rendering for parent/child bead relationships
- continuous refresh loop that picks up newly ready beads
- merge action for completed beads

Out of scope:

- editing bead fields directly in the TUI
- planner/spec authoring in TUI
- embedded log streaming from Codex transport
- remote/multi-repo orchestration

## Functional Requirements

### 1. Entry Command

Add:

- `orchestrator tui`

Optional flags:

- `--feature-root <bead_id>` (scope to one feature tree)
- `--refresh-seconds <int>` (default `3`, minimum `1`)

### 2. Panels

Required panels:

- **Left panel: Bead List**
  - default filter: `ready`, `in_progress`, `blocked`, `deferred`
  - optional filter modes: `all`, `ready`, `in_progress`, `blocked`, `deferred`, `done`
  - sorted by bead id ascending
  - tree format showing descendants indented under parents
  - each row shows: `bead_id`, `status`, `agent_type`, short `title`

- **Right panel: Bead Info**
  - shows details for currently selected bead in human-readable form
  - minimum fields:
    - bead id, title, status, type, agent
    - parent id, feature root id
    - dependencies
    - acceptance criteria
    - block reason (if any)
    - expected/touched/changed files summary
    - latest handoff summary sections (`completed`, `remaining`, `next_action`, `next_agent`)

- **Bottom panel: Activity/Status**
  - last refresh timestamp
  - current filter + optional feature-root scope
  - count summary by status
  - last action outcome (e.g., merge success/failure)

### 3. Continuous Monitoring Behavior

The TUI must continuously refresh bead data on the configured interval.

Refresh source:

- read from repository storage (`.orchestrator/beads`) through existing storage service logic

Behavior requirements:

- new beads should appear automatically without restarting TUI
- state transitions should appear automatically
- selection should remain stable when possible across refresh cycles

### 4. Merge Action

From selected bead, provide a merge action when bead status is `done`.

Behavior:

- invoke existing merge path (`orchestrator merge <bead_id>` equivalent behavior)
- show success/failure in bottom status panel
- on failure, show concise error and keep UI running

Safety:

- merge action requires explicit confirmation keypress (`m` then `y`)
- disallow merge action for non-`done` beads with a clear message

### 5. Tree Rendering

Tree rendering should be based on bead parent relationships:

- top-level roots first (epic/feature roots)
- descendants indented
- collapsed/expanded behavior is optional in v1 (default expanded is acceptable)

### 6. Keyboard Controls

Minimum controls:

- `j/k` or arrow keys: move selection
- `f`: cycle filter modes
- `r`: manual refresh now
- `m`: merge selected done bead (with confirmation)
- `q`: quit

### 7. Degraded Mode

If TUI rendering dependency is unavailable at runtime:

- print a clear message with install hint
- exit non-zero
- do not mutate bead state

## Recommended V1 Additions (Low Cost, High Value)

- show a `*` marker for beads whose `metadata.needs_human_intervention` is true
- show one-line hint when a blocked bead has `next_agent` in handoff summary
- add `Enter` to toggle a larger scrollable detail view for long handoff text

## Non-Functional Requirements

- deterministic rendering order for stable operator experience
- no network dependency for core view rendering
- low overhead refresh (avoid expensive full-repo scans outside storage layer)
- keep implementation small and testable

## Acceptance Criteria

The feature is complete when:

1. `orchestrator tui` opens a three-panel interface and renders bead list + selected bead details.
2. Default list shows only `ready`, `in_progress`, `blocked`, and `deferred`.
3. Filter control allows `all` and per-state views.
4. Related beads are rendered in parent/child tree order.
5. UI refresh loop picks up new beads and state transitions without restart.
6. Merge action works for done beads and reports success/failure without crashing UI.
7. Tests cover list filtering, tree ordering, detail rendering selection, and merge action guardrails.

## Suggested Implementation Notes

- keep TUI data model separate from rendering layer:
  - `collect_rows(...)`
  - `build_tree(...)`
  - `format_detail_panel(...)`
- use existing `RepositoryStorage` and `WorktreeManager` for behavior; do not duplicate merge logic
- prefer snapshot-style unit tests for formatting functions and deterministic ordering
- keep runtime loop thin and side-effect free except explicit merge action

## Deliverables

- `orchestrator tui` command wired in CLI
- TUI module with list/detail/status panels
- filter + refresh + selection + merge controls
- tests for tree/filter/detail/merge guardrails
- README section documenting controls and usage
