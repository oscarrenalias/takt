---
id: ADR-1897d241
title: takt-fleet reads projects through a documented set of .takt/ files, including
  from a read-only web server
status: approved
created_at: '2026-11-21T16:28:12.663678+00:00'
authors:
- Renalias, Oscar
description: Expands the fleet's read surfaces to bead, trace, audit and telemetry
  files and allows a read-only takt-fleet serve, keeping all write and import boundaries.
accepted_at: '2026-11-21T16:34:28.053633+00:00'
superseded_at: null
superseded_by: null
supersedes:
- ADR-ea044a9f
tags:
- fleet
- architecture
- boundaries
- web-ui
related_specs: []
related_beads: []
review_after: null
---

# takt-fleet reads projects through a documented set of .takt/ files, including from a read-only web server

## Summary

> In the context of building a browser UI for takt-fleet that shows projects, runs, live activity and individual beads, facing a boundary (ADR-ea044a9f) that limits fleet reads to CLI JSON output, `events.jsonl` and active-run markers, we decided to expand the fleet's read surface to a documented set of `.takt/` files (bead JSON, traces, audit snapshots, telemetry, `version.json`), read tolerantly as raw data, and to allow a read-only `takt-fleet serve` within that boundary, to achieve fast per-request access without breaking per-project version pinning, accepting that those files become a versioned public contract that takt must keep backward compatible.

## Context

ADR-ea044a9f made `takt-fleet` a separate CLI that touches projects only through the `takt` CLI as a subprocess and documented `.takt/` reads, and stated that a future web control plane must stay within those surfaces or supersede it. The surfaces it lists (summary/`--json` stdout, `.takt/logs/events.jsonl`, `.takt/runs/active/*.json`) cannot support a bead explorer, trace viewer or audit view.

Calling `uv run takt bead show --json` per request is the obvious way to stay inside the old boundary, but it costs roughly a second of `uv` start-up per call. That is too slow for an interactive UI that renders dozens of beads.

Each registered project can pin its own takt version. Data the fleet reads may therefore be older or newer than the fleet's own `agent_takt` models. `agent_takt.audit` imports `agent_takt.storage`, which the fleet must not import, so the fleet cannot build audits itself.

Live agent activity is not yet observable. `runner.py` runs `claude -p` to completion and the session id is only known from its final output. Claude Code does write its transcript to `<claude config>/projects/*/<session_id>.jsonl` while it runs.

## Decision Drivers

* The fleet must never be able to corrupt a project or race its scheduler.
* Respect each project's pinned takt version; tolerate schema drift in both directions.
* Interactive latency: no subprocess per UI request.
* Keep the project-facing contract explicit and documented.

## Considered Options

### Option A — Stay inside ADR-ea044a9f: subprocess per request

* Good: No change to the boundary; every read goes through the project's own takt.
* Bad: About one second of `uv run` start-up per call; a bead tree view would need many calls or a new bulk command per screen.
* Bad: Every new UI view needs a new takt CLI command first.

### Option B — Import takt's storage layer and read projects in-process

* Good: Typed, fast, no parsing code in the fleet.
* Bad: Runs the fleet's takt version against every project's data; models that reject unknown or missing fields break on version drift.
* Bad: `RepositoryStorage` has write paths and locking; importing it removes the structural guarantee that the fleet cannot write.

### Option C — Documented `.takt/` files as the read protocol, parsed tolerantly

* Good: Fast direct reads; no subprocess for anything that is read-only.
* Good: Raw dicts pass unknown fields through, so newer projects still render.
* Bad: Bead, trace, audit and telemetry file shapes become a public contract that takt has to keep backward compatible.
* Bad: The fleet duplicates a little reading logic that `storage.py` already has.

### Option D — Server with its own database fed from the projects

* Good: Fast queries and history across projects.
* Bad: A second copy of the truth that has to be kept in sync, plus migration and infrastructure; rejected again for the same reasons as in ADR-ea044a9f.

## Decision

**Option C.** This ADR supersedes ADR-ea044a9f and carries over all its rules except the list of allowed read surfaces. The binding rules are:

* **Writes:** unchanged. The fleet never writes under a project's `.takt/`. Writes are confined to `registry.py` and `runlog.py`, targeting the XDG registry and fleet run log. A web server may add fleet-owned files under the same XDG directories only.
* **Imports:** unchanged. Never import `agent_takt.storage`, `scheduler`, `runner`, `gitutils`, `planner`, `cli.*`, or any module that imports them transitively (for example `agent_takt.audit`). Pure helpers and models (for example `agent_takt.models`, `agent_takt.bead_metrics`, `agent_takt.traces.normalize`) are allowed. `tests/fleet/test_boundaries.py` must check transitive imports as well as direct ones.
* **Subprocess:** unchanged. `subprocess` calls with `cwd=<project>` appear only in `adapter.py`. Mutations (`dispatch`, `run`) still go only through the takt CLI.
* **Allowed read surfaces** (all read-only):
  * `takt ... --json` / summary stdout via `TaktAdapter`
  * `.takt/logs/events.jsonl`
  * `.takt/runs/active/*.json`
  * `.takt/beads/*.json`
  * `.takt/traces/<bead_id>/*`
  * `.takt/audits/<feature_root_id>/*` (stored snapshots only; the fleet does not build audits)
  * `.takt/telemetry/<bead_id>/*`
  * `.takt/version.json`
  * the agent session transcript whose session id a documented event names, once takt records one at worker start
* **Documented means documented:** every file above must be described in a contract document in `docs/` (shape, stability, which fields are optional) before the fleet reads it. Changes to those shapes follow the same rule as `events.jsonl` (ADR-add43ad0): new fields optional, existing fields not removed or repurposed.
* **Tolerant reads:** fleet code reads these files as plain JSON. It may use `agent_takt` models or helpers to derive values, but a failure there must fall back to the raw data, never to an error page. Unknown fields are passed through to API clients. Each project's `version.json` is reported so drift is visible.
* **Server allowed:** a user-launched, foreground, read-only `takt-fleet serve` is within this boundary. It holds no database and no state that is not derived from the surfaces above or the fleet's own files. Server specifics are in ADR-31a0427e.

## Consequences

### Positive

* A web UI can show beads, traces and audits with file-read latency.
* Projects on different takt versions still share one fleet, and drift is visible to the operator.
* The project-facing contract becomes explicit for every file the fleet uses, not just events.

### Negative

* takt takes on backward-compatibility duties for bead, trace, audit and telemetry file shapes, and has to document them.
* The fleet carries its own tolerant reading code, which can lag behind new takt fields until it is updated.
* The boundary test has to grow to check transitive imports, which makes it more complex.
* Live transcript viewing depends on a takt change (recording the session id at worker start) and is Claude-only; Codex runs show events only.
