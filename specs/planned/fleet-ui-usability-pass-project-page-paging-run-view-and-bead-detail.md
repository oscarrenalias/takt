---
name: "Fleet UI usability pass: project page, paging, run view and bead detail"
id: spec-930dd898
description: "Operator feedback on the fleet UI: newest-first paging for beads and activity, clickable scheduler runs with a run page, a project page that leads with recent work, and a readable bead detail page"
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
feature_root_id: B-e48ae6f7
---
# Fleet UI usability pass: project page, paging, run view and bead detail

## Objective

The operator has used `takt-fleet serve` after P1 (spec-a8f0160d) and P2 (spec-be2ec1ba) and found seven usability problems:
- the project page leads with low-value content;
- bead and activity lists put the newest items last and load far more data than is shown;
- scheduler runs are a dead list;
- the bead detail page is visually flat and its history entries run together.

This spec fixes those without adding new data sources. Everything is still read from the files in `docs/project-files.md` and `events.jsonl`, within ADR-1897d241, ADR-31a0427e, ADR-73d258d5 and ADR-f42387c6.

## Problems to Fix

1. **Bead list is oldest first.** `build_bead_list` (`web/viewmodels.py`) sorts by creation time ascending, so the most recent beads are at the bottom of a list of hundreds.
2. **Scheduler runs are not clickable.** The project page (`static/js/project.js`) shows a table of runs from `read_scheduler_runs` with nothing to open, duplicating `takt-fleet scheduler-runs list`.
3. **Activity over-fetches.** `static/js/activity.js` and `project.js` request `MAX_EVENTS = 1000` (`static/js/feed.js`). `app.js` refetches the whole Activity view after every `fleet_event`, although the SSE message already carries the event.
4. **The project page leads with the wrong things.** It shows the project card, a scheduler runs table and a long activity feed. The operator wants the latest features and beads first.
5. **Bead detail is visually flat.** All sections share the same grey/white styling. The base text there computes to about 0.9em; the operator wants 1.1em.
6. **History entries run together.** In the bead detail history list, the timestamp, event name, agent and summary render without spacing (for example `2026-09-23T09:50:11created scheduler`).
7. **Bead list loads everything.** `/api/projects/{name}/beads` defaults to 500 rows, and the client renders them all.

## Changes

### 1. Cursor paging for events and beads (server)

- **`GET /api/events`**
  - Default `limit` 25 (max 200).
  - A response carries `next_cursor`: an opaque string, or null when there is nothing older.
  - Passing `cursor=<next_cursor>` returns the next page of strictly older events, with no duplicates and no gaps across pages, including events that share a timestamp.
  - Merging across projects stays newest first.
  - Older events are read backwards from the end of each `events.jsonl`. The first page must not read whole files.
  - `?project=` keeps working.
- **`GET /api/projects/{name}/beads`**
  - Sorted **newest first** by creation time (first `execution_history` timestamp), ties broken by bead ID descending.
  - Default `limit` 25 (max 200), with `cursor`/`next_cursor` paging over the filtered, sorted result.
  - `total` keeps reporting the full match count.
  - Filters (`status`, `agent`, `label`, `feature_root`, `q`) apply before paging.
  - `order=oldest` gives the previous ascending order.
- Cursors are validated. A malformed cursor returns 400 JSON `{error}`, never a 500.

### 2. Scheduler run view

- **`GET /api/projects/{name}/runs`:** the project's scheduler runs, newest first, paged as above (default 25). Each row has `run_id`, status (`running`, `stale`, or finished with `exit_reason`), `started_at`, `finished_at`, duration, `cycles`, `started_count`, `max_workers`, `runner` and `feature_root_id`.
- **`GET /api/projects/{name}/runs/{run_id}`:** run detail built from `events.jsonl` records whose payload `run_id` matches:
  - the run row;
  - `cycles`: one entry per `scheduler_cycle_started`/`_completed` pair, with index, start and end times and the started/completed/blocked/deferred counts;
  - `beads`: one entry per bead with a `bead_started`, `bead_completed`, `bead_blocked` or `bead_failed` event in the run. Each entry has the bead ID, title, agent type, final outcome in this run, duration and `cost_usd` from those events, and the cycle it started in;
  - `correctives_created` (beads named in `created_bead_ids`, or corrective beads started in the run);
  - `total_cost_usd`: sum of the non-null `cost_usd` values, or null if none.

  The `run_id` must match `SR-` followed by 8 hex characters and must exist in the project's log; otherwise 404.
