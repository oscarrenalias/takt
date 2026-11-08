---
id: ADR-44e7453e
title: Bead state never travels on feature branches
status: draft
created_at: 2026-11-08T15:47:38.012229+00:00
authors: ['Renalias, Oscar']
description: Worker commits never stage .takt/beads/; merges and finalize defend against bead-state leakage at every layer.
tags: [git, storage, merge]
related_specs: [spec-5f05ff7a, spec-9173b2da, spec-d1a1398c]
related_beads: 
supersedes: 
---
# Bead state never travels on feature branches

## Summary

> In the context of feature worktrees that check out a copy of `.takt/beads/` while the scheduler keeps writing bead state on `main`, facing repeated merge conflicts on bead JSON and, later, silent loss of `main`'s bead state during merges, we decided that worker commits on feature branches never stage `.takt/beads/` and that every layer (worktree exclude, commit staging, merge attributes, merge-time resolution, finalize-time path stripping) enforces this, to achieve feature branches that carry only code and merges that never damage bead state, accepting that bead files stay tracked in the feature branch index and the protection is spread across several mechanisms.

## Context

Bead state under `.takt/beads/` belongs to the orchestrator and is written from the main checkout by `RepositoryStorage`. Every feature worktree also checks out a copy of those files, because they are tracked on `main` in self-hosting repos.

The decision took three attempts:

1. **Merge conflicts** (`specs/done/exclude-bead-state-from-feature-branches-to-eliminate-merge-conflicts.md`). Bead JSON diverged on both sides and nearly every `takt merge` hit conflicts under `.takt/beads/`. These produced merge-conflict beads and could use up the corrective budget. The fix added `_write_worktree_exclude()`, an untracking "chore" commit at worktree creation, and `.takt/beads/** merge=ours` in `.gitattributes`.
2. **Data loss** (`specs/done/eliminate-chore-commit-destruction-of-main-bead-state.md`). The chore commit's `git rm --cached` deletions sat in feature branch history and propagated to `main` on merge, which wiped `main`'s bead state. That needed four recovery commits in one session. The chore commit was removed, and `commit_all()` became responsible for keeping bead state out of worker commits.
3. **Ignored-path failures** (`specs/done/make-commitall-robust-to-ignored-taktbeads-paths.md`). The pathspec form `git add -- :/ :(exclude).takt/beads/**` failed with "paths are ignored" when operators added `.takt/beads/` to `.gitignore` as the quiet-mode recipe says. `commit_all()` was rewritten to list and filter the paths to stage.

## Considered Options

### Option A — Keep bead files tracked but never stage them from workers; defend at every layer (chosen)

* Good: No commit on a feature branch ever adds, modifies or deletes bead state, so the final merge has nothing destructive to carry to `main`.
* Good: Works whether or not `.takt/beads/` is in `.gitignore`, because staging uses an explicit filtered path list.
* Good: Multiple independent guards. If one is bypassed, another catches the leak.
* Bad: Several mechanisms in `gitutils.py` and `scheduler/finalize.py` must stay consistent.
* Bad: The feature branch index still holds an inherited and increasingly stale snapshot of bead files.

### Option B — Untrack bead state on the feature branch with a commit at worktree creation

* Good: Simple, and `git add -A` could then be used unchanged.
* Bad: The `git rm --cached` commit stays in branch history and deletes `main`'s bead files on merge. This was observed in production and is why the approach was removed.

### Option C — Rely on `.gitignore` / `info/exclude` and `git add -A`

* Good: No custom staging logic.
* Bad: `info/exclude` cannot override `!.takt/beads/**` in a project `.gitignore`, and tracked files are not affected by ignore rules at all.
* Bad: `git add -A` silently stages untracked bead files when they are not ignored.

### Option D — Accept bead-file merge conflicts and let merge-conflict beads resolve them

* Good: No special handling.
* Bad: The conflicts are mechanical JSON divergence with no semantic content, and they cost scheduler cycles and corrective attempts on nearly every merge.

## Decision

**Bead state is never part of worker work product on a feature branch.** These mechanisms are all required:

* `_write_worktree_exclude()` / `_protect_worktree_bead_state()` write `.takt/beads/` to `.git/worktrees/<name>/info/exclude`. This only hides untracked files from `git status`. No commit is made at worktree creation.
* `WorktreeManager.commit_all()` lists changes via `_stageable_paths()` (`git status --porcelain=v1 --untracked-files=all -z`), drops anything starting with `.takt/beads/`, and runs `git add -- <paths>`. It never uses `git add -A`, `git add -f` or `:(exclude)` pathspecs. If nothing is left after filtering, it returns `None`.
* `.gitattributes` declares `.takt/beads/** merge=ours`, and `_merge_with_bead_state_fallback()` / `_resolve_bead_state_conflicts()` auto-complete a merge only when every remaining conflict is under `.takt/beads/`.
* `merge_main_into_branch()` saves and removes untracked bead files before the preflight merge and restores them in `finally`.
* `_strip_bead_state_paths()` in `scheduler/finalize.py` removes `.takt/beads/` entries from worker-reported `touched_files` / `changed_files`.

No code path may create a commit that untracks or deletes bead state on a feature branch.

## Consequences

### Positive

* Feature merges no longer conflict on bead JSON and no longer delete `main`'s bead state.
* The quiet-mode `.gitignore` recipe and the default `takt init` scaffold both work with the same staging code.
* Bead-state noise does not inflate bead records or followup scope.

### Negative

* The logic is spread across `gitutils.py`, `.gitattributes` and `finalize.py`, so changing one guard means checking all of them.
* `_resolve_bead_state_conflicts()` still resolves DU conflicts with `git rm` and other conflicts with `--ours`, whichever side is merging. Its `direction` parameter is accepted but not acted on yet, so on `main` a DU conflict could still drop a file. With the chore commit gone this case should be rare.
* Feature worktrees carry stale tracked copies of bead files that operators must not read as live state; use the CLI instead.
