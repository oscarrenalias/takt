---
id: ADR-0e71d9d2
title: File-scope conflicts are checked at schedule time, not locked at run time
status: approved
created_at: '2026-11-08T15:47:38.715560+00:00'
authors:
- Renalias, Oscar
description: The scheduler defers beads whose declared file scopes overlap within
  a feature tree; no runtime file locks.
accepted_at: '2026-11-08T15:52:49.296672+00:00'
superseded_at: null
superseded_by: null
supersedes: []
tags:
- scheduler
- concurrency
related_specs:
- spec-ae687703
- spec-1eb14a19
- spec-1b99116b
related_beads: []
review_after: null
---

# File-scope conflicts are checked at schedule time, not locked at run time

## Summary

> In the context of several workers sharing one feature worktree, facing the risk of two beads editing the same files at once, we decided that the scheduler compares declared file scopes (`touched_files`, `expected_files`, `expected_globs`) when it dispatches and defers overlapping beads in the same feature tree, with no file locking while beads run and an opt-in `serialize_within_feature_tree` for toolchains that lock the whole worktree, to achieve deterministic, explainable parallelism from bead state alone, accepting that protection is only as good as the declared scope.

## Context

`specs/done/conflict-aware-scheduling.md` added conflict awareness so the scheduler could run beads in parallel "without assigning overlapping work". It required decisions to be "deterministic", "explainable from bead state alone", and "Git-native and repository-backed". Its implementation notes said to "prefer extending the existing bead schema instead of creating a separate locking system" and to "keep conflict checks in the scheduler". Distributed or multi-machine locking, symbol-level tracking and automatic resolution were out of scope. It also required that beads without scope be "handled conservatively".

Two later specs refined the rule:

* `specs/done/scheduler-serialize-within-feature-tree.md`. Swift/SPM/Xcode/Simulator toolchains lock at the workspace level, which file-scope checks cannot see. The spec added `SchedulerConfig.serialize_within_feature_tree` (YAML `common.scheduler.serialize_within_feature_tree`, default `False`). Auto-detection and per-feature overrides were explicitly rejected.
* `specs/done/cross-feature-root-file-scope-conflict-false-positive.md`. Beads in different feature trees run in different worktrees and cannot collide while running. Overlap between them is a merge-time concern handled by `takt merge`. The explicit-scope check was limited to the same feature tree.

## Considered Options

### Option A — Static scope-overlap check at dispatch time (chosen)

* Good: Decisions come only from persisted bead fields, so they are deterministic and each deferral has a readable `block_reason`.
* Good: No lock files, lease files per path or cleanup when workers crash.
* Good: Unrelated beads in the same tree still run in parallel.
* Bad: Relies on planner-declared scope. A bead that edits files outside its `expected_files` is not caught while it runs.
* Bad: Glob overlap is approximated by prefix comparison (`_globs_overlap()` / `_glob_prefix()`), which can over-defer.

### Option B — Runtime file locking (agents acquire locks on files before editing)

* Good: Catches edits outside the declared scope.
* Bad: Workers are external CLI agents (Codex, Claude Code) whose individual file writes takt cannot intercept.
* Bad: Needs a separate lock store, crash recovery and deadlock handling; the spec asked for none of these.

### Option C — Always serialise beads within a feature tree

* Good: Simple and fully safe within a worktree.
* Bad: Gives up all parallelism inside a feature. It is kept as an opt-in (Option D) for toolchains that need it, not as the default.

### Option D — Opt-in worktree-level serialisation flag (chosen, as an escape hatch)

* Good: Covers workspace-wide toolchain locks that file scope cannot express.
* Good: Off by default, so other projects see no change.
* Bad: Loses within-tree packing for the projects that enable it.

## Decision

**File-scope conflicts are detected statically by the scheduler when it dispatches, never through runtime locks.** In `scheduler/core.py`, `_select_beads_for_dispatch()` compares each ready bead against active, in-flight and already-selected beads using `_beads_conflict()`. A conflicting bead gets a `deferred` event with a reason from `_find_conflict_reason()`. The rules:

* Conflicts only apply between two `MUTATING_AGENTS` beads (`developer`, `tester`, `documentation`, `defect`) in the **same feature tree**. Different feature trees never conflict at dispatch time.
* The best available scope is used, in order: `touched_files`, then `expected_files`, then `expected_globs` (`Bead.scope_source()`).
* If either bead has no scope, they conflict ("worktree in use by in-progress <id> (no file scope defined)").
* With `serialize_within_feature_tree: true`, any two mutating beads in the same tree conflict ("worktree serialization enabled — waiting on in-progress <id>").
* Otherwise they conflict only if their scopes overlap ("file-scope conflict with in-progress <id>").

## Consequences

### Positive

* Every deferral can be explained from bead state and is recorded in `execution_history`.
* Parallelism across feature trees is never limited by file scope.
* Toolchain-locked stacks have a one-line config fix.

### Negative

* Out-of-scope edits by a worker are not prevented while it runs; they show up later through `touched_files`, review, or merge conflicts.
* Poor or missing scope from the planner either over-serialises (no scope) or under-protects (wrong scope).
* Overlap between feature trees is caught only at merge time, via the merge-conflict bead flow.
