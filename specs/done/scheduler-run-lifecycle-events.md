---
name: Scheduler run lifecycle events
id: spec-1cdb1b8a
description: "Scheduler records its own run lifecycle (start/finish/exit reason/totals), run_id on all events, active-run markers; surfaced in takt summary, takt-fleet summary and takt-fleet scheduler-runs"
dependencies: null
priority: medium
complexity: null
status: done
tags:
- scheduler
- events
- fleet
- observability
scope:
  in: null
  out: null
feature_root_id: B-f7558440
---
# Scheduler run lifecycle events

## Objective

`takt run` leaves no record of itself as a *run*. `.takt/logs/events.jsonl` gets per-cycle and per-bead events, but nothing marks when a run starts or finishes, why it stopped, what it achieved, or whether a scheduler is running in a project right now. That is why `takt-fleet runs` can only show runs started through `takt-fleet`, and why a future web-based fleet control plane would have nothing to show for runs started inside a project. This spec makes the scheduler record its own run lifecycle and ties every event to the run that produced it. It also publishes an "active run" marker that cheaply answers "is a scheduler running here?", and surfaces that data in `takt summary`, `takt-fleet summary` and a new `takt-fleet scheduler-runs list`. The event set is chosen to be what a browser control plane over all registered projects would need: a fleet-wide view of which projects are running, a live activity feed, run history with outcomes and cost, and per-bead progress.

## Problems to Fix

1. **No run boundaries.** `command_run()` (`src/agent_takt/cli/commands/run.py:108`) writes only `scheduler_cycle_started` / `scheduler_cycle_completed` (keyed by `pid` and `cycle_index`). Nothing marks the start or end of a run, so the only way to see a run is to group cycle events by pid, and that can't tell a crashed run from one still in progress.
2. **No exit reason or totals.** Why a run ended (quiescence, the `--max-cycles` cap, Ctrl-C, SIGTERM or an exception) is not recorded anywhere; Ctrl-C and exceptions just propagate out of `command_run()`. Run totals and `final_state` go to stdout as JSON on the normal path only, and are never persisted.
3. **Events can't be grouped by run.** Bead events (`bead_started`, `bead_completed`, …), the finalize-time events (`structural_assumption_recorded`, `criteria_gap_recorded`, `verdict_overridden` in `scheduler/finalize.py`) and lease events carry no run identifier.
4. **Bead events are too thin for a dashboard.** `bead_started` / `bead_completed` / `bead_blocked` / `bead_failed` have no `feature_root_id`, and the terminal events carry no duration, cost or verdict, so a UI would have to load every bead file to show them.
5. **No cheap "is it running?" answer.** No marker file exists, so `takt summary` and `takt-fleet summary` can't say whether a project's scheduler is active.
6. **Concurrent writers.** `RepositoryStorage.record_event()` (`src/agent_takt/storage.py:515`) appends without any lock. Bead events are emitted from worker threads when `--max-workers > 1`, and two `takt run --feature-root` processes can append to the same file at once. Large payloads (e.g. `structural_assumption_recorded.statements`) are not guaranteed to be written atomically.

## Changes

### 1. Run identity and lifecycle events (`takt run`)

`command_run()` generates a run ID at start: `SR-` + the first 8 hex characters of `uuid4().hex` (e.g. `SR-3fa91c02`). The `SR-` prefix distinguishes scheduler runs from fleet runs (`FR-…`).

**Startup order** (exactly this):

1. Resolve `--feature-root`. If resolution fails, return 1 as today: no run ID, no events, no marker.
2. Generate the run ID.
3. `reap_stale_runs(storage, new_run_id)` (§3).
4. `storage.set_event_context(run_id=<id>)`.
5. Write `scheduler_run_started`.
6. Write the active marker.
7. Install the SIGTERM handler.
8. Enter the cycle loop.

Steps 4 onward sit inside a single `try/finally`. The `finally` block:

1. writes `scheduler_run_finished`;
2. removes the marker;
3. restores the SIGTERM handler;
4. clears the event context;
5. stops the reporter.

Each step is wrapped so that a failure in one is logged with `logger.warning(..., exc_info=True)` and does not prevent the others.

