---
name: Auto-commit bead state on every save
id: spec-b518b2b6
description: Commit bead JSON to git on every save so bead state is always in git,
  eliminating merge failures caused by uncommitted bead files.
dependencies: null
priority: high
complexity: small
status: planned
tags:
- storage
- git
- reliability
scope:
  in: storage.py, gitutils.py, tests
  out: scheduler.py, cli.py, tui.py, bead data model
feature_root_id: B-fa1d9a85
---

# Auto-commit bead state on every save

## Objective

Bead JSON files change throughout a feature's lifetime — created, leased, transitioned, corrected — but are only committed to git when something forces it (a manual commit, or a merge failing because of dirty state). This causes two recurring problems: merge preflight fails with "your local changes would be overwritten", and bead history is invisible to `git log`. The fix is to commit the bead JSON file on every write, so bead state is always in git.

---

## Problems to Fix

1. **Merge failures from uncommitted bead state** — `orchestrator merge` runs `git merge --no-ff`, which fails if any tracked or soon-to-be-tracked bead JSON file has uncommitted changes in the main worktree. Today this requires operator intervention to stage and commit bead files manually.
2. **Bead history not in git** — bead transitions (created → ready → in_progress → done) happen entirely in JSON files that are not committed until something external forces it. The git log shows no trace of the workflow until a merge.
3. **No single fix point** — bead writes happen from multiple call sites (`save_bead`, `_cleanup_deleted_dependency_references`, `_record_missing_dependency_warning`). All of them need to commit, but they all route through `_write_bead`.

---

## Changes

### 1. Add `_git_commit_bead(path)` to `RepositoryStorage` in `storage.py`

A small private method that stages and commits a single bead file after every write:

```python
def _git_commit_bead(self, path: Path, bead: Bead) -> None:
    """Stage and commit a single bead file. No-op if git is unavailable or nothing to commit."""
    try:
        rel = path.relative_to(self.root)
        subprocess.run(["git", "add", str(rel)], cwd=self.root, check=True, capture_output=True)
        msg = f"[bead] {bead.bead_id}: {bead.status}"
        subprocess.run(["git", "commit", "-m", msg], cwd=self.root, check=True, capture_output=True)
    except subprocess.CalledProcessError:
        pass  # not a git repo, nothing staged, or git unavailable — ignore silently
```

The method is best-effort: if git is not available, the repo is not initialised, or there is nothing to commit (file unchanged), it silently does nothing. Bead writes must never fail because of a git error.

### 2. Call `_git_commit_bead` from `_write_bead` in `storage.py`

`_write_bead` is the single chokepoint for all bead writes. Adding the commit call here covers every write path:

```python
def _write_bead(self, bead: Bead) -> None:
    self.initialize()
    path = self.bead_path(bead.bead_id)
    tmp_path = path.with_suffix(f"{path.suffix}.tmp")
    tmp_path.write_text(json.dumps(bead.to_dict(), indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(path)
    self._git_commit_bead(path, bead)   # ← new
```

### 3. Add a threading lock to serialise git operations in `storage.py`

The scheduler uses `ThreadPoolExecutor` and calls `_write_bead` from multiple worker threads concurrently. `git add` + `git commit` are not safe to run in parallel. Add a module-level or instance-level lock:

```python
import threading

class RepositoryStorage:
    _git_lock = threading.Lock()

    def _git_commit_bead(self, path: Path, bead: Bead) -> None:
        with self._git_lock:
            try:
                ...
            except subprocess.CalledProcessError:
                pass
```

The lock only serialises git operations, not bead writes themselves — the `tmp_path.replace(path)` atomic rename is unaffected.

### 4. Commit message format

```
[bead] B-abc12345: in_progress
[bead] B-abc12345: done
[bead] B-abc12345: blocked
[bead] B-abc12345: created (developer)
```

For creation, include the agent type. For other transitions, just the status. Keep messages short — `git log --oneline` should be scannable.

To distinguish creation from update, check whether the bead file existed before the write:

```python
msg = (
    f"[bead] {bead.bead_id}: created ({bead.agent_type})"
    if not path.exists()   # checked before tmp_path.replace(path)
    else f"[bead] {bead.bead_id}: {bead.status}"
)
```

---

## Files to Modify

| File | Change |
|---|---|
| `src/codex_orchestrator/storage.py` | Add `_git_commit_bead()`, threading lock, call from `_write_bead()` |
| `tests/test_orchestrator.py` | Verify bead writes produce git commits; verify lock serialises concurrent writes |

---

## Acceptance Criteria

- Every call to `_write_bead` produces a git commit containing the bead's JSON file
- Commit message format is `[bead] <bead_id>: <status>` (or `created (<agent_type>)` for new beads)
- Concurrent bead writes from multiple scheduler workers do not produce git errors or corrupt commits
- `_git_commit_bead` is best-effort: no exception propagates if git fails or there is nothing to commit
- After running a full scheduler cycle, `git log --oneline` shows one commit per bead transition
- `orchestrator merge` no longer fails with "your local changes would be overwritten by merge" due to bead files
- All existing tests pass

---

## Pending Decisions

### 1. Git author identity
Auto-commits will use whatever `git config user.name` / `user.email` is set in the repo. Should the commit author be set explicitly (e.g. `Orchestrator <orchestrator@local>`)? **Recommendation: leave as-is — use the repo's configured identity. Overriding it would create confusing attribution.**

### 2. Commit per-write vs batch per cycle
Committing on every single write means a bead that goes through several rapid transitions (created → lease acquired → done + followups created) produces 3–5 commits in quick succession. Should transitions be batched? **Recommendation: commit per write for simplicity and auditability. The git log noise is acceptable and matches the existing pattern of one commit per agent run.**

### 3. Deleted beads
`delete_bead` removes the JSON file and calls `git rm`. Should deletion also be committed immediately? **Yes — same principle. Add a `_git_commit_deletion(path, bead_id)` alongside `_git_commit_bead`.**
