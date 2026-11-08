---
id: ADR-a188353c
title: Specs describe changes; specs/current records current behaviour; ADRs record
  decisions
status: approved
created_at: '2026-11-08T15:47:08.350536+00:00'
authors:
- Renalias, Oscar
description: Specs are forward-looking change documents, specs/current holds living
  per-capability behaviour merged from requirement deltas, ADRs hold cross-cutting
  decisions.
accepted_at: '2026-11-08T15:52:52.652698+00:00'
superseded_at: null
superseded_by: null
supersedes: []
tags:
- specs
- governance
- foundational
related_specs:
- spec-ea87bef7
- spec-b2f91a3c
- spec-92d4fb81
- spec-e93e36bf
related_beads: []
review_after: null
---

# Specs describe changes; specs/current records current behaviour; ADRs record decisions

## Summary

> In the context of agents and operators needing durable written context for a self-hosting codebase, facing specs that go stale once done, free-form untestable acceptance criteria, and settled decisions that kept getting re-litigated, we decided to split the record three ways — lifecycle-managed specs (via `spec.py`) describe changes, `specs/current/<capability>.md` holds current behaviour merged from `## Requirements` deltas, and ADRs record cross-cutting decisions — to achieve a single, purpose-specific home for each kind of knowledge, accepting three artefact types and their tooling that authors must learn to tell apart.

## Context

Before this split, knowledge lived in three places, each poorly suited to some of what it held:

* **Specs** (`specs/drafts/`, `specs/planned/`, `specs/done/`) describe *what to build*. `spec-cli` (spec-b2f91a3c) introduced `spec.py` because spec creation, status changes and file moves were manual and error-prone, with frontmatter and folder drifting apart.
* Once in `specs/done/`, a spec goes stale. `living-specs-and-requirement-deltas` (spec-ea87bef7) noted that "nothing records current behaviour": learning how a capability behaves today meant reading code or reconciling a series of done specs that may contradict each other. Acceptance criteria were free-form and nothing tied them to tests.
* `add-adr-lifecycle-management-basics` (spec-92d4fb81) observed that standalone decisions not tied to a feature ended up in CLAUDE.md ("becoming a kitchen sink"), in bead `design_decisions`, or in tribal memory, and were re-litigated by agents in later sessions.

The living-specs concepts (living specs, delta specs, archive-merge) are borrowed from OpenSpec. The tooling stays takt's own: a stdlib-only `spec.py`, no Node dependency, and no second task tracker alongside beads.

## Considered Options

### Option A — Three artefacts with distinct purposes: specs (changes), `specs/current/` (behaviour), ADRs (decisions)

* Good: Each question has one place to look: "what are we building?" (spec), "how does it behave now?" (`specs/current/`), "why did we choose this?" (ADR).
* Good: Behaviour records are built automatically from reviewed specs, so they stay current without separate authoring.
* Good: Structured WHEN/THEN scenarios can be checked mechanically, from spec validation through planning to tester evidence.
* Good: ADRs and specs share the status-folder pattern and hash-derived IDs, so operators do not learn a second mental model.
* Bad: Three artefact types and two CLIs (`spec.py`, `takt adr`) to learn.

### Option B — Keep everything in specs (edit done specs to reflect current behaviour)

* Good: One artefact type.
* Bad: Done specs describe a change at a point in time, and rewriting them destroys that history.
* Bad: A capability's behaviour stays scattered across many specs, and decisions without an owning spec still have no home.

### Option C — Record decisions and conventions in CLAUDE.md

* Good: Agents load it automatically.
* Bad: It grows without bound, has no lifecycle, and loses superseded reasoning to git history (spec-92d4fb81).

### Option D — Adopt OpenSpec tooling wholesale

* Good: An existing, documented workflow.
* Bad: Adds a Node dependency and a parallel change tracker that would compete with beads.

## Decision

**Each kind of knowledge has exactly one home:**

1. **Specs are forward-looking change documents.** They live in `specs/<status>/` and are managed only through `spec.py` (the `skill-spec-management` skill): `create`, `set status`, `validate`, and so on. Operators and agents do not use `mv` or hand-edit managed frontmatter. A spec may carry an optional `## Requirements` section of deltas, `### ADDED|MODIFIED|REMOVED: <capability> / <requirement>`. ADDED and MODIFIED entries need a `SHALL`/`MUST` sentence and `#### Scenario:` blocks with `- **WHEN**`/`- **THEN**` lines. REMOVED entries need a `Reason:` line.
2. **`specs/current/<capability>.md` is the living record of current behaviour.** `spec.py set status done` validates the deltas against `specs/current/` and merges them, writing a `<!-- source: spec-<id> -->` line on each requirement. If any delta is invalid it exits 1 and changes nothing. `current` is a reserved status, and `specs/current/` is never listed as specs.
3. **Scenarios are carried through to tests.** `takt plan` (`spec_requirements.py`, `_check_scenario_coverage`) refuses to persist with `--write`/`--from-file` unless every ADDED/MODIFIED scenario is covered by a tester criterion `Scenario: <capability> / <requirement> / <scenario>`. `--allow-uncovered-scenarios` downgrades this to a warning. Tester `met` claims for such criteria must cite a `path::test_name`.
4. **ADRs are backward-looking records of cross-cutting decisions** that have no single owning spec or that outlive one. They live in `adr/<status>/` and are managed through `takt adr`. Corrections happen by supersession, not edits.

## Consequences

### Positive

* Agents and operators can read how a capability behaves today without reconstructing it from history.
* Acceptance criteria tied to scenarios become verifiable end to end: validation, plan-time coverage, and tester citation checks.
* Settled decisions stop being re-argued in later sessions. CLAUDE.md can point to ADRs instead of accumulating rationale.
* Spec frontmatter and folder location stay in sync because only `spec.py` moves files.

### Negative

* Authors must judge whether something is a spec, a requirement delta or an ADR, and three tools exist for three artefacts.
* `## Requirements` is optional, so `specs/current/` fills in only as new specs complete. Earlier behaviour is not back-filled.
* Concurrent MODIFIED deltas to the same requirement are last-writer-wins. The `source:` comment makes this auditable but does not prevent it.
* Neither `specs/current/` nor approved ADRs are loaded into agent context automatically yet. They must be cited explicitly in bead descriptions.
