# Codex Agent Orchestration

Multi-agent orchestration system that coordinates AI workers (Codex or Claude Code) on a shared codebase using Git worktrees.

## Quick Reference

```bash
uv run python -m unittest discover -s tests -v   # run tests
uv run orchestrator summary                       # bead status overview
uv run orchestrator bead list --plain             # all beads as table
uv run orchestrator --runner claude run --once    # one scheduler cycle with Claude Code
uv run orchestrator tui                           # interactive terminal UI
```

## Project Layout

```
src/codex_orchestrator/
  cli.py          CLI dispatch and output formatting
  config.py       YAML config loader + frozen dataclass models
  scheduler.py    Orchestration loop: leases, conflicts, followups (all params from config)
  storage.py      Bead JSON persistence under .orchestrator/beads/ + telemetry artifacts
  models.py       Bead, Lease, HandoffSummary, AgentRunResult
  runner.py       AgentRunner ABC + CodexAgentRunner, ClaudeCodeAgentRunner
  prompts.py      Worker/planner prompt construction + guardrail loading (config-overridable)
  skills.py       Skill allowlists and isolated execution root setup (config-driven)
  gitutils.py     Worktree creation, commits, merges
  planner.py      Spec-to-bead-graph planning service
  tui.py          Textual-based interactive UI
  console.py      CLI output helpers (spinners, spinner pool, colours)

templates/agents/   Guardrail templates per agent type (mandatory)
.agents/skills/     Skill definitions (SKILL.md + agents/openai.yaml)
.orchestrator/      Runtime state: beads/, logs/, worktrees/, telemetry/, agent-runs/, config.yaml
```

## Key Concepts

**Beads** are the unit of work. Lifecycle: `open` -> `ready` -> `in_progress` -> `done` | `blocked` | `handed_off`.

**Agent types**: `planner`, `developer`, `tester`, `documentation`, `review`. Only `developer`, `tester`, `documentation` mutate code. Both `PLANNER_OUTPUT_SCHEMA` (for planner LLM responses) and `AGENT_OUTPUT_SCHEMA` (for worker agent responses proposing `new_beads`) enforce these values via a JSON schema `enum` constraint — invalid types are rejected at parse time before any bead is created. `planner.py` adds a Python-level check in `PlanningService.write_plan()` as defense-in-depth.

**Verdicts**: Review and tester beads produce `verdict: approved | needs_changes`. Verdict is the control-flow signal; narrative fields (`completed`, `remaining`) are context only.

**Followup beads**: When a developer bead completes, the scheduler auto-creates followup children using suffixes from `config.scheduler.followup_suffixes` (default: `-test`, `-docs`, `-review`). For shared followup beads (tester, documentation, or review beads that depend on multiple developer beads), the scheduler pre-populates `touched_files` and `changed_files` by aggregating the `touched_files` and `changed_files` from all done developer dependencies' handoff summaries before dispatching the bead. Files appearing in multiple developers' handoff summaries are deduplicated — each file path appears at most once in the resulting lists. Population is skipped (no-op) when fewer than two developer dependencies are in `done` status. This ensures downstream agents see the complete file scope across all contributing developers, not just those explicitly listed at bead creation time.

**Planner-owned followup suppression**: Developer beads that are children of a planner or feature-root bead (`_uses_planner_owned_followups` returns `True`) use shared planner-pre-created followup beads instead of auto-creating per-developer legacy children. When a developer bead is in a planner-owned feature tree, legacy followup creation is **fully suppressed** for all three followup types (tester, documentation, review) — even if no planner-owned bead is found for a given type. Scope syncing via `_sync_followup_scope` still runs when a matching planner-owned bead exists. Standalone/manual developer flows (no planner parent) continue to use the legacy per-developer child-bead creation path unchanged.

**Corrective beads**: Transient failures matching `config.scheduler.transient_block_patterns` get up to `config.scheduler.max_corrective_attempts` (default 2) automatic `-corrective` retries.

## Multi-Backend Support

Two runners exist side by side. Select via `--runner` flag, `ORCHESTRATOR_RUNNER` env var, or `config.default_runner` (resolved in that priority order).

