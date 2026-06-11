---
name: Make commit_all robust to ignored .takt/beads/ paths
id: spec-9173b2da
description: Fix WorktreeManager.commit_all() so it tolerates .takt/beads/ being in .gitignore — the documented quiet-mode recipe — rather than failing the worker auto-commit and stranding real code changes.
dependencies: null
priority: high
complexity: small
status: done
tags:
- storage
- gitutils
- quiet-mode
- bugfix
scope:
  in: null
  out: null
feature_root_id: null
---
# Make commit_all robust to ignored .takt/beads/ paths

## Objective

When a project opts into quiet mode (`common.commit_bead_state: false`) and follows the cleanup recipe documented in `CLAUDE.md` — adding `.takt/beads/` to `.gitignore` and untracking the directory — worker auto-commits fail with:

```
Auto-commit failed: The following paths are ignored by one of your .gitignore files:
.takt/beads
hint: Use -f if you really want to add them.
```

The bead is marked `blocked`, its real code changes are stranded in the shared feature worktree, and the operator has to manually commit + requeue. The failure is misleading: the worker succeeded and produced valid output; only the orchestrator's commit step broke.

This is an internal contradiction in takt: `takt init` does not add `.takt/beads/` to the scaffolded `.gitignore`, but the documented quiet-mode cleanup recipe **does**, and `commit_all()` only handles the first case. Make `commit_all()` robust to both configurations so the documented recipe is actually safe to follow.

### How this regression came about

| Date | Commit | Effect |
|---|---|---|
| 2026-05-06 | `2332acce` "Convert commit_all staging from `git add -A` to pathspec exclusion" | `commit_all` switched to `:/` + `:(exclude).takt/beads/**`. The previous `git add -A` silently respected `.gitignore`; the new pathspec does not. Latent bug planted — but unreachable because no documented configuration put `.takt/beads/` in `.gitignore`. |
| 2026-05-28 | `e16a87bd` (merge of `feature/b-f5d14de1`, the `commit_bead_state` toggle) | `CLAUDE.md` started instructing operators to add `.takt/beads/` to `.gitignore` as part of the quiet-mode cleanup recipe. The latent pathspec bug became reachable. |

The quiet-mode feature did not write the broken code, but it created the supported configuration that triggers it. The previous `git add -A` behaviour handled the ignored case correctly but had its own failure mode — it would silently stage untracked bead-state files when they were *not* in `.gitignore`. The fix proposed here (enumerate-and-filter) is the only approach that handles both configurations explicitly rather than relying on git's pathspec/`.gitignore` interaction.

## Problems to Fix

1. **`commit_all()` uses a pathspec that doesn't tolerate ignored `.takt/beads/`.** The current invocation in `src/agent_takt/gitutils.py` (around line 284–300) is roughly:

   ```python
   ["git", "add", "--", ":/", ":(exclude).takt/beads/**", ":(exclude).takt/beads/"]
   ```

   The `:(exclude)` pathspecs suppress *adding* those paths, but git still scans the untracked `.takt/beads/*.json` files first, sees them in `.gitignore`, and exits non-zero with the "paths are ignored" warning. The entire `git add` fails, so the legitimate worker changes never reach the index.

2. **Misleading block reason.** `src/agent_takt/scheduler/finalize.py` (~line 187) catches the resulting `GitError` and sets `bead.block_reason = f"Auto-commit failed: {exc}"`. The bead is marked `blocked` and the operator sees a failure that looks like a worker fault. A spurious `-corrective` bead may be spawned and hit the same wall.

3. **Inconsistency between init scaffold and cleanup recipe.**
   - `src/agent_takt/onboarding/scaffold.py:28` (`_GITIGNORE_ENTRIES`) does **not** include `.takt/beads/`.
   - `CLAUDE.md` → "Cleaning bead-state history in an existing repo" instructs operators to **add** `.takt/beads/` to `.gitignore`.
   - `commit_all()` only works correctly for the first configuration.

4. **Recovery noise.** Each ignored-path failure produces a blocked bead, often a spurious `-corrective`, and forces the operator to delete both and commit the worktree manually before requeuing the original.