- **Run page** at `#/projects/<name>/runs/<run_id>`:
  - a header with status, duration, exit reason, workers and runner;
  - a cycle timeline;
  - a beads table with outcome colours and links to bead detail;
  - total cost.

  A running run updates live from `fleet_event` messages for that project; it applies the payload directly rather than refetching on every event, and only refetches the run detail at the end of each cycle.
- **Runs list page** at `#/projects/<name>/runs` with clickable rows and "Load older".

### 3. Project page redesign (`static/js/project.js`)

Order of content:
1. **Header:** the project card summary (health, scheduler state, counts, version). When a run is in progress, a prominent "running since …" link to its run page.
2. **Recent features:** the 8 most recently active feature trees, using the feature list rows from P2 (status bar, in-flight marker, criteria and assumption counts, last merge), each linking to the feature tree. A "View all features" link.
3. **Recent beads:** the 10 newest beads (status, agent, title, badges, relative time), each linking to bead detail. A "View all beads" link.
4. **Compact footer:**
   - a single "Last run" line (status or exit reason, duration, finished "x ago") linking to the run page, plus "All runs";
   - a "Recent activity" strip of the 10 newest events with "View all activity".

   Both are visually secondary.

The scheduler runs table and the long activity feed are removed from this page.

### 4. Activity screen (`static/js/activity.js`, `feed.js`, `app.js`)

- **Initial load:** the newest 25 events (or 25 for the selected project).
- **Live events:** a new `fleet_event` is prepended from its SSE payload without refetching. Events not matching the active project filter are ignored.
- **"Load older":** appends the next page using `next_cursor`. The button is hidden when `next_cursor` is null.
- **Memory cap:** the client keeps at most 500 events in memory and drops the oldest beyond that. Once it does, "Load older" fetches from the oldest kept event.
- **Bead links:** events with a `bead_id` keep linking to bead detail (from P2).
- **Refetch rule:** `app.js` no longer refetches the Activity view on `fleet_event`. A refetch happens only on reconnect.

### 5. Bead list screen (`static/js/beads.js`)

- Shows the newest 25 matching beads first, with "Load older" paging.
- Shows the total count ("25 of 312").
- Changing a filter or the search resets paging.
- `beads_changed` still triggers a debounced refresh of the loaded pages. Beads newly created while the screen is open appear at the top.

### 6. Bead detail styling (`static/css/beaddetail.css`, `static/js/beaddetail.js`)

- **Base font:** the bead detail page's base text size is `1.1em` relative to the app's body size (currently `15px` in `styles.css`). Monospace blocks scale with it.
- **Distinct sections:** each section gets its own accent colour, used for a left border and a tinted header background. Colours come from CSS variables with light and dark values, at AA contrast in both themes. Accents per section:

  | Section | Accent |
  |---|---|
  | Summary | neutral / brand |
  | Description | grey-blue |
  | Acceptance criteria | green |
  | Handoff | blue |
  | Assumptions | amber |
  | Files | teal |
  | Dependencies & children | purple |
  | History | slate |
  | Cost & attempts | gold |
  | Traces | indigo |
  | Discovered issues | orange |
  | Raw JSON | muted |

- **Status colours:**
  - criteria items carry status colours: met green, unmet red, descoped grey, unreported amber;
  - structural assumptions are amber-highlighted with a "structural" pill;
  - the verdict and block reason get coloured badges in the summary.
- **Collapsing:** section headers show item counts. Collapsed sections still show their header colour.

### 7. History layout (`static/js/beaddetail.js`)

