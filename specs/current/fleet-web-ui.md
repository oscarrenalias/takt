# fleet-web-ui

## Requirement: Read-only local server
<!-- source: spec-a8f0160d -->
`takt-fleet serve` SHALL serve the web UI and JSON API bound to localhost by default, MUST answer every non-GET request with 405, and MUST refuse a non-local `--host` unless `--allow-remote` is given.

### Scenario: Non-GET request is rejected
- **WHEN** a client sends POST, PUT, PATCH or DELETE to `/api/fleet`, `/api/projects/<name>`, `/api/events` or `/`
- **THEN** the server responds 405 and no project or registry file changes

### Scenario: Remote bind requires opt-in
- **WHEN** `takt-fleet serve --host 0.0.0.0` runs without `--allow-remote`
- **THEN** it exits 2 with a message explaining `--allow-remote`, and no server starts

### Scenario: Missing web extra
- **WHEN** `takt-fleet serve` runs and `starlette` cannot be imported
- **THEN** it exits 1 with a message naming the `fleet-ui` extra

## Requirement: Fleet overview data
<!-- source: spec-a8f0160d -->
`GET /api/fleet` SHALL return one project card per registered project with health, scheduler state, bead counts by status, takt version and version drift, computed from project files without spawning subprocesses.

### Scenario: Mixed fleet
- **WHEN** the registry holds an idle project, a project with an alive active-run marker, a project whose path does not exist and a project without `version.json`
- **THEN** their cards report scheduler `idle`, `running` with its run id, health `missing`, and `version_drift` `unknown` respectively

### Scenario: Older takt version
- **WHEN** a project's `version.json` records a version older than the server's installed version
- **THEN** its card reports `version_drift` `behind`

### Scenario: Tolerant bead reading
- **WHEN** a project has one bead file missing optional fields, one with unknown extra fields and one that is not valid JSON
- **THEN** the first two are counted under their status, `unreadable_beads` is 1, and the response is 200

### Scenario: No subprocesses
- **WHEN** `/api/fleet` and `/api/projects/<name>` are requested with `subprocess.run` and `subprocess.Popen` patched to fail
- **THEN** both respond 200

## Requirement: Fleet activity
<!-- source: spec-a8f0160d -->
`GET /api/events` SHALL return recent events from all projects (or one project with `?project=`) newest first, each with a project name, a one-line summary and a severity.

### Scenario: Merged across projects
- **WHEN** two projects have interleaved events
- **THEN** the response lists events from both newest first, each tagged with its project

### Scenario: Unknown event types are kept
- **WHEN** a project's log contains an event type the server does not know
- **THEN** it is returned with severity `info` and a summary containing the event type

## Requirement: Live updates over SSE
<!-- source: spec-a8f0160d -->
`GET /api/stream` SHALL push `fleet_event` for every line appended to any project's `events.jsonl` after the client connects, `project_updated` when a project card changes, and `fleet_changed` when the registry changes, without duplicating or losing complete lines.

### Scenario: Appended event is pushed
- **WHEN** a line is appended to a project's `events.jsonl` while a client is connected
- **THEN** the client receives one `fleet_event` with that project and event type

### Scenario: Partial and malformed lines
- **WHEN** half a line is written, then the rest with a newline, then a malformed line, then a valid line
- **THEN** the client receives exactly two `fleet_event` messages, for the completed line and the valid line

### Scenario: Bead status change updates the card
- **WHEN** a bead file's status changes from `in_progress` to `blocked`
- **THEN** a `project_updated` message carries a card whose `blocked` count increased by one

### Scenario: Registry change
- **WHEN** a project is added to the registry file while a client is connected
- **THEN** the client receives `fleet_changed` including the new project

### Scenario: Truncated log
- **WHEN** a project's `events.jsonl` is truncated and new lines are written
- **THEN** the new lines are pushed and no earlier line is sent again

## Requirement: Browser screens
<!-- source: spec-a8f0160d -->
The UI SHALL provide overview, activity and project screens that render from the API, update live from the SSE stream without a page reload, and load no resources from outside the server.

### Scenario: Overview renders and updates live
- **WHEN** the overview is open in a browser and a bead in a fixture project changes to `blocked`
- **THEN** that project's card shows the increased blocked count without a reload, and no console errors occur

### Scenario: Activity feed receives new events
- **WHEN** the activity screen is open and an event is appended to a fixture project's log
- **THEN** a new `activity-item` for that project appears at the top without a reload

### Scenario: Navigate to a project
- **WHEN** the user clicks a project card on the overview
- **THEN** the project page opens showing that project's scheduler runs and activity filtered to it

### Scenario: Connection recovers
- **WHEN** the SSE connection drops and the server is reachable again
- **THEN** the connection indicator shows `reconnecting`, then `live`, and the current view refreshes

### Scenario: No external requests
- **WHEN** any screen loads
- **THEN** every network request the page makes goes to the server's own origin

## Requirement: Demo mode
<!-- source: spec-a8f0160d -->
`takt-fleet serve --demo` SHALL serve generated fixture projects with ongoing synthetic activity and MUST NOT read or write the real fleet registry.

### Scenario: Demo leaves the registry alone
- **WHEN** `--demo` starts with `XDG_CONFIG_HOME` pointing at a directory holding a registry file
- **THEN** that registry file is unchanged, and `/api/fleet` lists only demo projects
