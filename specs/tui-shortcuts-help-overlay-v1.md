# TUI Shortcuts Help Overlay V1

## Objective

Add a tiny in-app help view so operators can discover keyboard shortcuts quickly.

## Scope

In scope:

- bind `?` to show a shortcuts help overlay/modal
- bind `Esc` (or `?` again) to close it
- keep existing TUI behavior unchanged when help is not open

Out of scope:

- redesigning panel layout
- adding persistent full-size help panel

## Functional Requirements

1. Pressing `?` opens a help overlay listing current keyboard shortcuts.
2. Help overlay includes at least:
   - navigation (`j/k`, arrows)
   - filter cycling (`f`)
   - refresh (`r`)
   - merge flow (`m`, `Enter`)
   - quit (`q`)
3. Pressing `Esc` closes the overlay and returns to previous focus state.
4. Pressing `?` while open also closes the overlay.
5. Footer should show a concise hint like `? help`.

## Acceptance Criteria

1. `?` toggles help overlay on/off reliably.
2. Overlay does not break refresh loop or merge state handling.
3. Existing keybindings keep working as before after closing help.
4. Tests cover help toggle and close behavior.

## Deliverables

- TUI keybinding and overlay implementation in `src/codex_orchestrator/tui.py`
- tests in `tests/test_tui.py`
- README controls note updated
