# TUI Observability and Reactive Scheduler

## Objective

The current TUI gives limited visibility into what the orchestrator is actually doing. The scheduler model is batch-oriented (one cycle dispatches a snapshot of ready beads, then waits for all of them to finish before the next cycle can start), which means idle workers sit unused and the UI shows "Scheduler cycle already in progress" without explaining what it is waiting for. This spec addresses both the observability gaps and the reactive scheduling model.

## Problems to Fix

### 1. No live view of running agents

The bead list shows `in_progress` status but gives no indication of how long the agent has been running or whether it is actually active vs a stale lease. There is no persistent "workers" view.

### 2. Scheduler cycle blocks on running agents

`_start_scheduler_worker()` sets `_scheduler_worker_running=True` for the entire duration of `run_once()`, including the time waiting for dispatched agents to finish. Interval ticks every 3 seconds fire, see the flag, and abort. New ready beads that become available mid-cycle are not picked up until the current cycle fully returns — which may be 10+ minutes away.

### 3. Deferred beads have no visible reason

When the scheduler defers a bead (file conflict, worktree in use, dependency not satisfied), nothing in the TUI explains why. The operator sees a ready bead that never starts.

### 4. Status bar is not informative

The current status bar shows a static summary line. It does not show how many workers are busy, how many ready beads are waiting, or when the next cycle fires.

### 5. Scheduler log is too verbose and too sparse simultaneously

Cycle-level events ("Scheduler cycle starting...") appear but per-bead events (deferred, why, which conflict) do not. Operators cannot tell what the scheduler decided during a cycle.

---

## Changes

### 1. Reactive scheduler: fill worker slots continuously

The batch model exists in two places that both need fixing:

**`Scheduler.run_once()`** (used by both CLI and TUI): currently dispatches a snapshot of ready beads into a `ThreadPoolExecutor`, waits for all of them to finish, then returns. New beads that become ready mid-execution (e.g. a corrective is created, or a dependency completes) are not picked up until the next call to `run_once()`.

Replace with a continuous fill model inside `run_once()` (or a new `run_continuous()` method):
- Maintain a live worker pool with `max_workers` slots
- Whenever a slot frees up, immediately scan for newly-ready beads and dispatch the next one
- Return only when no workers are running AND no ready beads remain (or `--once` is set)

**TUI `_start_scheduler_worker`**: the interval tick guard (`_scheduler_worker_running`) prevents a new cycle from starting while the previous one is active. Once `run_once()` is reactive internally, the TUI can simplify: just ensure only one call to `run_once()` is active at a time, which is correct since the reactive `run_once()` will now handle continuous filling itself.

This means both `orchestrator run` (CLI) and the TUI scheduler benefit from the same reactive behaviour.

### 2. Live worker panel (or active-bead indicators)

Add a persistent live view of currently-running agents. Either:
- A dedicated narrow row below the bead list showing active workers, or
- Highlight in-progress beads in the bead list with a running timer

Each active agent shows:
```
⚙ B-a0302285  tester   Validate bead graph feature...  (8m 23s)
```

The timer updates every second. When the agent completes, the row is replaced by a status icon and fades or scrolls into the scheduler log.

### 3. Deferral reasons in scheduler log

When the scheduler defers a bead, log the reason explicitly:

```
[10:23:41] Deferred B-fedf72c8: dependency B-a0302285 not done
[10:23:41] Deferred B-cc3d06ce-review: waiting for B-cc3d06ce-docs
[10:23:41] Deferred B-5441fde0: file conflict with in-progress B-09ea66ab (src/codex_orchestrator/tui.py)
```

This replaces the opaque silence where the operator just sees a bead that never starts.

### 4. Informative status bar

Replace the current single-line status bar with structured live counts:

```
3 running | 2 ready | 1 blocked | next scan in 2s | S:auto
```

Fields:
- `N running` — agents currently executing
- `N ready` — beads ready to dispatch
- `N blocked` — beads blocked (not deferred — actually blocked with a block_reason)
- `next scan in Ns` — countdown to next interval tick
- `S:auto` / `S:manual` — whether continuous auto-run is enabled

### 5. Bead list status clarity

Distinguish between bead states that currently look identical:

| Current display | New display |
|---|---|
| `in_progress` (active agent) | `⚙ running` (green) |
| `in_progress` (stale lease) | `⚙ stale?` (yellow, if lease age > lease_timeout / 2) |
| `ready` (will run next cycle) | `· ready` (white) |
| `ready` (deferred — conflict) | `⊘ deferred` (grey, with reason on hover/detail) |
| `blocked` | `✗ blocked` (red) |

### 6. Scheduler log improvements

- Show per-bead dispatch events: `[10:23:41] Dispatched B-a0302285 tester`
- Show per-bead completion events: `[10:31:04] Completed B-a0302285 tester (7m 23s) → approved`
- Show deferral reasons (see above)
- Suppress repetitive "Scheduler cycle starting..." lines when nothing changed — only log a cycle if it actually dispatched or deferred something

---

## Files to Modify

| File | Change |
|---|---|
| `src/codex_orchestrator/scheduler.py` | Reactive continuous fill model inside `run_once()` or new `run_continuous()` |
| `src/codex_orchestrator/cli.py` | Update `command_run()` loop to use reactive scheduler |
| `src/codex_orchestrator/tui.py` | Live worker view, status bar, log improvements, bead list status display, simplified cycle guard |
| `tests/test_orchestrator.py` | Tests for reactive scheduling: slot-fill-on-completion behaviour |
| `tests/test_tui.py` | Tests for deferral logging, status bar content |

---

## Acceptance Criteria

- New ready beads are picked up within one interval tick (≤3 seconds) of a worker slot freeing up, without waiting for all other running agents to finish
- The status bar shows live counts: running, ready, blocked, next scan countdown
- Each scheduler deferral is logged with the specific reason (dependency, conflict, or worktree)
- In-progress beads show elapsed time in the bead list or a dedicated worker panel
- Stale leases (in_progress with no active agent) are visually distinguishable from genuinely running beads
- "Scheduler cycle already in progress" message is eliminated — replaced by informative per-bead log events
- All existing TUI tests pass
- The `S` key toggle for auto-run continues to work

---

## Pending Decisions

### 1. Worker panel vs inline timers
Show running agents as a separate panel row, or just annotate the bead list with timers? A separate panel is cleaner but takes vertical space. Inline timers are compact but harder to scan. **Leans toward inline with elapsed time in the bead list row.**

### 2. Stale lease detection threshold
At what age should an in-progress bead be flagged as "stale?"  Half the `lease_timeout_minutes` is a reasonable default but may show false positives for long-running agents. **Undecided.**

### 3. Deferral visibility in bead list
Should deferred beads show `⊘ deferred` in the list, or just log the reason? Showing it in the list gives immediate visibility but adds a new visual state. **Leans toward showing it — the current silent deferral is the main pain point.**

### 4. Countdown timer accuracy
The "next scan in Ns" countdown requires updating the status bar every second. This may cause unnecessary re-renders. Could show a progress bar instead, or just show the interval period without a live countdown. **Undecided.**
