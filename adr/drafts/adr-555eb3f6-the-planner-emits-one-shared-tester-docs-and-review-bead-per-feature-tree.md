---
id: ADR-555eb3f6
title: The planner emits one shared tester, docs and review bead per feature tree
status: draft
created_at: 2026-11-08T15:47:40.961897+00:00
authors: ['Renalias, Oscar']
description: Planned feature trees use exactly one shared tester, documentation and review bead instead of per-developer followups; standalone developer beads keep legacy followups.
tags: [planner, scheduler, followups]
related_specs: [spec-7d0a815c]
related_beads: 
supersedes: 
---
# The planner emits one shared tester, docs and review bead per feature tree

## Summary

> In the context of turning a spec into a bead graph of many small developer beads, facing a 3N explosion of per-developer tester, docs and review beads that each saw only a fragment of the feature, we decided that every planned feature tree contains exactly one shared tester, one shared documentation and one shared review bead that depend on all developer beads, with per-developer auto-followups suppressed in planned trees, to achieve whole-feature testing, documentation and review at lower bead count, accepting that verification starts only after all development finishes and that two followup models coexist.

## Context

Originally, every completed developer bead triggered `_create_followups()` to create `-test`, `-docs` and `-review` children. Planner developer beads were also large (one per logical boundary) and often ran 10-15 minutes, risking timeouts (spec-7d0a815c).

Shrinking developer beads made the followup problem worse. A feature with 5 developer beads produced 15 followup beads. Reviewers reviewed tiny increments with no view of architectural coherence, docs beads documented fragments, and testers could not write tests across the whole feature.

Shared followup beads created by the planner have a practical problem of their own: at plan time the planner cannot know which files developer agents will touch, so the beads start with empty file scope and review/test agents would refuse to proceed.

## Decision Drivers

* Developer beads should stay small (one focused change, about 10 minutes).
* Testers, documenters and reviewers need the whole feature in view.
* Bead count should grow with N developers, not 4N.
* Ad-hoc developer beads created with `takt bead create` must still be tested, documented and reviewed.

## Considered Options

### Option A — Planner-emitted shared followups per feature tree, legacy followups for standalone beads

* Good: N developer beads produce N + 3 beads; each followup agent sees the full feature.
* Good: the review bead depends on the tester and docs beads, so it reviews tests and documentation too.
* Good: standalone and manual developer beads keep the existing behaviour unchanged.
* Bad: the scheduler must detect planner-owned followups and suppress its own, so two code paths exist.
* Bad: no verification starts until every developer bead is done.

### Option B — Keep per-developer auto-followups for every developer bead

* Good: immediate, isolated validation of each change; one code path.
* Bad: 3N followup beads; reviews and docs see fragments (the problem spec-7d0a815c set out to fix).

### Option C — Shared followups chosen at the planner's discretion

* Good: flexibility for unusual specs.
* Bad: a tree can end up missing a followup type, or mixing shared and per-developer children, leaving gaps in coverage or duplicate work. The planner prompt rules this out explicitly ("never omit any of these three types").

## Decision

**Every planner-produced feature tree contains exactly one shared `tester`, one shared `documentation` and one shared `review` bead as children of the feature root. The planner never creates per-developer tester, documentation or review children.**

Dependency shape, enforced by `build_planner_prompt` in `src/agent_takt/prompts.py`:

* the shared tester and documentation beads depend on **all** developer beads in the tree;
* the shared review bead depends on all developer beads **plus** the shared tester and documentation beads.

Scheduler behaviour in `src/agent_takt/scheduler/followups.py`:

* `_create_followups` suppresses legacy `-test`/`-docs`/`-review` creation when `_uses_planner_owned_followups` is true (the developer bead's parent is a planner or `feature` bead, or it sits directly under the feature root) **and** `_planner_owned_followup` finds a feature-root child of the right agent type that already depends on the bead. Scope syncing (`_sync_followup_scope`) still runs against the shared bead.
* Standalone developer beads keep legacy per-developer followups. Corrective and `merge-conflict` beads get none, and defect beads get only a `-review`.
* Before a `tester`, `documentation` or `review` bead is dispatched, `_populate_shared_followup_touched_files` merges `touched_files` and `changed_files` from every **done** dependency (developer, tester and documentation alike) into its scope, persisting only when the scope changed.

## Consequences

### Positive

* Bead count for a feature is N + 3 instead of 4N.
* Reviews assess architectural coherence; docs describe the feature as a whole; tests cover cross-bead behaviour.
* The review bead sees test and doc files because its scope includes the tester and docs beads' changes.
* `takt plan` scenario coverage (`Scenario:` criteria on the tester bead) has a single place to check.

### Negative

* Testing and review happen late; a defect introduced by the first developer bead is found only after all developer beads finish, and correctives then target the shared bead.
* A large feature produces large shared tester/review beads that can approach agent timeouts.
* Two followup models (shared vs legacy) coexist and detection depends on tree shape and dependency lists, which is easy to break with hand-edited plans.
* Planner-time file scope for shared beads is empty, so they rely on runtime scope population.
