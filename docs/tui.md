# Interactive TUI

Launch with `uv run orchestrator tui`. Requires `textual` (installed via `uv sync`).

```bash
uv run orchestrator tui
uv run orchestrator tui --feature-root B0030
uv run orchestrator tui --refresh-seconds 5
```

## Layout

```
Screen (vertical)
  #main-row (vertical, height: 1fr)
    #top-row (horizontal, height: 2fr)
      #list-panel    (width: 1fr)
      #detail-panel  (width: 1fr)
    #scheduler-log   (full-width, height: 1fr)
  #status-bar  (height: 1, no border)
```

The top row holds two equal-width panels side by side:
- **Beads** (left): bead tree in feature-root order, with active filter label in the title
- **Details** (right): selected bead scope and handoff fields

The **Scheduler Log** spans the full width below the top row, showing live scheduler activity. It is focusable and scrollable.

A single-line status bar at the very bottom shows the current mode, latest action result, and footer counts. It has no border or padding.

## Panel Focus

`Tab` / `Shift+Tab` cycles focus through all three panels in order: **Beads → Details → Scheduler Log → Beads**. The focused panel highlights its border and shows scroll hints in its subtitle.

The **Scheduler Log** panel supports the same scroll keys as the detail panel when it has focus (`j`/`k`, `PageUp`/`PageDown`, `Home`/`End`, `g`/`G`).

## Maximize Toggle

Press `m` to expand the currently focused panel. The other two panels are hidden, and the maximized panel fills the available area. Press `m` again to restore the default layout. Focus does not change when toggling maximize.

When the **Scheduler Log** panel is maximized, the entire top row (Beads + Details) is also hidden, so the log expands to fill the full screen height below the status bar. Maximizing the **Beads** or **Details** panel only hides the sibling panel within the top row; the top row container itself remains visible.

The status bar remains visible at all times — it is never hidden by maximize.

## Keyboard Bindings

| Key | Action |
|-----|--------|
| `q` | Quit |
| `Tab` / `Shift+Tab` | Cycle focus: list → detail → scheduler log → list |
| `j` / `Down` | Move selection down (list) or scroll down (detail/log) |
| `k` / `Up` | Move selection up (list) or scroll up (detail/log) |
| `PageUp` / `PageDown` | Page through whichever panel has focus |
| `Home` / `End` | Jump to start or end of focused panel |
| `g` / `G` | Jump to first or last bead in list |
| `n` / `N` | Move active collapsible section in detail panel |
| `Enter` | Toggle active detail section, or confirm a pending merge |
| `f` / `Shift+F` | Cycle filters forward / backward |
| `a` | Toggle timed refresh on/off |
| `r` | Manual refresh (or choose `ready` in status update flow) |
| `s` | Run one scheduler cycle |
| `S` | Toggle continuous scheduler runs on timed refreshes |
| `t` | Start retry confirmation for selected blocked bead |
| `u` | Start status update flow for selected bead |
| `b` / `d` | Choose `blocked` / `done` in status update flow |
| `y` | Confirm pending retry or status update |
| `c` | Cancel pending action |
| `m` | Toggle maximize on focused panel |
| `M` | Merge current feature branch into main |
| `?` | Toggle help overlay |
| `Esc` | Close help overlay |

## Refresh and Scheduler Modes

The TUI starts in `manual refresh | scheduler=manual`. Mode is shown in the status panel footer.

- `a` — enables/disables timed refresh. Turning off also disables timed scheduler runs.
- `s` — one-shot scheduler pass (respects `--feature-root` scope if set).
- `S` — toggles continuous mode: each timed refresh runs a scheduler cycle instead of a read-only refresh.

## Filters

| Filter | Statuses shown |
|--------|---------------|
| `default` | `open`, `ready`, `in_progress`, `blocked`, `handed_off` |
| `actionable` | `open`, `ready` |
| `deferred` | `handed_off` |
| `done` | `done` |
| `all` | Every status |

When `--feature-root` is set, the root bead stays visible regardless of filter.

## Operator Actions

- **Retry** (`t` → `y`): requeues a blocked bead to `ready`.
- **Status update** (`u` → `r`/`b`/`d` → `y`): manually transitions a bead. Developer beads cannot be manually marked `done` — they must complete through the scheduler to trigger followup beads.
- **Merge** (`M` → `Enter`): merges a `done` bead's feature branch. Press `M` (Shift+M) to initiate the merge confirmation flow and confirm with `Enter`.

All actions require confirmation and report results in the status panel. Failed merges stay inside the TUI without closing the session.

## Bead List Display

Bead titles in the list panel are dynamically truncated to fit the available panel width. The truncation accounts for fixed-width elements on each row (selection marker, tree indent, bead ID prefix, status tag, and telemetry badge), leaving the remaining width for the title. Truncated titles are suffixed with `...`. If the panel is too narrow to show any title characters, only `...` is shown. The fallback width when panel dimensions are unavailable is 120 characters.

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

## Mouse Behavior

- Clicking a bead row focuses the list and selects that bead.
- Clicking the detail panel focuses it without changing selection.
- Clicking a section header folds/unfolds that collapsible block.
- Mouse wheel follows the hovered panel: wheel over list moves selection, wheel over detail scrolls content.
