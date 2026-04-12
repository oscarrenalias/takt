---
name: TUI dashboard mode: cross-process scheduler event log
id: spec-cb04e3ba
description: Make the TUI scheduler log panel reflect activity from any process running the scheduler, not just the current TUI session.
dependencies:
priority: medium
complexity: medium
status: draft
tags: [tui, scheduler, observability]
scope:
  in: Enriching events.jsonl payloads, writing scheduler events from both reporters, TUI log panel tailing events.jsonl, scheduler lock file for external-run detection
  out: Real-time streaming via sockets or named pipes, changes to bead storage format, changes to scheduler execution logic
feature_root_id:
---
# TUI dashboard mode: cross-process scheduler event log

## Objective

Currently the TUI's scheduler log panel is fed exclusively by `TuiSchedulerReporter` callbacks, which only fire when the scheduler runs inside the TUI process. If `takt run` executes in a separate terminal, the log panel stays silent and the bead tree only updates every 3 seconds via storage polling. This makes the TUI useless as a monitoring dashboard for work kicked off from the CLI. The fix is to make both reporters write structured events (with full context payloads) to the existing `events.jsonl` file, and have the TUI tail that file to populate the log panel — regardless of which process is running the scheduler.

## Problems to Fix

1. **Log panel is blind to external runs.** `CliSchedulerReporter` writes only to console spinners. If `takt run` is running in another terminal, the TUI log panel receives zero updates.
2. **`events.jsonl` payloads are too thin.** The file currently only captures `bead_completed` (with `bead_id` and `agent_type`) and `bead_deleted`. Contextual strings — agent handoff summaries, worktree paths, deferral reasons, error text — are printed to stdout and then discarded.
3. **Some scheduler lifecycle events are not recorded at all.** `bead_started`, `bead_blocked`, `bead_failed`, `bead_deferred`, `worktree_ready`, and `lease_expired` are never written to `events.jsonl`.
4. **No indication in the TUI that an external scheduler process is active.** The TUI may try to start its own scheduler cycle on top of an already-running CLI run, causing concurrent writes to bead files.

## Changes

### 1. Enrich `events.jsonl` event payloads

Extend `RepositoryStorage.record_event()` payloads for existing events and add new event types. All new fields are optional so old log consumers aren't broken.

New and updated event types:

| `event_type` | New payload fields |
|---|---|
| `bead_started` | `bead_id`, `agent_type`, `title` |
| `bead_completed` | `bead_id`, `agent_type`, `summary`, `created_bead_ids: list[str]` |
| `bead_blocked` | `bead_id`, `agent_type`, `summary` |
| `bead_failed` | `bead_id`, `agent_type`, `summary` |
| `bead_deferred` | `bead_id`, `agent_type`, `reason` |
| `worktree_ready` | `bead_id`, `branch_name`, `worktree_path` |
| `lease_expired` | `bead_id` |
| `scheduler_cycle_started` | `max_workers`, `feature_root_id` (nullable), `pid` |
| `scheduler_cycle_completed` | `started_count`, `completed_count`, `blocked_count`, `deferred_count`, `pid` |

### 2. Write scheduler events from both reporters

- **`CliSchedulerReporter`**: on each callback, call `self.storage.record_event(event_type, payload)` in addition to the existing console output. `CliSchedulerReporter.__init__` must accept `storage: RepositoryStorage`.
- **`TuiSchedulerReporter`**: same — call `self._state.storage.record_event(...)` alongside the existing `call_from_thread` path.
- **`command_run`**: emit `scheduler_cycle_started` before the loop and `scheduler_cycle_completed` after.
- Remove the duplicate direct `record_event("bead_completed", ...)` calls in `scheduler/finalize.py` (lines 183, 294–297, 377–380) — these are now handled by the reporter.

### 3. TUI tails `events.jsonl` for the log panel

Add `_tail_event_log()` to `TuiRuntimeState`:
- Track `_event_log_offset: int` (byte offset into `events.jsonl`, initialised to current file size on TUI start so new-only events are shown by default).
- On each `refresh()` call, open `events.jsonl`, seek to `_event_log_offset`, read new lines, advance offset.
- Convert each new JSON line to a human-readable log string using a `_format_event(record) -> str | None` helper (returns `None` for unknown or hidden event types, which are silently skipped).
- Feed the formatted strings to the log panel via the existing `_append_log_line` path.
- `scheduler_cycle_started` and `scheduler_cycle_completed` events are written to `events.jsonl` but `_format_event` returns `None` for them — they never appear in the TUI log panel.

`_format_event` output examples:
```
[14:32:01] developer B-abc123 · "Add login endpoint" started
[14:32:45] B-abc123 worktree .takt/worktrees/B-a7bc3f91 on feature/b-a7bc3f91
[14:33:12] B-abc123 completed — Added login endpoint with JWT validation
[14:34:00] B-abc123 blocked — No structured output after 3 attempts
```

### 5. On-demand history loading

