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
  scheduler.py    Orchestration loop: leases, conflicts, followups
  storage.py      Bead JSON persistence under .orchestrator/beads/
  models.py       Bead, Lease, HandoffSummary, AgentRunResult
  runner.py       AgentRunner ABC + CodexAgentRunner, ClaudeCodeAgentRunner
  prompts.py      Worker/planner prompt construction + guardrail loading
  skills.py       Skill allowlists and isolated execution root setup
  gitutils.py     Worktree creation, commits, merges
  planner.py      Spec-to-bead-graph planning service
  tui.py          Textual-based interactive UI
  console.py      CLI output helpers (spinners, colours)

templates/agents/   Guardrail templates per agent type (mandatory)
.agents/skills/     Skill definitions (SKILL.md + agents/openai.yaml)
.orchestrator/      Runtime state: beads/, logs/, worktrees/, agent-runs/, config.yaml
```

## Key Concepts

**Beads** are the unit of work. Lifecycle: `open` -> `ready` -> `in_progress` -> `done` | `blocked` | `handed_off`.

**Agent types**: `planner`, `developer`, `tester`, `documentation`, `review`. Only `developer`, `tester`, `documentation` mutate code.

**Verdicts**: Review and tester beads produce `verdict: approved | needs_changes`. Verdict is the control-flow signal; narrative fields (`completed`, `remaining`) are context only.

**Followup beads**: When a developer bead completes, the scheduler auto-creates `-test`, `-docs`, `-review` children.

**Corrective beads**: Transient failures (quota, timeout) get up to 2 automatic `-corrective` retries.

## Multi-Backend Support

Two runners exist side by side. Select via `--runner` flag or `ORCHESTRATOR_RUNNER` env var (default: `codex`).

Isolated execution root layout per backend:

| | Codex | Claude Code |
|---|---|---|
| Skills directory | `exec_root/.agents/skills/` | `exec_root/.claude/skills/` |
| Agent steering | Embedded in prompt | `exec_root/CLAUDE.md` (auto-loaded) |
| CLI invocation | `codex exec --full-auto` | `claude -p --dangerously-skip-permissions` |

Beads are backend-agnostic. A bead started with Codex can be retried with Claude Code via `orchestrator --runner claude retry <bead_id>`.

## Configuration

Orchestrator settings live in `.orchestrator/config.yaml`. The config module (`src/codex_orchestrator/config.py`) loads this file and exposes frozen dataclasses:

- **`OrchestratorConfig`** -- top-level: `default_runner`, `templates_dir`, `agent_types`, `scheduler`, `backends`.
- **`SchedulerConfig`** -- lease timeouts, corrective/followup suffixes, transient failure patterns.
- **`BackendConfig`** -- per-backend binary path, skills dir, CLI flags, and tool allowlists.

Key functions:

- `load_config(root)` -- loads config from `root/.orchestrator/config.yaml`; falls back to `default_config()` if the file is missing.
- `default_config()` -- returns built-in defaults matching the previously hardcoded values.
- `config.allowed_tools_for(backend, agent_type)` -- returns the deduplicated union of default + per-agent tools for a backend.

If no config file exists, all behaviour is identical to the hardcoded defaults. The YAML file has three top-level blocks: `common` (shared settings and scheduler), `codex`, and `claude` (per-backend settings including tool allowlists).

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
