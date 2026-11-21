---
name: "takt-fleet serve: live agent sessions and fleet-wide attention and cost views"
id: spec-e2bada88
description: Live view of running Claude sessions plus fleet-wide attention and cost pages in the fleet web UI
dependencies: null
priority: null
complexity: null
status: draft
tags:
- fleet
- web-ui
- frontend
- traces
scope:
  in: null
  out: null
feature_root_id: null
---
# takt-fleet serve: live agent sessions and fleet-wide attention and cost views

## Objective

`takt-fleet serve` now shows projects, feature trees, beads, captured traces, audits and scheduler runs, but two things are still missing:
- **Live visibility.** An operator cannot see what an agent is doing while a bead runs. Traces only appear after the session ends.
- **A fleet-wide view of what needs attention.** Blocked beads, untriaged discovered issues, unmet criteria and spending are visible only by opening each project in turn.

This spec adds:
1. A live session view that follows a running Claude Code session's transcript, using the `agent_session_started` event from spec-a2cb3aed.
2. A fleet-wide **Attention** page.
3. A fleet-wide **Cost** page.

All three are read-only and stay within ADR-1897d241, ADR-31a0427e, ADR-73d258d5 and ADR-f42387c6.

## Problems to Fix

1. **Running sessions are invisible.** The scheduler records `agent_session_started` (`bead_id`, `role`, `session_id`, `started_at`; see `docs/events.md#agent_session_started`). The transcript grows at `<claude config dir>/projects/*/<session_id>.jsonl` while the session runs (the config dir is `$CLAUDE_CONFIG_DIR`, else `~/.claude`; worker transcripts land under `~/.claude/projects/<encoded worktree path>/`). Nothing reads it until `trace_captured` copies it into `.takt/traces/` at the end.
2. **No cross-project triage.** Blocked beads, `open` beads labelled `discovered`, unmet criteria and structural assumptions in unmerged features are only visible per project, or per bead in the TUI/CLI.
3. **No spending overview.** `bead_completed`, `bead_blocked` and `bead_failed` events carry `cost_usd`, and `scheduler_run_finished` carries a run total. Nothing aggregates them across projects or over time.

## Changes

### 1. Live sessions (server)

- **Finding live sessions.** A session is *live* when its project's `events.jsonl` has an `agent_session_started` event and no later `trace_captured` event with the same `session_id`, and the started time is within the last 6 hours. The time limit stops a crashed run from showing a session as live forever. `bead_completed`, `bead_blocked` or `bead_failed` for the same bead after the start also ends it.
- **Locating the transcript.** Search `<config dir>/projects/*/<session_id>.jsonl`, where the config dir is `$CLAUDE_CONFIG_DIR`, else `~/.claude`, of the user running the server. Cache the found path. The `session_id` must be a UUID, and the resolved path must be inside `<config dir>/projects/`; anything else is rejected. A missing file is reported as `waiting` (the session may not have written yet), not as an error. This is the read surface ADR-1897d241 allows ("the agent session transcript whose session id a documented event names"). It must be read-only.
- **Normalising.** Use `agent_takt.traces.normalize.normalize_claude_transcript`. It imports only `agent_takt.traces.models`, which the fleet boundary allows. If it fails, fall back to an empty list with an error note. The server keeps per-session state: bytes read so far, the complete lines, and the normalised events. On change it re-reads only appended complete lines (a partial last line is held), then re-normalises the accumulated lines. Tool calls are paired with their results across polls, so a tool call can gain its result later.
- **Size cap.** If the transcript exceeds `common.trace_max_bytes`' default (50 MiB), stop following it and report `too_large`.

### 2. Live sessions (API and stream)

- **`GET /api/live`:** all live sessions across the fleet: `{project, bead_id, bead_title, agent_type, feature_root_id, role, session_id, started_at, status: waiting|live|too_large, event_count, last_event_at, tool_calls, errors}`.
- **`GET /api/projects/{name}/beads/{bead_id}/live`:** live sessions for one bead, plus the most recent ended session of that bead if it ended in the last 10 minutes, marked `ended`, so a page does not go blank at the moment a session finishes.
- **`GET /api/projects/{name}/beads/{bead_id}/live/{session_id}?after=<n>`:** session meta, summary (tool counts, errors, usage totals, as for P2 traces), and normalised events with index `>= after`. The client polls nothing; it uses `after` after stream notifications. Events already sent can change: a tool call gaining its result is re-sent with its original index.
- **SSE:**
  - `live_session_started {project, bead_id, session_id}` when a session becomes live;
  - `live_session_updated {project, bead_id, session_id, event_count}` at most once per poll interval per session when new events exist;
  - `live_session_ended {project, bead_id, session_id}` when it ends.

  Transcripts are only read while at least one SSE client is connected or a live endpoint was requested in the last 60 seconds. Otherwise only `events.jsonl` is followed.

### 3. Live sessions (UI)

