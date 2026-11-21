---
id: ADR-73d258d5
title: "Fleet web UI frontend is a vendored, no-build Preact + htm app with server-side view models"
status: draft
created_at: 2026-11-21T16:28:13.899735+00:00
authors: ['Renalias, Oscar']
description: "Frontend stack and the rule that screen logic lives in the Python server."
tags: [fleet, web-ui, frontend]
related_specs: 
related_beads: 
supersedes: 
---
# Fleet web UI frontend is a vendored, no-build Preact + htm app with server-side view models

## Summary

> In the context of a browser UI for takt-fleet in a Python project that builds itself through beads, facing the cost of adding a Node toolchain to development, CI, packaging and agent worktrees, we decided on a no-build frontend using Preact and htm as vendored ES modules, with screen logic computed by the server into view models, to achieve one toolchain, an offline-capable wheel and logic testable in pytest, accepting a less capable frontend toolchain and a possible later migration to a bundler.

## Context

takt is a Python package distributed as a wheel; its test gate is `uv run pytest`. All code changes are made by agents through beads (ADR-b6447b2e), so whatever toolchain the frontend uses has to work in every feature worktree and be understood by developer, tester and review agents. The framework is otherwise stack-agnostic for user projects (ADR-d2b1f2b4), but this choice concerns takt's own code.

The UI is a local dashboard: a handful of screens (fleet overview, activity feed, run view, bead explorer) with live updates. It must work without internet access.

## Decision Drivers

* One toolchain: no Node, npm or bundler needed to develop, test or package takt.
* Works offline; no CDN at runtime.
* As much logic as possible testable with pytest.
* Agents must be able to change the UI reliably within a single bead.

## Considered Options

### Option A — Vite + React/Svelte, built assets shipped in the wheel

* Good: Full modern toolchain, component libraries, TypeScript, JS unit tests.
* Bad: Adds Node and npm to development, CI and every agent worktree; build output must be produced in CI or committed.
* Bad: Two test runners, and a merge gate that has to run both.

### Option B — Server-rendered HTML with htmx

* Good: No JS app at all; logic stays in Python templates.
* Bad: Awkward for live-updating, deeply nested views such as a bead tree with expanding detail and streaming transcript panes.

### Option C — Preact + htm as vendored ES modules, no build step

* Good: Component model and JSX-like templates with no compiler; the files the browser loads are the files in the repo.
* Good: Small (tens of kB), shipped as package data, works offline.
* Bad: No TypeScript, no JS unit-test runner, fewer ready-made components.
* Bad: Vendored library upgrades are manual.

## Decision

**Option C.** Binding rules:

* The frontend is plain ES modules plus CSS, served as static package data from the fleet package. There is no build step and no `package.json`.
* Third-party JS (Preact, htm, and any later additions) is vendored as pinned, unmodified files with the version in the file name or a manifest. Pages never load scripts or styles from a CDN.
* **Thin client:** the server shapes data into view models per screen (status badges, criteria rollups, durations, ordering, tree structure). The client renders those models and handles navigation and live-update merging only. Logic that can live in the server must live in the server, where pytest covers it.
* Interactive elements and key regions carry stable `data-testid` attributes or accessible roles for tests (see ADR-f42387c6).
* Moving to a bundler-based stack requires a superseding ADR, which should be triggered by concrete pain (screen count, shared component needs, a need for TypeScript), not preference.

## Consequences

### Positive

* `uv sync` plus `uv run pytest` stay the only things needed to develop and test takt, including in agent worktrees.
* The wheel contains everything the UI needs and works offline.
* Most UI behaviour is tested in Python.

### Negative

* No type checking or JS unit tests; client bugs are caught only by browser tests (ADR-f42387c6).
* Less component ecosystem; tables, graphs and code views are hand-built or vendored one by one.
* A later migration to a bundler would touch every screen.
