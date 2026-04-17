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
    FILTER_ACTIONABLE,
    FILTER_ALL,
    FILTER_DEFAULT,
    PANEL_DETAIL,
    PANEL_LIST,
    PANEL_SCHEDULER_LOG,
    TuiRuntimeState,
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

        self.assertEqual(FILTER_ACTIONABLE, state.filter_mode)
        self.assertIn("Filter set to actionable.", state.status_panel_text())

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

    def test_default_filter_mode_is_filter_all(self) -> None:
        from agent_takt.tui.state import FILTER_ALL
        state = TuiRuntimeState(storage=self.storage)
        self.assertEqual(FILTER_ALL, state.filter_mode)

    def test_default_layout_mode_is_layout_wide(self) -> None:
        from agent_takt.tui.state import LAYOUT_WIDE
        state = TuiRuntimeState(storage=self.storage)
        self.assertEqual(LAYOUT_WIDE, state.layout_mode)

    def test_toggle_layout_cycles_wide_to_compact_to_wide(self) -> None:
        from agent_takt.tui.state import LAYOUT_WIDE, LAYOUT_COMPACT
        state = TuiRuntimeState(storage=self.storage)
        self.assertEqual(LAYOUT_WIDE, state.layout_mode)
        result1 = state.toggle_layout()
        self.assertEqual(LAYOUT_COMPACT, result1)
        self.assertEqual(LAYOUT_COMPACT, state.layout_mode)
        result2 = state.toggle_layout()
        self.assertEqual(LAYOUT_WIDE, result2)
        self.assertEqual(LAYOUT_WIDE, state.layout_mode)

    def test_toggle_layout_does_not_mutate_selection_or_other_fields(self) -> None:
        self.storage.create_bead(bead_id="B0001", title="T", agent_type="developer", description="d", status=BEAD_READY)
        state = TuiRuntimeState(storage=self.storage, filter_mode=FILTER_ALL)
        state.selected_index = 0
        state.selected_bead_id = "B0001"
        state.focused_panel = PANEL_LIST
        before_index = state.selected_index
        before_bead_id = state.selected_bead_id
        before_focus = state.focused_panel
        state.toggle_layout()
        self.assertEqual(before_index, state.selected_index)
        self.assertEqual(before_bead_id, state.selected_bead_id)
        self.assertEqual(before_focus, state.focused_panel)


