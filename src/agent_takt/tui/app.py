from __future__ import annotations

from datetime import datetime
from pathlib import Path

from rich.text import Text

from ..models import BEAD_BLOCKED, BEAD_DONE, BEAD_IN_PROGRESS, BEAD_READY, Bead
from ..storage import RepositoryStorage
from .state import (
    DETAIL_SECTION_ACCEPTANCE,
    DETAIL_SECTION_FILES,
    DETAIL_SECTION_HANDOFF,
    DETAIL_SECTION_HISTORY,
    DETAIL_SECTION_ORDER,
    DETAIL_SECTION_TELEMETRY,
    EXECUTION_HISTORY_DISPLAY_LIMIT,
    PANEL_DETAIL,
    PANEL_LIST,
    PANEL_SCHEDULER_LOG,
    TuiRuntimeState,
    _format_block,
    _format_duration_ms,
    _format_list,
    _value_or_dash,
)
from .render import (
    _DEFAULT_PANEL_WIDTH,
    _telemetry_badge,
    _truncate_title,
    format_help_overlay,
    render_detail_panel,
)


def _panel_badge(panel_name: str, *, focused: bool) -> str:
    state = "ACTIVE" if focused else "idle"
    return f"{panel_name} [{state}]"


def _format_filter_label(filter_mode: str) -> str:
    return filter_mode.replace("_", " ").title()


def _beads_panel_title(filter_mode: str, *, focused: bool) -> str:
    return _panel_badge(f"Beads [{_format_filter_label(filter_mode)}]", focused=focused)


def _focus_status_hint(focused_panel: str) -> str:
    if focused_panel == PANEL_DETAIL:
        return "detail scroll"
    if focused_panel == PANEL_SCHEDULER_LOG:
        return "scheduler log scroll"
    return "list navigation"


def _detail_summary_lines(bead: Bead | None) -> list[str]:
    if bead is None:
        return ["No bead selected."]
    handoff = bead.handoff_summary
    return [
        f"Bead: {bead.bead_id}",
        f"Title: {bead.title}",
        f"Status: {bead.status}",
        f"Type: {bead.bead_type}",
        f"Agent: {bead.agent_type}",
        f"Parent: {_value_or_dash(bead.parent_id)}",
        f"Feature Root: {_value_or_dash(bead.feature_root_id)}",
        f"Dependencies: {_format_list(bead.dependencies)}",
        f"Block Reason: {_value_or_dash(bead.block_reason or handoff.block_reason)}",
    ]


