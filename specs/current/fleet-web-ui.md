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

## Requirement: Bead explorer data
<!-- source: spec-be2ec1ba -->
The server SHALL list a project's beads with filters and return any bead's full detail, including criteria accounting, assumptions, history, telemetry rows and the raw bead with unknown fields, and MUST locate beads only among those read from the project's bead directory.

### Scenario: Filtered bead list
- **WHEN** `/api/projects/<name>/beads?status=blocked&agent=developer` is requested for a fixture project with blocked and done developer and tester beads
- **THEN** only blocked developer beads are returned, sorted by creation time, with `total` matching

### Scenario: Bead detail joins criteria and status
- **WHEN** the detail of a bead with three acceptance criteria and `criteria_status` entries for indexes 0 and 2 is requested
- **THEN** the response lists three criteria with statuses from entries 0 and 2 and `unreported` for index 1, and `raw` contains the bead's unknown extra fields

### Scenario: Governance badges
- **WHEN** a bead has a structural assumption, an unmet criterion and the `discovered` label
- **THEN** its row's `badges` include `structural_assumptions`, `unmet_criteria` and `discovered`

### Scenario: Unsafe bead id
- **WHEN** a bead path parameter is `..%2F..%2Fetc` or names no bead in the directory
- **THEN** the server responds 404 and opens no file outside the project's documented `.takt/` directories

### Scenario: Cost fallback
- **WHEN** a bead cannot be converted to an `agent_takt` `Bead`, but its `telemetry_history` carries cost values
- **THEN** its detail reports the summed cost and the response is 200

## Requirement: Feature trees
<!-- source: spec-be2ec1ba -->
The server SHALL list each project's feature trees with status, criteria, assumption, cost, merge and audit rollups, and return a feature's beads as a nested tree.

### Scenario: Tree structure
- **WHEN** a feature detail is requested for a root with children and a bead whose parent is missing
- **THEN** children are nested under their parents ordered by creation, and the orphan appears under the root

### Scenario: In-flight features first
- **WHEN** a project has one feature with a bead in progress and an older idle feature with more recent activity
- **THEN** the in-progress feature is listed first

## Requirement: Traces and attempts
<!-- source: spec-be2ec1ba -->
The server SHALL return a bead's trace records with a computed summary and paged, filterable events, and each telemetry attempt with prompt and response capped at 256 KiB.

### Scenario: Paged and filtered trace events
- **WHEN** a trace with 450 events, 3 of them failed tool calls, is requested with `limit=200` and then with `errors_only=true`
- **THEN** the first response has 200 events and `total` 450, and the second has exactly the 3 failed tool calls, and both carry the same summary with error count 3

### Scenario: Large prompt is capped
- **WHEN** a telemetry attempt whose prompt exceeds 256 KiB is requested
- **THEN** the returned prompt is at most 256 KiB and `truncated` is true for the prompt and false for a short response

### Scenario: Missing trace record
- **WHEN** a trace record with status `missing` and no events file is requested
- **THEN** the server responds 200 with its meta, an empty event list and `total` 0

## Requirement: Stored audits
<!-- source: spec-be2ec1ba -->
The server SHALL list and return a feature's stored audit snapshots exactly as written, and MUST NOT build audits itself.

### Scenario: Audit returned as stored
- **WHEN** an audit snapshot containing keys unknown to the server is requested by its listed id
- **THEN** the response contains the stored report with those keys intact

### Scenario: Unknown audit id
- **WHEN** an audit id that is not among the listed snapshot stems is requested
- **THEN** the server responds 404

## Requirement: Bead change notifications
<!-- source: spec-be2ec1ba -->
The SSE stream SHALL send a `beads_changed` event naming the project and every bead whose file changed, appeared or disappeared since the previous poll, even when the project's status counts do not change.

### Scenario: Handoff change without status change
- **WHEN** a done bead's file is rewritten with a new handoff summary while a client is connected
- **THEN** the client receives `beads_changed` for that project listing that bead id, and no `project_updated` for it

## Requirement: Drill-down screens
<!-- source: spec-be2ec1ba -->
The UI SHALL provide feature list, feature tree, bead list, bead detail, trace, attempt and audit screens reachable by navigation from the project page, with working breadcrumbs, and an open bead page MUST update without a reload when its bead changes.

