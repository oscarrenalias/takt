---
name: Add commit_bead_state config toggle
id: spec-7fd6960b
description: Add common.commit_bead_state config toggle to suppress per-save git commits of bead state. Default on for backwards compatibility; off for new takt init projects.
dependencies: null
priority: medium
complexity: null
status: done
tags:
- config
- storage
- onboarding
- dx
scope:
  in: null
  out: null
feature_root_id: B-f5d14de1
---
# Add commit_bead_state config toggle

## Objective

`RepositoryStorage.save_bead()` currently calls `_git_commit_bead()` on every bead state mutation, including every appended `execution_history` event. In the agent-takt repo this has produced **5,598 of 6,901 commits (81%)** as `[bead] B-xxx: <status>` noise — making real `[takt]` worker commits very hard to spot in `git log`.

The bead JSON is the source of truth on disk, and there are already two parallel audit trails (`execution_history` inside each bead, and `.takt/logs/events.jsonl`), so the per-save commits are not load-bearing — they are duplicate bookkeeping.

This spec adds a single boolean config switch — `common.commit_bead_state` — that suppresses those commits. New projects default to the quiet behavior; existing repos are untouched until they opt in by editing their config.

## Problems to Fix

1. **Every bead state mutation creates a git commit.** `_git_commit_bead()` at `src/agent_takt/storage.py:56` is invoked from `save_bead()` for every status transition and every appended execution event. There is no way for an operator to turn this off short of editing source code.
2. **`git log` on `main` is dominated by bookkeeping commits.** Real changes (`[takt]` worker commits, manual commits, doc/spec changes) are buried in `[bead]` noise.
3. **The audit trail provided by these commits is already duplicated** in `execution_history` (per bead) and in `.takt/logs/events.jsonl`. The commits do not enable any operator workflow that those two sources do not already cover.

## Changes

### Decisions (already taken — do not re-litigate)

| Decision | Choice |
|---|---|
| Config location | `common.commit_bead_state` (bool) |
| Toggle scope | Suppress only the git commit. Keep `_write_worktree_exclude`, `merge=ours` policy, and worker `:(exclude).takt/beads/**` plumbing intact — they are still useful for operator ergonomics and for repos that keep the flag on. |
| Migration | **Opt-in.** No `takt migrate-beads` command. Operators of existing repos follow a documented manual recipe to untrack history if they want it. |
| Default in `default_config()` | `True` — backwards-compatible for any repo whose `.takt/config.yaml` lacks the new key. |
| Default written by `takt init` for new projects | `False` — new projects get the quiet behavior out of the box. |
| `takt upgrade` behavior | Does **not** flip the flag and does **not** modify `.gitignore`. If the key is absent from an upgraded repo's config, behavior remains `True` via `default_config()`. |

### 1. Config model — `src/agent_takt/config.py`

- Add field on `CommonConfig` (at `config.py:10`):
  ```python
  commit_bead_state: bool = True
  ```
- In `default_config()` (around `config.py:95`), include `commit_bead_state=True` in the `CommonConfig(...)` construction.
- In the YAML parser (around `config.py:226–231`), read `common.get("commit_bead_state", defaults.common.commit_bead_state)` and pass it into the `CommonConfig` constructor.

### 2. Storage — `src/agent_takt/storage.py`

- Extend `RepositoryStorage.__init__` (at `storage.py:39`) with an optional kwarg `commit_bead_state: bool | None = None`. Store it as `self._commit_bead_state`.
- Expose the resolved value as a public read-only property `RepositoryStorage.commit_bead_state` returning `self._commit_bead_state`. Tests assert against this property — no need to invent ad-hoc inspection helpers or patched constructors.
- When `self._commit_bead_state is False`, early-return at the top of `_git_commit_bead` (`storage.py:56`) and `_git_commit_bead_deletion` (`storage.py:118`) — before any `git add` / `git diff` / `git commit` subprocess invocation.
- When `self._commit_bead_state is None`, fall through to the existing `RepositoryStorage._auto_commit` class-level guard (preserves test paths that toggle `_auto_commit = False`).
- When `self._commit_bead_state is True`, behave exactly as today — which still respects the class-level `RepositoryStorage._auto_commit` guard. The new flag is an *additional* suppressor, never an overrider; setting `commit_bead_state=True` does not force commits when `_auto_commit` is `False`.
- The bead JSON write path itself is **unchanged** — `_write_bead` continues to atomically write the JSON file regardless of whether a commit follows.

