---
id: ADR-add43ad0
title: events.jsonl is the cross-process integration surface
status: approved
created_at: '2026-11-08T15:46:58.687028+00:00'
authors:
- Renalias, Oscar
description: Append-only .takt/logs/events.jsonl is the contract between the scheduler
  process and every observer (TUI, takt-fleet).
accepted_at: '2026-11-08T15:52:51.018130+00:00'
superseded_at: null
superseded_by: null
supersedes: []
tags:
- events
- observability
- architecture
related_specs:
- spec-cb04e3ba
- spec-1cdb1b8a
related_beads: []
review_after: null
---

# events.jsonl is the cross-process integration surface

## Summary

> In the context of a scheduler (`takt run`) that executes in its own process while the TUI, `takt-fleet` and other tools need to observe it, facing the need for a live, durable, multi-reader view of scheduler activity without coupling observers to the scheduler process, we decided to make the append-only `.takt/logs/events.jsonl` file the single cross-process integration surface, to achieve observers that work regardless of who started the run and survive crashes, accepting polling latency, an unbounded file and a schema that must be kept stable and documented by hand.

## Context

Before the TUI dashboard spec (spec-cb04e3ba) the TUI could run scheduler cycles itself (`s`/`S` keys, a timed auto-scheduler) and its log panel was fed in-process by `TuiSchedulerReporter`. When `takt run` executed in another terminal, the log panel saw nothing, and a TUI-driven cycle could race an external `takt run`. At the time, `events.jsonl` carried only `bead_completed` and `bead_deleted`. Everything else `CliSchedulerReporter` knew (handoff summaries, deferral reasons, worktree paths) was printed to stdout and lost.

`takt-fleet` (spec-0ba9d2a3) then needed a merged live stream across projects (`takt-fleet watch`). Spec-1cdb1b8a found further gaps: no run boundaries, no exit reasons, events that could not be grouped by run, no cheap "is a scheduler running here?" answer, and `RepositoryStorage.record_event()` appending without a lock even though bead events come from worker threads (`--max-workers > 1`) and two `takt run --feature-root` processes can share one file.

Spec-cb04e3ba ruled real-time streaming over sockets or named pipes out of scope. Spec-1cdb1b8a ruled out heartbeat events (liveness comes from active markers plus a pid check) and log rotation.

## Decision Drivers

* Observers must see runs started by any process: a terminal, the fleet, CI.
* No long-running daemon or extra service per project.
* Several writers (threads and processes) append concurrently.
* Consumers of different versions must tolerate new event types and fields.
* The record must outlive the process that wrote it, including crashed runs.

## Considered Options

### Option A — Append-only JSONL event log as the shared contract

* Good: Any process can read it with plain file I/O; `agent_takt_fleet/tailer.py` and `scheduler_runs.py` read it without importing `agent_takt`.
* Good: Durable. A crashed run still leaves its events, and `reap_stale_runs` can write a `scheduler_run_finished` for it afterwards.
* Good: Byte-offset tailing (`TuiRuntimeState._tail_event_log`, `load_event_log_history`) is cheap and supports scrolling back through history.
* Bad: Polling only. Latency is the reader's poll interval (TUI refresh, 3 s by default; fleet tailer 1 s).
* Bad: Grows without bound. `scheduler-runs list` reads the whole file.

### Option B — Live IPC stream from the scheduler (socket / named pipe)

* Good: Push delivery with low latency.
* Bad: Observers only see events while connected. History and crashed runs are lost.
* Bad: The scheduler has to manage a server endpoint and its lifecycle. Explicitly out of scope in spec-cb04e3ba.

### Option C — Keep the TUI as a scheduler host with in-process reporting

* Good: No serialisation and no file I/O. The reporter calls straight into the UI.
* Bad: Blind to runs in other processes, and races with an external `takt run`. This is the problem spec-cb04e3ba fixed.

### Option D — Observers derive state by scanning bead JSON

* Good: No new artefact.
* Bad: No run boundaries, exit reasons or deferral and failure context. Every refresh has to load every bead file.

## Decision

**`.takt/logs/events.jsonl` is the integration surface between the scheduler and every out-of-process observer.** Binding rules:

* Events are written only through `RepositoryStorage.record_event(event_type, payload)` (`storage.py`). It appends one `{timestamp, event_type, payload}` JSON line under a thread lock plus `fcntl.flock`, so concurrent threads and processes never interleave partial lines.
* The log is append-only. Existing record shapes stay backward compatible: new fields are optional and consumers ignore unknown event types.
* During `takt run`, `set_event_context(run_id=SR-<8hex>)` merges `run_id` into every payload with `setdefault`. Run boundaries are `scheduler_run_started` / `scheduler_run_finished` (with `exit_reason`). Liveness comes from active markers under `.takt/runs/active/` (`run_registry.py`), not from heartbeat events.
* The TUI is a pure dashboard. It tails `events.jsonl` for its log panel and never starts scheduler cycles.
* Every event type must be documented in `docs/events.md`. `tests/test_events_doc.py` scans `src/agent_takt/` for literal `record_event("<name>", ...)` calls and fails on any undocumented name.

## Consequences

### Positive

* The TUI, `takt-fleet watch`, `takt-fleet scheduler-runs list` and `takt summary`'s scheduler state all work for runs started anywhere, including runs not started by the fleet.
* Crashed or killed runs are still accounted for, through stale-marker reaping and the `crashed` exit reason.
* `docs/events.md` is an enforced contract that a later control plane (e.g. a web UI) can build on without touching scheduler internals.

### Negative

* Readers poll, so dashboards lag by up to one poll interval.
* The file has no rotation or size limit. Full scans get slower as it grows.
* The payload schema is a de facto public API. Renaming or removing fields can silently break the TUI, the fleet, or an older takt pinned in another project.
* `flock` is advisory and is skipped where `fcntl` is unavailable (non-POSIX platforms).
