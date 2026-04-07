---
name: "Refactor: split tui.py into package"
id: spec-e923fc0e
description: "Split tui.py (2230 lines) into a focused package with separate modules for state, tree, rendering, actions, and the Textual app. Split test_tui.py accordingly."
dependencies: null
priority: medium
complexity: medium
status: planned
tags:
- refactoring
- tui
scope:
  in: "src/agent_takt/tui.py, tests/test_tui.py"
  out: "cli.py, scheduler.py, onboarding.py"
feature_root_id: null
---
# Refactor: split tui.py into package

## Objective

`tui.py` is the largest file in the codebase at 2230 lines. It contains state management, tree building, panel rendering, operator action flows (merge, retry, status update, scheduler cycle), and the Textual App class with all keybindings and event handlers — all interleaved. `test_tui.py` (3837 lines) mirrors this complexity.

This spec splits both into focused units. No functional changes.

## Principles

- No functional changes. All tests pass after the split.
- Extract, don't rewrite. Move code as-is.
- Preserve public API. `from agent_takt.tui import run_tui` continues to work via `__init__.py` re-export.
- Tests follow source.
- No circular imports between new modules.

## Proposed Module Split

`src/agent_takt/tui.py` → `src/agent_takt/tui/` package:

| New module | Responsibility | Approx lines |
|---|---|---|
| `tui/__init__.py` | `run_tui()` entry point, re-exports | ~10 |
| `tui/state.py` | `TuiRuntimeState` — bead state, selection, filter mode, scroll offsets | ~350 |
| `tui/tree.py` | `build_tree_rows()`, `TreeRow`, `collect_tree_rows()`, tree navigation helpers | ~250 |
| `tui/render.py` | `render_tree_panel()`, `render_detail_panel()`, `format_detail_panel()`, `format_help_overlay()` | ~400 |
| `tui/actions.py` | Operator action flows — merge, retry, status update, scheduler cycle | ~300 |
| `tui/app.py` | Textual `App` subclass, `compose()`, keybindings, event handlers | ~300 |

Dependency order within the package (no cycles): `state` ← `tree` ← `render` ← `actions` ← `app`.

## Proposed Test Split

`tests/test_tui.py` (3837 lines) → multiple files:

| New test file | Covers |
|---|---|
| `tests/test_tui_state.py` | `TuiRuntimeState` — selection, filtering, scroll |
| `tests/test_tui_tree.py` | Tree row building, collection, navigation |
| `tests/test_tui_render.py` | Panel rendering, detail formatting, help overlay |
| `tests/test_tui_actions.py` | Merge, retry, status update action flows |
| `tests/test_tui_app.py` | App composition, keybinding dispatch, event handling |

`tests/test_tui.py` is deleted once all tests are migrated.

## Files to Modify

| Action | File |
|---|---|
| Replace with package | `src/agent_takt/tui.py` → `src/agent_takt/tui/` |
| New | `tests/test_tui_state.py`, `test_tui_tree.py`, `test_tui_render.py`, `test_tui_actions.py`, `test_tui_app.py` |
| Delete after migration | `tests/test_tui.py` |

## Acceptance Criteria

- `from agent_takt.tui import run_tui` works unchanged via re-export
- No TUI module exceeds 500 lines
- No TUI test file exceeds 600 lines
- No circular imports within the `tui/` package (state → tree → render → actions → app)
- `uv run pytest tests/ -n auto -q` passes in full after the split
- `tests/test_tui.py` is deleted
