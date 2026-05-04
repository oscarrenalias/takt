from __future__ import annotations

import dataclasses
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_takt.config import default_config
from agent_takt.gitutils import WorktreeManager
from agent_takt.models import (
    BEAD_DONE,
    BEAD_IN_PROGRESS,
    BEAD_READY,
    AgentRunResult,
    Lease,
    BEAD_BLOCKED,
)
from agent_takt.scheduler import Scheduler
from agent_takt.storage import RepositoryStorage

# Suppress git commits for the test session (mirrors test_orchestrator.py convention).
RepositoryStorage._auto_commit = False

from helpers import FakeRunner, OrchestratorTests  # noqa: E402


class _FakeRunnerWithDefault(FakeRunner):
    """FakeRunner that returns a default completed result for beads not explicitly configured.

    Prevents continuous-loop tests from stalling when followup beads are created and
    dispatched — they complete cleanly instead of failing and creating corrective beads.
    """

    def run_bead(self, bead, *, workdir, context_paths, execution_env=None, dep_handoffs=None):
        if bead.bead_id in self.results:
            return super().run_bead(
                bead,
                workdir=workdir,
                context_paths=context_paths,
                execution_env=execution_env,
                dep_handoffs=dep_handoffs,
            )
        return AgentRunResult(outcome="completed", summary="default-complete")


