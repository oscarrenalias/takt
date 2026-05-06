# agent-takt

Multi-agent orchestration system that coordinates AI workers (Codex or Claude Code) on a shared codebase using Git worktrees.

## Quick Reference

```bash
uv run pytest tests/ -n auto -q                  # run tests
uv run takt --version                             # print installed version
uv run takt summary                               # bead status overview
uv run takt bead list --plain                     # all beads as table
uv run takt bead graph                            # Mermaid diagram of all beads (--feature-root <id>, --output <file>)
uv run takt --runner claude run                   # run all beads to quiescence with Claude Code
uv run takt tui                                   # interactive terminal UI
```

## Project Layout

```
src/agent_takt/
  cli/            CLI dispatch and output formatting package
    __init__.py   Main CLI entry point and command dispatch (imports parser, formatting, services, commands)
    parser.py     Argument parser construction (build_parser, _refresh_seconds)
    formatting.py Bead list and claims plain-text formatting helpers (format_bead_list_plain, format_claims_plain)
    services.py   Service wiring (make_services, apply_operator_status_update, validate_operator_status_update)
    commands/     Command sub-packages; one module per command group
      __init__.py Re-exports command_bead, _validated_feature_root_id, _resolve_feature_root_id
      bead.py     bead sub-command handler (create, list, show, update, delete, label, unlabel, claims, graph)
      run.py      run command + CliSchedulerReporter (cycle progress reporter for CLI output)
      merge.py    merge command handler
      telemetry.py telemetry command + formatting helpers (command_telemetry, aggregate_telemetry)
      init.py     init and upgrade command handlers
      misc.py     Remaining commands: plan, handoff, retry, summary, tui, asset
  config.py       YAML config loader + frozen dataclass models
  scheduler/      Orchestration loop package: leases, conflicts, followups (all params from config)
    __init__.py   Re-exports Scheduler, SchedulerReporter, SchedulerResult
    core.py       Main Scheduler class and scheduling loop
    execution.py  Bead execution and lease management
    finalize.py   Bead finalization and status transitions
    followups.py  Followup bead creation and scope syncing; _create_followups caches list_beads() once and threads the result through helper methods (beads= param) to avoid repeated O(n) storage scans
    reporter.py   SchedulerReporter: cycle summary formatting
  storage.py      Bead JSON persistence under .takt/beads/ + telemetry artifacts; git commit failures are non-fatal and recorded as `git_commit_failed` execution events
  models.py       Bead (incl. recovery_for), Lease, HandoffSummary, AgentRunResult
  runner.py       AgentRunner ABC + CodexAgentRunner, ClaudeCodeAgentRunner; _extract_json_from_text tries 4 ordered strategies (direct parse → strip outer fence → embedded fence → outermost {…} match) with per-strategy DEBUG-level logging
  prompts.py      Worker/planner prompt construction + guardrail loading (config-overridable)
  skills.py       Per-agent skill catalog allowlists and isolated execution root setup (config-driven)
  gitutils.py     Worktree creation, commits, merges, bead-state exclusion (_write_worktree_exclude)
  planner.py      Spec-to-bead-graph planning service
  tui/            Textual-based interactive UI package
    __init__.py   Public re-exports (run_tui and all public symbols)
    state.py      Runtime state, filter constants, tree row helpers
    tree.py       Bead tree construction (build_tree_rows, collect_tree_rows)
    render.py     Panel rendering (render_tree_panel, render_detail_panel); focused panels show [ACTIVE] badge, unfocused panels show no badge
    actions.py    Operator action handlers (retry, status update, merge, scheduler)
    app.py        Textual App class, keybindings, build_tui_app, run_tui; _on_interval_tick tracks prev_len before refresh() and appends new scheduler log lines so events are never dropped
    reporter.py   TuiSchedulerReporter: live scheduler event posting from worker threads
  console.py      CLI output helpers (spinners, spinner pool, colours)
  _assets.py      importlib.resources helpers for locating bundled package data (_data/)
  onboarding/     takt init/upgrade helpers package; all public symbols re-exported from __init__.py
    prompts.py    STACKS catalog, InitAnswers dataclass, collect_init_answers, _select_from_list
    scaffold.py   scaffold_project() entry point; gitignore, memory seed, commit helpers
    assets.py     Asset installation helpers (templates, skills, config)
    config.py     Config YAML generation and template placeholder substitution
    upgrade.py    Asset upgrade evaluation (AssetDecision, evaluate_upgrade_actions) and manifest I/O
    version.py    Version tracking helpers: write_version_file, read_version_file, check_version_drift

templates/agents/   Guardrail templates per agent type (mandatory)
templates/skills/   Subagent skill catalog (`core/`, `role/`, `capability/`, `task/`); primary source
.agents/skills/     Operator skill overrides (custom exceptions only; falls back to templates/skills/)
.takt/              Runtime state: beads/, logs/, worktrees/, telemetry/, agent-runs/, config.yaml, version.json
```