### Scenario: Drill down from project to trace
- **WHEN** the user opens a project, then a feature, then a bead in the tree, then one of its traces
- **THEN** each screen renders its key region, the breadcrumb shows the path, and no console errors occur

### Scenario: Live bead detail
- **WHEN** a bead detail page is open and the bead's history gains an entry in its file
- **THEN** the new history item appears without a reload

### Scenario: Bead search
- **WHEN** the user types part of a bead title into the bead list search box
- **THEN** only beads whose id or title contain that text are listed

### Scenario: Activity links to beads
- **WHEN** the activity feed shows an event whose payload has a `bead_id`
- **THEN** clicking it opens that bead's detail page

## Requirement: Paged activity API
<!-- source: spec-930dd898 -->
`GET /api/events` SHALL return the newest events first in pages (default 25) with an opaque `next_cursor`, and following cursors MUST return every older event exactly once.

### Scenario: Paging without gaps or duplicates
- **WHEN** a fixture fleet has 70 events across two projects, including several with identical timestamps, and the client follows `next_cursor` from the first page until it is null
- **THEN** it receives all 70 events exactly once, newest first

### Scenario: Malformed cursor
- **WHEN** `/api/events?cursor=not-a-cursor` is requested
- **THEN** the server responds 400 with a JSON error

### Scenario: First page reads only the end of the log
- **WHEN** a project's `events.jsonl` is several megabytes and the first page is requested
- **THEN** the response contains the newest 25 events, and the reader did not read the whole file

## Requirement: Paged bead list
<!-- source: spec-930dd898 -->
`GET /api/projects/{name}/beads` SHALL return beads newest first in pages (default 25) with `total` and `next_cursor`, applying filters before paging.

### Scenario: Newest first with filters
- **WHEN** a project has 60 beads, 30 of them developer beads, and `/beads?agent=developer` is requested
- **THEN** the first page has 25 developer beads ordered by creation time descending, `total` is 30, and the next page has the remaining 5

## Requirement: Scheduler run view
<!-- source: spec-930dd898 -->
The server SHALL list a project's scheduler runs and return a run's cycles, the beads it ran with outcomes and costs, and its total cost, built only from `events.jsonl` records carrying that `run_id`.

### Scenario: Run detail
- **WHEN** a finished run has two cycles, three beads (one blocked) with `cost_usd` 0.5, 0.25 and null, and one corrective
- **THEN** the run detail lists both cycles with their counts, the three beads with outcomes, the corrective, and `total_cost_usd` 0.75

### Scenario: Unknown run
- **WHEN** a `run_id` that is malformed or absent from the project's log is requested
- **THEN** the server responds 404

## Requirement: Project page leads with recent work
<!-- source: spec-930dd898 -->
The project page SHALL show the most recently active features and the newest beads first, with links to their pages and to the full lists, and SHALL show scheduler runs and activity only as a compact last-run line and a short recent-activity strip.

### Scenario: Project page content
- **WHEN** the project page of a fixture project with 12 features and 40 beads is opened
- **THEN** it shows 8 feature rows and 10 bead rows in that order, "View all" links to the features and beads screens, a "Last run" line linking to the run page, at most 10 activity items, and no scheduler runs table

### Scenario: Last run opens the run page
- **WHEN** the user clicks the "Last run" line
- **THEN** the run page opens showing that run's cycles and beads

## Requirement: Incremental activity feed
<!-- source: spec-930dd898 -->
The activity screen SHALL load the newest 25 events, add live events from the stream without refetching the event list, and load older events on demand.

### Scenario: Live prepend without refetch
- **WHEN** the activity screen is open and an event is appended to a fixture project's log
- **THEN** a new item appears at the top, and no further request to `/api/events` is made

### Scenario: Load older
- **WHEN** the user clicks "Load older" on the activity screen
- **THEN** 25 older events are appended below the existing ones without duplicates

## Requirement: Readable bead detail
<!-- source: spec-930dd898 -->
The bead detail page SHALL render at a base font size of 1.1 times the body size, give each section a distinct accent colour in light and dark themes, and render each history entry's timestamp, event, agent and summary as separately spaced cells.