**`scheduler_run_started`**: written once, before the first cycle:

```json
{"run_id": "SR-3fa91c02", "pid": 41231, "hostname": "oscar-mbp",
 "takt_version": "0.1.65", "runner": "claude", "max_workers": 4,
 "max_cycles": 50, "feature_root_id": null}
```

- `takt_version` comes from `importlib.metadata.version("agent-takt")`.
- `runner` is the resolved runner backend name (`claude` / `codex`). `make_services()` (`src/agent_takt/cli/services.py`) already resolves it from `--runner` → env var → `config.default_runner`. It stores the name on the scheduler as `scheduler.runner_backend: str | None`, and `command_run()` reads that attribute. Do not use `args.runner`, which is `None` when the env var or config default applies. `Scheduler` instances built elsewhere (tests) default `runner_backend` to `None`.
- `feature_root_id` is the resolved `--feature-root` or `null`.

**`scheduler_run_finished`**: written exactly once when the run ends, including on error and interruption (use `try/finally`):

```json
{"run_id": "SR-3fa91c02", "pid": 41231, "exit_reason": "quiescence", "error": null,
 "cycles": 3, "started_count": 5, "completed_count": 4, "blocked_count": 1,
 "correctives_created_count": 1, "deferred_count": 2,
 "final_state": {"done": 120, "blocked": 1, "ready": 0},
 "duration_seconds": 1834.2, "cost_usd": 3.41}
```

- `exit_reason` is one of:
  - `quiescence`: a cycle started no beads.
  - `max_cycles`: the `--max-cycles` cap was reached.
  - `interrupted`: `KeyboardInterrupt`.
  - `terminated`: SIGTERM.
  - `error`: any other exception; `error` holds `"<ExceptionType>: <message>"` truncated to 500 characters.
  - `crashed`: only written by stale-run detection, see §3.
- **Every exit path, including errors and interrupts**, records the following, accumulated up to the point of exit:
  - `cycles`: the number of cycles that began;
  - the de-duplicated counts from the dicts already kept in `command_run()`.
- `final_state` is computed in `finally` using the same feature-root scoping as the CLI JSON block (move that computation into a helper used by both). If computing it raises, it is `null`.
- `duration_seconds` is always set: monotonic time since step 2, rounded to 1 decimal.
- `cost_usd` is the sum of the non-null `cost_usd` values from the terminal bead events (`bead_completed`, `bead_blocked`, `bead_failed`) emitted during this run. `CliSchedulerReporter` accumulates it under a lock, and the reporter exposes it as `run_cost_usd`. It is rounded to 4 decimals, or `null` when no terminal event had a cost. Do **not** recompute it from `bead.metadata`.
- The original exception (`KeyboardInterrupt`, `SystemExit`, others) is re-raised after the event is written. The CLI's stdout JSON block and console output are unchanged on the normal path. On exception paths, they behave exactly as today (nothing extra printed).

**SIGTERM**: when `command_run()` runs on the main thread, it installs a SIGTERM handler for the duration of the run. The handler sets a local `terminated` flag and then raises `SystemExit(143)`. Classification in `finally`:

| Condition | `exit_reason` |
|---|---|
| `terminated` flag set | `terminated` |
| `KeyboardInterrupt` | `interrupted` |
| Any other exception, including a `SystemExit` not raised by the handler | `error` |
| Loop ended because a cycle started no beads | `quiescence` |
| Loop ended because of the cycle cap | `max_cycles` |

The previous handler is restored in `finally`. Off the main thread (e.g. in tests), no handler is installed.

*Accepted limitation*: an interrupt or SIGTERM raised while `Scheduler.run_once` is inside its `ThreadPoolExecutor` block waits for in-flight workers to finish before `finally` runs, so `scheduler_run_finished` may be delayed by the remaining agent time. This spec does not change executor shutdown behaviour. If the process is SIGKILLed during that wait, the next run reports it as `crashed` (§3).

**Existing cycle events** gain `run_id`.

### 2. Run context on every event

`RepositoryStorage` gains a run-scoped event context:

```python
def set_event_context(self, **fields: object) -> None: ...   # e.g. run_id="SR-…"
def clear_event_context(self) -> None: ...
```

