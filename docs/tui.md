# Interactive TUI

Launch with `uv run takt tui`. Requires `textual` (installed via `uv sync`).

```bash
uv run takt tui
uv run takt tui --feature-root B0030
uv run takt tui --refresh-seconds 5
```

## Layout

```
Screen (vertical)
  #main-row (vertical, height: 1fr)
    #top-row (horizontal, height: 2fr)
      #list-panel    (width: 1fr)
      #detail-panel  (width: 1fr)  ← hidden in compact mode
    #scheduler-log   (full-width, height: 1fr)
  #status-bar  (height: 1, no border)
```

The top row holds two equal-width panels side by side:
- **Beads** (left): bead tree in feature-root order, with active filter label in the title
- **Details** (right): selected bead scope and handoff fields

The **Scheduler Log** spans the full width below the top row, showing live scheduler activity. It is focusable and scrollable.

A single-line status bar at the very bottom shows live scheduler counts — `{N} running | {N} ready | {N} blocked` — and the latest status message. Counts update on every TUI refresh. It has no border or padding.

### Layout Toggle

Press `L` to switch between **wide** and **compact** layouts:

- **Wide** (default): all three panels visible — Beads, Details, and Scheduler Log.
- **Compact**: the Details panel is hidden. Tab cycles only between Beads and Scheduler Log. Use compact mode on narrow terminals where the two-column layout is too cramped to read. Press `Enter` to open a bead's full detail in a popup overlay instead.

When switching from wide to compact while the Details panel has focus, focus moves to the Beads panel automatically.

## Panel Focus

`Tab` / `Shift+Tab` cycles focus through panels in order:

- **Wide layout**: Beads → Details → Scheduler Log → Beads
- **Compact layout**: Beads → Scheduler Log → Beads (Details panel is hidden)

The focused panel highlights its border and shows scroll hints in its subtitle.

The **Scheduler Log** panel supports the same scroll keys as the detail panel when it has focus (`j`/`k`, `PageUp`/`PageDown`, `Home`/`End`, `g`/`G`).

## Maximize Toggle

Press `m` to expand the currently focused panel. The other two panels are hidden, and the maximized panel fills the available area. Press `m` again to restore the default layout. Focus does not change when toggling maximize.

When the **Scheduler Log** panel is maximized, the entire top row (Beads + Details) is also hidden, so the log expands to fill the full screen height below the status bar. Maximizing the **Beads** or **Details** panel only hides the sibling panel within the top row; the top row container itself remains visible.

The status bar remains visible at all times — it is never hidden by maximize.

## Keyboard Bindings

| Key | Action |
|-----|--------|
| `q` | Quit |
| `Tab` / `Shift+Tab` | Cycle focus between panels (order depends on layout mode) |
| `j` / `Down` | Move selection down (list) or scroll down (detail/log) |
| `k` / `Up` | Move selection up (list) or scroll up (detail/log) |
| `PageUp` / `PageDown` | Page through whichever panel has focus |
| `Home` / `End` | Jump to start or end of focused panel |
| `g` / `G` | Jump to first or last bead in list |
| `n` / `N` | Move active collapsible section in detail panel |
| `Enter` | Open a scrollable detail popup for the selected bead |
| `Escape` | Dismiss the detail popup or help overlay |
| `f` / `Shift+F` | Cycle filters forward / backward |
| `r` | Manual refresh (or choose `ready` in status update flow) |
| `H` | Load up to 50 historical events into the Scheduler Log |
| `t` | Start retry confirmation for selected blocked bead |
| `u` | Start status update flow for selected bead |
| `b` / `d` | Choose `blocked` / `done` in status update flow |
| `y` | Confirm pending retry or status update |
| `c` | Cancel pending action |
| `m` | Toggle maximize on focused panel |
| `L` | Toggle compact / wide layout |
| `?` | Toggle help overlay |

## Refresh and Scheduler Log

The TUI is a **pure dashboard** — it observes bead state and scheduler activity but does not start scheduler cycles. To run beads, use `takt run` (or `takt --runner claude run`) in a separate terminal.

Timed refresh runs automatically on the interval set by `--refresh-seconds` (default: 3 s). Press `r` to trigger an immediate refresh at any time.

### Cross-process event log visibility

The Scheduler Log panel tails `.takt/logs/events.jsonl`, which is written by `takt run` as it dispatches beads. This means the TUI shows live activity from any concurrently running `takt run` process without sharing a process boundary.

On startup the panel shows no historical entries — only events that occur while the TUI is open appear automatically. Press `H` to load up to 50 historical events from `events.jsonl` into the panel. Repeated presses walk further back through the log.

### Recorded event categories

