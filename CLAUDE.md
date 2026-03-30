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
  tui.py          Textual-based interactive UI (async scheduler, collapsible tree, telemetry)
  console.py      CLI output helpers (spinners, spinner pool, colours)

templates/agents/   Guardrail templates per agent type (mandatory)
.agents/skills/     Skill definitions (SKILL.md + agents/openai.yaml)
.orchestrator/      Runtime state: beads/, logs/, worktrees/, telemetry/, agent-runs/, config.yaml
```

## Key Concepts

**Beads** are the unit of work. Lifecycle: `open` -> `ready` -> `in_progress` -> `done` | `blocked` | `handed_off`.

**Agent types**: `planner`, `developer`, `tester`, `documentation`, `review`. Only `developer`, `tester`, `documentation` mutate code.

**Verdicts**: Review and tester beads produce `verdict: approved | needs_changes`. Verdict is the control-flow signal; narrative fields (`completed`, `remaining`) are context only.

**Followup beads**: When a developer bead completes, the scheduler auto-creates followup children using suffixes from `config.scheduler.followup_suffixes` (default: `-test`, `-docs`, `-review`).

**Corrective beads**: Transient failures matching `config.scheduler.transient_block_patterns` get up to `config.scheduler.max_corrective_attempts` (default 2) automatic `-corrective` retries.

## Multi-Backend Support

Two runners exist side by side. Select via `--runner` flag, `ORCHESTRATOR_RUNNER` env var, or `config.default_runner` (resolved in that priority order).

Isolated execution root layout per backend:

| | Codex | Claude Code |
|---|---|---|
| Skills directory | `exec_root/.agents/skills/` | `exec_root/.claude/skills/` |
| Agent steering | Embedded in prompt | `exec_root/CLAUDE.md` (auto-loaded) |
| CLI invocation | `codex exec --full-auto` | `claude -p --dangerously-skip-permissions` |

Both runners accept `config: OrchestratorConfig` and `backend: BackendConfig` at construction. Binary paths, CLI flags, and allowed tools are read from config -- not hardcoded. If constructed without arguments (e.g. in tests), runners fall back to `default_config()`.

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

### Per-agent model selection

Claude Code supports per-agent-type model selection via `config.model_for("claude", agent_type)`. The model is resolved as: `model_by_agent[agent_type]` if set, otherwise `model_default`, otherwise `None` (omits `--model` flag, letting the CLI use its own default).

`BackendConfig` carries two fields for this:

- `model_default: str | None` -- fallback model for all agent types (default: `"claude-sonnet-4-6"`).
- `model_by_agent: dict[str, str]` -- per-agent overrides. Defaults assign `claude-sonnet-4-6` to compute-heavy agent types (`developer`, `tester`, `planner`) and `claude-haiku-4-5-20251001` to lighter agent types (`review`, `documentation`).

In `.orchestrator/config.yaml`, these appear under the `claude` block:

```yaml
claude:
  model_default: claude-sonnet-4-6
  model_by_agent:
    developer: claude-sonnet-4-6
    tester: claude-sonnet-4-6
    planner: claude-sonnet-4-6
    review: claude-haiku-4-5-20251001
    documentation: claude-haiku-4-5-20251001
