# Externalize Configuration Phase 1: Config Module and YAML File

## Objective

Introduce a central YAML configuration file at `.orchestrator/config.yaml` and a Python config module that loads it, replacing the hardcoded constants scattered across the codebase. This first phase creates the config infrastructure; subsequent phases wire it into existing modules.

## Why This Matters

Configuration is currently hardcoded across `runner.py`, `cli.py`, `skills.py`, `scheduler.py`, and `prompts.py`. Changing the Claude Code allowed-tools list, switching the default runner backend, or adjusting scheduler lease timeouts all require code changes. This blocks operators from tuning behaviour per project or per environment.

The most pressing need is per-agent-type tool allowlisting for Claude Code. Today, all agent types (developer, tester, reviewer, planner, documentation) share the same hardcoded `--allowedTools` string. A planner agent does not need `Agent` or `NotebookEdit`; a tester does not need `Write`. Granular control requires externalizing this config.

## Scope

In scope:

- New file `src/codex_orchestrator/config.py` with frozen dataclass models and a YAML loader
- New file `.orchestrator/config.yaml` with default values matching all current hardcoded constants
- Add `pyyaml>=6,<7` to `pyproject.toml` dependencies
- Unit tests for config loading, defaults, merge logic, and missing-file fallback

Out of scope:

- Wiring config into runner, CLI, scheduler, skills, or prompts (covered by Phase 2 and Phase 3)
- Config file validation beyond type checking (no JSON Schema enforcement)
- CLI commands for inspecting or editing config

## Functional Requirements

### 1. YAML Config File

The config file lives at `.orchestrator/config.yaml` (alongside the existing `beads/`, `logs/`, and `worktrees/` directories). It contains three top-level blocks:

**`common`** -- shared settings:

| Key | Type | Default | Purpose |
|-----|------|---------|---------|
| `default_runner` | string | `"codex"` | Default backend when no `--runner` flag or `$ORCHESTRATOR_RUNNER` is set |
| `templates_dir` | string | `"templates/agents"` | Path to guardrail templates, relative to repo root |
| `agent_types` | list of strings | `["planner", "developer", "tester", "documentation", "review"]` | Built-in agent types |
| `scheduler.lease_timeout_minutes` | integer | `30` | Minutes before an in-progress lease expires |
| `scheduler.max_corrective_attempts` | integer | `2` | Max automatic corrective beads per failure |
| `scheduler.corrective_suffix` | string | `"corrective"` | Suffix for corrective bead IDs |
| `scheduler.followup_suffixes` | mapping | `{tester: test, documentation: docs, review: review}` | Bead ID suffixes for followup agent types |
| `scheduler.transient_block_patterns` | list of strings | `["high demand", "internal server error", "timeout", "timed out", "connection reset", "connection refused", "temporarily unavailable", "service unavailable", "missing bearer", "unauthorized"]` | Substring patterns that indicate transient (retryable) failures |

**`codex`** -- Codex backend settings:

| Key | Type | Default | Purpose |
|-----|------|---------|---------|
| `binary` | string | `"codex"` | Path or name of the Codex CLI binary |
| `skills_dir` | string | `".agents"` | Parent directory for skills inside the isolated execution root |
| `flags` | list of strings | `["--skip-git-repo-check", "--full-auto", "--color", "never"]` | Extra CLI flags passed to every `codex exec` invocation |

**`claude`** -- Claude Code backend settings:

| Key | Type | Default | Purpose |
|-----|------|---------|---------|
| `binary` | string | `"claude"` | Path or name of the Claude Code CLI binary |
| `skills_dir` | string | `".claude"` | Parent directory for skills inside the isolated execution root |
| `flags` | list of strings | `["--dangerously-skip-permissions"]` | Extra CLI flags passed to every `claude -p` invocation |
| `allowed_tools_default` | list of strings | `["Edit", "Write", "Read", "Bash", "Glob", "Grep", "Skill", "ToolSearch", "WebSearch", "WebFetch"]` | Base tool set available to all Claude Code agent types |
| `allowed_tools_by_agent` | mapping of agent type to list of strings | see below | Per-agent-type additional tools merged with the default set |

Default `allowed_tools_by_agent`:

```yaml
developer:
  - Agent
  - NotebookEdit
  - TaskCreate
  - TaskUpdate
  - TaskGet
  - TaskList
tester:
  - Agent
  - TaskCreate
  - TaskUpdate
  - TaskGet
  - TaskList
planner: []
review: []
documentation:
  - NotebookEdit
```

### 2. Config Module (`config.py`)

The module exposes three frozen dataclasses and two public functions:

```
SchedulerConfig
  lease_timeout_minutes: int
  max_corrective_attempts: int
  followup_suffixes: dict[str, str]
  corrective_suffix: str
  transient_block_patterns: tuple[str, ...]

BackendConfig
  binary: str
  skills_dir: str
  flags: list[str]
  allowed_tools_default: list[str]
  allowed_tools_by_agent: dict[str, list[str]]

OrchestratorConfig
  default_runner: str
  templates_dir: str
  agent_types: list[str]
  scheduler: SchedulerConfig
  backends: dict[str, BackendConfig]
```

Public methods on `OrchestratorConfig`:

- `backend(name: str) -> BackendConfig` -- returns the backend config for the given name; raises `KeyError` with a clear message listing valid backends if the name is not found.
- `allowed_tools_for(backend: str, agent_type: str) -> list[str]` -- returns deduplicated union of `allowed_tools_default` + `allowed_tools_by_agent.get(agent_type, [])` for the named backend. For backends with empty tool lists (e.g., Codex), returns an empty list.

Public functions:

- `load_config(root: Path) -> OrchestratorConfig` -- loads `.orchestrator/config.yaml` relative to `root`. Falls back to `default_config()` if the file does not exist. Raises on malformed YAML.
- `default_config() -> OrchestratorConfig` -- returns an `OrchestratorConfig` with all defaults matching the current hardcoded values. This ensures zero breaking changes for existing setups with no config file.

### 3. Dependency

Add `pyyaml>=6,<7` to `pyproject.toml` under `[project] dependencies`.

## Acceptance Criteria

- `load_config(root)` returns a valid `OrchestratorConfig` when `.orchestrator/config.yaml` exists
- `load_config(root)` returns `default_config()` when no config file exists
- `default_config()` returns values identical to the current hardcoded constants in `runner.py`, `cli.py`, `skills.py`, `scheduler.py`, and `prompts.py`
- `allowed_tools_for("claude", "developer")` returns the merged default + developer-specific tools, deduplicated
- `allowed_tools_for("claude", "planner")` returns only the default tools (planner has empty override)
- `allowed_tools_for("codex", "developer")` returns an empty list (Codex does not use `--allowedTools`)
- `backend("codex")` and `backend("claude")` return the correct `BackendConfig` instances
- `backend("nonexistent")` raises `KeyError`
- All existing tests continue to pass (`uv run python -m unittest discover -s tests -v`)
- Config dataclasses are frozen (immutable after creation)

## Files

| File | Action |
|------|--------|
| `src/codex_orchestrator/config.py` | Create |
| `.orchestrator/config.yaml` | Create |
| `pyproject.toml` | Modify (add pyyaml) |
| `tests/test_config.py` | Create (optional, recommended) |
