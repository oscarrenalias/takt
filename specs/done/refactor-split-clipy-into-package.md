---
name: "Refactor: split cli.py into package"
id: spec-3986e80d
description: "Split cli.py (1704 lines) into a focused package with separate modules for argument parsing, formatting, services, and command implementations. Move CLI tests out of test_orchestrator.py."
dependencies: spec-2e7c81af
priority: medium
complexity: medium
status: done
tags:
- refactoring
- cli
scope:
  in: "src/agent_takt/cli.py, tests/test_orchestrator.py (CLI tests only)"
  out: "scheduler.py, tui.py, onboarding.py"
feature_root_id: null
---
# Refactor: split cli.py into package

## Objective

`cli.py` has grown to 1704 lines covering argument parsing, all command implementations (bead, run, merge, telemetry, init, upgrade, plan, summary, retry, tui, asset), formatting helpers, telemetry aggregation, and service wiring — all in one file. CLI-related tests are still embedded in `test_orchestrator.py` alongside scheduler tests.

This spec splits both into focused units. No functional changes.

## Dependencies

Depends on spec-2e7c81af (scheduler refactor) because `tests/helpers.py` (shared `FakeRunner`, `OrchestratorTests` base class) must exist before CLI tests can be moved out of `test_orchestrator.py`.

## Principles

- No functional changes. All tests pass after the split.
- Extract, don't rewrite. Move code as-is.
- Preserve public API. `from agent_takt.cli import main` continues to work via `__init__.py` re-export.
- Tests follow source.
- No circular imports between new modules.

## Proposed Module Split

`src/agent_takt/cli.py` → `src/agent_takt/cli/` package:

| New module | Responsibility | Approx lines |
|---|---|---|
| `cli/__init__.py` | `main()` entry point, re-exports | ~20 |
| `cli/parser.py` | `build_parser()`, all argparse subparser definitions | ~200 |
| `cli/formatting.py` | `format_bead_list_plain()`, `format_claims_plain()`, `_plain_value()`, bead formatting helpers | ~100 |
| `cli/services.py` | `make_services()`, `validate_operator_status_update()`, `apply_operator_status_update()` | ~60 |
| `cli/commands/bead.py` | `command_bead()` — create, show, list, delete, label, graph | ~250 |
| `cli/commands/run.py` | `command_run()`, `CliSchedulerReporter` | ~150 |
| `cli/commands/merge.py` | `command_merge()`, `_emit_merge_conflict_bead()`, `_get_diff_context()`, `_merge_conflict_attempt_cap_exceeded()` | ~180 |
| `cli/commands/telemetry.py` | `command_telemetry()`, `aggregate_telemetry()`, `_format_telemetry_table()`, telemetry helpers | ~250 |
| `cli/commands/init.py` | `command_init()`, `command_upgrade()` | ~270 |
| `cli/commands/misc.py` | `command_plan()`, `command_summary()`, `command_retry()`, `command_handoff()`, `command_tui()`, `command_asset()` | ~200 |

## Proposed Test Split

CLI tests currently in `tests/test_orchestrator.py` → dedicated files:

| New test file | Covers |
|---|---|
| `tests/test_cli_bead.py` | `command_bead` — create, show, list, delete, label, graph |
| `tests/test_cli_run.py` | `command_run`, `CliSchedulerReporter` |
| `tests/test_cli_merge.py` | `command_merge`, conflict bead emission, diff context |
| `tests/test_cli_telemetry.py` | `command_telemetry`, `aggregate_telemetry`, formatting |

Existing `test_cli_upgrade.py`, `test_cli_init.py`, `test_cli_version.py` remain as-is.

After migration, `tests/test_orchestrator.py` should be empty and is deleted (the scheduler tests were moved by spec-2e7c81af).

## Files to Modify

| Action | File |
|---|---|
| Replace with package | `src/agent_takt/cli.py` → `src/agent_takt/cli/` |
| New | `tests/test_cli_bead.py`, `test_cli_run.py`, `test_cli_merge.py`, `test_cli_telemetry.py` |
| Delete after migration | `tests/test_orchestrator.py` |

## Acceptance Criteria

- `from agent_takt.cli import main` works unchanged via re-export
- No CLI module exceeds 500 lines
- No CLI test file exceeds 600 lines
- No circular imports within the `cli/` package
- `uv run pytest tests/ -n auto -q` passes in full after the split
- `tests/test_orchestrator.py` is deleted (all tests migrated between this and spec-2e7c81af)