```

The runner passes `--model <model>` to the `claude` CLI when a model is resolved. This applies to both the main `run_bead()` call and any structured-output retry.

### Per-bead model override

Individual beads can override the config-level model selection by setting `metadata.model_override`. The full resolution order for Claude Code is: `bead.metadata["model_override"]` > `config.model_by_agent[agent_type]` > `config.model_default` > `None` (CLI default).

Set via CLI:

```bash
uv run orchestrator bead update <id> --model claude-opus-4-6
```

When a developer bead completes, the scheduler propagates `model_override` from the parent to all followup children (test, docs, review) and any dynamically discovered sub-beads. This ensures the entire feature subtree uses the same model without manual per-bead configuration.

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

When a structured-output retry occurs (single-turn, no-tool call to reformat a conversational response), `cost_usd` and `duration_api_ms` from the retry are added into the main response envelope before telemetry extraction. Other fields (`num_turns`, token counts, `session_id`) remain from the main run. This ensures telemetry reflects total spend without inflating turn/token metrics.

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
- **`BackendConfig`** -- per-backend binary path, skills dir, CLI flags, tool allowlists, and model selection.

Key functions:

- `load_config(root)` -- loads config from `root/.orchestrator/config.yaml`; falls back to `default_config()` if the file is missing.
- `default_config()` -- returns built-in defaults matching the previously hardcoded values.
- `config.backend(name)` -- returns the `BackendConfig` for a backend; raises `KeyError` with valid options on unknown name.
- `config.allowed_tools_for(backend, agent_type)` -- returns the deduplicated union of default + per-agent tools for a backend.
- `config.model_for(backend, agent_type)` -- returns the model for a specific agent type, falling back to `model_default`. Returns `None` if neither is set.

If no config file exists, all behaviour is identical to the hardcoded defaults. The YAML file has three top-level blocks: `common` (shared settings and scheduler), `codex`, and `claude` (per-backend settings including tool allowlists and model selection).

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

## Multi-Worker CLI Output

`orchestrator run --max-workers N` controls parallelism. The CLI output adapts based on `N`:

- **Single worker** (`--max-workers 1`, the default): Uses a single `Spinner` that animates on the current line, replaced by a status icon on completion.
- **Multiple workers** (`--max-workers N` where N > 1): Uses `SpinnerPool`, which reserves N terminal lines and updates each slot in-place via ANSI cursor positioning. Each active bead gets its own spinner line; finished beads show a final icon (✓/!/✗) in their slot.

Both modes are thread-safe — `ConsoleReporter` serializes all output through a lock. Non-TTY environments (pipes, CI) fall back to sequential line-by-line output with no cursor manipulation.

`CliSchedulerReporter` wraps both modes. It creates a `SpinnerPool` when `max_workers > 1` and calls `reporter.stop()` in a `finally` block to clean up the spinner region on exit.

## TUI Scheduler Integration

The TUI runs scheduler cycles asynchronously in a background worker thread so the UI remains responsive during long-running agent executions. Key components:

- **`--max-workers N`** flag on `orchestrator tui` controls scheduler parallelism (default 1). Passed through `run_tui()` and `build_tui_app()` into `TuiRuntimeState`.
- **`TuiSchedulerReporter`** implements `SchedulerReporter` and posts timestamped events (bead started/completed/blocked/failed) to the TUI's `RichLog` scheduler log widget via `app.call_from_thread()`.
- **Async worker pattern**: `_start_scheduler_worker()` launches `run_worker()` with `exclusive=True`. The worker calls `TuiRuntimeState.run_scheduler_cycle(reporter=...)` which invokes `scheduler.run_once()` directly (not `command_run`). On completion, `_on_scheduler_worker_done()` resets state and re-renders.
- **Guard against double-runs**: Both the app-level `_scheduler_worker_running` flag and the state-level `scheduler_running` flag prevent concurrent cycles.
- **Status bar**: The old 6-line status panel is replaced by a compact 2-line status bar (`#status-bar`) showing mode and status, plus a separate `#scheduler-log` `RichLog` widget for live scheduler output. The `[RUNNING]` indicator appears in the status bar while a cycle is active.
- **Continuous mode**: When timed refresh fires with `continuous_run_enabled`, it calls `_start_scheduler_worker()` (async) instead of blocking the UI thread.

Keybindings: `s` triggers a single scheduler cycle, `S` toggles continuous mode.

### TUI Telemetry Display

Bead telemetry (from `bead.metadata["telemetry"]`) surfaces in two places:

1. **Bead list / tree panel**: Each bead label appends a compact badge like `[$0.32, 2:55]` showing cost and duration. The badge is empty when no telemetry is available. Duration uses `duration_ms` (falling back to `duration_api_ms`) formatted as `m:ss`.

2. **Detail panel — Telemetry section**: A collapsible section (constant `DETAIL_SECTION_TELEMETRY`, added to `DETAIL_SECTION_ORDER`) displays: `cost_usd`, `duration`, `num_turns`, `input_tokens`, `output_tokens`, `cache_read_tokens`, `prompt_chars`, and `session_id`. When `telemetry_history` contains multiple attempts, an additional line shows the attempt count and cumulative cost.

## Conventions

- Guardrail templates are **mandatory**. Missing `templates/agents/{agent_type}.md` fails the bead with `FileNotFoundError`.
- Bead metadata is authoritative; always read/write through `RepositoryStorage`.
- Execution history is append-only (audit trail).
- Operator status updates are restricted: developer beads cannot be manually marked `done` (must go through scheduler to trigger followups).
- File-scope conflicts are checked statically at schedule time. Overlapping `expected_files`/`expected_globs` between in-progress beads cause blocking.
- Feature branches follow `feature/{feature_root_id}`. Worktrees live at `.orchestrator/worktrees/{feature_root_id}`.
- Bead IDs are sequential (`B0001`, `B0002`, ...). Children use suffixes (`B0001-test`, `B0001-review`).

## Testing

Tests use `unittest` (not pytest). `FakeRunner` mocks agent execution. Run with:

```bash
uv run python -m unittest discover -s tests -v
```

## Working with Beads

Always use the CLI to query bead state — do not read `.orchestrator/beads/*.json` files directly:

```bash
uv run orchestrator bead show <id>          # single bead details (JSON)
uv run orchestrator bead list --plain       # all beads as table
uv run orchestrator summary                 # counts + next actionable beads
uv run orchestrator summary --feature-root <id>  # scoped to a feature
```
