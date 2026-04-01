# Development Guide

## Project Layout

```
src/codex_orchestrator/
  cli.py          CLI dispatch and output formatting
  config.py       YAML config loader + frozen dataclass models
  scheduler.py    Orchestration loop: leases, conflicts, followups
  storage.py      Bead JSON persistence + telemetry artifacts
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
.orchestrator/      Runtime state: beads/, logs/, worktrees/, telemetry/, agent-runs/, config.yaml
```

## Testing

```bash
uv run python -m unittest discover -s tests -v
```

Tests use `unittest` (not pytest). `FakeRunner` mocks agent execution. Target individual modules with:

```bash
uv run python -m unittest tests.test_orchestrator -v
uv run python -m unittest tests.test_tui -v
```

## Agent Guardrails

Guardrail templates live in `templates/agents/` and are mandatory — a missing template fails the bead with `FileNotFoundError`. The built-in set:

- `planner.md`, `developer.md`, `tester.md`, `documentation.md`, `review.md`

At runtime, `build_worker_prompt()` injects an `Agent guardrails:` section and appends the serialized bead context. The applied template is stored under `metadata.guardrails` and `execution_history` for audit.

Only the most recent 5 `execution_history` entries are included in the prompt payload to keep prompt size bounded. The full history remains in bead storage and is unaffected.

## Verdict-First Review and Test Results

Review and tester beads produce structured verdict fields:

- `verdict`: `approved` or `needs_changes`
- `findings_count`: number of unresolved findings
- `requires_followup`: explicit follow-up signal

The scheduler treats `verdict` as the control-flow source of truth:
- `approved` completes the bead regardless of narrative `remaining` text
- `needs_changes` blocks the bead and requires a `block_reason`

## Conflict-Aware Scope

Beads declare `expected_files` and `expected_globs` at creation. The scheduler checks for overlap between in-progress beads and defers conflicting ones. Active file claims are visible via `orchestrator bead claims`.

## Configuration

Settings live in `.orchestrator/config.yaml`. See `src/codex_orchestrator/config.py` for the full schema. Key sections: `common` (scheduler), `codex`, `claude` (per-backend binary, flags, tools, models, timeouts).

## Multi-Backend Support

Two runner backends: `codex` and `claude`. Select via `--runner`, `$ORCHESTRATOR_RUNNER`, or `config.default_runner`.

See [multi-backend-agents.md](multi-backend-agents.md) for full details.

## Telemetry

Two-tier storage per bead execution:
1. Lightweight metrics in `bead.metadata["telemetry"]` and `telemetry_history`
2. Full prompt/response artifact at `.orchestrator/telemetry/<bead_id>/<attempt>.json`

See [scheduler-telemetry.md](scheduler-telemetry.md) for the full schema.
