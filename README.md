# Codex Agent Orchestration MVP

This repository contains a local Python CLI for orchestrating specialized Codex workers against a Git-native task graph.

## Highlights

- Repository-backed bead storage under `.orchestrator/beads/`
- Deterministic scheduler with dependency resolution, conflict-aware file claims, and worker leases
- Isolated Git worktrees per active bead
- Structured handoffs between developer, tester, documentation, and review agents
- Template-backed guardrails for planner, developer, tester, documentation, and review workers
- Assisted planner command backed by Codex CLI

## Quick start

```bash
uv sync
orchestrator bead create --title "Implement feature X" --agent developer --description "Read spec and implement"
orchestrator run --once
orchestrator bead claims
orchestrator summary
```

## Summary command

Use `orchestrator summary` to print a lightweight JSON snapshot of current orchestration state.

Example:

```bash
orchestrator summary
```

- `counts`: per-status totals for `open`, `ready`, `in_progress`, `blocked`, `done`, and `handed_off`
- `next_up`: up to five `ready` beads (sorted by bead id)
- `attention`: up to five `blocked` beads (sorted by bead id), including `block_reason`

To optionally scope output to one feature tree, pass `--feature-root <bead_id>`:

```bash
orchestrator summary --feature-root B0002
```

Without `--feature-root`, the command summarizes all beads in the current execution root. With `--feature-root`, it only returns data when the id is a valid feature root; invalid ids or non-feature-root ids return empty counts and empty lists.

## Development

```bash
uv run python -m unittest discover -s tests -v
uv build
```

## Layout

- `.orchestrator/beads/`: authoritative bead state
- `.orchestrator/logs/events.jsonl`: scheduler event log
- `.orchestrator/worktrees/`: per-bead Git worktrees
- `docs/memory/`: shared project memory
- `templates/agents/`: editable guardrail templates for built-in agent types

## Specialized agent guardrails

Built-in worker guardrails live in `templates/agents/` and are the primary editable source of truth for role behavior. The current built-in set is:

- `templates/agents/planner.md`
- `templates/agents/developer.md`
- `templates/agents/tester.md`
- `templates/agents/documentation.md`
- `templates/agents/review.md`

At runtime, `build_worker_prompt(...)` resolves the template for the active `agent_type` from the active execution root (repository root or bead worktree), injects an `Agent guardrails:` section with the template path and Markdown body, and then appends the serialized bead execution context. There is no hardcoded fallback for built-in agents: if `templates/agents/<agent_type>.md` is missing, prompt construction fails with `FileNotFoundError` and the worker run is blocked instead of running without guardrails.

Before a worker executes, the scheduler stores the applied template under `metadata.guardrails` and the serialized prompt payload under `metadata.worker_prompt_context`. It also appends a `guardrails_applied` entry to `execution_history`, so `orchestrator bead show <bead_id>` exposes which guardrails were used for that run.

If an agent blocks because the work belongs to another specialization, the result is preserved on the bead rather than treated like an unstructured failure. Inspect `handoff_summary.block_reason`, `handoff_summary.next_agent`, `metadata.last_agent_result`, `status`, and `execution_history` in `orchestrator bead show <bead_id>` to see why the role-scope handoff was blocked and which agent should take over next.

## Conflict-aware scope

- Beads can persist `expected_files`, `expected_globs`, `touched_files`, and `conflict_risks`
- Planner output can seed expected scope for child beads
- Workers can update scope during execution and Git worktrees are inspected for actual touched files
- `orchestrator bead claims` shows the active in-progress file claims used by the scheduler
