from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_takt.models import (
    BEAD_DONE,
    PlanChild,
    PlanProposal,
)
from agent_takt.planner import PlanningService
from agent_takt.prompts import build_planner_prompt
from agent_takt.storage import RepositoryStorage

RepositoryStorage._auto_commit = False

from helpers import FakeRunner, OrchestratorTests as _OrchestratorBase  # noqa: E402


class PlannerTests(_OrchestratorBase):

    def test_planner_writes_epic_and_children(self) -> None:
        spec_path = self.root / "spec.md"
        spec_path.write_text("Feature spec\n", encoding="utf-8")
        proposal = PlanProposal(
            epic_title="Epic",
            epic_description="Parent task",
            linked_docs=["spec.md"],
            feature=PlanChild(
                title="Feature root",
                agent_type="developer",
                description="shared execution root",
                acceptance_criteria=["works"],
                children=[
                    PlanChild(
                        title="Implement",
                        agent_type="developer",
                        description="build",
                        acceptance_criteria=["works"],
                        dependencies=[],
                        expected_files=["src/agent_takt/scheduler.py"],
                        children=[
                            PlanChild(
                                title="Review",
                                agent_type="review",
                                description="check",
                                acceptance_criteria=["approved"],
                                dependencies=["Implement"],
                                expected_globs=["src/agent_takt/*.py"],
                            )
                        ],
                    )
                ],
            ),
        )
        planner = PlanningService(self.storage, FakeRunner(proposal=proposal))
        created = planner.write_plan(planner.propose(spec_path))
        self.assertEqual(4, len(created))
        epic = self.storage.load_bead(created[0])
        feature = self.storage.load_bead(created[1])
        implement = self.storage.load_bead(created[2])
        review = self.storage.load_bead(created[3])
        self.assertEqual(BEAD_DONE, epic.status)
        self.assertIsNone(epic.feature_root_id)
        self.assertEqual(BEAD_DONE, feature.status)
        self.assertEqual("feature", feature.bead_type)
        self.assertEqual(feature.bead_id, feature.feature_root_id)
        self.assertEqual(feature.bead_id, implement.parent_id)
        self.assertEqual(feature.bead_id, implement.feature_root_id)
        self.assertEqual(feature.bead_id, review.feature_root_id)
        self.assertEqual(implement.bead_id, review.parent_id)
        self.assertEqual([implement.bead_id], review.dependencies)
        self.assertEqual(["src/agent_takt/scheduler.py"], implement.expected_files)
        self.assertEqual(["src/agent_takt/*.py"], review.expected_globs)

    def test_planner_writes_shared_followups_at_feature_root_with_multi_bead_dependencies(self) -> None:
        spec_path = self.root / "spec.md"
        spec_path.write_text("Feature spec\n", encoding="utf-8")
        proposal = PlanProposal(
            epic_title="Epic",
            epic_description="Parent task",
            linked_docs=["spec.md"],
            feature=PlanChild(
                title="Feature root",
                agent_type="developer",
                description="shared execution root",
                acceptance_criteria=["works"],
                children=[
                    PlanChild(
                        title="Implement A",
                        agent_type="developer",
                        description="first focused change",
                        acceptance_criteria=["works"],
                        expected_files=["src/a.py"],
                    ),
                    PlanChild(
                        title="Implement B",
                        agent_type="developer",
                        description="second focused change",
                        acceptance_criteria=["works"],
                        dependencies=["Implement A"],
                        expected_files=["src/b.py"],
                    ),
                    PlanChild(
                        title="Shared tester",
                        agent_type="tester",
                        description="validate combined changes",
                        acceptance_criteria=["approved"],
                        dependencies=["Implement A", "Implement B"],
                    ),
                    PlanChild(
                        title="Shared docs",
                        agent_type="documentation",
                        description="document combined changes",
                        acceptance_criteria=["docs updated"],
                        dependencies=["Implement A", "Implement B"],
                    ),
                    PlanChild(
                        title="Shared review",
                        agent_type="review",
                        description="review combined changes",
                        acceptance_criteria=["approved"],
                        dependencies=["Implement A", "Implement B", "Shared tester", "Shared docs"],
                    ),
                ],
            ),
        )

        planner = PlanningService(self.storage, FakeRunner(proposal=proposal))
        created = planner.write_plan(planner.propose(spec_path))

        self.assertEqual(7, len(created))
        feature = self.storage.load_bead(created[1])
        implement_a = self.storage.load_bead(created[2])
        implement_b = self.storage.load_bead(created[3])
        shared_test = self.storage.load_bead(created[4])
        shared_docs = self.storage.load_bead(created[5])
        shared_review = self.storage.load_bead(created[6])
        self.assertEqual(feature.bead_id, implement_a.parent_id)
        self.assertEqual(feature.bead_id, implement_b.parent_id)
        self.assertEqual(feature.bead_id, shared_test.parent_id)
        self.assertEqual(feature.bead_id, shared_docs.parent_id)
        self.assertEqual(feature.bead_id, shared_review.parent_id)
        self.assertEqual([implement_a.bead_id], implement_b.dependencies)
        self.assertEqual([implement_a.bead_id, implement_b.bead_id], shared_test.dependencies)
        self.assertEqual([implement_a.bead_id, implement_b.bead_id], shared_docs.dependencies)
        self.assertEqual(
            [implement_a.bead_id, implement_b.bead_id, shared_test.bead_id, shared_docs.bead_id],
            shared_review.dependencies,
        )

    def test_write_plan_rejects_invalid_agent_type(self) -> None:
        spec_path = self.root / "spec.md"
        spec_path.write_text("Feature spec\n", encoding="utf-8")
        proposal = PlanProposal(
            epic_title="Epic",
            epic_description="Parent task",
            feature=PlanChild(
                title="Feature root",
                agent_type="developer",
                description="shared execution root",
                acceptance_criteria=[],
                children=[
                    PlanChild(
                        title="Bad bead",
                        agent_type="docs",
                        description="invalid agent type",
                        acceptance_criteria=[],
                    )
                ],
            ),
        )
        planner = PlanningService(self.storage, FakeRunner(proposal=proposal))
        with self.assertRaises(ValueError) as ctx:
            planner.write_plan(planner.propose(spec_path))
        self.assertIn("docs", str(ctx.exception))
        self.assertIn("Bad bead", str(ctx.exception))

    def test_build_planner_prompt_requires_small_developer_beads_and_shared_followups(self) -> None:
        prompt = build_planner_prompt("Ship the feature")
        self.assertIn("one focused change", prompt)
        self.assertIn("roughly 10 minutes of implementation work", prompt)
        self.assertIn(
            "Split broader logical units into smaller dependent developer beads instead of assigning one bead to absorb multiple distinct changes.",
            prompt,
        )
        self.assertIn("touch more than 2-3 functions", prompt)
        self.assertIn("break it into smaller dependent beads with explicit ordering", prompt)
        self.assertIn(
            "coalesce tester, documentation, and review work into shared follow-up beads rather than duplicating that work in each implementation bead.",
            prompt,
        )
        self.assertIn(
            "Those shared follow-up beads should depend on the full related implementation set they validate, document, or review so the follow-up happens after the combined change is ready.",
            prompt,
        )


if __name__ == "__main__":
    unittest.main()
