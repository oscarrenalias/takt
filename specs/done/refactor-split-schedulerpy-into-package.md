---
name: "Refactor: split scheduler.py into package"
id: spec-2e7c81af
description: "Split scheduler.py (1203 lines) into a focused package with separate modules for core loop, execution, finalisation, followups, and reporter types. Split test_orchestrator.py accordingly."
dependencies: null
priority: medium
complexity: medium
status: done
tags:
- refactoring
- scheduler
scope:
  in: "src/agent_takt/scheduler.py, tests/test_orchestrator.py"
  out: "cli.py, tui.py, onboarding.py, runner.py"
feature_root_id: null
---
# Refactor: split scheduler.py into package

## Objective

`scheduler.py` has grown to 1203 lines with a single `Scheduler` class handling the core loop, bead selection, conflict detection, lease management, execution setup, finalisation, followup/corrective creation, telemetry, and git commits — plus the `SchedulerReporter` protocol and `SchedulerResult` dataclass. `test_orchestrator.py` (5048 lines, 257 test methods) covers all of this plus CLI commands, making it unwieldy.

This spec splits both into focused units. No functional changes — all behaviour, public APIs, and test outcomes stay identical.

## Principles

- No functional changes. All tests pass after the split.
- Extract, don't rewrite. Move code as-is; no cleanup during the move.
- Preserve public API. `from agent_takt.scheduler import Scheduler` continues to work via `__init__.py` re-exports.
- Tests follow source. Each new source module gets a corresponding test file.
- No circular imports between new modules.

## Proposed Module Split

`src/agent_takt/scheduler.py` → `src/agent_takt/scheduler/` package:

| New module | Responsibility | Approx lines |
|---|---|---|
| `scheduler/__init__.py` | Re-exports `Scheduler`, `SchedulerReporter`, `SchedulerResult` | ~10 |
| `scheduler/reporter.py` | `SchedulerReporter` protocol, `SchedulerResult` dataclass | ~50 |
| `scheduler/core.py` | `Scheduler` class — `run_once()`, bead selection, conflict detection, lease management | ~300 |
| `scheduler/execution.py` | `_process()` — worktree setup, skill isolation, guardrail loading, agent invocation | ~250 |
| `scheduler/finalize.py` | `_finalize()` — state updates, telemetry writes, git commits | ~300 |
| `scheduler/followups.py` | `_create_followup_beads()`, `_populate_shared_followup_touched_files()`, `_sync_followup_scope()`, corrective logic | ~200 |

## Proposed Test Split

`tests/test_orchestrator.py` (5048 lines) → multiple files:

| New test file | Covers |
|---|---|
| `tests/helpers.py` | Shared `FakeRunner`, `OrchestratorTests` base class — used by all scheduler and CLI test files |
| `tests/test_scheduler_core.py` | `run_once()`, bead selection, conflict detection, lease management |
| `tests/test_scheduler_execution.py` | `_process()` — worktree setup, skill isolation, agent invocation |
| `tests/test_scheduler_finalize.py` | `_finalize()` — state transitions, telemetry, git commits |
| `tests/test_scheduler_followups.py` | Followup/corrective bead creation, scope population, scope sync |
| `tests/test_scheduler_beads.py` | `DeleteBeadTests`, `StructuredHandoffFieldsTests`, `BeadAutoCommitTests` |

`tests/test_orchestrator.py` is deleted once all scheduler and bead tests are migrated. CLI tests currently embedded in `test_orchestrator.py` are left in place for the CLI refactor spec (spec-3986e80d) to move.

## Files to Modify

| Action | File |
|---|---|
| Replace with package | `src/agent_takt/scheduler.py` → `src/agent_takt/scheduler/` |
| New | `tests/helpers.py` |
| New | `tests/test_scheduler_core.py`, `test_scheduler_execution.py`, `test_scheduler_finalize.py`, `test_scheduler_followups.py`, `test_scheduler_beads.py` |
| Delete after migration | `tests/test_orchestrator.py` |

## Acceptance Criteria

- `from agent_takt.scheduler import Scheduler` works unchanged via re-export
- `from agent_takt.scheduler import SchedulerReporter, SchedulerResult` works unchanged
- No scheduler module exceeds 500 lines
- No scheduler test file exceeds 600 lines
- No circular imports within the `scheduler/` package
- `tests/helpers.py` exports `FakeRunner` and `OrchestratorTests` for reuse by the CLI refactor spec
- `uv run pytest tests/ -n auto -q` passes in full after the split
- `tests/test_orchestrator.py` is deleted (CLI tests left in place for spec-3986e80d)
