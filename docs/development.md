# Development Guide

## Project Layout

```
src/agent_takt/
  cli/            CLI dispatch and output formatting package
    __init__.py   Main CLI entry point and command dispatch
    parser.py     Argument parser construction
    formatting.py Bead list and claims plain-text formatting helpers
    services.py   Service wiring (make_services, apply_operator_status_update)
    commands/     Command sub-packages; one module per command group
      __init__.py Re-exports command_bead and feature-root helpers
      bead.py     bead sub-command handler
      run.py      run command + CliSchedulerReporter
      merge.py    merge command handler
      telemetry.py telemetry command and formatting helpers
      init.py     init and upgrade command handlers
      misc.py     Remaining commands: plan, handoff, retry, summary, tui, asset
  config.py       YAML config loader + frozen dataclass models
  scheduler/      Orchestration loop package: leases, conflicts, followups
    __init__.py   Re-exports Scheduler, SchedulerReporter, SchedulerResult
    core.py       Main Scheduler class and scheduling loop
    execution.py  Bead execution and lease management
    finalize.py   Bead finalization and status transitions
    followups.py  Followup bead creation and scope syncing
    reporter.py   SchedulerReporter: cycle summary formatting
  storage.py      Bead JSON persistence + telemetry artifacts
  models.py       Bead, Lease, HandoffSummary, AgentRunResult
  runner.py       AgentRunner ABC + CodexAgentRunner, ClaudeCodeAgentRunner
  prompts.py      Worker/planner prompt construction + guardrail loading
  skills.py       Skill allowlists and isolated execution root setup
  gitutils.py     Worktree creation, commits, merges
  graph.py        Mermaid bead graph renderer (render_bead_graph)
  planner.py      Spec-to-bead-graph planning service
  tui/            Textual-based interactive UI package
    __init__.py   Public re-exports (run_tui and all public symbols)
    state.py      Runtime state, filter constants, tree row helpers
    tree.py       Bead tree construction (build_tree_rows, collect_tree_rows)
    render.py     Panel rendering (render_tree_panel, render_detail_panel)
    actions.py    Operator action handlers (retry, status update, merge, scheduler)
    app.py        Textual App class, keybindings, and TuiSchedulerReporter
  console.py      CLI output helpers (spinners, colours)
  _assets.py      importlib.resources helpers for locating bundled package data
  onboarding.py   scaffold_project() and asset-install helpers used by takt init

templates/agents/   Guardrail templates per agent type (mandatory)
templates/skills/   Subagent skill catalog (SKILL.md + agents/openai.yaml); primary skill source
.agents/skills/     Operator-facing exception skills (custom overrides only; falls back from templates/skills/)
.takt/              Runtime state: beads/, logs/, worktrees/, telemetry/, agent-runs/, config.yaml
```

## Testing

```bash
uv run pytest tests/ -n auto -q
```

Tests run via pytest (with xdist for parallel execution). `FakeRunner` and `OrchestratorTests` base class are defined in `tests/helpers.py` and shared across all scheduler test modules. Target individual modules with:

```bash
# Scheduler tests
uv run pytest tests/test_scheduler_core.py -v
uv run pytest tests/test_scheduler_execution.py -v
uv run pytest tests/test_scheduler_finalize.py -v
uv run pytest tests/test_scheduler_followups.py -v
uv run pytest tests/test_scheduler_beads.py -v
uv run pytest tests/test_tui_state.py -v
uv run pytest tests/test_tui_tree.py -v
uv run pytest tests/test_tui_render.py -v
uv run pytest tests/test_tui_actions.py -v
uv run pytest tests/test_tui_app.py -v

# CLI tests (one file per command group)
uv run pytest tests/test_cli_bead.py -v
uv run pytest tests/test_cli_run.py -v
uv run pytest tests/test_cli_merge.py -v
uv run pytest tests/test_cli_telemetry.py -v
uv run pytest tests/test_cli_init.py -v
uv run pytest tests/test_cli_upgrade.py -v
uv run pytest tests/test_cli_plan.py -v
uv run pytest tests/test_cli_summary.py -v
uv run pytest tests/test_cli_tui.py -v
uv run pytest tests/test_cli_version.py -v
```

## Agent Guardrails

Guardrail templates live in `templates/agents/` and are mandatory — a missing template fails the bead with `FileNotFoundError`. The built-in set:

