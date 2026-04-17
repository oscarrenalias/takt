from __future__ import annotations

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
    BEAD_BLOCKED,
    BEAD_DONE,
    BEAD_HANDED_OFF,
    BEAD_IN_PROGRESS,
    BEAD_OPEN,
    BEAD_READY,
    Bead,
)
from agent_takt.storage import RepositoryStorage
from agent_takt.tui import (
    FILTER_ACTIONABLE,
    FILTER_ALL,
    FILTER_DEFAULT,
    FILTER_DEFERRED,
    FILTER_DONE,
    build_tree_rows,
    collect_tree_rows,
    render_tree_panel,
    resolve_selected_bead,
    resolve_selected_index,
    supported_filter_modes,
)


class TuiTreeBuildingTests(unittest.TestCase):
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
        self.assertEqual("[dim]○[/dim] B0002 · Root", rows[0].label)
        self.assertEqual("  [dim]○[/dim] B0002-1 · Alpha", rows[1].label)
        self.assertEqual("    [dim]○[/dim] B0002-1-1 · Grandchild A", rows[2].label)
        self.assertEqual("  [dim]○[/dim] B0002-2 · Beta", rows[3].label)

    def test_tui_supports_default_grouped_and_terminal_filters(self) -> None:
        statuses = [
            BEAD_OPEN,
            BEAD_READY,
            BEAD_IN_PROGRESS,
            BEAD_BLOCKED,
            BEAD_HANDED_OFF,
            BEAD_DONE,
        ]
        for index, status in enumerate(statuses, start=1):
            self.storage.create_bead(
                bead_id=f"B{index:04d}",
                title=status,
                agent_type="developer",
                description=status,
                status=status,
            )

        default_rows = collect_tree_rows(self.storage, filter_mode=FILTER_DEFAULT)
        self.assertEqual(
            [BEAD_OPEN, BEAD_READY, BEAD_IN_PROGRESS, BEAD_BLOCKED, BEAD_HANDED_OFF],
            [row.bead.status for row in default_rows],
        )
        self.assertEqual([BEAD_OPEN, BEAD_READY], [row.bead.status for row in collect_tree_rows(self.storage, filter_mode=FILTER_ACTIONABLE)])
        self.assertEqual([BEAD_HANDED_OFF], [row.bead.status for row in collect_tree_rows(self.storage, filter_mode=FILTER_DEFERRED)])
        self.assertEqual([BEAD_DONE], [row.bead.status for row in collect_tree_rows(self.storage, filter_mode=FILTER_DONE)])
        self.assertEqual(statuses, [row.bead.status for row in collect_tree_rows(self.storage, filter_mode=FILTER_ALL)])
        self.assertIn(BEAD_DONE, supported_filter_modes())

    def test_tui_feature_root_filter_keeps_root_when_status_filter_hides_it(self) -> None:
        root = self.storage.create_bead(
            bead_id="B0001",
            title="Feature Root",
            agent_type="developer",
            description="root",
            status=BEAD_DONE,
        )
        self.storage.create_bead(
            bead_id="B0001-test",
            title="Child",
            agent_type="developer",
            description="child",
            parent_id=root.bead_id,
            status=BEAD_READY,
        )

        rows = collect_tree_rows(self.storage, filter_mode=FILTER_DEFAULT, feature_root_id=root.bead_id)

        self.assertEqual(["B0001", "B0001-test"], [row.bead_id for row in rows])
        self.assertEqual([0, 1], [row.depth for row in rows])
        self.assertEqual([BEAD_DONE, BEAD_READY], [row.bead.status for row in rows])

    def test_tui_tree_rows_are_deterministic_and_indent_descendants(self) -> None:
        root_b = Bead(bead_id="B0002", title="Root B", agent_type="developer", description="b")
        child_b2 = Bead(
            bead_id="B0002-2",
            title="Child B2",
            agent_type="developer",
            description="b2",
            parent_id="B0002",
        )
        root_a = Bead(bead_id="B0001", title="Root A", agent_type="developer", description="a")
        child_a2 = Bead(
            bead_id="B0001-2",
            title="Child A2",
            agent_type="developer",
            description="a2",
            parent_id="B0001",
        )
        child_a1 = Bead(
            bead_id="B0001-1",
            title="Child A1",
            agent_type="developer",
            description="a1",
            parent_id="B0001",
        )
        grandchild = Bead(
            bead_id="B0001-1-1",
            title="Grandchild",
            agent_type="developer",
            description="a11",
            parent_id="B0001-1",
        )

        rows = build_tree_rows([child_b2, child_a2, root_b, grandchild, root_a, child_a1])

        self.assertEqual(
            ["B0001", "B0001-1", "B0001-1-1", "B0001-2", "B0002", "B0002-2"],
            [row.bead_id for row in rows],
        )
        self.assertEqual([0, 1, 2, 1, 0, 1], [row.depth for row in rows])
        self.assertEqual("  [dim]○[/dim] B0001-1 · Child A1", rows[1].label)
        self.assertEqual("    [dim]○[/dim] B0001-1-1 · Grandchild", rows[2].label)

    def test_tui_selection_preserves_selected_bead_when_visible(self) -> None:
        first = Bead(bead_id="B0001", title="First", agent_type="developer", description="one")
        second = Bead(bead_id="B0002", title="Second", agent_type="developer", description="two")
        rows = build_tree_rows([first, second])

        self.assertEqual(1, resolve_selected_index(rows, selected_bead_id="B0002", previous_index=0))
        self.assertEqual("B0002", resolve_selected_bead(rows, selected_bead_id="B0002", previous_index=0).bead_id)
        self.assertEqual(1, resolve_selected_index(rows, selected_bead_id="B9999", previous_index=3))
        self.assertEqual("B0001", resolve_selected_bead(rows, previous_index=None).bead_id)

    def test_tui_render_tree_panel_marks_selected_row(self) -> None:
        rows = build_tree_rows([
            Bead(bead_id="B0001", title="One", agent_type="developer", description="one", status=BEAD_READY),
            Bead(bead_id="B0002", title="Two", agent_type="developer", description="two", status=BEAD_BLOCKED),
        ])

        panel = render_tree_panel(rows, 1)

        self.assertIn("> B0002 · Two [blocked]", panel)
        self.assertIn("  B0001 · One [ready]", panel)
        self.assertNotIn("Beads [", panel)


if __name__ == "__main__":
    unittest.main()
