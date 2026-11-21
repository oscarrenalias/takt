---
name: "takt-fleet serve: bead explorer, traces and audits"
id: spec-be2ec1ba
description: "Read-only drill-down in the fleet web UI: feature trees, bead detail, traces, telemetry attempts and stored audits, with live bead change notifications"
dependencies: null
priority: null
complexity: null
status: done
tags:
- fleet
- web-ui
- frontend
scope:
  in: null
  out: null
feature_root_id: B-e0542fb7
---
# takt-fleet serve: bead explorer, traces and audits

## Objective

`takt-fleet serve` (spec-a8f0160d) shows projects, scheduler state and a live event feed, but it stops at the project level. To understand what is happening inside a project, an operator still has to go to a terminal and run `takt bead list`, `takt bead show`, `takt bead history`, `takt trace show` and `takt audit` one project at a time. This spec adds read-only drill-down to the web UI:
- **Feature trees:** browse each project's feature trees and beads.
- **Bead detail:** open any bead to see its full state (handoff, acceptance criteria accounting, assumptions, history, files, cost).
- **Agent sessions:** read a bead's captured trace and its prompt and response per attempt.
- **Audits:** view a feature's stored audit snapshots and merge records.

All of it updates live while beads change. Everything is read from the files documented in `docs/project-files.md`, within the boundaries of ADR-1897d241, ADR-31a0427e, ADR-73d258d5 and ADR-f42387c6, which are binding.

## Problems to Fix

1. **No bead-level view in the UI.** `GET /api/projects/{name}` returns only the project card and scheduler runs. Bead files (`.takt/beads/*.json`) are parsed by `ProjectReader.read_beads()` (`web/readers.py`) solely to count statuses.
2. **Feature trees are invisible.** There is no way to see a feature root, its children, their dependencies and the tree's status, criteria and cost rollups across projects. The TUI has this for one project only.
3. **Traces, telemetry prompts and audits are terminal-only.** `.takt/traces/<bead_id>/<seq>.*`, `.takt/telemetry/<bead_id>/<n>.json` and `.takt/audits/<feature_root_id>/<timestamp>.json` are documented read surfaces, but nothing in the fleet reads them.
4. **The stream cannot tell the browser that one bead changed.** `FleetStreamHub.poll_once()` (`web/stream.py`) sends `project_updated` only when the project card changes (counts, scheduler, health, version). A bead whose handoff, history or criteria change without a status change produces no message, so an open bead page would go stale.

## Changes

### 1. Readers (`web/readers.py`)

Extend `ProjectReader`, keeping the P1 rules: raw JSON, tolerant (skip and count unparseable files, never raise), unknown fields preserved, cache by path + size + mtime, no subprocesses, no forbidden imports directly or indirectly.

- **Bead lookup by ID:** return the bead dict from the cached `read_beads()` result. **IDs from URLs are never joined into filesystem paths.** A bead is found only by matching `bead_id` among the beads already read from the directory listing.
- **Traces for a bead:** list `<seq>.meta.json` records under `.takt/traces/<bead_id>/` for a bead ID that exists, sorted by `seq`. Read one record's `<seq>.events.jsonl` with `offset`/`limit` paging and optional filters (`kind`, `errors_only`, `tool`). Compute a summary for the record: counts per tool, error count, total duration, usage totals (summing `usage` events), assistant message count. Seq must be an integer that appears in the listing.
- **Telemetry attempt:** read `.takt/telemetry/<bead_id>/<n>.json` for an attempt number that appears in the directory listing. `prompt_text` and `response_text` are each capped at 256 KiB in the API response, with a `truncated` flag per field.
- **Audit snapshots for a feature root:** list `<timestamp>.json` files under `.takt/audits/<feature_root_id>/` (newest first), each `{audit_id: <timestamp stem>, generated_at, size}`. Read one by `audit_id`, which must match a listed stem. Returned as stored: the server never rebuilds an audit and must not import `agent_takt.audit`.

### 2. View models (`web/viewmodels.py`)

