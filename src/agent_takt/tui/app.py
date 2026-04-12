from __future__ import annotations

from rich.text import Text

from ..models import BEAD_BLOCKED, BEAD_IN_PROGRESS, BEAD_READY, Bead
from ..storage import RepositoryStorage
from .actions import OrchestratorTuiActionsMixin
from .reporter import TuiSchedulerReporter
from .state import (
    DETAIL_SECTION_ORDER,
    PANEL_DETAIL,
    PANEL_LIST,
    PANEL_SCHEDULER_LOG,
    TuiRuntimeState,
)
from .render import (
    _DEFAULT_PANEL_WIDTH,
    _beads_panel_title,
    _detail_section_body,
    _detail_section_title,
    _detail_summary_lines,
    _panel_badge,
    _telemetry_badge,
    _truncate_title,
    format_help_overlay,
    render_detail_panel,
)


def _focus_status_hint(focused_panel: str) -> str:
    if focused_panel == PANEL_DETAIL:
        return "detail scroll"
    if focused_panel == PANEL_SCHEDULER_LOG:
        return "scheduler log scroll"
    return "list navigation"


def _live_status_bar_text(runtime_state: TuiRuntimeState) -> str:
    beads = runtime_state.beads
    running = sum(1 for b in beads if b.status == BEAD_IN_PROGRESS)
    ready = sum(1 for b in beads if b.status == BEAD_READY)
    blocked = sum(1 for b in beads if b.status == BEAD_BLOCKED)
    mode = "auto" if runtime_state.continuous_run_enabled else "manual"
    base = f"{running} running | {ready} ready | {blocked} blocked | S:{mode}"
    if runtime_state.status_message:
        return f"{base} | {runtime_state.status_message}"
    return base


def load_textual_runtime() -> object:
    try:
        import textual  # type: ignore
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "The `takt tui` command requires the optional `textual` package. "
            "Install dependencies and retry."
        ) from exc
    return textual