- **Live session page** at `#/projects/<name>/beads/<bead_id>/live/<session_id>`:
  - a header with bead, agent, role, started time and elapsed time, and status;
  - a summary bar (tool calls, errors, tokens);
  - a timeline using the P2 trace-view components (tool calls with tool, input summary, duration, error marker, expandable result; assistant text excerpts) that appends new events as they arrive;
  - an auto-scroll toggle, on by default and paused automatically when the user scrolls up;
  - when the session ends, a banner linking to the captured trace once `trace_captured` arrives.
- **Overview:** a "Live now" strip lists all live sessions across the fleet (project, bead title, agent, elapsed time, last tool used) with links to their live pages. It is hidden when there are none.
- **Project page and run page:** in-progress beads with a live session get a "watch live" link.
- **Bead detail:** a live-session banner with a link while one is running.

### 4. Attention page (fleet-wide)

- **`GET /api/attention`:** sections, each a list with project and links:
  - **Blocked beads:** bead, title, agent, `block_reason`, time blocked (last history entry), and whether correctives remain.
  - **Discovered issues awaiting triage:** `open` beads labelled `discovered`, with the reporting bead (`metadata.discovered_issue_of`) and age.
  - **Unmet criteria:** in feature trees without a `merge_records` entry, done beads whose `criteria_status` has `unmet` entries, with the criterion text.
  - **Structural assumptions to review:** in unmerged feature trees, structural assumptions with statement, rationale and bead.
  - **Stale runs:** projects whose scheduler state is `stale`.

  Each section has a count and is sorted most recent first.
- **Page** at `#/attention`, with a header nav link showing the total count as a badge. The page updates on `beads_changed`, `project_updated` and `fleet_event` with a debounced refetch. Each item links to its bead or project. Empty sections collapse to a one-line "nothing here".

### 5. Cost page (fleet-wide)

- **`GET /api/cost?days=<7|30|90>`** (default 30): built from `bead_completed`, `bead_blocked` and `bead_failed` events with non-null `cost_usd` in each project's `events.jsonl` within the window. Returns:
  - totals per project;
  - per-day totals per project (UTC days);
  - the 10 most expensive beads (project, bead, title, agent, cost);
  - cost per agent type;
  - fleet total and number of priced events, plus the count of events with null cost, so gaps are visible.

  Reading must stay bounded: read each log backwards from the end and stop at the first event older than the window.
- **Page** at `#/cost`:
  - a window selector;
  - a fleet total;
  - a stacked per-day bar chart by project, as hand-built SVG (no chart library; ADR-73d258d5), with readable axes, a legend and hover tooltips showing day, project and amount;
  - a per-project table;
  - a per-agent-type table;
  - the top-10 beads table, linking to bead pages.

  It works in light and dark themes and at 360 px (the chart scrolls inside its own box).

### 6. Fixtures, demo, tests and docs

- **Fixtures (`web/demo.py`).** Each fixture project gets a fake Claude config dir under the fixture root, passed to the app, never the real `~/.claude`. Add:
  - live sessions (one growing transcript, one `waiting`, one that ends with a `trace_captured`);
  - blocked beads, `discovered` beads, unmet criteria and structural assumptions in unmerged trees;
  - cost events across 30+ days and several projects.

  The demo driver appends transcript lines to the growing session every few seconds.
- **Config dir injection.** The server takes the Claude config dir from `CLAUDE_CONFIG_DIR` or `~/.claude`. `create_app` accepts an override, used by tests and `--demo`.
- **Layer 1:**
  - live detection rules (start without capture is live; capture ends it; 6 h cutoff; completion event ends it);
  - transcript location safety (non-UUID ids and path escapes are rejected);
  - incremental reading with partial lines and a tool call gaining its result later;
  - the `waiting` state;
  - SSE live events;
  - attention sections against fixtures;
  - cost aggregation (window, per-day buckets, null-cost counting, bounded backward reading).
- **Layer 2 (Playwright `ui`):** the live page appends events without reload as the fixture transcript grows; the overview "Live now" strip; the attention page sections and links; the cost chart renders with legend and tooltips; no horizontal page scroll at 360 px. Use the module-scoped browser fixture.
- **Layer 3:** screenshots of the live session, attention and cost pages into `docs/assets/fleet-ui/`.
- **Docs:** `docs/fleet.md` covers the new pages, routes and SSE events, and that live sessions read Claude transcripts from the local config dir (Claude only; Codex sessions are not shown).

## Requirements

### ADDED: fleet-web-ui / Live session detection
The server SHALL treat a session as live when its `agent_session_started` event has no later `trace_captured` for the same session and no later completion, block or failure event for its bead, and it started less than 6 hours ago.

#### Scenario: Started and not captured
- **WHEN** a fixture project's log has `agent_session_started` for session S of bead B and nothing later for S or B
- **THEN** `/api/live` lists S for B with status `live` (transcript present) or `waiting` (transcript absent)

#### Scenario: Captured session is not live
- **WHEN** a `trace_captured` event for session S follows its `agent_session_started`
- **THEN** S is not listed in `/api/live`