## Changes

Rewrite `WorktreeManager.commit_all()` in `src/agent_takt/gitutils.py` so that it stages paths **explicitly** instead of leaning on git's `:/` + `:(exclude)` semantics. The new flow:

1. Enumerate worktree changes with `git status --porcelain=v1 --untracked-files=all -z` (NUL-delimited to handle paths with spaces or newlines).
2. Parse the result into a list of relative paths. For each entry, derive the path that needs staging (handle rename/copy entries by taking the destination path).
3. **Filter out** any path that starts with `.takt/beads/` — bead state must never be staged by worker commits.
4. If the filtered list is empty, return `None` exactly as today (no commit attempted, no event emitted).
5. Otherwise, invoke `git add -- <path1> <path2> …` with the filtered list. Use `xargs -0`-style chunking only if path counts grow large (rare). No `:/` shotgun, no `:(exclude)` pathspec.
6. Proceed to `git commit` exactly as today.

Behavior contract:
- Identical to today when `.takt/beads/` is **not** in `.gitignore` (the `takt init` default): real worker changes commit; bead-state files are present in the worktree but never staged because the filter strips them.
- Fixed when `.takt/beads/` **is** in `.gitignore` (the documented quiet-mode recipe): the `git status --porcelain` enumeration already respects `.gitignore` for the listing portion of the output (status flags ignored paths as `!!` only when `--ignored` is passed, which we will not pass), so ignored files simply don't appear in the parse output. The subsequent explicit `git add <paths>` only references non-ignored, real changes — no warning, no non-zero exit.

Additional defensive notes:
- Preserve the existing `_clean_untracked_bead_state()` invocation order — that helper continues to provide its safety-net role.
- Continue to emit `git_commit_failed` execution events when the actual `git commit` step fails for unrelated reasons (e.g., hook rejection); this spec does not change that path.
- Do **not** invoke `git add -f`; forcing ignored paths would re-introduce the bead-state pollution we just removed.

## Files to Modify

| File | Change |
|---|---|
| `src/agent_takt/gitutils.py` | Rewrite `WorktreeManager.commit_all()` to enumerate-and-filter rather than rely on exclude pathspec. Keep `_clean_untracked_bead_state()` unchanged. |
| `tests/test_gitutils.py` (or nearest existing) | Add regression tests for both gitignore configurations and the empty-changes case. |
| `CLAUDE.md` (Conventions → "Bead state exclusion" bullet) | One-line update noting that `commit_all()` now enumerates explicit paths rather than using `:/` + `:(exclude)`. |

## Acceptance Criteria

- **Quiet-mode recipe works end-to-end.** In a repo with `common.commit_bead_state: false` and `.takt/beads/` in `.gitignore`, a developer bead that writes a real source file completes, `commit_all()` succeeds, the resulting commit contains the source file but no `.takt/beads/` entries, and the bead status transitions to `done` (never `blocked` with `Auto-commit failed: …`).
- **Default scaffold still works.** In a repo created by `takt init` (where `.takt/beads/` is not in `.gitignore`), behavior is unchanged: real code commits as `[takt] <bead-id>: <summary>`, bead-state files are not staged, no `[bead]` commits are produced.
- **No real changes → no commit attempted.** When the filtered path list is empty, `commit_all()` returns `None`, no `git commit` runs, and no `git_commit_failed` event is appended.
- **Tests cover both gitignore configurations** plus the empty-changes path. Tests use a real on-disk git repo (no mocking) so the gitignore + pathspec interaction is exercised by real git.
- **No regression in existing scheduler/storage tests.** Full `uv run pytest tests/ -n auto -q` passes.

## Pending Decisions

- **Should `takt init` start scaffolding `.takt/beads/` into the generated `.gitignore` when `commit_bead_state: false`?** This would close the init/cleanup inconsistency at the source rather than just making `commit_all()` tolerate it. Deferred to a separate spec — out of scope here.
- **Should the blocked-bead path also be quieter?** When `commit_all()` does fail for an unrelated git reason, the current "Auto-commit failed: …" block reason is fine; no change proposed here.