Isolated execution root layout per backend:

| | Codex | Claude Code |
|---|---|---|
| Skills directory | `exec_root/.agents/skills/` | `exec_root/.claude/skills/` |
| Agent steering | Embedded in prompt | `exec_root/CLAUDE.md` (auto-loaded) |
| CLI invocation | `codex exec --full-auto` | `claude -p --dangerously-skip-permissions` |

Both runners accept `config: OrchestratorConfig` and `backend: BackendConfig` at construction. Binary paths, CLI flags, allowed tools, and subprocess timeouts are read from config -- not hardcoded. If constructed without arguments (e.g. in tests), runners fall back to `default_config()`.

### Subprocess timeouts

All agent subprocess calls enforce a configurable timeout via `BackendConfig.timeout_seconds` (default 600s / 10 minutes). Claude Code's structured-output retry call uses `BackendConfig.retry_timeout_seconds` (default 300s / 5 minutes). When a subprocess exceeds its timeout, a `RuntimeError` is raised with a descriptive message. Both values are overridable per-backend in `.orchestrator/config.yaml` under `timeout_seconds` and `retry_timeout_seconds`.

CLI commands are split into **structural flags** (per-invocation values like `--output-schema`, `--json-schema`, `-C`, `-p`) that stay in code, and **backend flags** (like `--full-auto`, `--dangerously-skip-permissions`) that come from `config.backend(name).flags`.

Claude Code's `--allowedTools` list is resolved per agent type via `config.allowed_tools_for("claude", agent_type)`, which merges the backend's `allowed_tools_default` with the agent-specific additions from `allowed_tools_by_agent`.

Default tools shared by all Claude Code agent types: `Edit`, `Write`, `Read`, `Bash`, `Glob`, `Grep`, `Skill`, `ToolSearch`, `WebSearch`, `WebFetch`.

Additional tools granted per agent type:

| Agent type | Extra tools |
|---|---|
| `developer` | `Agent`, `NotebookEdit`, `TaskCreate`, `TaskUpdate`, `TaskGet`, `TaskList` |
| `tester` | `Agent`, `TaskCreate`, `TaskUpdate`, `TaskGet`, `TaskList` |
| `documentation` | `NotebookEdit` |
| `planner` | _(none)_ |
| `review` | _(none)_ |

These defaults live in `default_config()` and can be overridden in `.orchestrator/config.yaml` under each backend's `allowed_tools_default` and `allowed_tools_by_agent` keys.

Beads are backend-agnostic. A bead started with Codex can be retried with Claude Code via `orchestrator --runner claude retry <bead_id>`.

### Runner telemetry capture

Both runners measure wall-clock duration and prompt size around every `run_bead()` call and attach the metrics to `AgentRunResult.telemetry` (a `dict[str, Any] | None`, defaults to `None`).

**Codex** captures minimal metrics (`source: "measured"`):

| Field | Description |
|---|---|
| `duration_ms` | Wall-clock time of the subprocess |
| `prompt_chars` | Prompt length in characters |
| `prompt_lines` | Prompt length in lines |
| `prompt_text` | Full prompt sent to the agent |
| `response_text` | Raw JSON response |

**Claude Code** additionally extracts provider-supplied fields from the JSON response envelope (`source: "provider"`):

| Field | Source in response |
|---|---|
| `cost_usd` | `total_cost_usd` |
| `duration_api_ms` | `duration_api_ms` |
| `num_turns` | `num_turns` |
| `input_tokens` | `usage.input_tokens` |
| `output_tokens` | `usage.output_tokens` |
| `cache_creation_tokens` | `usage.cache_creation_input_tokens` |
| `cache_read_tokens` | `usage.cache_read_input_tokens` |
| `stop_reason` | `stop_reason` |
| `session_id` | `session_id` |
| `permission_denials` | `permission_denials` |

The scheduler integrates telemetry into its `_finalize()` flow via `_store_telemetry()`, which runs after building the handoff summary but before outcome-specific processing (blocked/completed/failed). This ensures telemetry is captured for all outcomes.

### Scheduler telemetry integration

`Scheduler._store_telemetry(bead, agent_result)` implements two-tier storage:

