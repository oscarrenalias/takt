---
id: ADR-0dce6ec5
title: One git worktree and branch per feature tree
status: approved
created_at: '2026-11-08T15:47:37.491479+00:00'
authors:
- Renalias, Oscar
description: All beads of a feature tree share .takt/worktrees/<feature_root_id> and
  feature/<root lowercased>; paths are deterministic.
accepted_at: '2026-11-08T15:52:48.171502+00:00'
superseded_at: null
superseded_by: null
supersedes: []
tags:
- foundational
- git
- scheduler
related_specs:
- spec-fd5f17b0
related_beads: []
review_after: null
---

# One git worktree and branch per feature tree

## Summary

> In the context of running several AI workers on one repository, facing the need to isolate unrelated features from each other while letting the beads of a single feature build on each other's changes, we decided that every feature tree gets exactly one git worktree at `.takt/worktrees/<feature_root_id>` on one branch `feature/<feature_root_id lowercased>`, shared by all of its beads, to achieve cheap isolation between features, a single mergeable unit per feature and paths anyone can derive from the feature root ID, accepting that beads inside one tree share a working directory and must be kept apart by the scheduler.

## Context

The MVP spec (`specs/done/beads_orchestration_mvp.md`) set out "isolated workspaces for each agent, even in the same source code tree (via git worktrees)" and gave the scheduler the job "Create isolated git worktrees", with the constraint "No shared working directories between 'epic' beads (beads and the sub-beads still share a folder)".

The first implementation keyed worktrees by bead: `WorktreeManager.worktree_path(bead_id)` and `ensure_worktree(bead_id, ...)`. Commit `8e63befa` ("Share worktrees across feature sub-beads") changed both to take a `feature_root_id`, added a per-feature lock (`_worktree_locks`), and made `takt merge` resolve the branch from the feature root's `execution_branch_name`. Per-bead worktrees did not fit how beads work: a feature is split into developer, tester, documentation and review beads that each need to see the code the earlier ones wrote, and the feature is merged to `main` as one unit.

Today the rule lives in a few places:

* `RepositoryStorage.default_execution_branch_name()` (`storage.py`) returns `f"feature/{feature_root_id.lower()}"`.
* `RepositoryStorage.create_bead()` sets `execution_branch_name` and `execution_worktree_path` (`worktrees_dir / feature_root_id`) on every bead in a tree, inheriting them from the parent where set.
* `BeadExecutor` (`scheduler/execution.py`) calls `self.worktrees.ensure_worktree(feature_root_id, branch_name)` before running a bead, records the path on the bead (and on `worktree_path` for `MUTATING_AGENTS`), and copies it back onto the feature root bead.
* `WorktreeManager.ensure_worktree()` (`gitutils.py`) creates the worktree under a per-feature lock, reusing the branch if it already exists or branching from the current `HEAD` otherwise.
* `takt bead delete` on a feature root removes that worktree and `feature/<id>` branch; `audit.py` derives the branch name the same way.

## Considered Options

### Option A — One worktree and branch per feature tree (chosen)

* Good: Beads in a tree see each other's committed and uncommitted work, so tester, docs and review beads can operate on the developer's output directly.
* Good: One branch per feature is the merge unit for `takt merge`; the preflight, test gate and merge record all attach to one branch and one feature root.
* Good: Paths are deterministic. Given `B-a7bc3f91`, the worktree is `.takt/worktrees/B-a7bc3f91/` and the branch is `feature/b-a7bc3f91`; nobody needs to list `.takt/worktrees/`.
* Good: Features stay fully isolated from each other, so unrelated feature trees can run in parallel.
* Bad: Beads in the same tree share a directory and can collide; this needs the schedule-time file-scope conflict check (see the separate ADR on file-scope conflicts).
* Bad: A long-running feature collects every bead's work on one branch, which makes the final merge larger.

### Option B — One worktree and branch per bead

* Good: Strongest isolation; no two workers ever share a directory.
* Bad: Each downstream bead (test, docs, review) would need the upstream branch merged in before it could start, which adds a merge step per dependency edge.
* Bad: Many short-lived branches per feature, and no single natural branch to merge to `main`.
* Bad: This was the original implementation and was replaced by commit `8e63befa` for these reasons.

### Option C — All workers in the main checkout

* Good: No worktree management at all.
* Bad: No isolation between features; one broken feature blocks `main` and every other feature.
* Bad: The scheduler's own bead-state writes on `main` would mix with worker edits.

## Decision

**A feature tree has exactly one worktree and one branch.** The worktree lives at `.takt/worktrees/<feature_root_id>` (ID not lowercased) and the branch is `feature/<feature_root_id.lower()>`. Both are derived from the feature root ID through `default_execution_branch_name()` and `worktrees_dir / feature_root_id`, and every bead in the tree runs there. Agents and operators should derive the path from the feature root ID, not by listing the worktrees directory. Beads without a feature root that belong to a mutating agent type are blocked instead of running in the main checkout. Concurrency inside a tree is the scheduler's job, not the worktree layout's.

## Consequences

### Positive

* The feature branch is the single thing that `takt merge` preflights, tests and merges, and the merge record lands on the feature root bead.
* Downstream beads need no merge step to see upstream work.
* Worktree and branch locations are predictable for agents, operators, `takt-fleet` and tooling.
* Deleting a feature root cleans up exactly one worktree and one branch.

### Negative

* Same-tree parallelism depends on accurate `expected_files` / `expected_globs`, and toolchains with workspace-wide locks need `serialize_within_feature_tree`.
* Commits from concurrent beads in one worktree have to be serialised (`WorktreeManager.commit_all()` takes a per-worktree lock) so one bead does not commit another's staged changes.
* The worktree checks out a copy of `.takt/beads/`, which brings its own isolation problems (see the ADR on bead state and feature branches).
* Beads created inside an already-merged feature tree reuse that tree's branch and need another merge cycle.