### Scenario: History cells are separated
- **WHEN** a bead detail page with history entries is open
- **THEN** each entry's timestamp, event, agent and summary are separate elements, the visible text of the entry has whitespace or a gap between each of them, and no text like `09:50:11created` appears

### Scenario: Font size and section colours
- **WHEN** a bead detail page is open
- **THEN** its computed base font size is 1.1 times the body font size, and the criteria, handoff, assumptions and history sections have different accent colours

### Scenario: Mobile layout
- **WHEN** the project, run and bead detail pages are opened at 360 px wide
- **THEN** none of them scrolls horizontally

## Requirement: Live session detection
<!-- source: spec-e2bada88 -->
The server SHALL treat a session as live when its `agent_session_started` event has no later `trace_captured` for the same session and no later completion, block or failure event for its bead, and it started less than 6 hours ago.

### Scenario: Started and not captured
- **WHEN** a fixture project's log has `agent_session_started` for session S of bead B and nothing later for S or B
- **THEN** `/api/live` lists S for B with status `live` (transcript present) or `waiting` (transcript absent)

### Scenario: Captured session is not live
- **WHEN** a `trace_captured` event for session S follows its `agent_session_started`
- **THEN** S is not listed in `/api/live`

### Scenario: Old start is ignored
- **WHEN** an `agent_session_started` event is more than 6 hours old with no later events for it
- **THEN** it is not listed as live

## Requirement: Live transcript following
<!-- source: spec-e2bada88 -->
The server SHALL read a live session's transcript only from the Claude config dir's `projects/` tree, reject session ids that are not UUIDs, and return normalised events incrementally by index, including updates to earlier tool calls.

### Scenario: Incremental events
- **WHEN** a client has received events up to index 9 and the transcript gains a tool result for the tool call at index 8 and two new assistant messages
- **THEN** `?after=10` returns the two new events, and the stream sends `live_session_updated`, and a refetch with `after=8` shows index 8 with its result and duration

### Scenario: Unsafe session id
- **WHEN** a live endpoint is called with a session id that is not a UUID or contains path separators
- **THEN** the server responds 404 and reads no file

### Scenario: Partial line held
- **WHEN** the transcript ends with an incomplete JSON line
- **THEN** that line is not normalised until it is completed

## Requirement: Live session screens
<!-- source: spec-e2bada88 -->
The UI SHALL show a fleet-wide "Live now" strip on the overview and a live session page that appends new events without reloading and links to the captured trace after the session ends.

### Scenario: Watch a session live
- **WHEN** the live session page is open and the fixture transcript gains a tool call
- **THEN** a new timeline entry appears without a page reload, and no console errors occur

### Scenario: Session ends
- **WHEN** a `trace_captured` event for the open session is appended
- **THEN** the page shows an ended banner with a link to the captured trace

## Requirement: Attention page
<!-- source: spec-e2bada88 -->
The server SHALL report, across all registered projects, blocked beads, open discovered issues, unmet criteria and structural assumptions in unmerged feature trees, and stale scheduler runs, and the UI SHALL show them on an attention page with links.

### Scenario: Attention sections
- **WHEN** the fixture fleet has two blocked beads in different projects, one open discovered bead, one unmet criterion in an unmerged tree and one in a merged tree
- **THEN** `/api/attention` lists both blocked beads, the discovered bead and only the unmet criterion from the unmerged tree, each with its project

### Scenario: Navigate from attention
- **WHEN** the user clicks a blocked bead on the attention page
- **THEN** that bead's detail page opens

## Requirement: Cost overview
<!-- source: spec-e2bada88 -->
The server SHALL aggregate `cost_usd` from bead completion, block and failure events per project, per UTC day and per agent type within a selectable window, report how many events had no cost, and read each log only back to the start of the window.

### Scenario: Window aggregation
- **WHEN** two projects have priced events on several days inside and outside a 7-day window, plus events with null cost
- **THEN** `/api/cost?days=7` sums only events inside the window per project, day and agent type, and reports the null-cost event count

### Scenario: Cost chart renders
- **WHEN** the cost page is opened on the fixture fleet
- **THEN** an SVG chart with one bar group per day and a legend per project is shown, and hovering a bar shows its day, project and amount
