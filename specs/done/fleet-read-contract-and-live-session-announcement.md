---
name: Fleet read contract and live session announcement
id: spec-a2cb3aed
description: Document the .takt/ files the fleet may read, check indirect fleet imports, and announce Claude sessions when they start
dependencies: null
priority: null
complexity: null
status: done
tags:
- fleet
- web-ui
- docs
- events
scope:
  in: null
  out: null
feature_root_id: B-963d942d
---
# Fleet read contract and live session announcement

## Objective

ADR-1897d241 lets `takt-fleet` (and the upcoming read-only `takt-fleet serve`, ADR-31a0427e) read a wider set of `.takt/` files directly: bead JSON, traces, stored audit snapshots, telemetry and `version.json`. It also lets the fleet read a live agent transcript once a documented event names its session. The ADR makes three things preconditions: each of those files must be described in a contract document before the fleet reads it, the fleet boundary test must also catch forbidden modules imported indirectly, and takt must announce a worker's session id when the session starts rather than only when it ends. This spec delivers those three preconditions in takt core. It does not build any UI.

## Problems to Fix

1. **No contract for most `.takt/` files.** `docs/events.md` documents `events.jsonl` and the active-run marker, but nothing documents the shape or stability of `.takt/beads/<id>.json`, `.takt/traces/<bead_id>/<seq>.{meta.json,raw.jsonl,events.jsonl}`, `.takt/audits/<feature_root_id>/<timestamp>.{json,md}`, `.takt/telemetry/<bead_id>/<n>.json` or `.takt/version.json`. Fleet code reading them would depend on undocumented internals, and nothing stops a takt change from breaking a fleet reader.
2. **The fleet boundary test checks only direct imports.** `tests/fleet/test_boundaries.py` (`_FORBIDDEN_MODULES`, `_FORBIDDEN_PREFIXES`, `test_no_forbidden_domain_imports`) flags `import agent_takt.storage` written in fleet code, but not an import of an allowed-looking module that itself imports a forbidden one. For example `agent_takt.audit` imports `agent_takt.storage`. ADR-1897d241 requires indirect imports to be rejected too.
3. **A running session cannot be found until it ends.** `ClaudeCodeAgentRunner` already generates the session id up front and passes `--session-id <uuid4>` (`_start_trace_session` in `runner.py`), but the id only reaches `events.jsonl` in `trace_captured` after the subprocess returns. An observer cannot tail the transcript of a bead that is still running.

## Changes

### 1. Contract document: `docs/project-files.md`

A new operator/integrator document describing every `.takt/` file that ADR-1897d241 allows the fleet to read, with one section per file family:

- `.takt/beads/<bead_id>.json`: every top-level field of the serialised bead, its type, whether it is always present or optional, and the meaning of nested structures the UI will show (`execution_history` entries, `handoff_summary` including `criteria_status`, `assumptions`, `discovered_issues`, `verdict`; `metadata` keys that are part of the contract such as `merge_records`, `telemetry_history`, `transient_retry_count`, `auto_corrective_for`, `discovered_issue_of`).
- `.takt/traces/<bead_id>/<seq>.*` (and `_planner/`): file names, the meta record fields and the normalised event schema. Link to `docs/traces.md` rather than duplicating it where it already documents them.
- `.takt/audits/<feature_root_id>/<timestamp>.{json,md}`: naming and the JSON top-level structure; link to `docs/audit.md`.
- `.takt/telemetry/<bead_id>/<n>.json`: fields; link to `docs/scheduler-telemetry.md` where applicable.
- `.takt/version.json`: fields.
- `.takt/logs/events.jsonl` and `.takt/runs/active/*.json`: one line each pointing to `docs/events.md`.
- A **compatibility rules** section stating the ADR-add43ad0 / ADR-1897d241 rule for these files: new fields are optional, existing fields are not removed, renamed or repurposed; readers must ignore unknown fields; changes to the documented shapes must update this document in the same change.
- A short **Readers** section listing `takt-fleet` as the consumer and stating that readers never write these files.

Link the new document from `docs/fleet.md` and from the CLAUDE.md project layout or ADR section where docs are listed (CLAUDE.md edits are allowed).

### 2. Drift test for the bead contract

A test (e.g. `tests/test_project_files_doc.py`) in the spirit of `tests/test_events_doc.py`: it builds a fully populated `Bead` through the normal model, serialises it the same way storage does, and fails if any top-level key of the serialised JSON is not mentioned in the bead section of `docs/project-files.md`. The failure message names the missing keys. The test must not be satisfied by a key appearing only in another section.

### 3. Transitive boundary check

Extend `tests/fleet/test_boundaries.py` so that, for every `agent_takt` module imported by fleet code (`src/agent_takt_fleet/**`), the test follows that module's own imports within the `agent_takt` package (relative and absolute, module-level and nested in functions) and fails if any path reaches a forbidden module (`agent_takt.storage`, `.scheduler`, `.runner`, `.gitutils`, `.planner`, `agent_takt.cli*`). The failure message shows the import chain, e.g. `agent_takt_fleet/x.py -> agent_takt.audit -> agent_takt.storage`. Resolution is static (AST over source files); it must not import the modules. Add a self-test that runs the checker over a small synthetic package tree in `tmp_path` and proves an indirect forbidden import is reported.