## Key Concepts

**Beads** are the unit of work. Lifecycle: `open` -> `ready` -> `in_progress` -> `done` | `blocked` | `handed_off`.

**Agent types**: `planner`, `developer`, `tester`, `documentation`, `review`, `recovery`. Only `developer`, `tester`, `documentation` mutate code. Invalid types are rejected at parse time via JSON schema `enum` constraints in both `PLANNER_OUTPUT_SCHEMA` and `AGENT_OUTPUT_SCHEMA`.

**Verdicts**: Review and tester beads produce `verdict: approved | needs_changes`. Verdict is the control-flow signal; narrative fields are context only.

**Followup beads**: When a developer bead completes, the scheduler auto-creates `-test`, `-docs`, `-review` children, unless the bead is a corrective bead or has `bead_type == "merge-conflict"`. For planner-owned feature trees, shared followup beads are used instead — legacy per-developer children are suppressed. Scope syncing (`_sync_followup_scope`) still runs when a matching planner-owned bead exists. Standalone developer flows use legacy per-developer creation unchanged.

The planner prompt mandates that every feature tree must include exactly one shared tester bead, one shared documentation bead, and one shared review bead — never per-developer tester/docs/review children. The shared tester and documentation beads must depend on all developer beads in the tree; the shared review bead must depend on all developer beads plus the shared tester and documentation beads.

**Shared followup scope population** (`_populate_shared_followup_touched_files`): Before a `tester`, `documentation`, or `review` bead starts, the scheduler aggregates `touched_files` and `changed_files` from all **done** dependency beads — including tester and documentation dependencies, not just developer beads — and merges them into the followup bead's scope. This ensures review beads see test files written by the tester and doc files written by the docs agent. Duplicates are deduplicated; the bead is only persisted if the merged scope differs from the existing one.

**Corrective beads**: Transient failures matching `config.scheduler.transient_block_patterns` get up to `config.scheduler.max_corrective_attempts` (default 5) automatic `-corrective` retries. The reactive slot-fill loop runs after each bead completes, so newly requeued or newly created corrective beads may be dispatched in the **same scheduler cycle** — not deferred to the next one. Tests that check `result.completed` or `result.blocked` must pre-configure `FakeRunner` with results for any bead the slot-fill will dispatch; use `assertIn` rather than `assertEqual` when the set of completed/blocked beads may be larger than expected.

Corrective beads are subject to a strict scope guardrail (enforced via `templates/agents/developer.md`): the agent must fix only the specific failure that blocked the parent bead. It must not add unrelated improvements, reapply previously-reverted changes, or touch files outside the parent bead's `expected_files`/`expected_globs` unless the fix genuinely requires it. If an unrelated issue is discovered during the fix, the agent must file a separate bead rather than fixing it in place. The reviewer checks that scope was respected.

**Recovery beads**: When a bead fails with a no-structured-output error, the scheduler automatically creates a `{bead_id}-recovery` bead (`bead_type="recovery"`, `agent_type="recovery"`) — no manual retry is required. The recovery bead's `recovery_for` field holds the `bead_id` of the original bead. When the recovery bead completes successfully, the scheduler applies its synthesised handoff to the original bead, marks it done, and triggers normal follow-up creation. Recovery beads do not consume corrective attempt slots. Recovery-of-recovery is prevented: a `bead_type="recovery"` bead that also fails without structured output does not create a second recovery bead.

If you run `takt retry` on a bead that already has a pending (non-terminal) recovery bead, the command warns and exits without requeuing — preventing a race with the in-progress recovery path. Manual retry is allowed again once the recovery bead reaches `done` or `blocked`.