- `planner.md`, `developer.md`, `tester.md`, `documentation.md`, `review.md`, `recovery.md`, `investigator.md`

At runtime, `build_worker_prompt()` injects an `Agent guardrails:` section and appends the serialized bead context. The applied template is stored under `metadata.guardrails` and `execution_history` for audit.

Only the most recent 5 `execution_history` entries are included in the prompt payload to keep prompt size bounded. The full history remains in bead storage and is unaffected.

### Template Placeholders

Bundled guardrail templates may contain `{{PLACEHOLDER}}` tokens that are substituted with project-specific values during `takt init`:

| Placeholder | Source | Example |
|---|---|---|
| `{{LANGUAGE}}` | `answers.language` | `Python`, `TypeScript/Node.js` |
| `{{TEST_COMMAND}}` | `answers.test_command` | `pytest`, `npm test` |
| `{{BUILD_CHECK_COMMAND}}` | `answers.build_check_command` | `tsc --noEmit`, `go build ./...` |

Substitution is performed by `onboarding.substitute_template_placeholders()`. The `takt init` command calls `onboarding.install_templates_with_substitution()`, which reads each bundled template, substitutes all recognised tokens, and writes the result to `templates/agents/`. Placeholders that appear in raw bundled templates are replaced in the installed copies — unrecognised `{{...}}` tokens are left as-is.

## Verdict-First Review and Test Results

Review and tester beads produce structured verdict fields:

- `verdict`: `approved` or `needs_changes`
- `findings_count`: number of unresolved findings
- `requires_followup`: explicit follow-up signal

The scheduler treats `verdict` as the control-flow source of truth:
- `approved` completes the bead regardless of narrative `remaining` text
- `needs_changes` blocks the bead and requires a `block_reason`

## Investigator Beads

Investigator beads run open-ended, read-only analysis tasks — technical debt surveys, dependency audits, coverage reviews, API surface analysis — without triggering any downstream followup beads.

**When to use investigator vs other types:**

| Type | Use when... |
|---|---|
| `investigator` | You want findings and recommendations with no code changes |
| `developer` | You want code changes (also auto-creates test/docs/review followups) |
| `review` | You want a verdict on an existing changeset |
| `documentation` | You want to update docs tied to a specific bead's changes |
| `planner` | You want to decompose a spec into a bead graph |

**Creating an investigator bead:**

```bash
uv run takt bead create \
  --agent investigator \
  --title "Technical debt survey: scheduler package" \
  --description "Survey src/agent_takt/scheduler/ for technical debt: duplication, complexity hotspots, missing abstractions, and test coverage gaps. Write findings to docs/investigator/scheduler-technical-debt.md" \
  --expected-files docs/investigator/scheduler-technical-debt.md
```

**Report file convention:** Reports are written to `docs/investigator/<slug>.md` where `<slug>` is derived from the bead title (lowercase, hyphens). Declare the path in `--expected-files` so the scheduler's conflict check applies to the report file's write scope.

**Report structure:** The agent writes a Markdown file with these sections:

```markdown
# <bead title>

*Generated by investigator bead <bead_id> on <date>*

## Executive Summary

<one paragraph>

## Findings

<detailed, numbered findings>

## Recommendations

<prioritised, numbered action items>

## Risk Areas

<what is at risk if findings are left unaddressed>
```

**Output schema:** Investigator beads use a distinct output schema. Required fields: `outcome`, `summary`, `findings`, `recommendations`, `risk_areas`, `report_path`, `block_reason`. Set `block_reason` to an empty string when the bead is not blocked. The fields `verdict`, `changed_files`, and `next_agent` are absent — they are not applicable to read-only investigation beads.

**No followup beads:** When an investigator bead completes, the scheduler does not create `-test`, `-docs`, or `-review` child beads.

## Bead Priority

Beads support a `priority` field that controls scheduling order within the set of already-eligible beads.

**Supported values:** `high` and `normal` (the default). `normal` is represented as `None` internally and displays as an empty cell in `takt bead list --plain`.

**Setting priority at creation:**

```bash
takt bead create --agent developer --title "Urgent fix" --description "..." --priority high
```

**Changing priority after creation:**

```bash
takt bead set-priority <bead_id> high    # elevate to high
takt bead set-priority <bead_id> normal  # revert to default
```

