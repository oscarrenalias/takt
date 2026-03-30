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

## Verdict-first review and tester results

Review and tester beads now support structured verdict fields in their worker JSON output:

- `verdict`: `approved` or `needs_changes`
- `findings_count`: non-negative integer for the number of findings reported
- `requires_followup`: optional explicit follow-up signal; when omitted, the scheduler derives it from the verdict

The scheduler treats `verdict` as the control-flow source of truth for `review` and `tester` beads:

- `approved` completes the bead even when `remaining` contains arbitrary narrative prose
- `needs_changes` blocks the bead and requires a `block_reason`
- `remaining` remains operator-facing context, but no longer decides whether structured review/test runs are blocked

Backward compatibility is still enabled through `REVIEW_TEST_VERDICT_COMPAT_MODE` in [`src/codex_orchestrator/scheduler.py`](src/codex_orchestrator/scheduler.py). When a legacy review/test result omits `verdict`, the scheduler falls back to the older `remaining`-text heuristic and appends a `compat_fallback_warning` record to `execution_history` so operators can see that the bead used compatibility behavior instead of the verdict-first path.

The persisted handoff fields now retain `verdict`, `findings_count`, and `requires_followup` alongside the existing narrative fields under `handoff_summary` and `metadata.last_agent_result`, so `orchestrator bead show <bead_id>` exposes whether a blocked review/test handoff came from structured verdict handling or the temporary compatibility path.

## Conflict-aware scope

- Beads can persist `expected_files`, `expected_globs`, `touched_files`, and `conflict_risks`
- Planner output can seed expected scope for child beads
- Workers can update scope during execution and Git worktrees are inspected for actual touched files
- `orchestrator bead claims` shows the active in-progress file claims used by the scheduler

## Interactive TUI

The project now ships an interactive terminal UI behind `orchestrator tui`. The console script is registered in `pyproject.toml` and dispatches through `src/codex_orchestrator/cli.py` into `src/codex_orchestrator/tui.py`.

Example:

```bash
orchestrator tui
orchestrator tui --feature-root B0030
orchestrator tui --refresh-seconds 5
```

The TUI starts in manual refresh mode. Opening the screen loads the current bead state once, leaves timed refresh disabled, and keeps scheduler execution in manual mode until the operator explicitly enables one of the automatic modes.

CLI behavior:

- `--feature-root <bead_id>` scopes the screen to one feature tree
- `--refresh-seconds <n>` controls the background refresh interval, defaults to `3`, and rejects values below `1`
- invalid or non-feature-root `--feature-root` values are rejected before the TUI starts
- the command requires `textual`; there is no fallback non-interactive TUI mode, so if the dependency is unavailable the command exits non-zero, prints a retry hint, and leaves bead state unchanged

Install note:

- `uv sync` installs the declared `textual` dependency for the normal path
- if `textual` is missing, `orchestrator tui` prints `Hint: install project dependencies so textual is available.`

The runtime renders three panels:

- a left-side tree of visible beads in feature-root order
- a right-side detail panel for the selected bead, including scope and handoff fields
- a bottom status panel with the current status message, latest activity, and footer counts

Refresh modes and focus cues:

- startup mode is `manual refresh | scheduler=manual | focus=list`
- `a` enables or disables timed refreshes without enabling scheduler runs
- `S` switches timed refreshes into timed scheduler passes; if timed refresh is off, `S` enables it first
- turning timed refresh off always returns the screen to full manual mode and also disables timed scheduler runs
- the status panel shows the current mode and focused panel, and the focused list or detail panel keeps the accent border so operators can see whether navigation keys will move the list selection or scroll the detail view

Keyboard bindings:

- `q`: quit
- `Tab`: move focus forward between the list and detail panels
- `Shift+Tab`: move focus backward between the list and detail panels
- `j` or `Down`: move the selected bead down when the list is focused, or scroll the current bead detail down when the detail panel is focused
- `k` or `Up`: move the selected bead up when the list is focused, or scroll the current bead detail up when the detail panel is focused
- `PageUp` and `PageDown`: move by a larger step in whichever panel currently has focus, paging the bead list or the bead detail view
- `Home` and `End`: jump to the start or end of whichever panel currently has focus, selecting the first or last visible bead in the list or jumping to the top or bottom of the detail view
- `f`: next filter
- `Shift+f`: previous filter
- `a`: toggle timed refresh on or off
- `?`: toggle the help overlay (`? help` stays visible in the footer)
- `Esc`: close the help overlay
- `r`: manual refresh, or choose `ready` while the status update flow is active
- `s`: run one scheduler cycle with the current scope
- `S`: toggle continuous scheduler runs on timed refreshes
- `t`: request retry for the selected blocked bead
- `u`: start the status update flow for the selected bead
- `b`: choose `blocked` while the status update flow is active
- `d`: choose `done` while the status update flow is active
- `y`: confirm the pending retry or status update
- `n`: cancel a pending merge, retry, or status update
- `m`: request merge for the selected bead
- `Enter`: confirm a pending merge

Mouse behavior:

- clicking a visible row in the list focuses the list panel and selects that bead
- clicking anywhere in the detail panel focuses the detail panel without changing the current selection
- mouse wheel input follows the hovered panel: wheel events over the list move selection one row at a time, while wheel events over the detail panel scroll long metadata without changing the selected bead