While set, `record_event()` merges these fields into every payload, without overwriting keys the caller set explicitly. `command_run()` sets `run_id` right after generating it and clears it in `finally`. As a result, every event written by the process during the run carries `payload.run_id`, including finalize-time events and `lease_expired`. Events written outside a run (`bead_deleted`, `feature_merged`, `merge_gate_failed`) stay unchanged. The record shape `{timestamp, event_type, payload}` is unchanged; `run_id` lives in `payload`.

`record_event()` builds the full line (`json.dumps(record) + "\n"`) first. It then appends it while holding:

- a `threading.Lock` on the storage instance, for worker threads;
- an exclusive `fcntl.flock` on the open events file, for concurrent `takt run` processes.

The append is a single `write()` call, and the file lock is released before the call returns. On platforms without `fcntl`, only the thread lock is used.

`set_event_context()` and `clear_event_context()` mutate a dict that `record_event()` copies under the same thread lock.

### 3. Active-run marker and stale-run detection

- At run start, after `scheduler_run_started`, write `.takt/runs/active/<run_id>.json` atomically (temp file + `os.replace`):

  ```json
  {"run_id": "SR-3fa91c02", "pid": 41231, "hostname": "oscar-mbp",
   "started_at": "2026-11-07T21:15:23+00:00", "runner": "claude",
   "max_workers": 4, "feature_root_id": null, "cycle_index": 1,
   "updated_at": "2026-11-07T21:15:23+00:00"}
  ```

- Rewrite it at the start of each cycle with the current `cycle_index` and `updated_at`.
- Delete it in `finally`, after writing `scheduler_run_finished`.
- One file per run, so concurrent `takt run --feature-root` processes don't collide.
- `.takt/runs/` must never be committed:
  - Ensure the `.gitignore` entries written by `takt init` cover it. If the existing entries (e.g. `.takt/*` or an explicit list) don't, add `.takt/runs/` to them.
  - Make `takt upgrade` add the same entry to an existing project's `.gitignore` through the same gitignore-update helper, idempotently.
  - This repo's own `.gitignore` already covers it via `.takt/*`; verify, don't duplicate.
  - Bead worktree commits never stage it, because `commit_all` only sees the worktree and markers live in the main checkout's `.takt/`.
- **Stale detection**: a marker is *alive* when its `hostname` equals `socket.gethostname()` and `os.kill(pid, 0)` succeeds (`PermissionError` counts as alive). It is *stale* when the hostname matches and the pid is gone. A marker from another hostname is reported as-is with `alive: null` (unknown).
- **Crash recording**: when a new `takt run` starts, it scans `.takt/runs/active/` first. For each stale marker on this host, it writes:

  ```json
  {"run_id": "<stale id>", "pid": <stale pid>, "exit_reason": "crashed",
   "detected_by_run_id": "<new run id>"}
  ```

  as a `scheduler_run_finished` event. `cycles`, the counts, `final_state`, `duration_seconds` and `cost_usd` are all `null`. The event keeps the stale run's `run_id`: `reap_stale_runs` runs before `set_event_context` (startup order step 3), and explicit payload keys are never overwritten by the context anyway.
- **Claim by delete**: `reap_stale_runs` first tries `os.remove(marker)`. It writes the `crashed` event only if the remove succeeded; a `FileNotFoundError` means another process already reaped it, so it skips that marker. This prevents duplicate `crashed` events when two runs start at once.
- Unparseable marker files are deleted without an event, with a logged warning.
- Put this logic in a new module `src/agent_takt/run_registry.py`:
  - `write_active_marker(root, data)`
  - `update_active_marker(root, run_id, **fields)`
  - `remove_active_marker(root, run_id)`
  - `is_pid_alive(hostname, pid) -> bool | None`
  - `list_active_runs(root) -> list[dict]` (each marker dict plus `alive: bool | None`)
  - `reap_stale_runs(storage, new_run_id) -> list[str]` (returns the reaped run IDs)

  `command_run()` stays thin.
