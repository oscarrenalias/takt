---
id: ADR-31a0427e
title: takt-fleet serve is a localhost, read-only Starlette server pushing updates
  over SSE
status: approved
created_at: '2026-11-21T16:28:13.225503+00:00'
authors:
- Renalias, Oscar
description: Server stack, transport, change detection and exposure rules for the
  fleet web UI.
accepted_at: '2026-11-21T16:34:28.839610+00:00'
superseded_at: null
superseded_by: null
supersedes: []
tags:
- fleet
- web-ui
- architecture
related_specs: []
related_beads: []
review_after: null
---

# takt-fleet serve is a localhost, read-only Starlette server pushing updates over SSE

## Summary

> In the context of a browser UI for takt-fleet that must update as beads start, finish and fail across many projects, facing a choice of server stack, push transport and exposure model, we decided on a user-launched `takt-fleet serve` built on Starlette and uvicorn behind an optional extra, pushing changes over Server-Sent Events from polling-based change detection, bound to localhost and strictly read-only, to achieve a live UI with minimal moving parts, accepting extra optional dependencies, about one second of update latency and no remote access in v1.

## Context

The UI is read-only and data flows only from the server to the browser. The data it shows lives in per-project `.takt/` files (see ADR-1897d241), which change when takt writes them; there is no event bus to subscribe to. `tailer.py` already watches `events.jsonl` by polling. The operator runs about a dozen local projects.

Traces include full agent transcripts, prompts and tool output, so anyone who can reach the server can read project code and conversation content. The fleet has no authentication.

takt's core dependencies are deliberately small (pyyaml, textual, onnxruntime, sqlite-vec, tokenizers, numpy); the web stack should not be forced on users who do not run the UI.

## Decision Drivers

* Push updates without the browser polling every endpoint.
* Read-only: no endpoint may change project or fleet state.
* Safe by default; transcript data must not be exposed on the network by accident.
* Keep core install size and dependency set unchanged.

## Considered Options

### Option A — stdlib `http.server` (ThreadingHTTPServer) with hand-rolled SSE

* Good: No new dependencies.
* Bad: No routing, streaming helpers or test client; SSE, cancellation on disconnect and static files all have to be written and tested by hand.

### Option B — Starlette + uvicorn as an optional extra, SSE transport

* Good: Small, async, streaming responses built in; `TestClient` makes endpoint tests plain pytest.
* Good: SSE is one-way, plain HTTP, and the browser's `EventSource` reconnects on its own.
* Bad: Two new dependencies (in an extra) and an async code path in a mostly synchronous codebase.

### Option C — WebSockets

* Good: Two-way channel, useful if the UI later sends commands.
* Bad: Unneeded for a read-only UI; harder to test and no automatic reconnect.

### Option D — Filesystem notifications (watchdog / FSEvents) for change detection

* Good: Lower latency, no idle polling.
* Bad: Another dependency with platform differences; `.takt/` writes are atomic renames and appends, which notification APIs report inconsistently. Polling at this scale is cheap.

## Decision

**Option B, with polling-based change detection.** Binding rules:

* The command is `takt-fleet serve [--host] [--port]`, running in the foreground until stopped. It is not a daemon and keeps no database.
* Starlette and uvicorn live in an optional extra (`agent-takt[fleet-ui]`). `takt-fleet serve` without the extra exits non-zero with an install hint; all other fleet commands work without it.
* The server binds to `127.0.0.1` by default. Binding to any other address requires an explicit opt-in flag and prints a warning that traces and transcripts will be reachable without authentication.
* The HTTP API is read-only: only `GET` endpoints (plus the SSE stream). No endpoint calls `TaktAdapter` methods that mutate (`create_bead`, `run`).
* Live updates use Server-Sent Events. Change detection polls file sizes and mtimes of the allowed surfaces (about once a second), reading only appended bytes for JSONL files. Events carry enough data for the client to update without a refetch where practical, and the client refetches the affected view otherwise.
* All project data access goes through the boundary in ADR-1897d241.

## Consequences

### Positive

* Live UI with simple, testable server code; SSE and REST endpoints are covered by `TestClient`.
* Core takt installs are unaffected.
* Nothing is exposed beyond the machine unless the operator asks for it.

### Negative

* Updates lag by up to the polling interval.
* No remote or phone access in v1; adding it needs authentication and a new decision.
* If the UI later needs to send commands (start runs, retry beads), the read-only rule and possibly the transport have to be revisited in a superseding ADR.
