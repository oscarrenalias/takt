from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_takt.gitutils import WorktreeManager
from agent_takt.models import (
    BEAD_DONE,
    BEAD_IN_PROGRESS,
    BEAD_READY,
    AgentRunResult,
    Lease,
)
from agent_takt.scheduler import Scheduler
from agent_takt.storage import RepositoryStorage

# Suppress git commits for the test session (mirrors test_orchestrator.py convention).
RepositoryStorage._auto_commit = False

from helpers import FakeRunner, OrchestratorTests  # noqa: E402


class SchedulerCoreTests(OrchestratorTests):
    # ------------------------------------------------------------------
    # Bead selection
    # ------------------------------------------------------------------

    def test_ready_beads_respect_dependencies(self) -> None:
        bead1 = self.storage.create_bead(title="First", agent_type="developer", description="one")
        bead2 = self.storage.create_bead(
            title="Second",
            agent_type="developer",
            description="two",
            dependencies=[bead1.bead_id],
        )
        ready = [bead.bead_id for bead in self.storage.ready_beads()]
        self.assertEqual([bead1.bead_id], ready)

        bead1.status = BEAD_DONE
        self.storage.save_bead(bead1)
        ready = [bead.bead_id for bead in self.storage.ready_beads()]
        self.assertEqual([bead2.bead_id], ready)

    # ------------------------------------------------------------------
    # Conflict detection
    # ------------------------------------------------------------------

    def test_scheduler_defers_overlapping_claims(self) -> None:
        bead1 = self.storage.create_bead(
            title="Scheduler conflict A",
            agent_type="developer",
            description="one",
            expected_files=["src/agent_takt/scheduler.py"],
        )
        bead2 = self.storage.create_bead(
            title="Scheduler conflict B",
            agent_type="developer",
            description="two",
            expected_files=["src/agent_takt/scheduler.py"],
        )
        runner = FakeRunner(
            results={
                bead1.bead_id: AgentRunResult(outcome="completed", summary="done", expected_files=bead1.expected_files),
            }
        )
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))
        result = scheduler.run_once(max_workers=2)
        self.assertEqual([bead1.bead_id], result.completed)
        self.assertEqual([bead1.bead_id], result.started)
        self.assertEqual([bead2.bead_id], result.deferred)
        deferred = self.storage.load_bead(bead2.bead_id)
        self.assertEqual(BEAD_READY, deferred.status)
        self.assertIn(bead1.bead_id, deferred.block_reason)

    def test_scheduler_allows_non_overlapping_claims_with_capacity(self) -> None:
        bead1 = self.storage.create_bead(
            title="Planner scope",
            agent_type="developer",
            description="one",
            expected_files=["src/agent_takt/planner.py"],
        )
        bead2 = self.storage.create_bead(
            title="Storage scope",
            agent_type="developer",
            description="two",
            expected_files=["src/agent_takt/storage.py"],
        )
        runner = FakeRunner(
            results={
                bead1.bead_id: AgentRunResult(outcome="completed", summary="done", expected_files=bead1.expected_files),
                bead2.bead_id: AgentRunResult(outcome="completed", summary="done", expected_files=bead2.expected_files),
            }
        )
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))
        result = scheduler.run_once(max_workers=2)
        self.assertEqual(sorted([bead1.bead_id, bead2.bead_id]), sorted(result.started))
        self.assertEqual(sorted([bead1.bead_id, bead2.bead_id]), sorted(result.completed))
        self.assertEqual([], result.deferred)

    def test_scheduler_handles_missing_scope_conservatively_within_same_feature_tree(self) -> None:
        epic = self.storage.create_bead(title="Epic", agent_type="planner", description="root", status=BEAD_DONE, bead_type="epic")
        root = self.storage.create_bead(title="Feature root", agent_type="developer", description="feature", parent_id=epic.bead_id, status=BEAD_DONE)
        bead1 = self.storage.create_bead(title="Implement A", agent_type="developer", description="one", parent_id=root.bead_id, dependencies=[root.bead_id])
        bead2 = self.storage.create_bead(title="Implement B", agent_type="developer", description="two", parent_id=root.bead_id, dependencies=[root.bead_id])
        runner = FakeRunner(
            results={
                bead1.bead_id: AgentRunResult(outcome="completed", summary="done"),
            }
        )
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))
        result = scheduler.run_once(max_workers=2)
        self.assertEqual([bead1.bead_id], result.started)
        self.assertEqual([bead2.bead_id], result.deferred)

    def test_same_feature_tree_non_overlapping_mutations_can_run_in_parallel(self) -> None:
        epic = self.storage.create_bead(title="Epic", agent_type="planner", description="root", status=BEAD_DONE, bead_type="epic")
        root = self.storage.create_bead(title="Feature root", agent_type="developer", description="feature", parent_id=epic.bead_id, status=BEAD_DONE)
        bead1 = self.storage.create_bead(
            title="Planner scope",
            agent_type="developer",
            description="one",
            parent_id=root.bead_id,
            dependencies=[root.bead_id],
            expected_files=["src/agent_takt/planner.py"],
        )
        bead2 = self.storage.create_bead(
            title="Storage scope",
            agent_type="developer",
            description="two",
            parent_id=root.bead_id,
            dependencies=[root.bead_id],
            expected_files=["src/agent_takt/storage.py"],
        )
        runner = FakeRunner(
            results={
                bead1.bead_id: AgentRunResult(outcome="completed", summary="done", expected_files=bead1.expected_files),
                bead2.bead_id: AgentRunResult(outcome="completed", summary="done", expected_files=bead2.expected_files),
            }
        )
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))
        result = scheduler.run_once(max_workers=2)
        self.assertEqual(sorted([bead1.bead_id, bead2.bead_id]), sorted(result.started))
        self.assertEqual(sorted([bead1.bead_id, bead2.bead_id]), sorted(result.completed))
        bead1 = self.storage.load_bead(bead1.bead_id)
        bead2 = self.storage.load_bead(bead2.bead_id)
        self.assertEqual(root.bead_id, bead1.feature_root_id)
        self.assertEqual(root.bead_id, bead2.feature_root_id)
        self.assertEqual(bead1.execution_worktree_path, bead2.execution_worktree_path)

    # ------------------------------------------------------------------
    # run_once() — feature root filter
    # ------------------------------------------------------------------

    def test_scheduler_run_once_feature_root_filter_runs_only_selected_tree(self) -> None:
        epic = self.storage.create_bead(
            title="Epic",
            agent_type="planner",
            description="root",
            status=BEAD_DONE,
            bead_type="epic",
        )
        root_a = self.storage.create_bead(
            title="Feature A",
            agent_type="developer",
            description="feature-a",
            parent_id=epic.bead_id,
            status=BEAD_DONE,
        )
        root_b = self.storage.create_bead(
            title="Feature B",
            agent_type="developer",
            description="feature-b",
            parent_id=epic.bead_id,
            status=BEAD_DONE,
        )
        bead_a = self.storage.create_bead(
            title="Task A",
            agent_type="developer",
            description="task-a",
            parent_id=root_a.bead_id,
            dependencies=[root_a.bead_id],
            expected_files=["src/a.py"],
        )
        bead_b = self.storage.create_bead(
            title="Task B",
            agent_type="developer",
            description="task-b",
            parent_id=root_b.bead_id,
            dependencies=[root_b.bead_id],
            expected_files=["src/b.py"],
        )
        runner = FakeRunner(
            results={
                bead_a.bead_id: AgentRunResult(outcome="completed", summary="done", expected_files=bead_a.expected_files),
            }
        )
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))
        result = scheduler.run_once(feature_root_id=root_a.bead_id, max_workers=2)
        self.assertEqual([bead_a.bead_id], result.started)
        self.assertEqual([bead_a.bead_id], result.completed)
        self.assertEqual([], result.blocked)
        self.assertEqual([], result.deferred)
        bead_b_after = self.storage.load_bead(bead_b.bead_id)
        self.assertEqual(BEAD_READY, bead_b_after.status)

    # ------------------------------------------------------------------
    # Lease management
    # ------------------------------------------------------------------

    def test_active_claims_report_in_progress_scope(self) -> None:
        bead = self.storage.create_bead(
            title="Active bead",
            agent_type="developer",
            description="running",
            expected_files=["src/agent_takt/scheduler.py"],
            touched_files=["src/agent_takt/scheduler.py"],
            conflict_risks="Potential overlap with scheduler edits.",
        )
        bead.status = BEAD_IN_PROGRESS
        bead.lease = Lease(owner="developer:B0001", expires_at="2099-01-01T00:00:00+00:00")
        self.storage.save_bead(bead)
        claims = self.storage.active_claims()
        self.assertEqual(1, len(claims))
        self.assertEqual(bead.bead_id, claims[0]["bead_id"])
        self.assertEqual(bead.bead_id, claims[0]["feature_root_id"])
        self.assertEqual("touched_files", claims[0]["scope_source"])
        self.assertEqual(["src/agent_takt/scheduler.py"], claims[0]["touched_files"])

    # ------------------------------------------------------------------
    # Feature root inheritance
    # ------------------------------------------------------------------

    def test_descendants_inherit_feature_root_and_shared_worktree(self) -> None:
        epic = self.storage.create_bead(title="Epic", agent_type="planner", description="root", status=BEAD_DONE, bead_type="epic")
        root = self.storage.create_bead(
            title="Feature root",
            agent_type="developer",
            description="feature",
            parent_id=epic.bead_id,
            expected_files=["src/root.py"],
        )
        child = self.storage.create_bead(
            title="Child task",
            agent_type="developer",
            description="subtask",
            parent_id=root.bead_id,
            dependencies=[root.bead_id],
            expected_files=["src/child.py"],
        )
        self.assertEqual(root.bead_id, root.feature_root_id)
        self.assertEqual(root.bead_id, child.feature_root_id)
        self.assertEqual(root.execution_worktree_path, child.execution_worktree_path)
        self.assertEqual(root.execution_branch_name, child.execution_branch_name)


if __name__ == "__main__":
    unittest.main()
