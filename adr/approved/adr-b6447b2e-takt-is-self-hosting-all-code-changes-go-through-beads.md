---
id: ADR-b6447b2e
title: Takt is self-hosting; all code changes go through beads
status: approved
created_at: '2026-07-01T07:40:53.367444+00:00'
authors:
- Renalias, Oscar
description: Feature work, bug fixes, and refactors in the takt codebase must be filed
  as beads or defect beads and executed by the takt scheduler — no manual source edits.
accepted_at: '2026-07-01T09:54:40.804403+00:00'
superseded_at: null
superseded_by: null
supersedes: []
tags:
- foundational
- policy
- governance
related_specs: []
related_beads: []
review_after: null
---

# Takt is self-hosting; all code changes go through beads

## Summary

In the context of building a multi-agent orchestrator whose value proposition is disciplined AI-driven development, facing the temptation for maintainers to bypass the pipeline for "quick" manual fixes, we decided that all code changes to the takt codebase must flow through the bead pipeline, to achieve authentic dogfooding and continuous validation of the tool, accepting slower turnaround for trivial changes and occasional bootstrap awkwardness.

## Context

Takt exists to enforce a structured development process on AI workers: every mutation flows through a spec, is decomposed into beads, executed by role-scoped agents, gated by tests, and reviewed before merge. If the maintainers of takt themselves bypass that pipeline to make manual edits, three things happen:

1. **The pipeline stops being validated by real use.** Bugs in the scheduler, guardrails, or CLI that would surface immediately for a paying user go unnoticed for months.
2. **The commit history diverges into two classes** — `[takt]`-authored commits versus operator-authored ones — with different quality gates. Operators can no longer trust that every merged change went through the same review process.
3. **The self-hosting story becomes marketing rather than reality.** The claim "takt is built with takt" is only true if it's actually true.

The counter-pressure is real: trivial changes (a typo, a one-line config tweak) feel disproportionate to hand off to the pipeline. The bootstrap problem is also real (spec-9173b2da's rename-path fix had to be cherry-picked onto main to unblock the very pipeline that was fixing itself). And documentation changes have blurry boundaries — is `README.md` a "code change"?

## Considered Options

### Option A — Strict self-hosting: all source code goes through beads; only specs, config, and doc-only files may be edited directly

* Good: Full dogfooding — every code change validates the pipeline.
* Good: Uniform quality gate; no two-tier history.
* Good: Forces takt to be usable for its own maintainers, which surfaces UX problems that a purely external user might tolerate as "just the way tools work."
* Bad: Slow for trivial fixes; a one-line typo becomes a spec → plan → run → merge cycle.
* Bad: Bootstrap failures (see spec-9173b2da) require carefully-scoped operator intervention to recover.
* Bad: Doc-vs-code boundary is fuzzy; operators must judge which exception applies.

### Option B — Loose self-hosting: pipeline for features, hand for anything else

* Good: Fast for one-off changes.
* Good: No bootstrap headaches.
* Bad: Two-tier history; loss of dogfooding signal.
* Bad: Slippery slope — "just this one thing" is how process discipline dies.
* Bad: Operators lose the confidence that every landed change was reviewed by the same pipeline they use.

### Option C — Fully manual: takt is developed like any other Python library

* Good: Familiar workflow; no self-referential complexity.
* Bad: Takt would not be dogfooded at all; the tool's assertions about how AI-driven development should work would be untested against its own codebase.
* Bad: A tool whose maintainers don't use it in the way they prescribe is a red flag for external adoption.

## Decision

**All code changes to the takt codebase — including bug fixes, refactors, and hotfixes — must go through the bead pipeline** (spec/plan/run/merge for features; `takt bead create --agent defect --type defect` for standalone bug fixes). The pipeline is the single authoritative merge path for source code.

Explicit exceptions, restricted to documentation and configuration artefacts that the pipeline itself uses:

- `CLAUDE.md` — the project's operator-guide and agent-context file
- `.takt/config.yaml` and other takt configuration files
- Spec files under `specs/` (managed by the spec-management skill)
- ADR files under `adr/` (managed by the ADR CLI)
- One-off, explicitly-authorised bootstrap operations required to recover from pipeline failures — these must be user-approved case-by-case and never become a habit

The `README.md` and `docs/*.md` doc corpus falls **inside** the pipeline scope — those must go through beads.

## Consequences

### Positive

* Every code change is authenticated by the same review, test, and merge machinery that external users depend on.
* Real bugs in the pipeline surface immediately, because the maintainers hit them first.
* The self-hosting claim is genuinely true, not aspirational.
* Discipline is preserved: no operator has license to bypass the pipeline "just this once."
* The commit history is uniform in provenance and gate.

### Negative

* Trivial changes cost more agent budget and clock time than a hand edit would.
* Bootstrap failures (where a bug in the pipeline blocks the pipeline from fixing itself) require careful, explicit operator intervention with clear scope.
* New maintainers experience friction that a conventional Python project wouldn't have. That friction is a feature, not a bug — but it's real.