class _RecordingReporter:
    """Minimal SchedulerReporter stub that records bead_deferred events."""

    def __init__(self):
        self.deferred_calls: list[tuple[str, str]] = []  # (bead_id, reason)

    def lease_expired(self, bead_id: str) -> None:
        pass

    def bead_started(self, bead) -> None:
        pass

    def worktree_ready(self, bead, branch_name: str, worktree_path: Path) -> None:
        pass

    def bead_completed(self, bead, summary: str, created: list) -> None:
        pass

    def bead_deferred(self, bead, reason: str) -> None:
        self.deferred_calls.append((bead.bead_id, reason))

    def bead_blocked(self, bead, summary: str) -> None:
        pass

    def bead_failed(self, bead, summary: str) -> None:
        pass


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
        runner = _FakeRunnerWithDefault(
            results={
                bead1.bead_id: AgentRunResult(outcome="completed", summary="done", expected_files=bead1.expected_files),
            }
        )
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))
        result = scheduler.run_once(max_workers=2)
        # bead1 must start and complete; bead2 must be deferred initially due to conflict.
        self.assertIn(bead1.bead_id, result.started)
        self.assertIn(bead1.bead_id, result.completed)
        self.assertIn(bead2.bead_id, result.deferred)

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
        runner = _FakeRunnerWithDefault(
            results={
                bead1.bead_id: AgentRunResult(outcome="completed", summary="done", expected_files=bead1.expected_files),
                bead2.bead_id: AgentRunResult(outcome="completed", summary="done", expected_files=bead2.expected_files),
            }
        )
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))
        result = scheduler.run_once(max_workers=2)
        # Both original beads must start and complete; neither should be deferred.
        self.assertIn(bead1.bead_id, result.started)
        self.assertIn(bead2.bead_id, result.started)
        self.assertIn(bead1.bead_id, result.completed)
        self.assertIn(bead2.bead_id, result.completed)
        self.assertNotIn(bead1.bead_id, result.deferred)
        self.assertNotIn(bead2.bead_id, result.deferred)

    def test_scheduler_handles_missing_scope_conservatively_within_same_feature_tree(self) -> None:
        epic = self.storage.create_bead(title="Epic", agent_type="planner", description="root", status=BEAD_DONE, bead_type="epic")
        root = self.storage.create_bead(title="Feature root", agent_type="developer", description="feature", parent_id=epic.bead_id, status=BEAD_DONE)
        bead1 = self.storage.create_bead(title="Implement A", agent_type="developer", description="one", parent_id=root.bead_id, dependencies=[root.bead_id])
        bead2 = self.storage.create_bead(title="Implement B", agent_type="developer", description="two", parent_id=root.bead_id, dependencies=[root.bead_id])
        runner = _FakeRunnerWithDefault(
            results={
                bead1.bead_id: AgentRunResult(outcome="completed", summary="done"),
            }
        )
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))
        result = scheduler.run_once(max_workers=2)
        # bead1 must start; bead2 must be deferred initially (no file scope, same worktree).
        self.assertIn(bead1.bead_id, result.started)
        self.assertIn(bead2.bead_id, result.deferred)

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
        runner = _FakeRunnerWithDefault(
            results={
                bead1.bead_id: AgentRunResult(outcome="completed", summary="done", expected_files=bead1.expected_files),
                bead2.bead_id: AgentRunResult(outcome="completed", summary="done", expected_files=bead2.expected_files),
            }
        )
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))
        result = scheduler.run_once(max_workers=2)
        # Both beads must start and complete, and neither should be deferred.
        self.assertIn(bead1.bead_id, result.started)
        self.assertIn(bead2.bead_id, result.started)
        self.assertIn(bead1.bead_id, result.completed)
        self.assertIn(bead2.bead_id, result.completed)
        self.assertNotIn(bead1.bead_id, result.deferred)
        self.assertNotIn(bead2.bead_id, result.deferred)
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
        runner = _FakeRunnerWithDefault(
            results={
                bead_a.bead_id: AgentRunResult(outcome="completed", summary="done", expected_files=bead_a.expected_files),
            }
        )
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))
        result = scheduler.run_once(feature_root_id=root_a.bead_id, max_workers=2)
        # bead_a must start and complete; bead_b (different feature tree) must remain untouched.
        self.assertIn(bead_a.bead_id, result.started)
        self.assertIn(bead_a.bead_id, result.completed)
        self.assertNotIn(bead_b.bead_id, result.started)
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
    # Priority ordering
    # ------------------------------------------------------------------

    def test_high_priority_bead_selected_before_earlier_normal_priority_bead(self) -> None:
        """A high-priority bead created after a normal-priority bead is selected first."""
        normal = self.storage.create_bead(
            title="Normal priority task",
            agent_type="developer",
            description="created first",
            expected_files=["src/normal.py"],
        )
        high = self.storage.create_bead(
            title="High priority task",
            agent_type="developer",
            description="created second but high priority",
            expected_files=["src/high.py"],
            priority="high",
        )
        runner = _FakeRunnerWithDefault(
            results={
                normal.bead_id: AgentRunResult(outcome="completed", summary="done", expected_files=normal.expected_files),
                high.bead_id: AgentRunResult(outcome="completed", summary="done", expected_files=high.expected_files),
            }
        )
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))
        # max_workers=1: high-priority bead must be the FIRST bead selected.
        result = scheduler.run_once(max_workers=1)
        self.assertEqual(high.bead_id, result.started[0])
        self.assertIn(high.bead_id, result.completed)

    def test_creation_order_preserved_within_priority_tier(self) -> None:
        """Among high-priority beads, creation order is preserved."""
        high1 = self.storage.create_bead(
            title="High priority first",
            agent_type="developer",
            description="first high",
            expected_files=["src/high1.py"],
            priority="high",
        )
        high2 = self.storage.create_bead(
            title="High priority second",
            agent_type="developer",
            description="second high",
            expected_files=["src/high2.py"],
            priority="high",
        )
        runner = _FakeRunnerWithDefault(
            results={
                high1.bead_id: AgentRunResult(outcome="completed", summary="done", expected_files=high1.expected_files),
                high2.bead_id: AgentRunResult(outcome="completed", summary="done", expected_files=high2.expected_files),
            }
        )
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))
        # max_workers=1: high1 was created first and must be the first bead started.
        result = scheduler.run_once(max_workers=1)
        self.assertEqual(high1.bead_id, result.started[0])

    def test_high_priority_bead_deferred_due_to_conflict_normal_bead_runs(self) -> None:
        """A conflicting high-priority bead is deferred; the non-conflicting normal bead runs."""
        # An already-in-progress bead holds the conflicting scope
        in_progress = self.storage.create_bead(
            title="In-progress bead",
            agent_type="developer",
            description="running",
            expected_files=["src/conflict.py"],
        )
        in_progress.status = BEAD_IN_PROGRESS
        in_progress.lease = Lease(owner="developer:running", expires_at="2099-01-01T00:00:00+00:00")
        self.storage.save_bead(in_progress)

        normal = self.storage.create_bead(
            title="Normal priority task",
            agent_type="developer",
            description="safe files",
            expected_files=["src/normal.py"],
        )
        high_conflicting = self.storage.create_bead(
            title="High priority conflicting task",
            agent_type="developer",
            description="conflicts with in-progress",
            expected_files=["src/conflict.py"],
            priority="high",
        )
        runner = FakeRunner(
            results={
                normal.bead_id: AgentRunResult(outcome="completed", summary="done", expected_files=normal.expected_files),
            }
        )
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))
        result = scheduler.run_once(max_workers=1)
        # High-priority bead is deferred due to conflict
        self.assertIn(high_conflicting.bead_id, result.deferred)
        # Normal-priority bead runs because it has no conflict
        self.assertIn(normal.bead_id, result.started)
        self.assertIn(normal.bead_id, result.completed)

    def test_multi_worker_selects_both_high_and_normal_priority_when_capacity_allows(self) -> None:
        """With max_workers=2, both a high and a normal priority bead are selected."""
        normal = self.storage.create_bead(
            title="Normal priority task",
            agent_type="developer",
            description="created first",
            expected_files=["src/normal.py"],
        )
        high = self.storage.create_bead(
            title="High priority task",
            agent_type="developer",
            description="created second but high priority",
            expected_files=["src/high.py"],
            priority="high",
        )
        runner = _FakeRunnerWithDefault(
            results={
                normal.bead_id: AgentRunResult(outcome="completed", summary="done", expected_files=normal.expected_files),
                high.bead_id: AgentRunResult(outcome="completed", summary="done", expected_files=high.expected_files),
            }
        )
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))
        result = scheduler.run_once(max_workers=2)
        self.assertIn(high.bead_id, result.started)
        self.assertIn(normal.bead_id, result.started)
        self.assertIn(high.bead_id, result.completed)
        self.assertIn(normal.bead_id, result.completed)
        # Neither original bead should be deferred; only followup beads may be deferred
        # due to intra-feature-tree worktree conflicts.
        self.assertNotIn(high.bead_id, result.deferred)
        self.assertNotIn(normal.bead_id, result.deferred)

    # ------------------------------------------------------------------
    # serialize_within_feature_tree flag
    # ------------------------------------------------------------------

    def test_serialize_off_same_tree_non_overlapping_files_both_dispatched(self) -> None:
        epic = self.storage.create_bead(title="Epic", agent_type="planner", description="root", status=BEAD_DONE, bead_type="epic")
        root = self.storage.create_bead(title="Feature root", agent_type="developer", description="feature", parent_id=epic.bead_id, status=BEAD_DONE)
        bead1 = self.storage.create_bead(
            title="Task A", agent_type="developer", description="a",
            parent_id=root.bead_id, dependencies=[root.bead_id],
            expected_files=["src/a.py"],
        )
        bead2 = self.storage.create_bead(
            title="Task B", agent_type="developer", description="b",
            parent_id=root.bead_id, dependencies=[root.bead_id],
            expected_files=["src/b.py"],
        )
        config = default_config()  # serialize_within_feature_tree=False
        runner = _FakeRunnerWithDefault(
            results={
                bead1.bead_id: AgentRunResult(outcome="completed", summary="done", expected_files=bead1.expected_files),
                bead2.bead_id: AgentRunResult(outcome="completed", summary="done", expected_files=bead2.expected_files),
            }
        )
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir), config=config)
        result = scheduler.run_once(max_workers=2)
        self.assertIn(bead1.bead_id, result.started)
        self.assertIn(bead2.bead_id, result.started)
        self.assertNotIn(bead1.bead_id, result.deferred)
        self.assertNotIn(bead2.bead_id, result.deferred)

    def test_serialize_on_same_tree_non_overlapping_files_second_deferred(self) -> None:
        epic = self.storage.create_bead(title="Epic", agent_type="planner", description="root", status=BEAD_DONE, bead_type="epic")
        root = self.storage.create_bead(title="Feature root", agent_type="developer", description="feature", parent_id=epic.bead_id, status=BEAD_DONE)
        bead1 = self.storage.create_bead(
            title="Task A", agent_type="developer", description="a",
            parent_id=root.bead_id, dependencies=[root.bead_id],
            expected_files=["src/a.py"],
        )
        bead2 = self.storage.create_bead(
            title="Task B", agent_type="developer", description="b",
            parent_id=root.bead_id, dependencies=[root.bead_id],
            expected_files=["src/b.py"],
        )
        cfg = default_config()
        config = dataclasses.replace(cfg, scheduler=dataclasses.replace(cfg.scheduler, serialize_within_feature_tree=True))
        runner = _FakeRunnerWithDefault(
            results={
                bead1.bead_id: AgentRunResult(outcome="completed", summary="done", expected_files=bead1.expected_files),
                bead2.bead_id: AgentRunResult(outcome="completed", summary="done", expected_files=bead2.expected_files),
            }
        )
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir), config=config)
        result = scheduler.run_once(max_workers=2)
        self.assertIn(bead1.bead_id, result.started)
        self.assertIn(bead2.bead_id, result.deferred)

    def test_serialize_on_cross_feature_tree_both_dispatched(self) -> None:
        bead1 = self.storage.create_bead(
            title="Tree A task", agent_type="developer", description="a",
            expected_files=["src/a.py"],
        )
        bead2 = self.storage.create_bead(
            title="Tree B task", agent_type="developer", description="b",
            expected_files=["src/b.py"],
        )
        cfg = default_config()
        config = dataclasses.replace(cfg, scheduler=dataclasses.replace(cfg.scheduler, serialize_within_feature_tree=True))
        runner = _FakeRunnerWithDefault(
            results={
                bead1.bead_id: AgentRunResult(outcome="completed", summary="done", expected_files=bead1.expected_files),
                bead2.bead_id: AgentRunResult(outcome="completed", summary="done", expected_files=bead2.expected_files),
            }
        )
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir), config=config)
        result = scheduler.run_once(max_workers=2)
        self.assertIn(bead1.bead_id, result.started)
        self.assertIn(bead2.bead_id, result.started)
        self.assertNotIn(bead1.bead_id, result.deferred)
        self.assertNotIn(bead2.bead_id, result.deferred)

    def test_serialize_on_non_mutating_pair_same_tree_no_conflict(self) -> None:
        epic = self.storage.create_bead(title="Epic", agent_type="planner", description="root", status=BEAD_DONE, bead_type="epic")
        root = self.storage.create_bead(title="Feature root", agent_type="developer", description="feature", parent_id=epic.bead_id, status=BEAD_DONE)
        planner_bead = self.storage.create_bead(
            title="Planner task", agent_type="planner", description="plan",
            parent_id=root.bead_id, dependencies=[root.bead_id],
        )
        review_bead = self.storage.create_bead(
            title="Review task", agent_type="review", description="review",
            parent_id=root.bead_id, dependencies=[root.bead_id],
        )
        cfg = default_config()
        config = dataclasses.replace(cfg, scheduler=dataclasses.replace(cfg.scheduler, serialize_within_feature_tree=True))
        runner = _FakeRunnerWithDefault(
            results={
                planner_bead.bead_id: AgentRunResult(outcome="completed", summary="done"),
                review_bead.bead_id: AgentRunResult(outcome="completed", summary="done", verdict="approved"),
            }
        )
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir), config=config)
        result = scheduler.run_once(max_workers=2)
        self.assertIn(planner_bead.bead_id, result.started)
        self.assertIn(review_bead.bead_id, result.started)
        self.assertNotIn(planner_bead.bead_id, result.deferred)
        self.assertNotIn(review_bead.bead_id, result.deferred)

    def test_serialize_on_deferral_reason_is_worktree_serialization_message(self) -> None:
        epic = self.storage.create_bead(title="Epic", agent_type="planner", description="root", status=BEAD_DONE, bead_type="epic")
        root = self.storage.create_bead(title="Feature root", agent_type="developer", description="feature", parent_id=epic.bead_id, status=BEAD_DONE)
        bead1 = self.storage.create_bead(
            title="Task A", agent_type="developer", description="a",
            parent_id=root.bead_id, dependencies=[root.bead_id],
            expected_files=["src/a.py"],
        )
        bead2 = self.storage.create_bead(
            title="Task B", agent_type="developer", description="b",
            parent_id=root.bead_id, dependencies=[root.bead_id],
            expected_files=["src/b.py"],
        )
        cfg = default_config()
        config = dataclasses.replace(cfg, scheduler=dataclasses.replace(cfg.scheduler, serialize_within_feature_tree=True))
        reporter = _RecordingReporter()
        runner = _FakeRunnerWithDefault(
            results={
                bead1.bead_id: AgentRunResult(outcome="completed", summary="done", expected_files=bead1.expected_files),
            }
        )
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir), config=config)
        result = scheduler.run_once(max_workers=2, reporter=reporter)
        self.assertIn(bead2.bead_id, result.deferred)
        reasons = {bid: reason for bid, reason in reporter.deferred_calls}
        self.assertIn(bead2.bead_id, reasons)
        expected_reason = f"worktree serialization enabled — waiting on in-progress {bead1.bead_id}"
        self.assertEqual(expected_reason, reasons[bead2.bead_id])

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