- Each history entry renders as a row with separate, aligned cells: timestamp (`YYYY-MM-DD HH:MM:SS`, local time, full ISO in a tooltip), event (as a coloured pill by event family: lifecycle, failure, verdict, scheduler), agent type, and summary.
- No two values may touch without visible spacing.
- On narrow screens the row wraps to two lines (timestamp and event, then agent and summary), still without horizontal page scroll at 360 px.
- `details`, when non-empty, is expandable per entry.

### 8. Tests and docs

- **Layer 1:**
  - API tests for cursor paging (no duplicates or gaps across pages, including equal timestamps; malformed cursor gives 400), newest-first bead order with filters, runs list and run detail (cycles, beads, cost sum, unknown `run_id` gives 404);
  - a test that the first events page does not read a whole large log (for example, a log of many megabytes where the test asserts bytes read stay bounded, or that the reader seeks from the end).
- **Layer 2 (Playwright `ui`):**
  - project page sections and links;
  - activity initial count 25, live prepend without a network refetch of `/api/events`, and "Load older";
  - bead list newest first with "Load older";
  - run page from the "Last run" link;
  - history rows have visible separation between their cells (each cell in its own element, with non-zero gap or padding);
  - bead detail computed base font size is 1.1 × the body font size;
  - sections have distinct accent colours;
  - no horizontal scroll at 360 px on the project, run and bead detail pages.

  Use the module-scoped browser fixture pattern.
- **Layer 3:** refresh the screenshots in `docs/assets/fleet-ui/` (project page, activity, bead detail light and dark, run page).
- **Fixtures (`web/demo.py`):** at least two finished runs and one running run with cycles, bead events with `cost_usd` and a corrective, plus enough events and beads to need several pages.
- **Docs:** update `docs/fleet.md` for the new routes, paging parameters and screens.

## Requirements

### ADDED: fleet-web-ui / Paged activity API
`GET /api/events` SHALL return the newest events first in pages (default 25) with an opaque `next_cursor`, and following cursors MUST return every older event exactly once.

#### Scenario: Paging without gaps or duplicates
- **WHEN** a fixture fleet has 70 events across two projects, including several with identical timestamps, and the client follows `next_cursor` from the first page until it is null
- **THEN** it receives all 70 events exactly once, newest first

#### Scenario: Malformed cursor
- **WHEN** `/api/events?cursor=not-a-cursor` is requested
- **THEN** the server responds 400 with a JSON error

#### Scenario: First page reads only the end of the log
- **WHEN** a project's `events.jsonl` is several megabytes and the first page is requested
- **THEN** the response contains the newest 25 events, and the reader did not read the whole file

### ADDED: fleet-web-ui / Paged bead list
`GET /api/projects/{name}/beads` SHALL return beads newest first in pages (default 25) with `total` and `next_cursor`, applying filters before paging.

#### Scenario: Newest first with filters
- **WHEN** a project has 60 beads, 30 of them developer beads, and `/beads?agent=developer` is requested
- **THEN** the first page has 25 developer beads ordered by creation time descending, `total` is 30, and the next page has the remaining 5

### ADDED: fleet-web-ui / Scheduler run view
The server SHALL list a project's scheduler runs and return a run's cycles, the beads it ran with outcomes and costs, and its total cost, built only from `events.jsonl` records carrying that `run_id`.

#### Scenario: Run detail
- **WHEN** a finished run has two cycles, three beads (one blocked) with `cost_usd` 0.5, 0.25 and null, and one corrective
- **THEN** the run detail lists both cycles with their counts, the three beads with outcomes, the corrective, and `total_cost_usd` 0.75

#### Scenario: Unknown run
- **WHEN** a `run_id` that is malformed or absent from the project's log is requested
- **THEN** the server responds 404

### ADDED: fleet-web-ui / Project page leads with recent work
The project page SHALL show the most recently active features and the newest beads first, with links to their pages and to the full lists, and SHALL show scheduler runs and activity only as a compact last-run line and a short recent-activity strip.

