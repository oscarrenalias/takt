---
name: Fix intermittent test suite hang at end of pytest run
id: spec-787df768
description: "Tests hang indefinitely near the end of the pytest run under -n auto, causing the takt merge test gate to time out and GitHub Actions to stall."
dependencies: null
priority: high
complexity: null
status: planned
tags: []
scope:
  in: null
  out: null
feature_root_id: null
---

## Objective

`pytest tests/ -n auto -q` hangs indefinitely near the end of the run (~92% progress), causing `takt merge`'s 1800 s test gate timeout to fire and creating spurious merge-conflict beads. The same hang manifests in GitHub Actions CI. Because there is no per-test timeout configured, a single blocking test stalls every xdist worker and the entire run never completes.

## Problems to Fix

1. **No per-test timeout.** `pytest-timeout` is not installed and not configured. A single hanging test blocks the entire run forever with no diagnostic output.

2. **`subprocess.run` in `storage.py` has no timeout.** `_git_commit_bead` and `_git_commit_bead_deletion` call `subprocess.run(["git", ...])` without a `timeout=` argument. If git stalls (GPG agent, credential helper, fs lock), the call blocks indefinitely. The caller holds `_git_lock` during the stall, so all concurrent threads also block.

3. **`t.join()` without timeout in `test_concurrent_writes_produce_no_index_lock_errors`.** `tests/test_scheduler_beads.py:742` calls `t.join()` with no timeout argument. If any of the 5 worker threads hangs (e.g. because a git subprocess in `storage.py` is stuck), the test hangs forever.

4. **Test git repos not isolated from global git config.** `tests/helpers.py:setUp` initialises a temp git repo but does not set `commit.gpgsign = false`. If the user or CI runner has GPG commit signing enabled globally, every `git commit` in test repos will attempt to invoke the GPG agent — which may block indefinitely when stdin is closed (`capture_output=True`).

5. **`takt merge` test-gate failure mode is a merge-conflict bead.** When the test gate times out, `takt merge` creates a `bead_type=merge-conflict` bead (e.g. `B-71b3c0d6`). Without a per-test timeout, the root cause is invisible and the merge-conflict bead agent cannot fix it. Once the underlying hang is fixed, the stranded merge-conflict bead must be cleaned up and `takt merge B-4c83e0c7` retried.

## Changes

### 1. Add `pytest-timeout` and configure a default timeout

In `pyproject.toml`:
- Add `pytest-timeout` to the dev dependencies.
- Add to `[tool.pytest.ini_options]`:
  ```toml
  timeout = 120
  timeout_method = "thread"
  ```
  120 s is generous for any single test but short enough to surface a hang in CI within two minutes.

### 2. Add timeout to git subprocess calls in `storage.py`

In `src/agent_takt/storage.py`, both `_git_commit_bead` and `_git_commit_bead_deletion`:
- Add `timeout=30` to every `subprocess.run(["git", ...])` call.
- `subprocess.TimeoutExpired` is already caught by the surrounding `except Exception: pass` block — git timeouts are non-fatal, same as other git errors.

### 3. Add `commit.gpgsign = false` to test repo setup

In `tests/helpers.py:setUp` (around line 64), after the `user.email`/`user.name` config calls, add:
```python
subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=self.root, check=True)
```
Apply the same change in all other test files that call `git init` and make commits: `tests/test_merge_safety.py`, `tests/test_scheduler_execution.py`, `tests/test_model_override.py`, `tests/test_labels.py`.

### 4. Add timeout and liveness check to `t.join()` in concurrent writes test

In `tests/test_scheduler_beads.py:742`, change:
```python
for t in threads:
    t.join()
```
to:
```python
for t in threads:
    t.join(timeout=10)
live = [t for t in threads if t.is_alive()]
self.assertEqual([], live, f"Threads still alive after timeout: {live}")
```

### 5. Post-merge cleanup instructions (handoff only)

After this fix is merged, the developer bead's handoff summary must note:
- Delete stranded bead `B-71b3c0d6` with `uv run takt bead delete B-71b3c0d6 --force`.
- Retry the merge with `uv run takt merge B-4c83e0c7`.

## Files to Modify

| File | What changes |
|---|---|
| `pyproject.toml` | Add `pytest-timeout` dev dependency; add `timeout` and `timeout_method` to `[tool.pytest.ini_options]` |
| `src/agent_takt/storage.py` | Add `timeout=30` to all `subprocess.run(["git", ...])` calls |
| `tests/helpers.py` | Add `git config commit.gpgsign false` in `setUp` after existing config calls |
| `tests/test_scheduler_beads.py` | Add `timeout=10` + liveness assertion to `t.join()` loop |
| `tests/test_merge_safety.py` | Add `git config commit.gpgsign false` in test repo setup |
| `tests/test_scheduler_execution.py` | Add `git config commit.gpgsign false` in test repo setup |
| `tests/test_model_override.py` | Add `git config commit.gpgsign false` in test repo setup |
| `tests/test_labels.py` | Add `git config commit.gpgsign false` in test repo setup |

## Acceptance Criteria

- `uv run pytest tests/ -n auto -q` completes without hanging, both locally and in GitHub Actions.
- `uv run pytest tests/ -n auto -q` completes in under 5 minutes on a developer machine.
- If a test does hang, pytest-timeout kills it after 120 s and reports the test name clearly rather than stalling the entire run.
- `test_concurrent_writes_produce_no_index_lock_errors` fails with a clear assertion error (not a hang) if any thread exceeds its join timeout.
- All existing tests continue to pass.

## Pending Decisions

- **Which timeout method for pytest-timeout?** `thread` is the safest cross-platform method; `signal` is more reliable on Unix but incompatible with Windows. Since CI is ubuntu-latest and development is macOS, `thread` is the safer default.
- **Timeout value.** 120 s per test is conservative. If TUI or integration tests run close to this limit in CI, raise it. Start at 120 s and adjust based on CI observations.
