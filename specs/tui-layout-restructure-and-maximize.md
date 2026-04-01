# TUI Layout Restructure and Panel Maximize

## Objective

Restructure the TUI layout so the scheduler log becomes a first-class peer panel alongside the bead list and detail panels, collapse the status bar to a single line, and add a maximize toggle so any panel can be expanded to fill the screen.

## Current Layout

```
Screen (vertical)
  #top-row (horizontal, height: 1fr)
    #list-panel    (width: 1fr)
    #detail-panel  (width: 1fr)
  #status-bar  (height: 3, bordered)
  #scheduler-log  (height: 8, fixed, below status)
```

Problems:
- The scheduler log is at the bottom, cramped to 8 fixed lines, and outside the panel area — making maximize awkward
- The status bar is a bordered 3-line box, wasting vertical space
- There is no way to expand any panel to read more content

## New Layout

```
Screen (vertical)
  #main-row (horizontal, height: 1fr)
    #list-panel      (width: 1fr)
    #detail-panel    (width: 1fr)
    #scheduler-log   (width: 1fr)
  #status-bar  (height: 1, no border, full-width)
```

All three panels are horizontal siblings. The status bar is a single-line bar at the very bottom of the screen — no border, no padding, styled like a terminal status line.

## Maximize Behavior

Pressing `m` toggles maximize on the currently focused panel:
- The focused panel expands to `width: 100%`
- The other two panels are hidden (`display: none`)
- Pressing `m` again restores all three panels to equal width
- Focus does not change when maximizing or restoring

The status bar remains visible at all times — it is never hidden by maximize.

Panels that can be maximized: bead list, bead detail, scheduler log.

## Panel Focus and Tab

Tab / Shift+Tab cycle through all three panels (bead list → detail → scheduler log → bead list). The scheduler log was not previously focusable for cycling purposes; after this change it is.

## Key Bindings

| Key | Action |
|-----|--------|
| `m` | Toggle maximize on focused panel |
| `Tab` | Focus next panel (list → detail → log → list) |
| `Shift+Tab` | Focus previous panel |

## Implementation Notes

- Add a `maximized_panel: str | None = None` field to the runtime state dataclass
- Add CSS classes `.maximized` (`width: 100%; height: 1fr`) and `.hidden` (`display: none`)
- `action_toggle_maximize` applies/removes these classes on all three panel widgets
- Panel focus cycling must be updated to include `#scheduler-log` as a third option
- `#status-bar` loses its border and drops to `height: 1`
- `#scheduler-log` moves from after `#status-bar` in `compose()` to inside the horizontal row

## Acceptance Criteria

- Scheduler log appears as a third horizontal panel alongside bead list and detail
- Status bar is a single borderless line at the bottom of the screen
- Pressing `m` expands the focused panel to full width and hides the other two
- Pressing `m` again restores the three-panel layout
- Tab / Shift+Tab cycles through all three panels
- Maximize works for all three panels: bead list, detail, scheduler log
- Status bar remains visible when any panel is maximized
- All existing TUI functionality (filters, refresh, scheduler trigger, help overlay) continues to work
- All existing tests pass

## Files to Modify

| File | Change |
|------|--------|
| `src/codex_orchestrator/tui.py` | Layout restructure, status bar simplification, maximize action |
