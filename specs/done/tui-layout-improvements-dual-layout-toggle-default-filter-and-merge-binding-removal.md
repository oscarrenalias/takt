---
name: "TUI layout improvements: dual-layout toggle, default filter, and merge binding removal"
id: spec-4cb360ad
description: "Toggle between wide (3-column) and compact (2-row + detail popup) layouts, default bead list filter to all, and remove merge keybindings so Enter opens bead detail."
dependencies: null
priority: medium
complexity: medium
status: done
tags:
- tui
- ux
scope:
  in: "TUI layout, keybindings, default filter mode, detail popup modal"
  out: "Scheduler, storage, CLI, any non-TUI code"
feature_root_id: null
---
# TUI layout improvements: dual-layout toggle, default filter, and merge binding removal

## Objective

Three cohesive improvements to the TUI that reinforce its role as a read-only monitoring dashboard: a hotkey to switch between a wide 3-column layout and a compact 2-row layout (tree + log, detail as popup) suitable for narrow terminals; defaulting the bead list to the `all` filter so the list is never confusingly empty after a merge; and removing the merge keybindings so that `Enter` can be repurposed for opening bead detail.

## Problems to Fix

1. **Single layout is bad on narrow terminals.** The current 3-column layout (tree | detail | log) is cramped below ~180 columns. There is no way to switch to a layout that gives the tree and log more vertical space.
2. **Default filter shows an empty list after a merge.** `FILTER_DEFAULT` excludes done beads. Once a feature is merged, all beads are done and the list appears empty, which is confusing.
3. **Enter triggers merge confirmation.** `Enter` is bound to `confirm_merge`, which is part of an operator action flow that no longer belongs in a pure dashboard TUI. This prevents `Enter` from being used for the more natural action of opening bead detail.

## Changes

### 1. Default filter → `all`

In `src/agent_takt/tui/state.py`, change the `filter_mode` field default on `TuiRuntimeState`:

```python
# Before (state.py:144)
filter_mode: str = FILTER_DEFAULT

# After
filter_mode: str = FILTER_ALL
```

`FILTER_ALL` is already defined in `tree.py` and includes done beads.

### 2. Remove merge keybindings

In `src/agent_takt/tui/app.py`, remove these two `Binding` entries from `OrchestratorTuiApp.BINDINGS`:

```python
Binding("M", "request_merge", "Merge (CLI)"),                             # remove
Binding("enter", "confirm_merge", "Confirm", show=False, priority=True),  # remove
```

The `action_confirm_merge` and `action_request_merge` methods in `actions.py` can be left in place or removed — agent's discretion.

### 3. Enter → open detail popup

Add a new binding:

```python
Binding("enter", "open_detail_popup", "Detail", show=False, priority=True),
```

Add `action_open_detail_popup` to the app class (can live in `app.py` or `actions.py`):

- If the selected bead is `None`, do nothing.
- Push a `DetailPopup` modal screen showing the detail for the currently selected bead.
- The modal is dismissed with `Escape`.

`DetailPopup` is a new `ModalScreen[None]` defined in `app.py`:

```python
class DetailPopup(ModalScreen[None]):
    BINDINGS = [Binding("escape", "dismiss_popup", "Close", show=False)]

    def __init__(self, bead: Bead, runtime_state: TuiRuntimeState) -> None: ...

    def compose(self) -> ComposeResult:
        # Render detail content inside a scrollable container.
        # Inspect render.py for the current signatures of render_detail_panel /
        # _detail_summary_lines and adapt as needed — a small refactor to make
        # these functions callable without the full app widget tree is acceptable.

    def action_dismiss_popup(self) -> None:
        self.dismiss(None)
```

Style: centred dialog at ~70% width / ~80% height, border `round $accent`, background `$surface`.

### 4. Dual-layout toggle (`L` key)

Add a `layout_mode` field to `TuiRuntimeState`:

```python
LAYOUT_WIDE = "wide"       # 3-column: tree | detail | log
LAYOUT_COMPACT = "compact" # 2-row: tree (top) / log (bottom); detail via popup

layout_mode: str = LAYOUT_WIDE
```

Add a `toggle_layout` method to `TuiRuntimeState`:

```python
def toggle_layout(self) -> None:
    self.layout_mode = LAYOUT_COMPACT if self.layout_mode == LAYOUT_WIDE else LAYOUT_WIDE
```

In `app.py`, add keybinding:

```python
Binding("L", "toggle_layout", "Toggle Layout"),
```

In `action_toggle_layout`, apply layout by toggling CSS classes:

- **Wide mode**: `#detail-panel` visible; `#main-row` uses horizontal layout with 3 columns.
- **Compact mode**: `#detail-panel` hidden; `#main-row` uses vertical layout (tree on top, log below).

Implement via a `compact` CSS class on the `Screen`:

```css
/* Wide (default) */
#main-row { layout: horizontal; }
#detail-panel { display: block; width: 1fr; }

/* Compact */
Screen.compact #main-row { layout: vertical; }
Screen.compact #detail-panel { display: none; }
Screen.compact #scheduler-log { height: 1fr; }
```

When switching from wide to compact, if the detail panel was focused, move focus to the list panel.

## Files to Modify

| File | Change |
|---|---|
| `src/agent_takt/tui/state.py` | Change `filter_mode` default to `FILTER_ALL`; add `layout_mode` field and `toggle_layout()` |
| `src/agent_takt/tui/app.py` | Remove merge bindings; add `enter` → `open_detail_popup`; add `L` → `toggle_layout`; add `DetailPopup` modal; add CSS for compact layout |
| `src/agent_takt/tui/actions.py` | Add `action_open_detail_popup`, `action_toggle_layout`; optionally remove `action_request_merge`, `action_confirm_merge` |

## Acceptance Criteria

- Opening the TUI with beads in `done` status shows those beads in the list immediately (default filter is `all`).
- Pressing `f` still cycles through filters including `default`, `actionable`, etc.
- Pressing `Enter` on a selected bead opens a popup modal showing the bead's detail content. The popup is dismissed with `Escape`.
- `Enter` no longer triggers merge confirmation.
- `M` key no longer triggers the merge flow.
- Pressing `L` switches to compact mode: tree panel on top, scheduler log below, detail panel hidden.
- Pressing `L` again restores wide mode with the detail panel visible.
- In compact mode, pressing `Enter` on a bead still opens the detail popup.
- Switching layouts does not lose the selected bead or reset scroll position.
- In compact mode, focus moves to the list panel if the detail panel was previously focused.
- All existing TUI tests pass; new tests cover the layout toggle and the detail popup open/dismiss flow.

## Pending Decisions

- ~~Should `n`/`N` (next/previous detail section) work inside the `DetailPopup`?~~ **Resolved: out of scope.** `n`/`N` navigation inside the popup is not required for this spec. The popup is a scrollable read-only view; section navigation can be added in a follow-up if needed.
