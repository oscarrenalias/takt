# Event Log (`events.jsonl`)

takt appends one JSON object per line to `.takt/logs/events.jsonl` whenever something noteworthy happens: a scheduler run starts, a bead is dispatched, a merge gate fails, and so on. The file is the cross-process feed read by the TUI Scheduler Log panel, `takt-fleet watch` and `takt-fleet scheduler-runs list`.

Events are written through `RepositoryStorage.record_event(event_type, payload)` (`src/agent_takt/storage.py`). Writes are serialised with a thread lock plus an `flock` on the file, so concurrent workers and processes never interleave partial lines.

> `tests/test_events_doc.py` scans `src/agent_takt/` for every literal `record_event("<name>", ...)` call and fails if the name is not mentioned in this file. Add a section here whenever you add an event.

## Record shape

```json
{
  "timestamp": "2026-05-05T08:02:51.123456+00:00",
  "event_type": "bead_started",
  "payload": { "run_id": "SR-1a2b3c4d", "bead_id": "B-abc12def", "...": "..." }
}
```

| Field | Type | Meaning |
|---|---|---|
| `timestamp` | string | UTC ISO-8601 time at which the event was recorded |
| `event_type` | string | One of the event names below |
| `payload` | object | Event-specific fields; see each event |

Event types the reader does not know are ignored by every consumer, so new events can be added without breaking the TUI or fleet tooling.

## Run-context rule

`RepositoryStorage` holds an in-memory *event context* (`set_event_context(**fields)`, `clear_event_context()`). On every `record_event` call, each context field is merged into the payload with `setdefault`:

- While `takt run` is executing, the context is `{"run_id": "<SR-xxxxxxxx>"}`, so every event recorded by that process (bead lifecycle events, finalize events, and so on) carries `payload.run_id`.
- A key supplied explicitly by the caller is **never overwritten** by the context. For example, `reap_stale_runs` records a `scheduler_run_finished` for a *dead* run with that run's own `run_id`, even though the reaping process has a different run context.
- Events recorded outside a run (for example `takt merge`, `takt bead delete`) have no context and no `run_id`.
- The context is per `RepositoryStorage` instance, i.e. per process. It is cleared when the run ends.

## Scheduler run lifecycle

A run is one invocation of `takt run`. Run IDs have the form `SR-<8 hex chars>`.

### `scheduler_run_started`

Emitted once, after stale runs have been reaped and the run context is set.

| Field | Type | Notes |
|---|---|---|
| `run_id` | string | `SR-<8hex>` |
| `pid` | int | Process ID of `takt run` |
| `hostname` | string | `socket.gethostname()` |
| `takt_version` | string \| null | Installed `agent-takt` version, null if unavailable |
| `runner` | string \| null | Runner backend name (`codex`, `claude`) |
| `max_workers` | int | `--max-workers` |
| `max_cycles` | int | `--max-cycles` after normalisation |
| `feature_root_id` | string \| null | Set when the run is scoped to one feature tree |

### `scheduler_cycle_started`

Emitted at the top of each scheduler cycle.

| Field | Type | Notes |
|---|---|---|
| `run_id` | string | |
| `cycle_index` | int | 1-based |
| `max_workers` | int | |
| `feature_root_id` | string \| null | |
| `pid` | int | |

### `scheduler_cycle_completed`

Emitted after each cycle's `run_once` returns.

| Field | Type | Notes |
|---|---|---|
| `run_id` | string | |
| `cycle_index` | int | |
| `started_count` | int | Beads started this cycle |
| `completed_count` | int | |
| `blocked_count` | int | |
| `deferred_count` | int | |
| `pid` | int | |

### `scheduler_run_finished`

Emitted exactly once per run, in a `finally` block, so it is written on normal exit, errors, Ctrl-C and SIGTERM. (If the process is killed without running `finally`, for example `kill -9` or power loss, the *next* `takt run` in the project writes the crashed variant below.) Failure to write the event is logged as a warning and does not mask the run's own result.