def _detail_section_body(bead: Bead | None, section: str, subtree_telemetry: dict | None = None) -> str:
    if bead is None:
        return "-"
    handoff = bead.handoff_summary
    if section == DETAIL_SECTION_ACCEPTANCE:
        return "\n".join(_format_block(bead.acceptance_criteria))
    if section == DETAIL_SECTION_FILES:
        return "\n".join(
            [
                f"expected: {_format_list(bead.expected_files)}",
                f"expected_globs: {_format_list(bead.expected_globs)}",
                f"touched: {_format_list(bead.touched_files)}",
                f"changed: {_format_list(bead.changed_files)}",
                f"updated_docs: {_format_list(bead.updated_docs)}",
            ]
        )
    if section == DETAIL_SECTION_HANDOFF:
        return "\n".join(
            [
                f"completed: {_value_or_dash(handoff.completed)}",
                f"remaining: {_value_or_dash(handoff.remaining)}",
                f"risks: {_value_or_dash(handoff.risks)}",
                f"next_action: {_value_or_dash(handoff.next_action)}",
                f"next_agent: {_value_or_dash(handoff.next_agent)}",
                f"block_reason: {_value_or_dash(handoff.block_reason)}",
                f"touched_files: {_format_list(handoff.touched_files)}",
                f"changed_files: {_format_list(handoff.changed_files)}",
                f"expected_files: {_format_list(handoff.expected_files)}",
                f"expected_globs: {_format_list(handoff.expected_globs)}",
                f"updated_docs: {_format_list(handoff.updated_docs)}",
                f"conflict_risks: {_value_or_dash(handoff.conflict_risks or bead.conflict_risks)}",
            ]
        )
    if section == DETAIL_SECTION_TELEMETRY:
        telemetry = bead.metadata.get("telemetry")
        if not telemetry:
            return "No telemetry data."
        lines = [
            f"cost_usd: ${telemetry.get('cost_usd', 0):.2f}",
            f"duration: {_format_duration_ms(telemetry.get('duration_ms') or telemetry.get('duration_api_ms'))}",
            f"num_turns: {_value_or_dash(telemetry.get('num_turns'))}",
            f"input_tokens: {_value_or_dash(telemetry.get('input_tokens'))}",
            f"output_tokens: {_value_or_dash(telemetry.get('output_tokens'))}",
            f"cache_read_tokens: {_value_or_dash(telemetry.get('cache_read_tokens'))}",
            f"prompt_chars: {_value_or_dash(telemetry.get('prompt_chars'))}",
            f"session_id: {_value_or_dash(telemetry.get('session_id'))}",
        ]
        history = bead.metadata.get("telemetry_history")
        if history and len(history) > 1:
            total_cost = sum(h.get("cost_usd", 0) or 0 for h in history)
            lines.append(f"attempts: {len(history)} (total cost: ${total_cost:.2f})")
        if subtree_telemetry is not None:
            sub_cost = subtree_telemetry.get("cost_usd", 0)
            sub_duration = subtree_telemetry.get("duration_ms", 0)
            sub_count = subtree_telemetry.get("bead_count", 0)
            lines.append(f"Subtree: ${sub_cost:.2f} total, {_format_duration_ms(sub_duration)} duration, {sub_count} beads")
        return "\n".join(lines)
    if section == DETAIL_SECTION_HISTORY:
        exec_history = bead.execution_history
        if not exec_history:
            return "No execution history."
        lines = []
        omitted = len(exec_history) - EXECUTION_HISTORY_DISPLAY_LIMIT
        if omitted > 0:
            lines.append(f"... {omitted} earlier entries omitted")
        for record in exec_history[-EXECUTION_HISTORY_DISPLAY_LIMIT:]:
            lines.append(f"[{record.timestamp}] {record.event} ({record.agent_type}): {record.summary}")
        return "\n".join(lines)
    raise ValueError(f"Unknown detail section: {section}")