Operator shortcuts:

- `a` toggles the timed refresh loop on or off without enabling timed scheduler runs
- `s` is the operator shortcut for a single scheduler pass from inside the TUI
- `S` turns timed refreshes into timed scheduler passes by toggling continuous run mode on or off
- `t` starts a retry confirmation flow for the selected blocked bead, and `y` is required to execute the retry
- `u` opens the operator status-update flow for the selected bead, then `r`, `b`, or `d` choose the target status before `y` confirms it
- `m` starts the operator merge flow for the selected done bead and waits for `Enter` before executing it

Refresh, help, and operator-action behavior:

- the initial screen is manual-first: bead data loads once, but there is no timed refresh or timed scheduler activity until the operator enables it
- `a` controls whether the timed refresh loop is active; turning it off also returns the TUI to fully manual refresh mode and disables timed scheduler runs
- timed refreshes run every `--refresh-seconds` seconds, keep the current selection when possible, and update the activity line
- `S` enables or disables continuous scheduler mode for timed refreshes; when enabled, each timed refresh runs one scheduler cycle instead of a read-only refresh, using the same scoped/global rules as `s`
- `s` runs the same one-shot scheduler path as `orchestrator run --once`; if the TUI was launched with `--feature-root <bead_id>` the run stays inside that feature tree, otherwise it operates across the full execution root
- `Tab` and `Shift+Tab` move focus between the list and detail panels without changing the layout or selection
- the focused panel keeps the accent border so it is always clear whether navigation keys will move the list selection or scroll the detail view, without changing the layout
- selecting a different bead from the list resets the detail view to the top of that bead's metadata so keyboard and wheel scrolling always starts from the new selection
- `?` opens a modal shortcut reference without changing the current bead selection or filter state
- while the help overlay is open, `?` and `Esc` close it and other keys are ignored by the overlay
- `r` performs an immediate refresh, clears any pending action, and updates the status panel unless the status update flow is active, where it selects `ready`
- retry is available only when the selected bead is `blocked`; `t` starts confirmation, `y` executes the retry, `n` cancels it, and invalid retry requests leave bead state unchanged and report the denial in the status panel
- `u` starts a short status flow for the selected bead, then `r`, `b`, or `d` chooses `ready`, `blocked`, or `done`, and `y` is required to execute the change
- status updates always require confirmation after a target is chosen; disallowed status transitions are rejected in-place, leave bead state unchanged, and report the reason in the status panel
- developer beads cannot be manually marked `done` via operator status update; complete them through scheduler execution so downstream tester/docs/review follow-up beads are created
- merge is available only when the selected bead is `done`
- `m` starts merge confirmation for the selected `done` bead, and `Enter` is required to execute the merge
- actions without the required preconditions, including retry on a non-`blocked` bead, status updates without a valid target, or merge on a non-`done` bead, do not mutate bead state
- `n` cancels a pending merge confirmation, retry confirmation, or status update flow
- a pending retry confirmation stays tied to the originally requested bead across timed refreshes and is cleared if that bead is no longer blocked
- a pending merge confirmation stays tied to the originally requested bead across timed refreshes and is cleared if that bead is no longer mergeable
- merge failures stay inside the TUI and are reported in the status panel instead of closing the session

The TUI behavior is backed by the same deterministic helpers exposed from `src/codex_orchestrator/tui.py`:

- deterministic bead loading and tree row construction
- stable selection recovery by bead id or previous cursor position
- shared filter constants for `default`, `all`, `actionable`, `deferred`, `done`, and per-status views
- detail-panel formatting for bead scope and handoff metadata
- status-panel formatting for action/result feedback and footer formatting for the active filter, run mode, row count, selected row, and per-status totals

Filter semantics are aligned to the scheduler status model:

- `default`: `open`, `ready`, `in_progress`, `blocked`, and `handed_off`
- `actionable`: `open` and `ready`
- `deferred`: `handed_off`
- `done`: `done`
- `all`: every known status in display order

When `--feature-root` is set, the requested feature-root bead stays visible even if the active status filter would otherwise hide it.

The detail formatter renders both bead-level scope fields and the latest handoff summary, including `expected_files`, `expected_globs`, `touched_files`, `changed_files`, `updated_docs`, `next_action`, `next_agent`, and the effective `conflict_risks`. The bottom status panel records the current status line, an explicit `Mode:` line, latest activity, `Last Action`, and `Last Result @ HH:MM:SS`, so the operator can see which action ran most recently, whether it succeeded, failed, or was rejected, and when that result was recorded. The `Mode:` line also carries the current refresh/scheduler mode and the active focus target, for example `manual refresh | scheduler=manual | focus=list`, `timed refresh every 3s | scheduler=manual | focus=detail`, or `timed scheduler every 3s | focus=list`. The footer formatter emits a compact single-line summary such as `filter=default | run=manual | rows=5 | selected=2 | open=1 | ready=1 | ... | ? help`, and flips to `run=continuous` when auto-run mode is enabled.

Regression coverage for the CLI parser, missing-dependency handling, helper functions, runtime state, scheduler action handlers, status update flow, and merge confirmation flow lives in `tests/test_orchestrator.py` and `tests/test_tui.py`.
