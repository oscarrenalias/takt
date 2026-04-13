---
name: Exclude bead state from feature branches to eliminate merge conflicts
id: spec-5f05ff7a
description: Configure worktrees to ignore .takt/beads/ so bead state is only ever committed on main, eliminating the JSON merge conflicts that repeatedly block feature merges.
dependencies:
priority: high
complexity: low
status: draft
tags: []
scope:
  in:
  out:
feature_root_id:
---

# Exclude Bead State from Feature Branches to Eliminate Merge Conflicts

## Objective

Every feature merge currently requires one or more merge-conflict resolution cycles because `.takt/beads/*.json` files accumulate on both the feature branch and `main` independently. The conflicts are never semantic — they are mechanical JSON divergence caused by execution history entries being appended on both sides — but git cannot auto-merge JSON and creates a conflict regardless. This spec fixes that by configuring each worktree to locally ignore `.takt/beads/` at creation time, so feature branches only ever carry code changes and bead state remains exclusively on `main`.

## Problems to Fix

1. **`.takt/beads/` files exist on feature branches.** When a worktree is created from `main`, it checks out whatever bead files existed at that point. Agents and the scheduler then both write to those files, causing the feature branch to diverge from `main` on the same JSON files.

2. **Preflight merge conflicts are almost always bead files.** In practice, every merge we attempt hits an add/add or content conflict in `.takt/beads/`. This creates merge-conflict beads, scheduler cycles, manual workarounds, and corrective-cap exhaustion — all overhead that adds no value.

3. **The corrective cap is reachable on long-lived features.** A feature that takes multiple scheduler runs to complete can exhaust its corrective attempt budget on repeated bead-file conflicts, requiring manual operator intervention.

4. **Git worktrees do not inherit a per-worktree exclude file by default.** There is no mechanism today that prevents feature branches from tracking `.takt/beads/` changes. It must be configured explicitly at worktree creation time.

## Changes

### 1. Write a per-worktree exclude file at worktree creation time

In `src/agent_takt/gitutils.py`, after `git worktree add` succeeds, locate the worktree's git metadata directory and write `.takt/beads/` to its `info/exclude` file.

Git stores per-worktree state at `<repo>/.git/worktrees/<worktree-name>/`. The exclude file for a worktree lives at `<repo>/.git/worktrees/<worktree-name>/info/exclude`.

The worktree name is the basename of the worktree path (e.g. for `.takt/worktrees/B-2a7ec879`, the name is `B-2a7ec879`).

```python
def _write_worktree_exclude(repo_root: Path, worktree_path: Path) -> None:
    """Prevent .takt/beads/ from being tracked on feature branches."""
    worktree_name = worktree_path.name
    exclude_path = repo_root / ".git" / "worktrees" / worktree_name / "info" / "exclude"
    exclude_path.parent.mkdir(parents=True, exist_ok=True)
    existing = exclude_path.read_text() if exclude_path.exists() else ""
    if ".takt/beads/" not in existing:
        with exclude_path.open("a") as f:
            f.write("\n# takt: bead state lives on main only\n.takt/beads/\n")
```

Call `_write_worktree_exclude(repo_root, worktree_path)` immediately after the `git worktree add` subprocess call in the worktree creation function.

### 2. Strip `.takt/beads/` from the worktree's index after creation

Writing the exclude file prevents new bead files from being staged, but files already present in the checked-out branch remain tracked. After writing the exclude file, run:

```bash
git -C <worktree_path> rm -r --cached --ignore-unmatch .takt/beads/
git -C <worktree_path> commit -m "chore: untrack bead state from feature branch [skip ci]" --allow-empty
```

This removes `.takt/beads/` from the feature branch's index without deleting the files on disk (they still exist from the checkout). From this point on, git treats them as untracked and the exclude file prevents them from being re-staged.

Use `--allow-empty` in case there are no bead files to remove (e.g. a fresh repo). Use `--ignore-unmatch` on the `rm` command for the same reason.

### 3. No changes to `storage.py` or scheduler logic

Bead state is already committed from the main project root (on `main`) by `RepositoryStorage._git_commit_bead()`. No changes are needed there — the fix is purely at worktree creation time.

## Files to Modify

| File | Change |
|------|--------|
| `src/agent_takt/gitutils.py` | Add `_write_worktree_exclude()` helper; call it after `git worktree add` |

## Acceptance Criteria

- After `takt run` creates a worktree, `.git/worktrees/<name>/info/exclude` contains `.takt/beads/`.
- `git -C <worktree> status` does not show `.takt/beads/` files as tracked or modified after a bead state update.
- `takt merge` on a feature that has completed all beads succeeds on the first attempt without creating a merge-conflict bead (tested by running a full feature cycle in the test suite).
- Existing bead state on `main` is unaffected — `.takt/beads/` files continue to be committed normally from the main project root.
- All existing tests pass.
- A new test verifies that after worktree creation, `.takt/beads/` is present in the worktree's exclude file.

## Pending Decisions

- **Should existing worktrees be patched retroactively?** A `takt upgrade` hook or a one-off migration could write the exclude file for any worktrees that predate this change. Low priority since existing worktrees are typically short-lived. → Resolved: out of scope for this spec; existing worktrees can be deleted and recreated if needed.
- **Should `.takt/beads/` also be added to the project `.gitattributes` with `merge=ours` as a belt-and-suspenders measure?** This would protect against any path that bypasses the exclude file. → Resolved: yes, add it as a safety net — low effort, belt-and-suspenders.