def _detail_section_title(section: str) -> str:
    titles = {
        DETAIL_SECTION_ACCEPTANCE: "Acceptance Criteria",
        DETAIL_SECTION_FILES: "Files",
        DETAIL_SECTION_HANDOFF: "Handoff",
        DETAIL_SECTION_TELEMETRY: "Telemetry",
        DETAIL_SECTION_HISTORY: "Execution History",
    }
    return titles[section]


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

    class OrchestratorTuiApp(App[None]):
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

        def action_focus_next_panel(self) -> None:
            self.runtime_state.cycle_focus(1)
            self._render_focus()
            self._sync_panel_focus()
            self._update_status_panel()

        def action_focus_previous_panel(self) -> None:
            self.runtime_state.cycle_focus(-1)
            self._render_focus()
            self._sync_panel_focus()
            self._update_status_panel()

        def action_move_down(self) -> None:
            if self.runtime_state.focused_panel == PANEL_DETAIL:
                if not self.runtime_state.scroll_detail(1, self._detail_viewport_height()):
                    self.runtime_state.status_message = "Detail view already at the bottom."
                self._sync_detail_scroll()
            elif self.runtime_state.focused_panel == PANEL_SCHEDULER_LOG:
                try:
                    self.query_one("#scheduler-log", RichLog).scroll_down()
                except NoMatches:
                    pass
            else:
                try:
                    bead_tree = self.query_one("#bead-tree", BeadTree)
                    bead_tree.action_cursor_down()
                except NoMatches:
                    pass
            self._update_status_panel()

        def action_move_up(self) -> None:
            if self.runtime_state.focused_panel == PANEL_DETAIL:
                if not self.runtime_state.scroll_detail(-1, self._detail_viewport_height()):
                    self.runtime_state.status_message = "Detail view already at the top."
                self._sync_detail_scroll()
            elif self.runtime_state.focused_panel == PANEL_SCHEDULER_LOG:
                try:
                    self.query_one("#scheduler-log", RichLog).scroll_up()
                except NoMatches:
                    pass
            else:
                try:
                    bead_tree = self.query_one("#bead-tree", BeadTree)
                    bead_tree.action_cursor_up()
                except NoMatches:
                    pass
            self._update_status_panel()

        def action_page_up(self) -> None:
            if self.runtime_state.focused_panel == PANEL_DETAIL:
                if not self.runtime_state.page_detail(-1, self._detail_viewport_height()):
                    self.runtime_state.status_message = "Detail view already at the top."
                self._sync_detail_scroll()
            elif self.runtime_state.focused_panel == PANEL_SCHEDULER_LOG:
                try:
                    self.query_one("#scheduler-log", RichLog).scroll_page_up()
                except NoMatches:
                    pass
            else:
                try:
                    bead_tree = self.query_one("#bead-tree", BeadTree)
                    bead_tree.action_page_up()
                except NoMatches:
                    pass
            self._update_status_panel()

        def action_page_down(self) -> None:
            if self.runtime_state.focused_panel == PANEL_DETAIL:
                if not self.runtime_state.page_detail(1, self._detail_viewport_height()):
                    self.runtime_state.status_message = "Detail view already at the bottom."
                self._sync_detail_scroll()
            elif self.runtime_state.focused_panel == PANEL_SCHEDULER_LOG:
                try:
                    self.query_one("#scheduler-log", RichLog).scroll_page_down()
                except NoMatches:
                    pass
            else:
                try:
                    bead_tree = self.query_one("#bead-tree", BeadTree)
                    bead_tree.action_page_down()
                except NoMatches:
                    pass
            self._update_status_panel()

        def action_go_home(self) -> None:
            if self.runtime_state.focused_panel == PANEL_DETAIL:
                if not self.runtime_state.jump_detail_to_start():
                    self.runtime_state.status_message = "Detail view already at the top."
                self._sync_detail_scroll()
            elif self.runtime_state.focused_panel == PANEL_SCHEDULER_LOG:
                try:
                    self.query_one("#scheduler-log", RichLog).scroll_home()
                except NoMatches:
                    pass
            else:
                try:
                    bead_tree = self.query_one("#bead-tree", BeadTree)
                    bead_tree.action_scroll_home()
                except NoMatches:
                    pass
            self._update_status_panel()

        def action_go_end(self) -> None:
            if self.runtime_state.focused_panel == PANEL_DETAIL:
                if not self.runtime_state.jump_detail_to_end(self._detail_viewport_height()):
                    self.runtime_state.status_message = "Detail view already at the bottom."
                self._sync_detail_scroll()
            elif self.runtime_state.focused_panel == PANEL_SCHEDULER_LOG:
                try:
                    self.query_one("#scheduler-log", RichLog).scroll_end()
                except NoMatches:
                    pass
            else:
                try:
                    bead_tree = self.query_one("#bead-tree", BeadTree)
                    bead_tree.action_scroll_end()
                except NoMatches:
                    pass
            self._update_status_panel()

        def action_filter_next(self) -> None:
            self._collapsed_bead_ids.clear()
            self.runtime_state.cycle_filter(1)
            self._render_all(force_detail=True, reset_detail_scroll=True)

        def action_filter_previous(self) -> None:
            self._collapsed_bead_ids.clear()
            self.runtime_state.cycle_filter(-1)
            self._render_all(force_detail=True, reset_detail_scroll=True)

        def action_previous_detail_section(self) -> None:
            self._move_detail_section(-1)

        def action_next_detail_section(self) -> None:
            self._move_detail_section(1)

        def action_toggle_timed_refresh(self) -> None:
            self.runtime_state.toggle_timed_refresh()
            self._update_status_panel()

        def action_manual_refresh(self) -> None:
            if self.runtime_state.status_flow_active:
                self.runtime_state.choose_status_target(BEAD_READY)
                self._update_status_panel()
                return
            self.runtime_state._clear_pending_actions()
            self.runtime_state.refresh(activity_message="Manual refresh completed.")
            self.runtime_state.status_message = "Refreshed bead state."
            self._render_all(force_detail=True)

        def action_scheduler_once(self) -> None:
            self._start_scheduler_worker()

        def action_toggle_continuous_run(self) -> None:
            self.runtime_state.toggle_continuous_run()
            self._update_status_panel()

        def action_retry_blocked(self) -> None:
            self.runtime_state.request_retry_selected_blocked_bead()
            self._update_status_panel()

        def action_start_status_update(self) -> None:
            self.runtime_state.open_status_update_flow()
            self._update_status_panel()

        def action_toggle_help(self) -> None:
            if self.runtime_state.toggle_help_overlay():
                self._update_status_panel()
                self.push_screen(HelpOverlay(self.runtime_state), callback=lambda _: self._update_status_panel())
                return
            self._update_status_panel()

        def action_toggle_maximize(self) -> None:
            focused = self.runtime_state.focused_panel
            try:
                list_panel = self.query_one("#list-panel", Vertical)
                detail_panel = self.query_one("#detail-panel", VerticalScroll)
                log_panel = self.query_one("#scheduler-log", RichLog)
                top_row = self.query_one("#top-row", Horizontal)
            except NoMatches:
                return
            all_panels = {
                PANEL_LIST: list_panel,
                PANEL_DETAIL: detail_panel,
                PANEL_SCHEDULER_LOG: log_panel,
            }
            if self.runtime_state.maximized_panel == focused:
                # Restore: remove maximized/hidden from all panels and top-row
                self.runtime_state.maximized_panel = None
                for panel in all_panels.values():
                    panel.remove_class("maximized", "hidden")
                top_row.remove_class("hidden")
                self.runtime_state.status_message = "Restored three-panel layout."
            else:
                # Maximize the focused panel, hide the others
                self.runtime_state.maximized_panel = focused
                for name, panel in all_panels.items():
                    if name == focused:
                        panel.remove_class("hidden")
                        panel.add_class("maximized")
                    else:
                        panel.remove_class("maximized")
                        panel.add_class("hidden")
                # When maximizing the scheduler log, also hide the top-row container
                # so the log panel can expand to fill all available space.
                if focused == PANEL_SCHEDULER_LOG:
                    top_row.add_class("hidden")
                else:
                    top_row.remove_class("hidden")
                self.runtime_state.status_message = f"Maximized {focused} panel."
            self._update_status_panel()
            # Force bead tree to rebuild with the new panel width after layout settles.
            self._last_list_render = ()
            self.call_after_refresh(self._populate_bead_tree)

        def action_request_merge(self) -> None:
            self.runtime_state.request_merge()
            self._update_status_panel()

        def action_confirm_merge(self) -> None:
            if self.runtime_state.help_overlay_visible:
                return
            if self.runtime_state.focused_panel == PANEL_DETAIL and not self.runtime_state.awaiting_merge_confirmation:
                if self._toggle_active_detail_section():
                    return
            if not self.runtime_state.awaiting_merge_confirmation and self.runtime_state.focused_panel == PANEL_LIST:
                # Delegate Enter to the Tree for expand/collapse toggle
                try:
                    bead_tree = self.query_one("#bead-tree", BeadTree)
                    bead_tree.action_toggle_node()
                except NoMatches:
                    pass
                return
            self.runtime_state.confirm_merge()
            self._render_all(force_detail=True)

        def action_choose_blocked_status(self) -> None:
            self.runtime_state.choose_status_target(BEAD_BLOCKED)
            self._update_status_panel()

        def action_choose_done_status(self) -> None:
            self.runtime_state.choose_status_target(BEAD_DONE)
            self._update_status_panel()

        def action_confirm_pending_action(self) -> None:
            if self.runtime_state.awaiting_retry_confirmation:
                self.runtime_state.confirm_retry_selected_blocked_bead()
            else:
                self.runtime_state.confirm_status_update()
            self._render_all(force_detail=True)

        def action_cancel_pending_action(self) -> None:
            self.runtime_state.cancel_pending_action()
            self._update_status_panel()

        def action_toggle_all_tree_nodes(self) -> None:
            """Toggle all tree nodes between fully expanded and fully collapsed."""
            try:
                bead_tree = self.query_one("#bead-tree", BeadTree)
            except NoMatches:
                return
            rows = self.runtime_state.rows
            expandable_ids = {row.bead.bead_id for row in rows if row.has_children}
            if not expandable_ids:
                return
            # If any expandable node is collapsed, expand all; otherwise collapse all.
            any_collapsed = bool(self._collapsed_bead_ids & expandable_ids)
            if any_collapsed:
                self._collapsed_bead_ids.clear()
            else:
                self._collapsed_bead_ids = set(expandable_ids)
            self._populate_bead_tree()

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