**Scheduler behaviour:** At each cycle, `ready` beads are sorted so that `high`-priority beads are dispatched before `normal`-priority beads. Priority controls *ordering only*:
- It does **not** bypass dependency resolution — a high-priority bead still waits for its dependencies to reach `done`.
- It does **not** promote sibling or related beads — only the bead itself is elevated.
- It does **not** override file-scope conflict deferral — a high-priority bead conflicting with an in-progress bead is still deferred.

**Viewing priority:** The `PRIORITY` column appears in `takt bead list --plain`. Beads with `normal` priority display as empty in that column.

## Conflict-Aware Scope

Beads declare `expected_files` and `expected_globs` at creation. The scheduler checks for overlap between in-progress beads and defers conflicting ones. Active file claims are visible via `takt bead claims`.

## Configuration

Settings live in `.takt/config.yaml`. See `src/agent_takt/config.py` for the full schema. Key sections: `common` (scheduler), `codex`, `claude` (per-backend binary, flags, tools, models, timeouts).

## Multi-Backend Support

Two runner backends: `codex` and `claude`. Select via `--runner`, `$AGENT_TAKT_RUNNER`, or `config.default_runner`. `$ORCHESTRATOR_RUNNER` is accepted as a legacy fallback.

See [multi-backend-agents.md](multi-backend-agents.md) for full details.

## Bead Auto-Commit

`RepositoryStorage` automatically commits every bead write and deletion through the storage chokepoint. No manual `git add`/`git commit` is required for bead metadata.

- **Write**: After each `_write_bead()` call, `_git_commit_bead()` stages and commits the bead JSON file. The commit message is `[bead] <id>: created (<agent_type>)` for new beads and `[bead] <id>: <status>` for updates.
- **Deletion**: After `delete_bead()` removes the file, `_git_commit_bead_deletion()` commits the removal with message `[bead] <id>: deleted`.

Both methods are best-effort: git failures are non-fatal so storage operations succeed even when git is unavailable (e.g., detached HEAD, no repo). On failure:

- **`_git_commit_bead`**: logs a warning via `logger.warning()` (with full traceback), appends a `git_commit_failed` event to the bead's `execution_history`, and persists the updated bead directly to disk (bypassing `_write_bead` to avoid infinite recursion).
- **`_git_commit_bead_deletion`**: logs a warning only — the bead file is already removed at that point so execution history cannot be updated.

Concurrent writes are serialized via an instance-level `threading.Lock` (`self._git_lock`). Using an instance-level lock (rather than a class-level one) prevents cross-instance blocking in parallel test runs.

The auto-commit behavior keeps the feature branch in a clean state with respect to bead metadata, which is important for the merge preflight — the rebase step compares against `main` and will not encounter unstaged bead changes.

## CI / Release Automation

The project uses a single GitHub Actions workflow (`.github/workflows/ci.yml`) that triggers on every push to `main`. It runs three sequential jobs:

1. **Test** — installs dependencies with `uv` and runs the full test suite (`uv run pytest tests/ -n auto -q`). All downstream jobs are gated on this passing.

2. **Build** — after tests pass, CI automatically bumps the **patch** component of the version in `pyproject.toml` (via `uv version --bump patch`), commits the change back to `main` with the message `chore: bump version to <version> [skip ci]`, then builds the package with `uv build`.

3. **Publish** — downloads the built distribution and creates a GitHub release tagged `v<version>` with auto-generated release notes, attaching all files from `dist/`.

### Version management

CI only ever increments the patch version. If you need to bump the major or minor version, do so locally before pushing:

```bash
uv version --bump minor   # or major
git add pyproject.toml
git commit -m "chore: bump minor version to X.Y.0"
git push
```

CI will then apply its patch bump on top of your bump.

### Secrets

No manual secret configuration is required. The workflow uses the repository's built-in `GITHUB_TOKEN` for both committing the version bump and creating the GitHub release.

## Telemetry

Two-tier storage per bead execution:
1. Lightweight metrics in `bead.metadata["telemetry"]` and `telemetry_history`
2. Full prompt/response artifact at `.takt/telemetry/<bead_id>/<attempt>.json`

See [scheduler-telemetry.md](scheduler-telemetry.md) for the full schema.
