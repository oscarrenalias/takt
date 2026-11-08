---
id: ADR-102056d3
title: Structured verdicts and handoffs drive control flow, never prose
status: draft
created_at: 2026-11-08T15:47:40.293985+00:00
authors: ['Renalias, Oscar']
description: Scheduler control flow is driven by schema-validated verdict, criteria_status and assumptions fields; free-form narrative is context only.
tags: [scheduler, handoff, governance]
related_specs: [spec-b9e74217, spec-3078243f, spec-e93e36bf, spec-36cd4ad3]
related_beads: 
supersedes: 
---
# Structured verdicts and handoffs drive control flow, never prose

## Summary

> In the context of a scheduler that decides bead outcomes from AI worker output, facing false blocks, silently dropped acceptance criteria and invisible agent guesses when those decisions were inferred from free-form text, we decided that schema-validated structured fields (`verdict`, `criteria_status`, `assumptions`) are the only control-flow signal and that the scheduler reconciles and enforces them at finalize time, to achieve deterministic, auditable pipeline decisions, accepting a larger output schema that every worker must fill in and a scheduler that can override an agent's own verdict.

## Context

Early review and tester beads were classified by parsing the free-form `remaining` field. A reviewer writing a benign phrase such as "no findings discovered" that was not covered by an allowlist was treated as reporting open work, which caused false blocks, unnecessary corrective loops and wasted tokens (spec-b9e74217). Prose also carried everything else downstream agents needed: why a change was made, what was tested, what was left out. Reviewers averaged 7.4 turns largely extracting that context (spec-3078243f).

Two further gaps showed up once verdicts were structured. A bead's `acceptance_criteria` were strings agents were "asked to satisfy", but nobody reported against them item by item, so a developer could skip one, a tester could skip whole behaviours, and a reviewer approving the code that existed would not notice what was never built (spec-e93e36bf). And agents filling gaps in a spec (a default value, an exit code, a config key shape) did so silently, at best mentioning it in `design_decisions` prose (spec-36cd4ad3).

The common root is that the scheduler cannot reliably act on, count or audit prose. takt runs without a human pacing each step, so the checks have to be mechanical.

## Decision Drivers

* Control flow must be deterministic: the same agent output always yields the same bead state.
* Scope loss between spec and merge must be detectable without a human reading every handoff.
* Agent-made decisions must be recorded and reviewable without pausing the run.
* Data must be aggregatable for `takt summary --feature-root` and the feature audit report.
* Agents can be wrong or optimistic about their own work; enforcement cannot rest on prompt wording alone.

## Considered Options

### Option A — Schema-validated structured fields, reconciled and enforced by the scheduler

* Good: `AGENT_OUTPUT_SCHEMA` (defined identically in `runner.py` and `prompts.py`, with a schema-identity test in `tests/test_recovery_flow.py`) constrains `verdict` to `approved | needs_changes`, `criteria_status[].status` to `met | unmet | descoped` and `assumptions[].scope` to `local | structural` via `enum`, with `additionalProperties: false`.
* Good: finalize-time helpers in `scheduler/finalize.py` (`_reconcile_criteria_status`, `_normalize_assumptions`, `_verify_scenario_citations`) make malformed or incomplete output safe: unreported criteria become `unmet`, unknown scopes become `structural`.
* Good: `_apply_review_test_verdict` can override an `approved` verdict to `needs_changes` when own or dependency criteria are `unmet`, so approval cannot outrun the evidence.
* Good: every field is countable and surfaces in events (`verdict_overridden`, `criteria_gap_recorded`, `structural_assumption_recorded`), `takt summary` and `takt audit`.
* Bad: larger output contract; every guardrail template must teach it and workers spend tokens filling it.
* Bad: the scheduler second-guessing an agent can surprise operators reading only the agent's narrative.

### Option B — Keep parsing narrative text, with better allowlists and heuristics

* Good: no schema change; agents keep writing natural prose.
* Bad: this is what existed and it false-blocked beads whenever wording drifted; every new phrase needs a new allowlist entry.
* Bad: cannot express per-criterion status or classify assumptions without brittle extraction.

### Option C — Structured fields, but trust the agent's verdict as final

* Good: simpler finalize logic; no overrides to explain.
* Bad: a tester or reviewer can return `approved` while its own criteria list unmet items (problem 2 in spec-e93e36bf).
* Bad: unmet criteria on a developer bead never stop the feature, so scope loss reaches `main`.

## Decision

**Bead control flow is decided only by structured, schema-validated fields; narrative fields (`completed`, `remaining`, `risks`, `design_decisions`, `test_coverage_notes`, `known_limitations`) are context for humans and downstream agents and must never drive state transitions.**

Binding rules:

* `review` and `tester` beads signal outcome through `verdict`. `approved` completes; `needs_changes` blocks and defaults `requires_followup` to true.
* Every worker reports `criteria_status` against the bead's 0-based acceptance criteria; `_reconcile_criteria_status` rewrites it to exactly one entry per criterion before the verdict is applied.
* The scheduler overrides an `approved` tester/review result to `needs_changes`/`blocked` (`_override_verdict`) when its own criteria include `unmet`, or, for review beads, when a done dependency has an unresolved `unmet` criterion (`dependency_criteria_state` in `scheduler/criteria.py`). `descoped` with a reason never triggers an override.
* Tester `Scenario:` criteria reported `met` must cite an existing `path::test_name`, or they are downgraded to `unmet`.
* Assumptions are recorded as structured `{statement, rationale, scope}` entries; reviewers must check every `structural` one. They never block automatically.
* The same reconciliation and overrides apply on the recovery path (`_handle_recovery_completion`), so recovery cannot be used to get an approval past unmet criteria.
* `developer`, `defect` and `documentation` beads are not blocked for unmet criteria; their gaps flow to the review bead, which cannot approve past them.

The legacy `remaining`-text fallback survives only behind `REVIEW_TEST_VERDICT_COMPAT_MODE` in `scheduler/finalize.py`, records a `compat_fallback_warning`, and still runs the criteria override. It is a transitional path, not a design option for new work.

## Consequences

### Positive

* No more false blocks from benign reviewer wording; verdict is unambiguous.
* Dropped acceptance criteria are caught by the scheduler rather than relying on reviewer attention, and corrective beads inherit them as explicit `Resolve unmet criterion #<i> from <bead_id>: ...` criteria.
* Fabricated or stale test citations for spec scenarios are caught cheaply at finalize.
* Feature-level governance (`takt summary --feature-root`, `takt audit`, TUI badges, the pre-merge summary) is built on countable data.

### Negative

* Agents that emit prose instead of JSON fail outright; this needs a separate recovery mechanism (recovery beads).
* Scheduler overrides add review/corrective cycles when an agent reports a criterion `unmet` that is actually fine; the cure is a better `descoped` reason, not removing the check.
* The scenario citation check proves only that a cited test exists, not that it exercises the scenario.
* Two copies of `AGENT_OUTPUT_SCHEMA` must be kept identical, and compat mode remains as code to retire.
