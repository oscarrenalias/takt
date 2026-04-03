# Multi-Backend Agent Support

The orchestrator supports multiple agent backends (Codex and Claude Code) side by side. Both execute beads identically through the scheduler, but differ in how they discover skills and receive steering context.

## Selecting a Backend

CLI flag (takes precedence):

```bash
orchestrator --runner claude run --once
orchestrator --runner codex plan spec.md
```

Environment variable (default when no flag is given):

```bash
export ORCHESTRATOR_RUNNER=claude
```

Falls back to `codex` if neither is set.

## Runner Architecture

`AgentRunner` is the abstract base in `src/codex_orchestrator/runner.py`. Each backend implements:

| Method | Purpose |
|---|---|
| `backend_name` | Returns `"codex"` or `"claude"` |
| `run_bead()` | Invokes the agent CLI and parses structured JSON output |
| `propose_plan()` | Invokes the agent CLI for planning |

Both runners share the same prompt construction (`prompts.py`), output schemas (`AGENT_OUTPUT_SCHEMA`, `PLANNER_OUTPUT_SCHEMA`), and bead lifecycle. Both schemas enforce `agent_type` as a JSON schema `enum` (`planner`, `developer`, `tester`, `documentation`, `review`), so responses containing an invalid agent type are rejected at parse time. `PlanningService.write_plan()` in `planner.py` adds a second Python-level check before creating beads.

## Isolated Execution Root

Before each bead runs, `prepare_isolated_execution_root()` in `skills.py` creates a per-bead directory under `.orchestrator/agent-runs/{bead_id}/`. The layout varies by backend:

### Codex

```
exec_root/
  .agents/skills/          # Codex auto-discovers skills here
  repo/                    # Symlink to feature worktree
  home/                    # Isolated home directory
```

### Claude Code

```
exec_root/
  .claude/skills/          # Claude Code auto-discovers skills here
  CLAUDE.md                # Guardrail template (agent steering)
  repo/                    # Symlink to feature worktree
  home/                    # Isolated home directory
```

## Skills