| Field | Type | Notes |
|---|---|---|
| `run_id` | string | |
| `pid` | int | |
| `exit_reason` | string | `quiescence`, `max_cycles`, `interrupted` (KeyboardInterrupt), `terminated` (SIGTERM), `error` (unhandled exception), or `crashed` (see below) |
| `error` | string \| null | `"<ExceptionType>: <message>"` truncated to 500 chars; null unless `exit_reason == "error"` |
| `cycles` | int | Cycles started |
| `started_count` | int | Distinct beads started during the run |
| `completed_count` | int | Distinct beads completed |
| `blocked_count` | int | Distinct beads blocked |
| `correctives_created_count` | int | |
| `deferred_count` | int | Total deferrals across cycles |
| `final_state` | object \| null | Bead counts by status, scoped to the feature tree when `feature_root_id` was set; null if it could not be computed |
| `duration_seconds` | float | Wall-clock, rounded to 0.1 s |
| `cost_usd` | float \| null | Summed worker cost, null when no worker reported cost |

**Crashed variant.** At the start of every run, `reap_stale_runs` inspects `.takt/runs/active/*.json`. A marker whose `hostname` matches this host and whose `pid` is no longer alive belongs to a dead run: the marker is deleted and a synthetic `scheduler_run_finished` is recorded with:

