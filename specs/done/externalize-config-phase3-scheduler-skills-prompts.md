# Externalize Configuration Phase 3: Scheduler, Skills, and Prompts

## Objective

Complete the config externalization by wiring `OrchestratorConfig` into `scheduler.py`, `skills.py`, and `prompts.py`, replacing the remaining hardcoded constants with values from config.

## Prerequisites

- Phase 1 complete: `config.py` with dataclasses and loader
- Phase 2 complete: `cli.py` loads config and passes it to `Scheduler`; runners accept config
- `Scheduler.__init__` already accepts `config` parameter (added in Phase 2 for forward compatibility)

## Scope

In scope:

- `scheduler.py`: replace module-level constants with reads from `config.scheduler`
- `skills.py`: replace `_BACKEND_SKILLS_DIR` and `AGENT_SKILL_ALLOWLIST` with config-driven values
- `prompts.py`: replace `BUILT_IN_AGENT_TYPES` and `DEFAULT_TEMPLATES_DIR` with config values

Out of scope:

- Moving `AGENT_SKILL_ALLOWLIST` into the YAML file (the skill allowlist is tightly coupled to the skill directory structure and guardrail templates; externalizing it to YAML would require also externalizing the skill catalog, which is a separate concern)
- Config file validation or schema enforcement

## Functional Requirements

### 1. `scheduler.py` Changes

Replace the following module-level constants with reads from `self.config.scheduler`:

| Current constant | Config path | Type |
|-----------------|-------------|------|
| `FOLLOWUP_SUFFIXES` | `config.scheduler.followup_suffixes` | `dict[str, str]` |
| `CORRECTIVE_SUFFIX` | `config.scheduler.corrective_suffix` | `str` |
| `MAX_CORRECTIVE_ATTEMPTS` | `config.scheduler.max_corrective_attempts` | `int` |
| `TRANSIENT_BLOCK_PATTERNS` | `config.scheduler.transient_block_patterns` | `tuple[str, ...]` |
| `timedelta(minutes=30)` (line 342) | `timedelta(minutes=config.scheduler.lease_timeout_minutes)` | `int` |

The `Scheduler` class already stores `self.config` (from Phase 2). All references to the module-level constants must be replaced with `self.config.scheduler.<field>`.

`FOLLOWUP_AGENT_BY_SUFFIX` (derived from `FOLLOWUP_SUFFIXES`) should be computed from `self.config.scheduler.followup_suffixes` in `__init__` and stored as an instance attribute.

`RUNNABLE_REASSIGN_AGENTS` (currently `set(BUILT_IN_AGENT_TYPES)`) should be derived from `self.config.agent_types`.

The module-level constants can be removed entirely once all references are replaced.

### 2. `skills.py` Changes

**`_BACKEND_SKILLS_DIR`**: Remove this dict. The skills directory is now derived from `config.backend(runner_backend).skills_dir`.

**`prepare_isolated_execution_root()`**: The function currently accepts `runner_backend: str`. Change the signature to accept `config: OrchestratorConfig` and `runner_backend: str`:

```python
def prepare_isolated_execution_root(
    *,
    orchestrator_state_dir: Path,
    catalog_repo_root: Path,
    workspace_repo_root: Path,
    bead: Bead,
    config: OrchestratorConfig,
    runner_backend: str,
) -> tuple[Path, dict[str, object]]:
```

Inside the function:
- Replace `_BACKEND_SKILLS_DIR.get(runner_backend, ".agents")` with `config.backend(runner_backend).skills_dir`
- The CLAUDE.md generation logic (for `runner_backend == "claude"`) stays as-is

The caller in `scheduler.py` already has `self.config` and `self.runner.backend_name`; it passes both.

**`AGENT_SKILL_ALLOWLIST`**: Keep as a module-level constant in this phase. Externalizing the skill catalog is a separate concern (the allowlist keys map to skill directories on disk, and changing one without the other would break execution). Add a comment noting this is intentionally not externalized.

### 3. `prompts.py` Changes

**`BUILT_IN_AGENT_TYPES`**: Keep as a module-level constant but add a function that accepts an override:

```python
def supported_agent_types(config_types: list[str] | None = None) -> tuple[str, ...]:
    return tuple(config_types) if config_types else BUILT_IN_AGENT_TYPES
```

Functions that validate agent types (`guardrail_template_path`, `load_guardrail_template`) should accept an optional `agent_types` parameter. When not provided, they fall back to `BUILT_IN_AGENT_TYPES`.

**`DEFAULT_TEMPLATES_DIR`**: Add an optional `templates_dir` parameter to `guardrail_template_path()` and `load_guardrail_template()`. When provided as a relative path, resolve it relative to the repo root. When not provided, fall back to `DEFAULT_TEMPLATES_DIR`.

The scheduler and runner already call `load_guardrail_template(bead.agent_type, root=runner_workdir)`. These call sites should additionally pass `templates_dir=config.templates_dir` and `agent_types=config.agent_types` when config is available.

## Acceptance Criteria

- Changing `common.scheduler.lease_timeout_minutes` in `config.yaml` changes the lease duration without code changes
- Changing `common.scheduler.max_corrective_attempts` limits corrective retries accordingly
- Changing `common.scheduler.transient_block_patterns` controls which errors trigger automatic correctives
- `prepare_isolated_execution_root` uses `config.backend("claude").skills_dir` (`.claude`) for Claude Code and `config.backend("codex").skills_dir` (`.agents`) for Codex
- `AGENT_SKILL_ALLOWLIST` remains in `skills.py` as a module-level constant (not externalized)
- `BUILT_IN_AGENT_TYPES` and `DEFAULT_TEMPLATES_DIR` remain as fallback constants, overridable via config
- Removing `.orchestrator/config.yaml` still works (all modules fall back to defaults)
- All existing tests pass (`uv run python -m unittest discover -s tests -v`)
- No module-level constants in `scheduler.py` for values that now come from config

## Files

| File | Action |
|------|--------|
| `src/codex_orchestrator/scheduler.py` | Modify |
| `src/codex_orchestrator/skills.py` | Modify |
| `src/codex_orchestrator/prompts.py` | Modify |