Recovery beads appear in `takt bead list --plain` as ordinary entries with `bead_type=recovery`. They are also visible as children of the original bead in `takt bead graph`.

## Multi-Backend Support

Select backend via `--runner` flag, `AGENT_TAKT_RUNNER` env var, or `config.default_runner` (resolved in that priority order). `ORCHESTRATOR_RUNNER` is accepted as a legacy fallback.

| | Codex | Claude Code |
|---|---|---|
| Skills directory | `exec_root/.agents/skills/` | `exec_root/.claude/skills/` |
| Agent steering | Embedded in prompt | `exec_root/CLAUDE.md` (auto-loaded) |
| CLI invocation | `codex exec --full-auto` | `claude -p --dangerously-skip-permissions` |

The skill catalog is role-scoped rather than global. `skills.py` keeps a fixed `AGENT_SKILL_ALLOWLIST` that bundles `core/base-orchestrator`, one role skill, and `memory` for every worker agent type. Most types also receive capability and task skills; the `planner` is an exception — it gets `task/spec-intake` and `task/dependency-graphing` but no `capability/` skill. The `scheduler` backend uses only scheduler-specific skills and does not receive `memory`.

Beads are backend-agnostic. A bead started with Codex can be retried with Claude Code via `takt --runner claude retry <bead_id>`.

See [docs/multi-backend-agents.md](docs/multi-backend-agents.md) for tool allowlists, subprocess timeouts, runner telemetry fields, and config wiring details.

## Configuration

Orchestrator settings live in `.takt/config.yaml`. Key dataclasses: `OrchestratorConfig`, `SchedulerConfig`, `BackendConfig`, `CommonConfig`. Falls back to built-in defaults if the file is missing. The YAML file has three top-level blocks: `common`, `codex`, and `claude`.

`CommonConfig` fields (under the `common:` block): `test_command`, `test_timeout_seconds`, `memory_cache_dir`. The `memory_cache_dir` key sets the directory where the ONNX embedding model is cached (default: `~/.cache/agent-takt/models`); override in CI environments or to share the model cache across projects.

`SchedulerConfig` includes a `serialize_within_feature_tree` flag (default: `false`). When set to `true`, the scheduler dispatches at most one bead at a time within each feature tree, even when multiple workers are available. Enable this for toolchains that cannot tolerate concurrent file access within a worktree — for example, Swift/Xcode/SPM projects where `Package.resolved` or the DerivedData cache is shared across all simultaneous builds. The trade-off is reduced parallelism: beads that could otherwise run concurrently in the same worktree are serialized, so overall cycle time increases.

Key functions in `config.py`: `load_config(root)`, `default_config()`, `config.backend(name)`, `config.allowed_tools_for(backend, agent_type)`.

## Fleet Manager

`takt-fleet` is a sibling CLI that coordinates work across multiple local takt projects. See [docs/fleet.md](docs/fleet.md) for full documentation.

```bash
# Register projects
uv run takt-fleet register /path/to/project-a --tag api
uv run takt-fleet register /path/to/project-b --name "backend" --tag api --tag prod

# Check health and bead counts across the fleet
uv run takt-fleet list
uv run takt-fleet summary --tag api

# Fan out a bead to all "api" projects, then run
uv run takt-fleet dispatch --title "..." --description "..." --tag api
uv run takt-fleet run --tag api --runner claude

# Watch live event stream
uv run takt-fleet watch --tag api --since 5m

# Query run history
uv run takt-fleet runs list --limit 10
uv run takt-fleet runs show FR-<id>
```

Registry: `~/.config/agent-takt/fleet.yaml` (XDG-aware).
Run logs: `~/.local/share/agent-takt/fleet/runs/` (one `FR-<8hex>.json` per run).

**v0.1.0 scope**: local filesystem projects only; no cross-project spec management; `dispatch` creates beads but does not trigger execution.

## Version Tracking

`takt init` and `takt upgrade` write `.takt/version.json` to record the installed takt version and timestamp:

```json
{
  "takt_version": "0.x.y",
  "last_upgraded_at": "2026-04-17T14:00:00+00:00"
}
```

`takt summary` calls `check_version_drift()` at startup and emits a console warning when:

- `.takt/version.json` is missing — prompt: `"No .takt/version.json found. Run 'takt upgrade' to record the current version."`
- The recorded version is older than the installed version — prompt names both versions and suggests `takt upgrade`.