1. **Tier 1 (bead metadata)**: Strips heavy fields (`prompt_text`, `response_text`) and stores lightweight metrics in `bead.metadata["telemetry"]` (latest attempt, overwritten each run) and appends to `bead.metadata["telemetry_history"]` (all attempts, capped).
2. **Tier 2 (artifact file)**: Writes the full prompt/response artifact via `storage.write_telemetry_artifact()`.

Attempt numbering is derived from `len(telemetry_history) + 1` at write time.

The history cap is configurable via `ORCHESTRATOR_TELEMETRY_MAX_ATTEMPTS` (default 10). Invalid, zero, or negative values fall back to the default. When exceeded, oldest entries are pruned after appending the new entry.

If the telemetry write fails, the bead outcome is preserved — a `telemetry_write_warning` event is appended to `execution_history` instead of raising.

### Telemetry artifact storage

Full prompt/response text for every bead execution attempt is persisted as a JSON artifact file at `.orchestrator/telemetry/<bead_id>/<attempt>.json`. These files are gitignored (heavy, potentially sensitive) and written atomically by `RepositoryStorage.write_telemetry_artifact()`.

Each artifact contains: `telemetry_version`, `bead_id`, `agent_type`, `attempt`, `started_at`, `finished_at`, `outcome`, `prompt_text`, `response_text`, `parsed_result`, `metrics`, and `error`. Failed attempts store `null` for `response_text`/`parsed_result` and populate `error` with `{"stage": "...", "message": "..."}`.

The `telemetry_dir` (`self.state_dir / "telemetry"`) is created alongside other state directories during `RepositoryStorage.initialize()`.

### Scheduler telemetry integration

After each bead execution, `Scheduler._finalize()` stores telemetry in two tiers: lightweight metrics in `bead.metadata["telemetry"]` (current attempt) and `bead.metadata["telemetry_history"]` (capped list of all attempts), plus full artifact files on disk. Heavy fields (`prompt_text`, `response_text`) are excluded from bead metadata.

The `telemetry_history` list is capped to 10 entries by default. Override with `ORCHESTRATOR_TELEMETRY_MAX_ATTEMPTS` env var (positive integer; invalid values fall back to default). Oldest entries are dropped when the cap is exceeded.

Telemetry failures are non-fatal: the bead outcome is preserved and a `telemetry_write_warning` event is recorded in `execution_history`.

See [docs/scheduler-telemetry.md](docs/scheduler-telemetry.md) for the full schema, flow diagram, and optimization signals table.

## Configuration

Orchestrator settings live in `.orchestrator/config.yaml`. The config module (`src/codex_orchestrator/config.py`) loads this file and exposes frozen dataclasses:

- **`OrchestratorConfig`** -- top-level: `default_runner`, `templates_dir`, `agent_types`, `scheduler`, `backends`.
- **`SchedulerConfig`** -- lease timeouts, corrective/followup suffixes, transient failure patterns.
- **`BackendConfig`** -- per-backend binary path, skills dir, CLI flags, tool allowlists, and subprocess timeouts.

Key functions:

- `load_config(root)` -- loads config from `root/.orchestrator/config.yaml`; falls back to `default_config()` if the file is missing.
- `default_config()` -- returns built-in defaults matching the previously hardcoded values.
- `config.backend(name)` -- returns the `BackendConfig` for a backend; raises `KeyError` with valid options on unknown name.
- `config.allowed_tools_for(backend, agent_type)` -- returns the deduplicated union of default + per-agent tools for a backend.

If no config file exists, all behaviour is identical to the hardcoded defaults. The YAML file has three top-level blocks: `common` (shared settings and scheduler), `codex`, and `claude` (per-backend settings including tool allowlists).

### Config wiring

`cli.make_services(root, runner_backend)` is the entry point that threads config through the system:

1. Loads config via `load_config(root)`.
2. Resolves the backend: `runner_backend` arg > `$ORCHESTRATOR_RUNNER` > `config.default_runner`.
3. Looks up the runner class from `_RUNNER_CLASSES` and the `BackendConfig` from `config.backend(name)`.
4. Passes both `config` and `backend` to the runner constructor.