class SlotFillTests(OrchestratorTests):
    """Tests for the continuous slot-fill loop introduced in the reactive scheduler."""

    def test_third_bead_dispatched_within_same_run_once_call(self) -> None:
        """With max_workers=2 and 3 ready beads, the third bead is dispatched within the
        same run_once() call after the first worker completes — not in a subsequent call."""
        bead1 = self.storage.create_bead(
            title="Task A", agent_type="developer", description="a", expected_files=["src/a.py"]
        )
        bead2 = self.storage.create_bead(
            title="Task B", agent_type="developer", description="b", expected_files=["src/b.py"]
        )
        bead3 = self.storage.create_bead(
            title="Task C", agent_type="developer", description="c", expected_files=["src/c.py"]
        )
        runner = _FakeRunnerWithDefault(
            results={
                bead1.bead_id: AgentRunResult(outcome="completed", summary="done", expected_files=bead1.expected_files),
                bead2.bead_id: AgentRunResult(outcome="completed", summary="done", expected_files=bead2.expected_files),
                bead3.bead_id: AgentRunResult(outcome="completed", summary="done", expected_files=bead3.expected_files),
            }
        )
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))
        result = scheduler.run_once(max_workers=2)
        # All three original beads must have been dispatched in this single run_once() call.
        self.assertIn(bead1.bead_id, result.started)
        self.assertIn(bead2.bead_id, result.started)
        self.assertIn(bead3.bead_id, result.started)
        # All three must have completed.
        self.assertIn(bead1.bead_id, result.completed)
        self.assertIn(bead2.bead_id, result.completed)
        self.assertIn(bead3.bead_id, result.completed)

    def test_run_once_terminates_when_no_futures_and_no_ready_beads(self) -> None:
        """run_once() must return when no futures are active and no ready beads remain."""
        bead = self.storage.create_bead(
            title="Task", agent_type="developer", description="work", expected_files=["src/x.py"]
        )
        runner = _FakeRunnerWithDefault(
            results={
                bead.bead_id: AgentRunResult(outcome="completed", summary="done", expected_files=bead.expected_files),
            }
        )
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))
        # Should not hang; returns once all work is done.
        result = scheduler.run_once(max_workers=2)
        self.assertIn(bead.bead_id, result.started)
        self.assertIn(bead.bead_id, result.completed)


