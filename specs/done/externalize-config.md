# Externalize Configuration to YAML

## Objective

Introduce a central YAML configuration file at `.orchestrator/config.yaml` and a Python config module that loads it, replacing the hardcoded constants scattered across `runner.py`, `cli.py`, `skills.py`, `scheduler.py`, and `prompts.py`.

## Why This Matters

Configuration is currently hardcoded across multiple modules. Changing the Claude Code allowed-tools list, switching the default runner backend, or adjusting scheduler lease timeouts all require code changes. The most pressing need is per-agent-type tool allowlisting for Claude Code: today all agent types share the same hardcoded `--allowedTools` string, but a planner agent should not have the same tools as a developer agent.

## Scope

In scope:

- New `src/codex_orchestrator/config.py` with frozen dataclass models and a YAML loader
- New `.orchestrator/config.yaml` with default values matching all current hardcoded constants
- Add `pyyaml>=6,<7` dependency to `pyproject.toml`
- Wire config into `runner.py`, `cli.py`, `scheduler.py`, `skills.py`, and `prompts.py`
- Per-agent-type Claude Code tool allowlisting

Out of scope:

- Moving `AGENT_SKILL_ALLOWLIST` into YAML (tightly coupled to skill directory structure)
- Config validation beyond type checking
- CLI commands for inspecting or editing config

## Implementation Phases

Work should be done incrementally in three phases within a single feature branch. Each phase depends on the previous one.

### Phase 1: Config Module and YAML File

Create the config infrastructure.

**New file `src/codex_orchestrator/config.py`:**

Three frozen dataclasses:

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

Methods on `OrchestratorConfig`:
- `backend(name: str) -> BackendConfig` -- raises `KeyError` listing valid backends if not found
- `allowed_tools_for(backend: str, agent_type: str) -> list[str]` -- returns deduplicated union of `allowed_tools_default` + `allowed_tools_by_agent.get(agent_type, [])`

Public functions:
- `load_config(root: Path) -> OrchestratorConfig` -- loads `.orchestrator/config.yaml`; falls back to `default_config()` if file does not exist
- `default_config() -> OrchestratorConfig` -- returns hardcoded defaults matching current constants

**New file `.orchestrator/config.yaml`:**

