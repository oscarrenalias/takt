# TUI Operator Actions V1

## Objective

Extend the TUI from read-mostly visibility to practical operator control, so common orchestration actions can be performed without leaving the TUI.

## Scope

In scope:

- start execution cycles from TUI
- retry blocked beads from TUI
- merge done beads from TUI
- simple status updates for selected bead (ready/blocked/done) with confirmation
- action result feedback in status panel

Out of scope:

- planner/spec authoring in TUI
- editing long bead fields/forms
- bulk operations across many beads

## Functional Requirements

### 1. Run Controls

Add keybindings:

- `s`: run one scheduler cycle (`run --once`) scoped to current TUI scope
- `S`: toggle continuous run mode on/off for current scope

Behavior:

- one-shot run should return control to TUI after cycle completes
- continuous mode should show active state in footer/status panel
- failures should be shown as non-fatal status messages

### 2. Bead Actions (Selected Bead)

Add keybindings:

- `t`: retry selected blocked bead
- `m`: merge selected done bead (existing flow, keep confirmation)
- `u`: open quick status action menu for selected bead:
  - mark ready
  - mark blocked
  - mark done

Safety:

- destructive/important actions require confirmation (`y/n`)
- action not allowed for current bead state should show a clear message and no mutation

### 3. Scope Behavior

Actions should respect TUI scope:

- when `--feature-root` is set, run controls operate only within that root
- without scope, actions operate on global state

### 4. Feedback

Bottom status panel should show:

- last action (`run once`, `retry Bxxxx`, `merge Bxxxx`, `update status`)
- success/failure result
- timestamp

## Acceptance Criteria

1. Operator can trigger one-shot run from TUI and see updated bead states afterward.
2. Operator can retry a blocked bead from TUI and see immediate status change.
3. Operator can merge a done bead from TUI with confirmation.
4. Operator can perform basic status updates from TUI with confirmation.
5. Errors do not crash TUI; they are surfaced in status panel.
6. Tests cover allowed/denied action paths and scope behavior.

## Deliverables

- TUI action handlers in `src/codex_orchestrator/tui.py`
- wiring to existing scheduler/storage/merge command paths
- tests in `tests/test_tui.py` and/or `tests/test_orchestrator.py`
- README shortcut/action updates
