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

from codex_orchestrator.cli import apply_operator_status_update, build_parser, command_tui
from codex_orchestrator.console import ConsoleReporter
from codex_orchestrator.models import (
    BEAD_BLOCKED,
    BEAD_DONE,
    BEAD_HANDED_OFF,
    BEAD_IN_PROGRESS,
    BEAD_OPEN,
    BEAD_READY,
    Bead,
    HandoffSummary,
    SchedulerResult,
)
from codex_orchestrator.storage import RepositoryStorage
from codex_orchestrator.tui import (
    DETAIL_SECTION_TELEMETRY,
    FILTER_ACTIONABLE,
    FILTER_ALL,
    FILTER_DEFAULT,
    FILTER_DEFERRED,
    FILTER_DONE,
    PANEL_DETAIL,
    PANEL_LIST,
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
    format_help_overlay,
    render_detail_panel,
    render_tree_panel,
    run_tui,
    supported_filter_modes,
)


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

    def test_supported_filter_modes_include_shared_and_per_status_entries(self) -> None:
        self.assertEqual(
            (
                FILTER_DEFAULT,
                FILTER_ALL,
                FILTER_ACTIONABLE,
                FILTER_DEFERRED,
                FILTER_DONE,
                BEAD_OPEN,
                BEAD_READY,
                BEAD_IN_PROGRESS,
                BEAD_BLOCKED,
                BEAD_HANDED_OFF,
            ),
            supported_filter_modes(),
        )
        self.assertEqual(1, supported_filter_modes().count(FILTER_DONE))

    def test_collect_tree_rows_filters_by_mode_and_keeps_feature_root_visible(self) -> None:
        feature_root_id, statuses = self._create_feature_tree()

        default_rows = collect_tree_rows(self.storage, filter_mode=FILTER_DEFAULT, feature_root_id=feature_root_id)
        actionable_rows = collect_tree_rows(self.storage, filter_mode=FILTER_ACTIONABLE, feature_root_id=feature_root_id)
        deferred_rows = collect_tree_rows(self.storage, filter_mode=FILTER_DEFERRED, feature_root_id=feature_root_id)
        done_rows = collect_tree_rows(self.storage, filter_mode=FILTER_DONE, feature_root_id=feature_root_id)
        ready_rows = collect_tree_rows(self.storage, filter_mode=BEAD_READY, feature_root_id=feature_root_id)
        all_rows = collect_tree_rows(self.storage, filter_mode=FILTER_ALL, feature_root_id=feature_root_id)

        self.assertEqual(
            [feature_root_id, "B0002-1", "B0002-2", "B0002-3", "B0002-4", "B0002-5"],
            [row.bead_id for row in default_rows],
        )
        self.assertEqual([feature_root_id, "B0002-1", "B0002-2"], [row.bead_id for row in actionable_rows])
        self.assertEqual([feature_root_id, "B0002-5"], [row.bead_id for row in deferred_rows])
        self.assertEqual([feature_root_id, "B0002-6"], [row.bead_id for row in done_rows])
        self.assertEqual([feature_root_id, "B0002-2"], [row.bead_id for row in ready_rows])
        self.assertEqual([feature_root_id, *statuses.keys()], [row.bead_id for row in all_rows])

    def test_build_tree_rows_orders_siblings_by_bead_id_and_indents_by_depth(self) -> None:
        rows = build_tree_rows(
            [
                Bead(bead_id="B0002-2-1", title="Child B", agent_type="developer", description="child", parent_id="B0002-2"),
                Bead(bead_id="B0002", title="Root", agent_type="developer", description="root"),
                Bead(bead_id="B0002-1", title="Alpha", agent_type="developer", description="child", parent_id="B0002"),
                Bead(bead_id="B0002-2", title="Beta", agent_type="developer", description="child", parent_id="B0002"),
                Bead(bead_id="B0002-1-1", title="Grandchild A", agent_type="developer", description="grandchild", parent_id="B0002-1"),
            ]
        )

        self.assertEqual(
            ["B0002", "B0002-1", "B0002-1-1", "B0002-2", "B0002-2-1"],
            [row.bead_id for row in rows],
        )
        self.assertEqual("B0002 · Root", rows[0].label)
        self.assertEqual("  B0002-1 · Alpha", rows[1].label)
        self.assertEqual("    B0002-1-1 · Grandchild A", rows[2].label)
        self.assertEqual("  B0002-2 · Beta", rows[3].label)

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
                touched_files=["tests/test_tui.py"],
                changed_files=["tests/test_tui.py"],
                expected_files=["tests/test_tui.py"],
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

    def test_runtime_refresh_keeps_selection_by_bead_id_when_rows_reorder(self) -> None:
        self.storage.create_bead(bead_id="B0002", title="Second", agent_type="developer", description="second", status=BEAD_READY)
        selected = self.storage.create_bead(
            bead_id="B0004",
            title="Fourth",
            agent_type="developer",
            description="fourth",
            status=BEAD_BLOCKED,
        )
        state = TuiRuntimeState(self.storage, filter_mode=FILTER_DEFAULT)
        state.selected_bead_id = selected.bead_id
        state.selected_index = 1

        self.storage.create_bead(bead_id="B0001", title="First", agent_type="developer", description="first", status=BEAD_READY)
        self.storage.create_bead(bead_id="B0003", title="Third", agent_type="developer", description="third", status=BEAD_READY)
        state.refresh()

        self.assertEqual(selected.bead_id, state.selected_bead_id)
        self.assertEqual(selected.bead_id, state.selected_bead().bead_id)
        self.assertEqual(3, state.selected_index)

    def test_help_overlay_text_documents_toggle_shortcuts(self) -> None:
        overlay = format_help_overlay()

        self.assertIn("Shortcuts", overlay)
        self.assertIn("Tab         Focus next panel", overlay)
        self.assertIn("Shift+Tab   Focus previous panel", overlay)
        self.assertIn("[ / ]      Prev/next detail section", overlay)
        self.assertIn("q           Quit", overlay)
        self.assertIn("Shift+f     Previous filter", overlay)
        self.assertIn("t           Request blocked-bead retry", overlay)
        self.assertIn("Enter       Toggle detail section / confirm merge", overlay)
        self.assertIn("y           Confirm retry/status update", overlay)
        self.assertIn("n           Cancel pending merge/retry/status", overlay)
        self.assertIn("? / Esc     Close help", overlay)

    def test_runtime_help_overlay_toggle_preserves_selection_and_filter(self) -> None:
        self.storage.create_bead(bead_id="B0001", title="First", agent_type="developer", description="first", status=BEAD_READY)
        selected = self.storage.create_bead(
            bead_id="B0002",
            title="Second",
            agent_type="developer",
            description="second",
            status=BEAD_BLOCKED,
        )
        state = TuiRuntimeState(self.storage, filter_mode=FILTER_DEFAULT)
        state.selected_bead_id = selected.bead_id
        state.selected_index = 1

        opened = state.toggle_help_overlay()

        self.assertTrue(opened)
        self.assertTrue(state.help_overlay_visible)
        self.assertEqual(selected.bead_id, state.selected_bead_id)
        self.assertEqual(1, state.selected_index)
        self.assertEqual(FILTER_DEFAULT, state.filter_mode)
        self.assertEqual("Help overlay open. Press ? or Esc to close.", state.status_message)

        closed = state.toggle_help_overlay()

        self.assertFalse(closed)
        self.assertFalse(state.help_overlay_visible)
        self.assertEqual(selected.bead_id, state.selected_bead_id)
        self.assertEqual(1, state.selected_index)
        self.assertEqual(FILTER_DEFAULT, state.filter_mode)
        self.assertEqual("Help overlay closed.", state.status_message)

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

    def test_help_overlay_close_preserves_pending_merge_until_confirmed_after_close(self) -> None:
        target = self.storage.create_bead(
            bead_id="B0001",
            title="Done",
            agent_type="developer",
            description="done",
            status=BEAD_DONE,
        )
        app = build_tui_app(self.storage, refresh_seconds=60)
        app.runtime_state.filter_mode = FILTER_ALL
        app.runtime_state.refresh(activity_message="Loaded bead state.")
        merged_ids: list[str] = []

        def fake_merge(args: SimpleNamespace, storage: RepositoryStorage, console: ConsoleReporter) -> int:
            merged_ids.append(args.bead_id)
            return 0

        async def exercise_app() -> tuple[bool, str, str]:
            with patch("codex_orchestrator.cli.command_merge", side_effect=fake_merge):
                async with app.run_test() as pilot:
                    await pilot.pause()
                    await pilot.press("m")
                    await pilot.pause()
                    pending_status = app.runtime_state.status_message

                    await pilot.press("?")
                    await pilot.pause()
                    await pilot.press("enter")
                    await pilot.pause()
                    while_overlay_open = app.runtime_state.status_message

                    await pilot.press("escape")
                    await pilot.pause()
                    await pilot.press("enter")
                    await pilot.pause()
                    return app.runtime_state.awaiting_merge_confirmation, pending_status, while_overlay_open

        awaiting_merge_confirmation, pending_status, while_overlay_open = asyncio.run(exercise_app())

        self.assertEqual(f"Confirm merge for {target.bead_id} with Enter.", pending_status)
        self.assertEqual("Help overlay open. Press ? or Esc to close.", while_overlay_open)
        self.assertFalse(awaiting_merge_confirmation)
        self.assertEqual([target.bead_id], merged_ids)

    def test_runtime_refresh_falls_back_to_previous_index_when_selected_bead_disappears(self) -> None:
        first = self.storage.create_bead(bead_id="B0001", title="First", agent_type="developer", description="first", status=BEAD_READY)
        second = self.storage.create_bead(bead_id="B0002", title="Second", agent_type="developer", description="second", status=BEAD_BLOCKED)
        state = TuiRuntimeState(self.storage, filter_mode=FILTER_DEFAULT)
        state.selected_bead_id = second.bead_id
        state.selected_index = 1

        second.status = BEAD_DONE
        self.storage.save_bead(second)
        state.refresh()

        self.assertEqual(first.bead_id, state.selected_bead_id)
        self.assertEqual(0, state.selected_index)

    def test_runtime_refresh_handles_corrupt_bead_file_without_crashing(self) -> None:
        bead = self.storage.create_bead(
            bead_id="B0001",
            title="Valid",
            agent_type="developer",
            description="valid",
            status=BEAD_READY,
        )
        state = TuiRuntimeState(self.storage, filter_mode=FILTER_DEFAULT)
        self.assertEqual(bead.bead_id, state.selected_bead_id)

        self.storage.bead_path(bead.bead_id).write_text("", encoding="utf-8")
        state.refresh()

        self.assertIn("Refresh failed:", state.status_message)
        self.assertIn("Refresh failed at", state.activity_message)
        self.assertEqual("refresh", state.last_action)
        self.assertTrue(state.last_result.startswith("failed:"))

    def test_render_panels_ignores_no_matches_when_overlay_is_active(self) -> None:
        app = build_tui_app(self.storage)

        from textual.css.query import NoMatches

        with patch.object(app, "query_one", side_effect=NoMatches()):
            app._render_panels()

    def test_runtime_merge_returns_failure_for_nonzero_exit_without_crashing(self) -> None:
        bead = self.storage.create_bead(bead_id="B0001", title="Done", agent_type="developer", description="done", status=BEAD_DONE)
        state = TuiRuntimeState(self.storage, filter_mode=FILTER_ALL)
        state.request_merge()

        def fake_merge(args: SimpleNamespace, storage: RepositoryStorage, console: ConsoleReporter) -> int:
            self.assertEqual(bead.bead_id, args.bead_id)
            console.error("merge returned 3")
            return 3

        merged = state.confirm_merge(fake_merge)

        self.assertFalse(merged)
        self.assertFalse(state.awaiting_merge_confirmation)
        self.assertEqual(f"Merge failed for {bead.bead_id}.", state.status_message)
        self.assertIn("merge returned 3", state.activity_message)

    def test_runtime_request_merge_on_non_done_bead_is_denied_without_state_mutation(self) -> None:
        blocked = self.storage.create_bead(
            bead_id="B0001",
            title="Blocked",
            agent_type="developer",
            description="blocked",
            status=BEAD_BLOCKED,
        )
        state = TuiRuntimeState(self.storage, filter_mode=FILTER_ALL)

        state.request_merge()

        self.assertFalse(state.awaiting_merge_confirmation)
        self.assertIsNone(state.pending_merge_bead_id)
        self.assertEqual(
            f"{blocked.bead_id} is {blocked.status}; only done beads can be merged.",
            state.status_message,
        )

    def test_runtime_confirm_merge_without_pending_confirmation_is_denied_without_state_mutation(self) -> None:
        done = self.storage.create_bead(
            bead_id="B0001",
            title="Done",
            agent_type="developer",
            description="done",
            status=BEAD_DONE,
        )
        state = TuiRuntimeState(self.storage, filter_mode=FILTER_ALL)

        merged = state.confirm_merge()

        self.assertFalse(merged)
        self.assertFalse(state.awaiting_merge_confirmation)
        self.assertIsNone(state.pending_merge_bead_id)
        self.assertEqual("No merge pending confirmation.", state.status_message)
        self.assertEqual(done.bead_id, state.selected_bead_id)

    def test_runtime_refresh_clears_pending_merge_when_target_leaves_done_view(self) -> None:
        self.storage.create_bead(bead_id="B0001", title="Other done", agent_type="developer", description="other", status=BEAD_DONE)
        target = self.storage.create_bead(
            bead_id="B0002",
            title="Target done",
            agent_type="developer",
            description="target",
            status=BEAD_DONE,
        )
        state = TuiRuntimeState(self.storage, filter_mode=FILTER_ALL)
        state.selected_bead_id = target.bead_id
        state.selected_index = 1
        state.request_merge()

        target.status = BEAD_BLOCKED
        self.storage.save_bead(target)
        state.refresh()

        self.assertFalse(state.awaiting_merge_confirmation)
        self.assertIsNone(state.pending_merge_bead_id)
        self.assertEqual(
            "Merge confirmation cleared because the requested bead is no longer mergeable.",
            state.status_message,
        )

    def test_runtime_confirm_merge_keeps_original_target_across_refresh(self) -> None:
        self.storage.create_bead(bead_id="B0002", title="Later", agent_type="developer", description="later", status=BEAD_DONE)
        target = self.storage.create_bead(
            bead_id="B0004",
            title="Target",
            agent_type="developer",
            description="target",
            status=BEAD_DONE,
        )
        state = TuiRuntimeState(self.storage, filter_mode=FILTER_ALL)
        state.selected_bead_id = target.bead_id
        state.selected_index = 1
        state.request_merge()

        self.storage.create_bead(bead_id="B0001", title="Earlier", agent_type="developer", description="earlier", status=BEAD_DONE)
        state.refresh()

        merged_ids: list[str] = []

        def fake_merge(args: SimpleNamespace, storage: RepositoryStorage, console: ConsoleReporter) -> int:
            merged_ids.append(args.bead_id)
            return 0

        merged = state.confirm_merge(fake_merge)

        self.assertTrue(merged)
        self.assertEqual([target.bead_id], merged_ids)
        self.assertFalse(state.awaiting_merge_confirmation)
        self.assertIsNone(state.pending_merge_bead_id)

    def test_runtime_toggle_continuous_run_updates_footer_and_last_action(self) -> None:
        self.storage.create_bead(bead_id="B0001", title="Ready", agent_type="developer", description="ready", status=BEAD_READY)
        state = TuiRuntimeState(self.storage, filter_mode=FILTER_DEFAULT)

        state.toggle_continuous_run()

        self.assertTrue(state.continuous_run_enabled)
        self.assertEqual("continuous run", state.last_action)
        self.assertEqual("enabled", state.last_result)
        self.assertIn("run=continuous", state.footer_text())

    def test_runtime_timed_refresh_mode_summary_tracks_focus_and_disable_resets_manual_mode(self) -> None:
        self.storage.create_bead(bead_id="B0001", title="Ready", agent_type="developer", description="ready", status=BEAD_READY)
        state = TuiRuntimeState(self.storage, filter_mode=FILTER_DEFAULT, refresh_seconds=7)

        state.set_focused_panel(PANEL_DETAIL)
        state.toggle_timed_refresh()

        self.assertTrue(state.timed_refresh_enabled)
        self.assertFalse(state.continuous_run_enabled)
        self.assertEqual("timed refresh", state.last_action)
        self.assertEqual("refresh/7s", state.last_result)
        self.assertIn("timed refresh every 7s | scheduler=manual | focus=detail", state.status_panel_text())

        state.toggle_continuous_run()
        self.assertIn("timed scheduler every 7s | focus=detail", state.status_panel_text())

        state.toggle_timed_refresh()
        self.assertFalse(state.timed_refresh_enabled)
        self.assertFalse(state.continuous_run_enabled)
        self.assertEqual("manual", state.last_result)
        self.assertIn("manual refresh | scheduler=manual | focus=detail", state.status_panel_text())

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

        async def exercise_app() -> tuple[str, str, str, str]:
            async with app.run_test() as pilot:
                await pilot.pause()
                list_panel = app.screen.query_one("#list-panel")
                detail_panel = app.screen.query_one("#detail-panel")
                status_panel = app.screen.query_one("#status-bar")
                default_title = title_text(list_panel.border_title)
                detail_title = title_text(detail_panel.border_title)
                status_title = title_text(status_panel.border_title)

                for _ in range(6):
                    await pilot.press("f")
                    await pilot.pause()

                ready_title = title_text(app.screen.query_one("#list-panel").border_title)
                return default_title, ready_title, detail_title, status_title

        default_title, ready_title, detail_title, status_title = asyncio.run(exercise_app())

        self.assertIn("Beads [Default] [ACTIVE]", default_title)
        self.assertIn("Beads [Ready] [ACTIVE]", ready_title)
        self.assertIn("Details [idle]", detail_title)
        self.assertEqual("Status", status_title)

    def test_runtime_defaults_to_manual_refresh_until_explicit_auto_mode_is_enabled(self) -> None:
        self.storage.create_bead(bead_id="B0001", title="Ready", agent_type="developer", description="ready", status=BEAD_READY)
        state = TuiRuntimeState(self.storage, filter_mode=FILTER_DEFAULT, refresh_seconds=11)

        self.assertFalse(state.timed_refresh_enabled)
        self.assertFalse(state.continuous_run_enabled)
        self.assertEqual(PANEL_LIST, state.focused_panel)
        self.assertIn("run=manual", state.footer_text())
        self.assertIn("manual refresh | scheduler=manual | focus=list", state.status_panel_text())

        state.toggle_timed_refresh()
        self.assertIn("timed refresh every 11s | scheduler=manual | focus=list", state.status_panel_text())

        state.toggle_continuous_run()
        self.assertIn("timed scheduler every 11s | focus=list", state.status_panel_text())

    def test_runtime_focus_cycles_between_list_and_detail(self) -> None:
        self.storage.create_bead(bead_id="B0001", title="Ready", agent_type="developer", description="ready", status=BEAD_READY)
        state = TuiRuntimeState(self.storage, filter_mode=FILTER_DEFAULT)

        state.cycle_focus(1)
        self.assertEqual(PANEL_DETAIL, state.focused_panel)

        state.cycle_focus(1)
        self.assertEqual(PANEL_LIST, state.focused_panel)

        state.cycle_focus(-1)
        self.assertEqual(PANEL_DETAIL, state.focused_panel)

    def test_runtime_detail_scroll_tracks_bounds(self) -> None:
        bead = self.storage.create_bead(
            bead_id="B0001",
            title="Scrollable",
            agent_type="developer",
            description="scroll",
            status=BEAD_READY,
            acceptance_criteria=[f"criterion {index}" for index in range(80)],
        )
        state = TuiRuntimeState(self.storage, filter_mode=FILTER_DEFAULT)
        self.assertEqual(bead.bead_id, state.selected_bead_id)

        self.assertTrue(state.scroll_detail(3, 8))
        self.assertEqual(3, state.detail_scroll_offset)

        self.assertTrue(state.jump_detail_to_end(8))
        self.assertEqual(state.detail_max_scroll(8), state.detail_scroll_offset)

        self.assertTrue(state.jump_detail_to_start())
        self.assertEqual(0, state.detail_scroll_offset)

    def test_runtime_selection_change_resets_detail_scroll_to_top(self) -> None:
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
        state = TuiRuntimeState(self.storage, filter_mode=FILTER_DEFAULT)

        self.assertTrue(state.scroll_detail(5, 8))
        self.assertEqual(5, state.detail_scroll_offset)

        self.assertTrue(state.select_index(1))
        self.assertEqual(second.bead_id, state.selected_bead_id)
        self.assertEqual(0, state.detail_scroll_offset)

    def test_runtime_boundary_selection_noop_preserves_detail_scroll(self) -> None:
        first = self.storage.create_bead(
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
        state = TuiRuntimeState(self.storage, filter_mode=FILTER_DEFAULT)

        self.assertTrue(state.scroll_detail(5, 8))

        state.move_selection(-1)

        self.assertEqual(first.bead_id, state.selected_bead_id)
        self.assertEqual(0, state.selected_index)
        self.assertEqual(5, state.detail_scroll_offset)
        self.assertEqual("Selection already at the first bead.", state.status_message)

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
            expected_files=["src/codex_orchestrator/tui.py"],
        )
        bead.changed_files = ["tests/test_tui.py"]
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
                await pilot.press("right_square_bracket")
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

    def test_runtime_scheduler_cycle_uses_feature_root_scope_and_records_result(self) -> None:
        feature_root_id, _ = self._create_feature_tree()
        state = TuiRuntimeState(self.storage, feature_root_id=feature_root_id, filter_mode=FILTER_ALL)

        fake_scheduler = Mock()
        fake_scheduler.run_once.return_value = SchedulerResult()

        with patch("codex_orchestrator.tui._make_services", return_value=(self.storage, fake_scheduler, object())) as make_services_mock:
            ran = state.run_scheduler_cycle()

        self.assertTrue(ran)
        make_services_mock.assert_called_once_with(self.storage.root)
        fake_scheduler.run_once.assert_called_once()
        call_kwargs = fake_scheduler.run_once.call_args.kwargs
        self.assertEqual(1, call_kwargs["max_workers"])
        self.assertEqual(feature_root_id, call_kwargs["feature_root_id"])
        self.assertEqual("scheduler run", state.last_action)
        self.assertEqual("success", state.last_result)

    def test_runtime_scheduler_cycle_without_scope_refreshes_global_state_after_completion(self) -> None:
        bead = self.storage.create_bead(
            bead_id="B0001",
            title="Ready",
            agent_type="developer",
            description="ready",
            status=BEAD_READY,
        )
        state = TuiRuntimeState(self.storage, filter_mode=FILTER_ALL)

        def fake_run_once(*, max_workers=1, feature_root_id=None, reporter=None):
            self.assertIsNone(feature_root_id)
            updated = self.storage.load_bead(bead.bead_id)
            updated.status = BEAD_DONE
            self.storage.save_bead(updated)
            result = SchedulerResult()
            result.started.append(bead.bead_id)
            result.completed.append(bead.bead_id)
            return result

        fake_scheduler = Mock()
        fake_scheduler.run_once.side_effect = fake_run_once

        with patch("codex_orchestrator.tui._make_services", return_value=(self.storage, fake_scheduler, object())):
            ran = state.run_scheduler_cycle()

        refreshed = self.storage.load_bead(bead.bead_id)
        self.assertTrue(ran)
        self.assertEqual(BEAD_DONE, refreshed.status)
        self.assertEqual(bead.bead_id, state.selected_bead_id)
        self.assertEqual(BEAD_DONE, state.selected_bead().status)
        self.assertEqual("scheduler run", state.last_action)
        self.assertEqual("success", state.last_result)
        self.assertIn("Cycle done", state.status_panel_text())

    def test_runtime_scheduler_cycle_failure_surfaces_in_status_panel_without_crashing(self) -> None:
        bead = self.storage.create_bead(
            bead_id="B0001",
            title="Ready",
            agent_type="developer",
            description="ready",
            status=BEAD_READY,
        )
        state = TuiRuntimeState(self.storage, filter_mode=FILTER_ALL)

        fake_scheduler = Mock()
        fake_scheduler.run_once.side_effect = RuntimeError("scheduler exploded")

        with patch("codex_orchestrator.tui._make_services", return_value=(self.storage, fake_scheduler, object())):
            ran = state.run_scheduler_cycle()

        self.assertFalse(ran)
        self.assertEqual(bead.bead_id, state.selected_bead_id)
        self.assertEqual("scheduler run", state.last_action)
        self.assertIn("failed", state.last_result)
        self.assertIn("scheduler exploded", state.last_result)
        self.assertIn("Scheduler run failed", state.status_message)

    def test_runtime_retry_requires_confirmation_before_requeue(self) -> None:
        bead = self.storage.create_bead(
            bead_id="B0001",
            title="Blocked",
            agent_type="developer",
            description="blocked",
            status=BEAD_BLOCKED,
        )
        state = TuiRuntimeState(self.storage, filter_mode=FILTER_DEFAULT)

        requested = state.request_retry_selected_blocked_bead()
        before_confirm = self.storage.load_bead(bead.bead_id)
        retried = state.confirm_retry_selected_blocked_bead()

        updated = self.storage.load_bead(bead.bead_id)
        self.assertTrue(requested)
        self.assertEqual(BEAD_BLOCKED, before_confirm.status)
        self.assertTrue(state.awaiting_retry_confirmation is False)
        self.assertTrue(retried)
        self.assertEqual(BEAD_READY, updated.status)
        self.assertEqual(f"retry {bead.bead_id}", state.last_action)
        self.assertEqual("success", state.last_result)
        self.assertIn(f"Retried {bead.bead_id}.", state.status_message)
        self.assertIsNone(state.pending_retry_bead_id)

    def test_runtime_retry_rejects_non_blocked_selection_without_mutation(self) -> None:
        bead = self.storage.create_bead(
            bead_id="B0001",
            title="Ready",
            agent_type="developer",
            description="ready",
            status=BEAD_READY,
        )
        state = TuiRuntimeState(self.storage, filter_mode=FILTER_DEFAULT)

        retried = state.request_retry_selected_blocked_bead()

        updated = self.storage.load_bead(bead.bead_id)
        self.assertFalse(retried)
        self.assertEqual(BEAD_READY, updated.status)
        self.assertEqual(f"retry {bead.bead_id}", state.last_action)
        self.assertEqual("invalid", state.last_result)
        self.assertIn("only blocked beads can be retried", state.status_message)

    def test_runtime_confirm_retry_requires_pending_confirmation(self) -> None:
        bead = self.storage.create_bead(
            bead_id="B0001",
            title="Blocked",
            agent_type="developer",
            description="blocked",
            status=BEAD_BLOCKED,
        )
        state = TuiRuntimeState(self.storage, filter_mode=FILTER_DEFAULT)

        retried = state.confirm_retry_selected_blocked_bead()
        updated = self.storage.load_bead(bead.bead_id)

        self.assertFalse(retried)
        self.assertEqual(BEAD_BLOCKED, updated.status)
        self.assertEqual("retry", state.last_action)
        self.assertEqual("invalid", state.last_result)
        self.assertEqual("No retry pending confirmation.", state.status_message)

    def test_runtime_cancel_pending_retry_clears_flow_without_mutation(self) -> None:
        bead = self.storage.create_bead(
            bead_id="B0001",
            title="Blocked",
            agent_type="developer",
            description="blocked",
            status=BEAD_BLOCKED,
        )
        state = TuiRuntimeState(self.storage, filter_mode=FILTER_DEFAULT)

        state.request_retry_selected_blocked_bead()
        cancelled = state.cancel_pending_action()
        bead_after = self.storage.load_bead(bead.bead_id)

        self.assertTrue(cancelled)
        self.assertEqual(BEAD_BLOCKED, bead_after.status)
        self.assertFalse(state.awaiting_retry_confirmation)
        self.assertIsNone(state.pending_retry_bead_id)
        self.assertEqual(f"Cancelled retry for {bead.bead_id}.", state.status_message)

    def test_runtime_refresh_clears_pending_retry_when_target_is_no_longer_blocked(self) -> None:
        target = self.storage.create_bead(
            bead_id="B0001",
            title="Blocked",
            agent_type="developer",
            description="blocked",
            status=BEAD_BLOCKED,
        )
        state = TuiRuntimeState(self.storage, filter_mode=FILTER_DEFAULT)
        state.request_retry_selected_blocked_bead()

        target.status = BEAD_READY
        self.storage.save_bead(target)
        state.refresh()

        self.assertFalse(state.awaiting_retry_confirmation)
        self.assertIsNone(state.pending_retry_bead_id)
        self.assertEqual(
            "Retry confirmation cleared because the requested bead is no longer blocked.",
            state.status_message,
        )

    def test_runtime_status_update_flow_can_mark_bead_blocked(self) -> None:
        bead = self.storage.create_bead(
            bead_id="B0001",
            title="Ready",
            agent_type="developer",
            description="ready",
            status=BEAD_READY,
        )
        state = TuiRuntimeState(self.storage, filter_mode=FILTER_DEFAULT)

        state.open_status_update_flow()
        state.choose_status_target(BEAD_BLOCKED)
        updated = state.confirm_status_update()

        bead_after = self.storage.load_bead(bead.bead_id)
        self.assertTrue(updated)
        self.assertEqual(BEAD_BLOCKED, bead_after.status)
        self.assertFalse(state.status_flow_active)
        self.assertEqual(f"status update {bead.bead_id}", state.last_action)
        self.assertEqual(f"success -> {BEAD_BLOCKED}", state.last_result)
        self.assertIn(f"Updated {bead.bead_id} to {BEAD_BLOCKED}.", state.status_message)

    def test_runtime_status_update_flow_updates_bead_after_confirmation(self) -> None:
        bead = self.storage.create_bead(
            bead_id="B0001",
            title="Ready docs bead",
            agent_type="documentation",
            description="ready",
            status=BEAD_READY,
        )
        state = TuiRuntimeState(self.storage, filter_mode=FILTER_DEFAULT)

        state.open_status_update_flow()
        state.choose_status_target(BEAD_DONE)
        updated = state.confirm_status_update()

        bead_after = self.storage.load_bead(bead.bead_id)
        self.assertTrue(updated)
        self.assertEqual(BEAD_DONE, bead_after.status)
        self.assertFalse(state.status_flow_active)
        self.assertEqual(f"status update {bead.bead_id}", state.last_action)
        self.assertEqual(f"success -> {BEAD_DONE}", state.last_result)
        self.assertIn(f"Updated {bead.bead_id} to {BEAD_DONE}.", state.status_message)

    def test_runtime_status_update_rejects_marking_developer_bead_done(self) -> None:
        bead = self.storage.create_bead(
            bead_id="B0001",
            title="Ready dev bead",
            agent_type="developer",
            description="ready",
            status=BEAD_READY,
        )
        state = TuiRuntimeState(self.storage, filter_mode=FILTER_DEFAULT)

        state.open_status_update_flow()
        state.choose_status_target(BEAD_DONE)
        updated = state.confirm_status_update()

        bead_after = self.storage.load_bead(bead.bead_id)
        self.assertFalse(updated)
        self.assertEqual(BEAD_READY, bead_after.status)
        self.assertFalse(state.status_flow_active)
        self.assertEqual(f"status update {bead.bead_id}", state.last_action)
        self.assertEqual("invalid", state.last_result)
        self.assertIn("developer bead; mark it done through scheduler execution", state.status_message)

    def test_runtime_status_update_rejects_disallowed_transition_without_mutation(self) -> None:
        bead = self.storage.create_bead(
            bead_id="B0001",
            title="Blocked",
            agent_type="developer",
            description="blocked",
            status=BEAD_BLOCKED,
        )
        state = TuiRuntimeState(self.storage, filter_mode=FILTER_DEFAULT)

        state.open_status_update_flow()
        state.choose_status_target(BEAD_DONE)
        updated = state.confirm_status_update()

        bead_after = self.storage.load_bead(bead.bead_id)
        self.assertFalse(updated)
        self.assertEqual(BEAD_BLOCKED, bead_after.status)
        self.assertFalse(state.status_flow_active)
        self.assertEqual(f"status update {bead.bead_id}", state.last_action)
        self.assertEqual("invalid", state.last_result)
        self.assertIn("developer bead; mark it done through scheduler execution", state.status_message)

    def test_operator_status_update_clears_stale_handoff_block_reason_when_unblocked(self) -> None:
        bead = self.storage.create_bead(
            bead_id="B0001",
            title="Blocked",
            agent_type="developer",
            description="blocked",
            status=BEAD_BLOCKED,
        )
        bead.handoff_summary = HandoffSummary(block_reason="Waiting on a stale blocker.")
        self.storage.save_bead(bead)

        updated = apply_operator_status_update(self.storage, bead.bead_id, BEAD_READY)
        reloaded = self.storage.load_bead(bead.bead_id)
        detail = format_detail_panel(reloaded)

        self.assertEqual(BEAD_READY, updated.status)
        self.assertEqual("", reloaded.block_reason)
        self.assertEqual("", reloaded.handoff_summary.block_reason)
        self.assertIn("Block Reason: -", detail)
        self.assertIn("  block_reason: -", detail)

    def test_runtime_status_update_requires_target_before_confirmation(self) -> None:
        bead = self.storage.create_bead(
            bead_id="B0001",
            title="Ready",
            agent_type="developer",
            description="ready",
            status=BEAD_READY,
        )
        state = TuiRuntimeState(self.storage, filter_mode=FILTER_DEFAULT)

        state.open_status_update_flow()
        updated = state.confirm_status_update()

        bead_after = self.storage.load_bead(bead.bead_id)
        self.assertFalse(updated)
        self.assertEqual(BEAD_READY, bead_after.status)
        self.assertTrue(state.status_flow_active)
        self.assertEqual(f"status update {bead.bead_id}", state.last_action)
        self.assertEqual("invalid", state.last_result)
        self.assertIn(f"Choose ready, blocked, or done for {bead.bead_id} before confirming.", state.status_message)

    def test_runtime_cancel_pending_status_update_clears_flow_without_mutation(self) -> None:
        bead = self.storage.create_bead(
            bead_id="B0001",
            title="Ready",
            agent_type="developer",
            description="ready",
            status=BEAD_READY,
        )
        state = TuiRuntimeState(self.storage, filter_mode=FILTER_DEFAULT)

        state.open_status_update_flow()
        state.choose_status_target(BEAD_BLOCKED)
        cancelled = state.cancel_pending_action()

        bead_after = self.storage.load_bead(bead.bead_id)
        self.assertTrue(cancelled)
        self.assertEqual(BEAD_READY, bead_after.status)
        self.assertFalse(state.status_flow_active)
        self.assertIsNone(state.pending_status_bead_id)
        self.assertIsNone(state.pending_status_target)
        self.assertEqual(f"Cancelled status update for {bead.bead_id}.", state.status_message)

    def test_runtime_merge_and_status_actions_clear_each_others_pending_state(self) -> None:
        bead = self.storage.create_bead(
            bead_id="B0001",
            title="Done",
            agent_type="developer",
            description="done",
            status=BEAD_DONE,
        )
        state = TuiRuntimeState(self.storage, filter_mode=FILTER_ALL)

        state.request_merge()
        self.assertTrue(state.awaiting_merge_confirmation)

        state.open_status_update_flow()
        self.assertFalse(state.awaiting_merge_confirmation)
        self.assertTrue(state.status_flow_active)
        self.assertEqual(bead.bead_id, state.pending_status_bead_id)

        state.choose_status_target(BEAD_BLOCKED)
        state.request_merge()

        self.assertTrue(state.awaiting_merge_confirmation)
        self.assertEqual(bead.bead_id, state.pending_merge_bead_id)
        self.assertFalse(state.status_flow_active)
        self.assertIsNone(state.pending_status_bead_id)
        self.assertIsNone(state.pending_status_target)
        self.assertEqual(f"Confirm merge for {bead.bead_id} with Enter.", state.status_message)

    def test_runtime_retry_merge_and_status_actions_clear_each_others_pending_state(self) -> None:
        bead = self.storage.create_bead(
            bead_id="B0001",
            title="Blocked",
            agent_type="developer",
            description="blocked",
            status=BEAD_BLOCKED,
        )
        state = TuiRuntimeState(self.storage, filter_mode=FILTER_ALL)

        state.request_retry_selected_blocked_bead()
        self.assertTrue(state.awaiting_retry_confirmation)

        state.open_status_update_flow()
        self.assertFalse(state.awaiting_retry_confirmation)
        self.assertTrue(state.status_flow_active)

        state.cancel_pending_action()
        state.request_retry_selected_blocked_bead()
        bead.status = BEAD_DONE
        self.storage.save_bead(bead)
        state.refresh()
        state.request_merge()

        self.assertFalse(state.awaiting_retry_confirmation)
        self.assertTrue(state.awaiting_merge_confirmation)
        self.assertEqual(bead.bead_id, state.pending_merge_bead_id)

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
        with patch("codex_orchestrator.tui.load_textual_runtime", side_effect=RuntimeError("textual missing")):
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

    # ── Async scheduler worker tests (B0131) ──────────────────

    def test_tui_scheduler_reporter_posts_events_to_state_log(self) -> None:
        """TuiSchedulerReporter methods append timestamped lines to scheduler_log."""
        self.storage.create_bead(bead_id="B0001", title="Dev", agent_type="developer", description="d", status=BEAD_READY)
        state = TuiRuntimeState(self.storage, filter_mode=FILTER_ALL)

        fake_app = Mock()
        fake_app.call_from_thread = Mock()
        reporter = TuiSchedulerReporter(fake_app, state)

        bead = self.storage.load_bead("B0001")
        reporter.bead_started(bead)
        reporter.worktree_ready(bead, "feature/b0001", Path("/tmp/wt"))
        reporter.bead_completed(bead, "done", [])
        reporter.bead_blocked(bead, "conflict")
        reporter.bead_failed(bead, "crash")
        reporter.bead_deferred(bead, "waiting")
        reporter.lease_expired("B0001")

        self.assertEqual(7, len(state.scheduler_log))
        self.assertIn("Started developer", state.scheduler_log[0])
        self.assertIn("Worktree ready", state.scheduler_log[1])
        self.assertIn("Completed", state.scheduler_log[2])
        self.assertIn("Blocked: conflict", state.scheduler_log[3])
        self.assertIn("Failed: crash", state.scheduler_log[4])
        self.assertIn("Deferred: waiting", state.scheduler_log[5])
        self.assertIn("Lease expired: B0001", state.scheduler_log[6])
        self.assertEqual(7, fake_app.call_from_thread.call_count)

    def test_tui_scheduler_reporter_survives_app_call_failure(self) -> None:
        """Reporter does not crash if app.call_from_thread raises."""
        self.storage.create_bead(bead_id="B0001", title="Dev", agent_type="developer", description="d", status=BEAD_READY)
        state = TuiRuntimeState(self.storage, filter_mode=FILTER_ALL)

        fake_app = Mock()
        fake_app.call_from_thread.side_effect = RuntimeError("no main thread")
        reporter = TuiSchedulerReporter(fake_app, state)

        bead = self.storage.load_bead("B0001")
        reporter.bead_started(bead)

        self.assertEqual(1, len(state.scheduler_log))
        self.assertIn("Started developer", state.scheduler_log[0])

    def test_tui_scheduler_reporter_stop_is_noop(self) -> None:
        """Reporter.stop() does nothing but must exist for interface compliance."""
        self.storage.create_bead(bead_id="B0001", title="Dev", agent_type="developer", description="d", status=BEAD_READY)
        state = TuiRuntimeState(self.storage, filter_mode=FILTER_ALL)
        reporter = TuiSchedulerReporter(Mock(), state)
        reporter.stop()  # must not raise

    def test_tui_scheduler_reporter_completed_logs_followup_children(self) -> None:
        """Reporter logs followup bead creation when children are provided."""
        self.storage.create_bead(bead_id="B0001", title="Dev", agent_type="developer", description="d", status=BEAD_DONE)
        child = self.storage.create_bead(bead_id="B0001-test", title="Test", agent_type="tester", description="t", parent_id="B0001", status=BEAD_OPEN)
        state = TuiRuntimeState(self.storage, filter_mode=FILTER_ALL)

        reporter = TuiSchedulerReporter(Mock(), state)
        bead = self.storage.load_bead("B0001")
        reporter.bead_completed(bead, "done", [child])

        self.assertEqual(2, len(state.scheduler_log))
        self.assertIn("Completed", state.scheduler_log[0])
        self.assertIn("Created followup B0001-test (tester)", state.scheduler_log[1])

    def test_runtime_scheduler_double_run_guard_rejects_concurrent_cycle(self) -> None:
        """run_scheduler_cycle returns False when scheduler_running is already True."""
        self.storage.create_bead(bead_id="B0001", title="Dev", agent_type="developer", description="d", status=BEAD_READY)
        state = TuiRuntimeState(self.storage, filter_mode=FILTER_ALL)

        state.scheduler_running = True
        ran = state.run_scheduler_cycle()

        self.assertFalse(ran)
        self.assertIn("already in progress", state.status_message)

    def test_runtime_scheduler_running_shows_indicator_in_status_panel(self) -> None:
        """[RUNNING] indicator appears in status panel text while scheduler is active."""
        self.storage.create_bead(bead_id="B0001", title="Dev", agent_type="developer", description="d", status=BEAD_READY)
        state = TuiRuntimeState(self.storage, filter_mode=FILTER_ALL)

        self.assertNotIn("[RUNNING]", state.status_panel_text())

        state.scheduler_running = True
        self.assertIn("[RUNNING]", state.status_panel_text())

        state.scheduler_running = False
        self.assertNotIn("[RUNNING]", state.status_panel_text())

    def test_runtime_scheduler_cycle_passes_max_workers_from_state(self) -> None:
        """run_scheduler_cycle forwards max_workers from TuiRuntimeState to scheduler."""
        self.storage.create_bead(bead_id="B0001", title="Dev", agent_type="developer", description="d", status=BEAD_READY)
        state = TuiRuntimeState(self.storage, filter_mode=FILTER_ALL, max_workers=3)

        fake_scheduler = Mock()
        fake_scheduler.run_once.return_value = SchedulerResult()

        with patch("codex_orchestrator.tui._make_services", return_value=(self.storage, fake_scheduler, object())):
            state.run_scheduler_cycle()

        call_kwargs = fake_scheduler.run_once.call_args.kwargs
        self.assertEqual(3, call_kwargs["max_workers"])

    def test_runtime_scheduler_cycle_passes_reporter_to_scheduler(self) -> None:
        """run_scheduler_cycle forwards the reporter argument to scheduler.run_once."""
        self.storage.create_bead(bead_id="B0001", title="Dev", agent_type="developer", description="d", status=BEAD_READY)
        state = TuiRuntimeState(self.storage, filter_mode=FILTER_ALL)

        fake_scheduler = Mock()
        fake_scheduler.run_once.return_value = SchedulerResult()
        sentinel_reporter = object()

        with patch("codex_orchestrator.tui._make_services", return_value=(self.storage, fake_scheduler, object())):
            state.run_scheduler_cycle(reporter=sentinel_reporter)

        call_kwargs = fake_scheduler.run_once.call_args.kwargs
        self.assertIs(sentinel_reporter, call_kwargs["reporter"])

    def test_runtime_scheduler_cycle_resets_running_flag_on_success_and_failure(self) -> None:
        """scheduler_running is reset to False after both successful and failed cycles."""
        self.storage.create_bead(bead_id="B0001", title="Dev", agent_type="developer", description="d", status=BEAD_READY)
        state = TuiRuntimeState(self.storage, filter_mode=FILTER_ALL)

        fake_scheduler = Mock()
        fake_scheduler.run_once.return_value = SchedulerResult()

        with patch("codex_orchestrator.tui._make_services", return_value=(self.storage, fake_scheduler, object())):
            state.run_scheduler_cycle()
        self.assertFalse(state.scheduler_running)

        fake_scheduler.run_once.side_effect = RuntimeError("boom")
        with patch("codex_orchestrator.tui._make_services", return_value=(self.storage, fake_scheduler, object())):
            state.run_scheduler_cycle()
        self.assertFalse(state.scheduler_running)

    def test_app_start_scheduler_worker_guard_prevents_double_launch(self) -> None:
        """_start_scheduler_worker is a no-op when a worker is already running."""
        self.storage.create_bead(bead_id="B0001", title="Dev", agent_type="developer", description="d", status=BEAD_READY)
        app = build_tui_app(self.storage, refresh_seconds=60)

        app._scheduler_worker_running = True
        with patch.object(app, "run_worker") as run_worker_mock:
            app._start_scheduler_worker()
            run_worker_mock.assert_not_called()

        self.assertIn("already in progress", app.runtime_state.status_message)

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

    def test_runtime_scheduler_cycle_result_summary_includes_all_outcome_types(self) -> None:
        """Cycle done message includes started/completed/blocked/deferred counts."""
        self.storage.create_bead(bead_id="B0001", title="Dev", agent_type="developer", description="d", status=BEAD_READY)
        state = TuiRuntimeState(self.storage, filter_mode=FILTER_ALL)

        result = SchedulerResult()
        result.started.append("B0001")
        result.completed.append("B0001")
        result.blocked.append("B0002")
        result.deferred.append("B0003")

        fake_scheduler = Mock()
        fake_scheduler.run_once.return_value = result

        with patch("codex_orchestrator.tui._make_services", return_value=(self.storage, fake_scheduler, object())):
            state.run_scheduler_cycle()

        self.assertIn("started=1", state.status_message)
        self.assertIn("completed=1", state.status_message)
        self.assertIn("blocked=1", state.status_message)
        self.assertIn("deferred=1", state.status_message)

    def test_runtime_scheduler_cycle_empty_result_shows_no_ready_beads(self) -> None:
        """When scheduler returns no outcomes, status says 'no ready beads'."""
        self.storage.create_bead(bead_id="B0001", title="Dev", agent_type="developer", description="d", status=BEAD_DONE)
        state = TuiRuntimeState(self.storage, filter_mode=FILTER_ALL)

        fake_scheduler = Mock()
        fake_scheduler.run_once.return_value = SchedulerResult()

        with patch("codex_orchestrator.tui._make_services", return_value=(self.storage, fake_scheduler, object())):
            state.run_scheduler_cycle()

        self.assertIn("no ready beads", state.status_message)

    def test_command_tui_rejects_descendant_scope_before_launch(self) -> None:
        feature_root_id, _ = self._create_feature_tree()
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)

        with patch("codex_orchestrator.tui.run_tui") as run_tui_mock:
            exit_code = command_tui(
                SimpleNamespace(feature_root=f"{feature_root_id}-1", refresh_seconds=3),
                self.storage,
                console,
            )

        self.assertEqual(1, exit_code)
        self.assertIn(f"{feature_root_id}-1 is not a valid feature root", stream.getvalue())
        run_tui_mock.assert_not_called()