#### Scenario: Old start is ignored
- **WHEN** an `agent_session_started` event is more than 6 hours old with no later events for it
- **THEN** it is not listed as live

### ADDED: fleet-web-ui / Live transcript following
The server SHALL read a live session's transcript only from the Claude config dir's `projects/` tree, reject session ids that are not UUIDs, and return normalised events incrementally by index, including updates to earlier tool calls.

#### Scenario: Incremental events
- **WHEN** a client has received events up to index 9 and the transcript gains a tool result for the tool call at index 8 and two new assistant messages
- **THEN** `?after=10` returns the two new events, and the stream sends `live_session_updated`, and a refetch with `after=8` shows index 8 with its result and duration

#### Scenario: Unsafe session id
- **WHEN** a live endpoint is called with a session id that is not a UUID or contains path separators
- **THEN** the server responds 404 and reads no file

#### Scenario: Partial line held
- **WHEN** the transcript ends with an incomplete JSON line
- **THEN** that line is not normalised until it is completed

### ADDED: fleet-web-ui / Live session screens
The UI SHALL show a fleet-wide "Live now" strip on the overview and a live session page that appends new events without reloading and links to the captured trace after the session ends.

#### Scenario: Watch a session live
- **WHEN** the live session page is open and the fixture transcript gains a tool call
- **THEN** a new timeline entry appears without a page reload, and no console errors occur

#### Scenario: Session ends
- **WHEN** a `trace_captured` event for the open session is appended
- **THEN** the page shows an ended banner with a link to the captured trace

### ADDED: fleet-web-ui / Attention page
The server SHALL report, across all registered projects, blocked beads, open discovered issues, unmet criteria and structural assumptions in unmerged feature trees, and stale scheduler runs, and the UI SHALL show them on an attention page with links.

#### Scenario: Attention sections
- **WHEN** the fixture fleet has two blocked beads in different projects, one open discovered bead, one unmet criterion in an unmerged tree and one in a merged tree
- **THEN** `/api/attention` lists both blocked beads, the discovered bead and only the unmet criterion from the unmerged tree, each with its project

#### Scenario: Navigate from attention
- **WHEN** the user clicks a blocked bead on the attention page
- **THEN** that bead's detail page opens

### ADDED: fleet-web-ui / Cost overview
The server SHALL aggregate `cost_usd` from bead completion, block and failure events per project, per UTC day and per agent type within a selectable window, report how many events had no cost, and read each log only back to the start of the window.

#### Scenario: Window aggregation
- **WHEN** two projects have priced events on several days inside and outside a 7-day window, plus events with null cost
- **THEN** `/api/cost?days=7` sums only events inside the window per project, day and agent type, and reports the null-cost event count

#### Scenario: Cost chart renders
- **WHEN** the cost page is opened on the fixture fleet
- **THEN** an SVG chart with one bar group per day and a legend per project is shown, and hovering a bar shows its day, project and amount

## Files to Modify

| File | Change |
|---|---|
| `src/agent_takt_fleet/web/live.py` (new) | live session detection, transcript location and incremental normalisation |
| `src/agent_takt_fleet/web/readers.py` | backward bounded reading helpers for cost, attention inputs |
| `src/agent_takt_fleet/web/viewmodels.py` | live, attention and cost view models |
| `src/agent_takt_fleet/web/app.py` | new routes, config-dir override |
| `src/agent_takt_fleet/web/stream.py` | live session SSE events, transcript polling gated on demand |
| `src/agent_takt_fleet/web/demo.py` | fake Claude config dir, live transcripts, attention and cost data |
| `src/agent_takt_fleet/web/static/` | live page, "Live now" strip, attention and cost pages, nav |
| `tests/fleet/web/` | layer 1 and layer 2 tests |
| `docs/fleet.md`, `docs/assets/fleet-ui/` | docs and screenshots |

## Acceptance Criteria

- Live sessions are detected by the rules in §1, located safely inside the Claude config dir, followed incrementally, and shown on a live page that updates without reload and links to the captured trace when done.
- The overview shows a "Live now" strip; project, run and bead pages link to live sessions.
- The attention page lists blocked beads, discovered issues, unmet criteria and structural assumptions in unmerged trees, and stale runs, across the fleet, with links.
- The cost page aggregates priced events per project, day and agent type for 7/30/90 days with bounded reads, shows a hand-built SVG chart and tables, and reports null-cost counts.
- No new dependencies; all endpoints GET-only; boundary tests (direct and transitive) pass; fixtures never touch the real `~/.claude`.
- Tests and screenshots as in §6; `uv run pytest tests/ -n auto -q` passes.

## Scope

In scope: live session following for Claude sessions, attention page, cost page, fixtures, tests, docs.

Out of scope: Codex live sessions, planner sessions, any write or control action (retry, promote, delete), alerts or notifications outside the browser, authentication.

## Pending Decisions

None.
