---
id: ADR-08d77cde
title: Merges go only through takt merge, with gates and a recorded approver
status: approved
created_at: '2026-11-08T15:47:40.531577+00:00'
authors:
- Renalias, Oscar
description: Feature branches reach main only via takt merge, with main-into-feature
  preflight, test gate, agent-resolved merge-conflict beads, approver, audit snapshot
  and merge record.
accepted_at: '2026-11-08T15:52:50.029756+00:00'
superseded_at: null
superseded_by: null
supersedes: []
tags:
- merge
- governance
- git
related_specs:
- spec-c1020c92
- spec-4967f1b0
related_beads: []
review_after: null
---

# Merges go only through takt merge, with gates and a recorded approver

## Summary

> In the context of AI-built feature branches landing on a shared `main`, facing conflicts surfacing on `main`, broken suites merged unchecked, conflicts handed back to the operator and merges that left no record, we decided that feature branches reach `main` only through `takt merge`, which syncs `main` into the branch, runs the test gate, turns failures into agent-resolved `merge-conflict` beads and records an approver, audit snapshot and merge record, to achieve safe, accountable merges without manual git work, accepting a slower multi-step merge loop and a self-asserted approver identity.

## Context

The original `merge` command ran a bare `git merge --no-ff` with no pre-flight checks (spec-c1020c92). When several features touched the same files, conflicts appeared on `main` instead of in the feature branch, and nothing ran the tests before the merge commit. A plain conflict handed back to the operator was no better than raw git, and made the takt command easy to bypass.

Later, spec-4967f1b0 found that `command_merge` (`src/agent_takt/cli/commands/merge.py`) recorded nothing: no approver, no commit SHAs, no test outcome, no event, and no trace of `--skip-tests` or `--skip-rebase`. The governance data takt was collecting (verdicts, `criteria_status`, assumptions) had no point where a person accepted it.

takt is self-hosting and runs agents unattended, so the merge is the one point where a human decision is recorded.

## Decision Drivers

* Conflicts must surface on the feature branch, where context exists, not on `main`.
* No feature lands on `main` without the configured `common.test_command` passing, unless the skip is explicit and recorded.
* Conflict and test-fix work belongs to agents via the scheduler; operators should not hand-edit git state.
* Every merge must answer "who accepted this, on what evidence, at which commits?"
* `takt merge` stays non-interactive.

## Considered Options

### Option A — Thin `takt merge` that gates, delegates fixes to beads, and records the merge

* Good: the scheduler stays the single place agents run; `takt merge` only detects problems and files a `merge-conflict` bead (`_emit_merge_conflict_bead`).
* Good: merging `main` into the feature branch (`WorktreeManager.merge_main_into_branch`) resolves everything in one pass, instead of replaying many small bead commits.
* Good: approver, SHAs, gate outcomes and a hashed audit snapshot give a durable record (`metadata.merge_records`, `.takt/audits/<feature_root_id>/`).
* Bad: a conflicted merge becomes a loop (`takt merge` → `takt run` → `takt merge`) and may repeat if `main` keeps moving.

### Option B — Rebase the feature branch onto `main` before merging

* Good: linear history.
* Bad: feature branches carry one commit per bead run; rebase replays each one and can produce many conflict rounds for an agent (rejected in spec-c1020c92, pending decision 1).

### Option C — Resolve conflicts inline inside `takt merge` (agent or operator)

* Good: one command finishes the job.
* Bad: puts agent execution outside the scheduler, bypassing leases, guardrails and corrective accounting.
* Bad: operator hand-resolution bypasses the pipeline; CLAUDE.md records that manual git operations corrupt state and create harder problems than the conflicts they fix.

### Option D — Plain `git merge` with conventions and documentation

* Good: no tooling.
* Bad: no test gate, no conflict handling, no record; exactly the state spec-c1020c92 and spec-4967f1b0 set out to fix.

## Decision

**Feature branches are merged into `main` only with `takt merge <bead_id>`. Running `git merge` on a feature branch by hand, or resolving merge conflicts or editing worktrees manually, is forbidden unless the user explicitly authorises it.**

`command_merge` proceeds as follows:

1. Resolve the approver (`--approver "Name <email>"`, else git `user.name`/`user.email`); exit 1 before any gate if neither is available.
2. Refuse to continue while an unresolved `merge-conflict` bead exists for the feature root.
3. Preflight: merge `main` into the feature branch. On conflict, abort, record `merge_gate_failed` (`gate: preflight`) and create a `merge-conflict` bead (`agent_type: developer`, `bead_type: merge-conflict`) with the conflicted files and diff.
4. Test gate: run `config.common.test_command` with `test_timeout_seconds`. On failure, record `merge_gate_failed` (`gate: tests`) and create a `merge-conflict` bead describing the failure.
5. Print the pre-merge summary, write the audit snapshot (`.json` + `.md`) via `build_feature_audit` in `src/agent_takt/audit.py`, run `git merge --no-ff`, then append a merge record (approver, `approver_source`, SHAs before/after, `preflight`, `test_gate`, `audit_path`, `audit_sha256`) to the feature root, add a `merged` history entry and log `feature_merged`.

`merge-conflict` beads are resolved by the scheduler. Their count per feature root is capped by `scheduler.max_corrective_attempts`; past the cap `takt merge` stops and asks for operator intervention. `--skip-rebase` and `--skip-tests` stay available, but they are recorded (`skipped`, `not_run`, `not_configured`) and highlighted in the summary.

## Consequences

### Positive

* Conflicts and broken suites are caught on the feature branch and fixed by agents with full context.
* Every merge carries an approver, the exact SHAs and the gate outcomes; skipped gates are visible after the fact.
* The audit snapshot's SHA-256 in the merge record makes later edits to the snapshot detectable.
* `merge-conflict` beads are distinct from `corrective` beads in listings and the TUI.

### Negative

* Merging is slower and may need several `takt merge` / `takt run` rounds when `main` is busy.
* The approver is self-asserted from git config; the record establishes accountability, not authentication.
* Audit snapshots live under `.takt/audits/` and are not committed, so they rely on local retention.
* The TUI cannot drive the merge inline; it directs the operator to the CLI.
