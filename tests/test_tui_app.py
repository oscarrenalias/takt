from __future__ import annotations

import asyncio
import io
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_takt.cli import apply_operator_status_update, build_parser, command_tui
from agent_takt.console import ConsoleReporter
from agent_takt.models import (
    BEAD_BLOCKED,
    BEAD_DONE,
    BEAD_HANDED_OFF,
    BEAD_IN_PROGRESS,
    BEAD_OPEN,
    BEAD_READY,
    Bead,
    ExecutionRecord,
    HandoffSummary,
    SchedulerResult,
)
from agent_takt.storage import RepositoryStorage
from agent_takt.tui import (
    DETAIL_SECTION_HISTORY,
    DETAIL_SECTION_TELEMETRY,
    EXECUTION_HISTORY_DISPLAY_LIMIT,
    FILTER_ACTIONABLE,
    FILTER_ALL,
    FILTER_DEFAULT,
    FILTER_DEFERRED,
    FILTER_DONE,
    PANEL_DETAIL,
    PANEL_LIST,
    PANEL_SCHEDULER_LOG,
    TuiRuntimeState,
    TuiSchedulerReporter,
    _detail_section_body,
    _detail_section_title,
    _format_duration_ms,
    _telemetry_badge,
    build_tree_rows,
    build_tui_app,
    collect_tree_rows,
    format_detail_panel,
    format_footer,
    format_help_overlay,
    render_detail_panel,
    render_tree_panel,
    resolve_selected_bead,
    resolve_selected_index,
    run_tui,
    supported_filter_modes,
)

from helpers import OrchestratorTests as _OrchestratorBase  # noqa: E402

class TuiRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        source_templates = REPO_ROOT / "templates" / "agents"
        target_templates = self.root / "templates" / "agents"
        target_templates.mkdir(parents=True, exist_ok=True)
        for template_path in source_templates.glob("*.md"):
            shutil.copy2(template_path, target_templates / template_path.name)
        self.storage = RepositoryStorage(self.root)
        self.storage.initialize()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _create_feature_tree(self) -> tuple[str, dict[str, str]]:
        epic = self.storage.create_bead(
            bead_id="B0001",
            title="Epic",
            agent_type="planner",
            description="epic",
            bead_type="epic",
            status=BEAD_DONE,
        )
        root = self.storage.create_bead(
            bead_id="B0002",
            title="Feature Root",
            agent_type="developer",
            description="feature",
            parent_id=epic.bead_id,
            status=BEAD_DONE,
        )
        statuses = {
            "B0002-1": BEAD_OPEN,
            "B0002-2": BEAD_READY,
            "B0002-3": BEAD_IN_PROGRESS,
            "B0002-4": BEAD_BLOCKED,
            "B0002-5": BEAD_HANDED_OFF,
            "B0002-6": BEAD_DONE,
        }
        for bead_id, status in statuses.items():
            self.storage.create_bead(
                bead_id=bead_id,
                title=f"{status} task",
                agent_type="developer",
                description=status,
                parent_id=root.bead_id,
                dependencies=[root.bead_id],
                status=status,
            )
        return root.bead_id, statuses

    def test_detail_panel_prefers_handoff_block_reason_and_renders_handoff_summary(self) -> None:
        bead = Bead(
            bead_id="B0099",
            title="Selected bead",
            agent_type="tester",
            description="detail coverage",
            status=BEAD_BLOCKED,
            handoff_summary=HandoffSummary(
                completed="Covered helper formatting.",
                remaining="Need a merge retry.",
                risks="Refresh state could regress.",
                next_action="Re-run merge flow.",
                next_agent="developer",
                block_reason="Waiting on merge conflict resolution.",
                touched_files=["tests/test_tui_app.py"],
                changed_files=["tests/test_tui_app.py"],
                expected_files=["tests/test_tui_app.py"],
                expected_globs=["tests/test_*.py"],
                updated_docs=["specs/tui-operator-console-v1.md"],
                conflict_risks="Keep footer wording aligned with runtime text.",
            ),
        )

        detail = format_detail_panel(bead)

        self.assertIn("Block Reason: Waiting on merge conflict resolution.", detail)
        self.assertIn("Handoff:", detail)
        self.assertIn("  completed: Covered helper formatting.", detail)
        self.assertIn("  remaining: Need a merge retry.", detail)
        self.assertIn("  next_agent: developer", detail)
        self.assertIn("  updated_docs: specs/tui-operator-console-v1.md", detail)
        self.assertIn("  conflict_risks: Keep footer wording aligned with runtime text.", detail)

    def test_help_overlay_text_documents_toggle_shortcuts(self) -> None:
        overlay = format_help_overlay()

        self.assertIn("Shortcuts", overlay)
        self.assertIn("Tab         Focus next panel", overlay)
        self.assertIn("Shift+Tab   Focus previous panel", overlay)
        self.assertIn("g / G       Jump to first/last bead", overlay)
        self.assertIn("n / N       Next/prev detail section", overlay)
        self.assertIn("q           Quit", overlay)
        self.assertIn("Shift+f     Previous filter", overlay)
        self.assertIn("t           Request blocked-bead retry", overlay)
        self.assertIn("Enter       Toggle detail section", overlay)
        self.assertIn("y           Confirm retry/status update", overlay)
        self.assertIn("c           Cancel pending retry/status", overlay)
        self.assertIn("? / Esc     Close help", overlay)

    def test_help_overlay_close_rerenders_status_panel(self) -> None:
        self.storage.create_bead(bead_id="B0001", title="First", agent_type="developer", description="first", status=BEAD_READY)
        app = build_tui_app(self.storage)

        async def exercise_app() -> tuple[str, str]:
            async with app.run_test() as pilot:
                await pilot.pause()
                status_panel = app.screen.query_one("#status-bar")
                opened_text = str(status_panel.renderable)

                await pilot.press("?")
                await pilot.pause()
                base_screen = app.screen_stack[0]
                opened_text = str(base_screen.query_one("#status-bar").renderable)

                await pilot.press("?")
                await pilot.pause()
                closed_text = str(app.screen.query_one("#status-bar").renderable)
                return opened_text, closed_text

        opened_text, closed_text = asyncio.run(exercise_app())

        self.assertIn("Help overlay open. Press ? or Esc to close.", opened_text)
        self.assertIn("Help overlay closed.", closed_text)

    def test_help_overlay_escape_restores_refresh_keybinding(self) -> None:
        self.storage.create_bead(bead_id="B0001", title="First", agent_type="developer", description="first", status=BEAD_READY)
        app = build_tui_app(self.storage, refresh_seconds=60)

        async def exercise_app() -> tuple[str, str, str]:
            async with app.run_test() as pilot:
                await pilot.pause()

                await pilot.press("?")
                await pilot.pause()
                await pilot.press("r")
                await pilot.pause()
                blocked_status = app.runtime_state.status_message
                blocked_activity = app.runtime_state.activity_message

                await pilot.press("escape")
                await pilot.pause()
                await pilot.press("r")
                await pilot.pause()
                refreshed_status = app.runtime_state.status_message
                return blocked_status, blocked_activity, refreshed_status

        blocked_status, blocked_activity, refreshed_status = asyncio.run(exercise_app())

        self.assertEqual("Help overlay open. Press ? or Esc to close.", blocked_status)
        self.assertEqual("Loaded bead state.", blocked_activity)
        self.assertEqual("Refreshed bead state.", refreshed_status)

    def test_pressing_m_shows_cli_redirect_message_in_tui(self) -> None:
        # TUI no longer performs merges inline; M key shows the CLI command
        self.storage.create_bead(bead_id="B0001", title="Done", agent_type="developer", description="done", status=BEAD_DONE)
        app = build_tui_app(self.storage, refresh_seconds=60)
        app.runtime_state.filter_mode = FILTER_ALL
        app.runtime_state.refresh()

        async def exercise_app() -> tuple[str, bool]:
            async with app.run_test() as pilot:
                await pilot.resize_terminal(80, 18)
                await pilot.pause()

                # Press M (Shift+M) — should show CLI redirect, not set pending state
                await pilot.press("M")
                await pilot.pause()
                status_after_m = app.runtime_state.status_message
                pending_after_m = app.runtime_state.awaiting_merge_confirmation

                return status_after_m, pending_after_m

        status_after_m, pending_after_m = asyncio.run(exercise_app())

        self.assertIn("takt merge B0001", status_after_m, "M key should show CLI redirect")
        self.assertFalse(pending_after_m, "M key should not set awaiting_merge_confirmation")

    def test_render_panels_ignores_no_matches_when_overlay_is_active(self) -> None:
        app = build_tui_app(self.storage)

        from textual.css.query import NoMatches

        with patch.object(app, "query_one", side_effect=NoMatches()):
            app._render_panels()

    def test_renderers_include_explicit_active_panel_cues(self) -> None:
        bead = self.storage.create_bead(
            bead_id="B0001",
            title="Ready",
            agent_type="developer",
            description="ready",
            status=BEAD_READY,
        )
        rows = build_tree_rows([bead])

        list_render = render_tree_panel(rows, 0, filter_mode=FILTER_DEFAULT, focused=True)
        blocked_render = render_tree_panel(rows, 0, filter_mode=BEAD_BLOCKED, focused=False)
        detail_render = render_detail_panel(bead, focused=False)

        self.assertIn(">> B0001", list_render)
        self.assertNotIn("Beads [Default] [ACTIVE]", list_render)
        self.assertNotIn("Beads [Blocked] [idle]", blocked_render)
        self.assertEqual("No beads match the current filter.", render_tree_panel([], None))
        self.assertNotIn("Details [idle]", detail_render)
        self.assertTrue(detail_render.startswith("Press Tab to focus."))

    def test_app_filter_cycle_updates_panel_border_titles(self) -> None:
        self.storage.create_bead(bead_id="B0001", title="Open", agent_type="developer", description="open", status=BEAD_OPEN)
        self.storage.create_bead(
            bead_id="B0002",
            title="Ready",
            agent_type="developer",
            description="ready",
            status=BEAD_READY,
        )
        app = build_tui_app(self.storage, refresh_seconds=60)

        def title_text(value: object) -> str:
            text = value.plain if hasattr(value, "plain") else str(value)
            return text.replace("\\[", "[").replace("\\]", "]")

        async def exercise_app() -> tuple[str, str, str, object]:
            async with app.run_test() as pilot:
                await pilot.pause()
                list_panel = app.screen.query_one("#list-panel")
                detail_panel = app.screen.query_one("#detail-panel")
                status_panel = app.screen.query_one("#status-bar")
                default_title = title_text(list_panel.border_title)
                detail_title = title_text(detail_panel.border_title)
                # Read raw border_title (not through title_text) to distinguish None from "None"
                status_raw_title = status_panel.border_title

                for _ in range(6):
                    await pilot.press("f")
                    await pilot.pause()

                ready_title = title_text(app.screen.query_one("#list-panel").border_title)
                return default_title, ready_title, detail_title, status_raw_title

        default_title, ready_title, detail_title, status_title = asyncio.run(exercise_app())

        self.assertIn("Beads [Default] [ACTIVE]", default_title)
        self.assertIn("Beads [Ready] [ACTIVE]", ready_title)
        self.assertIn("Details [idle]", detail_title)
        # Status bar is now a borderless single-line widget; border_title is not set
        self.assertIsNone(status_title)

    def test_keyboard_detail_page_and_home_end_actions_scroll_without_changing_selection(self) -> None:
        self.storage.create_bead(
            bead_id="B0001",
            title="Scrollable",
            agent_type="developer",
            description="scroll",
            status=BEAD_READY,
            acceptance_criteria=[f"criterion {index}" for index in range(80)],
        )
        self.storage.create_bead(
            bead_id="B0002",
            title="Second",
            agent_type="developer",
            description="next",
            status=BEAD_READY,
        )
        app = build_tui_app(self.storage, refresh_seconds=60)

        async def exercise_app() -> tuple[int, int, int, str, int]:
            async with app.run_test() as pilot:
                await pilot.resize_terminal(80, 18)
                await pilot.pause()
                await pilot.press("tab")
                await pilot.pause()
                await pilot.press("enter")
                await pilot.pause()

                await pilot.press("pagedown")
                await pilot.pause()
                after_page_down = app.runtime_state.detail_scroll_offset

                await pilot.press("end")
                await pilot.pause()
                after_end = app.runtime_state.detail_scroll_offset

                await pilot.press("home")
                await pilot.pause()
                return (
                    after_page_down,
                    after_end,
                    app.runtime_state.detail_scroll_offset,
                    app.runtime_state.selected_bead_id or "-",
                    -1 if app.runtime_state.selected_index is None else app.runtime_state.selected_index,
                )

        after_page_down, after_end, after_home, selected_bead_id, selected_index = asyncio.run(exercise_app())

        self.assertGreaterEqual(after_page_down, 0)
        self.assertGreaterEqual(after_end, after_page_down)
        self.assertEqual(0, after_home)
        self.assertEqual("B0001", selected_bead_id)
        self.assertEqual(0, selected_index)

    def test_keyboard_boundary_list_navigation_preserves_detail_scroll(self) -> None:
        self.storage.create_bead(
            bead_id="B0001",
            title="Scrollable",
            agent_type="developer",
            description="scroll",
            status=BEAD_READY,
            acceptance_criteria=[f"criterion {index}" for index in range(80)],
        )
        self.storage.create_bead(
            bead_id="B0002",
            title="Second",
            agent_type="developer",
            description="next",
            status=BEAD_READY,
        )
        app = build_tui_app(self.storage, refresh_seconds=60)

        async def exercise_app() -> tuple[int, int, str, int, str]:
            async with app.run_test() as pilot:
                await pilot.resize_terminal(80, 18)
                await pilot.pause()
                await pilot.press("tab")
                await pilot.pause()
                await pilot.press("j")
                await pilot.pause()
                scrolled_offset = app.runtime_state.detail_scroll_offset

                await pilot.press("shift+tab")
                await pilot.pause()
                await pilot.press("up")
                await pilot.pause()
                return (
                    scrolled_offset,
                    app.runtime_state.detail_scroll_offset,
                    app.runtime_state.selected_bead_id or "-",
                    -1 if app.runtime_state.selected_index is None else app.runtime_state.selected_index,
                    app.runtime_state.status_message,
                )

        scrolled_offset, offset_after_noop, selected_bead_id, selected_index, status_message = asyncio.run(exercise_app())

        self.assertGreater(scrolled_offset, 0)
        self.assertEqual(scrolled_offset, offset_after_noop)
        self.assertEqual("B0001", selected_bead_id)
        self.assertEqual(0, selected_index)
        # With the Tree widget, up at boundary is handled silently by the tree;
        # the status message reflects the most recent panel focus change.
        self.assertIsInstance(status_message, str)

    def test_keyboard_navigation_routes_by_focused_panel(self) -> None:
        self.storage.create_bead(
            bead_id="B0001",
            title="Scrollable",
            agent_type="developer",
            description="scroll",
            status=BEAD_READY,
            acceptance_criteria=[f"criterion {index}" for index in range(80)],
        )
        second = self.storage.create_bead(
            bead_id="B0002",
            title="Second",
            agent_type="developer",
            description="next",
            status=BEAD_READY,
        )
        app = build_tui_app(self.storage, refresh_seconds=60)

        async def exercise_app() -> tuple[str, int, int, str]:
            async with app.run_test() as pilot:
                await pilot.resize_terminal(80, 18)
                await pilot.pause()
                await pilot.press("tab")
                await pilot.pause()
                detail_focus = app.runtime_state.focused_panel

                await pilot.press("j")
                await pilot.pause()
                scrolled_offset = app.runtime_state.detail_scroll_offset
                selected_while_detail = app.runtime_state.selected_bead_id

                await pilot.press("shift+tab")
                await pilot.pause()
                await pilot.press("j")
                await pilot.pause()
                return (
                    detail_focus,
                    scrolled_offset,
                    -1 if app.runtime_state.selected_index is None else app.runtime_state.selected_index,
                    selected_while_detail,
                )

        detail_focus, scrolled_offset, selected_index, selected_while_detail = asyncio.run(exercise_app())

        self.assertEqual(PANEL_DETAIL, detail_focus)
        self.assertGreater(scrolled_offset, 0)
        self.assertEqual("B0001", selected_while_detail)
        self.assertEqual(second.bead_id, app.runtime_state.selected_bead_id)
        self.assertEqual(1, selected_index)

    def test_mouse_click_and_wheel_route_to_list_and_detail_panels(self) -> None:
        self.storage.create_bead(
            bead_id="B0001",
            title="Scrollable",
            agent_type="developer",
            description="scroll",
            status=BEAD_READY,
            acceptance_criteria=[f"criterion {index}" for index in range(80)],
        )
        self.storage.create_bead(
            bead_id="B0002",
            title="Second",
            agent_type="developer",
            description="next",
            status=BEAD_READY,
        )
        app = build_tui_app(self.storage, refresh_seconds=60)

        class FakeOffset:
            def __init__(self, y: int) -> None:
                self.y = y

        class FakeClickEvent:
            def __init__(self, widget: object, y: int) -> None:
                self.widget = widget
                self._offset = FakeOffset(y)

            def get_content_offset(self, widget: object) -> FakeOffset:
                return self._offset

        class FakeScrollEvent:
            def __init__(self, widget: object) -> None:
                self.widget = widget
                self.stopped = False

            def stop(self) -> None:
                self.stopped = True

        async def exercise_app() -> tuple[str, str, int, int]:
            async with app.run_test() as pilot:
                await pilot.resize_terminal(80, 18)
                await pilot.pause()
                bead_tree = app.screen.query_one("#bead-tree")
                detail_widget = app.screen.query_one("#bead-detail")

                # Click on tree focuses the list panel; Tree handles selection natively
                app.on_click(FakeClickEvent(bead_tree, y=3))
                focus_after_list_click = app.runtime_state.focused_panel

                # Click on detail focuses the detail panel
                app.on_click(FakeClickEvent(detail_widget, y=2))
                focus_after_detail_click = app.runtime_state.focused_panel

                # Mouse scroll on detail scrolls the detail view
                detail_scroll = FakeScrollEvent(detail_widget)
                app.on_mouse_scroll_down(detail_scroll)
                detail_offset = app.runtime_state.detail_scroll_offset

                return (
                    focus_after_list_click,
                    focus_after_detail_click,
                    detail_offset,
                    -1 if app.runtime_state.selected_index is None else app.runtime_state.selected_index,
                )

        focus_after_list_click, focus_after_detail_click, detail_offset, selected_index = asyncio.run(exercise_app())

        self.assertEqual(PANEL_LIST, focus_after_list_click)
        self.assertEqual(PANEL_DETAIL, focus_after_detail_click)
        self.assertGreater(detail_offset, 0)
        self.assertEqual(0, selected_index)

    def test_mouse_panel_click_selection_resets_detail_scroll_and_routes_container_widgets(self) -> None:
        self.storage.create_bead(
            bead_id="B0001",
            title="Scrollable",
            agent_type="developer",
            description="scroll",
            status=BEAD_READY,
            acceptance_criteria=[f"criterion {index}" for index in range(80)],
        )
        self.storage.create_bead(
            bead_id="B0002",
            title="Second",
            agent_type="developer",
            description="next",
            status=BEAD_READY,
        )
        app = build_tui_app(self.storage, refresh_seconds=60)

        class FakeOffset:
            def __init__(self, y: int) -> None:
                self.y = y

        class FakeClickEvent:
            def __init__(self, widget: object, y: int) -> None:
                self.widget = widget
                self._offset = FakeOffset(y)

            def get_content_offset(self, widget: object) -> FakeOffset:
                return self._offset

        async def exercise_app() -> tuple[int, int, str, str]:
            async with app.run_test() as pilot:
                await pilot.resize_terminal(80, 18)
                await pilot.pause()
                detail_panel = app.screen.query_one("#detail-panel")

                # Scroll the detail panel while focused on it
                app.runtime_state.set_focused_panel(PANEL_DETAIL, announce=False)
                app.runtime_state.scroll_detail(4, app._detail_viewport_height())
                app._update_detail_panel()
                scrolled_offset = app.runtime_state.detail_scroll_offset

                # Switch to list panel and navigate down to select B0002
                # This should reset detail scroll because selection changes
                await pilot.press("shift+tab")
                await pilot.pause()
                await pilot.press("j")
                await pilot.pause()
                selected_after_nav = app.runtime_state.selected_bead_id or "-"
                offset_after_nav = app.runtime_state.detail_scroll_offset

                # Click detail panel to switch focus back
                app.on_click(FakeClickEvent(detail_panel, y=1))
                return (
                    scrolled_offset,
                    offset_after_nav,
                    selected_after_nav,
                    app.runtime_state.focused_panel,
                )

        scrolled_offset, offset_after_nav, selected_after_nav, focused_panel = asyncio.run(exercise_app())

        self.assertGreater(scrolled_offset, 0)
        self.assertEqual(0, offset_after_nav)
        self.assertEqual("B0002", selected_after_nav)
        self.assertEqual(PANEL_DETAIL, focused_panel)

    def test_focus_indicator_updates_panel_titles_for_keyboard_and_mouse_switches(self) -> None:
        self.storage.create_bead(
            bead_id="B0001",
            title="Scrollable",
            agent_type="developer",
            description="scroll",
            status=BEAD_READY,
            acceptance_criteria=[f"criterion {index}" for index in range(20)],
        )
        app = build_tui_app(self.storage, refresh_seconds=60)

        class FakeOffset:
            def __init__(self, y: int) -> None:
                self.y = y

        class FakeClickEvent:
            def __init__(self, widget: object, y: int) -> None:
                self.widget = widget
                self._offset = FakeOffset(y)

            def get_content_offset(self, widget: object) -> FakeOffset:
                return self._offset

        def title_text(value: object) -> str:
            text = value.plain if hasattr(value, "plain") else str(value)
            return text.replace("\\[", "[").replace("\\]", "]")

        async def exercise_app() -> tuple[object, object, object, object, object, object, str]:
            async with app.run_test() as pilot:
                await pilot.resize_terminal(80, 18)
                await pilot.pause()
                list_panel = app.screen.query_one("#list-panel")
                detail_panel = app.screen.query_one("#detail-panel")
                initial_titles = (title_text(list_panel.border_title), title_text(detail_panel.border_title))

                await pilot.press("tab")
                await pilot.pause()
                after_keyboard_titles = (title_text(list_panel.border_title), title_text(detail_panel.border_title))

                app.on_click(FakeClickEvent(list_panel, y=2))
                after_mouse_titles = (title_text(list_panel.border_title), title_text(detail_panel.border_title))

                return (
                    initial_titles[0],
                    initial_titles[1],
                    after_keyboard_titles[0],
                    after_keyboard_titles[1],
                    after_mouse_titles[0],
                    after_mouse_titles[1],
                    app.runtime_state.status_panel_text(),
                )

        initial_list, initial_detail, keyboard_list, keyboard_detail, mouse_list, mouse_detail, status_panel = asyncio.run(exercise_app())

        self.assertEqual("Beads [Default] [ACTIVE]", initial_list)
        self.assertEqual("Details [idle]", initial_detail)
        self.assertEqual("Beads [Default] [idle]", keyboard_list)
        self.assertEqual("Details [ACTIVE]", keyboard_detail)
        self.assertEqual("Beads [Default] [ACTIVE]", mouse_list)
        self.assertEqual("Details [idle]", mouse_detail)
        self.assertIn("focus=list", status_panel)

    def test_detail_scroll_reuses_rendered_content_during_keyboard_scroll(self) -> None:
        self.storage.create_bead(
            bead_id="B0001",
            title="Scrollable",
            agent_type="developer",
            description="scroll",
            status=BEAD_READY,
            acceptance_criteria=[f"criterion {index}" for index in range(80)],
        )
        app = build_tui_app(self.storage, refresh_seconds=60)

        async def exercise_app() -> tuple[int, int]:
            async with app.run_test() as pilot:
                await pilot.resize_terminal(80, 18)
                await pilot.pause()
                await pilot.press("tab")
                await pilot.pause()

                detail_body = app.screen.query_one("#detail-acceptance-body")
                original_update = detail_body.update
                detail_body.update = Mock(wraps=original_update)

                await pilot.press("j")
                await pilot.pause()
                await pilot.press("pagedown")
                await pilot.pause()
                return detail_body.update.call_count, app.runtime_state.detail_scroll_offset

        update_calls, detail_offset = asyncio.run(exercise_app())

        self.assertEqual(0, update_calls)
        self.assertGreater(detail_offset, 0)

    def test_detail_keyboard_scroll_moves_vertical_scroll_for_expanded_sections(self) -> None:
        self.storage.create_bead(
            bead_id="B0001",
            title="Scrollable",
            agent_type="developer",
            description="scroll",
            status=BEAD_READY,
            acceptance_criteria=[f"criterion {index}" for index in range(80)],
        )
        app = build_tui_app(self.storage, refresh_seconds=60)

        async def exercise_app() -> tuple[float, int, float]:
            from textual.containers import VerticalScroll

            async with app.run_test() as pilot:
                await pilot.resize_terminal(80, 18)
                await pilot.pause()
                await pilot.press("tab")
                await pilot.pause()
                await pilot.press("enter")
                await pilot.pause()

                detail_panel = app.screen.query_one("#detail-panel", VerticalScroll)
                await pilot.press("j")
                await pilot.pause()
                await pilot.press("pagedown")
                await pilot.pause()
                return detail_panel.scroll_y, app.runtime_state.detail_scroll_offset, detail_panel.max_scroll_y

        scroll_y, detail_offset, max_scroll_y = asyncio.run(exercise_app())

        self.assertGreater(max_scroll_y, 0)
        self.assertGreater(scroll_y, 0)
        self.assertGreater(detail_offset, 0)

    def test_detail_panel_uses_collapsible_sections_with_compact_defaults(self) -> None:
        bead = self.storage.create_bead(
            bead_id="B0001",
            title="Collapsible",
            agent_type="developer",
            description="detail",
            status=BEAD_READY,
            acceptance_criteria=["criterion 1", "criterion 2"],
            expected_files=["src/agent_takt/tui.py"],
        )
        bead.changed_files = ["tests/test_tui_app.py"]
        bead.handoff_summary = HandoffSummary(remaining="Need validation.")
        self.storage.save_bead(bead)
        app = build_tui_app(self.storage, refresh_seconds=60)

        async def exercise_app() -> tuple[bool, bool, bool, bool, bool, str]:
            from textual.widgets import Collapsible

            async with app.run_test() as pilot:
                await pilot.resize_terminal(80, 18)
                await pilot.pause()
                acceptance = app.screen.query_one("#detail-acceptance", Collapsible)
                files = app.screen.query_one("#detail-files", Collapsible)
                handoff = app.screen.query_one("#detail-handoff", Collapsible)
                initial = (acceptance.collapsed, files.collapsed, handoff.collapsed)

                await pilot.press("tab")
                await pilot.pause()
                await pilot.press("enter")
                await pilot.pause()
                await pilot.press("n")
                await pilot.pause()
                await pilot.press("enter")
                await pilot.pause()

                return (
                    initial[0],
                    initial[1],
                    initial[2],
                    acceptance.collapsed,
                    files.collapsed,
                    app.runtime_state.status_message,
                )

        initial_acceptance, initial_files, initial_handoff, acceptance_after_enter, files_after_nav, status = asyncio.run(
            exercise_app()
        )

        self.assertTrue(initial_acceptance)
        self.assertTrue(initial_files)
        self.assertTrue(initial_handoff)
        self.assertFalse(acceptance_after_enter)
        self.assertFalse(files_after_nav)
        self.assertEqual("Files expanded.", status)

    def test_detail_section_header_click_toggles_collapsible(self) -> None:
        bead = self.storage.create_bead(
            bead_id="B0001",
            title="Clickable",
            agent_type="developer",
            description="detail",
            status=BEAD_READY,
        )
        bead.handoff_summary = HandoffSummary(remaining="Need operator attention.")
        self.storage.save_bead(bead)
        app = build_tui_app(self.storage, refresh_seconds=60)

        async def exercise_app() -> tuple[bool, str]:
            from textual.widgets import Collapsible

            async with app.run_test() as pilot:
                await pilot.resize_terminal(100, 30)
                await pilot.pause()
                handoff = app.screen.query_one("#detail-handoff", Collapsible)
                title = next(child for child in handoff.children if hasattr(child, "_on_click"))
                await title._on_click(SimpleNamespace(stop=lambda: None))
                await pilot.pause()
                return handoff.collapsed, app.runtime_state.status_message

        collapsed, status = asyncio.run(exercise_app())

        self.assertFalse(collapsed)
        self.assertEqual("Handoff expanded.", status)

    def test_mouse_list_boundary_scroll_noop_preserves_detail_scroll(self) -> None:
        self.storage.create_bead(
            bead_id="B0001",
            title="Scrollable",
            agent_type="developer",
            description="scroll",
            status=BEAD_READY,
            acceptance_criteria=[f"criterion {index}" for index in range(80)],
        )
        self.storage.create_bead(
            bead_id="B0002",
            title="Second",
            agent_type="developer",
            description="next",
            status=BEAD_READY,
        )
        app = build_tui_app(self.storage, refresh_seconds=60)

        class FakeScrollEvent:
            def __init__(self, widget: object) -> None:
                self.widget = widget
                self.stopped = False

            def stop(self) -> None:
                self.stopped = True

        async def exercise_app() -> tuple[int, int, str, int, str, bool]:
            async with app.run_test() as pilot:
                await pilot.resize_terminal(80, 18)
                await pilot.pause()
                list_widget = app.screen.query_one("#bead-tree")
                detail_widget = app.screen.query_one("#bead-detail")

                detail_scroll = FakeScrollEvent(detail_widget)
                app.on_mouse_scroll_down(detail_scroll)
                detail_offset = app.runtime_state.detail_scroll_offset

                list_scroll = FakeScrollEvent(list_widget)
                app.on_mouse_scroll_up(list_scroll)
                return (
                    detail_offset,
                    app.runtime_state.detail_scroll_offset,
                    app.runtime_state.selected_bead_id or "-",
                    -1 if app.runtime_state.selected_index is None else app.runtime_state.selected_index,
                    app.runtime_state.status_message,
                    list_scroll.stopped,
                )

        detail_offset, offset_after_noop, selected_bead_id, selected_index, status_message, stopped = asyncio.run(exercise_app())

        self.assertGreater(detail_offset, 0)
        self.assertEqual(detail_offset, offset_after_noop)
        self.assertEqual("B0001", selected_bead_id)
        self.assertEqual(0, selected_index)
        # With the Tree widget, boundary scroll is handled silently by the tree
        self.assertIsInstance(status_message, str)

    def test_app_status_update_flow_uses_keyboard_confirmation(self) -> None:
        bead = self.storage.create_bead(
            bead_id="B0001",
            title="Ready",
            agent_type="documentation",
            description="ready",
            status=BEAD_READY,
        )
        app = build_tui_app(self.storage, refresh_seconds=60)

        async def exercise_app() -> tuple[str, str]:
            async with app.run_test() as pilot:
                await pilot.pause()
                await pilot.press("u")
                await pilot.press("d")
                await pilot.press("y")
                await pilot.pause()
                bead_after = self.storage.load_bead(bead.bead_id)
                return app.runtime_state.status_message, bead_after.status

        status_message, bead_status = asyncio.run(exercise_app())

        self.assertEqual(BEAD_DONE, bead_status)
        self.assertIn(f"Updated {bead.bead_id} to {BEAD_DONE}.", status_message)

    def test_app_retry_flow_uses_keyboard_confirmation(self) -> None:
        bead = self.storage.create_bead(
            bead_id="B0001",
            title="Blocked",
            agent_type="developer",
            description="blocked",
            status=BEAD_BLOCKED,
        )
        app = build_tui_app(self.storage, refresh_seconds=60)

        async def exercise_app() -> tuple[str, str]:
            async with app.run_test() as pilot:
                await pilot.pause()
                await pilot.press("t")
                await pilot.press("y")
                await pilot.pause()
                bead_after = self.storage.load_bead(bead.bead_id)
                return app.runtime_state.status_message, bead_after.status

        status_message, bead_status = asyncio.run(exercise_app())

        self.assertEqual(BEAD_READY, bead_status)
        self.assertIn(f"Retried {bead.bead_id}.", status_message)

    def test_app_status_update_flow_uses_refresh_keybinding_for_ready_target(self) -> None:
        bead = self.storage.create_bead(
            bead_id="B0001",
            title="Blocked",
            agent_type="developer",
            description="blocked",
            status=BEAD_BLOCKED,
        )
        app = build_tui_app(self.storage, refresh_seconds=60)

        async def exercise_app() -> tuple[str, str]:
            async with app.run_test() as pilot:
                await pilot.pause()
                await pilot.press("u")
                await pilot.press("r")
                await pilot.press("y")
                await pilot.pause()
                bead_after = self.storage.load_bead(bead.bead_id)
                return app.runtime_state.status_message, bead_after.status

        status_message, bead_status = asyncio.run(exercise_app())

        self.assertEqual(BEAD_READY, bead_status)
        self.assertIn(f"Updated {bead.bead_id} to {BEAD_READY}.", status_message)

    def test_interval_tick_dispatches_refresh_and_scheduler_by_runtime_mode(self) -> None:
        self.storage.create_bead(bead_id="B0001", title="Ready", agent_type="developer", description="ready", status=BEAD_READY)
        app = build_tui_app(self.storage, refresh_seconds=60)

        with patch.object(app.runtime_state, "refresh") as refresh_mock:
            with patch.object(app, "_start_scheduler_worker") as scheduler_mock:
                app._on_interval_tick()
                refresh_mock.assert_not_called()
                scheduler_mock.assert_not_called()

                app.runtime_state.toggle_timed_refresh()
                app._on_interval_tick()
                refresh_mock.assert_called_once_with()
                scheduler_mock.assert_not_called()

                refresh_mock.reset_mock()
                app.runtime_state.toggle_continuous_run()
                app._on_interval_tick()
                refresh_mock.assert_not_called()
                scheduler_mock.assert_called_once_with()

    def test_panel_updates_skip_redundant_rerenders_until_content_changes(self) -> None:
        self.storage.create_bead(bead_id="B0001", title="Ready", agent_type="developer", description="ready", status=BEAD_READY)
        app = build_tui_app(self.storage, refresh_seconds=60)

        async def exercise_app() -> tuple[int, int, int, int]:
            async with app.run_test() as pilot:
                await pilot.pause()
                bead_list = app.screen.query_one("#bead-tree")
                bead_detail = app.screen.query_one("#detail-summary")
                status_panel = app.screen.query_one("#status-bar")

                app._update_list_panel()
                app._update_detail_panel()
                app._update_status_panel()

                with patch.object(bead_list, "clear") as list_update:
                    app._update_list_panel()
                with patch.object(bead_detail, "update") as detail_update:
                    app._update_detail_panel()
                with patch.object(status_panel, "update") as status_update:
                    app._update_status_panel()

                app.runtime_state.status_message = "Changed status."
                with patch.object(status_panel, "update") as changed_status_update:
                    app._update_status_panel()

                with patch.object(bead_detail, "update") as forced_detail_update:
                    app._update_detail_panel(force=True)

                return (
                    list_update.call_count,
                    detail_update.call_count,
                    status_update.call_count,
                    changed_status_update.call_count + forced_detail_update.call_count,
                )

        list_calls, detail_calls, status_calls, changed_calls = asyncio.run(exercise_app())

        self.assertEqual(0, list_calls)
        self.assertEqual(0, detail_calls)
        self.assertEqual(0, status_calls)
        self.assertEqual(2, changed_calls)

    def test_build_parser_wires_tui_command_and_run_tui_reports_dependency_hint(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["tui", "--feature-root", "B0002", "--refresh-seconds", "7"])
        self.assertEqual("tui", args.command)
        self.assertEqual("B0002", args.feature_root)
        self.assertEqual(7, args.refresh_seconds)

        stream = io.StringIO()
        with patch("agent_takt.tui.app.load_textual_runtime", side_effect=RuntimeError("textual missing")):
            exit_code = run_tui(self.storage, stream=stream)

        self.assertEqual(1, exit_code)
        self.assertIn("textual missing", stream.getvalue())
        self.assertIn("Hint: install project dependencies so `textual` is available.", stream.getvalue())

    def test_list_panel_has_vertical_scrollbar_via_overflow_auto(self) -> None:
        self.storage.create_bead(bead_id="B0001", title="Open", agent_type="developer", description="open", status=BEAD_OPEN)
        app = build_tui_app(self.storage, refresh_seconds=60)

        async def exercise_app() -> tuple[str, str]:
            async with app.run_test() as pilot:
                await pilot.pause()
                list_panel = app.screen.query_one("#list-panel")
                detail_panel = app.screen.query_one("#detail-panel")
                return (
                    str(list_panel.styles.overflow_y),
                    str(detail_panel.styles.overflow_y),
                )

        list_overflow, detail_overflow = asyncio.run(exercise_app())
        self.assertEqual("auto", list_overflow)
        self.assertEqual("auto", detail_overflow)

    def test_tree_widget_hides_root_node_and_shows_beads_as_top_level(self) -> None:
        self.storage.create_bead(bead_id="B0001", title="Alpha", agent_type="developer", description="alpha", status=BEAD_READY)
        self.storage.create_bead(
            bead_id="B0001-1", title="Child", agent_type="developer", description="child",
            parent_id="B0001", dependencies=["B0001"], status=BEAD_OPEN,
        )
        app = build_tui_app(self.storage, refresh_seconds=60)

        async def exercise_app() -> tuple[bool, int, str]:
            async with app.run_test() as pilot:
                await pilot.resize_terminal(80, 18)
                await pilot.pause()
                bead_tree = app.screen.query_one("#bead-tree")
                return (
                    bead_tree.show_root,
                    len(list(bead_tree.root.children)),
                    bead_tree.root.children[0].label.plain if bead_tree.root.children else "-",
                )

        show_root, child_count, first_label = asyncio.run(exercise_app())

        self.assertFalse(show_root)
        self.assertEqual(1, child_count)
        self.assertIn("B0001", first_label)

    def test_enter_key_in_list_panel_delegates_to_tree_not_merge(self) -> None:
        root = self.storage.create_bead(bead_id="B0001", title="Root", agent_type="developer", description="root", status=BEAD_READY)
        self.storage.create_bead(
            bead_id="B0001-1", title="Child", agent_type="developer", description="child",
            parent_id="B0001", dependencies=["B0001"], status=BEAD_OPEN,
        )
        app = build_tui_app(self.storage, refresh_seconds=60)

        async def exercise_app() -> tuple[bool, str]:
            async with app.run_test() as pilot:
                await pilot.resize_terminal(80, 18)
                await pilot.pause()

                # Press Enter on list panel — should delegate to tree, not trigger merge
                await pilot.press("enter")
                await pilot.pause()

                return (
                    app.runtime_state.awaiting_merge_confirmation,
                    app.runtime_state.status_message,
                )

        awaiting_merge, status = asyncio.run(exercise_app())

        # Enter in list panel should NOT have triggered merge flow
        self.assertFalse(awaiting_merge)
        self.assertNotIn("Confirm merge", status)

    def test_enter_key_blocked_during_help_overlay_in_list_panel(self) -> None:
        self.storage.create_bead(bead_id="B0001", title="Root", agent_type="developer", description="root", status=BEAD_READY)
        self.storage.create_bead(
            bead_id="B0001-1", title="Child", agent_type="developer", description="child",
            parent_id="B0001", dependencies=["B0001"], status=BEAD_OPEN,
        )
        app = build_tui_app(self.storage, refresh_seconds=60)

        async def exercise_app() -> tuple[str, str]:
            async with app.run_test() as pilot:
                await pilot.resize_terminal(80, 18)
                await pilot.pause()

                await pilot.press("?")
                await pilot.pause()
                overlay_status = app.runtime_state.status_message

                # Press enter while help overlay is open
                await pilot.press("enter")
                await pilot.pause()
                after_enter_status = app.runtime_state.status_message

                return overlay_status, after_enter_status

        overlay_status, after_enter_status = asyncio.run(exercise_app())

        self.assertIn("Help overlay open", overlay_status)
        # Enter should have been blocked — status unchanged
        self.assertIn("Help overlay open", after_enter_status)

    def test_list_panel_cache_detects_status_change_and_triggers_rebuild(self) -> None:
        bead = self.storage.create_bead(bead_id="B0001", title="Ready", agent_type="developer", description="ready", status=BEAD_READY)
        app = build_tui_app(self.storage, refresh_seconds=60)

        async def exercise_app() -> tuple[int, int]:
            async with app.run_test() as pilot:
                await pilot.resize_terminal(80, 18)
                await pilot.pause()

                # First call should be a no-op (already rendered)
                bead_tree = app.screen.query_one("#bead-tree")
                with patch.object(bead_tree, "clear") as clear_mock:
                    app._update_list_panel()
                    first_calls = clear_mock.call_count

                # Change the bead status and refresh
                bead.status = BEAD_BLOCKED
                self.storage.save_bead(bead)
                app.runtime_state.refresh()
                # Now update should rebuild
                with patch.object(app, "_populate_bead_tree") as populate_mock:
                    app._update_list_panel()
                    second_calls = populate_mock.call_count

                return first_calls, second_calls

        no_change_calls, after_change_calls = asyncio.run(exercise_app())

        self.assertEqual(0, no_change_calls)
        self.assertEqual(1, after_change_calls)

    def test_tree_node_collapse_expand_event_handlers_update_tracked_set(self) -> None:
        from textual.widgets import Tree as TextualTree

        self.storage.create_bead(bead_id="B0001", title="Root", agent_type="developer", description="root", status=BEAD_READY)
        self.storage.create_bead(
            bead_id="B0001-1", title="Child", agent_type="developer", description="child",
            parent_id="B0001", dependencies=["B0001"], status=BEAD_OPEN,
        )
        app = build_tui_app(self.storage, refresh_seconds=60)

        async def exercise_app() -> tuple[bool, bool, bool]:
            async with app.run_test() as pilot:
                await pilot.resize_terminal(80, 18)
                await pilot.pause()
                bead_tree = app.screen.query_one("#bead-tree")
                root_node = bead_tree.root.children[0]

                # Directly simulate collapse event
                root_node.collapse()
                await pilot.pause()
                after_collapse = "B0001" in app._collapsed_bead_ids

                # Directly simulate expand event
                root_node.expand()
                await pilot.pause()
                after_expand = "B0001" in app._collapsed_bead_ids

                # Verify tree has children
                has_children = len(list(root_node.children)) > 0

                return after_collapse, after_expand, has_children

        after_collapse, after_expand, has_children = asyncio.run(exercise_app())

        self.assertTrue(after_collapse)
        self.assertFalse(after_expand)
        self.assertTrue(has_children)

    # ── Toggle-all expand/collapse tests (B0137) ──────────────

    def test_toggle_all_expands_when_any_collapsed(self) -> None:
        """Toggle-all should clear collapsed set when some expandable nodes are collapsed."""
        self.storage.create_bead(
            bead_id="B0001", title="Parent A", agent_type="developer",
            description="a", status=BEAD_READY,
        )
        self.storage.create_bead(
            bead_id="B0001-1", title="Child A1", agent_type="developer",
            description="c1", parent_id="B0001", dependencies=["B0001"],
            status=BEAD_OPEN,
        )
        self.storage.create_bead(
            bead_id="B0002", title="Parent B", agent_type="developer",
            description="b", status=BEAD_READY,
        )
        self.storage.create_bead(
            bead_id="B0002-1", title="Child B1", agent_type="developer",
            description="c2", parent_id="B0002", dependencies=["B0002"],
            status=BEAD_OPEN,
        )
        app = build_tui_app(self.storage, refresh_seconds=60)

        async def exercise_app() -> tuple[set, set]:
            async with app.run_test() as pilot:
                await pilot.resize_terminal(120, 24)
                await pilot.pause()

                # Set a known collapsed state
                app._collapsed_bead_ids = {"B0001", "B0002"}

                # Mock _populate_bead_tree to prevent event-driven side effects
                with patch.object(type(app), "_populate_bead_tree", lambda self: None):
                    before_toggle = set(app._collapsed_bead_ids)
                    app.action_toggle_all_tree_nodes()
                    after_toggle = set(app._collapsed_bead_ids)

                return before_toggle, after_toggle

        before, after = asyncio.run(exercise_app())
        self.assertEqual({"B0001", "B0002"}, before)
        self.assertEqual(set(), after)

    def test_toggle_all_collapses_when_all_expanded(self) -> None:
        """Toggle-all should collapse all expandable nodes when none are collapsed."""
        self.storage.create_bead(
            bead_id="B0001", title="Parent A", agent_type="developer",
            description="a", status=BEAD_READY,
        )
        self.storage.create_bead(
            bead_id="B0001-1", title="Child A1", agent_type="developer",
            description="c1", parent_id="B0001", dependencies=["B0001"],
            status=BEAD_OPEN,
        )
        self.storage.create_bead(
            bead_id="B0002", title="Parent B", agent_type="developer",
            description="b", status=BEAD_READY,
        )
        self.storage.create_bead(
            bead_id="B0002-1", title="Child B1", agent_type="developer",
            description="c2", parent_id="B0002", dependencies=["B0002"],
            status=BEAD_OPEN,
        )
        app = build_tui_app(self.storage, refresh_seconds=60)

        async def exercise_app() -> tuple[set, set]:
            async with app.run_test() as pilot:
                await pilot.resize_terminal(120, 24)
                await pilot.pause()

                # Start from all-expanded state
                app._collapsed_bead_ids.clear()

                with patch.object(type(app), "_populate_bead_tree", lambda self: None):
                    before_toggle = set(app._collapsed_bead_ids)
                    app.action_toggle_all_tree_nodes()
                    after_toggle = set(app._collapsed_bead_ids)

                return before_toggle, after_toggle

        before, after = asyncio.run(exercise_app())
        self.assertEqual(set(), before)
        self.assertIn("B0001", after)
        self.assertIn("B0002", after)

    def test_toggle_all_double_press_roundtrips(self) -> None:
        """Two consecutive toggles should roundtrip: collapse then expand."""
        self.storage.create_bead(
            bead_id="B0001", title="Parent", agent_type="developer",
            description="a", status=BEAD_READY,
        )
        self.storage.create_bead(
            bead_id="B0001-1", title="Child", agent_type="developer",
            description="c", parent_id="B0001", dependencies=["B0001"],
            status=BEAD_OPEN,
        )
        app = build_tui_app(self.storage, refresh_seconds=60)

        async def exercise_app() -> tuple[set, set]:
            async with app.run_test() as pilot:
                await pilot.resize_terminal(120, 24)
                await pilot.pause()

                # Start from all-expanded state
                app._collapsed_bead_ids.clear()

                with patch.object(type(app), "_populate_bead_tree", lambda self: None):
                    # First toggle: collapse all
                    app.action_toggle_all_tree_nodes()
                    after_first = set(app._collapsed_bead_ids)

                    # Second toggle: expand all
                    app.action_toggle_all_tree_nodes()
                    after_second = set(app._collapsed_bead_ids)

                return after_first, after_second

        after_first, after_second = asyncio.run(exercise_app())
        self.assertEqual({"B0001"}, after_first)
        self.assertEqual(set(), after_second)

    def test_toggle_all_noop_when_no_expandable_nodes(self) -> None:
        """Toggle-all should be a no-op when there are no parent nodes."""
        self.storage.create_bead(
            bead_id="B0001", title="Leaf A", agent_type="developer",
            description="a", status=BEAD_READY,
        )
        self.storage.create_bead(
            bead_id="B0002", title="Leaf B", agent_type="developer",
            description="b", status=BEAD_READY,
        )
        app = build_tui_app(self.storage, refresh_seconds=60)

        async def exercise_app() -> set:
            async with app.run_test() as pilot:
                await pilot.resize_terminal(120, 24)
                await pilot.pause()

                app.action_toggle_all_tree_nodes()
                await pilot.pause()
                return set(app._collapsed_bead_ids)

        result = asyncio.run(exercise_app())
        self.assertEqual(set(), result)

    def test_toggle_all_only_affects_expandable_nodes(self) -> None:
        """Collapse-all should only track IDs that have children, not leaves."""
        self.storage.create_bead(
            bead_id="B0001", title="Parent", agent_type="developer",
            description="a", status=BEAD_READY,
        )
        self.storage.create_bead(
            bead_id="B0001-1", title="Child", agent_type="developer",
            description="c", parent_id="B0001", dependencies=["B0001"],
            status=BEAD_OPEN,
        )
        self.storage.create_bead(
            bead_id="B0002", title="Standalone leaf", agent_type="developer",
            description="b", status=BEAD_READY,
        )
        app = build_tui_app(self.storage, refresh_seconds=60)

        async def exercise_app() -> set:
            async with app.run_test() as pilot:
                await pilot.resize_terminal(120, 24)
                await pilot.pause()

                # Collapse all
                app.action_toggle_all_tree_nodes()
                await pilot.pause()
                return set(app._collapsed_bead_ids)

        collapsed = asyncio.run(exercise_app())
        self.assertEqual({"B0001"}, collapsed)
        self.assertNotIn("B0002", collapsed)

    def test_help_overlay_includes_toggle_all_shortcut(self) -> None:
        """The help overlay should document the E keybinding."""
        overlay = format_help_overlay()
        self.assertIn("E           Expand/collapse all tree nodes", overlay)

    def test_filter_cycling_clears_collapsed_state(self) -> None:
        """Cycling the filter mode should reset collapsed bead IDs."""
        self.storage.create_bead(
            bead_id="B0001", title="Parent", agent_type="developer",
            description="a", status=BEAD_READY,
        )
        self.storage.create_bead(
            bead_id="B0001-1", title="Child", agent_type="developer",
            description="c", parent_id="B0001", dependencies=["B0001"],
            status=BEAD_OPEN,
        )
        app = build_tui_app(self.storage, refresh_seconds=60)

        async def exercise_app() -> tuple[set, set]:
            async with app.run_test() as pilot:
                await pilot.resize_terminal(120, 24)
                await pilot.pause()

                # Collapse all
                app.action_toggle_all_tree_nodes()
                await pilot.pause()
                after_collapse = set(app._collapsed_bead_ids)

                # Cycle filter — should clear collapsed state
                app.action_filter_next()
                await pilot.pause()
                after_filter = set(app._collapsed_bead_ids)

                return after_collapse, after_filter

        after_collapse, after_filter = asyncio.run(exercise_app())
        self.assertIn("B0001", after_collapse)
        self.assertEqual(set(), after_filter)

    def test_binding_E_exists_for_toggle_all(self) -> None:
        """The TUI app should have a binding for 'E' mapped to toggle_all_tree_nodes."""
        app = build_tui_app(self.storage, refresh_seconds=60)

        async def exercise_app() -> bool:
            async with app.run_test() as pilot:
                await pilot.resize_terminal(80, 18)
                await pilot.pause()
                # Check that the action method exists on the app
                return hasattr(app, "action_toggle_all_tree_nodes") and callable(app.action_toggle_all_tree_nodes)

        has_action = asyncio.run(exercise_app())
        self.assertTrue(has_action)

    # ── B0139 keyboard shortcut remap tests ────────────────────

    def test_g_key_jumps_to_first_bead_in_list_panel(self) -> None:
        """Pressing 'g' in the list panel should jump selection to the first bead."""
        self.storage.create_bead(bead_id="B0001", title="First", agent_type="developer", description="a", status=BEAD_READY)
        self.storage.create_bead(bead_id="B0002", title="Second", agent_type="developer", description="b", status=BEAD_READY)
        self.storage.create_bead(bead_id="B0003", title="Third", agent_type="developer", description="c", status=BEAD_READY)
        app = build_tui_app(self.storage, refresh_seconds=60)

        async def exercise_app() -> tuple[str, str]:
            async with app.run_test() as pilot:
                await pilot.resize_terminal(80, 24)
                await pilot.pause()
                # Navigate down to last bead
                await pilot.press("j")
                await pilot.pause()
                await pilot.press("j")
                await pilot.pause()
                after_down = app.runtime_state.selected_bead_id or "-"
                # Press 'g' to jump to first
                await pilot.press("g")
                await pilot.pause()
                return after_down, app.runtime_state.selected_bead_id or "-"

        after_down, after_g = asyncio.run(exercise_app())
        self.assertEqual("B0003", after_down)
        self.assertEqual("B0001", after_g)

    def test_G_key_jumps_to_last_bead_in_list_panel(self) -> None:
        """Pressing 'G' in the list panel should jump selection to the last bead."""
        self.storage.create_bead(bead_id="B0001", title="First", agent_type="developer", description="a", status=BEAD_READY)
        self.storage.create_bead(bead_id="B0002", title="Second", agent_type="developer", description="b", status=BEAD_READY)
        self.storage.create_bead(bead_id="B0003", title="Third", agent_type="developer", description="c", status=BEAD_READY)
        app = build_tui_app(self.storage, refresh_seconds=60)

        async def exercise_app() -> str:
            async with app.run_test() as pilot:
                await pilot.resize_terminal(80, 24)
                await pilot.pause()
                initial = app.runtime_state.selected_bead_id or "-"
                self.assertEqual("B0001", initial)
                # Press 'G' to jump to last
                await pilot.press("G")
                await pilot.pause()
                return app.runtime_state.selected_bead_id or "-"

        after_G = asyncio.run(exercise_app())
        self.assertEqual("B0003", after_G)

    def test_n_key_navigates_to_next_detail_section(self) -> None:
        """Pressing 'n' in the detail panel should navigate to the next section."""
        bead = self.storage.create_bead(
            bead_id="B0001", title="Sectioned", agent_type="developer",
            description="detail", status=BEAD_READY,
            acceptance_criteria=["criterion 1"],
            expected_files=["src/foo.py"],
        )
        bead.handoff_summary = HandoffSummary(remaining="Needs review.")
        self.storage.save_bead(bead)
        app = build_tui_app(self.storage, refresh_seconds=60)

        async def exercise_app() -> tuple[int, int]:
            async with app.run_test() as pilot:
                await pilot.resize_terminal(80, 18)
                await pilot.pause()
                await pilot.press("tab")
                await pilot.pause()
                initial_section = app._active_detail_section_index
                await pilot.press("n")
                await pilot.pause()
                return initial_section, app._active_detail_section_index

        initial, after_n = asyncio.run(exercise_app())
        self.assertEqual(0, initial)
        self.assertEqual(1, after_n)

    def test_N_key_navigates_to_previous_detail_section(self) -> None:
        """Pressing 'N' in the detail panel should navigate to the previous section."""
        bead = self.storage.create_bead(
            bead_id="B0001", title="Sectioned", agent_type="developer",
            description="detail", status=BEAD_READY,
            acceptance_criteria=["criterion 1"],
            expected_files=["src/foo.py"],
        )
        bead.handoff_summary = HandoffSummary(remaining="Needs review.")
        self.storage.save_bead(bead)
        app = build_tui_app(self.storage, refresh_seconds=60)

        async def exercise_app() -> tuple[int, int]:
            async with app.run_test() as pilot:
                await pilot.resize_terminal(80, 18)
                await pilot.pause()
                await pilot.press("tab")
                await pilot.pause()
                # Navigate forward twice
                await pilot.press("n")
                await pilot.pause()
                await pilot.press("n")
                await pilot.pause()
                after_forward = app._active_detail_section_index
                # Navigate back
                await pilot.press("N")
                await pilot.pause()
                return after_forward, app._active_detail_section_index

        after_forward, after_N = asyncio.run(exercise_app())
        self.assertEqual(2, after_forward)
        self.assertEqual(1, after_N)

    def test_c_key_cancels_pending_retry(self) -> None:
        """Pressing 'c' should cancel a pending retry action (replaces old 'n' binding)."""
        self.storage.create_bead(
            bead_id="B0001", title="Blocked", agent_type="developer",
            description="blocked", status=BEAD_BLOCKED,
        )
        app = build_tui_app(self.storage, refresh_seconds=60)

        async def exercise_app() -> tuple[str, str, str]:
            async with app.run_test() as pilot:
                await pilot.pause()
                # Start retry flow
                await pilot.press("t")
                await pilot.pause()
                pending_status = app.runtime_state.status_message
                # Cancel with 'c'
                await pilot.press("c")
                await pilot.pause()
                bead_after = self.storage.load_bead("B0001")
                return pending_status, app.runtime_state.status_message, bead_after.status

        pending, after_cancel, bead_status = asyncio.run(exercise_app())
        self.assertIn("Confirm retry", pending)
        self.assertIn("Cancelled", after_cancel)
        self.assertEqual(BEAD_BLOCKED, bead_status)

    def test_c_key_cancels_pending_status_update(self) -> None:
        """Pressing 'c' should cancel a pending status update action."""
        self.storage.create_bead(
            bead_id="B0001", title="Ready", agent_type="documentation",
            description="ready", status=BEAD_READY,
        )
        app = build_tui_app(self.storage, refresh_seconds=60)

        async def exercise_app() -> tuple[str, str, str]:
            async with app.run_test() as pilot:
                await pilot.pause()
                # Start status update flow
                await pilot.press("u")
                await pilot.press("d")
                await pilot.pause()
                pending_status = app.runtime_state.status_message
                # Cancel with 'c'
                await pilot.press("c")
                await pilot.pause()
                bead_after = self.storage.load_bead("B0001")
                return pending_status, app.runtime_state.status_message, bead_after.status

        pending, after_cancel, bead_status = asyncio.run(exercise_app())
        self.assertIn("done", pending.lower())
        self.assertIn("Cancelled", after_cancel)
        self.assertEqual(BEAD_READY, bead_status)

    def test_detail_panel_subtitle_shows_updated_keybinding_hints(self) -> None:
        """The detail panel subtitle should show 'n/N section' instead of old '[/] section'."""
        self.storage.create_bead(
            bead_id="B0001", title="Test", agent_type="developer",
            description="test", status=BEAD_READY,
        )
        app = build_tui_app(self.storage, refresh_seconds=60)

        async def exercise_app() -> str:
            async with app.run_test() as pilot:
                await pilot.resize_terminal(80, 18)
                await pilot.pause()
                await pilot.press("tab")
                await pilot.pause()
                detail_panel = app.screen.query_one("#detail-panel")
                subtitle = detail_panel.border_subtitle
                return subtitle.plain if hasattr(subtitle, "plain") else str(subtitle)

        subtitle = asyncio.run(exercise_app())
        self.assertIn("n/N section", subtitle)
        self.assertNotIn("[/]", subtitle)

    def test_bindings_include_g_G_n_N_c_keys(self) -> None:
        """The TUI app bindings should include g, G, n, N, and c keys."""
        app = build_tui_app(self.storage, refresh_seconds=60)

        async def exercise_app() -> dict:
            async with app.run_test() as pilot:
                await pilot.pause()
                bindings = {}
                for b in app.BINDINGS:
                    bindings[b.key] = b.action
                return bindings

        bindings = asyncio.run(exercise_app())
        self.assertEqual("go_home", bindings.get("g"))
        self.assertEqual("go_end", bindings.get("G"))
        self.assertEqual("next_detail_section", bindings.get("n"))
        self.assertEqual("previous_detail_section", bindings.get("N"))
        self.assertEqual("cancel_pending_action", bindings.get("c"))

    # ── Async scheduler worker tests (B0131) ──────────────────

    def test_app_start_scheduler_worker_guard_prevents_double_launch(self) -> None:
        """_start_scheduler_worker is a no-op when a worker is already running."""
        self.storage.create_bead(bead_id="B0001", title="Dev", agent_type="developer", description="d", status=BEAD_READY)
        app = build_tui_app(self.storage, refresh_seconds=60)

        app._scheduler_worker_running = True
        with patch.object(app, "run_worker") as run_worker_mock:
            app._start_scheduler_worker()
            run_worker_mock.assert_not_called()

        self.assertIn("already in progress", app.runtime_state.status_message)

    def test_app_start_scheduler_worker_does_not_prematurely_set_scheduler_running(self) -> None:
        """_start_scheduler_worker must NOT set runtime_state.scheduler_running=True.

        Regression test for B-79b03bf2: before the fix, _start_scheduler_worker set
        runtime_state.scheduler_running=True *before* calling run_scheduler_cycle(), which
        caused run_scheduler_cycle() to see the flag already set and return False immediately,
        so the cycle never actually executed.
        """
        self.storage.create_bead(bead_id="B0001", title="Dev", agent_type="developer", description="d", status=BEAD_READY)
        app = build_tui_app(self.storage, refresh_seconds=60)

        captured_scheduler_running: list[bool] = []

        def capture_and_skip(*args, **kwargs) -> None:
            # Capture runtime_state.scheduler_running at the moment run_worker is called.
            # If the bug were present, this would be True (set prematurely), preventing the
            # cycle from executing.
            captured_scheduler_running.append(app.runtime_state.scheduler_running)

        with patch.object(app, "run_worker", side_effect=capture_and_skip):
            app._start_scheduler_worker()

        self.assertEqual([False], captured_scheduler_running, (
            "runtime_state.scheduler_running must be False when run_worker is called; "
            "setting it to True here causes run_scheduler_cycle() to abort immediately."
        ))

    def test_app_on_scheduler_worker_done_resets_flags_and_rerenders(self) -> None:
        """_on_scheduler_worker_done clears both running flags and triggers re-render."""
        self.storage.create_bead(bead_id="B0001", title="Dev", agent_type="developer", description="d", status=BEAD_READY)
        app = build_tui_app(self.storage, refresh_seconds=60)

        app._scheduler_worker_running = True
        app.runtime_state.scheduler_running = True

        with patch.object(app, "_render_all") as render_mock:
            app._on_scheduler_worker_done()

        self.assertFalse(app._scheduler_worker_running)
        self.assertFalse(app.runtime_state.scheduler_running)
        render_mock.assert_called_once_with(force_detail=True)

    def test_app_action_scheduler_once_delegates_to_start_scheduler_worker(self) -> None:
        """Pressing 's' (action_scheduler_once) delegates to _start_scheduler_worker."""
        self.storage.create_bead(bead_id="B0001", title="Dev", agent_type="developer", description="d", status=BEAD_READY)
        app = build_tui_app(self.storage, refresh_seconds=60)

        with patch.object(app, "_start_scheduler_worker") as worker_mock:
            app.action_scheduler_once()
            worker_mock.assert_called_once_with()

    def test_command_tui_rejects_descendant_scope_before_launch(self) -> None:
        feature_root_id, _ = self._create_feature_tree()
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)

        with patch("agent_takt.tui.run_tui") as run_tui_mock:
            exit_code = command_tui(
                SimpleNamespace(feature_root=f"{feature_root_id}-1", refresh_seconds=3),
                self.storage,
                console,
            )

        self.assertEqual(1, exit_code)
        self.assertIn(f"{feature_root_id}-1 is not a valid feature root", stream.getvalue())
        run_tui_mock.assert_not_called()

