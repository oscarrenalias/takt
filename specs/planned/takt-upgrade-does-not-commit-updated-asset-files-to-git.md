---
name: takt upgrade does not commit updated asset files to git
id: spec-6fef03a1
description: "Add a git commit step to takt upgrade so installed/updated asset files are committed automatically, matching takt init behaviour"
dependencies: null
priority: high
complexity: low
status: planned
tags: []
scope:
  in: null
  out: null
feature_root_id: null
---

# takt upgrade does not commit updated asset files to git

## Objective

`takt upgrade` installs or updates managed asset files (templates, skills, etc.) on disk but never commits them to git.
This leaves the working tree dirty after every upgrade, and — more importantly — causes agents to encounter "Missing guardrail template" errors because the updated files are untracked and therefore absent from feature branch worktrees.
`takt init` already commits via `commit_scaffold()`; `takt upgrade` must do the same.

## Problems to Fix

1. `command_upgrade` in `src/agent_takt/cli/commands/init.py` writes files (lines 117–200) then exits without staging or committing anything.
2. Agents working in a worktree only see committed files. If `templates/agents/developer.md` (and similar) were installed by `takt upgrade` but never committed, the worktree will be missing them, and the agent will crash with `FileNotFoundError: Missing guardrail template for built-in agent developer`.
3. `commit_scaffold()` already exists in `src/agent_takt/onboarding/scaffold.py` and handles exactly this task (stages `templates/`, `.agents/skills/`, `.claude/skills/`, `.takt/config.yaml`, `.takt/assets-manifest.json`, and `.gitignore`; creates a commit). It is not called from `command_upgrade`.

## Changes

### `src/agent_takt/cli/commands/init.py`

At the end of `command_upgrade`, after the manifest is written and config keys are merged, add a call to `commit_scaffold(root, console)` guarded by `not dry_run`.

For dry-run mode, print a dim notice: `[dry-run] would commit upgraded assets`.

```python
from ...onboarding.scaffold import commit_scaffold

# At the end of command_upgrade, after config key merge:
if not dry_run:
    commit_scaffold(root, console)
else:
    console.emit(f"  {console._c(DIM)}[dry-run] would commit upgraded assets{console._c(RESET)}")
```

`commit_scaffold()` already handles the "nothing to commit" case gracefully (warns and returns), so no additional error handling is needed.

## Files to Modify

| File | Change |
|------|--------|
| `src/agent_takt/cli/commands/init.py` | Import `commit_scaffold` and call it at the end of `command_upgrade`, guarded by `not dry_run` |

## Acceptance Criteria

- Running `takt upgrade` in a git repo where assets were changed results in a new git commit (reusing `commit_scaffold`).
- Running `takt upgrade --dry-run` does NOT create a git commit; a dim notice `[dry-run] would commit upgraded assets` is printed.
- Running `takt upgrade` when no assets changed does not create an empty commit — `commit_scaffold` warns gracefully and returns.
- After `takt upgrade`, `git status` shows a clean working tree (or only user-owned files intentionally skipped).
- Existing tests for `command_upgrade` continue to pass.
- A new test asserts that `commit_scaffold` is called (or that the git commit subprocess is invoked) when at least one asset is updated and `dry_run=False`.

## Pending Decisions

- ~~Use `commit_scaffold()` directly vs. a dedicated helper with a different commit message.~~ **Resolution: use `commit_scaffold()` directly — zero new code, commit message `"chore: takt init scaffold"` is acceptable.**