- The marker JSON format above is a documented contract (`docs/events.md`). `takt-fleet` reads marker files directly and does **not** import `agent_takt.run_registry`, to keep fleet decoupled from the installed takt version. Fleet uses its own copy of the small liveness check.

### 4. Richer bead events (`CliSchedulerReporter`)

- `bead_started`, `bead_completed`, `bead_blocked`, `bead_failed`, `bead_deferred`, `worktree_ready` gain `feature_root_id`, computed as `storage.feature_root_id_for(bead)`, the same as `summary()`; not the raw `bead.feature_root_id`.
- `bead_completed`, `bead_blocked` and `bead_failed` gain `duration_seconds`, `cost_usd` and `attempt`. These are taken **only from telemetry stored for this execution**, never from an earlier attempt:
  - In `bead_started`, the reporter snapshots `bead.metadata.get("telemetry", {}).get("attempt")` per bead ID (thread-safe).
  - In the terminal event, it re-reads `bead.metadata["telemetry"]`. If that dict exists and its `attempt` differs from the snapshot, the values are fresh:
    - `duration_seconds = duration_ms / 1000` (rounded to 1 decimal);
    - `cost_usd = cost_usd`;
    - `attempt = attempt`.
  - Otherwise (no telemetry, or unchanged attempt, e.g. exception paths that never store telemetry) all three are `null`.
  - The terminal event's non-null `cost_usd` is added to the reporter's `run_cost_usd` (§1).
- `bead_completed` also gains `verdict` (`handoff_summary.verdict` or `null`).

Existing payload keys keep their names and meanings.

### 5. `takt summary`

The **unscoped** `takt summary` JSON gains:

```json
"scheduler": {"state": "running", "active_runs": [ {<marker fields>, "alive": true} ]}
```

- `state` is `running` if any marker is alive, `stale` if markers exist but none are alive, and `idle` if there are no markers.
- The `--feature-root` scoped summary is unchanged.

### 6. `takt-fleet`

- **`takt-fleet summary`** adds a `SCHEDULER` column after `HANDED_OFF`, showing `running` / `idle` / `stale`, or `-` when the project's `takt summary` has no `scheduler` key (older takt) or the project is unhealthy. Rows are built in `src/agent_takt_fleet/cli/commands/summary.py`: pass the `scheduler` object (or `None`) through in each row dict, and render it in `format_fleet_summary`. `--json` includes `scheduler` per project, `null` when absent. Update both and their tests.
- **New `takt-fleet scheduler-runs list`** `[--tag …] [--project …] [--since DURATION] [--limit N (default 20)] [--plain] [--json]`:
  - Reads each target project's `.takt/logs/events.jsonl` and `.takt/runs/active/*.json` directly (no subprocess, no import of `agent_takt`).
  - Pairs `scheduler_run_started` with `scheduler_run_finished` by `payload.run_id`. A `finished` event without a matching `started` (e.g. a `crashed` record for a run whose start event is outside the file) is still listed, with an empty `STARTED_AT`.
  - **Row semantics:**
    - `STARTED_AT` is the `scheduler_run_started` record's top-level `timestamp`, shown as `%Y-%m-%dT%H:%M:%SZ`.
    - `DURATION` is `duration_seconds` from the finished event, formatted like `runs list` durations, or `-` when null or not finished.
    - `STATUS` is the `exit_reason`. A run with no finished event is `running` if its active marker exists and is alive, otherwise `unknown`.
    - Null counts and costs render as `-`. Cost uses the `$0.00` format.
  - **Filtering and ordering:** `--since` (parsed with `_parse_duration` from `src/agent_takt_fleet/runlog.py`) keeps runs whose `STARTED_AT` is within the window, plus finished-only rows whose finished timestamp is within it. Rows are merged across projects, sorted newest first by start time (finished-only rows by finished time), then truncated to `--limit` globally.
  - Columns: `PROJECT`, `RUN_ID`, `STARTED_AT`, `DURATION`, `STATUS`, `CYCLES`, `BEADS` (started count), `DONE` (completed count), `BLOCKED`, `COST`.
  - Missing logs, unreadable lines, and events without `payload.run_id` (older takt) are skipped silently. A project with no runs contributes no rows. Exit 0 even when nothing is listed, printing `No scheduler runs found.`
  - `--json` emits a list of run objects: `project`, `run_id`, `started_at`, `finished_at`, every field from both events, and `status`.
  - Reuse `_add_fleet_filters`, `filter_projects` and `format_table` (separator under the header).