Server-side shaping per ADR-73d258d5, so the client only renders.

- **Bead row:** `bead_id`, `title`, `status`, `agent_type`, `bead_type`, `parent_id`, `feature_root_id`, `labels`, `created_at` (first `execution_history` timestamp), `updated_at` (last), `verdict`, `badges`. `badges` is a list of `structural_assumptions` (count > 0), `unmet_criteria`, `descoped_criteria`, `merged` (feature root with `metadata.merge_records`), `discovered` (label), `corrective` (`metadata.auto_corrective_for`), `recovery` (`recovery_for`), `high_priority`. These mirror the TUI governance badges (`_governance_badge` in `src/agent_takt/tui/render.py`); the meaning must match, the code must not be imported.
- **Bead list:** rows filtered by repeatable `status`, `agent`, `label` (AND for labels, as in `takt bead list`), `feature_root` and a case-insensitive `q` substring over id and title. Sorted by creation time then ID, as `takt bead list` does.
- **Feature list:** one entry per feature root (a bead whose `bead_id == feature_root_id`; epic planner beads with no feature root are excluded), with:
  - title, root status and tree status counts
  - `in_flight` (any bead in progress) and `last_activity_at`
  - criteria totals (met/unmet/descoped across the tree), assumption counts (structural/local)
  - total cost and wall-clock time (see cost below)
  - `last_merge` (last `merge_records` entry or null) and the number of stored audits

  Sorted with in-flight features first, then by last activity.
- **Feature detail:** the feature entry plus a nested tree of the beads (children ordered by creation). Beads whose parent is missing hang under the root. Includes each bead's row, `dependencies` with each dependency's status, the merge records and the audit list.
- **Bead detail:**
  - The bead row plus `description`, `block_reason`, `acceptance_criteria` joined with `criteria_status` by index (criterion, status, evidence; criteria never reported show status `unreported`).
  - The `handoff_summary` text fields, and `assumptions` sorted structural first.
  - Files: expected, globs, touched, changed, updated docs.
  - `dependencies` and `children`, each with status and title.
  - `execution_history` in chronological order.
  - `telemetry`: one row per `metadata.telemetry_history` entry with attempt, outcome, cost, duration, tokens and model where present, plus totals.
  - `traces` (meta list), `merge_records`, `discovered_issues` (beads whose `metadata.discovered_issue_of` is this bead).
  - `raw`: the full bead dict as read, unknown fields included.
- **Cost:** use `agent_takt.bead_metrics` (`bead_cost_usd`, `bead_wall_clock_seconds`; it imports only `agent_takt.models`) when the bead converts to a `Bead`. If conversion or the helper fails, fall back to summing `metadata.telemetry_history` cost fields, or null. Per ADR-1897d241 a failure never breaks the response.

### 3. API (GET only; the read-only rules of P1 apply)

| Route | Returns |
|---|---|
| `GET /api/projects/{name}/features` | feature list |
| `GET /api/projects/{name}/features/{root_id}` | feature detail |
| `GET /api/projects/{name}/features/{root_id}/audits/{audit_id}` | stored audit JSON plus `{audit_id, generated_at}` |
| `GET /api/projects/{name}/beads?status=&agent=&label=&feature_root=&q=&limit=` | bead list (default limit 500, max 2000, with `total`) |
| `GET /api/projects/{name}/beads/{bead_id}` | bead detail |
| `GET /api/projects/{name}/beads/{bead_id}/traces/{seq}?offset=&limit=&kind=&errors_only=&tool=` | trace meta, summary, page of events (default limit 200, max 1000), `total` |
| `GET /api/projects/{name}/beads/{bead_id}/telemetry/{attempt}` | telemetry attempt with capped prompt/response |

Unknown project, bead, feature, seq, attempt or audit: 404 JSON `{error}`. Malformed path parameters (for example `..`, slashes or encoded separators) are 404 and never touch the filesystem outside the documented directories.

### 4. Stream (`web/stream.py`)

