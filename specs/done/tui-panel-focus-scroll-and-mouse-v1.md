# TUI Panel Focus, Scroll, And Mouse V1

## Objective

Improve the operator TUI usability by adding:

1. panel focus switching
2. scroll support for long bead details
3. mouse interaction support

This is a small follow-up to the existing TUI feature and should not redesign the overall layout.

## Scope

In scope:

- keyboard focus toggle between list and detail panels
- keyboard scrolling in detail panel
- mouse click and wheel behavior for list/detail interaction
- tests for focus and scroll behavior
- README controls update

Out of scope:

- new panels
- log streaming transport changes
- TUI theming overhaul

## Functional Requirements

### 1. Panel Focus Switching

Add focus states:

- `list` panel focused
- `detail` panel focused

Controls:

- `Tab`: switch focus forward (list -> detail -> list)
- `Shift+Tab`: switch focus backward

UI requirement:

- focused panel should be visually distinguishable.

### 2. Detail Panel Scrolling

When detail panel is focused:

- `j/k` or arrow up/down scrolls detail text
- `PageUp/PageDown` scrolls by larger step
- `Home/End` jumps to top/bottom
- selecting a different bead resets the detail scroll position to the top of the new bead

When list panel is focused:

- existing selection navigation behavior remains unchanged.

### 3. Mouse Support

Required behavior:

- click on list row focuses the list panel and selects the clicked visible bead row
- click inside detail panel gives focus to the detail panel without changing selection
- mouse wheel over focused/hovered detail panel scrolls detail text
- mouse wheel over list panel moves list selection

### 4. Backward Compatibility

- existing keybindings (`q`, `f`, `r`, `m`, `Enter`) continue to work.
- no changes to merge safety flow.

## Acceptance Criteria

1. Operator can switch focus between list/detail using `Tab`/`Shift+Tab`.
2. Detail content longer than viewport can be scrolled with keyboard and mouse.
3. List selection remains deterministic and keyboard behavior is unchanged when list panel is focused.
4. Mouse click selection and wheel scrolling work in both panels.
5. Tests cover focus switching, detail scroll mechanics, and mouse selection hooks.

## Deliverables

- TUI runtime changes in `src/codex_orchestrator/tui.py`
- tests (new or updated) in `tests/test_tui.py`
- README controls section update