- `takt-fleet runs` stays fleet-only. Its `--help` text says it lists runs started via `takt-fleet dispatch` / `run`, and points to `scheduler-runs` for scheduler runs inside projects.

### 7. TUI

`TuiState` event rendering (`src/agent_takt/tui/state.py`, near the existing suppression of `scheduler_cycle_*`) renders:

- `scheduler_run_started` as `[ts] run SR-… started (runner, max_workers=N)`;
- `scheduler_run_finished` as `[ts] run SR-… finished: <exit_reason> (completed N, blocked N)`, in red for `error` / `crashed`.

Cycle events stay suppressed.

### 8. Documentation

- New `docs/events.md`: the canonical catalog of every event type in `events.jsonl`, with the record shape, each event's payload fields and types, when it's emitted, and the run-context rule. It is the contract for external consumers (fleet, the future web control plane). It includes the event types that already exist (`bead_*`, `worktree_ready`, `lease_expired`, `scheduler_cycle_*`, `structural_assumption_recorded`, `criteria_gap_recorded`, `verdict_overridden`, `feature_merged`, `merge_gate_failed`, `bead_deleted`).
- `docs/fleet.md`: document the `SCHEDULER` column and `scheduler-runs list`, and clarify the scope of `runs`.
- `docs/tui.md`: the new log lines.
- `CLAUDE.md`: short notes under *Quiescence Loop* and *Fleet Manager* pointing to `docs/events.md`.

## Requirements

### ADDED: scheduler-run-events / Run lifecycle events
`takt run` SHALL write exactly one `scheduler_run_started` and exactly one `scheduler_run_finished` event per run, sharing a `run_id` of the form `SR-<8 hex>`, and `scheduler_run_finished` MUST record an `exit_reason`.

#### Scenario: Run reaches quiescence
- **WHEN** `takt run` completes all ready beads and a cycle starts no beads
- **THEN** `events.jsonl` contains `scheduler_run_started` and `scheduler_run_finished` with the same `run_id`
- **AND** `scheduler_run_finished.exit_reason` is `quiescence` with `cycles`, counts, `final_state`, `duration_seconds` and `cost_usd` populated

#### Scenario: Run hits the cycle cap
- **WHEN** `takt run --max-cycles 1` runs while beads remain ready after the first cycle
- **THEN** `scheduler_run_finished.exit_reason` is `max_cycles` and `cycles` is 1

#### Scenario: Run is interrupted
- **WHEN** `Scheduler.run_once` raises `KeyboardInterrupt` during a run
- **THEN** `scheduler_run_finished` is written with `exit_reason` `interrupted` and the `KeyboardInterrupt` propagates

#### Scenario: Run fails with an exception
- **WHEN** `Scheduler.run_once` raises `RuntimeError("boom")`
- **THEN** `scheduler_run_finished` has `exit_reason` `error` and `error` `RuntimeError: boom`, and the exception propagates
- **AND** `cycles`, the counts so far and `duration_seconds` are populated

#### Scenario: Run receives SIGTERM
- **WHEN** the SIGTERM handler installed by `command_run()` is invoked during a cycle (on the main thread)
- **THEN** `scheduler_run_finished` has `exit_reason` `terminated`, `SystemExit` propagates, and the previous SIGTERM handler is restored

#### Scenario: Unrelated SystemExit is an error
- **WHEN** `Scheduler.run_once` raises `SystemExit(2)` without the SIGTERM handler having fired
- **THEN** `scheduler_run_finished.exit_reason` is `error`

#### Scenario: Unresolvable feature root starts no run
- **WHEN** `takt run --feature-root B-doesnotexist` is invoked
- **THEN** it exits 1 and no `scheduler_run_started` event or active marker is written

### ADDED: scheduler-run-events / Run context on all events
Every event recorded by a `takt run` process while a run is active SHALL include `payload.run_id` equal to that run's ID, without overwriting a `run_id` set explicitly by the caller.

