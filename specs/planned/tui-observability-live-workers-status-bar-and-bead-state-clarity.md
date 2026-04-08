---
name: "TUI Observability: Live Workers, Status Bar, and Bead State Clarity"
id: spec-787eb92e
description: "TUI rendering improvements — live elapsed timers on running beads, informative status bar, deferred bead visual state, stale lease detection. Depends on spec-f4a943a4 (reactive scheduler + bead_deferred reporter method)."
dependencies: spec-f4a943a4
priority: medium
complexity: medium
status: planned
tags: []
scope:
  in: null
  out: null
feature_root_id: null
---
# TUI Observability: Live Workers, Status Bar, and Bead State Clarity

## Objective

The TUI currently gives limited visibility into what the scheduler is doing. Running agents show as `in_progress` with no indication of elapsed time or whether the agent is genuinely active vs holding a stale lease. Deferred beads are invisible — the operator sees a ready bead that never starts, with no explanation. The status bar is a static summary line. This spec addresses these rendering gaps after the reactive scheduler (spec-f4a943a4) has landed, which provides the `bead_deferred` reporter hook needed for deferral display.

## Problems to Fix

### 1. No elapsed time on running agents

In-progress beads in the bead list show no indication of how long they have been running. There is no way to distinguish a bead that started 30 seconds ago from one that has been running for 45 minutes with a lease that expired 15 minutes ago.

### 2. Stale leases look identical to active agents

A bead with `status=in_progress` and an expired lease is visually identical to one with an active agent. Operators have no way to spot a hung bead without checking `takt bead show`.

### 3. Deferred beads are silent

When the scheduler defers a bead (file conflict, dependency not done, worktree in use), the bead remains `ready` in the list with no visible explanation. After spec-f4a943a4 lands, `bead_deferred` events are emitted — this spec wires them into the TUI log and bead list.

### 4. Status bar is not informative

The current status bar shows a static summary string. It does not show live worker counts, how many beads are ready, or how many are blocked.

---

## Changes

### 1. Elapsed timers on in-progress beads

**File: `src/agent_takt/tui/render.py`** (bead list rendering)

For each bead with `status=in_progress`, compute elapsed time from the most recent `in_progress` execution history entry and append it to the row:

```
⚙ B-a0302285  tester   Validate bead graph feature...  (8m 23s)
```

The timer updates on each TUI refresh tick (already fires every few seconds via the Textual reactive loop). No additional timers needed.

### 2. Stale lease visual distinction

**File: `src/agent_takt/tui/render.py`**

When an in-progress bead's lease `expires_at` is in the past, render the row differently — yellow colour or a `stale?` suffix:

```
⚙ B-a0302285  tester   Validate bead graph feature...  (52m 10s) stale?
```

Threshold: lease is expired (current time > `lease.expires_at`). No configurable threshold needed — expired = stale.

### 3. Deferred bead log entries

**File: `src/agent_takt/tui/app.py`** (`TuiSchedulerReporter`)

`bead_deferred(bead, reason)` (provided by spec-f4a943a4) already appends a log line:
```
[10:23:41] Deferred B-5441fde0: file conflict with in-progress B-09ea66ab
```

This spec ensures the TUI log panel renders those lines with a distinct colour (grey/dim) to distinguish deferral noise from completions and errors.

Optionally, show a `⊘ deferred` indicator in the bead list row for a bead that was deferred in the most recent cycle. This requires `TuiSchedulerReporter` to maintain a `_deferred_this_cycle: set[str]` that is cleared at cycle start and populated by `bead_deferred()`. The bead list render checks this set.

### 4. Informative status bar

**File: `src/agent_takt/tui/app.py`**

Replace the current static status bar line with structured live counts, updated on each refresh:

```
3 running | 2 ready | 1 blocked | S:auto
```

Fields:
- `N running` — beads currently `in_progress`
- `N ready` — beads with `status=ready`
- `N blocked` — beads with `status=blocked`
- `S:auto` / `S:manual` — scheduler auto-run toggle state (already tracked)

No countdown timer — it adds re-render churn for marginal value.

---

## Files to Modify

| File | Change |
|---|---|
| `src/agent_takt/tui/render.py` | Add elapsed timer and stale-lease indicator to in-progress bead rows |
| `src/agent_takt/tui/app.py` | Structured status bar; `_deferred_this_cycle` set for deferred bead indicators; style deferred log lines |
| `tests/test_tui_render.py` | Tests for elapsed timer rendering, stale lease rendering |
| `tests/test_tui_app.py` | Tests for status bar content, deferred indicator set lifecycle |

---

## Acceptance Criteria

- In-progress beads show elapsed time (e.g. `(8m 23s)`) in the bead list, updated on each TUI refresh
- In-progress beads with an expired lease show a `stale?` visual indicator (distinct colour or label)
- Deferred bead log lines appear in the scheduler log panel in a visually distinct style (dim/grey)
- The status bar shows `N running | N ready | N blocked | S:auto/manual` and updates live
- Deferred beads show `⊘ deferred` (or equivalent) in the bead list for the most recent cycle
- All existing TUI tests pass

---

## Pending Decisions

### 1. Deferred indicator in bead list
Show `⊘ deferred` in the list row for the current cycle only, or persistently until the bead is dispatched? Persistent is simpler but inaccurate — a bead might stop being deferred without the TUI noticing. **Leans toward current-cycle-only, cleared at each cycle start.**

### 2. Stale lease colour
Yellow for stale, or a suffix label, or both? Colour-only is less accessible. **Leans toward yellow + `stale?` text suffix.**