Skills live in the repository under `.agents/skills/` and follow the [Agent Skills](https://agentskills.io) open standard. The catalog is organized by responsibility: `core/`, `role/`, `capability/`, `task/`, plus the shared `memory/` skill. Each skill directory contains a `SKILL.md` with YAML frontmatter (`name`, `description`) and repository-specific instructions.

`prepare_isolated_execution_root()` copies skills from the catalog repository root, not from the per-bead workspace symlink. That keeps the execution bundle stable even when a feature worktree only contains a partial `.agents/skills/` tree.

The `AGENT_SKILL_ALLOWLIST` in `skills.py` controls which skills each agent type can access. Each worker agent receives `core/base-orchestrator`, its role-specific skill, and the shared `memory` skill. Most agent types also receive capability and task skills; the `planner` is an exception — it gets `task/spec-intake` and `task/dependency-graphing` but no `capability/` skill. The `scheduler` agent type is limited to `core/base-orchestrator` and `role/scheduler-policy`. Only the allowlisted skills are copied into the execution root.

| Aspect | Codex | Claude Code |
|---|---|---|
| Target directory | `exec_root/.agents/skills/` | `exec_root/.claude/skills/` |
| Discovery | Auto (Codex reads `.agents/skills/`) | Auto (Claude Code reads `.claude/skills/`) |
| Policy file | `agents/openai.yaml` per skill | SKILL.md frontmatter (`allowed-tools`, `disable-model-invocation`) |

The same source SKILL.md files are used for both backends. The `openai.yaml` policy files are Codex-specific and ignored by Claude Code.

## Agent Steering (Guardrail Templates)

Role-specific guardrail templates live in `templates/agents/{agent_type}.md` and define what each agent type is allowed and disallowed to do.

| Aspect | Codex | Claude Code |
|---|---|---|
| Delivery | Embedded in the worker prompt by `build_worker_prompt()` | Written to `exec_root/CLAUDE.md` (auto-loaded natively) |
| Prompt inclusion | Yes (always) | Yes (also included in prompt) |

For Claude Code, the guardrail template is written as a `CLAUDE.md` file in the execution root during skill isolation setup. Claude Code loads `CLAUDE.md` automatically from the working directory tree, so the agent receives role-specific constraints without additional prompt tokens.

## CLI Invocation Differences

| | Codex | Claude Code |
|---|---|---|
| Command | `codex exec` | `claude -p` |
| Auto-approve | `--full-auto` | `--dangerously-skip-permissions` |
| Schema | `--output-schema <file>` (temp file) | `--json-schema '<json>'` (inline) |
| Output | `--output-last-message <file>` (temp file) | `--output-format json` (stdout) |
| Working dir | `-C <path>` flag | `cwd=` on subprocess |
| Prompt input | stdin (`-`) | stdin (piped) |

## Tool Allowlists (Claude Code)

Claude Code's `--allowedTools` list is resolved per agent type via `config.allowed_tools_for("claude", agent_type)`, which merges the backend's `allowed_tools_default` with agent-specific additions from `allowed_tools_by_agent`.

Default tools shared by all Claude Code agent types:

`Edit`, `Write`, `Read`, `Bash`, `Glob`, `Grep`, `Skill`, `ToolSearch`, `WebSearch`, `WebFetch`

Additional tools granted per agent type:

| Agent type | Extra tools |
|---|---|
| `developer` | `Agent`, `NotebookEdit`, `TaskCreate`, `TaskUpdate`, `TaskGet`, `TaskList` |
| `tester` | `Agent`, `TaskCreate`, `TaskUpdate`, `TaskGet`, `TaskList` |
| `documentation` | `NotebookEdit` |
| `planner` | _(none)_ |
| `review` | _(none)_ |

These defaults live in `default_config()` and can be overridden in `.orchestrator/config.yaml` under each backend's `allowed_tools_default` and `allowed_tools_by_agent` keys.

Codex does not use an `--allowedTools` flag; tool access is controlled via the `agents/openai.yaml` policy files in each skill directory.

## Subprocess Timeouts

All agent subprocess calls enforce a configurable timeout. Both values are read from `BackendConfig` and can be overridden per backend in `.orchestrator/config.yaml`.

| Config key | Default | Applies to |
|---|---|---|
| `timeout_seconds` | `600` (10 min) | Main `run_bead()` / `propose_plan()` subprocess |
| `retry_timeout_seconds` | `300` (5 min) | Claude Code structured-output retry call only |

When a subprocess exceeds its timeout, a `RuntimeError` is raised with a descriptive message. The bead is then marked `blocked` by the scheduler.

## Config Wiring

`cli.make_services(root, runner_backend)` is the single entry point that threads config through the system:

1. Loads config via `load_config(root)`.
2. Resolves the backend: `runner_backend` arg > `$ORCHESTRATOR_RUNNER` > `config.default_runner`.
3. Looks up the runner class from `_RUNNER_CLASSES` and the `BackendConfig` from `config.backend(name)`.
4. Passes both `config` and `backend` to the runner constructor.

Unknown backend names produce a `SystemExit` listing valid options from `config.backends.keys()`.

Both runners accept `config: OrchestratorConfig` and `backend: BackendConfig` at construction. Binary paths, CLI flags, allowed tools, and subprocess timeouts are read from config — not hardcoded. If constructed without arguments (e.g. in tests), runners fall back to `default_config()`.

### Scheduler config wiring

`Scheduler.__init__` reads all operational parameters from `self.config.scheduler` into instance attributes. There are no module-level constants for scheduler tuning — all values come from config:

| Instance attribute | Config source | Default |
|---|---|---|
| `self.followup_suffixes` | `config.scheduler.followup_suffixes` | `{"tester": "test", "documentation": "docs", "review": "review"}` |
| `self.corrective_suffix` | `config.scheduler.corrective_suffix` | `"corrective"` |
| `self.max_corrective_attempts` | `config.scheduler.max_corrective_attempts` | `2` |
| `self.transient_block_patterns` | `config.scheduler.transient_block_patterns` | 10 built-in patterns |
| `self.lease_timeout_minutes` | `config.scheduler.lease_timeout_minutes` | `30` |

### Skills config wiring

`prepare_isolated_execution_root()` accepts `config: OrchestratorConfig` and `runner_backend: str`. The skills directory is resolved via `config.backend(runner_backend).skills_dir` (`.agents` for Codex, `.claude` for Claude Code). `AGENT_SKILL_ALLOWLIST` remains a module-level constant — it is tightly coupled to the skill directory structure and not externalized to YAML.

### Prompts config wiring

`guardrail_template_path()` and `load_guardrail_template()` accept optional `templates_dir` and `agent_types` parameters. When provided, they override the built-in `DEFAULT_TEMPLATES_DIR` and `BUILT_IN_AGENT_TYPES` constants. The scheduler passes `config.templates_dir` and `config.agent_types` to these functions.

## Runner Telemetry

Both runners capture telemetry metrics around every `run_bead()` call and attach them to `AgentRunResult.telemetry` (a `dict[str, Any] | None`, defaults to `None`). The scheduler stores telemetry opaquely and does not interpret its contents.

Each telemetry dict includes a `source` field indicating how the metrics were obtained:

- **`"measured"`** — wall-clock timing and prompt size computed by the runner itself (Codex).
- **`"provider"`** — includes measured metrics plus additional fields extracted from the agent's JSON response envelope (Claude Code).

### Codex telemetry fields

All fields are locally measured by the runner (`source: "measured"`).

| Field | Type | Description |
|---|---|---|
| `duration_ms` | `int` | Wall-clock time of the subprocess in milliseconds |
| `prompt_chars` | `int` | Prompt length in characters |
| `prompt_lines` | `int` | Prompt length in lines |
| `prompt_text` | `str` | Full prompt sent to the agent |
| `response_text` | `str` | Raw JSON response from the agent |
| `source` | `str` | Always `"measured"` |

### Claude Code telemetry fields

Includes locally measured metrics plus provider-reported fields extracted from the response (`source: "provider"`).

| Field | Type | Source in response | Description |
|---|---|---|---|
| `cost_usd` | `float \| None` | `total_cost_usd` | Total API cost in USD |
| `duration_ms` | `int` | measured | Wall-clock time in milliseconds |
| `duration_api_ms` | `int \| None` | `duration_api_ms` | API-reported duration |
| `num_turns` | `int \| None` | `num_turns` | Number of conversation turns |
| `input_tokens` | `int \| None` | `usage.input_tokens` | Input tokens consumed |
| `output_tokens` | `int \| None` | `usage.output_tokens` | Output tokens generated |
| `cache_creation_tokens` | `int \| None` | `usage.cache_creation_input_tokens` | Tokens used for cache creation |
| `cache_read_tokens` | `int \| None` | `usage.cache_read_input_tokens` | Tokens read from cache |
| `stop_reason` | `str \| None` | `stop_reason` | Why the agent stopped |
| `session_id` | `str \| None` | `session_id` | Agent session identifier |
| `permission_denials` | `Any \| None` | `permission_denials` | Permission denial events |
| `prompt_chars` | `int` | measured | Prompt length in characters |
| `prompt_lines` | `int` | measured | Prompt length in lines |
| `prompt_text` | `str` | measured | Full prompt sent to the agent |
| `response_text` | `str` | measured | Raw JSON response |
| `source` | `str` | — | Always `"provider"` |

## Telemetry Artifact Storage

Full prompt and response text for every bead execution attempt is persisted as a JSON artifact file. These artifacts provide a complete audit trail for debugging and post-hoc analysis.

### Storage path

Artifacts are written to:

```
.orchestrator/telemetry/<bead_id>/<attempt>.json
```

For example, the second attempt of bead `B0042` is stored at `.orchestrator/telemetry/B0042/2.json`. The `telemetry/` directory is created automatically during `RepositoryStorage.initialize()`.

### Artifact JSON schema

Each artifact file contains:

| Field | Type | Description |
|---|---|---|
| `telemetry_version` | `int` | Schema version (currently `1`) |
| `bead_id` | `str` | Bead identifier |
| `agent_type` | `str` | Agent type that executed the bead (e.g. `"developer"`, `"tester"`) |
| `attempt` | `int` | Attempt number (1-based) |
| `started_at` | `str` | ISO 8601 timestamp when execution began |
| `finished_at` | `str` | ISO 8601 timestamp when execution ended |
| `outcome` | `str` | Result of the run: `"completed"`, `"blocked"`, or `"failed"` |
| `prompt_text` | `str \| null` | Full prompt sent to the agent |
| `response_text` | `str \| null` | Raw JSON response from the agent (`null` on failure) |
| `parsed_result` | `object \| null` | Parsed structured output (`null` on failure) |
| `metrics` | `object` | Runner telemetry metrics (see tables above) |
| `error` | `object \| null` | Error details with `"stage"` and `"message"` keys (`null` on success) |

### Write behavior

Artifacts are written atomically: the runner writes to a temporary file first, then renames it into place. This prevents partial files from appearing if the process is interrupted.

### Gitignore

Telemetry artifacts are excluded from version control via `.gitignore`:

```
.orchestrator/telemetry/
```

These files can be large (they contain full prompts and responses) and may include sensitive context, so they are kept local-only. Bead metadata in `.orchestrator/beads/` remains tracked by Git.
