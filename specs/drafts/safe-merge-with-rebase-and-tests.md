# Safe Merge: Rebase and Test Before Merge

## Objective

`orchestrator merge` currently does a bare `git merge --no-ff` with no pre-flight checks. When merging multiple feature branches in sequence, conflicts surface on `main` and tests are not verified before the merge commits. This spec adds a mandatory rebase-then-test step before the actual merge, with agent-assisted conflict resolution rather than handing the problem back to the operator.

## Problems to Fix

### 1. No rebase before merge

Feature branches diverge from `main` over time. When multiple features touch the same files, merging them in sequence causes conflicts on `main` rather than surfacing them in the feature branch context where they are easier to resolve.

### 2. No test gate before merge

`orchestrator merge` merges unconditionally. A feature branch with a broken test suite can be merged silently.

### 3. Rebase conflicts are dumped on the operator

A bare `git rebase` that fails and hands back a conflict is no better than what we have today. Conflicts must be resolved automatically by an agent, not left for the operator to fix manually.

### 4. `orchestrator merge` is too easy to bypass

The right tool must be meaningfully better than `git merge`. Making the merge command safe and agent-assisted is the incentive to always go through it.

---

## Architecture: Option B — Thin merge command, scheduler does the work

`orchestrator merge` stays thin. It attempts the rebase, detects any conflict or test failure, and **creates a bead** for the scheduler to resolve. The operator then runs `orchestrator run` (or the TUI scheduler) to execute the resolution agent, then retries `orchestrator merge`. The scheduler remains the single place where agents run.

```
orchestrator merge B-abc
  → merge main into feature succeeds, tests pass → merge commit on main ✓

orchestrator merge B-abc
  → merge main into feature conflicts → creates B-abc-merge-conflict bead → exit with instructions
  → operator: orchestrator run --once
  → agent resolves conflict on feature branch, commits
  → operator: orchestrator merge B-abc (retries; if main moved again, may cycle)

orchestrator merge B-abc
  → merge succeeds, tests fail → creates B-abc-merge-conflict bead (test-fix context) → exit
  → operator: orchestrator run --once
  → agent fixes tests on feature branch
  → operator: orchestrator merge B-abc
```

If main evolves while the conflict bead is running, the next `orchestrator merge` attempt merges main into the feature branch again — potentially producing another conflict bead. Each iteration is a focused agent fix. The `max_corrective_attempts` cap from config applies; if exceeded, the command escalates to the operator with a clear message.

---

## Changes

### 1. New bead type: `merge-conflict`

Add `"merge-conflict"` to the set of valid `bead_type` values (alongside `task`, `epic`, `corrective`). This makes merge-conflict resolution beads immediately identifiable in `bead list`, `summary`, and the TUI — distinct from corrective beads created for agent output failures.

Merge-conflict beads:
- Are created by `command_merge()`, not the scheduler
- Have `agent_type: "developer"`
- Have `bead_type: "merge-conflict"`
- Have a dedicated guardrail template: `templates/agents/merge-conflict.md`
- Are children of the feature root bead
- Carry the conflicted file list and both-sides diff in their `description`

### 2. New guardrail template: `templates/agents/merge-conflict.md`

Focused instructions for an agent resolving a rebase conflict:
- You are resolving a git rebase conflict on branch `{branch_name}` against `main`
- The conflicted files are listed in your scope
- Preserve the intent of the feature branch; accept main's version only when the feature change is superseded
- Do not introduce unrelated changes
- After resolving, run `git rebase --continue`
- Verify the test suite passes before completing

A second variant covers test failures post-rebase (same template, different instruction set in the description).

### 3. `WorktreeManager` additions in `gitutils.py`

```python
def merge_main_into_branch(self, branch_name: str, main: str = "main") -> None:
    """Checkout branch_name and merge main into it. Raises GitError on conflict."""

def abort_merge(self) -> None:
    """Abort an in-progress merge (git merge --abort)."""

def conflicted_files(self) -> list[str]:
    """Return list of files with unresolved conflicts in the working tree."""
```

### 4. New config fields in `config.py` / `config.yaml`

```yaml
common:
  test_command: "uv run python -m unittest discover -s tests"
  test_timeout_seconds: 600
```

- `test_command: str | None` — command to run after rebase; if `None`, test step is skipped with a warning
- `test_timeout_seconds: int` — default 600; applies to the test subprocess