class DeferralReporterTests(OrchestratorTests):
    """Tests verifying structured deferral reasons are emitted via the reporter."""

    def test_conflict_deferral_calls_reporter_with_worktree_reason(self) -> None:
        """_find_conflict_reason must produce 'worktree in use' when beads share a feature tree
        and neither has file-scope, and bead_deferred is called with that reason."""
        epic = self.storage.create_bead(title="Epic", agent_type="planner", description="root", status=BEAD_DONE, bead_type="epic")
        root = self.storage.create_bead(title="Root", agent_type="developer", description="r", parent_id=epic.bead_id, status=BEAD_DONE)
        bead1 = self.storage.create_bead(
            title="Task A", agent_type="developer", description="a",
            parent_id=root.bead_id, dependencies=[root.bead_id],
        )
        bead2 = self.storage.create_bead(
            title="Task B", agent_type="developer", description="b",
            parent_id=root.bead_id, dependencies=[root.bead_id],
        )
        reporter = _RecordingReporter()
        runner = _FakeRunnerWithDefault(
            results={bead1.bead_id: AgentRunResult(outcome="completed", summary="done")}
        )
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))
        scheduler.run_once(max_workers=2, reporter=reporter)
        # bead2 must have been reported as deferred with a worktree-conflict reason.
        reasons = {bid: reason for bid, reason in reporter.deferred_calls}
        self.assertIn(bead2.bead_id, reasons)
        self.assertIn("worktree in use", reasons[bead2.bead_id])

    def test_file_scope_conflict_calls_reporter_with_file_scope_reason(self) -> None:
        """_find_conflict_reason must produce 'file-scope conflict' when both beads declare
        overlapping expected_files, and bead_deferred is called with that reason."""
        bead1 = self.storage.create_bead(
            title="Task A", agent_type="developer", description="a",
            expected_files=["src/shared.py"],
        )
        bead2 = self.storage.create_bead(
            title="Task B", agent_type="developer", description="b",
            expected_files=["src/shared.py"],
        )
        reporter = _RecordingReporter()
        runner = _FakeRunnerWithDefault(
            results={bead1.bead_id: AgentRunResult(outcome="completed", summary="done", expected_files=bead1.expected_files)}
        )
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))
        scheduler.run_once(max_workers=2, reporter=reporter)
        reasons = {bid: reason for bid, reason in reporter.deferred_calls}
        self.assertIn(bead2.bead_id, reasons)
        self.assertIn("file-scope conflict", reasons[bead2.bead_id])

    def test_dependency_deferral_calls_reporter_with_dependency_not_done_reason(self) -> None:
        """When a READY bead has an unsatisfied dependency, bead_deferred must be called
        with a reason string containing 'dependency not done' for that dep's bead_id."""
        dep = self.storage.create_bead(
            title="Dep", agent_type="developer", description="dep work", expected_files=["src/dep.py"]
        )
        # child is READY in storage but its dependency (dep) is not done.
        child = self.storage.create_bead(
            title="Child", agent_type="developer", description="child work",
            expected_files=["src/child.py"], dependencies=[dep.bead_id],
        )
        reporter = _RecordingReporter()
        runner = _FakeRunnerWithDefault(
            results={dep.bead_id: AgentRunResult(outcome="completed", summary="done", expected_files=dep.expected_files)}
        )
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))
        scheduler.run_once(max_workers=1, reporter=reporter)
        # The dep bead runs in cycle 1. The child bead can only run after dep is done.
        # In the first fill pass, child should be reported as deferred via reporter.
        reported_ids = [bid for bid, _ in reporter.deferred_calls]
        self.assertIn(child.bead_id, reported_ids)
        reason = next(r for bid, r in reporter.deferred_calls if bid == child.bead_id)
        self.assertIn("dependency not done", reason)
        self.assertIn(dep.bead_id, reason)

    def test_deferred_this_cycle_gate_prevents_duplicate_dep_deferral(self) -> None:
        """bead_deferred must be called at most once per bead per run_once() call,
        even when _select_beads_for_dispatch is invoked multiple times in the same cycle."""
        dep = self.storage.create_bead(
            title="Dep", agent_type="developer", description="dep", expected_files=["src/dep.py"]
        )
        child = self.storage.create_bead(
            title="Child", agent_type="developer", description="child",
            expected_files=["src/child.py"], dependencies=[dep.bead_id],
        )
        reporter = _RecordingReporter()
        runner = _FakeRunnerWithDefault(
            results={dep.bead_id: AgentRunResult(outcome="completed", summary="done", expected_files=dep.expected_files)}
        )
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))
        # max_workers=2 causes multiple fill-loop iterations; child must be reported at most once.
        scheduler.run_once(max_workers=2, reporter=reporter)
        child_reports = [(bid, r) for bid, r in reporter.deferred_calls if bid == child.bead_id]
        self.assertLessEqual(len(child_reports), 1)

    def test_dep_blocked_bead_not_added_to_result_deferred(self) -> None:
        """Dependency-blocked READY beads must NOT appear in result.deferred — only
        conflict-deferred beads should be there."""
        dep = self.storage.create_bead(
            title="Dep", agent_type="developer", description="dep", expected_files=["src/dep.py"]
        )
        child = self.storage.create_bead(
            title="Child", agent_type="developer", description="child",
            expected_files=["src/child.py"], dependencies=[dep.bead_id],
        )
        runner = _FakeRunnerWithDefault(
            results={dep.bead_id: AgentRunResult(outcome="completed", summary="done", expected_files=dep.expected_files)}
        )
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))
        result = scheduler.run_once(max_workers=1)
        # Dep-blocked child must NOT be in result.deferred (only conflict-deferred beads are).
        self.assertNotIn(child.bead_id, result.deferred)


