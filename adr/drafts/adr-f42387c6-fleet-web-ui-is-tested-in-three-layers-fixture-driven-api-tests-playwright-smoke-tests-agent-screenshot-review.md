---
id: ADR-f42387c6
title: "Fleet web UI is tested in three layers: fixture-driven API tests, Playwright smoke tests, agent screenshot review"
status: draft
created_at: 2026-11-21T16:28:14.502482+00:00
authors: ['Renalias, Oscar']
description: "Testing strategy for the fleet web UI, all under pytest."
tags: [fleet, web-ui, testing]
related_specs: 
related_beads: 
supersedes: 
---
# Fleet web UI is tested in three layers: fixture-driven API tests, Playwright smoke tests, agent screenshot review

## Summary

> In the context of a browser UI for takt-fleet that agents build and change through beads, facing the need to verify data correctness, live updates and visual quality without a JS toolchain or brittle pixel diffs, we decided on three test layers under pytest (fixture-driven API and SSE tests, Playwright smoke tests per screen, and screenshots reviewed by tester and review agents), to achieve most coverage at the cheapest layer and test evidence that fits takt's merge gate and scenario citation check, accepting a Chromium download for browser tests and no automated visual regression.

## Context

The merge gate runs `config.common.test_command` (`uv run pytest tests/ -n auto -q`). Tester beads must cite `path::test_name` for every `Scenario:` criterion (`_verify_scenario_citations`), so UI tests must be pytest tests to count as evidence.

The frontend is a thin no-build client with server-side view models (ADR-73d258d5), so most UI logic is in Python. The data comes from `.takt/` files read tolerantly across takt versions (ADR-1897d241), and updates arrive over SSE (ADR-31a0427e). Both of those are easy to get subtly wrong and need dedicated tests.

Claude-based tester and review agents can read image files, so they can inspect screenshots directly.

## Decision Drivers

* Everything runs under pytest and can be cited as `path::test_name`.
* Most coverage where tests are fast and deterministic.
* Prove live updates end to end at least once per screen.
* Avoid flaky, constantly re-baselined tests.

## Considered Options

### Option A — API and SSE tests only, manual UI checks

* Good: No browser dependency; fast.
* Bad: Nothing verifies that screens render, navigation works, or live updates reach the DOM; regressions surface only when the operator opens the UI.

### Option B — Three layers: fixtures + API tests, Playwright smoke tests, agent screenshot review

* Good: Bulk of coverage in fast Python tests; a thin browser layer proves rendering and live updates; visual quality judged by a reviewer instead of pixel equality.
* Bad: Playwright adds a dev dependency and roughly 150 MB of Chromium on developer machines and CI.

### Option C — Option B plus automated screenshot diff regression

* Good: Catches unintended visual changes automatically.
* Bad: Brittle across fonts, OS and rendering; needs baseline images re-approved on every intentional change, which agents would do reflexively, defeating the purpose.

## Decision

**Option B.** Binding rules:

* **Fixture builder:** a shared test helper builds synthetic projects (`.takt/` beads, `events.jsonl`, active-run markers, traces, audit snapshots, `version.json`) and a fake fleet registry under `tmp_path`. Fixtures include deliberately older and newer bead shapes to test tolerant reading. Tests never read real projects or the real registry.
* **Layer 1, API and SSE (bulk of coverage):** every endpoint and view model is tested with Starlette's `TestClient` against fixtures. SSE tests append to fixture files and assert the event is delivered, including truncated lines, removed projects and file rotation.
* **Layer 2, browser smoke tests:** `pytest-playwright` tests start `takt-fleet serve` on a random port against the fixture registry. Each screen gets tests that it renders key elements (selected by role or `data-testid`, never CSS structure), that navigation between screens works, and that an appended event updates the page without a reload. Any browser console error or uncaught page exception fails the test. Tests use a fixed clock, disabled animations and no network.
* Browser tests are marked `@pytest.mark.ui` and skip cleanly when Chromium is not installed, so the default test command still passes on a fresh machine. CI has a job that installs Chromium and runs them.
* **Layer 3, agent visual review:** tester beads for UI work save screenshots of affected screens, rendered from fixture data, into the worktree; review beads inspect them and judge layout and readability as part of their verdict. There is no pixel-diff regression suite.
* `takt-fleet serve` can run against the fixture data (for example a `--demo` mode or a fixture registry), so the operator sees the same data the tests use.

## Consequences

### Positive

* UI test evidence satisfies the merge gate and the scenario citation check like any other test.
* Live updates are verified end to end, which is the riskiest part of the UI.
* Version-drift tolerance is tested explicitly rather than discovered in use.

### Negative

* Developer machines and CI need Chromium for full coverage; where it is missing, browser tests are skipped and coverage is lower without a failure.
* Visual regressions are caught only by agent or human review, not automatically.
* The fixture builder must be kept in step with takt's file shapes as they evolve.
