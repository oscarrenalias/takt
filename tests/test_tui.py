from __future__ import annotations

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

from codex_orchestrator.cli import build_parser, command_tui
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
    collect_tree_rows,
    format_detail_panel,
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