No warning is shown when versions match or when the repo version is ahead of the installed version. To clear a drift warning, run `takt upgrade` — it overwrites the file and records the current installed version.

## Multi-Worker CLI Output

`takt run --max-workers N` controls parallelism. Single-worker uses a `Spinner`; multi-worker uses `SpinnerPool` (N reserved terminal lines, ANSI cursor positioning). Both are thread-safe. Non-TTY falls back to line-by-line output.

After `takt run` completes, the CLI prints a cycle summary and emits a JSON block:

```json
{
  "started": ["B-abc...", ...],
  "completed": ["B-abc...", ...],
  "blocked": ["B-abc...", ...],
  "correctives_created": ["B-abc...", ...],
  "deferred_count": 3,
  "final_state": {"done": 2, "blocked": 1, "ready": 0, ...}
}
```

## Conventions

- Guardrail templates are **mandatory**. Missing `templates/agents/{agent_type}.md` fails the bead with `FileNotFoundError`.
- Bead metadata is authoritative; always read/write through `RepositoryStorage`.
- Execution history is append-only (audit trail). A `git_commit_failed` event is recorded when a git auto-commit in `_git_commit_bead` fails — the bead JSON is still persisted to disk so no state is lost. If nothing is staged after `git add` (bead content unchanged), the commit is silently skipped — no event is recorded. Deletion commits log only (no event appended, since the bead file no longer exists). A `dependency_resolution_error` event is appended — and a `logger.warning` emitted with `exc_info=True` — when a dependency lookup throws in `_dep_is_done` (`core.py`) or `_load_dep_handoffs` (`execution.py`); the affected bead is immediately persisted so the error appears in its audit trail. For `_dep_is_done`, the event is only appended when the optional `depending_bead` argument is supplied (callers in the dependency-deferral path pass the bead; callers that only need the boolean do not).
- Operator status updates are restricted: developer beads cannot be manually marked `done` (must go through scheduler to trigger followups).
- File-scope conflicts are checked statically at schedule time. Overlapping `expected_files`/`expected_globs` between in-progress beads cause blocking.
- **Branch naming**: `feature/{feature_root_id.lower()}` (e.g. `B-a7bc3f91` → `feature/b-a7bc3f91`).
- **Worktree paths**: `.takt/worktrees/{feature_root_id}` (not lowercased). The path is **deterministic** — derive it from the feature root ID rather than running `ls .takt/worktrees/` to discover it. If you have a feature root `B-a7bc3f91`, its worktree is `.takt/worktrees/B-a7bc3f91/`. Agents and operators should not need to enumerate the worktrees directory; if you find yourself reaching for `ls .takt/worktrees/`, you almost certainly have the feature root ID already and can construct the path directly.
- **Bead state exclusion**: When a feature worktree is created, `.takt/beads/` is written to `.git/worktrees/<name>/info/exclude` so bead files are invisible to git in that worktree. An initial commit also untracks any pre-existing bead files via `git rm --cached`. The `.gitattributes` rule `.takt/beads/** merge=ours` provides an additional safety net, and `WorktreeManager.merge_main_into_branch()` plus `merge_branch()` now enforce that policy explicitly by auto-resolving any `.takt/beads/` merge conflicts in favor of the current branch before completing the merge. In practice that means preflight merges keep the feature branch's bead snapshot, while final merges on `main` keep `main`'s bead snapshot; the fallback only commits automatically when every remaining conflict is under `.takt/beads/`. `merge_main_into_branch` additionally guards against the "would be overwritten by merge" error: before merging, it saves and removes any untracked `.takt/beads/` files (those not in the git index) via `_save_and_remove_bead_files`, then restores them unconditionally in a `try/finally` block via `_restore_saved_bead_files` — so the feature worktree's local bead snapshot is preserved regardless of whether the merge succeeds or fails. Tracked bead files are left alone and flow through git's normal merge path. `WorktreeManager.commit_all()` passes `:(exclude).takt/beads/**` to `git add` so bead files are never staged; if nothing is staged after the add (e.g. when `.takt/beads/` is still visible as untracked after a safety-net merge and no real worker changes exist), `commit_all` returns `None` without attempting a commit.
- **Bead ID allocation**: Root beads: `B-{first 8 hex chars}`. Child beads append suffixes (`B-abc12def-test`, `B-abc12def-review`).
- **Bead sorting**: By creation timestamp (first `execution_history` entry), falling back to bead ID on tie.
- **Prefix resolution**: `RepositoryStorage.resolve_bead_id(prefix)` resolves partial IDs; raises `ValueError` on zero or multiple matches.