### 5. Updated `command_merge()` in `cli.py`

```
1. Load bead, resolve branch name
2. Check for existing unresolved merge-conflict bead for this feature root
   → if one exists and is not done: exit with "resolve B-xxx-merge-conflict first"
3. Unless --skip-rebase:
   a. Merge main into the feature branch (checkout feature branch, git merge main)
   b. On conflict:
      - Abort merge (git merge --abort)
      - Create merge-conflict bead with conflicted files + diff context
      - Print: "Merge conflict — created B-xxx-merge-conflict. Run scheduler then retry."
      - Return 1
4. Unless --skip-tests:
   a. Run config.common.test_command in repo root (stream output)
   b. On failure or timeout:
      - Create merge-conflict bead describing the test failures
      - Print: "Tests failed — created B-xxx-merge-conflict. Run scheduler then retry."
      - Return 1
5. git merge --no-ff branch_name
6. Print success
```

**Flags added to `orchestrator merge`:**

| Flag | Effect |
|---|---|
| `--skip-rebase` | Skip rebase step (branch already up to date) |
| `--skip-tests` | Skip test gate |

### 6. `max_corrective_attempts` applies to merge-conflict beads

The scheduler already enforces `config.scheduler.max_corrective_attempts`. Merge-conflict beads count against this limit for their parent. If the limit is exceeded, `command_merge` exits with an escalation message instead of creating another bead.

---

## Files to Add / Modify

| File | Change |
|---|---|
| `src/codex_orchestrator/models.py` | Add `"merge-conflict"` to valid `bead_type` values |
| `src/codex_orchestrator/config.py` | Add `test_command` and `test_timeout_seconds` to `CommonConfig` |
| `src/codex_orchestrator/gitutils.py` | Add `merge_main_into_branch()`, `abort_merge()`, `conflicted_files()` |
| `src/codex_orchestrator/cli.py` | Update `command_merge()` and merge subparser |
| `templates/agents/merge-conflict.md` | New guardrail template for conflict resolution agents |
| `.orchestrator/config.yaml` | Add `test_command` and `test_timeout_seconds` under `common:` |
| `src/codex_orchestrator/tui.py` | Disable `M` key binding; show "Use `orchestrator merge <id>` from the CLI" message |
| `tests/test_merge_safety.py` | New: conflict → bead created, test failure → bead created, happy path, skip flags, max attempts cap |

---

## Acceptance Criteria

- `orchestrator merge <id>` rebases the feature branch on current `main` before merging
- On rebase conflict: rebase is aborted cleanly, a `merge-conflict` bead is created with context, operator is directed to run the scheduler
- On test failure after rebase: a `merge-conflict` bead is created describing the failures
- After the conflict bead completes, `orchestrator merge` retried successfully completes the merge
- `merge-conflict` beads are visually distinct from `corrective` beads in `bead list` and the TUI
- `--skip-rebase` and `--skip-tests` bypass respective steps
- `max_corrective_attempts` is respected; exceeded limit escalates to operator instead of creating another bead
- `config.common.test_command` absent → test step skipped with a warning
- The TUI `M` key is disabled and shows a message directing the operator to use `orchestrator merge` from the CLI
- All existing tests pass

---

## Pending Decisions

### 1. ~~Rebase vs merge~~ — Resolved: merge main into feature branch
Merge main into the feature branch (`git merge main` from the feature branch) rather than rebasing. Feature branches in this system can have many small commits (one per bead agent run); rebase would replay each commit individually, potentially producing multiple conflict rounds for the agent to resolve. Merging main into the feature branch resolves all conflicts in one pass — simpler for agent-assisted resolution. The final `orchestrator merge` still does `git merge --no-ff feature/b-abc` onto main. **Decided: merge main into feature branch.**

### 2. ~~TUI merge confirmation~~ — Resolved: disable merge in TUI for now
The TUI's `M` key currently calls `command_merge` directly and blocks the UI. With the new flow, a merge may create a conflict bead and require a follow-up scheduler run — not something the TUI can handle inline without becoming async. For now, **disable the `M` key binding in the TUI** and display a message directing the operator to use `orchestrator merge` from the CLI instead. A follow-up spec will address making TUI merge async once the CLI flow is stable. **Decided: disable TUI merge, add CLI-redirect message.**

