from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_takt.cli import apply_operator_status_update
from agent_takt.models import (
    BEAD_BLOCKED,
    BEAD_DONE,
    BEAD_IN_PROGRESS,
    BEAD_OPEN,
    BEAD_READY,
    Bead,
    HandoffSummary,
    SchedulerResult,
)
from agent_takt.storage import RepositoryStorage
from agent_takt.tui import (
    FILTER_ALL,
    FILTER_DEFAULT,
    PANEL_DETAIL,
    PANEL_LIST,
    PANEL_SCHEDULER_LOG,
    TuiRuntimeState,
    TuiSchedulerReporter,
    format_detail_panel,
)


class TuiRuntimeStateTests(unittest.TestCase):
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
        from agent_takt.models import BEAD_HANDED_OFF
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
        self.assertEqual(1, state.selected_index)

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

    def test_runtime_merge_returns_failure_for_nonzero_exit_without_crashing(self) -> None:
        # TUI no longer executes merges inline; request_merge shows CLI redirect.
        # confirm_merge is a no-op when there is no pending confirmation state.
        self.storage.create_bead(bead_id="B0001", title="Done", agent_type="developer", description="done", status=BEAD_DONE)
        state = TuiRuntimeState(self.storage, filter_mode=FILTER_ALL)
        state.request_merge()

        merged = state.confirm_merge()

        self.assertFalse(merged)
        self.assertFalse(state.awaiting_merge_confirmation)
        self.assertEqual("No merge pending confirmation.", state.status_message)

    def test_runtime_request_merge_on_non_done_bead_shows_cli_redirect(self) -> None:
        # TUI shows CLI redirect for any bead regardless of status; the CLI enforces constraints
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
        self.assertIn(f"takt merge {blocked.bead_id}", state.status_message)

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

    def test_runtime_refresh_shows_cli_redirect_for_selected_bead(self) -> None:
        # request_merge shows CLI redirect for the currently selected bead;
        # no pending merge state is stored, so refresh does not need to clear it.
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

        # CLI redirect is shown for the selected bead
        self.assertIn(f"takt merge {target.bead_id}", state.status_message)
        self.assertFalse(state.awaiting_merge_confirmation)
        self.assertIsNone(state.pending_merge_bead_id)

    def test_runtime_request_merge_shows_cli_redirect_for_selected_bead(self) -> None:
        # request_merge always shows the CLI redirect for the currently selected bead.
        # After a refresh that reorders beads, a new request_merge reflects the current selection.
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

        self.assertIn(f"takt merge {target.bead_id}", state.status_message)
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

    def test_runtime_focus_cycles_through_all_three_panels(self) -> None:
        self.storage.create_bead(bead_id="B0001", title="Ready", agent_type="developer", description="ready", status=BEAD_READY)
        state = TuiRuntimeState(self.storage, filter_mode=FILTER_DEFAULT)

        state.cycle_focus(1)
        self.assertEqual(PANEL_DETAIL, state.focused_panel)

        state.cycle_focus(1)
        self.assertEqual(PANEL_SCHEDULER_LOG, state.focused_panel)

        state.cycle_focus(1)
        self.assertEqual(PANEL_LIST, state.focused_panel)

        state.cycle_focus(-1)
        self.assertEqual(PANEL_SCHEDULER_LOG, state.focused_panel)

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

    def test_runtime_scheduler_cycle_uses_feature_root_scope_and_records_result(self) -> None:
        feature_root_id, _ = self._create_feature_tree()
        state = TuiRuntimeState(self.storage, feature_root_id=feature_root_id, filter_mode=FILTER_ALL)

        fake_scheduler = Mock()
        fake_scheduler.run_once.return_value = SchedulerResult()

        with patch("agent_takt.tui._make_services", return_value=(self.storage, fake_scheduler, object())) as make_services_mock:
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

        with patch("agent_takt.tui._make_services", return_value=(self.storage, fake_scheduler, object())):
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

        with patch("agent_takt.tui._make_services", return_value=(self.storage, fake_scheduler, object())):
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
        # TUI no longer sets awaiting_merge_confirmation; request_merge shows a CLI redirect.
        # Verify that request_merge still clears a pending status flow.
        bead = self.storage.create_bead(
            bead_id="B0001",
            title="Done",
            agent_type="developer",
            description="done",
            status=BEAD_DONE,
        )
        state = TuiRuntimeState(self.storage, filter_mode=FILTER_ALL)

        # request_merge shows CLI redirect without setting pending merge state
        state.request_merge()
        self.assertFalse(state.awaiting_merge_confirmation)
        self.assertIsNone(state.pending_merge_bead_id)
        self.assertIn(f"takt merge {bead.bead_id}", state.status_message)

        # opening status flow clears any merge-related UI state
        state.open_status_update_flow()
        self.assertFalse(state.awaiting_merge_confirmation)
        self.assertTrue(state.status_flow_active)
        self.assertEqual(bead.bead_id, state.pending_status_bead_id)

        # request_merge clears the status flow and shows CLI redirect
        state.choose_status_target(BEAD_BLOCKED)
        state.request_merge()

        self.assertFalse(state.awaiting_merge_confirmation)
        self.assertIsNone(state.pending_merge_bead_id)
        self.assertFalse(state.status_flow_active)
        self.assertIsNone(state.pending_status_bead_id)
        self.assertIsNone(state.pending_status_target)
        self.assertIn(f"takt merge {bead.bead_id}", state.status_message)

    def test_runtime_retry_merge_and_status_actions_clear_each_others_pending_state(self) -> None:
        # TUI no longer sets awaiting_merge_confirmation; request_merge shows a CLI redirect.
        # Verify that request_merge clears a pending retry flow.
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

        # request_merge clears retry state and shows CLI redirect; does not set pending merge
        self.assertFalse(state.awaiting_retry_confirmation)
        self.assertFalse(state.awaiting_merge_confirmation)
        self.assertIsNone(state.pending_merge_bead_id)
        self.assertIn(f"takt merge {bead.bead_id}", state.status_message)

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

        # Index 0 is the "Scheduler cycle starting..." header added on the first _post call.
        self.assertEqual(8, len(state.scheduler_log))
        self.assertIn("Scheduler cycle starting", state.scheduler_log[0])
        self.assertIn("Started developer", state.scheduler_log[1])
        self.assertIn("Worktree ready", state.scheduler_log[2])
        self.assertIn("Completed", state.scheduler_log[3])
        self.assertIn("Blocked: conflict", state.scheduler_log[4])
        self.assertIn("Failed: crash", state.scheduler_log[5])
        self.assertIn("Deferred: waiting", state.scheduler_log[6])
        self.assertIn("Lease expired: B0001", state.scheduler_log[7])
        self.assertEqual(8, fake_app.call_from_thread.call_count)

    def test_tui_scheduler_reporter_survives_app_call_failure(self) -> None:
        """Reporter does not crash if app.call_from_thread raises."""
        self.storage.create_bead(bead_id="B0001", title="Dev", agent_type="developer", description="d", status=BEAD_READY)
        state = TuiRuntimeState(self.storage, filter_mode=FILTER_ALL)

        fake_app = Mock()
        fake_app.call_from_thread.side_effect = RuntimeError("no main thread")
        reporter = TuiSchedulerReporter(fake_app, state)

        bead = self.storage.load_bead("B0001")
        reporter.bead_started(bead)

        # Index 0 is the "Scheduler cycle starting..." header; index 1 is the event line.
        self.assertEqual(2, len(state.scheduler_log))
        self.assertIn("Scheduler cycle starting", state.scheduler_log[0])
        self.assertIn("Started developer", state.scheduler_log[1])

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

        # Index 0 is the "Scheduler cycle starting..." header added on the first _post call.
        self.assertEqual(3, len(state.scheduler_log))
        self.assertIn("Scheduler cycle starting", state.scheduler_log[0])
        self.assertIn("Completed", state.scheduler_log[1])
        self.assertIn("Created followup B0001-test (tester)", state.scheduler_log[2])

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

        with patch("agent_takt.tui._make_services", return_value=(self.storage, fake_scheduler, object())):
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

        with patch("agent_takt.tui._make_services", return_value=(self.storage, fake_scheduler, object())):
            state.run_scheduler_cycle(reporter=sentinel_reporter)

        call_kwargs = fake_scheduler.run_once.call_args.kwargs
        self.assertIs(sentinel_reporter, call_kwargs["reporter"])

    def test_runtime_scheduler_cycle_resets_running_flag_on_success_and_failure(self) -> None:
        """scheduler_running is reset to False after both successful and failed cycles."""
        self.storage.create_bead(bead_id="B0001", title="Dev", agent_type="developer", description="d", status=BEAD_READY)
        state = TuiRuntimeState(self.storage, filter_mode=FILTER_ALL)

        fake_scheduler = Mock()
        fake_scheduler.run_once.return_value = SchedulerResult()

        with patch("agent_takt.tui._make_services", return_value=(self.storage, fake_scheduler, object())):
            state.run_scheduler_cycle()
        self.assertFalse(state.scheduler_running)

        fake_scheduler.run_once.side_effect = RuntimeError("boom")
        with patch("agent_takt.tui._make_services", return_value=(self.storage, fake_scheduler, object())):
            state.run_scheduler_cycle()
        self.assertFalse(state.scheduler_running)

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

        with patch("agent_takt.tui._make_services", return_value=(self.storage, fake_scheduler, object())):
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

        with patch("agent_takt.tui._make_services", return_value=(self.storage, fake_scheduler, object())):
            state.run_scheduler_cycle()

        self.assertIn("no ready beads", state.status_message)

    # -- TuiRuntimeState subtree_telemetry_for and _subtree_cache -------------

    def test_runtime_subtree_telemetry_for_returns_none_for_leaf_bead(self) -> None:
        bead = self.storage.create_bead(
            bead_id="B0001", title="Leaf", agent_type="developer", description="d", status=BEAD_READY,
        )
        state = TuiRuntimeState(storage=self.storage, filter_mode=FILTER_ALL)
        state.refresh()
        self.assertIsNone(state.subtree_telemetry_for("B0001"))

    def test_runtime_subtree_telemetry_for_returns_dict_for_parent(self) -> None:
        parent = self.storage.create_bead(
            bead_id="B0001", title="Parent", agent_type="developer", description="d", status=BEAD_IN_PROGRESS,
        )
        child = self.storage.create_bead(
            bead_id="B0001-test", title="Test", agent_type="tester", description="t",
            parent_id="B0001", status=BEAD_READY,
        )
        child.metadata["telemetry"] = {"cost_usd": 0.40, "duration_ms": 40_000}
        self.storage.save_bead(child)
        state = TuiRuntimeState(storage=self.storage, filter_mode=FILTER_ALL)
        state.refresh()
        result = state.subtree_telemetry_for("B0001")
        self.assertIsNotNone(result)
        self.assertAlmostEqual(0.40, result["cost_usd"])
        self.assertEqual(1, result["bead_count"])

    def test_runtime_detail_panel_body_includes_subtree_for_parent(self) -> None:
        parent = self.storage.create_bead(
            bead_id="B0001", title="Parent", agent_type="developer", description="d", status=BEAD_IN_PROGRESS,
        )
        parent.metadata["telemetry"] = {"cost_usd": 0.10, "duration_ms": 10_000}
        self.storage.save_bead(parent)
        child = self.storage.create_bead(
            bead_id="B0001-test", title="Test", agent_type="tester", description="t",
            parent_id="B0001", status=BEAD_READY,
        )
        child.metadata["telemetry"] = {"cost_usd": 0.30, "duration_ms": 30_000}
        self.storage.save_bead(child)
        state = TuiRuntimeState(storage=self.storage, filter_mode=FILTER_ALL)
        state.refresh()
        # Select the parent bead using its loaded instance from the state cache
        parent_bead = self.storage.load_bead("B0001")
        body = state.detail_panel_body(parent_bead)
        self.assertIn("Subtree:", body)

    def test_runtime_detail_panel_body_no_subtree_for_leaf(self) -> None:
        bead = self.storage.create_bead(
            bead_id="B0001", title="Leaf", agent_type="developer", description="d", status=BEAD_READY,
        )
        bead.metadata["telemetry"] = {"cost_usd": 0.20, "duration_ms": 20_000}
        self.storage.save_bead(bead)
        state = TuiRuntimeState(storage=self.storage, filter_mode=FILTER_ALL)
        state.refresh()
        loaded = self.storage.load_bead("B0001")
        body = state.detail_panel_body(loaded)
        self.assertNotIn("Subtree:", body)

    # -- TuiRuntimeState tests migrated from TuiLegacyTests -------------------

    def test_tui_runtime_refresh_preserves_selection_and_shows_new_rows(self) -> None:
        first = self.storage.create_bead(bead_id="B0001", title="First", agent_type="developer", description="one", status=BEAD_READY)
        second = self.storage.create_bead(bead_id="B0002", title="Second", agent_type="developer", description="two", status=BEAD_BLOCKED)
        state = TuiRuntimeState(self.storage)
        state.selected_bead_id = second.bead_id
        state.selected_index = 1

        self.storage.create_bead(bead_id="B0003", title="Third", agent_type="developer", description="three", status=BEAD_READY)
        state.refresh()

        self.assertEqual(second.bead_id, state.selected_bead_id)
        self.assertEqual(second.bead_id, state.selected_bead().bead_id)
        self.assertEqual(["B0001", "B0002", "B0003"], [row.bead_id for row in state.rows])

    def test_tui_runtime_cycles_filters_and_updates_status_panel(self) -> None:
        self.storage.create_bead(bead_id="B0001", title="Open", agent_type="developer", description="one", status=BEAD_OPEN)
        self.storage.create_bead(bead_id="B0002", title="Done", agent_type="developer", description="two", status=BEAD_DONE)
        state = TuiRuntimeState(self.storage)

        state.cycle_filter(1)

        self.assertEqual(FILTER_ALL, state.filter_mode)
        self.assertIn("Filter set to all.", state.status_panel_text())
        self.assertIn("done=1", state.status_panel_text())

    def test_tui_runtime_merge_shows_cli_redirect_for_any_bead(self) -> None:
        # TUI no longer performs merges inline; it shows the CLI command regardless of bead status
        bead = self.storage.create_bead(bead_id="B0001", title="Ready", agent_type="developer", description="one", status=BEAD_READY)
        state = TuiRuntimeState(self.storage)

        state.request_merge()

        self.assertFalse(state.awaiting_merge_confirmation)
        self.assertIsNone(state.pending_merge_bead_id)
        self.assertIn(f"takt merge {bead.bead_id}", state.status_message)

    def test_tui_runtime_merge_shows_cli_redirect_for_done_bead(self) -> None:
        # TUI redirects to CLI instead of executing merge inline
        bead = self.storage.create_bead(bead_id="B0001", title="Done", agent_type="developer", description="one", status=BEAD_DONE)
        state = TuiRuntimeState(self.storage, filter_mode=FILTER_ALL)

        state.request_merge()

        self.assertFalse(state.awaiting_merge_confirmation)
        self.assertIsNone(state.pending_merge_bead_id)
        self.assertIn(f"takt merge {bead.bead_id}", state.status_message)

    def test_tui_runtime_confirm_merge_no_op_when_no_pending_state(self) -> None:
        # confirm_merge returns False gracefully when awaiting_merge_confirmation is False
        self.storage.create_bead(bead_id="B0001", title="Done", agent_type="developer", description="one", status=BEAD_DONE)
        state = TuiRuntimeState(self.storage, filter_mode=FILTER_ALL)

        # request_merge no longer sets awaiting_merge_confirmation
        state.request_merge()
        self.assertFalse(state.awaiting_merge_confirmation)

        merged = state.confirm_merge()

        self.assertFalse(merged)
        self.assertEqual("No merge pending confirmation.", state.status_message)

    def test_tui_runtime_merge_clears_other_pending_states(self) -> None:
        # request_merge clears pending retry/status flows
        bead = self.storage.create_bead(bead_id="B0001", title="Done", agent_type="developer", description="one", status=BEAD_DONE)
        state = TuiRuntimeState(self.storage, filter_mode=FILTER_ALL)

        state.request_merge()

        self.assertFalse(state.awaiting_retry_confirmation)
        self.assertFalse(state.status_flow_active)
        self.assertIn(f"takt merge {bead.bead_id}", state.status_message)


if __name__ == "__main__":
    unittest.main()