Add an SSE event `beads_changed` with `{project, bead_ids}`, sent when the content signature (size + mtime) of one or more bead files changes, or a bead file appears or disappears, between polls. One message per project per poll, listing all changed IDs. The existing `project_updated` behaviour is unchanged. Document the event alongside the P1 events in `docs/fleet.md`.

### 5. Frontend (ADR-73d258d5: vendored Preact + htm, no build step, thin client)

New hash routes:
- `#/projects/<name>/features` (list)
- `#/projects/<name>/features/<root_id>` (tree)
- `#/projects/<name>/beads` (searchable list)
- `#/projects/<name>/beads/<bead_id>` (detail)
- `#/projects/<name>/beads/<bead_id>/traces/<seq>` (trace)
- `#/projects/<name>/beads/<bead_id>/telemetry/<attempt>` (prompt and response)
- `#/projects/<name>/features/<root_id>/audits/<audit_id>` (audit)

- **Navigation.** The project page gains tabs or links to Features and Beads. A breadcrumb (Overview › project › feature › bead › trace) appears on every drill-down screen. Bead IDs shown anywhere, including activity items whose payload has a `bead_id`, link to the bead page.
- **Feature list:** cards or rows with status bar (counts), in-flight marker, criteria and assumption counts, cost, last merge and audits count.
- **Feature tree:** collapsible tree of beads with status colour, agent type, badges and verdict. Dependencies are shown on hover or expansion. Rollup header as in the feature list. Merge records and audit links.
- **Bead list:** filter controls for status, agent and label, a text search box, and result count. Rows link to detail.
- **Bead detail.** Sections, each collapsible:
  - **Summary:** status, agent, type, verdict, block reason, parent, feature root, labels.
  - **Description.**
  - **Acceptance criteria:** each with met/unmet/descoped/unreported status and evidence.
  - **Handoff:** completed, remaining, risks, design decisions, known limitations, test coverage notes.
  - **Assumptions:** structural highlighted.
  - **Files.**
  - **Dependencies and children.**
  - **History:** timeline.
  - **Cost and attempts:** with links to prompt/response views.
  - **Traces:** links.
  - **Discovered issues.**
  - **Raw JSON:** collapsed by default.
- **Trace view:** meta header (status, role, backend, session id, started/finished, tool calls) and summary (tools table, errors, tokens). An event timeline where tool calls show tool name, a one-line input summary, duration, an error marker and an expandable result excerpt, and assistant text shows its excerpt. Filters for errors only, kind and tool. "Load more" paging.
- **Telemetry view:** prompt and response in scrollable monospace blocks, with a truncation notice when capped, plus the parsed result and metrics.
- **Audit view:** renders the stored report's sections (feature, status, beads table, gates with verdict history, assumptions, criteria with gaps, cost, trace totals, merges). Unknown keys are shown in a generic key/value block rather than dropped.
- **Live updates:** an open feature tree, bead list or bead detail refetches (debounced) when `beads_changed` names a bead it shows, or names any bead of the shown project for list and tree screens. The trace view refetches its summary when the trace's bead changes.
- All new key regions and controls carry `data-testid` attributes (for example `feature-row`, `bead-tree`, `bead-tree-node-<id>`, `bead-row`, `bead-filter-status`, `bead-search`, `bead-detail`, `criteria-item`, `assumption-item`, `history-item`, `trace-event`, `trace-filter-errors`, `audit-section`, `breadcrumb`).
- Same visual language as P1: light and dark via `prefers-color-scheme`, consistent status colours, usable from 360 px with no horizontal page scroll. Long monospace blocks scroll inside their own box.

### 6. Fixture builder and demo (`web/demo.py`)

Extend the fixtures so every new screen has realistic data. They need at least:
- One feature tree with developer, tester, docs and review beads, a corrective child, a recovery bead and a discovered-issue bead.
- Assumptions (structural and local) and criteria statuses (met, unmet, descoped).
- `telemetry_history` and telemetry attempt files, including one prompt over 256 KiB.
- Trace records (`captured` with events covering every `kind`, including tool errors, and one `missing`).
- Merge records and two stored audit snapshots.
- A bead with unknown extra fields and nested unknown keys in an audit.