### 3. Service wiring — `src/agent_takt/cli/services.py`

- In `make_services()` (at `services.py:63`), load `config` **before** constructing `RepositoryStorage`. Pass `commit_bead_state=config.common.commit_bead_state` into the constructor.
- The argument order swap (config → storage) is local to this function; no callers change.
- Direct `RepositoryStorage(root)` constructions in tests (e.g. `tests/test_tui_state.py`, `tests/test_config_wiring_phase3.py`) continue to work because the new kwarg is optional and defaults to `None`.

### 4. Onboarding — `src/agent_takt/onboarding/`

- `scaffold.py:28` (`_GITIGNORE_ENTRIES`) — do **not** add `.takt/beads/` to the shared constant. Instead, extend `update_gitignore()` (at `scaffold.py:96`) with a new kwarg `include_bead_state: bool = False`. When `True`, append `.takt/beads/` to the to-add list before the existing dedup logic runs.
- `scaffold.py:307` — when `scaffold_project()` calls `update_gitignore()` for a fresh `takt init`, pass `include_bead_state=True` (because the scaffolded config will have `commit_bead_state: false`).
- `src/agent_takt/onboarding/config.py` — the generated `.takt/config.yaml` template must include `commit_bead_state: false` inside the `common:` block, alongside the existing keys (`test_command`, `test_timeout_seconds`, `memory_cache_dir`). Find the existing emission of the `common:` block and add the new key.
- `src/agent_takt/onboarding/upgrade.py` — leave the upgrade path's config handling **alone**. Do **not** auto-add the new key to existing configs and do **not** modify `.gitignore` on upgrade.

### 5. Docs — `CLAUDE.md`

Heading locations are pinned to remove guesswork:

- **New paragraph under `## Configuration`** (the existing H2 at `CLAUDE.md:113`), inserted directly after the `CommonConfig fields ...` paragraph that lists `test_command`, `test_timeout_seconds`, `memory_cache_dir`. Document `common.commit_bead_state`: default `True` for backwards compatibility in existing repos, default `False` in `.takt/config.yaml` generated by `takt init`, what it controls (suppresses `_git_commit_bead` / `_git_commit_bead_deletion`), and when an operator would flip it.
- **Update the bead-state-exclusion bullet inside `## Conventions`** (the existing H2 at `CLAUDE.md:204`). Append one sentence: when the flag is off, `_git_commit_bead` / `_git_commit_bead_deletion` are no-ops; the worktree-exclude and `merge=ours` mechanisms still apply for any repos that keep the flag on.
- **New H3 subsection `### Cleaning bead-state history in an existing repo`**, placed **inside `## Configuration`** (the H2 at `CLAUDE.md:113`), immediately after the new `commit_bead_state` paragraph described above. Body is the manual opt-in recipe:
  1. Set `common.commit_bead_state: false` in `.takt/config.yaml`.
  2. Append `.takt/beads/` to `.gitignore` and commit.
  3. `git rm -r --cached .takt/beads/`, commit.
  4. (Optional, destructive) note that purging *historical* `[bead]` commits requires `git filter-repo` / interactive rebase and is out of scope for the tool.

### 6. Tests

- **Config loader** (extend `tests/test_config_wiring_phase3.py` or the nearest existing config-wiring test):
  - `CommonConfig.commit_bead_state` defaults to `True` in `default_config()`.
  - YAML override of `common.commit_bead_state: false` is honored.
  - `make_services()` constructs `RepositoryStorage` with the resolved flag value (assert via inspection of the storage instance, e.g. a new public-or-protected attribute or by capturing the constructor kwargs in a patched test double).