#### Scenario: Bead and finalize events carry run_id
- **WHEN** a run completes a bead that records `bead_started`, `bead_completed` and `criteria_gap_recorded`
- **THEN** each of those events has `payload.run_id` equal to the run's `run_id`

#### Scenario: Events outside a run are unchanged
- **WHEN** `takt bead delete` records `bead_deleted` with no run active
- **THEN** its payload has no `run_id` key

### ADDED: scheduler-run-events / Active run marker
`takt run` SHALL maintain `.takt/runs/active/<run_id>.json` for the lifetime of the run and MUST remove it when the run ends by any path.

#### Scenario: Marker exists during and is removed after a run
- **WHEN** a run is in its first cycle
- **THEN** `.takt/runs/active/<run_id>.json` exists with `pid`, `hostname`, `started_at` and `cycle_index` 1
- **AND** after the run returns (normally or via exception) the file no longer exists

#### Scenario: Stale marker is reaped as a crashed run
- **WHEN** a marker exists for a pid on this host that is no longer alive and a new `takt run` starts
- **THEN** a `scheduler_run_finished` event with the stale `run_id`, `exit_reason` `crashed` and `detected_by_run_id` set to the new run is written
- **AND** the stale marker file is deleted

#### Scenario: Two runs reap the same stale marker
- **WHEN** `reap_stale_runs` is called twice for the same stale marker (simulating two concurrent starts)
- **THEN** exactly one `crashed` `scheduler_run_finished` event is written for that run ID

### ADDED: scheduler-run-events / Enriched bead events
Bead terminal events SHALL include `feature_root_id`, `duration_seconds`, `cost_usd` and `attempt`, and `bead_completed` SHALL include `verdict`, using `null` when the value is unavailable.

#### Scenario: Completed review bead reports verdict and cost
- **WHEN** a review bead with telemetry `cost_usd` 0.21 and `duration_ms` 45000 completes with verdict `approved`
- **THEN** its `bead_completed` event has `verdict` `approved`, `cost_usd` 0.21, `duration_seconds` 45.0 and the bead's `feature_root_id`
- **AND** `scheduler_run_finished.cost_usd` for that run includes 0.21

#### Scenario: Stale telemetry from an earlier attempt is not reported
- **WHEN** a bead whose metadata already holds telemetry from a previous attempt is blocked by a path that stores no new telemetry
- **THEN** its `bead_blocked` event has `duration_seconds`, `cost_usd` and `attempt` all `null`

### ADDED: scheduler-run-events / Scheduler state in summary
Unscoped `takt summary` SHALL include a `scheduler` object whose `state` is `running`, `stale` or `idle` based on the active run markers.

#### Scenario: Idle project
- **WHEN** no active run markers exist
- **THEN** `takt summary` reports `scheduler.state` `idle` and `scheduler.active_runs` `[]`

#### Scenario: Running project
- **WHEN** a marker exists whose pid is alive on this host
- **THEN** `scheduler.state` is `running` and the marker appears in `active_runs` with `alive` true

### ADDED: fleet-scheduler-visibility / Scheduler column in fleet summary
`takt-fleet summary` SHALL show each project's scheduler state in a `SCHEDULER` column and MUST show `-` when the project's summary has no `scheduler` key.

#### Scenario: Mixed fleet
- **WHEN** one project reports `scheduler.state` `running` and another project's summary lacks `scheduler`
- **THEN** the table shows `running` and `-` respectively in the `SCHEDULER` column

### ADDED: fleet-scheduler-visibility / Scheduler run history
`takt-fleet scheduler-runs list` SHALL list scheduler runs from each target project's event log, newest first, honouring `--project`, `--tag`, `--since` and `--limit`.

#### Scenario: Finished and running runs across projects
- **WHEN** project A's log has a finished run and project B's log has a started run with an alive active marker
- **THEN** the output lists both, A's with its `exit_reason` and B's with status `running`, newest first

#### Scenario: Legacy log without run events
- **WHEN** a project's log contains only cycle events without `run_id`
- **THEN** that project contributes no rows and the command exits 0

## Files to Modify