### 4. `agent_session_started` event

When the scheduler runs a bead with `ClaudeCodeAgentRunner`, record an `agent_session_started` event in `.takt/logs/events.jsonl` as each `claude -p` session starts. This applies to the main session and to the structured-output retry session, and the event must be written **before** the subprocess returns. Payload:

| Field | Value |
|---|---|
| `bead_id` | bead being run |
| `agent_type` | bead's agent type |
| `feature_root_id` | bead's feature root (may be null) |
| `backend` | `"claude"` |
| `role` | `main` or `structured_output_retry` (the existing trace role constants) |
| `session_id` | the uuid passed via `--session-id` |
| `started_at` | UTC ISO-8601 |

The run-context rule applies (`run_id` is merged in during `takt run`). Codex runs emit no `agent_session_started` event. Planner runs are out of scope. The runner must not import storage; use a callback or similar hook supplied by the scheduler's execution path. A failure to record the event is logged and must not change the bead's outcome.

Document the event in `docs/events.md` (the `tests/test_events_doc.py` check enforces this), including the note that the transcript can be located as `<claude config dir>/projects/*/<session_id>.jsonl` while the session runs, and that consumers must tolerate the file not existing yet.

## Requirements

### ADDED: fleet-boundaries / Documented project file contract
takt SHALL document, in `docs/project-files.md`, every `.takt/` file family that ADR-1897d241 allows the fleet to read, and a test MUST fail when a top-level bead JSON key is missing from the bead section of that document.

#### Scenario: Undocumented bead field fails the drift test
- **WHEN** the serialised bead contains a top-level key that the bead section of `docs/project-files.md` does not mention
- **THEN** the drift test fails and its message names that key

#### Scenario: Current bead fields are all documented
- **WHEN** the drift test runs against the current `Bead` model and the shipped document
- **THEN** it passes

### ADDED: fleet-boundaries / Indirect forbidden imports are rejected
The fleet boundary test SHALL fail when fleet code imports an `agent_takt` module that directly or indirectly imports a forbidden module, and MUST report the full import chain.

#### Scenario: Indirect import through an allowed-looking module
- **WHEN** a synthetic fleet module imports a synthetic helper module that imports the forbidden storage module
- **THEN** the checker reports a violation whose message contains the chain from the fleet file through the helper to the forbidden module

#### Scenario: Current fleet code passes
- **WHEN** the transitive check runs over the real `src/agent_takt_fleet` tree
- **THEN** it reports no violations

### ADDED: agent-traces / Session start announced
The scheduler SHALL record an `agent_session_started` event carrying the session id, role, bead id, agent type and feature root before each Claude worker session's subprocess returns, and MUST NOT record it for Codex runs.

#### Scenario: Event is visible while the session is still running
- **WHEN** a Claude bead runs with `subprocess.run` patched to read `events.jsonl` during the call
- **THEN** the log already contains an `agent_session_started` event with that bead's id, role `main` and the same session id passed via `--session-id`

#### Scenario: Structured-output retry gets its own announcement
- **WHEN** the first session returns no structured output and the runner starts the structured-output retry session
- **THEN** a second `agent_session_started` event with role `structured_output_retry` and the retry session's id is recorded

#### Scenario: Codex runs are not announced
- **WHEN** a bead runs with the Codex runner
- **THEN** no `agent_session_started` event is recorded for it

#### Scenario: Event recording failure does not change the outcome
- **WHEN** recording the `agent_session_started` event raises
- **THEN** the bead's run proceeds and finishes with the same outcome it would have had

## Files to Modify

| File | Change |
|---|---|
| `docs/project-files.md` | New contract document |
| `docs/events.md` | Document `agent_session_started` |
| `docs/fleet.md` | Link to the contract document |
| `CLAUDE.md` | Mention `docs/project-files.md` |
| `tests/test_project_files_doc.py` | New bead-contract drift test |
| `tests/fleet/test_boundaries.py` | Transitive import check plus self-test |
| `src/agent_takt/runner.py` | Session-start hook invoked when a Claude session starts |
| `src/agent_takt/scheduler/execution.py` | Supply the hook; record `agent_session_started` |
| `tests/test_scheduler_execution.py` (or a new test file) | Event tests |

## Acceptance Criteria

- `docs/project-files.md` exists, covers beads, traces, audits, telemetry, `version.json`, events and active markers, and contains a compatibility rules section.
- The bead drift test passes on the current code and fails with the missing key named when a top-level key is undocumented.
- The transitive boundary check reports indirect forbidden imports with the import chain and passes on the current fleet tree.
- `agent_session_started` is recorded before the Claude subprocess returns for main and structured-output retry sessions, with the payload fields listed above, is not recorded for Codex, and never changes a bead's outcome.
- `docs/events.md` documents `agent_session_started` and `tests/test_events_doc.py` passes.
- `uv run pytest tests/ -n auto -q` passes.

## Scope

In scope: documentation of the read contract, the drift test, the transitive boundary check, and the session-start event.

Out of scope: any fleet reader or server code (spec-a8f0160d), planner session announcement, Codex live sessions, changing any existing file shape.

## Pending Decisions

None.
