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
  skills.py       Per-agent skill catalog allowlists and isolated execution root setup (config-driven)
  gitutils.py     Worktree creation, commits, merges
  planner.py      Spec-to-bead-graph planning service
  tui.py          Textual-based interactive UI
  console.py      CLI output helpers (spinners, spinner pool, colours)

templates/agents/   Guardrail templates per agent type (mandatory)
.agents/skills/     Shared skill catalog (`core/`, `role/`, `capability/`, `task/`, `memory/`)
.orchestrator/      Runtime state: beads/, logs/, worktrees/, telemetry/, agent-runs/, config.yaml
```

## Key Concepts

**Beads** are the unit of work. Lifecycle: `open` -> `ready` -> `in_progress` -> `done` | `blocked` | `handed_off`.

**Agent types**: `planner`, `developer`, `tester`, `documentation`, `review`. Only `developer`, `tester`, `documentation` mutate code. Invalid types are rejected at parse time via JSON schema `enum` constraints in both `PLANNER_OUTPUT_SCHEMA` and `AGENT_OUTPUT_SCHEMA`.

**Verdicts**: Review and tester beads produce `verdict: approved | needs_changes`. Verdict is the control-flow signal; narrative fields are context only.

**Followup beads**: When a developer bead completes, the scheduler auto-creates `-test`, `-docs`, `-review` children. For planner-owned feature trees, shared followup beads are used instead — legacy per-developer children are suppressed. Scope syncing (`_sync_followup_scope`) still runs when a matching planner-owned bead exists. Standalone developer flows use legacy per-developer creation unchanged.

**Corrective beads**: Transient failures matching `config.scheduler.transient_block_patterns` get up to `config.scheduler.max_corrective_attempts` (default 2) automatic `-corrective` retries.

## Multi-Backend Support

Select backend via `--runner` flag, `ORCHESTRATOR_RUNNER` env var, or `config.default_runner` (resolved in that priority order).

| | Codex | Claude Code |
|---|---|---|
| Skills directory | `exec_root/.agents/skills/` | `exec_root/.claude/skills/` |
| Agent steering | Embedded in prompt | `exec_root/CLAUDE.md` (auto-loaded) |
| CLI invocation | `codex exec --full-auto` | `claude -p --dangerously-skip-permissions` |

The skill catalog is role-scoped rather than global. `skills.py` keeps a fixed `AGENT_SKILL_ALLOWLIST` that bundles `core/base-orchestrator`, one role skill, and `memory` for every worker agent type. Most types also receive capability and task skills; the `planner` is an exception — it gets `task/spec-intake` and `task/dependency-graphing` but no `capability/` skill. The `scheduler` backend uses only scheduler-specific skills and does not receive `memory`.

Beads are backend-agnostic. A bead started with Codex can be retried with Claude Code via `orchestrator --runner claude retry <bead_id>`.

See [docs/multi-backend-agents.md](docs/multi-backend-agents.md) for tool allowlists, subprocess timeouts, runner telemetry fields, and config wiring details.

## Configuration

Orchestrator settings live in `.orchestrator/config.yaml`. Key dataclasses: `OrchestratorConfig`, `SchedulerConfig`, `BackendConfig`. Falls back to built-in defaults if the file is missing. The YAML file has three top-level blocks: `common`, `codex`, and `claude`.

Key functions in `config.py`: `load_config(root)`, `default_config()`, `config.backend(name)`, `config.allowed_tools_for(backend, agent_type)`.

## Multi-Worker CLI Output

`orchestrator run --max-workers N` controls parallelism. Single-worker uses a `Spinner`; multi-worker uses `SpinnerPool` (N reserved terminal lines, ANSI cursor positioning). Both are thread-safe. Non-TTY falls back to line-by-line output.

After `orchestrator run` completes, the CLI prints a cycle summary and emits a JSON block:

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
- Execution history is append-only (audit trail).
- Operator status updates are restricted: developer beads cannot be manually marked `done` (must go through scheduler to trigger followups).
- File-scope conflicts are checked statically at schedule time. Overlapping `expected_files`/`expected_globs` between in-progress beads cause blocking.
- **Branch naming**: `feature/{feature_root_id.lower()}` (e.g. `B-a7bc3f91` → `feature/b-a7bc3f91`).
- **Worktree paths**: `.orchestrator/worktrees/{feature_root_id}` (not lowercased).
- **Bead ID allocation**: Root beads: `B-{first 8 hex chars}`. Child beads append suffixes (`B-abc12def-test`, `B-abc12def-review`).
- **Bead sorting**: By creation timestamp (first `execution_history` entry), falling back to bead ID on tie.
- **Prefix resolution**: `RepositoryStorage.resolve_bead_id(prefix)` resolves partial IDs; raises `ValueError` on zero or multiple matches.

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

`bead delete` enforces: bead must exist, have no children, and be in a deletable status (`open`, `ready`, `blocked` without `--force`; `in_progress`, `done`, `handed_off` require `--force`). Deleting a feature root bead also removes the associated Git worktree and feature branch. Artifact directories (`.orchestrator/agent-runs/<id>/`, `.orchestrator/telemetry/<id>/`) are removed. A `bead_deleted` event is appended to `.orchestrator/logs/events.jsonl`.
