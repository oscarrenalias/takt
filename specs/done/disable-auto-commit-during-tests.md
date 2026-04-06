---
name: Disable auto-commit during tests
id: spec-017afb02
description: Add a class-level flag to RepositoryStorage so tests can suppress git
  commits, preventing test suite slowdown from hundreds of subprocess calls.
dependencies: null
priority: high
complexity: small
status: done
tags:
- storage
- testing
- performance
scope:
  in: storage.py, tests/test_orchestrator.py
  out: scheduler.py, cli.py, models.py
feature_root_id: B-49fd235e
---

# Disable auto-commit during tests

## Objective

The auto-commit feature (`_git_commit_bead`, `_git_commit_bead_deletion`) calls `subprocess.run(["git", "add", ...])` and `subprocess.run(["git", "commit", ...])` on every bead write and deletion. In a test environment where the worktree is a real git repository, this makes every test that writes a bead trigger a live git commit. With 1,387 tests and many bead writes per test, this adds hundreds of seconds to the test suite wall-clock time, causing the merge test gate to time out.

The fix is a class-level opt-out flag on `RepositoryStorage` that tests can set to suppress all git commit side-effects without affecting any other storage behavior.

---

## Changes

### 1. Add `_auto_commit` class variable to `RepositoryStorage` in `storage.py`

```python
class RepositoryStorage:
    _git_lock = threading.Lock()
    _auto_commit: bool = True   # set to False in tests to suppress git commits
```

### 2. Guard both commit helpers with `_auto_commit`

In `_git_commit_bead`:

```python
def _git_commit_bead(self, path: Path, bead: Bead) -> None:
    if not RepositoryStorage._auto_commit:
        return
    with self._git_lock:
        ...
```

In `_git_commit_bead_deletion` (added by bead B-e1f3b340):

```python
def _git_commit_bead_deletion(self, path: Path, bead_id: str) -> None:
    if not RepositoryStorage._auto_commit:
        return
    with self._git_lock:
        ...
```

### 3. Disable in `tests/test_orchestrator.py` at module level

At the top of the test module, after imports:

```python
from codex_orchestrator.storage import RepositoryStorage
RepositoryStorage._auto_commit = False
```

This is a module-level assignment. It runs once when the test module is imported and suppresses all git commits for the entire test session. No `setUp`/`tearDown` wiring needed.

The `BeadAutoCommitTests` class is the only place where auto-commit behavior is explicitly tested. Those tests call `_git_commit_bead` directly on a fresh `RepositoryStorage` instance backed by a temp git repo — they do not rely on the module-level flag being True, so they remain unaffected. If needed, `BeadAutoCommitTests.setUp` can re-enable it with `RepositoryStorage._auto_commit = True` and restore in `tearDown`.

---

## Files to Modify

| File | Change |
|---|---|
| `src/codex_orchestrator/storage.py` | Add `_auto_commit = True` class var; guard both `_git_commit_bead` and `_git_commit_bead_deletion` |
| `tests/test_orchestrator.py` | Add `RepositoryStorage._auto_commit = False` at module level; `BeadAutoCommitTests` re-enables it in `setUp`/`tearDown` |

---

## Acceptance Criteria

- `RepositoryStorage._auto_commit = False` suppresses all `subprocess.run` calls in `_git_commit_bead` and `_git_commit_bead_deletion`
- The full test suite runs in under 120 seconds (approximately the pre-auto-commit baseline)
- `BeadAutoCommitTests` still passes: those tests explicitly test git commit behavior and are isolated from the module-level flag
- Default value `_auto_commit = True` means production behavior is unchanged
- All existing tests pass
