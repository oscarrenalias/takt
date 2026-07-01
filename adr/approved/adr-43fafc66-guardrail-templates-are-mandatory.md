---
id: ADR-43fafc66
title: Guardrail templates are mandatory
status: approved
created_at: '2026-07-01T07:40:38.047844+00:00'
authors:
- Renalias, Oscar
description: Every runnable agent type must have a templates/agents/<type>.md file;
  missing template fails the bead with FileNotFoundError.
accepted_at: '2026-07-01T09:54:39.809138+00:00'
superseded_at: null
superseded_by: null
supersedes: []
tags:
- foundational
- agents
- safety
related_specs: []
related_beads: []
review_after: null
---

# Guardrail templates are mandatory

## Summary

> In the context of running AI workers that mutate a shared codebase, facing the risk of agents drifting out of scope or violating role expectations, we decided that every runnable agent type must have a mandatory guardrail template at `templates/agents/<type>.md`, to achieve enforced role boundaries and predictable per-agent behaviour, accepting that adding a new agent type is not a one-line change.

## Context

AI coding agents will happily broaden scope, run tests when told not to, redesign unrelated architecture, and skip documentation — unless something in their prompt actively forbids it. Takt's design goal is deterministic, role-scoped behaviour: a developer bead writes code but does not run tests; a tester bead runs tests but does not fix unrelated code; a reviewer bead produces a verdict but does not silently edit the code it's reviewing.

That behavioural contract lives in the guardrail template — a markdown file per agent type at `templates/agents/<agent_type>.md`, loaded into every worker prompt for that type. Without a guardrail file, the agent falls back to whatever the base model does by default, which is unbounded.

The question is: what should happen when a guardrail file is missing? A soft fallback (proceed with a generic prompt) is convenient during development but silently degrades to unbounded behaviour in production. A hard failure (raise `FileNotFoundError`) forces the operator to author a guardrail before the agent runs, at the cost of some friction.

## Considered Options

### Option A — Mandatory: missing template fails the bead with `FileNotFoundError`

* Good: Impossible to run an agent type in production without an explicit behavioural contract.
* Good: When a new agent type is added to `BUILT_IN_AGENT_TYPES`, the missing template is caught immediately, not months later when the first bead runs.
* Good: The guardrail file is a concrete, reviewable artefact — operators can see and edit exactly what each agent is being told.
* Bad: Adding a new agent type is a two-file change (enum + template); the template is easy to forget.
* Bad: A packaging or file-permission issue can turn into a hard scheduler failure with a misleading error surface.

### Option B — Soft fallback: missing template logs a warning and uses a generic prompt

* Good: Adding a new agent type is a one-line change; templates can be filled in incrementally.
* Good: Missing packaging doesn't hard-fail the scheduler.
* Bad: Silent behavioural drift — an agent may be running with no scope constraints and neither operator nor scheduler notices until damage is done.
* Bad: Different projects would behave differently depending on which templates they had installed.
* Bad: The generic prompt has no way to encode role-specific requirements (e.g. "tester must not modify implementation code"), so the "fallback" isn't actually a working agent.

### Option C — Optional templates, with `takt validate` command to check for gaps

* Good: Same convenience as Option B during development.
* Good: `takt validate` provides an escape valve for operators who want to audit gaps.
* Bad: Relies on the operator remembering to run `takt validate` before running the scheduler.
* Bad: Failure mode is deferred to whoever notices the missing template, which in practice is nobody until a bead misbehaves.

## Decision

**Every agent type in `BUILT_IN_AGENT_TYPES` (or in a project's `common.agent_types` override) must have a corresponding guardrail template at `templates/agents/<agent_type>.md`.** The template is loaded via `load_guardrail_template()` at bead-start time; a missing file raises `FileNotFoundError` and blocks the bead with an explicit error surface.

Corollary: adding a new agent type to takt requires three artefacts landing together — the enum entry in `BUILT_IN_AGENT_TYPES` (and JSON schema), the guardrail template at `templates/agents/<type>.md`, and a mirror at `src/agent_takt/_data/templates/agents/<type>.md`. The parity test catches the source-vs-packaged mirror gap.

## Consequences

### Positive

* No agent runs in production without an explicit, reviewable behavioural contract.
* Role boundaries are enforceable and reviewable — operators can grep the template to know what each agent will and won't do.
* New agent types are introduced together with their guardrails, not months later.
* The mandatory-file contract makes the packaging gap for `recovery.md` and `defect.md` (fixed by B-af5e9477) impossible to reintroduce silently.

### Negative

* Adding a new agent type is a multi-file change; forgetting the template will hard-fail the first bead that tries to use the type.
* Operators cannot temporarily "disable" an agent type by removing its template — the correct mechanism is to remove the type from `common.agent_types`.
* Packaging failures (missing file in the installed wheel) surface as hard scheduler errors rather than degraded behaviour, which is stricter but also less forgiving in the field.
