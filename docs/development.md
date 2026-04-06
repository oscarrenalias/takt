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
  graph.py        Mermaid bead graph renderer (render_bead_graph)
  planner.py      Spec-to-bead-graph planning service
  tui.py          Textual-based interactive UI
  console.py      CLI output helpers (spinners, colours)
  _assets.py      importlib.resources helpers for locating bundled package data
  onboarding.py   scaffold_project() and asset-install helpers used by orchestrator init

templates/agents/   Guardrail templates per agent type (mandatory)
.agents/skills/     Skill definitions (SKILL.md + agents/openai.yaml)
.orchestrator/      Runtime state: beads/, logs/, worktrees/, telemetry/, agent-runs/, config.yaml
```

## Testing

```bash
uv run pytest tests/ -n auto -q
```

Tests run via pytest (with xdist for parallel execution). `FakeRunner` mocks agent execution. Target individual modules with:

```bash
uv run pytest tests/test_orchestrator.py -v
uv run pytest tests/test_tui.py -v
```

## Agent Guardrails

Guardrail templates live in `templates/agents/` and are mandatory — a missing template fails the bead with `FileNotFoundError`. The built-in set:

- `planner.md`, `developer.md`, `tester.md`, `documentation.md`, `review.md`

At runtime, `build_worker_prompt()` injects an `Agent guardrails:` section and appends the serialized bead context. The applied template is stored under `metadata.guardrails` and `execution_history` for audit.

Only the most recent 5 `execution_history` entries are included in the prompt payload to keep prompt size bounded. The full history remains in bead storage and is unaffected.

### Template Placeholders

Bundled guardrail templates may contain `{{PLACEHOLDER}}` tokens that are substituted with project-specific values during `orchestrator init`:

| Placeholder | Source | Example |
|---|---|---|
| `{{LANGUAGE}}` | `answers.language` | `Python`, `TypeScript/Node.js` |
| `{{TEST_COMMAND}}` | `answers.test_command` | `pytest`, `npm test` |
| `{{BUILD_CHECK_COMMAND}}` | `answers.build_check_command` | `tsc --noEmit`, `go build ./...` |

Substitution is performed by `onboarding.substitute_template_placeholders()`. The `orchestrator init` command calls `onboarding.install_templates_with_substitution()`, which reads each bundled template, substitutes all recognised tokens, and writes the result to `templates/agents/`. Placeholders that appear in raw bundled templates are replaced in the installed copies — unrecognised `{{...}}` tokens are left as-is.

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

## Bead Auto-Commit

`RepositoryStorage` automatically commits every bead write and deletion through the storage chokepoint. No manual `git add`/`git commit` is required for bead metadata.

- **Write**: After each `_write_bead()` call, `_git_commit_bead()` stages and commits the bead JSON file. The commit message is `[bead] <id>: created (<agent_type>)` for new beads and `[bead] <id>: <status>` for updates.
- **Deletion**: After `delete_bead()` removes the file, `_git_commit_bead_deletion()` commits the removal with message `[bead] <id>: deleted`.

Both methods are best-effort: git failures are caught and silently ignored so storage operations remain non-fatal when git is unavailable (e.g., detached HEAD, no repo). Concurrent writes are serialized via a class-level `threading.Lock`.

The auto-commit behavior keeps the feature branch in a clean state with respect to bead metadata, which is important for the merge preflight — the rebase step compares against `main` and will not encounter unstaged bead changes.

## Telemetry

Two-tier storage per bead execution:
1. Lightweight metrics in `bead.metadata["telemetry"]` and `telemetry_history`
2. Full prompt/response artifact at `.orchestrator/telemetry/<bead_id>/<attempt>.json`

See [scheduler-telemetry.md](scheduler-telemetry.md) for the full schema.