class TuiFormatEventTests(unittest.TestCase):
    """Tests for TuiRuntimeState._format_event."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        source_templates = REPO_ROOT / "templates" / "agents"
        target_templates = self.root / "templates" / "agents"
        target_templates.mkdir(parents=True, exist_ok=True)
        for template_path in source_templates.glob("*.md"):
            shutil.copy2(template_path, target_templates / template_path.name)
        from agent_takt.storage import RepositoryStorage as _RS
        self.storage = _RS(self.root)
        self.storage.initialize()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _make_state(self) -> TuiRuntimeState:
        return TuiRuntimeState(storage=self.storage)

    def _record(self, event_type: str, payload: dict | None = None) -> dict:
        return {
            "event_type": event_type,
            "timestamp": "2024-01-15T10:30:00+00:00",
            "payload": payload or {"bead_id": "B0001"},
        }

    def test_format_event_bead_started_includes_agent_type_and_title(self) -> None:
        state = self._make_state()
        record = self._record("bead_started", {"bead_id": "B0001", "agent_type": "developer", "title": "My task"})
        result = state._format_event(record)
        self.assertIsNotNone(result)
        self.assertIn("B0001", result)
        self.assertIn("developer", result)
        self.assertIn("My task", result)
        self.assertIn("started", result)

    def test_format_event_bead_started_without_title_omits_title_part(self) -> None:
        state = self._make_state()
        record = self._record("bead_started", {"bead_id": "B0002", "agent_type": "tester"})
        result = state._format_event(record)
        self.assertIsNotNone(result)
        self.assertIn("B0002", result)
        self.assertNotIn('·', result)

    def test_format_event_worktree_ready_includes_branch_and_path(self) -> None:
        state = self._make_state()
        record = self._record("worktree_ready", {
            "bead_id": "B0001",
            "branch_name": "feature/b0001",
            "worktree_path": "/tmp/wt/B0001",
        })
        result = state._format_event(record)
        self.assertIsNotNone(result)
        self.assertIn("feature/b0001", result)
        self.assertIn("/tmp/wt/B0001", result)
        self.assertIn("[dim]", result)

    def test_format_event_bead_completed_includes_summary_and_green_markup(self) -> None:
        state = self._make_state()
        record = self._record("bead_completed", {"bead_id": "B0001", "summary": "All tests pass"})
        result = state._format_event(record)
        self.assertIsNotNone(result)
        self.assertIn("B0001", result)
        self.assertIn("completed", result)
        self.assertIn("All tests pass", result)
        self.assertIn("[green]", result)

    def test_format_event_bead_blocked_includes_summary_and_yellow_markup(self) -> None:
        state = self._make_state()
        record = self._record("bead_blocked", {"bead_id": "B0001", "summary": "Needs changes"})
        result = state._format_event(record)
        self.assertIsNotNone(result)
        self.assertIn("blocked", result)
        self.assertIn("Needs changes", result)
        self.assertIn("[yellow]", result)

    def test_format_event_bead_failed_includes_summary_and_bold_red_markup(self) -> None:
        state = self._make_state()
        record = self._record("bead_failed", {"bead_id": "B0001", "summary": "Exit code 1"})
        result = state._format_event(record)
        self.assertIsNotNone(result)
        self.assertIn("failed", result)
        self.assertIn("Exit code 1", result)
        self.assertIn("[bold red]", result)

    def test_format_event_bead_deferred_includes_reason_and_dim_markup(self) -> None:
        state = self._make_state()
        record = self._record("bead_deferred", {"bead_id": "B0001", "reason": "dep not done"})
        result = state._format_event(record)
        self.assertIsNotNone(result)
        self.assertIn("deferred", result)
        self.assertIn("dep not done", result)
        self.assertIn("[dim]", result)

    def test_format_event_lease_expired_includes_bead_id(self) -> None:
        state = self._make_state()
        record = self._record("lease_expired", {"bead_id": "B0001"})
        result = state._format_event(record)
        self.assertIsNotNone(result)
        self.assertIn("B0001", result)
        self.assertIn("lease expired", result)

    def test_format_event_scheduler_cycle_started_returns_none(self) -> None:
        state = self._make_state()
        self.assertIsNone(state._format_event(self._record("scheduler_cycle_started")))

    def test_format_event_scheduler_cycle_completed_returns_none(self) -> None:
        state = self._make_state()
        self.assertIsNone(state._format_event(self._record("scheduler_cycle_completed")))

    def test_format_event_bead_deleted_returns_none(self) -> None:
        state = self._make_state()
        self.assertIsNone(state._format_event(self._record("bead_deleted")))

    def test_format_event_unknown_type_returns_none(self) -> None:
        state = self._make_state()
        self.assertIsNone(state._format_event(self._record("some_future_event_type")))

    def test_format_event_invalid_timestamp_uses_fallback(self) -> None:
        state = self._make_state()
        record = {"event_type": "bead_started", "timestamp": "not-a-date", "payload": {"bead_id": "B0001", "agent_type": "developer"}}
        result = state._format_event(record)
        self.assertIsNotNone(result)
        self.assertIn("--:--:--", result)


class TuiTailEventLogTests(unittest.TestCase):
    """Tests for TuiRuntimeState._tail_event_log and load_event_log_history."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        source_templates = REPO_ROOT / "templates" / "agents"
        target_templates = self.root / "templates" / "agents"
        target_templates.mkdir(parents=True, exist_ok=True)
        for template_path in source_templates.glob("*.md"):
            shutil.copy2(template_path, target_templates / template_path.name)
        from agent_takt.storage import RepositoryStorage as _RS
        self.storage = _RS(self.root)
        self.storage.initialize()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write_event(self, event_type: str, bead_id: str = "B0001", **extra) -> None:
        import json as _json
        event_path = self.storage.logs_dir / "events.jsonl"
        record = {"event_type": event_type, "timestamp": "2024-01-15T10:30:00+00:00", "payload": {"bead_id": bead_id, **extra}}
        with event_path.open("a", encoding="utf-8") as fh:
            fh.write(_json.dumps(record) + "\n")

    def test_post_init_sets_offset_to_eof_when_events_file_exists(self) -> None:
        self._write_event("bead_started", agent_type="developer")
        state = TuiRuntimeState(storage=self.storage)
        event_path = self.storage.logs_dir / "events.jsonl"
        self.assertEqual(event_path.stat().st_size, state._event_log_offset)
        self.assertEqual(event_path.stat().st_size, state._history_offset)

    def test_post_init_sets_offsets_to_zero_when_no_events_file(self) -> None:
        event_path = self.storage.logs_dir / "events.jsonl"
        if event_path.exists():
            event_path.unlink()
        state = TuiRuntimeState(storage=self.storage)
        self.assertEqual(0, state._event_log_offset)
        self.assertEqual(0, state._history_offset)

    def test_tail_event_log_returns_new_lines_since_last_offset(self) -> None:
        state = TuiRuntimeState(storage=self.storage)
        initial_log_len = len(state.scheduler_log)

        self._write_event("bead_started", agent_type="developer", title="Task one")
        self._write_event("bead_completed", summary="Done")
        new_lines = state._tail_event_log()

        self.assertEqual(2, len(new_lines))
        self.assertTrue(any("started" in line for line in new_lines))
        self.assertTrue(any("completed" in line for line in new_lines))

    def test_tail_event_log_advances_offset_so_second_call_returns_only_newer_lines(self) -> None:
        state = TuiRuntimeState(storage=self.storage)

        self._write_event("bead_started", agent_type="developer")
        state._tail_event_log()  # consume first event

        self._write_event("bead_completed", summary="Done")
        second_call = state._tail_event_log()

        self.assertEqual(1, len(second_call))
        self.assertIn("completed", second_call[0])

    def test_tail_event_log_returns_empty_when_no_new_content(self) -> None:
        self._write_event("bead_started", agent_type="developer")
        state = TuiRuntimeState(storage=self.storage)
        result = state._tail_event_log()
        self.assertEqual([], result)

    def test_tail_event_log_skips_suppressed_event_types(self) -> None:
        state = TuiRuntimeState(storage=self.storage)
        self._write_event("scheduler_cycle_started")
        self._write_event("scheduler_cycle_completed")
        self._write_event("bead_deleted")
        result = state._tail_event_log()
        self.assertEqual([], result)

    def test_load_event_log_history_prepends_lines_to_scheduler_log(self) -> None:
        import json as _json
        event_path = self.storage.logs_dir / "events.jsonl"
        records = [
            {"event_type": "bead_started", "timestamp": "2024-01-15T10:00:00+00:00", "payload": {"bead_id": "B0001", "agent_type": "developer"}},
            {"event_type": "bead_completed", "timestamp": "2024-01-15T10:01:00+00:00", "payload": {"bead_id": "B0001", "summary": "Done"}},
        ]
        with event_path.open("w", encoding="utf-8") as fh:
            for r in records:
                fh.write(_json.dumps(r) + "\n")

        # Create a new state so offset is set to EOF (no live tail)
        state = TuiRuntimeState(storage=self.storage)
        initial_log = list(state.scheduler_log)

        n = state.load_event_log_history(50)

        self.assertGreaterEqual(n, 1)
        self.assertLessEqual(len(initial_log), len(state.scheduler_log))
        self.assertTrue(any("B0001" in line for line in state.scheduler_log))

    def test_load_event_log_history_sets_exhausted_when_beginning_reached(self) -> None:
        import json as _json
        event_path = self.storage.logs_dir / "events.jsonl"
        record = {"event_type": "bead_started", "timestamp": "2024-01-15T10:00:00+00:00", "payload": {"bead_id": "B0001", "agent_type": "developer"}}
        event_path.write_text(_json.dumps(record) + "\n", encoding="utf-8")

        state = TuiRuntimeState(storage=self.storage)
        state.load_event_log_history(100)

        self.assertTrue(state._history_exhausted)
        self.assertEqual(0, state._history_offset)

    def test_load_event_log_history_returns_zero_when_exhausted(self) -> None:
        import json as _json
        event_path = self.storage.logs_dir / "events.jsonl"
        record = {"event_type": "bead_started", "timestamp": "2024-01-15T10:00:00+00:00", "payload": {"bead_id": "B0001", "agent_type": "developer"}}
        event_path.write_text(_json.dumps(record) + "\n", encoding="utf-8")

        state = TuiRuntimeState(storage=self.storage)
        state.load_event_log_history(100)
        self.assertTrue(state._history_exhausted)

        result = state.load_event_log_history(100)
        self.assertEqual(0, result)