class TuiLegacyTests(_OrchestratorBase):
    """Migrated TUI tests from test_orchestrator.py."""

    def test_tui_detail_panel_and_footer_include_handoff_scope_and_counts(self) -> None:
        bead = Bead(
            bead_id="B0099",
            title="Implement TUI",
            agent_type="developer",
            description="build helpers",
            status=BEAD_BLOCKED,
            parent_id="B0090",
            feature_root_id="B0030",
            dependencies=["B0098"],
            acceptance_criteria=["Build rows", "Format detail panel"],
            expected_files=["src/agent_takt/tui.py"],
            expected_globs=["tests/test_tui*.py"],
            touched_files=["src/agent_takt/tui.py"],
            changed_files=["src/agent_takt/tui.py", "tests/test_orchestrator.py"],
            updated_docs=["docs/tui.md"],
            block_reason="Waiting on review",
            conflict_risks="Coordinate with review bead on footer text.",
            handoff_summary=HandoffSummary(
                completed="Implemented the TUI helpers.",
                remaining="Need review signoff.",
                risks="Footer wording may change with runtime integration.",
                changed_files=["src/agent_takt/tui.py", "tests/test_orchestrator.py"],
                updated_docs=["docs/tui.md"],
                next_action="Run the review bead.",
                next_agent="review",
                block_reason="Waiting on review",
                expected_files=["src/agent_takt/tui.py"],
                expected_globs=["tests/test_tui*.py"],
                touched_files=["src/agent_takt/tui.py"],
                conflict_risks="Coordinate with review bead on footer text.",
            ),
        )

        detail = format_detail_panel(bead)
        footer = format_footer(
            [bead],
            filter_mode=FILTER_DEFAULT,
            selected_index=0,
            total_rows=1,
            continuous_run_enabled=False,
        )

        self.assertIn("Bead: B0099", detail)
        self.assertIn("Status: blocked", detail)
        self.assertIn("Parent: B0090", detail)
        self.assertIn("Feature Root: B0030", detail)
        self.assertIn("Dependencies: B0098", detail)
        self.assertIn("  - Build rows", detail)
        self.assertIn("  changed: src/agent_takt/tui.py, tests/test_orchestrator.py", detail)
        self.assertIn("  next_agent: review", detail)
        self.assertIn("  conflict_risks: Coordinate with review bead on footer text.", detail)
        self.assertEqual(
            "filter=default | run=manual | rows=1 | selected=1 | open=0 | ready=0 | in_progress=0 | blocked=1 | handed_off=0 | done=0",
            footer.removesuffix(" | ? help"),
        )
        self.assertTrue(footer.endswith(" | ? help"))

    def test_tui_detail_panel_handles_empty_selection_and_empty_scope_lists(self) -> None:
        self.assertEqual("No bead selected.", format_detail_panel(None))

        bead = Bead(
            bead_id="B0100",
            title="Empty detail state",
            agent_type="tester",
            description="verify formatter fallbacks",
        )

        detail = format_detail_panel(bead)

        self.assertIn("Dependencies: -", detail)
        self.assertIn("Acceptance Criteria:\n  -", detail)
        self.assertIn("Block Reason: -", detail)
        self.assertIn("  expected: -", detail)
        self.assertIn("  conflict_risks: -", detail)

    def test_run_tui_returns_nonzero_and_hint_when_textual_missing(self) -> None:
        stream = io.StringIO()

        with patch("agent_takt.tui.app.load_textual_runtime", side_effect=RuntimeError("missing textual")):
            exit_code = run_tui(self.storage, stream=stream)

        self.assertEqual(1, exit_code)
        self.assertIn("Hint: install project dependencies", stream.getvalue())

