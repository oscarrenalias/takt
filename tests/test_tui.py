from __future__ import annotations

import asyncio
import io
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
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
)
from codex_orchestrator.storage import RepositoryStorage
from codex_orchestrator.tui import (
    FILTER_ACTIONABLE,
    FILTER_ALL,
    FILTER_DEFAULT,
    FILTER_DEFERRED,
    FILTER_DONE,
    TuiRuntimeState,
    build_tree_rows,
    build_tui_app,
    collect_tree_rows,
    format_detail_panel,
    format_help_overlay,
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
        self.assertIn("q           Quit", overlay)
        self.assertIn("Shift+f     Previous filter", overlay)
        self.assertIn("t           Request blocked-bead retry", overlay)
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
                status_panel = app.screen.query_one("#status-panel")
                opened_text = str(status_panel.renderable)

                await pilot.press("?")
                await pilot.pause()
                base_screen = app.screen_stack[0]
                opened_text = str(base_screen.query_one("#status-panel").renderable)

                await pilot.press("?")
                await pilot.pause()
                closed_text = str(app.screen.query_one("#status-panel").renderable)
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

    def test_runtime_scheduler_cycle_uses_feature_root_scope_and_records_result(self) -> None:
        feature_root_id, _ = self._create_feature_tree()
        state = TuiRuntimeState(self.storage, feature_root_id=feature_root_id, filter_mode=FILTER_ALL)

        fake_scheduler = object()

        with patch("codex_orchestrator.cli.make_services", return_value=(self.storage, fake_scheduler, object())) as make_services_mock:
            with patch("codex_orchestrator.cli.command_run", return_value=0) as command_run_mock:
                ran = state.run_scheduler_cycle()

        self.assertTrue(ran)
        make_services_mock.assert_called_once_with(self.storage.root)
        command_run_args = command_run_mock.call_args.args[0]
        self.assertTrue(command_run_args.once)
        self.assertEqual(1, command_run_args.max_workers)
        self.assertEqual(feature_root_id, command_run_args.feature_root)
        self.assertEqual("scheduler run", state.last_action)
        self.assertEqual("success", state.last_result)
        self.assertEqual("Scheduler cycle completed.", state.status_message)

    def test_runtime_scheduler_cycle_without_scope_refreshes_global_state_after_completion(self) -> None:
        bead = self.storage.create_bead(
            bead_id="B0001",
            title="Ready",
            agent_type="developer",
            description="ready",
            status=BEAD_READY,
        )
        state = TuiRuntimeState(self.storage, filter_mode=FILTER_ALL)
        fake_scheduler = object()

        def fake_run(args: SimpleNamespace, scheduler: object, console: ConsoleReporter) -> int:
            self.assertIs(fake_scheduler, scheduler)
            self.assertIsNone(args.feature_root)
            updated = self.storage.load_bead(bead.bead_id)
            updated.status = BEAD_DONE
            self.storage.save_bead(updated)
            console.info(f"completed {bead.bead_id}")
            return 0

        with patch("codex_orchestrator.cli.make_services", return_value=(self.storage, fake_scheduler, object())):
            with patch("codex_orchestrator.cli.command_run", side_effect=fake_run):
                ran = state.run_scheduler_cycle()

        refreshed = self.storage.load_bead(bead.bead_id)
        self.assertTrue(ran)
        self.assertEqual(BEAD_DONE, refreshed.status)
        self.assertEqual(bead.bead_id, state.selected_bead_id)
        self.assertEqual(BEAD_DONE, state.selected_bead().status)
        self.assertIn("completed B0001", state.activity_message)
        self.assertIn("Last Action: scheduler run", state.status_panel_text())
        self.assertIn("Last Result: success", state.status_panel_text())

    def test_runtime_scheduler_cycle_failure_surfaces_in_status_panel_without_crashing(self) -> None:
        bead = self.storage.create_bead(
            bead_id="B0001",
            title="Ready",
            agent_type="developer",
            description="ready",
            status=BEAD_READY,
        )
        state = TuiRuntimeState(self.storage, filter_mode=FILTER_ALL)

        with patch("codex_orchestrator.cli.make_services", return_value=(self.storage, object(), object())):
            with patch("codex_orchestrator.cli.command_run", side_effect=RuntimeError("scheduler exploded")):
                ran = state.run_scheduler_cycle()

        self.assertFalse(ran)
        self.assertEqual(bead.bead_id, state.selected_bead_id)
        self.assertEqual("scheduler run", state.last_action)
        self.assertEqual("failed: scheduler exploded", state.last_result)
        self.assertEqual("Scheduler run failed: scheduler exploded", state.status_message)
        self.assertIn("Last Action: scheduler run", state.status_panel_text())
        self.assertIn("Last Result: failed: scheduler exploded", state.status_panel_text())

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
            title="Ready",
            agent_type="developer",
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
        self.assertIn(f"{bead.bead_id} is {BEAD_BLOCKED}; cannot mark it {BEAD_DONE}.", state.status_message)

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
            agent_type="developer",
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


if __name__ == "__main__":
    unittest.main()
