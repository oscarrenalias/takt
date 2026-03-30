# Externalize Configuration Phase 2: Runners and CLI

## Objective

Wire the config module (created in Phase 1) into `runner.py` and `cli.py`, replacing hardcoded CLI binary paths, flags, and the allowed-tools list with values read from `OrchestratorConfig` and `BackendConfig`.

## Prerequisites

- Phase 1 complete: `config.py` exists with `load_config()`, `default_config()`, `OrchestratorConfig`, `BackendConfig`
- `.orchestrator/config.yaml` exists with default values
- `pyyaml` is in `pyproject.toml`

## Scope

In scope:

- `cli.py`: load config at startup, thread it into runner and scheduler construction
- `runner.py`: both `CodexAgentRunner` and `ClaudeCodeAgentRunner` accept config, build CLI commands from config values, resolve per-agent-type allowed tools
- Remove the `_RUNNERS` registry in `cli.py` in favor of config-driven instantiation

Out of scope:

- Scheduler, skills, or prompts changes (covered by Phase 3)
- New CLI flags for config file path (use the default `.orchestrator/config.yaml` location)

## Functional Requirements

### 1. `cli.py` Changes

`make_services(root, runner_backend=None)` must:

1. Call `load_config(root)` to get `OrchestratorConfig`
2. Resolve the backend name: `runner_backend` arg > `$ORCHESTRATOR_RUNNER` env var > `config.default_runner`
3. Get the `BackendConfig` via `config.backend(backend_name)`
4. Instantiate the correct runner class:
   - `"codex"` -> `CodexAgentRunner(config=config, backend=backend_cfg)`
   - `"claude"` -> `ClaudeCodeAgentRunner(config=config, backend=backend_cfg)`
   - Unknown backend -> `SystemExit` with valid options listed from `config.backends.keys()`
5. Pass `config` to `Scheduler(storage, runner, worktrees, config=config)` -- the scheduler does not consume config in this phase, but accepting it now avoids a second signature change in Phase 3
6. Pass `config` to `PlanningService(storage, runner)` -- no changes needed to PlanningService internals

The `_RUNNERS` dict and the `AgentRunner` import used only for its type annotation in that dict can be removed. The `--runner` flag choices should be derived from `config.backends.keys()` or remain hardcoded as `["codex", "claude"]` (either is acceptable).

### 2. `runner.py` Changes

**`AgentRunner` base class**: Add `config` and `backend` as constructor parameters stored as instance attributes. The `backend_name` property already exists and should continue to work.

**`CodexAgentRunner.__init__`**: Replace `codex_bin` parameter with `config` and `backend`. Store them as instance attributes.

**`CodexAgentRunner._exec_json()`**: Build the command from config:

```
cmd = [
    self.backend.binary,
    "exec",
    *self.backend.flags,
    "--output-schema", str(schema_path),
    "--output-last-message", str(output_path),
    "-C", str(workdir),
    "-",
]
```

The `--output-schema`, `--output-last-message`, `-C`, and `-` flags are structural (they carry per-invocation values) and must NOT come from config. Only the backend-level flags (`--skip-git-repo-check`, `--full-auto`, `--color never`) are externalized.

**`ClaudeCodeAgentRunner.__init__`**: Replace `claude_bin` parameter with `config` and `backend`. Store them as instance attributes.

**`ClaudeCodeAgentRunner._exec_json()`**: Accept an additional `agent_type: str | None = None` keyword parameter. Build the command from config:

```
tools = self.config.allowed_tools_for("claude", agent_type or "developer")
cmd = [
    self.backend.binary,
    "-p",
    *self.backend.flags,
    "--allowedTools", ",".join(tools),
    "--output-format", "json",
    "--json-schema", json.dumps(schema),
]
```

The `--output-format json`, `--json-schema`, and `-p` flags are structural and must NOT come from config.

**`ClaudeCodeAgentRunner.run_bead()`**: Pass `agent_type=bead.agent_type` to `_exec_json()` so the tool list varies per agent.

**`ClaudeCodeAgentRunner._retry_structured_output()`**: The retry call should use the same `agent_type` as the original call. Add `agent_type` parameter and thread it through.

**`ClaudeCodeAgentRunner.propose_plan()`**: Pass `agent_type="planner"` to `_exec_json()`.

**Backward compatibility for tests**: If existing tests instantiate `CodexAgentRunner()` or `ClaudeCodeAgentRunner()` with no arguments, provide a convenience path: if `config` is not passed, call `default_config()` internally. This avoids breaking test fixtures that construct runners directly.

### 3. Remove Hardcoded Values

After this phase, the following hardcoded values must no longer appear in `runner.py` or `cli.py`:

- `"codex"` as a binary name (now `config.backend("codex").binary`)
- `"claude"` as a binary name (now `config.backend("claude").binary`)
- `"--skip-git-repo-check"`, `"--full-auto"`, `"--color"`, `"never"` (now `config.backend("codex").flags`)
- `"--dangerously-skip-permissions"` (now `config.backend("claude").flags`)
- The `--allowedTools` string (now `config.allowed_tools_for("claude", agent_type)`)
- Default runner `"codex"` in `make_services` (now `config.default_runner`)

## Acceptance Criteria

- `orchestrator --runner claude run --once` resolves allowed tools from config per agent type
- `orchestrator --runner codex run --once` builds the codex command from config flags
- `orchestrator run --once` (no `--runner`) uses `config.default_runner`
- Modifying `claude.allowed_tools_by_agent.developer` in `config.yaml` changes what tools the developer agent gets
- Modifying `codex.binary` in `config.yaml` to a non-existent path produces a clear error
- Removing `.orchestrator/config.yaml` entirely still works (falls back to `default_config()`)
- All existing tests pass (`uv run python -m unittest discover -s tests -v`)

## Files

| File | Action |
|------|--------|
| `src/codex_orchestrator/cli.py` | Modify |
| `src/codex_orchestrator/runner.py` | Modify |