The demo activity driver also rewrites a bead's handoff or history periodically, so `beads_changed` can be seen live.

### 7. Tests (ADR-f42387c6)

- **Layer 1:** `TestClient` tests for every new endpoint and view model against fixtures, including path-parameter safety, paging and filters, the cost fallback and the `beads_changed` stream event.
- **Layer 2:** Playwright `ui` tests for each new screen and the live update of an open bead page, using the module-scoped browser fixture pattern now in `tests/fleet/web/test_ui.py`. Never leave an event loop running for later tests, and fail on console errors.
- **Layer 3:** screenshots of feature list, feature tree, bead detail, trace view and audit view from demo data into `docs/assets/fleet-ui/`.

### 8. Documentation

Extend the `takt-fleet serve` section of `docs/fleet.md` with the new screens (with screenshots), routes and the `beads_changed` event, and update `CLAUDE.md` if the `web/` layout description changes.

## Requirements

### ADDED: fleet-web-ui / Bead explorer data
The server SHALL list a project's beads with filters and return any bead's full detail, including criteria accounting, assumptions, history, telemetry rows and the raw bead with unknown fields, and MUST locate beads only among those read from the project's bead directory.

#### Scenario: Filtered bead list
- **WHEN** `/api/projects/<name>/beads?status=blocked&agent=developer` is requested for a fixture project with blocked and done developer and tester beads
- **THEN** only blocked developer beads are returned, sorted by creation time, with `total` matching

#### Scenario: Bead detail joins criteria and status
- **WHEN** the detail of a bead with three acceptance criteria and `criteria_status` entries for indexes 0 and 2 is requested
- **THEN** the response lists three criteria with statuses from entries 0 and 2 and `unreported` for index 1, and `raw` contains the bead's unknown extra fields

#### Scenario: Governance badges
- **WHEN** a bead has a structural assumption, an unmet criterion and the `discovered` label
- **THEN** its row's `badges` include `structural_assumptions`, `unmet_criteria` and `discovered`

#### Scenario: Unsafe bead id
- **WHEN** a bead path parameter is `..%2F..%2Fetc` or names no bead in the directory
- **THEN** the server responds 404 and opens no file outside the project's documented `.takt/` directories

#### Scenario: Cost fallback
- **WHEN** a bead cannot be converted to an `agent_takt` `Bead`, but its `telemetry_history` carries cost values
- **THEN** its detail reports the summed cost and the response is 200

### ADDED: fleet-web-ui / Feature trees
The server SHALL list each project's feature trees with status, criteria, assumption, cost, merge and audit rollups, and return a feature's beads as a nested tree.

#### Scenario: Tree structure
- **WHEN** a feature detail is requested for a root with children and a bead whose parent is missing
- **THEN** children are nested under their parents ordered by creation, and the orphan appears under the root

#### Scenario: In-flight features first
- **WHEN** a project has one feature with a bead in progress and an older idle feature with more recent activity
- **THEN** the in-progress feature is listed first

### ADDED: fleet-web-ui / Traces and attempts
The server SHALL return a bead's trace records with a computed summary and paged, filterable events, and each telemetry attempt with prompt and response capped at 256 KiB.

#### Scenario: Paged and filtered trace events
- **WHEN** a trace with 450 events, 3 of them failed tool calls, is requested with `limit=200` and then with `errors_only=true`
- **THEN** the first response has 200 events and `total` 450, and the second has exactly the 3 failed tool calls, and both carry the same summary with error count 3

#### Scenario: Large prompt is capped
- **WHEN** a telemetry attempt whose prompt exceeds 256 KiB is requested
- **THEN** the returned prompt is at most 256 KiB and `truncated` is true for the prompt and false for a short response

#### Scenario: Missing trace record
- **WHEN** a trace record with status `missing` and no events file is requested
- **THEN** the server responds 200 with its meta, an empty event list and `total` 0

