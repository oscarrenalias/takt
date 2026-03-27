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

## Bead list command

`orchestrator bead list` prints all beads as JSON by default.

Example:

```bash
orchestrator bead list
```

Use `--plain` for a human-readable table.

Example:

```bash
orchestrator bead list --plain
```

## Bead claims command

`orchestrator bead claims` prints active in-progress claims as JSON by default.

Regression coverage in [`tests/test_orchestrator.py`](tests/test_orchestrator.py) locks in both output modes: the default JSON payload and the optional plain-text rendering.

If a bead links a doc path that is missing from the expected subdirectory, the worker context loader now falls back to a unique basename match elsewhere in the repo. That keeps handoff beads usable when the linked doc was moved without updating older bead metadata.

Example:

```bash
orchestrator bead claims
```

Use `--plain` for a compact, human-readable view.

Example:

```bash
orchestrator bead claims --plain
```

Plain output renders one line per active claim in this format:

```text
<bead_id> | <agent_type> | feature=<feature_root_id> | lease=<lease_owner>
```

If there are no active claims, plain output prints:

```text
No active claims.
```

When a `review` bead is validating the `bead claims --plain` change, sign-off stays blocked if the output still needs implementation work. In that case, `orchestrator bead show <bead_id>` preserves the developer handoff under `handoff_summary.next_agent`, `handoff_summary.block_reason`, and `metadata.last_agent_result`, so the next owner is explicit instead of being inferred from a failed run.

The current regression checks cover:

- default `orchestrator bead claims` output remaining machine-readable JSON
- `orchestrator bead claims --plain` emitting the compact single-line format
- `orchestrator bead claims --plain` returning `No active claims.` when nothing is running
- parser support for the `bead claims --plain` flag

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

## TUI helper layer

The interactive `orchestrator tui` command is still pending, but the shared data-model and formatting helpers for that screen now live in `src/codex_orchestrator/tui.py`.

The current helper layer covers:

- deterministic bead loading and tree row construction
- stable selection recovery by bead id or previous cursor position
- shared filter constants for `default`, `all`, `actionable`, `deferred`, `done`, and per-status views
- detail-panel formatting for bead scope and handoff metadata
- footer formatting for the active filter, row count, selected row, and per-status totals

The shipped filter semantics are intentionally aligned to the scheduler's status model:

- `default`: `open`, `ready`, `in_progress`, `blocked`, and `handed_off`
- `actionable`: `open` and `ready`
- `deferred`: `handed_off`
- `done`: `done`
- `all`: every known status in display order

The detail formatter renders both bead-level scope fields and the latest handoff summary, including `expected_files`, `expected_globs`, `touched_files`, `changed_files`, `updated_docs`, `next_action`, `next_agent`, and the effective `conflict_risks`. The footer formatter currently emits a compact single-line summary such as `filter=default | rows=5 | selected=2 | open=1 | ready=1 | ...`.

Regression coverage for these helpers lives in `tests/test_orchestrator.py`. The remaining runtime work is to wire these helpers into the actual TUI refresh loop, keyboard handling, merge flow, and dependency checks for the optional rendering library.