Unknown backend names produce a `SystemExit` listing valid options from `config.backends.keys()`.

### Scheduler config wiring

`Scheduler.__init__` reads all operational parameters from `self.config.scheduler` into instance attributes. There are no module-level constants for scheduler tuning -- all values come from config:

| Instance attribute | Config source | Default |
|---|---|---|
| `self.followup_suffixes` | `config.scheduler.followup_suffixes` | `{"tester": "test", "documentation": "docs", "review": "review"}` |
| `self.corrective_suffix` | `config.scheduler.corrective_suffix` | `"corrective"` |
| `self.max_corrective_attempts` | `config.scheduler.max_corrective_attempts` | `2` |
| `self.transient_block_patterns` | `config.scheduler.transient_block_patterns` | 10 built-in patterns (auth, timeout, etc.) |
| `self.lease_timeout_minutes` | `config.scheduler.lease_timeout_minutes` | `30` |
| `self.runnable_reassign_agents` | `config.agent_types` | all 5 built-in types |
| `self.followup_agent_by_suffix` | derived from `followup_suffixes` | `{"-test": "tester", ...}` |

### Skills config wiring

`prepare_isolated_execution_root()` accepts `config: OrchestratorConfig` and `runner_backend: str`. The skills directory is resolved via `config.backend(runner_backend).skills_dir` (`.agents` for Codex, `.claude` for Claude Code). `AGENT_SKILL_ALLOWLIST` remains as a module-level constant intentionally -- it is tightly coupled to the skill directory structure and not externalized to YAML.

### Prompts config wiring

`guardrail_template_path()` and `load_guardrail_template()` accept optional `templates_dir` and `agent_types` parameters. When provided, they override the built-in `DEFAULT_TEMPLATES_DIR` and `BUILT_IN_AGENT_TYPES` constants. The scheduler passes `config.templates_dir` and `config.agent_types` to these functions. `supported_agent_types(config_types)` returns the config-provided list or falls back to the built-in constant.

`build_worker_prompt()` caps the `execution_history` included in the prompt payload to the last `_EXECUTION_HISTORY_PROMPT_CAP` entries (default 5) to keep prompt size bounded. The full history is preserved in bead storage and is unaffected by this cap.

## Multi-Worker CLI Output

`orchestrator run --max-workers N` controls parallelism. The CLI output adapts based on `N`:

- **Single worker** (`--max-workers 1`, the default): Uses a single `Spinner` that animates on the current line, replaced by a status icon on completion.
- **Multiple workers** (`--max-workers N` where N > 1): Uses `SpinnerPool`, which reserves N terminal lines and updates each slot in-place via ANSI cursor positioning. Each active bead gets its own spinner line; finished beads show a final icon (✓/!/✗) in their slot.

Both modes are thread-safe — `ConsoleReporter` serializes all output through a lock. Non-TTY environments (pipes, CI) fall back to sequential line-by-line output with no cursor manipulation.

`CliSchedulerReporter` wraps both modes. It creates a `SpinnerPool` when `max_workers > 1` and calls `reporter.stop()` in a `finally` block to clean up the spinner region on exit.

### Run cycle summary output

After `orchestrator run` completes (with `--once` or when no more ready beads remain), the CLI prints two summary lines and a JSON block:

1. **Cycle summary** (success or warn): `started N, completed N, blocked N, deferred N (total cycles)` — counts across all scheduler cycles in the run. Each bead ID is deduplicated: if a bead is started in multiple cycles, it appears only once in the final counts. `deferred` is an integer total across all cycles (not deduplicated).
2. **Final state** (info): `N done, N blocked, N ready` — live counts from storage after the run, scoped to the feature root if `--feature-root` was specified.

The JSON object emitted via `console.dump_json` has the following shape:

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

`started`, `completed`, `blocked`, and `correctives_created` are sorted lists of unique bead IDs. `deferred_count` is the cumulative integer count of deferred events across all cycles. `final_state` is a dict of all bead statuses present in storage (or the scoped feature, if `--feature-root` was given) mapped to their counts.

## Conventions

