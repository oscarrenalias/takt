---
id: ADR-d160fd37
title: Beads are the unit of work
status: approved
created_at: '2026-07-01T07:40:18.403995+00:00'
authors:
- Renalias, Oscar
description: Beads (not issues/tickets/PRs) are the atomic unit of scheduled work
  in takt.
accepted_at: '2026-07-01T09:54:38.332348+00:00'
superseded_at: null
superseded_by: null
supersedes: []
tags:
- foundational
- core
related_specs: []
related_beads: []
review_after: null
---

# Beads are the unit of work

## Summary

In the context of coordinating specialised AI workers against a shared codebase, facing the need for atomic, agent-scoped units of work with structured handoffs and guardrails, we decided to introduce the **bead** as takt's atomic work unit, to achieve reliable step-by-step decomposition and cross-agent orchestration, accepting a takt-specific vocabulary that operators must learn.

## Context

AI coding agents are undisciplined by default — they skip tests, drift out of scope, forget documentation, and lose context across long tasks. Coordinating multiple agents (developers, testers, reviewers) against a shared codebase requires a unit of work that is smaller than a feature but larger than a single tool call: something that carries a specific role assignment, a well-defined scope, a lifecycle, and a structured handoff to the next agent.

Existing systems in adjacent spaces (issues, tickets, pull requests, cards) were designed for humans, not for AI orchestration. They lack the structured input/output schema, the lifecycle machinery, the file-scope conflict-checking, the followup graph, and the per-agent guardrail hooks that a scheduler needs to reliably drive AI workers.

## Considered Options

### Option A — Beads

* Good: Takt-specific vocabulary avoids overloading meanings from adjacent tools (issue, PR, task).
* Good: Structured lifecycle (`open → ready → in_progress → done | blocked | handed_off`) is trivially machine-driveable.
* Good: JSON persistence with schema-enforced fields makes bead state a first-class programmable artefact.
* Good: Feature root beads compose into planner-generated graphs with dependencies, followups, and shared review nodes.
* Bad: New concept for operators to learn.
* Bad: JSON files under `.takt/beads/` add to project structure noise if not managed.

### Option B — Reuse GitHub Issues (or similar external tracker) as the unit of work

* Good: Familiar to any developer; nothing to learn.
* Good: Free integration with GitHub-based workflows.
* Bad: External API dependency; can't work offline; rate limits.
* Bad: Issue schema is human-optimised — no `handoff_summary`, no `verdict`, no `expected_files`, no `execution_history`. We'd have to shoehorn structured data into free-text.
* Bad: Lifecycle is coarse (`open` / `closed`) — no `blocked`, no `handed_off`.
* Bad: Cross-project use forces one tracker per project; agents can't reason about work in-place.

### Option C — Reuse pull requests / branches as the unit of work

* Good: Git-native, no external service.
* Bad: PRs are a merge artefact, not a work unit. Every work step would need its own PR.
* Bad: No lifecycle beyond "open / merged / closed" — again, no `blocked` state.
* Bad: PRs don't carry a role assignment; agents would have to infer it from title or labels.

## Decision

**A bead is the atomic unit of scheduled work in takt.** Every mutation to a project's codebase must flow through a bead: it carries a title, description, agent type assignment, file scope, dependencies, lifecycle status, and a structured handoff summary. The scheduler operates exclusively on beads; agents read beads and produce structured bead outputs; the operator queries and manipulates work only through beads.

Beads are stored as JSON under `.takt/beads/` and can optionally be tracked in git or held as untracked working state (see `commit_bead_state` config). They are indexed by hash-derived 8-hex IDs (see the ID-scheme ADR) and are lifecycle-driven by the scheduler; operators do not manually transition developer beads to `done` (that must go through the scheduler to trigger followups).

## Consequences

### Positive

* Every unit of work carries structured metadata (scope, role, dependencies, handoff) that the scheduler can operate on programmatically.
* Lifecycle events (`ready`, `blocked`, `done`) are unambiguous — agents and operators share the same state machine.
* Cross-agent handoffs are typed (developer → tester → docs → review), enforced by the followup machinery rather than by convention.
* No external service dependency; work state travels with the repo.

### Negative

* Operators must learn takt-specific vocabulary (bead, feature root, corrective, defect, followup) that has no direct analogue in adjacent tools.
* `.takt/beads/*.json` adds a project directory that some operators will find noisy (mitigated by `commit_bead_state: false` default for new projects, which keeps bead state out of git history).
* Third-party tooling (issue trackers, project management dashboards) doesn't understand beads and must be bridged if operators want that visibility. `takt-fleet` provides some of this cross-project rollup, but takt does not integrate with GitHub Issues, Jira, or Linear out of the box.