- `run_id` and `pid` of the dead run, `exit_reason: "crashed"`, and `detected_by_run_id` (the new run's ID);
- `cycles`, `started_count`, `completed_count`, `blocked_count`, `correctives_created_count`, `deferred_count`, `final_state`, `duration_seconds`, `cost_usd` and `error` all `null`.

Markers from other hosts are left alone (liveness cannot be checked). Unparseable markers or markers with an invalid `pid` are removed with a warning and produce no event. Deleting the marker is the claim step, so concurrent reapers do not record duplicate events.

## Active-run marker

While a run is in progress it keeps a marker file at `.takt/runs/active/<run_id>.json` (managed by `src/agent_takt/run_registry.py`; `.takt/runs/` is gitignored). The file is written atomically (temp file + `os.replace`) and removed when the run ends.

```json
{
  "run_id": "SR-1a2b3c4d",
  "pid": 12345,
  "hostname": "my-laptop.local",
  "started_at": "2026-05-05T08:02:51.123456+00:00",
  "runner": "claude",
  "max_workers": 4,
  "feature_root_id": null,
  "cycle_index": 3,
  "updated_at": "2026-05-05T08:20:11.654321+00:00"
}
```

| Field | Type | Notes |
|---|---|---|
| `run_id` | string | Matches the file name |
| `pid`, `hostname` | int, string | Used for liveness checks |
| `started_at` | string | UTC ISO-8601 |
| `runner` | string \| null | |
| `max_workers` | int | |
| `feature_root_id` | string \| null | |
| `cycle_index` | int | Updated at the start of every cycle |
| `updated_at` | string | UTC ISO-8601, refreshed with `cycle_index` |

Liveness: `is_pid_alive(hostname, pid)` returns `True`/`False` for markers from this host (`os.kill(pid, 0)`; `PermissionError` counts as alive) and `None` for other hosts.

`takt summary` (unscoped) reads the markers via `list_active_runs()` and adds `scheduler: {"state": ..., "active_runs": [...]}`; each active run is the marker plus an `alive` field. `state` is `running` when any marker is alive, `stale` when markers exist but none is confirmed alive, and `idle` when there are none. The scoped form (`--feature-root`) omits `scheduler`.

## Bead lifecycle events

Emitted by the CLI reporter in `takt run` (`src/agent_takt/cli/commands/run.py`). All carry `bead_id`; most carry `agent_type` and `feature_root_id`.

| Event | When | Payload fields |
|---|---|---|
| `lease_expired` | A leased bead's lease ran out and it was reclaimed | `bead_id` |
| `bead_started` | A worker begins executing a bead | `bead_id`, `agent_type`, `title`, `feature_root_id` |
| `worktree_ready` | The feature worktree has been prepared for the bead | `bead_id`, `branch_name`, `worktree_path`, `feature_root_id` |
| `bead_completed` | A bead finishes successfully | `bead_id`, `agent_type`, `summary`, `created_bead_ids` (list), `feature_root_id`, `verdict` (string or null), plus telemetry fields |
| `bead_deferred` | A ready bead was skipped this cycle (dependencies, file-scope conflict, and so on) | `bead_id`, `agent_type`, `reason`, `feature_root_id` |
| `bead_blocked` | A bead ended blocked | `bead_id`, `agent_type`, `summary`, `feature_root_id`, plus telemetry fields |
| `bead_failed` | A bead's worker execution failed | `bead_id`, `agent_type`, `summary`, `feature_root_id`, plus telemetry fields |

*Telemetry fields* (on `bead_completed`, `bead_blocked`, `bead_failed`): `duration_seconds` (float or null), `cost_usd` (float or null), `attempt` (int or null). They are null when the bead's telemetry is stale (same attempt as before this execution) or missing.

## Governance events

Emitted by the scheduler finalizer (`src/agent_takt/scheduler/finalize.py`). All carry `bead_id`, `agent_type` and `feature_root_id`.

| Event | When | Extra payload fields |
|---|---|---|
| `structural_assumption_recorded` | A handoff (including one applied from a recovery bead) contains at least one `structural` assumption | `count` (int), `statements` (list of strings) |
| `criteria_gap_recorded` | A bead completes, without being overridden, while some criteria are `unmet` or `descoped` | `unmet` (int), `descoped` (int) |
| `verdict_overridden` | A tester/review bead's `approved` result was overridden to `needs_changes` | `reason` (`own_criteria` or `dependency_criteria`), `unmet` (list of refs) |

## Discovered-issue events

Emitted by the scheduler finalizer when a worker handoff (or a recovery bead's handoff applied to the original bead) reports `discovered_issues`. One event is written per newly created bead; duplicates (same reporting bead and case-insensitive title) and entries without a title are skipped silently.

| Event | When | Payload fields |
|---|---|---|
| `issue_discovered` | An open bead labelled `discovered` was created from a reported issue | `bead_id` (the new bead), `discovered_issue_of` (the reporting bead), `feature_root_id` (the reporting bead's feature root), `title`, `agent_type` (the new bead's agent type, default `developer`) |

The reporting bead also gets a `discovered_issue_created` entry in its `execution_history` (`details.bead_id` is the new bead). See `uv run takt bead history <id> --event discovered_issue_created`.

## Transient retry events

| Event | When | Payload fields |
|---|---|---|
| `transient_retries_exhausted` | A blocked bead with a transient `block_reason` has been requeued `scheduler.max_transient_retries` times (default 5; counter in `metadata.transient_retry_count`). The bead stays blocked, no corrective is created, and the event is recorded once. `takt retry` resets the counter; so does completing the bead. | `bead_id`, `agent_type`, `feature_root_id`, `retries` (int), `last_reason` |

## Merge and deletion events

| Event | Emitted by | Payload fields |
|---|---|---|
| `merge_gate_failed` | `takt merge` when the preflight (main-into-feature merge), test gate or final `git merge` fails | `feature_root_id`, `gate` (`preflight`, `tests` or `merge`), `reason`, `approver`, `at` (UTC ISO-8601); the final-merge failure also includes `audit_path` |
| `feature_merged` | `takt merge` after a successful merge | The merge record appended to `metadata.merge_records` (approver, SHAs, gate outcomes, `audit_path`, `audit_sha256`; see [audit.md](audit.md)) plus `feature_root_id` |
| `bead_deleted` | `takt bead delete` | `bead_id`, `title` |

## Consumers

| Consumer | What it reads |
|---|---|
| TUI Scheduler Log | Formats most events as log lines; see [tui.md](tui.md#recorded-event-categories) |
| `takt-fleet watch` | Streams all events, prefixed by project |
| `takt-fleet scheduler-runs list` | Pairs `scheduler_run_started` and `scheduler_run_finished` by `run_id`; see [fleet.md](fleet.md#takt-fleet-scheduler-runs-list) |
| `takt-fleet summary` / `takt summary` | Active-run markers (not the log) for the SCHEDULER column |