- Guardrail templates are **mandatory**. Missing `templates/agents/{agent_type}.md` fails the bead with `FileNotFoundError`.
- Bead metadata is authoritative; always read/write through `RepositoryStorage`.
- Execution history is append-only (audit trail).
- Operator status updates are restricted: developer beads cannot be manually marked `done` (must go through scheduler to trigger followups).
- File-scope conflicts are checked statically at schedule time. Overlapping `expected_files`/`expected_globs` between in-progress beads cause blocking.
- **Branch naming**: Feature branches are named `feature/{feature_root_id.lower()}`. For example, a feature root ID `B-a7bc3f91` produces branch name `feature/b-a7bc3f91` (lowercased for Git convention compatibility).
- **Worktree paths**: Worktrees are created at `.orchestrator/worktrees/{feature_root_id}` using the feature root ID directly (not lowercased). Example: `.orchestrator/worktrees/B-a7bc3f91`.
- **Bead ID allocation**: Root beads use UUID format (`B-{first 8 hex chars}`). Child beads append suffixes (`B-abc12def-test`, `B-abc12def-review`). The UUID generation ensures short, unique, and hyphenated ID format that works with Git branch names (via lowercasing) and filesystem paths.
- **Bead sorting**: Beads are sorted by creation timestamp (first `execution_history` entry timestamp), falling back to bead ID on tie. This ensures consistent ordering independent of ID generation strategy.
- **Prefix resolution**: Use `RepositoryStorage.resolve_bead_id(prefix)` to resolve ambiguous or partial bead ID matches. Supports exact IDs or partial prefixes (e.g., `B-a7bc` matches `B-a7bc3f91`). Returns the full ID if exactly one match exists, raises `ValueError` on zero or multiple matches.

## Testing

Tests use `unittest` (not pytest). `FakeRunner` mocks agent execution. Run with:

```bash
uv run python -m unittest discover -s tests -v
```

## No Manual Code Changes

This project is self-hosting — all code changes go through beads, including bug fixes and hotfixes. Do not edit source files directly. Create a bead, let the system implement it, and merge via the normal pipeline. The only exceptions are CLAUDE.md, config files, and spec files.

## Running Commands

All commands must be prefixed with `uv run`. This is the only supported way to run the orchestrator and tests:

```bash
uv run orchestrator ...                     # any orchestrator command
uv run python -m unittest discover -s tests -v  # run tests
```

Do not invoke `orchestrator` or `python` directly without `uv run`.

## Working with Beads

Always use the CLI to query bead state — do not read `.orchestrator/beads/*.json` files directly:

```bash
uv run orchestrator bead show <id>          # single bead details (JSON)
uv run orchestrator bead list --plain       # all beads as table
uv run orchestrator summary                 # counts + next actionable beads
uv run orchestrator summary --feature-root <id>  # scoped to a feature
uv run orchestrator bead delete <id>        # delete a bead (open/ready/blocked only)
uv run orchestrator bead delete <id> --force  # delete regardless of status
```

**Deleting beads**: `bead delete` removes a bead and its JSON file. Safety rules enforced by `RepositoryStorage.delete_bead()`:

- The bead must exist (raises `ValueError` otherwise).
- The bead must have no children — beads whose `parent_id` matches the deleted bead (raises `ValueError` listing child IDs).
- Without `--force`, only `open`, `ready`, and `blocked` beads can be deleted. Beads with status `in_progress`, `done`, or `handed_off` require `--force`.
- Deleting a bead removes its ID from the `dependencies` list of all other beads automatically.
- When deleting a **feature root** bead (where `feature_root_id == bead_id`), the CLI also removes the associated Git worktree and feature branch (`feature/<bead_id_lowercased>`). If the worktree has uncommitted changes, a warning is printed but removal proceeds (`git worktree remove --force`). Worktree and branch deletion failures are non-fatal: a warning is emitted and the command still exits successfully.
- The CLI removes artifact directories `.orchestrator/agent-runs/<bead_id>/` and `.orchestrator/telemetry/<bead_id>/` if they exist (non-fatal if absent).
- After a successful deletion, a `bead_deleted` event (with `bead_id` and `title`) is appended to `.orchestrator/logs/events.jsonl` for audit purposes.
