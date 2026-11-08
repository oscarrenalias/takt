---
id: ADR-ea044a9f
title: takt-fleet is a separate CLI that drives projects only through public surfaces
status: draft
created_at: 2026-11-08T15:46:58.873170+00:00
authors: ['Renalias, Oscar']
description: takt-fleet touches projects only via the takt CLI subprocess and documented .takt/ reads; no daemon, no shared database.
tags: [fleet, architecture, boundaries]
related_specs: [spec-0ba9d2a3, spec-1cdb1b8a]
related_beads: 
supersedes: 
---
# takt-fleet is a separate CLI that drives projects only through public surfaces

## Summary

> In the context of operators running takt across many local projects, facing the need to dispatch work, trigger runs and watch activity fleet-wide without breaking any project's state or version pinning, we decided to ship `takt-fleet` as a separate CLI that reaches projects only through the `takt` CLI (as a subprocess) and documented `.takt/` files, with no daemon and no shared database, to achieve a thin, safe coordinator that works with each project's own takt version, accepting subprocess overhead, JSON-over-stdout parsing and polling-based visibility.

## Context

Spec-0ba9d2a3 (Fleet Manager v0.1.0) addressed operators repeating the same CLI steps in N projects: fanning out an ad-hoc bead to each project, and draining queued work across projects. Each project owns its state in `.takt/` (beads, logs, config), may pin a different takt version through its own `uv` environment, and may have a `takt run` already going in another terminal. A coordinator that wrote to project state directly or called takt's domain logic in-process would bypass locking, version pinning and scheduler invariants.

That spec put cross-project specs, cross-project bead dependencies, coordinated merges, remote projects, a web UI, a persistent daemon and authentication out of scope. Spec-1cdb1b8a later added scheduler run lifecycle events and active-run markers, so the fleet could see runs it had not started (`takt-fleet summary` SCHEDULER column, `takt-fleet scheduler-runs list`; see `specs/current/fleet-scheduler-visibility.md`). Its objective names a future web-based fleet control plane as a consumer of that data.

## Decision Drivers

* No path for the fleet to corrupt a project's `.takt/` state.
* Respect each project's pinned takt version.
* Zero standing infrastructure: no server, no daemon, no database.
* An audit trail of what the fleet ran and how each project responded.

## Considered Options

### Option A — Separate CLI using only the takt CLI subprocess plus documented file reads

* Good: Every mutation goes through `takt` itself, with all its validation and locking.
* Good: `TaktAdapter` runs `uv run takt ...` with `cwd=project_path`, so each project resolves its own pinned takt (Pending Decision 2 in spec-0ba9d2a3).
* Good: Stateless apart from two fleet-owned files, so there is nothing to keep running or migrate.
* Bad: Subprocess start-up cost on every call, plus fragile JSON extraction from mixed stdout (`_extract_json`).
* Bad: Live views poll files (`tailer.py`). No push notifications.

### Option B — Import takt's domain logic (`storage`, `scheduler`) and operate on project paths in-process

* Good: Faster, typed access with no stdout parsing.
* Bad: Runs the fleet's takt version against every project, which breaks per-project pinning.
* Bad: Bypasses the CLI's guards and can race a running scheduler.

### Option C — Long-running daemon / server with a shared database

* Good: Push updates, central state, and a natural base for a web UI.
* Bad: New infrastructure to run, secure and keep in sync with per-project truth. Explicitly out of scope for v0.1.0 (persistent daemon, web UI, authentication).

## Decision

**`takt-fleet` (package `agent_takt_fleet`, console script declared alongside `takt` in `pyproject.toml`) interacts with registered projects exclusively through the `takt` CLI as a subprocess and by reading documented `.takt/` files.** Binding rules, enforced by AST scans in `tests/fleet/test_boundaries.py`:

* Never write under a project's `.takt/`. Filesystem writes are confined to `registry.py` and `runlog.py`, targeting only the XDG registry (`$XDG_CONFIG_HOME/agent-takt/fleet.yaml`) and the fleet run log (`$XDG_DATA_HOME/agent-takt/fleet/runs/FR-<8hex>.json`), resolved in `paths.py`. These are the fleet's only state.
* Never import `agent_takt.storage`, `scheduler`, `runner`, `gitutils`, `planner` or `cli.*`. Importing utilities and models is allowed.
* `subprocess` calls with `cwd=<project>` appear only in `adapter.py` (`TaktAdapter`).
* Reads are limited to documented surfaces: `takt ... --json`/summary stdout, `.takt/logs/events.jsonl` (`tailer.py`, `scheduler_runs.py`) and `.takt/runs/active/*.json` markers.
* `dispatch` creates beads but does not execute them. `run` spawns `uv run takt run` per project.

A future server or web control plane must either stay within these surfaces (CLI subprocess plus documented `.takt/` reads, fleet-owned state only) or supersede this ADR explicitly.

## Consequences

### Positive

* The fleet cannot bypass takt's invariants or corrupt a project. The worst case is a failed subprocess, recorded with stdout and stderr in the run log via `AdapterError`.
* Projects on different takt versions can share one fleet.
* The project-facing contract (`docs/events.md`, `summary` JSON, active markers) is explicit, which gives a later control plane a stable base.

### Negative

* Any capability the fleet needs must first be exposed as a takt CLI command or a documented file. Takt changes can block fleet features.
* Subprocess fan-out and stdout JSON parsing are slower and more brittle than in-process calls.
* No push channel. `watch` and the scheduler column rely on polling and pid checks, and remote projects are unsupported because `alive` is unknown for markers from other hosts.
* A web UI or daemon cannot be built on this ADR as-is unless it keeps to the same boundary.