### ADDED: fleet-web-ui / Stored audits
The server SHALL list and return a feature's stored audit snapshots exactly as written, and MUST NOT build audits itself.

#### Scenario: Audit returned as stored
- **WHEN** an audit snapshot containing keys unknown to the server is requested by its listed id
- **THEN** the response contains the stored report with those keys intact

#### Scenario: Unknown audit id
- **WHEN** an audit id that is not among the listed snapshot stems is requested
- **THEN** the server responds 404

### ADDED: fleet-web-ui / Bead change notifications
The SSE stream SHALL send a `beads_changed` event naming the project and every bead whose file changed, appeared or disappeared since the previous poll, even when the project's status counts do not change.

#### Scenario: Handoff change without status change
- **WHEN** a done bead's file is rewritten with a new handoff summary while a client is connected
- **THEN** the client receives `beads_changed` for that project listing that bead id, and no `project_updated` for it

### ADDED: fleet-web-ui / Drill-down screens
The UI SHALL provide feature list, feature tree, bead list, bead detail, trace, attempt and audit screens reachable by navigation from the project page, with working breadcrumbs, and an open bead page MUST update without a reload when its bead changes.

#### Scenario: Drill down from project to trace
- **WHEN** the user opens a project, then a feature, then a bead in the tree, then one of its traces
- **THEN** each screen renders its key region, the breadcrumb shows the path, and no console errors occur

#### Scenario: Live bead detail
- **WHEN** a bead detail page is open and the bead's history gains an entry in its file
- **THEN** the new history item appears without a reload

#### Scenario: Bead search
- **WHEN** the user types part of a bead title into the bead list search box
- **THEN** only beads whose id or title contain that text are listed

#### Scenario: Activity links to beads
- **WHEN** the activity feed shows an event whose payload has a `bead_id`
- **THEN** clicking it opens that bead's detail page

## Files to Modify

| File | Change |
|---|---|
| `src/agent_takt_fleet/web/readers.py` | bead lookup, traces, telemetry attempts, audit snapshots |
| `src/agent_takt_fleet/web/viewmodels.py` | bead rows and badges, bead list, feature list and detail, bead detail, cost |
| `src/agent_takt_fleet/web/app.py` | new GET routes |
| `src/agent_takt_fleet/web/stream.py` | `beads_changed` |
| `src/agent_takt_fleet/web/demo.py` | richer fixtures and demo activity |
| `src/agent_takt_fleet/web/static/` | router, breadcrumbs, new screens, styles |
| `tests/fleet/web/` | layer 1 and layer 2 tests |
| `docs/fleet.md`, `docs/assets/fleet-ui/` | docs and screenshots |
| `CLAUDE.md` | layout note if needed |

## Acceptance Criteria

- All routes in §3 exist with the documented shapes, are GET-only, spawn no subprocesses, and return 404 for unknown or unsafe identifiers.
- Bead, trace, telemetry and audit data are read only from the files documented in `docs/project-files.md`; the fleet boundary tests (direct and transitive) pass.
- Unknown fields in beads and audits reach the client (`raw` and generic blocks); a malformed file never causes a 500.
- `beads_changed` is emitted for content-only bead changes.
- Feature list, feature tree, bead list, bead detail, trace, attempt and audit screens work as described in §5, link to each other with breadcrumbs, and update live.
- Fixtures cover the cases in §6 and demo mode shows them.
- Layer 1 tests run in the default suite; `ui` tests pass with Chromium and skip without it, without leaking an event loop; screenshots are in `docs/assets/fleet-ui/` and referenced from `docs/fleet.md`.
- `uv run pytest tests/ -n auto -q` passes.

## Scope

In scope: read-only drill-down into beads, feature trees, traces, telemetry attempts and stored audits, live bead change notifications, fixtures, tests and docs.

Out of scope (later specs): live agent transcript tailing via `agent_session_started` (P3), fleet-wide cost dashboards and cross-project queues (P3), planner traces (`_planner/`), any write or control action, authentication.

## Pending Decisions

None.
