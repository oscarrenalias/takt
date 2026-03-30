# TUI Responsiveness And Interaction V2

## Objective

Make the operator TUI feel fast, reliable, and predictable by fixing interaction fundamentals:

- smooth list/detail navigation
- reliable keyboard + mouse scrolling
- low-latency panel updates
- explicit refresh behavior that avoids unnecessary redraw churn

This is a quality-focused iteration on the existing TUI, not a full redesign.

## Why This Matters

Current operator feedback indicates the TUI feels clumsy and slow:

- detail scrolling is not smooth
- mouse wheel behavior is inconsistent
- frequent refreshes can interrupt reading
- the whole screen appears to rerender too often for small actions

When operators use the TUI continuously, interaction quality is core functionality, not polish.

## Scope

In scope:

- switch detail and list rendering to scroll-native Textual components
- reduce unnecessary full-panel rerenders
- make refresh behavior manual-first with clear operator control
- improve mouse click + wheel handling consistency
- add responsiveness-focused regression tests
- update README TUI controls/behavior docs

Out of scope:

- new orchestration domain features
- redesigning planner/scheduler semantics
- external telemetry backends
- visual theming overhaul

## Functional Requirements

### 1. Manual-First Refresh Model

Default TUI behavior should avoid periodic storage refresh churn while the operator is reading.

Requirements:

- manual refresh remains available via `r`
- auto-refresh is disabled by default
- operator can toggle timed refresh/continuous mode explicitly (existing controls may be reused)
- status panel clearly shows current run/refresh mode

### 2. Visual Clarity And Ergonomics

The TUI should remain minimal but not feel spartan.

Requirements:

- add clear visual hierarchy between list, detail, and status/action areas
- use consistent spacing/padding so dense bead text remains readable
- keep focused-panel indication strong and unambiguous
- improve list row readability with subtle status-aware emphasis (without noisy color usage)
- keep color choices high-contrast and terminal-safe (no reliance on one accent color only)
- ensure visual behavior degrades gracefully on narrow terminal widths

### 3. Scroll-Native Detail Panel

The detail panel must use a Textual component with native scrolling behavior instead of manual line slicing.

Requirements:

- keyboard scroll (`j/k`, arrows, `PageUp/PageDown`, `Home/End`) remains supported
- wheel scrolling over details works consistently
- switching selected bead resets detail view to top
- panel should not require full-screen rerender for each scroll step

### 4. Efficient Rendering

Rendering updates should be incremental and panel-local when possible.

Requirements:

- avoid rebuilding both panels for single-panel interactions
- avoid recomputing heavy detail text unless selected bead or bead data changed
- selection movement updates list/detail without stutter on normal data sizes

### 5. Reliable Mouse Interaction

Mouse behavior must be deterministic.

Requirements:

- click list row selects bead and focuses list
- click detail panel focuses detail without changing bead selection
- wheel over list scrolls list/selection behavior consistently
- wheel over detail scrolls detail consistently
- no accidental mode switches during mouse interactions

### 6. Lightweight Visual Feedback

Operators should receive immediate but non-disruptive interaction feedback.

Requirements:

- status/action outcomes (success, warning, failure) are visually distinct in the status area
- pending confirmations (merge/retry/status update) are visually obvious without obscuring content
- optional, subtle row/panel highlight updates on selection/focus change (no heavy animations required)

### 7. Compatibility And Safety

- existing operator safety flows (retry/merge/status confirmation) remain intact
- existing keyboard shortcuts keep working unless explicitly changed and documented
- no regression in merge gating or feature-root scoping behavior

## Non-Functional Requirements

- target interactive update latency under ~100ms for common actions (selection move, single scroll step) on a typical local repo state
- avoid visible jitter caused by timer refresh while operator is actively interacting
- behavior should be consistent across keyboard-only and mouse-assisted workflows

## Acceptance Criteria

1. TUI starts in manual refresh mode and does not auto-refresh unless explicitly enabled.
2. Focus, selection, and status areas are visually clear and readable in typical terminal sizes.
3. Detail panel scrolling via keyboard and mouse is smooth and reliable for long bead content.
4. Mouse click and wheel behavior for list and detail panels works consistently and predictably.
5. Panel updates are incremental enough that normal navigation does not feel laggy/stuttery.
6. Pending confirmations and action outcomes are visually obvious and unambiguous.
7. Existing merge/retry/status safety flows and feature-root scoping remain correct.
8. Tests cover manual-vs-auto refresh behavior, scroll interactions, mouse routing, and no-regression safety paths.
9. README documents refresh mode behavior, interaction controls, and visual/focus cues.

## Suggested Implementation Notes

- prefer Textual scroll-capable widgets/containers for detail content
- isolate list panel rendering from detail panel rendering so each can update independently
- cache rendered detail content per selected bead/version and invalidate only when needed
- avoid calling storage refresh from timer while modal overlays are active or while manual scroll interaction is in progress
- keep status panel updates lightweight and avoid triggering heavy list/detail recomputation

## Deliverables

- TUI runtime updates in `src/codex_orchestrator/tui.py`
- test updates/additions in `tests/test_tui.py` (and `tests/test_orchestrator.py` only if needed)
- README updates describing refresh model and interaction behavior
