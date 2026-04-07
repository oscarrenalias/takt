from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_takt.config import SchedulerConfig
from agent_takt.graph import MAX_TITLE_LENGTH, render_bead_graph
from agent_takt.models import (
    BEAD_BLOCKED,
    BEAD_DONE,
    BEAD_IN_PROGRESS,
    BEAD_READY,
)
from agent_takt.storage import RepositoryStorage

RepositoryStorage._auto_commit = False

from helpers import OrchestratorTests as _OrchestratorBase  # noqa: E402


class GraphTests(_OrchestratorBase):

    def test_render_bead_graph_outputs_labels_edges_icons_and_orphans(self) -> None:
        dependency = self.storage.create_bead(
            title="Dependency bead",
            agent_type="planner",
            description="upstream dependency",
            status=BEAD_DONE,
            bead_id="B-graph-dep",
        )
        # Create B-missing so dependency validation passes, but exclude it from
        # the list passed to render_bead_graph to test that missing-node edges
        # are not rendered.
        self.storage.create_bead(
            title="Missing bead",
            agent_type="developer",
            description="bead that will be omitted from graph input",
            bead_id="B-missing",
        )
        main = self.storage.create_bead(
            title="X" * (MAX_TITLE_LENGTH + 8),
            agent_type="developer",
            description="main task",
            parent_id=dependency.bead_id,
            dependencies=[dependency.bead_id, "B-missing"],
            status=BEAD_IN_PROGRESS,
            bead_id="B-graph-main",
        )
        corrective = self.storage.create_bead(
            title='Corrective "fix"\nfollowup',
            agent_type="developer",
            description="corrective task",
            parent_id=main.bead_id,
            status=BEAD_BLOCKED,
            bead_id="B-graph-main-corrective",
        )
        orphan = self.storage.create_bead(
            title="Standalone",
            agent_type="review",
            description="orphan node",
            status=BEAD_READY,
            bead_id="B-graph-orphan",
        )

        graph = render_bead_graph([dependency, main, corrective, orphan], SchedulerConfig())

        truncated_title = f'{"X" * (MAX_TITLE_LENGTH - 3)}...'
        self.assertTrue(graph.startswith("graph TD\n"))
        self.assertIn('B_graph_dep["B-graph-dep: Dependency bead [planner] ✓"]', graph)
        self.assertIn(
            f'B_graph_main["B-graph-main: {truncated_title} [developer] ..."]',
            graph,
        )
        self.assertIn(
            'B_graph_main_corrective["B-graph-main-corrective: Corrective \\"fix\\" followup [developer] !"]',
            graph,
        )
        self.assertIn('B_graph_orphan["B-graph-orphan: Standalone [review] ○"]', graph)
        self.assertIn("B_graph_dep --> B_graph_main", graph)
        self.assertIn("B_graph_main_corrective -.-> B_graph_main", graph)
        self.assertNotIn("B_missing --> B_graph_main", graph)
        self.assertIn("B_graph_orphan", graph)


if __name__ == "__main__":
    unittest.main()
