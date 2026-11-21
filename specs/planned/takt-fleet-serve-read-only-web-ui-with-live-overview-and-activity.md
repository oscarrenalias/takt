---
name: "takt-fleet serve: read-only web UI with live overview and activity"
id: spec-a8f0160d
description: Read-only local web server and browser UI for takt-fleet with live fleet overview, activity feed and project pages
dependencies: null
priority: null
complexity: null
status: planned
tags:
- fleet
- web-ui
- frontend
scope:
  in: null
  out: null
feature_root_id: B-ecbd9a0b
---
# takt-fleet serve: read-only web UI with live overview and activity

## Objective

Operators run takt across about a dozen local projects and have no single place to see them all live. `takt-fleet summary` and `takt-fleet watch` are one-shot tables and a terminal tail. This spec adds `takt-fleet serve`, a local, read-only web server with a browser UI that shows every registered project (health, scheduler state, bead counts, takt version drift), a live activity feed of events across the fleet, and a page per project. It updates in the browser as work starts, finishes and fails. It is the first slice of the fleet control plane. Bead explorer, traces, audits and live transcripts come in later specs and must be able to build on this server, read layer and test harness.

It is governed by four approved ADRs, which are binding:
- **ADR-1897d241:** read surfaces and boundaries.
- **ADR-31a0427e:** Starlette/uvicorn as an optional extra, SSE, polling, localhost and read-only.
- **ADR-73d258d5:** vendored no-build Preact + htm, server-side view models.
- **ADR-f42387c6:** three-layer testing.

## Problems to Fix

1. **No live, fleet-wide view.** `takt-fleet summary` spawns `uv run takt summary` per project (about one second each) and prints once. `takt-fleet watch` streams raw event lines in a terminal with no per-project state.
2. **No way to see at a glance which projects are busy, stuck or behind on takt version.** Scheduler state, blocked counts and `.takt/version.json` live in separate places per project.
3. **No HTTP surface at all.** Every later UI feature (bead explorer, traces, transcripts) needs a server, a tolerant read layer and a test harness to build on.

## Changes

### 1. Packaging