def build_tui_app(
    storage: RepositoryStorage,
    *,
    feature_root_id: str | None = None,
    refresh_seconds: int = 3,
    max_workers: int = 1,
):
    load_textual_runtime()
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.css.query import NoMatches
    from textual.containers import Center, Horizontal, Vertical, VerticalScroll
    from textual.screen import ModalScreen
    from textual.widgets import Collapsible, RichLog, Static, Tree

    class FocusableStatic(Static):
        can_focus = True

    class BeadTree(Tree[Bead]):
        """Tree widget for displaying beads with native expand/collapse."""

        show_root = False

        def _node_label(self, bead: Bead, width: int | None = None, subtree_telemetry: dict | None = None) -> str:
            badge = _telemetry_badge(bead, subtree_telemetry=subtree_telemetry)
            prefix = f"{bead.bead_id} · "
            suffix = f" [{bead.status}]{badge}"
            avail = (width if width is not None else _DEFAULT_PANEL_WIDTH)
            title_budget = avail - len(prefix) - len(suffix)
            title = _truncate_title(bead.title, max(0, title_budget))
            return f"{prefix}{title}{suffix}"

    class HelpOverlay(ModalScreen[None]):
        CSS = """
        HelpOverlay {
            align: center middle;
            background: $background 60%;
        }

        #help-dialog {
            width: 48;
            height: auto;
            border: round $accent;
            padding: 1 2;
            background: $surface;
        }
        """

        BINDINGS = [
            Binding("escape", "close_overlay", "Close", show=False),
            Binding("question_mark", "close_overlay", "Close Help", show=False),
        ]

        def __init__(self, runtime_state: TuiRuntimeState) -> None:
            super().__init__()
            self.runtime_state = runtime_state

        def compose(self) -> ComposeResult:
            with Center():
                yield Static(format_help_overlay(), id="help-dialog")

        def on_key(self, event: object) -> None:
            key = getattr(event, "key", None)
            if key not in {"escape", "question_mark"} and hasattr(event, "stop"):
                event.stop()

        def action_close_overlay(self) -> None:
            self.runtime_state.close_help_overlay()
            self.dismiss(None)

    class OrchestratorTuiApp(OrchestratorTuiActionsMixin, App[None]):
        CSS = """
        Screen {
            layout: vertical;
        }

        #main-row {
            height: 1fr;
        }

        #top-row {
            height: 2fr;
        }

        #scheduler-log {
            height: 1fr;
        }

        #list-panel, #detail-panel, #scheduler-log {
            border: round $accent;
            padding: 1;
            width: 1fr;
            overflow-y: auto;
        }

        #bead-tree, #bead-detail {
            height: 1fr;
        }

        #bead-detail {
            height: auto;
        }

        #detail-summary {
            padding-bottom: 1;
        }

        .detail-section {
            margin-top: 1;
        }

        .detail-section.-active {
            background: $accent 10%;
            tint: $accent 5%;
        }

        .focused {
            border: double $success;
            background: $success 12%;
            tint: $success 8%;
        }

        .maximized {
            width: 100%;
            height: 1fr;
        }

        .hidden {
            display: none;
        }

        #status-bar {
            height: 1;
            padding: 0 1;
        }
        """

        BINDINGS = [
            Binding("q", "quit", "Quit"),
            Binding("tab", "focus_next_panel", "Next Panel", show=False, priority=True),
            Binding("shift+tab", "focus_previous_panel", "Prev Panel", show=False, priority=True),
            Binding("j", "move_down", "Down", show=False),
            Binding("k", "move_up", "Up", show=False),
            Binding("down", "move_down", "Down", show=False),
            Binding("up", "move_up", "Up", show=False),
            Binding("pageup", "page_up", "Page Up", show=False),
            Binding("pagedown", "page_down", "Page Down", show=False),
            Binding("home", "go_home", "Home", show=False),
            Binding("end", "go_end", "End", show=False),
            Binding("g", "go_home", "Go to Top", show=False),
            Binding("G", "go_end", "Go to Bottom", show=False),
            Binding("n", "next_detail_section", "Next Detail Section", show=False),
            Binding("N", "previous_detail_section", "Prev Detail Section", show=False),
            Binding("f", "filter_next", "Next Filter"),
            Binding("shift+f", "filter_previous", "Prev Filter", show=False),
            Binding("question_mark", "toggle_help", "Help", show=False),
            Binding("a", "toggle_timed_refresh", "Auto Refresh"),
            Binding("r", "manual_refresh", "Refresh"),
            Binding("s", "scheduler_once", "Run Once"),
            Binding("S", "toggle_continuous_run", "Auto Run"),
            Binding("t", "retry_blocked", "Retry"),
            Binding("u", "start_status_update", "Status"),
            Binding("m", "toggle_maximize", "Maximize"),
            Binding("M", "request_merge", "Merge (CLI)"),
            Binding("enter", "confirm_merge", "Confirm", show=False, priority=True),
            Binding("b", "choose_blocked_status", "Blocked", show=False),
            Binding("d", "choose_done_status", "Done", show=False),
            Binding("y", "confirm_pending_action", "Confirm", show=False),
            Binding("c", "cancel_pending_action", "Cancel", show=False),
            Binding("E", "toggle_all_tree_nodes", "Expand/Collapse All", show=False),
        ]

        def __init__(self) -> None:
            super().__init__()
            self.runtime_state = TuiRuntimeState(
                storage,
                feature_root_id=feature_root_id,
                refresh_seconds=refresh_seconds,
                max_workers=max_workers,
            )
            self._last_list_render = ()
            self._last_detail_render = ""
            self._last_status_render = ""
            self._active_detail_section_index = 0
            self._detail_collapsed = {section: True for section in DETAIL_SECTION_ORDER}
            self._collapsed_bead_ids: set[str] = set()
            self._scheduler_worker_running = False

        def compose(self) -> ComposeResult:
            with Vertical(id="main-row"):
                with Horizontal(id="top-row"):
                    with Vertical(id="list-panel"):
                        yield BeadTree("Beads", id="bead-tree")
                    with VerticalScroll(id="detail-panel", can_focus=True):
                        with Vertical(id="bead-detail"):
                            yield Static(id="detail-summary")
                            for section in DETAIL_SECTION_ORDER:
                                yield Collapsible(
                                    Static(id=f"detail-{section}-body"),
                                    id=f"detail-{section}",
                                    title=_detail_section_title(section),
                                    collapsed=self._detail_collapsed[section],
                                    classes="detail-section",
                                )
                yield RichLog(id="scheduler-log", auto_scroll=True, wrap=True)
            yield Static(id="status-bar")

        def on_mount(self) -> None:
            self.title = "takt TUI"
            self.sub_title = feature_root_id or "all features"
            self.set_interval(refresh_seconds, self._on_interval_tick)
            self._populate_bead_tree()
            self._render_all()
            self._sync_panel_focus()
            try:
                log_widget = self.query_one("#scheduler-log", RichLog)
                log_widget.border_title = Text("Scheduler Log")
                log_widget.write(Text.from_markup("[dim]Press s to run a scheduler cycle, S for continuous mode[/dim]"))
            except NoMatches:
                pass

        def _make_help_overlay_screen(self) -> "HelpOverlay":
            return HelpOverlay(self.runtime_state)

        def _on_interval_tick(self) -> None:
            if not self.runtime_state.timed_refresh_enabled:
                return
            if self.runtime_state.continuous_run_enabled:
                self._start_scheduler_worker()
            else:
                self.runtime_state.refresh()
                self._render_all(force_detail=True)

        def _render_panels(self) -> None:
            self._render_all(force_detail=True)

        def _render_all(self, *, force_detail: bool = False, reset_detail_scroll: bool = False) -> None:
            self._render_focus()
            self._update_list_panel()
            self._update_detail_panel(force=force_detail, reset_scroll=reset_detail_scroll)
            self._update_status_panel()

        def _render_focus(self) -> None:
            try:
                list_panel = self.query_one("#list-panel", Vertical)
                detail_panel = self.query_one("#detail-panel", VerticalScroll)
                log_panel = self.query_one("#scheduler-log", RichLog)
            except NoMatches:
                # Main panels are not mounted on top-level while modal screens are active.
                return

            list_panel.set_class(self.runtime_state.focused_panel == PANEL_LIST, "focused")
            detail_panel.set_class(self.runtime_state.focused_panel == PANEL_DETAIL, "focused")
            log_panel.set_class(self.runtime_state.focused_panel == PANEL_SCHEDULER_LOG, "focused")
            list_panel.border_title = Text(
                _beads_panel_title(
                    self.runtime_state.filter_mode,
                    focused=self.runtime_state.focused_panel == PANEL_LIST,
                )
            )
            list_panel.border_subtitle = "Enter/j/k move selection" if self.runtime_state.focused_panel == PANEL_LIST else "Tab to activate"
            detail_panel.border_title = Text(_panel_badge("Details", focused=self.runtime_state.focused_panel == PANEL_DETAIL))
            detail_panel.border_subtitle = (
                Text("j/k scroll | n/N section | Enter toggle")
                if self.runtime_state.focused_panel == PANEL_DETAIL
                else "Tab to activate"
            )
            log_panel.border_title = Text(_panel_badge("Scheduler Log", focused=self.runtime_state.focused_panel == PANEL_SCHEDULER_LOG))
            log_panel.border_subtitle = (
                "j/k scroll | g/G top/bottom"
                if self.runtime_state.focused_panel == PANEL_SCHEDULER_LOG
                else "Tab to activate"
            )

        def _sync_panel_focus(self) -> None:
            try:
                if self.runtime_state.focused_panel == PANEL_DETAIL:
                    self.query_one("#detail-panel", VerticalScroll).focus()
                    self._focus_active_detail_section()
                elif self.runtime_state.focused_panel == PANEL_SCHEDULER_LOG:
                    self.query_one("#scheduler-log", RichLog).focus()
                else:
                    self.query_one("#bead-tree", BeadTree).focus()
            except NoMatches:
                return

        def _populate_bead_tree(self) -> None:
            """Rebuild the Tree widget from current runtime_state rows."""
            try:
                bead_tree = self.query_one("#bead-tree", BeadTree)
            except NoMatches:
                return

            bead_tree.clear()
            rows = self.runtime_state.rows
            if not rows:
                bead_tree.root.set_label("No beads match the current filter.")
                return

            bead_tree.root.set_label("Beads")
            # Build a map from bead_id to tree node for parent lookups
            node_map: dict[str, object] = {}
            for row in rows:
                bead = row.bead
                parent_node = node_map.get(bead.parent_id) if bead.parent_id else None
                target = parent_node if parent_node is not None else bead_tree.root
                tree_width = bead_tree.size.width if bead_tree.size.width > 0 else None
                subtree_tel = self.runtime_state.subtree_telemetry_for(bead.bead_id)
                label = bead_tree._node_label(bead, width=tree_width, subtree_telemetry=subtree_tel)
                if row.has_children:
                    node = target.add(label, data=bead)
                else:
                    node = target.add_leaf(label, data=bead)
                node_map[bead.bead_id] = node

            # Restore collapsed state
            for bead_id in self._collapsed_bead_ids:
                if bead_id in node_map:
                    node_map[bead_id].collapse()

            # Expand all non-collapsed nodes (Tree defaults to collapsed)
            for bead_id, node in node_map.items():
                if bead_id not in self._collapsed_bead_ids and hasattr(node, 'expand'):
                    node.expand()

            # Also expand root
            bead_tree.root.expand()

            # Restore selection
            selected_id = self.runtime_state.selected_bead_id
            if selected_id and selected_id in node_map:
                bead_tree.select_node(node_map[selected_id])

        def _update_list_panel(self) -> None:
            # Build a cache key from bead IDs, statuses, and titles to skip redundant rebuilds
            cache_key = tuple(
                (row.bead_id, row.bead.status, row.bead.title, row.depth)
                for row in self.runtime_state.rows
            )
            if cache_key == self._last_list_render:
                return
            self._last_list_render = cache_key
            self._populate_bead_tree()

        def _update_detail_panel(self, *, force: bool = False, reset_scroll: bool = False) -> None:
            try:
                detail_summary = self.query_one("#detail-summary", Static)
            except NoMatches:
                return
            if reset_scroll:
                self._reset_detail_sections()
                self.runtime_state.jump_detail_to_start()
            bead = self.runtime_state.selected_bead()
            subtree_tel = self.runtime_state.subtree_telemetry_for(bead.bead_id) if bead else None
            detail_render = render_detail_panel(bead, focused=self.runtime_state.focused_panel == PANEL_DETAIL, subtree_telemetry=subtree_tel)
            if force or detail_render != self._last_detail_render:
                detail_summary.update("\n".join(_detail_summary_lines(bead)))
                self._refresh_detail_sections(bead)
                self._last_detail_render = detail_render
            self.call_after_refresh(self._sync_detail_scroll)

        def _update_status_panel(self) -> None:
            try:
                status_bar = self.query_one("#status-bar", Static)
            except NoMatches:
                return
            status_render = _live_status_bar_text(self.runtime_state)
            if status_render != self._last_status_render:
                status_bar.update(status_render)
                self._last_status_render = status_render

        def _sync_detail_scroll(self) -> None:
            try:
                detail_panel = self.query_one("#detail-panel", VerticalScroll)
            except NoMatches:
                return
            self.runtime_state.set_rendered_detail_content_height(detail_panel.virtual_size.height)
            self.runtime_state.clamp_detail_scroll(self._detail_viewport_height())
            detail_panel.scroll_to(y=self.runtime_state.detail_scroll_offset, animate=False, force=True)

        def _list_viewport_height(self) -> int | None:
            try:
                return self.query_one("#bead-tree", BeadTree).content_region.height
            except NoMatches:
                return None

        def _detail_viewport_height(self) -> int | None:
            try:
                return self.query_one("#detail-panel", VerticalScroll).content_region.height
            except NoMatches:
                return None

        def _active_detail_section(self) -> str:
            return DETAIL_SECTION_ORDER[self._active_detail_section_index]

        def _reset_detail_sections(self) -> None:
            self._active_detail_section_index = 0
            self._detail_collapsed = {section: True for section in DETAIL_SECTION_ORDER}

        def _refresh_detail_sections(self, bead: Bead | None) -> None:
            subtree_tel = self.runtime_state.subtree_telemetry_for(bead.bead_id) if bead else None
            for section in DETAIL_SECTION_ORDER:
                body = self.query_one(f"#detail-{section}-body", Static)
                body.update(_detail_section_body(bead, section, subtree_telemetry=subtree_tel))
                collapsible = self.query_one(f"#detail-{section}", Collapsible)
                collapsible.collapsed = self._detail_collapsed[section]
                collapsible.set_class(section == DETAIL_SECTION_ORDER[self._active_detail_section_index], "-active")

        def _focus_active_detail_section(self) -> None:
            try:
                collapsible = self.query_one(f"#detail-{self._active_detail_section()}", Collapsible)
            except NoMatches:
                return
            title = next((child for child in collapsible.children if hasattr(child, "focus")), None)
            if title is not None:
                title.focus()

        def _move_detail_section(self, step: int) -> None:
            if self.runtime_state.focused_panel != PANEL_DETAIL:
                return
            next_index = max(0, min(self._active_detail_section_index + step, len(DETAIL_SECTION_ORDER) - 1))
            if next_index == self._active_detail_section_index:
                self.runtime_state.status_message = (
                    "Already at the last detail section." if step > 0 else "Already at the first detail section."
                )
                self._update_status_panel()
                return
            self._active_detail_section_index = next_index
            self.runtime_state.status_message = f"Active detail section: {_detail_section_title(self._active_detail_section())}."
            self._update_detail_panel(force=True)
            self.call_after_refresh(self._focus_active_detail_section)
            self._update_status_panel()

        def _toggle_active_detail_section(self) -> bool:
            if self.runtime_state.selected_bead() is None:
                return False
            section = self._active_detail_section()
            self._detail_collapsed[section] = not self._detail_collapsed[section]
            state = "collapsed" if self._detail_collapsed[section] else "expanded"
            self.runtime_state.status_message = f"{_detail_section_title(section)} {state}."
            self._update_detail_panel(force=True)
            self.call_after_refresh(self._focus_active_detail_section)
            return True

        def on_tree_node_highlighted(self, event: Tree.NodeHighlighted[Bead]) -> None:
            bead = event.node.data
            if bead is None:
                return
            previous_selection = self._selection_marker()
            for index, row in enumerate(self.runtime_state.rows):
                if row.bead_id == bead.bead_id:
                    self.runtime_state.select_index(index)
                    break
            changed = self._selection_changed(previous_selection)
            self._update_detail_panel(force=changed, reset_scroll=changed)
            self._update_status_panel()

        def on_tree_node_collapsed(self, event: Tree.NodeCollapsed[Bead]) -> None:
            bead = event.node.data
            if bead is not None:
                self._collapsed_bead_ids.add(bead.bead_id)

        def on_tree_node_expanded(self, event: Tree.NodeExpanded[Bead]) -> None:
            bead = event.node.data
            if bead is not None:
                self._collapsed_bead_ids.discard(bead.bead_id)

        def on_collapsible_collapsed(self, event: Collapsible.Collapsed) -> None:
            self._sync_detail_state_from_collapsible(event.collapsible, collapsed=True)

        def on_collapsible_expanded(self, event: Collapsible.Expanded) -> None:
            self._sync_detail_state_from_collapsible(event.collapsible, collapsed=False)

        def _selection_marker(self) -> tuple[str | None, int | None]:
            return self.runtime_state.selected_bead_id, self.runtime_state.selected_index

        def _selection_changed(self, previous_selection: tuple[str | None, int | None]) -> bool:
            return previous_selection != self._selection_marker()

        def _widget_matches_panel(self, widget: object, target_ids: set[str]) -> bool:
            current = widget
            while current is not None:
                if getattr(current, "id", None) in target_ids:
                    return True
                current = getattr(current, "parent", None)
            return False

        def on_click(self, event: object) -> None:
            widget = getattr(event, "widget", None)
            if widget is None:
                return
            if self._widget_matches_panel(widget, {"bead-tree", "list-panel"}):
                # Tree widget handles its own click-to-select; just sync focus
                self.runtime_state.set_focused_panel(PANEL_LIST, announce=False)
                self._render_focus()
                self._sync_panel_focus()
                self._update_status_panel()
                return
            if self._widget_matches_panel(widget, {"bead-detail", "detail-panel"}):
                self.runtime_state.set_focused_panel(PANEL_DETAIL, announce=False)
                self._sync_detail_section_from_widget(widget)
                self._render_focus()
                self._sync_panel_focus()
                self._update_status_panel()

        def on_mouse_scroll_down(self, event: object) -> None:
            self._route_mouse_scroll(event, direction=1)

        def on_mouse_scroll_up(self, event: object) -> None:
            self._route_mouse_scroll(event, direction=-1)

        def _route_mouse_scroll(self, event: object, *, direction: int) -> None:
            widget = getattr(event, "widget", None)
            if self._widget_matches_panel(widget, {"bead-detail", "detail-panel"}):
                self.runtime_state.set_focused_panel(PANEL_DETAIL, announce=False)
                changed = self.runtime_state.scroll_detail(direction, self._detail_viewport_height())
                if not changed:
                    self.runtime_state.status_message = (
                        "Detail view already at the bottom." if direction > 0 else "Detail view already at the top."
                    )
                self._render_focus()
                self._sync_panel_focus()
                self._sync_detail_scroll()
                self._update_status_panel()
                if hasattr(event, "stop"):
                    event.stop()
                return
            if self._widget_matches_panel(widget, {"bead-tree", "list-panel"}):
                # Tree widget handles its own scrolling
                self.runtime_state.set_focused_panel(PANEL_LIST, announce=False)
                self._render_focus()
                self._sync_panel_focus()
                self._update_status_panel()
                return
            if self.runtime_state.focused_panel == PANEL_DETAIL:
                self.runtime_state.scroll_detail(direction, self._detail_viewport_height())
                self._sync_detail_scroll()
            else:
                # Fallback: delegate scroll to the tree
                pass
            self._update_status_panel()

        def _sync_detail_section_from_widget(self, widget: object) -> None:
            current = widget
            while current is not None:
                widget_id = getattr(current, "id", None)
                for index, section in enumerate(DETAIL_SECTION_ORDER):
                    if widget_id == f"detail-{section}":
                        self._active_detail_section_index = index
                        return
                current = getattr(current, "parent", None)

        def _sync_detail_state_from_collapsible(self, collapsible: Collapsible, *, collapsed: bool) -> None:
            for index, section in enumerate(DETAIL_SECTION_ORDER):
                if collapsible.id != f"detail-{section}":
                    continue
                self._active_detail_section_index = index
                self._detail_collapsed[section] = collapsed
                collapsible.set_class(True, "-active")
                self.runtime_state.status_message = (
                    f"{_detail_section_title(section)} {'collapsed' if collapsed else 'expanded'}."
                )
                self._refresh_detail_sections(self.runtime_state.selected_bead())
                self.call_after_refresh(self._sync_detail_scroll)
                self._update_status_panel()
                return

        # ── Async scheduler worker ───────────────────────────────

        def _start_scheduler_worker(self) -> None:
            """Launch a scheduler cycle in a background worker thread."""
            if self._scheduler_worker_running:
                self.runtime_state.status_message = "Scheduler cycle already in progress."
                self._update_status_panel()
                return
            self._scheduler_worker_running = True
            self._update_status_panel()
            self.run_worker(self._scheduler_worker_task, exclusive=True, thread=True)

        def _scheduler_worker_task(self) -> bool:
            """Runs in a worker thread. Uses TuiSchedulerReporter for live events."""
            reporter = TuiSchedulerReporter(self, self.runtime_state)
            try:
                return self.runtime_state.run_scheduler_cycle(reporter=reporter)
            finally:
                self.call_from_thread(self._on_scheduler_worker_done)

        def _on_scheduler_worker_done(self) -> None:
            """Called on the main thread when the worker finishes."""
            self._scheduler_worker_running = False
            self.runtime_state.scheduler_running = False
            self._render_all(force_detail=True)

        def _append_log_line(self, line: str) -> None:
            """Append a line to the scheduler log widget. Must be called on the main thread."""
            try:
                log_widget = self.query_one("#scheduler-log", RichLog)
                log_widget.write(Text.from_markup(line))
            except NoMatches:
                pass

    return OrchestratorTuiApp()


def run_tui(
    storage: RepositoryStorage,
    *,
    feature_root_id: str | None = None,
    refresh_seconds: int = 3,
    max_workers: int = 1,
    stream: object | None = None,
) -> int:
    try:
        app = build_tui_app(storage, feature_root_id=feature_root_id, refresh_seconds=refresh_seconds, max_workers=max_workers)
    except RuntimeError as exc:
        target = stream if hasattr(stream, "write") else None
        message = f"{exc}\nHint: install project dependencies so `textual` is available.\n"
        if target is None:
            raise SystemExit(message.rstrip())
        target.write(message)
        if hasattr(target, "flush"):
            target.flush()
        return 1

    app.run()
    return 0
