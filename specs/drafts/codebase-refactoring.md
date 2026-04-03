# Codebase Refactoring

## Objective

Break up oversized modules and classes into smaller, focused units to improve readability, testability, and maintainability. No functional changes — purely structural.

## Why Now

After rapid feature development (Claude Code backend, config externalization, telemetry, multi-worker output, TUI improvements), several files have grown beyond comfortable size. Classes have accumulated multiple responsibilities and methods are long. Refactoring now, before the next wave of features, prevents compounding complexity.

## Timing

This should be executed after B0128 (minor improvements) merges and no feature branches are in flight. Refactoring the same files that active beads are modifying would cause merge conflicts.

## Candidates

### 1. `tui.py` — highest priority

**Problem**: Single file containing state management, tree building, rendering, all TUI actions (merge, retry, status update, filter, scroll, help overlay), scheduler integration, and the Textual App class. `TuiRuntimeState` is the worst offender — it manages bead state, handles user actions, drives rendering, and runs the scheduler.

**Proposed split**:

| New module | Responsibility | Extracted from |
|---|---|---|
| `tui/state.py` | `TuiRuntimeState` — bead state, selection, filter mode, scroll offsets | Current `TuiRuntimeState` core state + navigation |
| `tui/actions.py` | Operator actions — merge, retry, status update flows, scheduler cycle | Current `TuiRuntimeState` action methods |
| `tui/render.py` | `render_tree_panel()`, `render_detail_panel()`, `format_detail_panel()`, `format_help_overlay()` | Current top-level rendering functions |
| `tui/tree.py` | `build_tree_rows()`, `TreeRow`, `collect_tree_rows()`, tree navigation helpers | Current tree-building logic |
| `tui/app.py` | Textual `App` subclass, `compose()`, keybindings, event handlers | Current inner App class |
| `tui/__init__.py` | `run_tui()` entry point, re-exports | Current `run_tui()` |

### 2. `scheduler.py` — medium priority

**Problem**: `_process()` (~100 lines) handles worktree setup, skill isolation, guardrail loading, agent execution, and result handling in one method. `_finalize()` handles state updates, followup creation, corrective planning, telemetry, and git commits. `_reevaluate_blocked()` mixes blocked-bead scanning with corrective logic.

**Proposed split**:

| New module | Responsibility | Extracted from |
|---|---|---|
| `scheduler/core.py` | `Scheduler` class — `run_once()`, bead selection, conflict detection, lease management | Current `Scheduler` core loop |
| `scheduler/execution.py` | `_process()` logic — worktree setup, skill isolation, agent invocation | Current `_process()` |
| `scheduler/finalize.py` | `_finalize()` logic — state updates, followup/corrective creation, telemetry writes | Current `_finalize()` |
| `scheduler/__init__.py` | Re-exports `Scheduler`, `SchedulerReporter`, `SchedulerResult` | Current public API |

### 3. `runner.py` — lower priority

**Problem**: `ClaudeCodeAgentRunner._exec_json()` does too much — builds the command, runs the subprocess, parses the response, extracts telemetry, handles structured output fallback, and triggers retry. The retry method duplicates most of the subprocess/parsing logic.

**Proposed split**:

| Refactoring | Description |
|---|---|
| Extract `_build_command()` | Builds the CLI command list from config + schema + agent_type |
| Extract `_parse_response()` | Parses stdout JSON, extracts structured_output or result, extracts telemetry |
| Extract `_run_subprocess()` | Runs subprocess with timing, env setup, error handling |
| DRY retry logic | `_retry_structured_output()` reuses `_run_subprocess()` and `_parse_response()` instead of duplicating them |

This could stay in a single file — the issue is method length, not module size.

## Principles

- No functional changes. Tests should pass without modification (or with only import path updates).
- Extract, don't rewrite. Move code as-is first, clean up second.
- Preserve public API. External callers (`cli.py`, tests) should see the same imports and interfaces.
- Use `__init__.py` re-exports so existing `from .scheduler import Scheduler` still works.

## Execution Order

1. `tui.py` first — largest file, most tangled, highest benefit
2. `scheduler.py` second — medium complexity, cleaner boundaries
3. `runner.py` third — smallest change, mostly method extraction within the file

Each phase should be its own bead or small group of beads under a shared feature root.

## Acceptance Criteria

- All existing tests pass without changes (or with only import path updates)
- No functional behavior changes
- Each new module has a single clear responsibility
- Public API (what `cli.py` and tests import) is unchanged via re-exports
- No circular imports between the new modules