- **Storage** (extend an existing storage-related test, e.g. `tests/test_orchestrator.py` or a `tests/test_storage*.py`):
  - Construct `RepositoryStorage(root, commit_bead_state=False)`. Call `save_bead` on a freshly-created bead. Assert `git rev-list --count HEAD` is unchanged between before and after.
  - Construct `RepositoryStorage(root, commit_bead_state=True)`. Call `save_bead`. Assert a new commit appears with subject matching `[bead] B-xxx: created (...)`.
  - Construct `RepositoryStorage(root)` (no kwarg). Behavior must match the existing class-level `_auto_commit` path.
- **Onboarding**:
  - A fresh `scaffold_project()` writes `.takt/beads/` into the generated `.gitignore`.
  - A fresh `scaffold_project()` writes `commit_bead_state: false` into `.takt/config.yaml` under the `common:` block.
- **Upgrade**:
  - Running upgrade against a fixture repo whose `.takt/config.yaml` has no `commit_bead_state` key does **not** mutate the config file or `.gitignore`.
  - Loading that fixture repo's config after upgrade resolves `commit_bead_state` to `True`.

## Files to Modify

| File | Change |
|---|---|
| `src/agent_takt/config.py` | Add `CommonConfig.commit_bead_state: bool = True`; default in `default_config()`; YAML loader read at lines ~226–231 |
| `src/agent_takt/storage.py` | Accept optional `commit_bead_state` in `RepositoryStorage.__init__`; early-return in `_git_commit_bead` (`:56`) and `_git_commit_bead_deletion` (`:118`) when the flag is `False` |
| `src/agent_takt/cli/services.py` | In `make_services()` (`:63`), load config before constructing storage; pass `commit_bead_state=config.common.commit_bead_state` |
| `src/agent_takt/onboarding/scaffold.py` | Add `include_bead_state: bool = False` kwarg to `update_gitignore()`; pass `True` from `scaffold_project()` |
| `src/agent_takt/onboarding/config.py` | Emit `commit_bead_state: false` in the scaffolded `common:` block |
| `CLAUDE.md` | Document the toggle; document the manual opt-in cleanup recipe |
| `tests/test_config_wiring_phase3.py` (or equivalent) | Config-loader + service-wiring assertions |
| `tests/test_orchestrator.py` (or `tests/test_storage*.py`) | Storage-level no-commit / commit-as-expected assertions |
| `tests/` (nearest existing onboarding test) | Scaffolding assertions for `.gitignore` and `config.yaml` |

## Acceptance Criteria

- A new project initialised via `uv run takt init` produces `.takt/config.yaml` containing `commit_bead_state: false` under the `common:` block, and a `.gitignore` containing `.takt/beads/`.
- In a freshly-initialised project, executing `uv run takt --runner claude run` (with at least one bead) produces **zero** new commits whose subject starts with `[bead]`. `[takt]` worker commits still appear as before.
- An existing repo whose `.takt/config.yaml` does not mention `commit_bead_state` continues to produce `[bead]` commits exactly as before — no behavioral drift after `takt upgrade`.
- Setting `common.commit_bead_state: false` in an existing repo's `.takt/config.yaml` (and no other change) immediately stops new `[bead]` commits on the next `takt run` invocation.
- `RepositoryStorage._auto_commit = False` (the existing test escape hatch) continues to suppress commits regardless of the new flag — no regression in current tests that rely on it.
- `RepositoryStorage.commit_bead_state` is a public read-only property reflecting the constructor-resolved flag value. Reading it does not mutate state.
- `update_gitignore(include_bead_state=True)` is **idempotent**: running `scaffold_project()` (or calling `update_gitignore` directly with `include_bead_state=True`) against an already-initialised repo whose `.gitignore` already contains `.takt/beads/` does not produce a duplicate entry. The function still returns `False` when no new entries are added.
- `uv run pytest tests/ -n auto -q` passes on `main` after the change.

## Pending Decisions

None — all key decisions are recorded under the "Decisions (already taken)" table above. Open implementation choices (e.g. whether to expose `_commit_bead_state` as a public attribute for test inspection, exact placement of new test cases) are left to the implementer.