class TuiSchedulerReporter:
    """SchedulerReporter that posts events to a Textual app from a worker thread."""

    def __init__(self, app: object, state: TuiRuntimeState) -> None:
        self._app = app
        self._state = state
        self._cycle_header_logged = False
        self._deferred_this_cycle: set[str] = set()

    def _post(self, text: str, *, style: str | None = None) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        if not self._cycle_header_logged:
            self._cycle_header_logged = True
            self._state.deferred_this_cycle.clear()
            header = f"[{ts}] Scheduler cycle starting..."
            self._state.scheduler_log.append(header)
            try:
                self._app.call_from_thread(self._app._append_log_line, header)
            except Exception:
                pass
        line = f"[{ts}] {text}"
        if style:
            line = f"[{style}]{line}[/{style}]"
        self._state.scheduler_log.append(line)
        try:
            self._app.call_from_thread(self._app._append_log_line, line)
        except Exception:
            pass

    def stop(self) -> None:
        pass

    def lease_expired(self, bead_id: str) -> None:
        self._post(f"Lease expired: {bead_id} requeued")

    def bead_started(self, bead: Bead) -> None:
        self._post(f"[{bead.bead_id}] Started {bead.agent_type}: {bead.title}")

    def worktree_ready(self, bead: Bead, branch_name: str, worktree_path: Path) -> None:
        self._post(f"[{bead.bead_id}] Worktree ready: {worktree_path}")

    def bead_completed(self, bead: Bead, summary: str, created: list[Bead]) -> None:
        self._post(f"[{bead.bead_id}] Completed")
        for child in created:
            self._post(f"[{bead.bead_id}] Created followup {child.bead_id} ({child.agent_type})")

    def bead_deferred(self, bead: Bead, reason: str) -> None:
        self._post(f"[{bead.bead_id}] Deferred: {reason}", style="dim")
        self._deferred_this_cycle.add(bead.bead_id)
        self._state.deferred_this_cycle = set(self._deferred_this_cycle)

    def bead_blocked(self, bead: Bead, summary: str) -> None:
        self._post(f"[{bead.bead_id}] Blocked: {summary}")

    def bead_failed(self, bead: Bead, summary: str) -> None:
        self._post(f"[{bead.bead_id}] Failed: {summary}")


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