class DepIsDoneLoggingTests(OrchestratorTests):
    """Tests for Scheduler._dep_is_done logging and error recording on load failure."""

    def test_dep_is_done_logs_warning_and_records_error_on_load_failure(self) -> None:
        depending_bead = self.storage.create_bead(
            title="Depending bead", agent_type="tester", description="dep test"
        )
        scheduler = Scheduler(self.storage, FakeRunner(), WorktreeManager(self.root, self.storage.worktrees_dir))

        with patch.object(self.storage, "load_bead", side_effect=ValueError("bead not found")):
            with patch.object(self.storage, "update_bead") as mock_update:
                with self.assertLogs("agent_takt.scheduler.core", level="WARNING") as log_ctx:
                    result = scheduler._dep_is_done("B-missing", depending_bead)

        self.assertFalse(result)
        log_text = "\n".join(log_ctx.output)
        self.assertIn("B-missing", log_text)
        error_events = [r for r in depending_bead.execution_history if r.event == "dependency_resolution_error"]
        self.assertEqual(1, len(error_events))
        self.assertEqual("B-missing", error_events[0].details["dep_id"])
        mock_update.assert_called_once_with(depending_bead)

    def test_dep_is_done_no_record_when_depending_bead_is_none(self) -> None:
        scheduler = Scheduler(self.storage, FakeRunner(), WorktreeManager(self.root, self.storage.worktrees_dir))

        with patch.object(self.storage, "load_bead", side_effect=ValueError("bead not found")):
            with patch.object(self.storage, "update_bead") as mock_update:
                with self.assertLogs("agent_takt.scheduler.core", level="WARNING"):
                    result = scheduler._dep_is_done("B-missing", None)

        self.assertFalse(result)
        mock_update.assert_not_called()


if __name__ == "__main__":
    unittest.main()