```yaml
common:
  default_runner: codex
  templates_dir: templates/agents
  agent_types:
    - planner
    - developer
    - tester
    - documentation
    - review
  scheduler:
    lease_timeout_minutes: 30
    max_corrective_attempts: 2
    followup_suffixes:
      tester: test
      documentation: docs
      review: review
    corrective_suffix: corrective
    transient_block_patterns:
      - "high demand"
      - "internal server error"
      - "timeout"
      - "timed out"
      - "connection reset"
      - "connection refused"
      - "temporarily unavailable"
      - "service unavailable"
      - "missing bearer"
      - "unauthorized"

codex:
  binary: codex
  skills_dir: .agents
  flags:
    - "--skip-git-repo-check"
    - "--full-auto"
    - "--color"
    - "never"

claude:
  binary: claude
  skills_dir: .claude
  flags:
    - "--dangerously-skip-permissions"
  allowed_tools_default:
    - Edit
    - Write
    - Read
    - Bash
    - Glob
    - Grep
    - Skill
    - ToolSearch
    - WebSearch
    - WebFetch
  allowed_tools_by_agent:
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

**Dependency:** Add `pyyaml>=6,<7` to `pyproject.toml`.

### Phase 2: Runners and CLI

Wire config into `runner.py` and `cli.py`.

**`cli.py`:**
- `make_services()` calls `load_config(root)` and threads config through
- Backend resolved: `--runner` flag > `$ORCHESTRATOR_RUNNER` > `config.default_runner`
- Runner instantiated from config: `CodexAgentRunner(config=config, backend=backend_cfg)` or `ClaudeCodeAgentRunner(config=config, backend=backend_cfg)`
- Remove `_RUNNERS` registry dict

**`runner.py`:**
- Both runners accept `config: OrchestratorConfig` and `backend: BackendConfig` in `__init__`
- `CodexAgentRunner._exec_json()`: build cmd from `self.backend.binary` + `self.backend.flags` (structural flags like `--output-schema`, `-C`, `-` remain hardcoded as they carry per-invocation values)
- `ClaudeCodeAgentRunner._exec_json()`: accept `agent_type` param, resolve tools via `self.config.allowed_tools_for("claude", agent_type)`, build cmd from `self.backend.binary` + `self.backend.flags`
- `run_bead()` passes `agent_type=bead.agent_type` to `_exec_json()`
- `propose_plan()` passes `agent_type="planner"` to `_exec_json()`
- `_retry_structured_output()` threads `agent_type` through
- Backward compatibility: if `config` not passed to constructor, call `default_config()` internally (avoids breaking test fixtures)

**Remove hardcoded values:** binary names, CLI flags, `--allowedTools` string, default runner `"codex"` — all replaced by config reads.

### Phase 3: Scheduler, Skills, and Prompts

Wire config into remaining modules.

**`scheduler.py`:**
- `Scheduler.__init__()` accepts and stores `config`
- Replace module-level constants:
  - `FOLLOWUP_SUFFIXES` -> `self.config.scheduler.followup_suffixes`
  - `CORRECTIVE_SUFFIX` -> `self.config.scheduler.corrective_suffix`
  - `MAX_CORRECTIVE_ATTEMPTS` -> `self.config.scheduler.max_corrective_attempts`
  - `TRANSIENT_BLOCK_PATTERNS` -> `self.config.scheduler.transient_block_patterns`
  - `timedelta(minutes=30)` -> `timedelta(minutes=self.config.scheduler.lease_timeout_minutes)`
- `FOLLOWUP_AGENT_BY_SUFFIX`: compute from config in `__init__`, store as instance attribute
- `RUNNABLE_REASSIGN_AGENTS`: derive from `self.config.agent_types`
- Remove replaced module-level constants

**`skills.py`:**
- Remove `_BACKEND_SKILLS_DIR` dict
- `prepare_isolated_execution_root()`: accept `config: OrchestratorConfig` and `runner_backend: str`, derive skills dir from `config.backend(runner_backend).skills_dir`
- `AGENT_SKILL_ALLOWLIST`: keep as module-level constant (intentionally not externalized; tightly coupled to skill directory structure)

**`prompts.py`:**
- Keep `BUILT_IN_AGENT_TYPES` and `DEFAULT_TEMPLATES_DIR` as fallback constants
- Add optional `agent_types` and `templates_dir` parameters to `guardrail_template_path()` and `load_guardrail_template()`
- Callers pass config values when available; fall back to constants when not

## Acceptance Criteria

- `load_config(root)` returns valid config when `.orchestrator/config.yaml` exists
- `load_config(root)` returns `default_config()` when no config file exists
- `default_config()` returns values identical to current hardcoded constants
- `allowed_tools_for("claude", "developer")` returns merged default + developer tools, deduplicated
- `allowed_tools_for("claude", "planner")` returns only default tools
- `allowed_tools_for("codex", "developer")` returns empty list
- `backend("nonexistent")` raises `KeyError` with valid options listed
- Modifying `claude.allowed_tools_by_agent.developer` in YAML changes actual tools at runtime
- Modifying `common.scheduler.lease_timeout_minutes` in YAML changes lease duration
- Removing `.orchestrator/config.yaml` entirely still works (falls back to defaults)
- All existing tests pass (`uv run python -m unittest discover -s tests -v`)
- Config dataclasses are frozen (immutable after creation)

## Files

| File | Action |
|------|--------|
| `src/codex_orchestrator/config.py` | Create |
| `.orchestrator/config.yaml` | Create |
| `pyproject.toml` | Modify (add pyyaml) |
| `src/codex_orchestrator/runner.py` | Modify |
| `src/codex_orchestrator/cli.py` | Modify |
| `src/codex_orchestrator/scheduler.py` | Modify |
| `src/codex_orchestrator/skills.py` | Modify |
| `src/codex_orchestrator/prompts.py` | Modify |