## Testing

Tests run via pytest with `FakeRunner` and `OrchestratorTests` from `tests/helpers.py` mocking agent execution. The scheduler tests are split across `test_scheduler_core.py`, `test_scheduler_execution.py`, `test_scheduler_finalize.py`, `test_scheduler_followups.py`, and `test_scheduler_beads.py`. CLI command tests are split across dedicated `test_cli_*.py` files (`test_cli_bead.py`, `test_cli_merge.py`, `test_cli_run.py`, `test_cli_telemetry.py`, and others); `test_orchestrator.py` covers the remaining integration tests (planner, TUI, prompts, storage). Run with:

```bash
uv run pytest tests/ -n auto -q
```

## No Manual Code Changes

This project is self-hosting — all code changes go through beads, including bug fixes and hotfixes. Do not edit source files directly. Create a bead, let the system implement it, and merge via the normal pipeline. The only exceptions are CLAUDE.md, config files, and spec files.

## Running Commands

All commands must be prefixed with `uv run`. This is the only supported way to run the orchestrator and tests:

```bash
uv run takt ...                             # any takt command
uv run pytest tests/ -n auto -q            # run tests
```

Do not invoke `takt` or `python` directly without `uv run`.

## Working with Beads

Always use the CLI to query bead state — do not read `.takt/beads/*.json` files directly:

```bash
uv run takt bead show <id>          # single bead details (JSON)
uv run takt bead list --plain       # all beads as table
uv run takt bead graph              # Mermaid diagram of all beads
uv run takt bead graph --feature-root <id>  # scoped to one feature
uv run takt bead graph --output graph.md    # write diagram to file
uv run takt summary                 # counts + next actionable beads
uv run takt summary --feature-root <id>  # scoped to a feature
uv run takt bead delete <id>        # delete a bead (open/ready/blocked only)
uv run takt bead delete <id> --force  # delete regardless of status
```

`bead delete` enforces: bead must exist, have no children, and be in a deletable status (`open`, `ready`, `blocked` without `--force`; `in_progress`, `done`, `handed_off` require `--force`). Deleting a feature root bead also removes the associated Git worktree and feature branch. Artifact directories (`.takt/agent-runs/<id>/`, `.takt/telemetry/<id>/`) are removed. A `bead_deleted` event is appended to `.takt/logs/events.jsonl`.

### Labels

Beads support free-form string labels for grouping and filtering.

```bash
# Create a bead with one or more labels
uv run takt bead create --agent developer --title "My task" --description "..." --label urgent --label api

# Add labels to an existing bead (idempotent — safe to repeat)
uv run takt bead label <id> urgent api

# Remove a single label
uv run takt bead unlabel <id> urgent

# Filter bead list to beads carrying ALL specified labels
uv run takt bead list --label urgent --label api
```

Labels are stored as a `list[str]` on the `Bead` model. Adding a label that is already present is a no-op. The `--label` filter on `bead list` requires every specified label to match (AND semantics).

---

## Creating a spec

Use the provided "skill-spec-management" skill to manage the spec lifecycle. Please refer to the skill for more information, do not manage spec lifecycle on your own, use the provided skill.

When a skill has been created, teh following sections are strongly recommended to be part of it:

1. **Objective** — One paragraph: what problem this solves and why it matters
2. **Problems to Fix** — Numbered list of specific issues, with current state described concretely
3. **Changes** — What to build: new files, modified files, new behaviours. Be prescriptive — include function signatures, field names, config keys, CLI flags where known
4. **Files to Modify** — Table: file path → what changes
5. **Acceptance Criteria** — Bullet list of verifiable conditions the implementation must satisfy
6. **Pending Decisions** — Any open questions that must be resolved before planning. Mark resolved decisions inline (strikethrough + resolution)

---

## Planning a Spec (Persisting Beads)

The `takt plan` command supports four modes via mutually exclusive flags:

| Mode | Flag | Calls LLM | Persists beads |
|---|---|---|---|
| Dry-run | _(none)_ | yes | no |
| Persist | `--write` | yes | yes |
| Save only | `--output FILE` | yes | no — writes plan JSON to FILE |
| Promote | `--from-file FILE` | no | yes — reads plan JSON from FILE |