#### Scenario: Project page content
- **WHEN** the project page of a fixture project with 12 features and 40 beads is opened
- **THEN** it shows 8 feature rows and 10 bead rows in that order, "View all" links to the features and beads screens, a "Last run" line linking to the run page, at most 10 activity items, and no scheduler runs table

#### Scenario: Last run opens the run page
- **WHEN** the user clicks the "Last run" line
- **THEN** the run page opens showing that run's cycles and beads

### ADDED: fleet-web-ui / Incremental activity feed
The activity screen SHALL load the newest 25 events, add live events from the stream without refetching the event list, and load older events on demand.

#### Scenario: Live prepend without refetch
- **WHEN** the activity screen is open and an event is appended to a fixture project's log
- **THEN** a new item appears at the top, and no further request to `/api/events` is made

#### Scenario: Load older
- **WHEN** the user clicks "Load older" on the activity screen
- **THEN** 25 older events are appended below the existing ones without duplicates

### ADDED: fleet-web-ui / Readable bead detail
The bead detail page SHALL render at a base font size of 1.1 times the body size, give each section a distinct accent colour in light and dark themes, and render each history entry's timestamp, event, agent and summary as separately spaced cells.

#### Scenario: History cells are separated
- **WHEN** a bead detail page with history entries is open
- **THEN** each entry's timestamp, event, agent and summary are separate elements, the visible text of the entry has whitespace or a gap between each of them, and no text like `09:50:11created` appears

#### Scenario: Font size and section colours
- **WHEN** a bead detail page is open
- **THEN** its computed base font size is 1.1 times the body font size, and the criteria, handoff, assumptions and history sections have different accent colours

#### Scenario: Mobile layout
- **WHEN** the project, run and bead detail pages are opened at 360 px wide
- **THEN** none of them scrolls horizontally

## Files to Modify

| File | Change |
|---|---|
| `src/agent_takt_fleet/web/readers.py` | backward event reading with cursors, run event extraction |
| `src/agent_takt_fleet/web/viewmodels.py` | bead order and paging, event paging, runs list and run detail |
| `src/agent_takt_fleet/web/app.py` | `cursor`/`order` params, runs routes, 400 on bad cursor |
| `src/agent_takt_fleet/web/demo.py` | runs, costs, more events and beads |
| `src/agent_takt_fleet/web/static/app.js` | routes; stop refetching activity on every event |
| `src/agent_takt_fleet/web/static/js/project.js` | redesigned project page |
| `src/agent_takt_fleet/web/static/js/activity.js`, `feed.js` | incremental feed and paging |
| `src/agent_takt_fleet/web/static/js/beads.js` | newest first and paging |
| `src/agent_takt_fleet/web/static/js/runs.js` (new) | runs list and run page |
| `src/agent_takt_fleet/web/static/js/beaddetail.js`, `css/beaddetail.css` | section styling, font size, history rows |
| `src/agent_takt_fleet/web/static/styles.css` | shared colour variables if needed |
| `tests/fleet/web/` | layer 1 and 2 tests |
| `docs/fleet.md`, `docs/assets/fleet-ui/` | docs and screenshots |

## Acceptance Criteria

- Events and beads APIs page newest first with opaque cursors, no duplicates or gaps, 400 on malformed cursors; the first events page reads only the end of each log.
- The runs list and run detail endpoints and pages work as described, including total cost and live updates for a running run.
- The project page shows recent features and beads first and compact run and activity sections, with no scheduler runs table.
- The activity screen loads 25 events, prepends live events without refetching, and loads older pages on demand.
- The bead list shows newest first with paging and a total count.
- Bead detail uses a 1.1em base font, distinct accessible section colours in both themes, status-coloured criteria and assumptions, and spaced history rows.
- No horizontal scroll at 360 px on the changed pages.
- Tests and screenshots as in §8; `uv run pytest tests/ -n auto -q` passes.

## Scope

In scope: the seven operator feedback items above and what they need (paging API, runs endpoints, fixtures, tests, docs).

Out of scope: live agent transcripts and fleet-wide dashboards (P3), any write or control action, new data sources beyond `events.jsonl` and the files in `docs/project-files.md`.

## Pending Decisions

None.