| Event | Display colour | Payload shown |
|-------|---------------|---------------|
| `bead_started` | default | agent type, bead ID, title |
| `worktree_ready` | dim | bead ID, worktree path, branch |
| `bead_completed` | green | bead ID, summary |
| `bead_blocked` | yellow | bead ID, summary |
| `bead_failed` | bold red | bead ID, summary |
| `bead_deferred` | dim | bead ID, deferral reason |
| `lease_expired` | dim yellow | bead ID |

Scheduler lifecycle events (`scheduler_cycle_started`, `scheduler_cycle_completed`) and `bead_deleted` are suppressed in the log panel.

## Filters

The TUI starts in the **`all`** filter, which shows every bead including done ones. Press `f` / `Shift+F` to cycle through filters.

| Filter | Statuses shown |
|--------|---------------|
| `all` | Every status (startup default) |
| `default` | `open`, `ready`, `in_progress`, `blocked`, `handed_off` |
| `actionable` | `open`, `ready` |
| `deferred` | `handed_off` |
| `done` | `done` |

When `--feature-root` is set, the root bead stays visible regardless of filter.

## Operator Actions

- **Retry** (`t` → `y`): requeues a blocked bead to `ready`.
- **Status update** (`u` → `r`/`b`/`d` → `y`): manually transitions a bead. Developer beads cannot be manually marked `done` — they must complete through the scheduler to trigger followup beads.
- **Detail popup** (`Enter`): opens a full scrollable view of the selected bead's details in a modal overlay. Press `Escape` to close.

Retry and status update actions require confirmation and report results in the status panel.

To merge a bead's feature branch, use `takt merge <bead_id>` directly in a terminal — merges are not executed inside the TUI.

## Bead List Display

Bead titles in the list panel are dynamically truncated to fit the available panel width. The truncation accounts for fixed-width elements on each row (selection marker, tree indent, bead ID prefix, status tag, and telemetry badge), leaving the remaining width for the title. Truncated titles are suffixed with `...`. If the panel is too narrow to show any title characters, only `...` is shown. The fallback width when panel dimensions are unavailable is 120 characters.

### In-progress row indicators

`in_progress` bead rows show additional suffix elements between the status tag and the telemetry badge:

- **Elapsed timer** — `(Xm YYs)`: wall-clock time since the most recent `started` execution history entry, updated on each TUI refresh. Only shown for `in_progress` beads.
- **Stale lease indicator** — ` stale?`: appears when the bead's lease `expires_at` is in the past. Signals that the worker may have stopped or stalled. Only shown for `in_progress` beads with an expired lease.

### Deferred bead indicator

Beads deferred by the scheduler during the **current** scheduler cycle are marked with ` ⊘ deferred` on their row. This indicator is populated by the scheduler reporter and is cleared at the start of each new scheduler cycle. It reflects only the most recent cycle's deferral decisions — it does not persist across cycles, scheduler restarts, or TUI refreshes between cycles.

## Telemetry Display

### Bead list badges

Each bead row shows a compact telemetry badge after the status tag:

- **Leaf beads** (no children): `[$0.32, 2:55]` — own cost and wall-clock duration.
- **Parent beads** (have children): `[$0.32 / $1.85]` — own cost / subtree total cost. The subtree total aggregates cost across all descendants recursively (children, grandchildren, etc.).

The badge is omitted when no telemetry is available for the bead.

### Detail panel — Telemetry section

The **Telemetry** collapsible section (`n`/`N` to navigate) shows per-field metrics for the selected bead:

```
cost_usd, duration, num_turns, input_tokens, output_tokens,
cache_read_tokens, prompt_chars, session_id
```

For beads with multiple execution attempts, a summary line shows the attempt count and cumulative cost: `attempts: 3 (total cost: $0.96)`.

For parent beads, an additional **Subtree** line aggregates across all descendants:

```
Subtree: $1.85 total, 12:30 duration, 4 beads
```

Fields are aggregated as sums. A bead contributes to the subtree totals only if it has telemetry recorded in its metadata.

## Execution History Display

The **Overview** and **History** sections of the detail panel each display at most the 5 most recent execution history entries. When a bead has more than 5 entries, the panel shows a truncation notice before the visible entries:

```
... 3 earlier entries omitted
[2026-04-01T19:35:50+00:00] created (scheduler): Bead created
...
```

The limit is fixed at 5 entries (`EXECUTION_HISTORY_DISPLAY_LIMIT` in `tui/state.py`). Earlier entries are not deleted — they remain stored in the bead JSON; only the display is truncated to keep the panel readable for long-running beads.

## Mouse Behavior

- Clicking a bead row focuses the list and selects that bead.
- Clicking the detail panel focuses it without changing selection.
- Clicking a section header folds/unfolds that collapsible block.
- Mouse wheel follows the hovered panel: wheel over list moves selection, wheel over detail scrolls content.