```bash
# Dry run — calls the LLM, prints plan JSON, does NOT create beads
uv run takt plan specs/drafts/my-spec.md

# Persist — creates beads in storage and ingests spec into memory
uv run takt plan --write specs/drafts/my-spec.md

# Save plan to file for review — calls the LLM but does NOT create beads
uv run takt plan --output /tmp/my-plan.json specs/drafts/my-spec.md

# Promote a saved plan — creates beads from FILE without calling the LLM
# spec_file argument is not required when using --from-file
uv run takt plan --from-file /tmp/my-plan.json
```

**Staged planning workflow** — generate once, review, then promote:
1. Run with `--output FILE` to save the plan JSON to disk.
2. Inspect (and optionally edit) the saved file.
3. Run with `--from-file FILE` to create beads from the reviewed plan.
4. Delete the plan file after promoting — the operator is responsible for cleanup; takt does not remove it automatically.

**Always use `--write` or `--from-file` to persist beads.** Without one of these flags, the planner output is printed but no beads are created.

After persisting, use `spec.py` to transition the spec to `planned`:

```bash
python3 <spec-py> set status planned spec-a3f19c2b
```

Then commit both the beads and the spec status change together.

---

## Checking Spec / Bead Status

```bash
# Overall counts
uv run takt summary

# Scoped to one feature
uv run takt summary --feature-root <bead_id>

# All beads as table
uv run takt bead list --plain

# Find the feature root ID for a spec
uv run takt bead list --plain | grep -i "<spec keyword>"
```

To find which bead corresponds to a spec, search by title keyword. The feature root bead (where `bead_id == feature_root_id`) is the top-level planner bead.

---

## Moving a Spec to Done

Conditions that must ALL be true:
1. `uv run takt summary --feature-root <id>` shows `ready=0, in_progress=0, blocked=0`
2. The feature branch has been merged to main via `takt merge <id>`
3. Tests pass on main

Then use `spec.py` to transition the spec:

```bash
python3 <spec-py> set status done spec-a3f19c2b
git add specs/
git commit -m "Move my-spec to done/ after merge"
```

---

## Merging a Feature

Use `takt merge`, never `git merge` directly:

```bash
uv run takt merge <bead_id>
```

This does:
1. Merges `main` into the feature branch (conflict check)
2. If conflict: creates a `merge-conflict` bead, exits with instructions
3. Runs `config.common.test_command` (currently: `uv run pytest tests/ -n auto -q`)
4. If tests fail: creates a `merge-conflict` bead, exits with instructions
5. If all clear: `git merge --no-ff` into main

If a merge-conflict bead is created, run the scheduler then retry:
```bash
uv run takt --runner claude run --max-workers 4
uv run takt merge <bead_id>  # retry
```

**Flags:**
- `--skip-rebase` — skip the main-into-feature sync step
- `--skip-tests` — skip the test gate

**IMPORTANT — no manual intervention without explicit user authorisation:**
Never resolve merge conflicts, run `git merge`, or manipulate worktrees manually unless the user has explicitly asked you to. If `takt merge` creates a merge-conflict bead, let the scheduler resolve it — that is exactly what the system is designed for. Manual git operations bypass the pipeline, corrupt state, and create harder problems than the ones they fix. The system is self-hosting and capable of resolving its own conflicts. Be patient and let it.

---

## Common Mistakes to Avoid

- **Running `takt plan` without `--write` or `--from-file`** — looks like it worked but nothing is persisted
- **Leaving plan files on disk after `--from-file`** — takt does not clean up the JSON file; delete it manually once beads are created
- **Moving spec to `planned/` before beads exist** — confusing if beads are later found missing
- **Moving spec to `done/` before merging** — spec says done but code isn't on main
- **Using `git merge` instead of `takt merge`** — bypasses rebase + test gate
- **Manually resolving merge conflicts without user authorisation** — let the scheduler handle merge-conflict beads; manual git operations corrupt state
- **Using `mv` to move spec files** — use `spec.py set status` instead to keep frontmatter and filesystem in sync
- **Creating beads inside an already-merged feature tree** — those beads need their own merge cycle; use standalone beads (no `--parent-id`) for fixes to merged features