class TuiLiveStatusBarTests(unittest.TestCase):
    """Tests for _live_status_bar_text (B-790f671f)."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        source_templates = REPO_ROOT / "templates" / "agents"
        target_templates = self.root / "templates" / "agents"
        target_templates.mkdir(parents=True, exist_ok=True)
        for template_path in source_templates.glob("*.md"):
            import shutil
            shutil.copy2(template_path, target_templates / template_path.name)
        from agent_takt.storage import RepositoryStorage as _RS
        self.storage = _RS(self.root)
        self.storage.initialize()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _make_state(self, beads: list, *, continuous_run: bool = False, status_message: str = "") -> TuiRuntimeState:
        for bead in beads:
            self.storage.save_bead(bead)
        state = TuiRuntimeState(storage=self.storage)
        state.filter_mode = FILTER_ALL
        state.refresh()
        state.continuous_run_enabled = continuous_run
        state.status_message = status_message
        return state

    def test_live_status_bar_text_counts_by_status(self) -> None:
        from agent_takt.tui.app import _live_status_bar_text

        beads = [
            Bead(bead_id="B-sb-01", title="T1", agent_type="developer", description="d", status=BEAD_IN_PROGRESS),
            Bead(bead_id="B-sb-02", title="T2", agent_type="developer", description="d", status=BEAD_IN_PROGRESS),
            Bead(bead_id="B-sb-03", title="T3", agent_type="developer", description="d", status=BEAD_READY),
            Bead(bead_id="B-sb-04", title="T4", agent_type="developer", description="d", status=BEAD_BLOCKED),
        ]
        state = self._make_state(beads)
        result = _live_status_bar_text(state)

        self.assertIn("2 running", result)
        self.assertIn("1 ready", result)
        self.assertIn("1 blocked", result)

    def test_live_status_bar_text_shows_manual_mode_when_not_continuous(self) -> None:
        from agent_takt.tui.app import _live_status_bar_text

        state = self._make_state([])
        state.continuous_run_enabled = False
        result = _live_status_bar_text(state)

        self.assertIn("S:manual", result)

    def test_live_status_bar_text_shows_auto_mode_when_continuous(self) -> None:
        from agent_takt.tui.app import _live_status_bar_text

        state = self._make_state([], continuous_run=True)
        result = _live_status_bar_text(state)

        self.assertIn("S:auto", result)

    def test_live_status_bar_text_appends_status_message(self) -> None:
        from agent_takt.tui.app import _live_status_bar_text

        state = self._make_state([], status_message="Press q to quit.")
        result = _live_status_bar_text(state)

        self.assertIn("Press q to quit.", result)

    def test_live_status_bar_text_no_status_message_omits_separator(self) -> None:
        from agent_takt.tui.app import _live_status_bar_text

        state = self._make_state([])
        state.status_message = ""
        result = _live_status_bar_text(state)

        # Should not end with a trailing "| "
        self.assertFalse(result.endswith("| "), f"Unexpected trailing separator: {result!r}")

    def test_update_status_panel_uses_live_status_bar_text_not_status_panel_text(self) -> None:
        """Regression: _update_status_panel must call _live_status_bar_text, not status_panel_text."""
        import inspect
        import ast

        app_source_path = REPO_ROOT / "src" / "agent_takt" / "tui" / "app.py"
        source = app_source_path.read_text()

        # Find the _update_status_panel function body and verify it uses _live_status_bar_text,
        # not status_panel_text.
        self.assertIn("_live_status_bar_text", source)
        # Check that _update_status_panel does NOT call status_panel_text
        lines = source.splitlines()
        in_update_status = False
        uses_status_panel_text = False
        for line in lines:
            if "def _update_status_panel(" in line:
                in_update_status = True
            elif in_update_status and line.strip().startswith("def "):
                break
            elif in_update_status and "status_panel_text()" in line:
                uses_status_panel_text = True
        self.assertFalse(
            uses_status_panel_text,
            "_update_status_panel should not call status_panel_text()"
        )


class TuiSchedulerReporterDeferredTests(unittest.TestCase):
    """Tests for TuiSchedulerReporter deferred-cycle lifecycle (B-8150a4da)."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        source_templates = REPO_ROOT / "templates" / "agents"
        target_templates = self.root / "templates" / "agents"
        target_templates.mkdir(parents=True, exist_ok=True)
        for template_path in source_templates.glob("*.md"):
            import shutil
            shutil.copy2(template_path, target_templates / template_path.name)
        from agent_takt.storage import RepositoryStorage as _RS
        self.storage = _RS(self.root)
        self.storage.initialize()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _make_reporter(self, state: TuiRuntimeState) -> TuiSchedulerReporter:
        from unittest.mock import MagicMock
        app = MagicMock()
        # Prevent call_from_thread from raising
        app.call_from_thread.return_value = None
        return TuiSchedulerReporter(app, state)

    def _make_state(self) -> TuiRuntimeState:
        return TuiRuntimeState(storage=self.storage)

    def _make_bead(self, bead_id: str) -> Bead:
        return Bead(
            bead_id=bead_id,
            title=f"Task {bead_id}",
            agent_type="developer",
            description="d",
            status=BEAD_READY,
        )

    def test_deferred_this_cycle_empty_before_any_cycle(self) -> None:
        state = self._make_state()
        self.assertEqual(set(), state.deferred_this_cycle)

    def test_bead_deferred_adds_bead_id_to_deferred_this_cycle(self) -> None:
        state = self._make_state()
        reporter = self._make_reporter(state)

        # Fire the cycle header first (as happens in a real scheduler run)
        reporter.lease_expired("B-trigger-00")

        bead = self._make_bead("B-def-reporter-01")
        reporter.bead_deferred(bead, "dependency not met")

        self.assertIn("B-def-reporter-01", state.deferred_this_cycle)

    def test_multiple_deferrals_accumulate_in_deferred_this_cycle(self) -> None:
        state = self._make_state()
        reporter = self._make_reporter(state)

        # Fire cycle header first so subsequent bead_deferred calls don't clear state
        reporter.lease_expired("B-trigger-00")

        beads = [self._make_bead(f"B-def-reporter-0{i}") for i in range(1, 4)]
        for bead in beads:
            reporter.bead_deferred(bead, "not ready")

        for bead in beads:
            self.assertIn(bead.bead_id, state.deferred_this_cycle)

    def test_next_cycle_first_post_clears_deferred_this_cycle(self) -> None:
        state = self._make_state()

        # Cycle 1: log header then defer a bead
        reporter1 = self._make_reporter(state)
        reporter1.lease_expired("B-trigger-00")  # logs cycle header
        bead = self._make_bead("B-def-reporter-10")
        reporter1.bead_deferred(bead, "not ready")
        self.assertIn("B-def-reporter-10", state.deferred_this_cycle)

        # Cycle 2: fresh reporter; the first _post call clears deferred_this_cycle
        reporter2 = self._make_reporter(state)
        another_bead = self._make_bead("B-def-reporter-11")
        reporter2.bead_started(another_bead)  # first _post of new cycle → clears

        self.assertEqual(set(), state.deferred_this_cycle)

    def test_deferred_log_entry_wrapped_in_dim_markup(self) -> None:
        state = self._make_state()
        reporter = self._make_reporter(state)
        bead = self._make_bead("B-def-reporter-20")

        reporter.bead_deferred(bead, "dep not met")

        # The log should contain a dim-wrapped entry for the deferred event
        deferred_entries = [line for line in state.scheduler_log if "Deferred" in line]
        self.assertTrue(deferred_entries, "Expected at least one deferred log entry")
        self.assertTrue(
            any("[dim]" in entry for entry in deferred_entries),
            f"Expected [dim] markup in deferred log entries: {deferred_entries}"
        )

    def test_non_deferred_log_entries_not_wrapped_in_dim(self) -> None:
        state = self._make_state()
        reporter = self._make_reporter(state)
        bead = self._make_bead("B-def-reporter-30")

        reporter.bead_started(bead)

        started_entries = [line for line in state.scheduler_log if "Started" in line]
        self.assertTrue(started_entries, "Expected at least one started log entry")
        self.assertFalse(
            any("[dim]" in entry for entry in started_entries),
            f"Expected no [dim] markup in started log entries: {started_entries}"
        )

    def test_blocked_log_entry_not_wrapped_in_dim(self) -> None:
        state = self._make_state()
        reporter = self._make_reporter(state)
        bead = self._make_bead("B-def-reporter-40")

        reporter.bead_blocked(bead, "needs changes")

        blocked_entries = [line for line in state.scheduler_log if "Blocked" in line]
        self.assertTrue(blocked_entries, "Expected at least one blocked log entry")
        self.assertFalse(
            any("[dim]" in entry for entry in blocked_entries),
            f"Expected no [dim] markup in blocked log entries: {blocked_entries}"
        )

    def test_bead_deferred_as_first_call_in_cycle_preserves_bead_id(self) -> None:
        """bead_deferred() as the first reporter call in a cycle must not lose the
        bead ID due to _post()'s cycle-start clear running after the state write
        (ordering bug fixed in B-3e855a6c-corrective)."""
        state = self._make_state()
        reporter = self._make_reporter(state)

        # No preceding reporter call — bead_deferred IS the first event this cycle
        bead = self._make_bead("B-def-first-call-01")
        reporter.bead_deferred(bead, "dependency not done: B-xxx")

        self.assertIn("B-def-first-call-01", state.deferred_this_cycle)


if __name__ == "__main__":
    unittest.main()