| File | Change |
|---|---|
| `src/agent_takt/cli/commands/run.py` | Run ID, `scheduler_run_started`/`finished`, exit reasons, SIGTERM handler, event context, marker lifecycle, `run_id` on cycle events, enriched bead events in `CliSchedulerReporter` |
| `src/agent_takt/run_registry.py` (new) | Active marker write/update/remove, `list_active_runs`, `reap_stale_runs` |
| `src/agent_takt/storage.py` | `set_event_context` / `clear_event_context`, lock in `record_event`, `scheduler` object in unscoped `summary()` |
| `src/agent_takt/tui/state.py` | Render run started/finished events |
| `src/agent_takt/cli/services.py` | Store resolved backend name as `scheduler.runner_backend` |
| `src/agent_takt/scheduler/core.py` | `runner_backend: str \| None = None` attribute on `Scheduler` |
| `src/agent_takt/onboarding/scaffold.py`, `onboarding/upgrade.py` / `cli/commands/init.py` (gitignore helper used by init and upgrade) | Ignore `.takt/runs/` in new and upgraded projects |
| `src/agent_takt_fleet/cli/commands/summary.py`, `formatters.py` | `SCHEDULER` column / `scheduler` in JSON |
| `src/agent_takt_fleet/cli/parser.py`, `cli/__init__.py`, `cli/commands/scheduler_runs.py` (new), `formatters.py` | `scheduler-runs list`, `runs` help text |
| `docs/events.md` (new), `docs/fleet.md`, `docs/tui.md`, `CLAUDE.md` | Documentation |
| `tests/…` | Tests per acceptance criteria |

## Acceptance Criteria

- Every scenario under **Requirements** is covered by a test using `FakeRunner` / `OrchestratorTests` (takt side) or tmp-path project fixtures with real `{timestamp, event_type, payload}` records (fleet side). Fleet tests MUST NOT invoke real `takt run` / `dispatch` against the user's registry.
- `takt run` stdout (console lines and the final JSON block) is byte-for-byte unchanged apart from timing-dependent content; existing `test_cli_run*` tests pass unmodified, except where they assert the exact set of events in `events.jsonl`.
- With `--max-workers 4`, every line in `events.jsonl` after a run parses as JSON (no interleaved writes), as checked by a test that records events from several threads concurrently.
- With two separate processes appending 200 events each to the same `events.jsonl` (e.g. via `multiprocessing`), every line parses as JSON and the line count is 400.
- `.takt/runs/` is ignored by git in this repo, in projects created by `takt init`, and in projects after `takt upgrade`. Running `takt upgrade` twice adds the entry once.
- `docs/events.md` documents every event type emitted anywhere under `src/agent_takt/`, and the active-marker JSON format. This is verified by a test that scans all `.py` files under `src/agent_takt/` with the multi-line-tolerant regex `record_event\(\s*["']([a-z_]+)["']` (`re.S`) and asserts each captured name appears in `docs/events.md`. The test must find at least `merge_gate_failed` (whose name is on the line after `record_event(`) and `scheduler_run_finished`.
- `uv run pytest tests/ -n auto -q` passes.

## Pending Decisions

- ~~Where does `run_id` live in the record?~~ **Resolved:** in `payload`, so the `{timestamp, event_type, payload}` record shape that existing consumers (TUI, `takt-fleet watch`) parse stays unchanged.
- ~~Heartbeat events for liveness?~~ **Resolved:** no. Liveness comes from the active marker plus a pid check, which is enough for local projects (the only supported fleet scope) and avoids event-log noise. The marker's `updated_at` (per cycle) is available if remote liveness is added later.
- ~~Fold scheduler runs into `takt-fleet runs`?~~ **Resolved:** no. A separate `scheduler-runs` command, because fleet runs and scheduler runs have different status models (`success/partial/error` vs `exit_reason`) and different filters (`--command`).
- ~~Log rotation / size limits for `events.jsonl`?~~ **Out of scope.** `scheduler-runs list` reads the whole file; revisit if logs grow large.
- ~~Web UI?~~ **Out of scope.** This spec only produces the data and contracts (`docs/events.md`, the `summary.scheduler` object, active markers) that a later web control-plane spec will consume.