Track `_history_offset: int` alongside `_event_log_offset`. On TUI start both are initialised to the current EOF position.

Add `load_event_log_history(n_lines: int) -> int` to `TuiRuntimeState`:
- Reads backwards from `_history_offset` in fixed 8 KB chunks, accumulating complete lines until `n_lines` displayable (non-`None` from `_format_event`) lines have been collected or the start of the file is reached.
- Prepends the collected lines to the log panel (so they appear above existing content).
- Updates `_history_offset` to the byte position of the earliest line consumed.
- Returns the number of lines actually loaded (0 means no more history).

Expose this as a TUI action bound to `H` (shift-H): load 50 historical lines per press. When `_history_offset` reaches 0 and a further `H` press yields 0 lines, show a one-time dim message in the log panel: `── beginning of event log ──`.

### 4. Scheduler lock file

Write `.takt/scheduler.lock` (containing the running process's PID) when any scheduler run starts, and remove it when the run ends (including on exception). Use a `try/finally` in `command_run` and in `TuiRuntimeState.run_scheduler_cycle`.

The TUI reads the lock file on each refresh:
- If the lock file exists and the PID is **not** the current TUI process, show a status indicator (e.g. `[external run active]` in the log panel header or the border title of `#scheduler-log`).
- If the lock PID belongs to a dead process (checked via `os.kill(pid, 0)`), remove the stale lock file.
- While an external lock is held, disable the `s` / `S` keybindings that start a TUI scheduler cycle, to prevent concurrent runs.

## Files to Modify

| File | Change |
|---|---|
| `src/agent_takt/storage.py` | No signature change to `record_event`; payloads enriched by callers |
| `src/agent_takt/scheduler/finalize.py` | Remove direct `record_event("bead_completed", ...)` calls (now reporter's responsibility) |
| `src/agent_takt/scheduler/execution.py` | Emit `bead_started` and `worktree_ready` via reporter (already done); ensure reporter receives storage ref if needed |
| `src/agent_takt/cli/commands/run.py` | Pass `storage` to `CliSchedulerReporter`; emit `scheduler_cycle_started` / `scheduler_cycle_completed`; write/remove lock file |
| `src/agent_takt/cli/__init__.py` | Pass `storage` through to `CliSchedulerReporter` construction |
| `src/agent_takt/tui/state.py` | Add `_event_log_offset`, `_history_offset`, `_tail_event_log()`, `_format_event()`, `load_event_log_history()`; call tail in `refresh()`; add lock file read logic |
| `src/agent_takt/tui/app.py` | Show external-run indicator in `#scheduler-log` border; disable `s`/`S` bindings when external lock held; update `TuiSchedulerReporter` to also call `record_event`; bind `H` to `load_event_log_history(50)` |

## Acceptance Criteria

- When `takt run` is executing in a separate terminal, the TUI log panel shows `bead_started`, `bead_completed`, `bead_blocked`, and `bead_failed` entries within one refresh cycle (≤ 3 seconds).
- Worktree path and branch are visible in the log panel for each started bead, sourced from `events.jsonl`.
- Agent handoff summaries appear in `bead_completed` log entries in the TUI, sourced from `events.jsonl`.
- Opening the TUI while a CLI run is active shows `[external run active]` in the scheduler log panel header/border.
- While an external run lock is held, pressing `s` or `S` in the TUI has no effect (bindings disabled).
- When the external run finishes (lock file removed), the TUI re-enables the scheduler bindings within one refresh cycle.
- A stale lock file (dead PID) is silently cleaned up on the next TUI refresh; the TUI does not stay locked indefinitely.
- Old events already in `events.jsonl` before the TUI opens are not replayed into the log panel (offset initialised to current EOF).
- Existing `bead_deleted` audit events in `events.jsonl` are unaffected.
- `scheduler_cycle_started` and `scheduler_cycle_completed` entries are present in `events.jsonl` but never appear in the TUI log panel.
- Pressing `H` in the TUI log panel prepends up to 50 historical lines (formatted via `_format_event`) without loading the entire file.
- Pressing `H` repeatedly loads progressively older history in 50-line pages.
- When the start of the file is reached, the panel shows `── beginning of event log ──` and further `H` presses are no-ops.
- All existing tests pass. New unit tests cover: `_format_event` for all event types (including that cycle events return `None`); lock file detection (live PID, dead PID, no file); `_tail_event_log` advancing the offset correctly; `load_event_log_history` with multi-chunk backwards reads and partial-history files.

## Pending Decisions

- ~~Should `scheduler_cycle_started` / `scheduler_cycle_completed` events be displayed in the TUI log panel, or filtered out (too noisy for normal use)?~~ Resolved: write to `events.jsonl` for audit purposes, but `_format_event` returns `None` for both — never shown in the TUI.
- ~~Should the TUI offer a way to scroll back through historical events from before the current session (opt-in "load history" action), or is start-at-EOF always correct?~~ Resolved: lazy reverse-read via `H` keybinding, 50 lines per press, chunked 8 KB reads backwards — no full-file load.