class TuiModeSummaryTests(unittest.TestCase):
    """Tests for mode_summary, format_footer, and removed field verification."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        source_templates = REPO_ROOT / "templates" / "agents"
        target_templates = self.root / "templates" / "agents"
        target_templates.mkdir(parents=True, exist_ok=True)
        for template_path in source_templates.glob("*.md"):
            shutil.copy2(template_path, target_templates / template_path.name)
        from agent_takt.storage import RepositoryStorage as _RS
        self.storage = _RS(self.root)
        self.storage.initialize()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_mode_summary_returns_dashboard_refresh_focus_format(self) -> None:
        state = TuiRuntimeState(storage=self.storage, refresh_seconds=7)
        result = state.mode_summary()
        self.assertEqual(f"dashboard | refresh=7s | focus={state.focused_panel}", result)

    def test_mode_summary_reflects_focused_panel(self) -> None:
        state = TuiRuntimeState(storage=self.storage)
        state.set_focused_panel(PANEL_DETAIL)
        result = state.mode_summary()
        self.assertIn("focus=detail", result)

    def test_runtime_state_has_no_timed_refresh_enabled_field(self) -> None:
        state = TuiRuntimeState(storage=self.storage)
        self.assertFalse(hasattr(state, "timed_refresh_enabled"))

    def test_runtime_state_has_no_continuous_run_enabled_field(self) -> None:
        state = TuiRuntimeState(storage=self.storage)
        self.assertFalse(hasattr(state, "continuous_run_enabled"))

    def test_format_footer_works_without_continuous_run_args(self) -> None:
        from agent_takt.tui import format_footer
        from agent_takt.models import Bead, BEAD_READY
        bead = Bead(bead_id="B0001", title="T", agent_type="developer", description="d", status=BEAD_READY)
        footer = format_footer([bead], filter_mode=FILTER_DEFAULT, selected_index=0, total_rows=1)
        self.assertIn("filter=default", footer)
        self.assertIn("rows=1", footer)
        self.assertIn("? help", footer)


if __name__ == "__main__":
    unittest.main()
