from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_takt.models import (
    BEAD_READY,
    Bead,
)
from agent_takt.storage import RepositoryStorage
from agent_takt.tui import (
    FILTER_ALL,
    PANEL_DETAIL,
    PANEL_LIST,
    PANEL_SCHEDULER_LOG,
    TuiRuntimeState,
    build_tui_app,
)

class TuiLayoutAndMaximizeTests(unittest.TestCase):
    """Tests for TUI three-panel layout, Tab/Shift+Tab focus cycling, and maximize behavior."""

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

    def _make_app(self):
        self.storage.create_bead(
            bead_id="B0001", title="Dev", agent_type="developer",
            description="d", status=BEAD_READY,
        )
        return build_tui_app(self.storage, refresh_seconds=60)

    # -- TuiRuntimeState unit tests (no Textual app required) -----------------

    def test_cycle_focus_forward_cycles_all_three_panels(self) -> None:
        """cycle_focus(1) should cycle list -> detail -> scheduler-log -> list."""
        state = TuiRuntimeState(storage=self.storage, filter_mode=FILTER_ALL)
        self.assertEqual(PANEL_LIST, state.focused_panel)
        state.cycle_focus(1)
        self.assertEqual(PANEL_DETAIL, state.focused_panel)
        state.cycle_focus(1)
        self.assertEqual(PANEL_SCHEDULER_LOG, state.focused_panel)
        state.cycle_focus(1)
        self.assertEqual(PANEL_LIST, state.focused_panel)

    def test_cycle_focus_backward_cycles_all_three_panels(self) -> None:
        """cycle_focus(-1) should cycle list -> scheduler-log -> detail -> list."""
        state = TuiRuntimeState(storage=self.storage, filter_mode=FILTER_ALL)
        self.assertEqual(PANEL_LIST, state.focused_panel)
        state.cycle_focus(-1)
        self.assertEqual(PANEL_SCHEDULER_LOG, state.focused_panel)
        state.cycle_focus(-1)
        self.assertEqual(PANEL_DETAIL, state.focused_panel)
        state.cycle_focus(-1)
        self.assertEqual(PANEL_LIST, state.focused_panel)

    def test_set_focused_panel_accepts_scheduler_log(self) -> None:
        """set_focused_panel should accept PANEL_SCHEDULER_LOG as a valid panel."""
        state = TuiRuntimeState(storage=self.storage, filter_mode=FILTER_ALL)
        state.set_focused_panel(PANEL_SCHEDULER_LOG)
        self.assertEqual(PANEL_SCHEDULER_LOG, state.focused_panel)

    def test_maximized_panel_defaults_to_none(self) -> None:
        """TuiRuntimeState.maximized_panel should default to None."""
        state = TuiRuntimeState(storage=self.storage, filter_mode=FILTER_ALL)
        self.assertIsNone(state.maximized_panel)

    def test_maximized_panel_field_is_settable(self) -> None:
        """TuiRuntimeState.maximized_panel should be assignable to a panel name or None."""
        state = TuiRuntimeState(storage=self.storage, filter_mode=FILTER_ALL)
        state.maximized_panel = PANEL_LIST
        self.assertEqual(PANEL_LIST, state.maximized_panel)
        state.maximized_panel = None
        self.assertIsNone(state.maximized_panel)

    # -- App integration tests ------------------------------------------------

    def test_compose_scheduler_log_is_child_of_main_row(self) -> None:
        """#scheduler-log must be a direct child of #main-row (three-panel peer)."""
        from textual.widgets import RichLog
        app = self._make_app()
        result = {}

        async def exercise_app() -> None:
            async with app.run_test() as pilot:
                await pilot.resize_terminal(120, 30)
                await pilot.pause()
                log_panel = app.query_one("#scheduler-log", RichLog)
                result["parent_id"] = log_panel.parent.id

        asyncio.run(exercise_app())
        self.assertEqual("main-row", result["parent_id"])

    def test_compose_status_bar_is_not_inside_main_row(self) -> None:
        """#status-bar must be a sibling of #main-row, not nested inside it."""
        from textual.widgets import Static
        app = self._make_app()
        result = {}

        async def exercise_app() -> None:
            async with app.run_test() as pilot:
                await pilot.resize_terminal(120, 30)
                await pilot.pause()
                status_bar = app.query_one("#status-bar", Static)
                result["parent_type"] = type(status_bar.parent).__name__

        asyncio.run(exercise_app())
        self.assertNotEqual("Horizontal", result["parent_type"])

    def test_tab_cycles_focus_through_all_three_panels(self) -> None:
        """Pressing Tab three times should cycle focus: list -> detail -> scheduler-log -> list."""
        app = self._make_app()
        panels: list[str] = []

        async def exercise_app() -> None:
            async with app.run_test() as pilot:
                await pilot.resize_terminal(120, 30)
                await pilot.pause()
                panels.append(app.runtime_state.focused_panel)
                await pilot.press("tab")
                await pilot.pause()
                panels.append(app.runtime_state.focused_panel)
                await pilot.press("tab")
                await pilot.pause()
                panels.append(app.runtime_state.focused_panel)
                await pilot.press("tab")
                await pilot.pause()
                panels.append(app.runtime_state.focused_panel)

        asyncio.run(exercise_app())
        self.assertEqual([PANEL_LIST, PANEL_DETAIL, PANEL_SCHEDULER_LOG, PANEL_LIST], panels)

    def test_shift_tab_cycles_focus_backward(self) -> None:
        """Pressing Shift+Tab should cycle focus backward: list -> scheduler-log -> detail."""
        app = self._make_app()
        panels: list[str] = []

        async def exercise_app() -> None:
            async with app.run_test() as pilot:
                await pilot.resize_terminal(120, 30)
                await pilot.pause()
                panels.append(app.runtime_state.focused_panel)
                await pilot.press("shift+tab")
                await pilot.pause()
                panels.append(app.runtime_state.focused_panel)
                await pilot.press("shift+tab")
                await pilot.pause()
                panels.append(app.runtime_state.focused_panel)

        asyncio.run(exercise_app())
        self.assertEqual([PANEL_LIST, PANEL_SCHEDULER_LOG, PANEL_DETAIL], panels)

    def test_maximize_list_panel_hides_others(self) -> None:
        """Pressing 'm' with list focused should give list .maximized and others .hidden."""
        app = self._make_app()
        result: dict = {}

        async def exercise_app() -> None:
            async with app.run_test() as pilot:
                await pilot.resize_terminal(120, 30)
                await pilot.pause()
                self.assertEqual(PANEL_LIST, app.runtime_state.focused_panel)
                await pilot.press("m")
                await pilot.pause()
                result["maximized"] = app.runtime_state.maximized_panel
                result["list_maximized"] = app.query_one("#list-panel").has_class("maximized")
                result["detail_hidden"] = app.query_one("#detail-panel").has_class("hidden")
                result["log_hidden"] = app.query_one("#scheduler-log").has_class("hidden")

        asyncio.run(exercise_app())
        self.assertEqual(PANEL_LIST, result["maximized"])
        self.assertTrue(result["list_maximized"])
        self.assertTrue(result["detail_hidden"])
        self.assertTrue(result["log_hidden"])

    def test_maximize_detail_panel_hides_others(self) -> None:
        """Pressing Tab then 'm' should maximize the detail panel and hide list/log."""
        app = self._make_app()
        result: dict = {}

        async def exercise_app() -> None:
            async with app.run_test() as pilot:
                await pilot.resize_terminal(120, 30)
                await pilot.pause()
                await pilot.press("tab")  # list -> detail
                await pilot.pause()
                self.assertEqual(PANEL_DETAIL, app.runtime_state.focused_panel)
                await pilot.press("m")
                await pilot.pause()
                result["maximized"] = app.runtime_state.maximized_panel
                result["detail_maximized"] = app.query_one("#detail-panel").has_class("maximized")
                result["list_hidden"] = app.query_one("#list-panel").has_class("hidden")
                result["log_hidden"] = app.query_one("#scheduler-log").has_class("hidden")

        asyncio.run(exercise_app())
        self.assertEqual(PANEL_DETAIL, result["maximized"])
        self.assertTrue(result["detail_maximized"])
        self.assertTrue(result["list_hidden"])
        self.assertTrue(result["log_hidden"])

    def test_maximize_scheduler_log_panel_hides_others(self) -> None:
        """Pressing Tab twice then 'm' should maximize scheduler-log and hide list/detail."""
        app = self._make_app()
        result: dict = {}

        async def exercise_app() -> None:
            async with app.run_test() as pilot:
                await pilot.resize_terminal(120, 30)
                await pilot.pause()
                await pilot.press("tab")  # list -> detail
                await pilot.press("tab")  # detail -> scheduler-log
                await pilot.pause()
                self.assertEqual(PANEL_SCHEDULER_LOG, app.runtime_state.focused_panel)
                await pilot.press("m")
                await pilot.pause()
                result["maximized"] = app.runtime_state.maximized_panel
                result["log_maximized"] = app.query_one("#scheduler-log").has_class("maximized")
                result["list_hidden"] = app.query_one("#list-panel").has_class("hidden")
                result["detail_hidden"] = app.query_one("#detail-panel").has_class("hidden")

        asyncio.run(exercise_app())
        self.assertEqual(PANEL_SCHEDULER_LOG, result["maximized"])
        self.assertTrue(result["log_maximized"])
        self.assertTrue(result["list_hidden"])
        self.assertTrue(result["detail_hidden"])

    def test_maximize_scheduler_log_also_hides_top_row(self) -> None:
        """Maximizing the scheduler-log panel should also hide the #top-row container so the log expands to fill screen."""
        app = self._make_app()
        result: dict = {}

        async def exercise_app() -> None:
            async with app.run_test() as pilot:
                await pilot.resize_terminal(120, 30)
                await pilot.pause()
                await pilot.press("tab")  # list -> detail
                await pilot.press("tab")  # detail -> scheduler-log
                await pilot.pause()
                self.assertEqual(PANEL_SCHEDULER_LOG, app.runtime_state.focused_panel)
                await pilot.press("m")
                await pilot.pause()
                result["top_row_hidden"] = app.query_one("#top-row").has_class("hidden")

        asyncio.run(exercise_app())
        self.assertTrue(result["top_row_hidden"])

    def test_maximize_list_panel_does_not_hide_top_row(self) -> None:
        """Maximizing the list panel should NOT hide #top-row (only scheduler-log maximize hides it)."""
        app = self._make_app()
        result: dict = {}

        async def exercise_app() -> None:
            async with app.run_test() as pilot:
                await pilot.resize_terminal(120, 30)
                await pilot.pause()
                self.assertEqual(PANEL_LIST, app.runtime_state.focused_panel)
                await pilot.press("m")
                await pilot.pause()
                result["top_row_hidden"] = app.query_one("#top-row").has_class("hidden")

        asyncio.run(exercise_app())
        self.assertFalse(result["top_row_hidden"])

    def test_maximize_detail_panel_does_not_hide_top_row(self) -> None:
        """Maximizing the detail panel should NOT hide #top-row (only scheduler-log maximize hides it)."""
        app = self._make_app()
        result: dict = {}

        async def exercise_app() -> None:
            async with app.run_test() as pilot:
                await pilot.resize_terminal(120, 30)
                await pilot.pause()
                await pilot.press("tab")  # list -> detail
                await pilot.pause()
                self.assertEqual(PANEL_DETAIL, app.runtime_state.focused_panel)
                await pilot.press("m")
                await pilot.pause()
                result["top_row_hidden"] = app.query_one("#top-row").has_class("hidden")

        asyncio.run(exercise_app())
        self.assertFalse(result["top_row_hidden"])

    def test_restore_from_scheduler_log_maximize_shows_top_row(self) -> None:
        """Pressing 'm' again after scheduler-log maximize should restore top-row visibility."""
        app = self._make_app()
        result: dict = {}

        async def exercise_app() -> None:
            async with app.run_test() as pilot:
                await pilot.resize_terminal(120, 30)
                await pilot.pause()
                await pilot.press("tab")  # list -> detail
                await pilot.press("tab")  # detail -> scheduler-log
                await pilot.pause()
                self.assertEqual(PANEL_SCHEDULER_LOG, app.runtime_state.focused_panel)
                await pilot.press("m")  # maximize scheduler-log
                await pilot.pause()
                await pilot.press("m")  # restore
                await pilot.pause()
                result["top_row_hidden"] = app.query_one("#top-row").has_class("hidden")
                result["maximized"] = app.runtime_state.maximized_panel

        asyncio.run(exercise_app())
        self.assertIsNone(result["maximized"])
        self.assertFalse(result["top_row_hidden"])

    def test_pressing_m_again_restores_three_panel_layout(self) -> None:
        """Pressing 'm' twice should toggle back to normal three-panel layout."""
        app = self._make_app()
        result: dict = {}

        async def exercise_app() -> None:
            async with app.run_test() as pilot:
                await pilot.resize_terminal(120, 30)
                await pilot.pause()
                await pilot.press("m")  # maximize list
                await pilot.pause()
                await pilot.press("m")  # restore
                await pilot.pause()
                result["maximized"] = app.runtime_state.maximized_panel
                list_panel = app.query_one("#list-panel")
                detail_panel = app.query_one("#detail-panel")
                log_panel = app.query_one("#scheduler-log")
                result["list_maximized"] = list_panel.has_class("maximized")
                result["list_hidden"] = list_panel.has_class("hidden")
                result["detail_maximized"] = detail_panel.has_class("maximized")
                result["detail_hidden"] = detail_panel.has_class("hidden")
                result["log_maximized"] = log_panel.has_class("maximized")
                result["log_hidden"] = log_panel.has_class("hidden")

        asyncio.run(exercise_app())
        self.assertIsNone(result["maximized"])
        self.assertFalse(result["list_maximized"])
        self.assertFalse(result["list_hidden"])
        self.assertFalse(result["detail_maximized"])
        self.assertFalse(result["detail_hidden"])
        self.assertFalse(result["log_maximized"])
        self.assertFalse(result["log_hidden"])

    def test_focus_unchanged_after_maximize_and_restore(self) -> None:
        """Toggling maximize should not change which panel is focused."""
        app = self._make_app()
        result: dict = {}

        async def exercise_app() -> None:
            async with app.run_test() as pilot:
                await pilot.resize_terminal(120, 30)
                await pilot.pause()
                await pilot.press("tab")  # -> detail
                await pilot.pause()
                result["before"] = app.runtime_state.focused_panel
                await pilot.press("m")
                await pilot.pause()
                result["after_maximize"] = app.runtime_state.focused_panel
                await pilot.press("m")
                await pilot.pause()
                result["after_restore"] = app.runtime_state.focused_panel

        asyncio.run(exercise_app())
        self.assertEqual(PANEL_DETAIL, result["before"])
        self.assertEqual(PANEL_DETAIL, result["after_maximize"])
        self.assertEqual(PANEL_DETAIL, result["after_restore"])

    def test_compose_main_row_is_vertical_container(self) -> None:
        """#main-row must be a Vertical container so top-row and scheduler-log stack vertically."""
        from textual.containers import Vertical
        app = self._make_app()
        result: dict = {}

        async def exercise_app() -> None:
            async with app.run_test() as pilot:
                await pilot.resize_terminal(120, 30)
                await pilot.pause()
                main_row = app.query_one("#main-row")
                result["type"] = type(main_row).__name__

        asyncio.run(exercise_app())
        self.assertEqual("Vertical", result["type"])

    def test_compose_top_row_is_horizontal_child_of_main_row(self) -> None:
        """#top-row must be a Horizontal container that is a direct child of #main-row."""
        from textual.containers import Horizontal
        app = self._make_app()
        result: dict = {}

        async def exercise_app() -> None:
            async with app.run_test() as pilot:
                await pilot.resize_terminal(120, 30)
                await pilot.pause()
                top_row = app.query_one("#top-row")
                result["type"] = type(top_row).__name__
                result["parent_id"] = top_row.parent.id

        asyncio.run(exercise_app())
        self.assertEqual("Horizontal", result["type"])
        self.assertEqual("main-row", result["parent_id"])

    def test_compose_list_and_detail_panels_are_inside_top_row(self) -> None:
        """#list-panel and #detail-panel must be children of #top-row, not directly of #main-row."""
        app = self._make_app()
        result: dict = {}

        async def exercise_app() -> None:
            async with app.run_test() as pilot:
                await pilot.resize_terminal(120, 30)
                await pilot.pause()
                list_panel = app.query_one("#list-panel")
                detail_panel = app.query_one("#detail-panel")
                result["list_parent"] = list_panel.parent.id
                result["detail_parent"] = detail_panel.parent.id

        asyncio.run(exercise_app())
        self.assertEqual("top-row", result["list_parent"])
        self.assertEqual("top-row", result["detail_parent"])

    def test_compose_scheduler_log_is_sibling_of_top_row_not_inside_it(self) -> None:
        """#scheduler-log must be a sibling of #top-row (both children of #main-row), not inside top-row."""
        from textual.widgets import RichLog
        app = self._make_app()
        result: dict = {}

        async def exercise_app() -> None:
            async with app.run_test() as pilot:
                await pilot.resize_terminal(120, 30)
                await pilot.pause()
                log_panel = app.query_one("#scheduler-log", RichLog)
                top_row = app.query_one("#top-row")
                result["log_parent_id"] = log_panel.parent.id
                result["top_row_parent_id"] = top_row.parent.id

        asyncio.run(exercise_app())
        # Both #scheduler-log and #top-row must share the same parent (#main-row)
        self.assertEqual("main-row", result["log_parent_id"])
        self.assertEqual("main-row", result["top_row_parent_id"])


if __name__ == '__main__':
    unittest.main()