- New optional extra in `pyproject.toml`: `fleet-ui = ["starlette>=0.37,<1", "uvicorn>=0.29,<1"]`.
- Add `starlette`, `uvicorn`, `httpx` (needed by Starlette's `TestClient`) and `pytest-playwright` to the `dev` dependency group, so `uv run pytest` in any worktree can import the server.
- Register a pytest marker `ui` in `[tool.pytest.ini_options]`.
- Static frontend files ship as package data of `agent_takt_fleet` (add a `[tool.setuptools.package-data]` entry). A built wheel must contain them.

### 2. Server package: `src/agent_takt_fleet/web/`

Suggested modules (developers may adjust names, not responsibilities):

- `readers.py`: tolerant, read-only access to one project's allowed surfaces (ADR-1897d241): bead files (`.takt/beads/*.json`), `events.jsonl`, `.takt/runs/active/*.json`, `.takt/version.json`. Reads raw JSON. Unparseable or unexpected files are skipped and counted, never raised. Unknown fields are preserved. Parsed bead files are cached by path, size and mtime, so an unchanged file is not re-parsed on each poll. Reuse `agent_takt_fleet.scheduler_runs` helpers for scheduler state and runs rather than duplicating them. No subprocesses and no imports of forbidden `agent_takt` modules, directly or indirectly.
- `viewmodels.py`: builds the JSON the screens render (ADR-73d258d5 thin client). Includes status counts, health, scheduler state, version drift, relative ordering and a one-line human summary per event. Reuse or share the event-summarising logic of `formatters.format_watch_event_line` so the CLI and UI agree.
- `stream.py`: the change-detection loop. It polls each project's `events.jsonl` (reading only appended bytes), active markers, bead directory and `version.json`, and the registry file, at `--poll-interval` (default 1.0 s). It publishes SSE messages to connected clients.
- `app.py`: Starlette app factory, e.g. `create_app(projects_provider, *, poll_interval, installed_version)`. Tests inject a fixture registry and projects through it.
- `demo.py`: the fixture builder (see §6), shared by tests and `--demo`.
- `static/`: `index.html`, `app.js` (plus modules as needed), `styles.css`, `vendor/`.

#### Health without subprocesses

`registry.compute_health` runs `takt --version` as a subprocess. The server must not spawn subprocesses, so it computes health from files only: `missing` (path gone), `no-takt` (no `.takt/config.yaml`) or `ok`. It also reports `takt_version` from `.takt/version.json` (null if absent) and `version_drift`: `behind` when that version is older than the server's installed `agent-takt` version (via `importlib.metadata`), `unknown` when absent, otherwise `none`.

### 3. HTTP API (GET only)

| Route | Returns |
|---|---|
| `GET /` | `index.html` |
| `GET /static/...` | static assets |
| `GET /api/fleet` | `{installed_version, projects: [ProjectCard...], generated_at}` |
| `GET /api/projects/{name}` | `ProjectCard` plus `scheduler_runs` (newest first, limit 20, same rows as `takt-fleet scheduler-runs list`) |
| `GET /api/events?project=<name>&limit=<n>` | recent events across the fleet (or one project), newest first, default limit 200, max 1000. Each is `{project, timestamp, event_type, payload, summary, severity}` with severity `info`, `success`, `warning` or `error` |
| `GET /api/stream` | SSE stream (below) |

`ProjectCard`: `name`, `path`, `tags`, `health`, `takt_version`, `version_drift`, `scheduler` (`running`, `stale` or `idle`, plus `run_id` and `started_at` when running), `bead_counts` (by status, always containing all six statuses), `unreadable_beads`, `last_event_at`.

Unknown project name: 404 JSON `{error}`. Any non-GET method on any route: 405. No route calls mutating `TaktAdapter` methods.

### 4. SSE stream

`GET /api/stream` (`text/event-stream`) sends:
- `event: fleet_event` with the same object shape as `/api/events` items, for each new line appended to any project's `events.jsonl` after the client connected.
- `event: project_updated` with a full `ProjectCard` whenever that card's content changes (bead counts, scheduler state, health, version).
- `event: fleet_changed` with the full `/api/fleet` body when the registry file changes (project added or removed).
- A comment heartbeat (`: ping`) every 15 s.

Edge cases the stream must handle without crashing or duplicating events:
- A partial last line (no trailing newline yet) is held until complete.
- A malformed JSON line is skipped.
- A file that shrinks or is replaced (rotation or truncation) is re-read from the start without re-sending already-sent lines older than the client's connection.
- A project whose directory disappears produces a `project_updated` with health `missing`.

Client disconnects are cleaned up.

### 5. Command: `takt-fleet serve`

```
takt-fleet serve [--host 127.0.0.1] [--port 8765] [--allow-remote] [--poll-interval SECONDS] [--demo] [--open]
```

- Default bind is `127.0.0.1:8765`. `--port 0` picks a free port. The actual URL is printed on start.
- A `--host` other than `127.0.0.1`, `::1` or `localhost` requires `--allow-remote`; without it the command exits 2 with an explanation. With it, a warning is printed that traces and transcripts may become reachable without authentication.
- If `starlette` or `uvicorn` cannot be imported, the command exits 1 with a message naming the `fleet-ui` extra (for example `uv sync --extra fleet-ui` or `pip install 'agent-takt[fleet-ui]'`). Other fleet commands are unaffected.
- `--demo` builds demo projects with the fixture builder in a temporary directory, serves them instead of the real registry, prints that it is in demo mode, never touches the real registry, and cleans up on exit. Demo mode keeps appending synthetic events (beads starting, completing, one blocking, a run finishing) every few seconds so live updates can be seen.
- `--open` opens the browser.
- Runs in the foreground; Ctrl-C exits 0.

### 6. Fixture builder (`agent_takt_fleet/web/demo.py`)

A function that writes N synthetic projects (each with `.takt/config.yaml`, beads in a mix of statuses, a feature tree, an `events.jsonl` with run lifecycle and bead events, optional active-run marker, `version.json`) plus a registry file, under a given directory. It also provides helpers to append events and change bead status, used by tests and demo mode. It includes variants for an older bead shape (missing optional fields), a newer bead shape (extra unknown fields), an unparseable bead file, a project without `version.json` and a missing project path. Timestamps are derived from an injectable clock.

### 7. Frontend (ADR-73d258d5)

- Vendored, pinned, unmodified third-party files only. The suggested single file is htm's standalone Preact build (`htm/preact/standalone.module.js` from htm 3.1.1, which bundles Preact and hooks), stored as `static/vendor/htm-preact-standalone-3.1.1.mjs` with a `static/vendor/VERSIONS.md` noting source URL and version. No CDN references anywhere in served HTML or JS. No build step, no `package.json`.
- Hash routing: `#/` (overview), `#/activity`, `#/projects/<name>`.
- **Overview:** one card per project showing name, tags, health, scheduler state (running is visually prominent, with how long it has been running), bead counts by status (blocked emphasised when non-zero), takt version with a drift badge, and last activity as relative time. Cards update live from `project_updated`. Clicking a card opens the project page.
- **Activity:** live feed, newest first, filterable by project, colour-coded by severity, showing project, relative time and summary. New events are prepended without a reload. Shows at most the most recent 1000 in memory.
- **Project page:** the project card in full, recent scheduler runs (run id, started, duration, exit reason or running), and the activity feed filtered to that project.
- A header with navigation and a connection indicator (`live`, `reconnecting`). The client resubscribes automatically after the stream drops and refetches the current view's data on reconnect.
- The look must be clean and readable: system font stack, light and dark themes via `prefers-color-scheme`, consistent status colours, usable from 360 px wide upwards with no horizontal page scroll.
- All key regions and interactive elements carry stable `data-testid` attributes (e.g. `project-card`, `project-card-<name>`, `bead-count-blocked`, `scheduler-state`, `activity-item`, `activity-filter`, `connection-state`, `scheduler-runs`).

### 8. Tests (ADR-f42387c6)

- **Layer 1:** `tests/fleet/web/` covers readers, view models and every endpoint, using `TestClient` against the fixture builder, plus SSE tests that append to fixture files and read the stream. These do not need a browser and must not be marked `ui`.
- **Layer 2:** `pytest-playwright` tests marked `ui`, starting the real server on a random port against fixture data. They skip with a clear reason when the Chromium browser for Playwright is not installed. They fail on any console error or uncaught page exception, and use Playwright's clock control and no external network.
- **CI:** add a job to `.github/workflows/` that installs Chromium (`uv run playwright install --with-deps chromium`) and runs `uv run pytest tests/ -m ui`.
- **Layer 3:** the tester saves screenshots of overview, activity and project page, from `--demo` data, light theme, at 1280×800, to `docs/assets/fleet-ui/`. The review bead inspects them. Agents may run `uv run playwright install chromium` to get a browser.

### 9. Documentation

- `docs/fleet.md`: a `takt-fleet serve` section covering install of the extra, flags, the localhost/remote rule, demo mode, the screens with the screenshots, the API routes and SSE event names, and how to run the `ui` tests locally.
- `CLAUDE.md`: add `web/` to the layout and a one-liner under Fleet Manager.

## Requirements

### ADDED: fleet-web-ui / Read-only local server
`takt-fleet serve` SHALL serve the web UI and JSON API bound to localhost by default, MUST answer every non-GET request with 405, and MUST refuse a non-local `--host` unless `--allow-remote` is given.

#### Scenario: Non-GET request is rejected
- **WHEN** a client sends POST, PUT, PATCH or DELETE to `/api/fleet`, `/api/projects/<name>`, `/api/events` or `/`
- **THEN** the server responds 405 and no project or registry file changes

#### Scenario: Remote bind requires opt-in
- **WHEN** `takt-fleet serve --host 0.0.0.0` runs without `--allow-remote`
- **THEN** it exits 2 with a message explaining `--allow-remote`, and no server starts

#### Scenario: Missing web extra
- **WHEN** `takt-fleet serve` runs and `starlette` cannot be imported
- **THEN** it exits 1 with a message naming the `fleet-ui` extra

### ADDED: fleet-web-ui / Fleet overview data
`GET /api/fleet` SHALL return one project card per registered project with health, scheduler state, bead counts by status, takt version and version drift, computed from project files without spawning subprocesses.

#### Scenario: Mixed fleet
- **WHEN** the registry holds an idle project, a project with an alive active-run marker, a project whose path does not exist and a project without `version.json`
- **THEN** their cards report scheduler `idle`, `running` with its run id, health `missing`, and `version_drift` `unknown` respectively

#### Scenario: Older takt version
- **WHEN** a project's `version.json` records a version older than the server's installed version
- **THEN** its card reports `version_drift` `behind`

#### Scenario: Tolerant bead reading
- **WHEN** a project has one bead file missing optional fields, one with unknown extra fields and one that is not valid JSON
- **THEN** the first two are counted under their status, `unreadable_beads` is 1, and the response is 200

#### Scenario: No subprocesses
- **WHEN** `/api/fleet` and `/api/projects/<name>` are requested with `subprocess.run` and `subprocess.Popen` patched to fail
- **THEN** both respond 200

### ADDED: fleet-web-ui / Fleet activity
`GET /api/events` SHALL return recent events from all projects (or one project with `?project=`) newest first, each with a project name, a one-line summary and a severity.

#### Scenario: Merged across projects
- **WHEN** two projects have interleaved events
- **THEN** the response lists events from both newest first, each tagged with its project

#### Scenario: Unknown event types are kept
- **WHEN** a project's log contains an event type the server does not know
- **THEN** it is returned with severity `info` and a summary containing the event type

### ADDED: fleet-web-ui / Live updates over SSE
`GET /api/stream` SHALL push `fleet_event` for every line appended to any project's `events.jsonl` after the client connects, `project_updated` when a project card changes, and `fleet_changed` when the registry changes, without duplicating or losing complete lines.

#### Scenario: Appended event is pushed
- **WHEN** a line is appended to a project's `events.jsonl` while a client is connected
- **THEN** the client receives one `fleet_event` with that project and event type

#### Scenario: Partial and malformed lines
- **WHEN** half a line is written, then the rest with a newline, then a malformed line, then a valid line
- **THEN** the client receives exactly two `fleet_event` messages, for the completed line and the valid line

#### Scenario: Bead status change updates the card
- **WHEN** a bead file's status changes from `in_progress` to `blocked`
- **THEN** a `project_updated` message carries a card whose `blocked` count increased by one

#### Scenario: Registry change
- **WHEN** a project is added to the registry file while a client is connected
- **THEN** the client receives `fleet_changed` including the new project

#### Scenario: Truncated log
- **WHEN** a project's `events.jsonl` is truncated and new lines are written
- **THEN** the new lines are pushed and no earlier line is sent again

### ADDED: fleet-web-ui / Browser screens
The UI SHALL provide overview, activity and project screens that render from the API, update live from the SSE stream without a page reload, and load no resources from outside the server.

#### Scenario: Overview renders and updates live
- **WHEN** the overview is open in a browser and a bead in a fixture project changes to `blocked`
- **THEN** that project's card shows the increased blocked count without a reload, and no console errors occur

#### Scenario: Activity feed receives new events
- **WHEN** the activity screen is open and an event is appended to a fixture project's log
- **THEN** a new `activity-item` for that project appears at the top without a reload

#### Scenario: Navigate to a project
- **WHEN** the user clicks a project card on the overview
- **THEN** the project page opens showing that project's scheduler runs and activity filtered to it

#### Scenario: Connection recovers
- **WHEN** the SSE connection drops and the server is reachable again
- **THEN** the connection indicator shows `reconnecting`, then `live`, and the current view refreshes

#### Scenario: No external requests
- **WHEN** any screen loads
- **THEN** every network request the page makes goes to the server's own origin

### ADDED: fleet-web-ui / Demo mode
`takt-fleet serve --demo` SHALL serve generated fixture projects with ongoing synthetic activity and MUST NOT read or write the real fleet registry.

#### Scenario: Demo leaves the registry alone
- **WHEN** `--demo` starts with `XDG_CONFIG_HOME` pointing at a directory holding a registry file
- **THEN** that registry file is unchanged, and `/api/fleet` lists only demo projects

## Files to Modify

| File | Change |
|---|---|
| `pyproject.toml` | `fleet-ui` extra, dev deps, `ui` marker, package data for `agent_takt_fleet` static files |
| `src/agent_takt_fleet/web/` (new) | readers, view models, stream, app, demo, static assets |
| `src/agent_takt_fleet/cli/parser.py` | `serve` subcommand |
| `src/agent_takt_fleet/cli/commands/serve.py` (new) | command handler |
| `src/agent_takt_fleet/formatters.py` | share event summary logic with the web view models if needed |
| `tests/fleet/web/` (new) | layer 1 and layer 2 tests |
| `.github/workflows/` | `ui` test job |
| `docs/fleet.md`, `docs/assets/fleet-ui/` | serve docs and screenshots |
| `CLAUDE.md` | layout and Fleet Manager mention |

## Acceptance Criteria

- `takt-fleet serve` starts a server on `127.0.0.1:8765` by default, prints its URL, and stops cleanly on Ctrl-C.
- All API routes in §3 exist with the documented shapes; every non-GET request is 405; no request spawns a subprocess.
- The SSE stream delivers `fleet_event`, `project_updated`, `fleet_changed` and heartbeats with the edge cases in §4 handled.
- Overview, activity and project screens work as described in §7, update live, and load nothing from outside the server.
- The frontend has no build step; vendored files are pinned and recorded in `static/vendor/VERSIONS.md`.
- The fixture builder supports the variants in §6 and is used by both tests and `--demo`.
- Layer 1 tests run in the default test command; `ui` tests skip cleanly without Chromium and pass with it; a CI job runs them.
- Fleet code passes the boundary tests, including the transitive check if spec-a2cb3aed has merged.
- Screenshots of the three screens are in `docs/assets/fleet-ui/` and referenced from `docs/fleet.md`.
- `uv run pytest tests/ -n auto -q` passes.

## Scope

In scope: server, read layer, SSE, overview, activity and project screens, demo mode, fixture builder, test harness, CI job, docs.

Out of scope (later specs): bead explorer and bead detail, traces, audits, telemetry and cost views, live agent transcripts (which depend on `agent_session_started` from spec-a2cb3aed), authentication, any write or control action.

## Pending Decisions

None.
