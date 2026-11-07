# scheduler-run-events

## Requirement: Run lifecycle events
<!-- source: spec-1cdb1b8a -->
`takt run` SHALL write exactly one `scheduler_run_started` and exactly one `scheduler_run_finished` event per run, sharing a `run_id` of the form `SR-<8 hex>`, and `scheduler_run_finished` MUST record an `exit_reason`.

### Scenario: Run reaches quiescence
- **WHEN** `takt run` completes all ready beads and a cycle starts no beads
- **THEN** `events.jsonl` contains `scheduler_run_started` and `scheduler_run_finished` with the same `run_id`
- **AND** `scheduler_run_finished.exit_reason` is `quiescence` with `cycles`, counts, `final_state`, `duration_seconds` and `cost_usd` populated

### Scenario: Run hits the cycle cap
- **WHEN** `takt run --max-cycles 1` runs while beads remain ready after the first cycle
- **THEN** `scheduler_run_finished.exit_reason` is `max_cycles` and `cycles` is 1

### Scenario: Run is interrupted
- **WHEN** `Scheduler.run_once` raises `KeyboardInterrupt` during a run
- **THEN** `scheduler_run_finished` is written with `exit_reason` `interrupted` and the `KeyboardInterrupt` propagates

### Scenario: Run fails with an exception
- **WHEN** `Scheduler.run_once` raises `RuntimeError("boom")`
- **THEN** `scheduler_run_finished` has `exit_reason` `error` and `error` `RuntimeError: boom`, and the exception propagates
- **AND** `cycles`, the counts so far and `duration_seconds` are populated

### Scenario: Run receives SIGTERM
- **WHEN** the SIGTERM handler installed by `command_run()` is invoked during a cycle (on the main thread)
- **THEN** `scheduler_run_finished` has `exit_reason` `terminated`, `SystemExit` propagates, and the previous SIGTERM handler is restored

### Scenario: Unrelated SystemExit is an error
- **WHEN** `Scheduler.run_once` raises `SystemExit(2)` without the SIGTERM handler having fired
- **THEN** `scheduler_run_finished.exit_reason` is `error`

### Scenario: Unresolvable feature root starts no run
- **WHEN** `takt run --feature-root B-doesnotexist` is invoked
- **THEN** it exits 1 and no `scheduler_run_started` event or active marker is written

## Requirement: Run context on all events
<!-- source: spec-1cdb1b8a -->
Every event recorded by a `takt run` process while a run is active SHALL include `payload.run_id` equal to that run's ID, without overwriting a `run_id` set explicitly by the caller.

### Scenario: Bead and finalize events carry run_id
- **WHEN** a run completes a bead that records `bead_started`, `bead_completed` and `criteria_gap_recorded`
- **THEN** each of those events has `payload.run_id` equal to the run's `run_id`

### Scenario: Events outside a run are unchanged
- **WHEN** `takt bead delete` records `bead_deleted` with no run active
- **THEN** its payload has no `run_id` key

## Requirement: Active run marker
<!-- source: spec-1cdb1b8a -->
`takt run` SHALL maintain `.takt/runs/active/<run_id>.json` for the lifetime of the run and MUST remove it when the run ends by any path.

### Scenario: Marker exists during and is removed after a run
- **WHEN** a run is in its first cycle
- **THEN** `.takt/runs/active/<run_id>.json` exists with `pid`, `hostname`, `started_at` and `cycle_index` 1
- **AND** after the run returns (normally or via exception) the file no longer exists

### Scenario: Stale marker is reaped as a crashed run
- **WHEN** a marker exists for a pid on this host that is no longer alive and a new `takt run` starts
- **THEN** a `scheduler_run_finished` event with the stale `run_id`, `exit_reason` `crashed` and `detected_by_run_id` set to the new run is written
- **AND** the stale marker file is deleted

### Scenario: Two runs reap the same stale marker
- **WHEN** `reap_stale_runs` is called twice for the same stale marker (simulating two concurrent starts)
- **THEN** exactly one `crashed` `scheduler_run_finished` event is written for that run ID

## Requirement: Enriched bead events
<!-- source: spec-1cdb1b8a -->
Bead terminal events SHALL include `feature_root_id`, `duration_seconds`, `cost_usd` and `attempt`, and `bead_completed` SHALL include `verdict`, using `null` when the value is unavailable.

### Scenario: Completed review bead reports verdict and cost
- **WHEN** a review bead with telemetry `cost_usd` 0.21 and `duration_ms` 45000 completes with verdict `approved`
- **THEN** its `bead_completed` event has `verdict` `approved`, `cost_usd` 0.21, `duration_seconds` 45.0 and the bead's `feature_root_id`
- **AND** `scheduler_run_finished.cost_usd` for that run includes 0.21

### Scenario: Stale telemetry from an earlier attempt is not reported
- **WHEN** a bead whose metadata already holds telemetry from a previous attempt is blocked by a path that stores no new telemetry
- **THEN** its `bead_blocked` event has `duration_seconds`, `cost_usd` and `attempt` all `null`

## Requirement: Scheduler state in summary
<!-- source: spec-1cdb1b8a -->
Unscoped `takt summary` SHALL include a `scheduler` object whose `state` is `running`, `stale` or `idle` based on the active run markers.

### Scenario: Idle project
- **WHEN** no active run markers exist
- **THEN** `takt summary` reports `scheduler.state` `idle` and `scheduler.active_runs` `[]`

### Scenario: Running project
- **WHEN** a marker exists whose pid is alive on this host
- **THEN** `scheduler.state` is `running` and the marker appears in `active_runs` with `alive` true