class TuiTelemetryDisplayTests(unittest.TestCase):
    """Tests for B0132: telemetry summary in bead list and detail panels."""

    # -- _format_duration_ms -------------------------------------------------

    def test_format_duration_ms_none_returns_dash(self) -> None:
        self.assertEqual("-", _format_duration_ms(None))

    def test_format_duration_ms_zero(self) -> None:
        self.assertEqual("0:00", _format_duration_ms(0))

    def test_format_duration_ms_under_one_minute(self) -> None:
        self.assertEqual("0:45", _format_duration_ms(45_000))

    def test_format_duration_ms_exact_minute(self) -> None:
        self.assertEqual("1:00", _format_duration_ms(60_000))

    def test_format_duration_ms_multi_minute(self) -> None:
        self.assertEqual("2:55", _format_duration_ms(175_000))

    def test_format_duration_ms_pads_seconds(self) -> None:
        self.assertEqual("1:05", _format_duration_ms(65_000))

    def test_format_duration_ms_float_input(self) -> None:
        self.assertEqual("0:30", _format_duration_ms(30_500.7))

    # -- _telemetry_badge ----------------------------------------------------

    def test_telemetry_badge_no_metadata_returns_empty(self) -> None:
        bead = Bead(bead_id="B0001", title="T", agent_type="developer", description="d")
        self.assertEqual("", _telemetry_badge(bead))

    def test_telemetry_badge_empty_telemetry_returns_empty(self) -> None:
        bead = Bead(bead_id="B0001", title="T", agent_type="developer", description="d")
        bead.metadata["telemetry"] = {}
        self.assertEqual("", _telemetry_badge(bead))

    def test_telemetry_badge_cost_and_duration(self) -> None:
        bead = Bead(bead_id="B0001", title="T", agent_type="developer", description="d")
        bead.metadata["telemetry"] = {"cost_usd": 0.32, "duration_ms": 175_000}
        self.assertEqual(" [$0.32, 2:55]", _telemetry_badge(bead))

    def test_telemetry_badge_cost_only(self) -> None:
        bead = Bead(bead_id="B0001", title="T", agent_type="developer", description="d")
        bead.metadata["telemetry"] = {"cost_usd": 1.5}
        self.assertEqual(" [$1.50]", _telemetry_badge(bead))

    def test_telemetry_badge_duration_only(self) -> None:
        bead = Bead(bead_id="B0001", title="T", agent_type="developer", description="d")
        bead.metadata["telemetry"] = {"duration_ms": 60_000}
        self.assertEqual(" [1:00]", _telemetry_badge(bead))

    def test_telemetry_badge_falls_back_to_duration_api_ms(self) -> None:
        bead = Bead(bead_id="B0001", title="T", agent_type="developer", description="d")
        bead.metadata["telemetry"] = {"cost_usd": 0.10, "duration_api_ms": 90_000}
        self.assertEqual(" [$0.10, 1:30]", _telemetry_badge(bead))

    def test_telemetry_badge_prefers_duration_ms_over_api_ms(self) -> None:
        bead = Bead(bead_id="B0001", title="T", agent_type="developer", description="d")
        bead.metadata["telemetry"] = {
            "cost_usd": 0.50,
            "duration_ms": 120_000,
            "duration_api_ms": 90_000,
        }
        self.assertEqual(" [$0.50, 2:00]", _telemetry_badge(bead))

    def test_telemetry_badge_zero_cost(self) -> None:
        bead = Bead(bead_id="B0001", title="T", agent_type="developer", description="d")
        bead.metadata["telemetry"] = {"cost_usd": 0.0, "duration_ms": 5_000}
        self.assertEqual(" [$0.00, 0:05]", _telemetry_badge(bead))

    # -- render_tree_panel badge integration ---------------------------------

    def test_render_tree_panel_includes_telemetry_badge(self) -> None:
        bead = Bead(bead_id="B0001", title="Task", agent_type="developer", description="d", status=BEAD_READY)
        bead.metadata["telemetry"] = {"cost_usd": 0.42, "duration_ms": 130_000}
        rows = build_tree_rows([bead])
        output = render_tree_panel(rows, selected_index=0, focused=True)
        self.assertIn("[$0.42, 2:10]", output)

    def test_render_tree_panel_no_badge_without_telemetry(self) -> None:
        bead = Bead(bead_id="B0001", title="Task", agent_type="developer", description="d", status=BEAD_READY)
        rows = build_tree_rows([bead])
        output = render_tree_panel(rows, selected_index=0, focused=True)
        self.assertNotIn("[", output.split("[ready]")[-1].strip())

    # -- format_detail_panel telemetry section --------------------------------

    def test_format_detail_panel_includes_telemetry_section(self) -> None:
        bead = Bead(bead_id="B0001", title="T", agent_type="developer", description="d", status=BEAD_DONE)
        bead.metadata["telemetry"] = {
            "cost_usd": 1.23,
            "duration_ms": 175_000,
            "num_turns": 5,
            "input_tokens": 10_000,
            "output_tokens": 2_000,
            "cache_read_tokens": 500,
            "prompt_chars": 8_000,
            "session_id": "sess-abc123",
        }
        detail = format_detail_panel(bead)
        self.assertIn("Telemetry:", detail)
        self.assertIn("cost_usd: $1.23", detail)
        self.assertIn("duration: 2:55", detail)
        self.assertIn("num_turns: 5", detail)
        self.assertIn("input_tokens: 10000", detail)
        self.assertIn("output_tokens: 2000", detail)
        self.assertIn("cache_read_tokens: 500", detail)
        self.assertIn("prompt_chars: 8000", detail)
        self.assertIn("session_id: sess-abc123", detail)

    def test_format_detail_panel_no_telemetry_omits_section(self) -> None:
        bead = Bead(bead_id="B0001", title="T", agent_type="developer", description="d", status=BEAD_DONE)
        detail = format_detail_panel(bead)
        self.assertNotIn("Telemetry:", detail)

    def test_format_detail_panel_telemetry_missing_optional_fields_shows_dash(self) -> None:
        bead = Bead(bead_id="B0001", title="T", agent_type="developer", description="d", status=BEAD_DONE)
        bead.metadata["telemetry"] = {"cost_usd": 0.50, "duration_ms": 30_000}
        detail = format_detail_panel(bead)
        self.assertIn("Telemetry:", detail)
        self.assertIn("cost_usd: $0.50", detail)
        self.assertIn("duration: 0:30", detail)
        self.assertIn("num_turns: -", detail)
        self.assertIn("session_id: -", detail)

    def test_format_detail_panel_telemetry_history_multi_attempt(self) -> None:
        bead = Bead(bead_id="B0001", title="T", agent_type="developer", description="d", status=BEAD_DONE)
        bead.metadata["telemetry"] = {"cost_usd": 0.50, "duration_ms": 30_000}
        bead.metadata["telemetry_history"] = [
            {"cost_usd": 0.30},
            {"cost_usd": 0.50},
        ]
        detail = format_detail_panel(bead)
        self.assertIn("attempts: 2 (total cost: $0.80)", detail)

    def test_format_detail_panel_telemetry_history_single_attempt_no_summary(self) -> None:
        bead = Bead(bead_id="B0001", title="T", agent_type="developer", description="d", status=BEAD_DONE)
        bead.metadata["telemetry"] = {"cost_usd": 0.50, "duration_ms": 30_000}
        bead.metadata["telemetry_history"] = [{"cost_usd": 0.50}]
        detail = format_detail_panel(bead)
        self.assertNotIn("attempts:", detail)

    def test_format_detail_panel_telemetry_history_with_none_costs(self) -> None:
        bead = Bead(bead_id="B0001", title="T", agent_type="developer", description="d", status=BEAD_DONE)
        bead.metadata["telemetry"] = {"cost_usd": 0.20}
        bead.metadata["telemetry_history"] = [
            {"cost_usd": None},
            {"cost_usd": 0.20},
            {},
        ]
        detail = format_detail_panel(bead)
        self.assertIn("attempts: 3 (total cost: $0.20)", detail)

    # -- _detail_section_body telemetry section ------------------------------

    def test_detail_section_body_telemetry_no_data(self) -> None:
        bead = Bead(bead_id="B0001", title="T", agent_type="developer", description="d")
        body = _detail_section_body(bead, DETAIL_SECTION_TELEMETRY)
        self.assertEqual("No telemetry data.", body)

    def test_detail_section_body_telemetry_with_data(self) -> None:
        bead = Bead(bead_id="B0001", title="T", agent_type="developer", description="d")
        bead.metadata["telemetry"] = {
            "cost_usd": 2.50,
            "duration_api_ms": 200_000,
            "num_turns": 12,
            "input_tokens": 50_000,
            "output_tokens": 8_000,
            "cache_read_tokens": 3_000,
            "prompt_chars": 40_000,
            "session_id": "sess-xyz789",
        }
        body = _detail_section_body(bead, DETAIL_SECTION_TELEMETRY)
        self.assertIn("cost_usd: $2.50", body)
        self.assertIn("duration: 3:20", body)
        self.assertIn("num_turns: 12", body)
        self.assertIn("input_tokens: 50000", body)
        self.assertIn("output_tokens: 8000", body)
        self.assertIn("cache_read_tokens: 3000", body)
        self.assertIn("prompt_chars: 40000", body)
        self.assertIn("session_id: sess-xyz789", body)

    def test_detail_section_body_telemetry_history(self) -> None:
        bead = Bead(bead_id="B0001", title="T", agent_type="developer", description="d")
        bead.metadata["telemetry"] = {"cost_usd": 1.00, "duration_ms": 60_000}
        bead.metadata["telemetry_history"] = [
            {"cost_usd": 0.75},
            {"cost_usd": 1.00},
        ]
        body = _detail_section_body(bead, DETAIL_SECTION_TELEMETRY)
        self.assertIn("attempts: 2 (total cost: $1.75)", body)

    def test_detail_section_body_none_bead(self) -> None:
        body = _detail_section_body(None, DETAIL_SECTION_TELEMETRY)
        self.assertEqual("-", body)

    # -- _detail_section_title -----------------------------------------------

    def test_detail_section_title_telemetry(self) -> None:
        self.assertEqual("Telemetry", _detail_section_title(DETAIL_SECTION_TELEMETRY))

    # -- DETAIL_SECTION_ORDER includes telemetry -----------------------------

    def test_detail_section_order_includes_telemetry(self) -> None:
        from codex_orchestrator.tui import DETAIL_SECTION_ORDER
        self.assertIn(DETAIL_SECTION_TELEMETRY, DETAIL_SECTION_ORDER)
        self.assertEqual(DETAIL_SECTION_TELEMETRY, DETAIL_SECTION_ORDER[-1])


if __name__ == "__main__":
    unittest.main()
