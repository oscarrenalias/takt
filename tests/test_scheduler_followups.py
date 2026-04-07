from __future__ import annotations

import dataclasses
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_takt.config import default_config
from agent_takt.gitutils import WorktreeManager
from agent_takt.models import (
    BEAD_BLOCKED,
    BEAD_DONE,
    BEAD_READY,
    AgentRunResult,
    ExecutionRecord,
)
from agent_takt.scheduler import Scheduler
from agent_takt.storage import RepositoryStorage

# Suppress git commits for the test session (mirrors test_orchestrator.py convention).
RepositoryStorage._auto_commit = False

from helpers import FakeRunner, OrchestratorTests  # noqa: E402


class SchedulerFollowupTests(OrchestratorTests):
    # ------------------------------------------------------------------
    # Followup bead creation — standalone developer
    # ------------------------------------------------------------------

    def test_scheduler_creates_followup_beads_for_developer(self) -> None:
        bead = self.storage.create_bead(title="Implement", agent_type="developer", description="do work")
        runner = FakeRunner(
            results={
                bead.bead_id: AgentRunResult(
                    outcome="completed",
                    summary="done",
                    completed="implemented",
                    remaining="handoff",
                    risks="none",
                    expected_files=["src/app.py"],
                    touched_files=["src/app.py"],
                    changed_files=["src/app.py"],
                    updated_docs=["docs/feature.md"],
                    next_action="test and document",
                    next_agent="tester",
                    conflict_risks="Review final changed files before merge.",
                )
            },
            writes={
                bead.bead_id: {
                    "src/app.py": "print('implemented')\n",
                }
            },
        )
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))
        result = scheduler.run_once()
        self.assertEqual([bead.bead_id], result.completed)

        bead = self.storage.load_bead(bead.bead_id)
        self.assertEqual(BEAD_DONE, bead.status)
        self.assertEqual(["src/app.py"], bead.expected_files)
        self.assertEqual(["src/app.py"], bead.touched_files)
        child_ids = {child.bead_id for child in self.storage.list_beads() if child.parent_id == bead.bead_id}
        self.assertIn(f"{bead.bead_id}-test", child_ids)
        self.assertIn(f"{bead.bead_id}-docs", child_ids)
        self.assertIn(f"{bead.bead_id}-review", child_ids)
        review_bead = self.storage.load_bead(f"{bead.bead_id}-review")
        self.assertEqual(["src/app.py"], review_bead.touched_files)
        self.assertEqual("Review final changed files before merge.", review_bead.conflict_risks)
        self.assertTrue(bead.metadata.get("last_commit"))

    def test_scheduler_still_creates_auto_followups_for_standalone_developer_bead(self) -> None:
        bead = self.storage.create_bead(
            title="Standalone implement",
            agent_type="developer",
            description="single scoped change",
            expected_files=["src/standalone.py"],
        )
        runner = FakeRunner(
            results={
                bead.bead_id: AgentRunResult(
                    outcome="completed",
                    summary="done",
                    touched_files=["src/standalone.py"],
                    changed_files=["src/standalone.py"],
                )
            }
        )
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))
        scheduler.run_once()

        test_bead = self.storage.load_bead(f"{bead.bead_id}-test")
        docs_bead = self.storage.load_bead(f"{bead.bead_id}-docs")
        review_bead = self.storage.load_bead(f"{bead.bead_id}-review")
        self.assertEqual(bead.bead_id, test_bead.parent_id)
        self.assertEqual([bead.bead_id], test_bead.dependencies)
        self.assertEqual(bead.bead_id, docs_bead.parent_id)
        self.assertEqual([bead.bead_id], docs_bead.dependencies)
        self.assertEqual(bead.bead_id, review_bead.parent_id)
        self.assertEqual([bead.bead_id, test_bead.bead_id, docs_bead.bead_id], review_bead.dependencies)

    def test_developer_new_beads_create_subtasks(self) -> None:
        bead = self.storage.create_bead(title="Implement with discovered work", agent_type="developer", description="do work")
        runner = FakeRunner(
            results={
                bead.bead_id: AgentRunResult(
                    outcome="completed",
                    summary="done",
                    new_beads=[
                        {
                            "title": "Follow-up task",
                            "agent_type": "developer",
                            "description": "extra work",
                            "acceptance_criteria": ["works"],
                            "dependencies": [bead.bead_id],
                            "linked_docs": [],
                            "expected_files": ["src/extra.py"],
                            "expected_globs": [],
                        }
                    ],
                )
            }
        )
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))
        scheduler.run_once()
        subtask = self.storage.load_bead(f"{bead.bead_id}-subtask")
        self.assertEqual("developer", subtask.agent_type)
        self.assertEqual(["src/extra.py"], subtask.expected_files)
        self.assertEqual(bead.bead_id, subtask.metadata["discovered_by"])

    def test_non_developer_new_beads_are_ignored(self) -> None:
        bead = self.storage.create_bead(title="Review work", agent_type="review", description="inspect")
        runner = FakeRunner(
            results={
                bead.bead_id: AgentRunResult(
                    outcome="completed",
                    summary="done",
                    new_beads=[
                        {
                            "title": "Unexpected recursive task",
                            "agent_type": "tester",
                            "description": "should not be created",
                            "acceptance_criteria": ["works"],
                            "dependencies": [],
                            "linked_docs": [],
                            "expected_files": ["tests/test_prompts.py"],
                            "expected_globs": [],
                        }
                    ],
                )
            }
        )
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))
        result = scheduler.run_once()
        self.assertEqual([bead.bead_id], result.completed)
        child_ids = [child.bead_id for child in self.storage.list_beads() if child.parent_id == bead.bead_id]
        self.assertEqual([], child_ids)

    def test_scheduler_does_not_duplicate_followup_beads(self) -> None:
        bead = self.storage.create_bead(title="Implement", agent_type="developer", description="do work")
        runner = FakeRunner(
            results={
                bead.bead_id: AgentRunResult(
                    outcome="completed",
                    summary="done",
                )
            }
        )
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))
        scheduler.run_once()

        bead = self.storage.load_bead(bead.bead_id)
        bead.status = BEAD_READY
        self.storage.save_bead(bead)
        scheduler.run_once()

        child_ids = sorted(child.bead_id for child in self.storage.list_beads() if child.parent_id == bead.bead_id)
        self.assertEqual([f"{bead.bead_id}-docs", f"{bead.bead_id}-review", f"{bead.bead_id}-test"], child_ids)

    def test_followups_and_discovered_subtasks_inherit_feature_root(self) -> None:
        epic = self.storage.create_bead(title="Epic", agent_type="planner", description="root", status=BEAD_DONE, bead_type="epic")
        root = self.storage.create_bead(
            title="Feature root",
            agent_type="developer",
            description="feature",
            parent_id=epic.bead_id,
            expected_files=["src/root.py"],
        )
        runner = FakeRunner(
            results={
                root.bead_id: AgentRunResult(
                    outcome="completed",
                    summary="done",
                    expected_files=["src/root.py"],
                    touched_files=["src/root.py"],
                    changed_files=["src/root.py"],
                    new_beads=[
                        {
                            "title": "Follow-up task",
                            "agent_type": "developer",
                            "description": "extra work",
                            "acceptance_criteria": ["works"],
                            "dependencies": [root.bead_id],
                            "linked_docs": [],
                            "expected_files": ["src/extra.py"],
                            "expected_globs": [],
                        }
                    ],
                )
            }
        )
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))
        scheduler.run_once()
        subtask = self.storage.load_bead(f"{root.bead_id}-subtask")
        review = self.storage.load_bead(f"{root.bead_id}-review")
        self.assertEqual(root.bead_id, subtask.feature_root_id)
        self.assertEqual(root.bead_id, review.feature_root_id)
        self.assertEqual(root.execution_worktree_path, subtask.execution_worktree_path)
        self.assertEqual(root.execution_worktree_path, review.execution_worktree_path)

    # ------------------------------------------------------------------
    # Planner-owned shared followups
    # ------------------------------------------------------------------

    def test_scheduler_uses_planner_owned_shared_followups_without_creating_legacy_children(self) -> None:
        epic = self.storage.create_bead(
            title="Epic",
            agent_type="planner",
            description="root",
            status=BEAD_DONE,
            bead_type="epic",
        )
        feature = self.storage.create_bead(
            title="Feature root",
            agent_type="developer",
            description="feature",
            parent_id=epic.bead_id,
            status=BEAD_DONE,
        )
        implement_a = self.storage.create_bead(
            title="Implement A",
            agent_type="developer",
            description="first change",
            parent_id=feature.bead_id,
            dependencies=[feature.bead_id],
            expected_files=["src/a.py"],
        )
        implement_b = self.storage.create_bead(
            title="Implement B",
            agent_type="developer",
            description="second change",
            parent_id=feature.bead_id,
            dependencies=[feature.bead_id],
            expected_files=["src/b.py"],
        )
        shared_dependencies = [implement_a.bead_id, implement_b.bead_id]
        shared_test = self.storage.create_bead(
            title="Shared tester",
            agent_type="tester",
            description="validate combined implementation",
            parent_id=feature.bead_id,
            dependencies=shared_dependencies,
        )
        shared_docs = self.storage.create_bead(
            title="Shared docs",
            agent_type="documentation",
            description="document combined implementation",
            parent_id=feature.bead_id,
            dependencies=shared_dependencies,
        )
        shared_review = self.storage.create_bead(
            title="Shared review",
            agent_type="review",
            description="review combined implementation",
            parent_id=feature.bead_id,
            dependencies=shared_dependencies,
        )
        runner = FakeRunner(
            results={
                implement_a.bead_id: AgentRunResult(
                    outcome="completed",
                    summary="done",
                    touched_files=["src/a.py"],
                    changed_files=["src/a.py"],
                )
            }
        )
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))
        result = scheduler.run_once()

        self.assertEqual([implement_a.bead_id], result.completed)
        shared_test = self.storage.load_bead(shared_test.bead_id)
        shared_docs = self.storage.load_bead(shared_docs.bead_id)
        shared_review = self.storage.load_bead(shared_review.bead_id)
        self.assertEqual(["src/a.py"], shared_test.touched_files)
        self.assertEqual(["src/a.py"], shared_test.changed_files)
        self.assertEqual(["src/a.py"], shared_docs.touched_files)
        self.assertEqual(["src/a.py"], shared_review.touched_files)
        self.assertEqual(["src/a.py"], shared_review.changed_files)
        bead_ids = {bead.bead_id for bead in self.storage.list_beads()}
        self.assertNotIn(f"{implement_a.bead_id}-test", bead_ids)
        self.assertNotIn(f"{implement_a.bead_id}-docs", bead_ids)
        self.assertNotIn(f"{implement_a.bead_id}-review", bead_ids)
        self.assertEqual(feature.bead_id, shared_test.parent_id)
        self.assertEqual(feature.bead_id, shared_docs.parent_id)
        self.assertEqual(feature.bead_id, shared_review.parent_id)

    def test_scheduler_prefers_planner_owned_shared_followups_over_legacy_child_ids(self) -> None:
        epic = self.storage.create_bead(
            title="Epic",
            agent_type="planner",
            description="root",
            status=BEAD_DONE,
            bead_type="epic",
        )
        feature = self.storage.create_bead(
            title="Feature root",
            agent_type="developer",
            description="feature",
            parent_id=epic.bead_id,
            status=BEAD_DONE,
        )
        implement = self.storage.create_bead(
            title="Implement A",
            agent_type="developer",
            description="first change",
            parent_id=feature.bead_id,
            dependencies=[feature.bead_id],
            expected_files=["src/a.py"],
        )
        shared_test = self.storage.create_bead(
            title="Shared tester",
            agent_type="tester",
            description="validate combined implementation",
            parent_id=feature.bead_id,
            dependencies=[implement.bead_id],
        )
        shared_docs = self.storage.create_bead(
            title="Shared docs",
            agent_type="documentation",
            description="document combined implementation",
            parent_id=feature.bead_id,
            dependencies=[implement.bead_id],
        )
        shared_review = self.storage.create_bead(
            title="Shared review",
            agent_type="review",
            description="review combined implementation",
            parent_id=feature.bead_id,
            dependencies=[implement.bead_id, shared_test.bead_id, shared_docs.bead_id],
        )
        self.storage.create_bead(
            bead_id=f"{implement.bead_id}-test",
            title="Legacy tester",
            agent_type="tester",
            description="legacy followup",
            parent_id=implement.bead_id,
            dependencies=[implement.bead_id],
        )
        self.storage.create_bead(
            bead_id=f"{implement.bead_id}-docs",
            title="Legacy docs",
            agent_type="documentation",
            description="legacy followup",
            parent_id=implement.bead_id,
            dependencies=[implement.bead_id],
        )
        self.storage.create_bead(
            bead_id=f"{implement.bead_id}-review",
            title="Legacy review",
            agent_type="review",
            description="legacy followup",
            parent_id=implement.bead_id,
            dependencies=[implement.bead_id],
        )

        scheduler = Scheduler(self.storage, FakeRunner(), WorktreeManager(self.root, self.storage.worktrees_dir))
        followups = scheduler._existing_followups_for(implement, include_planner_owned=True)

        self.assertEqual(shared_test.bead_id, followups["tester"].bead_id)
        self.assertEqual(shared_docs.bead_id, followups["documentation"].bead_id)
        self.assertEqual(shared_review.bead_id, followups["review"].bead_id)

    def test_scheduler_does_not_backfill_legacy_children_when_shared_followups_exist(self) -> None:
        epic = self.storage.create_bead(
            title="Epic",
            agent_type="planner",
            description="root",
            status=BEAD_DONE,
            bead_type="epic",
        )
        feature = self.storage.create_bead(
            title="Feature root",
            agent_type="developer",
            description="feature",
            parent_id=epic.bead_id,
            status=BEAD_DONE,
        )
        implement = self.storage.create_bead(
            title="Implement A",
            agent_type="developer",
            description="first change",
            parent_id=feature.bead_id,
            dependencies=[feature.bead_id],
            expected_files=["src/a.py"],
        )
        shared_test = self.storage.create_bead(
            title="Shared tester",
            agent_type="tester",
            description="validate combined implementation",
            parent_id=feature.bead_id,
            dependencies=[implement.bead_id],
        )
        runner = FakeRunner(
            results={
                implement.bead_id: AgentRunResult(
                    outcome="completed",
                    summary="done",
                    touched_files=["src/a.py"],
                    changed_files=["src/a.py"],
                )
            }
        )

        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))
        scheduler.run_once()

        bead_ids = {bead.bead_id for bead in self.storage.list_beads()}
        self.assertIn(shared_test.bead_id, bead_ids)
        self.assertNotIn(f"{implement.bead_id}-test", bead_ids)
        self.assertNotIn(f"{implement.bead_id}-docs", bead_ids)
        self.assertNotIn(f"{implement.bead_id}-review", bead_ids)

    def test_scheduler_ignores_nested_feature_followups_when_shared_root_followups_exist(self) -> None:
        epic = self.storage.create_bead(
            title="Epic",
            agent_type="planner",
            description="root",
            status=BEAD_DONE,
            bead_type="epic",
        )
        feature = self.storage.create_bead(
            title="Feature root",
            agent_type="developer",
            description="feature",
            parent_id=epic.bead_id,
            status=BEAD_DONE,
        )
        implement = self.storage.create_bead(
            title="Implement A",
            agent_type="developer",
            description="first change",
            parent_id=feature.bead_id,
            dependencies=[feature.bead_id],
            expected_files=["src/a.py"],
        )
        shared_test = self.storage.create_bead(
            title="Shared tester",
            agent_type="tester",
            description="validate combined implementation",
            parent_id=feature.bead_id,
            dependencies=[implement.bead_id],
        )
        self.storage.create_bead(
            title="Nested tester",
            agent_type="tester",
            description="nested followup that should not shadow shared root followups",
            parent_id=implement.bead_id,
            dependencies=[implement.bead_id],
        )

        scheduler = Scheduler(self.storage, FakeRunner(), WorktreeManager(self.root, self.storage.worktrees_dir))

        followup = scheduler._planner_owned_followup(implement, "tester")

        self.assertIsNotNone(followup)
        self.assertEqual(shared_test.bead_id, followup.bead_id)

    # ------------------------------------------------------------------
    # Shared followup scope population
    # ------------------------------------------------------------------

    def test_populate_shared_followup_touched_files_aggregates_multiple_developers(self) -> None:
        # Set up two developer beads that are both done with different touched/changed files.
        dev_a = self.storage.create_bead(
            title="Implement A",
            agent_type="developer",
            description="first change",
            expected_files=["src/a.py"],
        )
        dev_a.status = BEAD_DONE
        dev_a.handoff_summary.touched_files = ["src/a.py", "src/shared.py"]
        dev_a.handoff_summary.changed_files = ["src/a.py"]
        self.storage.save_bead(dev_a)

        dev_b = self.storage.create_bead(
            title="Implement B",
            agent_type="developer",
            description="second change",
            expected_files=["src/b.py"],
        )
        dev_b.status = BEAD_DONE
        dev_b.handoff_summary.touched_files = ["src/b.py", "src/shared.py"]
        dev_b.handoff_summary.changed_files = ["src/b.py", "src/shared.py"]
        self.storage.save_bead(dev_b)

        # Shared tester bead depends on both developer beads.
        shared_test = self.storage.create_bead(
            title="Shared tester",
            agent_type="tester",
            description="validate combined implementation",
            dependencies=[dev_a.bead_id, dev_b.bead_id],
        )

        scheduler = Scheduler(self.storage, FakeRunner(), WorktreeManager(self.root, self.storage.worktrees_dir))
        scheduler._populate_shared_followup_touched_files(shared_test)

        shared_test = self.storage.load_bead(shared_test.bead_id)
        # All files from both developers' touched_files and changed_files should be present.
        self.assertEqual(
            sorted(["src/a.py", "src/b.py", "src/shared.py"]),
            sorted(shared_test.touched_files),
        )
        self.assertEqual(
            sorted(["src/a.py", "src/b.py", "src/shared.py"]),
            sorted(shared_test.changed_files),
        )

    def test_populate_shared_followup_touched_files_deduplicates_common_files(self) -> None:
        # Both developer beads touch the same file — it should appear only once.
        dev_a = self.storage.create_bead(
            title="Implement A",
            agent_type="developer",
            description="first change",
        )
        dev_a.status = BEAD_DONE
        dev_a.handoff_summary.touched_files = ["src/common.py", "src/a.py"]
        dev_a.handoff_summary.changed_files = ["src/common.py"]
        self.storage.save_bead(dev_a)

        dev_b = self.storage.create_bead(
            title="Implement B",
            agent_type="developer",
            description="second change",
        )
        dev_b.status = BEAD_DONE
        dev_b.handoff_summary.touched_files = ["src/common.py", "src/b.py"]
        dev_b.handoff_summary.changed_files = ["src/common.py", "src/b.py"]
        self.storage.save_bead(dev_b)

        shared_review = self.storage.create_bead(
            title="Shared review",
            agent_type="review",
            description="review combined implementation",
            dependencies=[dev_a.bead_id, dev_b.bead_id],
        )

        scheduler = Scheduler(self.storage, FakeRunner(), WorktreeManager(self.root, self.storage.worktrees_dir))
        scheduler._populate_shared_followup_touched_files(shared_review)

        shared_review = self.storage.load_bead(shared_review.bead_id)
        # src/common.py appears in both but must only be listed once.
        self.assertEqual(
            sorted(["src/a.py", "src/b.py", "src/common.py"]),
            sorted(shared_review.touched_files),
        )
        self.assertEqual(
            sorted(["src/a.py", "src/b.py", "src/common.py"]),
            sorted(shared_review.changed_files),
        )

    def test_populate_shared_followup_touched_files_aggregates_single_done_dependency(self) -> None:
        # A single done dependency with touched_files should populate the shared followup.
        dev_a = self.storage.create_bead(
            title="Implement A",
            agent_type="developer",
            description="first change",
        )
        dev_a.status = BEAD_DONE
        dev_a.handoff_summary.touched_files = ["src/a.py"]
        dev_a.handoff_summary.changed_files = ["src/a.py"]
        self.storage.save_bead(dev_a)

        dev_b = self.storage.create_bead(
            title="Implement B",
            agent_type="developer",
            description="second change",
        )
        # dev_b is NOT done — still open.
        self.storage.save_bead(dev_b)

        shared_test = self.storage.create_bead(
            title="Shared tester",
            agent_type="tester",
            description="validate combined implementation",
            dependencies=[dev_a.bead_id, dev_b.bead_id],
        )

        scheduler = Scheduler(self.storage, FakeRunner(), WorktreeManager(self.root, self.storage.worktrees_dir))
        scheduler._populate_shared_followup_touched_files(shared_test)

        shared_test = self.storage.load_bead(shared_test.bead_id)
        self.assertEqual(["src/a.py"], shared_test.touched_files)
        self.assertEqual(["src/a.py"], shared_test.changed_files)

    def test_populate_shared_followup_touched_files_includes_tester_dependency_files(self) -> None:
        dev = self.storage.create_bead(
            title="Implement A",
            agent_type="developer",
            description="first change",
        )
        dev.status = BEAD_DONE
        dev.handoff_summary.touched_files = ["src/a.py"]
        dev.handoff_summary.changed_files = ["src/a.py"]
        self.storage.save_bead(dev)

        tester = self.storage.create_bead(
            title="Shared tester",
            agent_type="tester",
            description="validate implementation",
            dependencies=[dev.bead_id],
        )
        tester.status = BEAD_DONE
        tester.handoff_summary.touched_files = ["tests/test_a.py"]
        tester.handoff_summary.changed_files = ["tests/test_a.py", "src/a.py"]
        self.storage.save_bead(tester)

        shared_review = self.storage.create_bead(
            title="Shared review",
            agent_type="review",
            description="review combined implementation",
            dependencies=[dev.bead_id, tester.bead_id],
        )

        scheduler = Scheduler(self.storage, FakeRunner(), WorktreeManager(self.root, self.storage.worktrees_dir))
        scheduler._populate_shared_followup_touched_files(shared_review)

        shared_review = self.storage.load_bead(shared_review.bead_id)
        self.assertEqual(
            sorted(["src/a.py", "tests/test_a.py"]),
            sorted(shared_review.touched_files),
        )
        self.assertEqual(
            sorted(["src/a.py", "tests/test_a.py"]),
            sorted(shared_review.changed_files),
        )

    def test_populate_shared_followup_touched_files_skips_when_done_deps_have_no_touched_files(self) -> None:
        # Done dependencies with only changed_files should not populate shared scope.
        tester = self.storage.create_bead(
            title="Shared tester",
            agent_type="tester",
            description="validate implementation",
        )
        tester.status = BEAD_DONE
        tester.handoff_summary.changed_files = ["tests/test_a.py"]
        self.storage.save_bead(tester)

        shared_review = self.storage.create_bead(
            title="Shared review",
            agent_type="review",
            description="review combined implementation",
            dependencies=[tester.bead_id],
        )

        scheduler = Scheduler(self.storage, FakeRunner(), WorktreeManager(self.root, self.storage.worktrees_dir))
        scheduler._populate_shared_followup_touched_files(shared_review)

        shared_review = self.storage.load_bead(shared_review.bead_id)
        self.assertEqual([], shared_review.touched_files)
        self.assertEqual([], shared_review.changed_files)

    # ------------------------------------------------------------------
    # Corrective bead creation and lifecycle
    # ------------------------------------------------------------------

    def test_scheduler_plans_corrective_bead_when_blocked_bead_has_next_agent(self) -> None:
        bead = self.storage.create_bead(title="Review", agent_type="review", description="inspect")
        bead.status = BEAD_BLOCKED
        bead.block_reason = "Needs corrective implementation."
        bead.handoff_summary.next_agent = "developer"
        self.storage.save_bead(bead)
        scheduler = Scheduler(self.storage, FakeRunner(results={}), WorktreeManager(self.root, self.storage.worktrees_dir))
        scheduler.run_once(max_workers=0)
        bead = self.storage.load_bead(bead.bead_id)
        self.assertEqual("review", bead.agent_type)
        self.assertEqual(BEAD_BLOCKED, bead.status)
        corrective_id = bead.metadata.get("auto_corrective_bead_id", "")
        self.assertTrue(corrective_id)
        corrective = self.storage.load_bead(corrective_id)
        self.assertEqual("developer", corrective.agent_type)

    def test_scheduler_does_not_reassign_blocked_bead_to_scheduler_agent(self) -> None:
        bead = self.storage.create_bead(title="Review", agent_type="review", description="inspect")
        bead.status = BEAD_BLOCKED
        bead.block_reason = "Needs follow-up."
        bead.handoff_summary.next_agent = "scheduler"
        self.storage.save_bead(bead)
        scheduler = Scheduler(self.storage, FakeRunner(results={}), WorktreeManager(self.root, self.storage.worktrees_dir))
        scheduler.run_once(max_workers=0)
        bead = self.storage.load_bead(bead.bead_id)
        self.assertEqual("review", bead.agent_type)
        corrective_id = bead.metadata.get("auto_corrective_bead_id", "")
        self.assertTrue(corrective_id)

    def test_scheduler_repairs_invalid_blocked_worker_agent_type_before_retry(self) -> None:
        bead = self.storage.create_bead(title="Test work", agent_type="tester", description="validate", bead_id="B9000-test")
        bead.status = BEAD_BLOCKED
        bead.agent_type = "scheduler"
        bead.block_reason = "Unsupported agent type for worker prompt: scheduler"
        bead.metadata["auto_corrective_bead_id"] = "B9000-test-corrective"
        self.storage.save_bead(bead)
        corrective = self.storage.create_bead(
            title="corrective",
            agent_type="developer",
            description="fix",
            bead_id="B9000-test-corrective",
            parent_id=bead.bead_id,
            status=BEAD_DONE,
            metadata={"auto_corrective_for": bead.bead_id},
        )
        self.assertEqual(BEAD_DONE, corrective.status)
        runner = FakeRunner(
            results={
                bead.bead_id: AgentRunResult(
                    outcome="completed",
                    summary="tester rerun succeeded",
                )
            }
        )
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))
        result = scheduler.run_once()
        self.assertEqual([bead.bead_id], result.completed)
        bead = self.storage.load_bead(bead.bead_id)
        self.assertEqual("tester", bead.agent_type)
        self.assertEqual(BEAD_DONE, bead.status)

    def test_scheduler_reevaluates_transient_blocked_bead_by_retrying(self) -> None:
        bead = self.storage.create_bead(title="Test", agent_type="tester", description="validate")
        bead.status = BEAD_BLOCKED
        bead.block_reason = "401 Unauthorized: Missing bearer or basic authentication in header"
        self.storage.save_bead(bead)
        runner = FakeRunner(
            results={
                bead.bead_id: AgentRunResult(
                    outcome="completed",
                    summary="tests passed",
                )
            }
        )
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))
        result = scheduler.run_once()
        self.assertEqual([bead.bead_id], result.completed)
        bead = self.storage.load_bead(bead.bead_id)
        self.assertEqual(BEAD_DONE, bead.status)

    def test_scheduler_creates_corrective_bead_for_non_transient_block(self) -> None:
        bead = self.storage.create_bead(title="Review", agent_type="review", description="inspect")
        bead.status = BEAD_BLOCKED
        bead.block_reason = "Spec mismatch in plain output format."
        bead.handoff_summary.remaining = "Fix formatting and preserve JSON behavior."
        self.storage.save_bead(bead)
        scheduler = Scheduler(self.storage, FakeRunner(results={}), WorktreeManager(self.root, self.storage.worktrees_dir))
        scheduler.run_once(max_workers=0)
        bead = self.storage.load_bead(bead.bead_id)
        corrective_id = bead.metadata.get("auto_corrective_bead_id", "")
        self.assertTrue(corrective_id)
        corrective = self.storage.load_bead(corrective_id)
        self.assertEqual(bead.bead_id, corrective.parent_id)
        self.assertEqual("developer", corrective.agent_type)
        self.assertEqual(BEAD_READY, corrective.status)
        self.assertEqual(bead.bead_id, corrective.metadata.get("auto_corrective_for"))

    def test_corrective_bead_completion_does_not_spawn_auto_followups(self) -> None:
        corrective = self.storage.create_bead(
            title="Corrective",
            agent_type="developer",
            description="fix",
            metadata={"auto_corrective_for": "B1234"},
        )
        runner = FakeRunner(
            results={
                corrective.bead_id: AgentRunResult(
                    outcome="completed",
                    summary="fixed",
                )
            },
            writes={corrective.bead_id: {"src/fix.py": "print('fixed')\n"}},
        )
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))
        result = scheduler.run_once()
        self.assertEqual([corrective.bead_id], result.completed)
        children = [item for item in self.storage.list_beads() if item.parent_id == corrective.bead_id]
        self.assertEqual([], children)

    def test_corrective_completion_requeues_blocked_tester_parent_immediately(self) -> None:
        parent = self.storage.create_bead(title="Test", agent_type="tester", description="validate")
        parent.status = BEAD_BLOCKED
        parent.block_reason = "Waiting for corrective implementation."
        self.storage.save_bead(parent)
        corrective = self.storage.create_bead(
            title="Corrective",
            agent_type="developer",
            description="fix",
            bead_id="B1234-corrective",
            parent_id=parent.bead_id,
            metadata={"auto_corrective_for": parent.bead_id},
        )
        runner = FakeRunner(
            results={
                corrective.bead_id: AgentRunResult(
                    outcome="completed",
                    summary="fixed",
                )
            },
            writes={corrective.bead_id: {"src/fix.py": "print('fixed')\n"}},
        )
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))
        result = scheduler.run_once()
        self.assertEqual([corrective.bead_id], result.completed)
        corrective = self.storage.load_bead(corrective.bead_id)
        parent = self.storage.load_bead(parent.bead_id)
        self.assertEqual(BEAD_READY, parent.status)
        self.assertEqual("", parent.block_reason)
        self.assertEqual(corrective.bead_id, parent.metadata.get("last_corrective_retry_source"))
        self.assertEqual(
            corrective.metadata.get("last_commit", ""),
            parent.metadata.get("last_corrective_retry_commit", ""),
        )
        self.assertEqual("retried", parent.execution_history[-1].event)
        self.assertIn(corrective.bead_id, parent.execution_history[-1].summary)

    def test_corrective_completion_requeues_blocked_review_parent_immediately(self) -> None:
        parent = self.storage.create_bead(title="Review", agent_type="review", description="inspect")
        parent.status = BEAD_BLOCKED
        parent.block_reason = "Waiting for corrective implementation."
        self.storage.save_bead(parent)
        corrective = self.storage.create_bead(
            title="Corrective",
            agent_type="developer",
            description="fix",
            bead_id="B1235-corrective",
            parent_id=parent.bead_id,
            metadata={"auto_corrective_for": parent.bead_id},
        )
        runner = FakeRunner(
            results={
                corrective.bead_id: AgentRunResult(
                    outcome="completed",
                    summary="fixed",
                )
            },
            writes={corrective.bead_id: {"src/fix.py": "print('fixed')\n"}},
        )
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))
        result = scheduler.run_once()
        self.assertEqual([corrective.bead_id], result.completed)
        parent = self.storage.load_bead(parent.bead_id)
        self.assertEqual(BEAD_READY, parent.status)
        self.assertEqual("", parent.block_reason)
        self.assertEqual(corrective.bead_id, parent.metadata.get("last_corrective_retry_source"))

    def test_scheduler_does_not_duplicate_auto_corrective_beads(self) -> None:
        bead = self.storage.create_bead(title="Review", agent_type="review", description="inspect")
        bead.status = BEAD_BLOCKED
        bead.block_reason = "Needs corrective implementation."
        self.storage.save_bead(bead)
        scheduler = Scheduler(self.storage, FakeRunner(results={}), WorktreeManager(self.root, self.storage.worktrees_dir))
        scheduler.run_once(max_workers=0)
        scheduler.run_once(max_workers=0)
        children = [item for item in self.storage.list_beads() if item.parent_id == bead.bead_id]
        corrective_children = [item for item in children if item.metadata.get("auto_corrective_for") == bead.bead_id]
        self.assertEqual(1, len(corrective_children))

    def test_scheduler_does_not_create_recursive_corrective_for_corrective_descendant(self) -> None:
        root = self.storage.create_bead(
            title="Corrective root",
            agent_type="developer",
            description="fix",
            bead_id="B9100-corrective",
            metadata={"auto_corrective_for": "B9100"},
        )
        child = self.storage.create_bead(
            title="Corrective review",
            agent_type="review",
            description="review corrective",
            bead_id="B9100-corrective-review",
            parent_id=root.bead_id,
            status=BEAD_BLOCKED,
        )
        child.block_reason = "Still blocked"
        self.storage.save_bead(child)
        scheduler = Scheduler(self.storage, FakeRunner(results={}), WorktreeManager(self.root, self.storage.worktrees_dir))
        scheduler.run_once(max_workers=0)
        descendants = [b for b in self.storage.list_beads() if b.parent_id == child.bead_id]
        self.assertEqual([], descendants)

    def test_scheduler_retries_parent_after_auto_corrective_completes(self) -> None:
        bead = self.storage.create_bead(title="Review", agent_type="review", description="inspect")
        bead.status = BEAD_BLOCKED
        bead.block_reason = "Needs corrective implementation."
        self.storage.save_bead(bead)
        scheduler = Scheduler(self.storage, FakeRunner(results={}), WorktreeManager(self.root, self.storage.worktrees_dir))
        scheduler.run_once(max_workers=0)
        bead = self.storage.load_bead(bead.bead_id)
        corrective_id = bead.metadata["auto_corrective_bead_id"]
        corrective = self.storage.load_bead(corrective_id)
        corrective.status = BEAD_DONE
        self.storage.save_bead(corrective)
        runner = FakeRunner(
            results={
                bead.bead_id: AgentRunResult(
                    outcome="completed",
                    summary="review pass after corrective",
                )
            }
        )
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))
        result = scheduler.run_once()
        self.assertEqual([bead.bead_id], result.completed)
        bead = self.storage.load_bead(bead.bead_id)
        self.assertEqual(BEAD_DONE, bead.status)

    def test_scheduler_does_not_reretry_same_bead_after_same_corrective(self) -> None:
        bead = self.storage.create_bead(title="Test", agent_type="tester", description="validate")
        bead.status = BEAD_BLOCKED
        bead.block_reason = "Still unresolved"
        bead.metadata["auto_corrective_bead_id"] = "B9200-corrective"
        bead.execution_history.append(
            ExecutionRecord(
                timestamp="2026-03-26T00:00:00+00:00",
                event="retried",
                agent_type="scheduler",
                summary="Requeued blocked bead after corrective bead B9200-corrective completed",
            )
        )
        self.storage.save_bead(bead)
        corrective = self.storage.create_bead(
            title="corrective",
            agent_type="developer",
            description="fix",
            bead_id="B9200-corrective",
            parent_id=bead.bead_id,
            status=BEAD_DONE,
        )
        self.assertEqual(BEAD_DONE, corrective.status)
        scheduler = Scheduler(self.storage, FakeRunner(results={}), WorktreeManager(self.root, self.storage.worktrees_dir))
        result = scheduler.run_once(max_workers=0)
        self.assertEqual([], result.started)
        bead = self.storage.load_bead(bead.bead_id)
        self.assertEqual(BEAD_BLOCKED, bead.status)

    def test_scheduler_allows_second_corrective_after_first_retry_still_blocked(self) -> None:
        bead = self.storage.create_bead(title="Test", agent_type="tester", description="validate")
        bead.status = BEAD_BLOCKED
        bead.block_reason = "Still unresolved after first corrective"
        bead.metadata["auto_corrective_bead_id"] = "B9300-corrective"
        bead.execution_history.append(
            ExecutionRecord(
                timestamp="2026-03-26T00:00:00+00:00",
                event="retried",
                agent_type="scheduler",
                summary="Requeued blocked bead after corrective bead B9300-corrective completed",
            )
        )
        self.storage.save_bead(bead)
        self.storage.create_bead(
            title="corrective",
            agent_type="developer",
            description="first fix",
            bead_id="B9300-corrective",
            parent_id=bead.bead_id,
            status=BEAD_DONE,
            metadata={"auto_corrective_for": bead.bead_id},
        )
        scheduler = Scheduler(self.storage, FakeRunner(results={}), WorktreeManager(self.root, self.storage.worktrees_dir))
        scheduler.run_once(max_workers=0)
        children = [b for b in self.storage.list_beads() if b.parent_id == bead.bead_id and b.metadata.get("auto_corrective_for") == bead.bead_id]
        self.assertEqual(2, len(children))
        self.assertTrue(any(child.bead_id != "B9300-corrective" for child in children))

    def test_scheduler_escalates_to_human_after_two_correctives(self) -> None:
        bead = self.storage.create_bead(title="Test", agent_type="tester", description="validate")
        bead.status = BEAD_BLOCKED
        bead.block_reason = "Still unresolved after two correctives"
        bead.metadata["auto_corrective_bead_id"] = "B9400-corrective-2"
        bead.execution_history.append(
            ExecutionRecord(
                timestamp="2026-03-26T00:00:00+00:00",
                event="retried",
                agent_type="scheduler",
                summary="Requeued blocked bead after corrective bead B9400-corrective-2 completed",
            )
        )
        self.storage.save_bead(bead)
        self.storage.create_bead(
            title="corrective1",
            agent_type="developer",
            description="fix 1",
            bead_id="B9400-corrective",
            parent_id=bead.bead_id,
            status=BEAD_DONE,
            metadata={"auto_corrective_for": bead.bead_id},
        )
        self.storage.create_bead(
            title="corrective2",
            agent_type="developer",
            description="fix 2",
            bead_id="B9400-corrective-2",
            parent_id=bead.bead_id,
            status=BEAD_DONE,
            metadata={"auto_corrective_for": bead.bead_id},
        )
        cfg = default_config()
        cfg = dataclasses.replace(cfg, scheduler=dataclasses.replace(cfg.scheduler, max_corrective_attempts=2))
        scheduler = Scheduler(self.storage, FakeRunner(results={}), WorktreeManager(self.root, self.storage.worktrees_dir), config=cfg)
        scheduler.run_once(max_workers=0)
        bead = self.storage.load_bead(bead.bead_id)
        self.assertTrue(bead.metadata.get("needs_human_intervention"))
        self.assertIn("Exceeded corrective attempt budget", bead.metadata.get("escalation_reason", ""))

    def test_review_needs_changes_creates_corrective_immediately(self) -> None:
        bead = self.storage.create_bead(
            title="Review work",
            agent_type="review",
            description="inspect",
            touched_files=["src/agent_takt/skills.py"],
            changed_files=["src/agent_takt/skills.py", "docs/multi-backend-agents.md"],
        )
        runner = FakeRunner(
            results={
                bead.bead_id: AgentRunResult(
                    outcome="completed",
                    summary="Review found required changes",
                    verdict="needs_changes",
                    findings_count=3,
                    next_agent="developer",
                )
            }
        )
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))
        result = scheduler.run_once()
        self.assertEqual([bead.bead_id], result.blocked)
        self.assertEqual(1, len(result.correctives_created))
        corrective_id = result.correctives_created[0]
        corrective = self.storage.load_bead(corrective_id)
        self.assertEqual("developer", corrective.agent_type)
        self.assertEqual(BEAD_READY, corrective.status)
        self.assertEqual(bead.bead_id, corrective.parent_id)
        self.assertEqual(bead.bead_id, corrective.metadata.get("auto_corrective_for"))
        self.assertEqual(["src/agent_takt/skills.py"], corrective.touched_files)
        self.assertEqual(
            ["src/agent_takt/skills.py", "docs/multi-backend-agents.md"],
            corrective.changed_files,
        )
        bead = self.storage.load_bead(bead.bead_id)
        self.assertEqual(corrective_id, bead.metadata.get("auto_corrective_bead_id"))
        self.assertEqual(["src/agent_takt/skills.py"], bead.touched_files)
        self.assertEqual(
            ["src/agent_takt/skills.py", "docs/multi-backend-agents.md"],
            bead.handoff_summary.changed_files,
        )

    def test_tester_needs_changes_creates_corrective_immediately(self) -> None:
        bead = self.storage.create_bead(title="Test work", agent_type="tester", description="validate")
        runner = FakeRunner(
            results={
                bead.bead_id: AgentRunResult(
                    outcome="completed",
                    summary="Tester found failures",
                    verdict="needs_changes",
                    findings_count=2,
                    next_agent="developer",
                )
            }
        )
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))
        result = scheduler.run_once()
        self.assertEqual([bead.bead_id], result.blocked)
        self.assertEqual(1, len(result.correctives_created))
        corrective_id = result.correctives_created[0]
        corrective = self.storage.load_bead(corrective_id)
        self.assertEqual("developer", corrective.agent_type)
        self.assertEqual(bead.bead_id, corrective.metadata.get("auto_corrective_for"))

    def test_developer_needs_changes_does_not_create_immediate_corrective(self) -> None:
        bead = self.storage.create_bead(title="Dev work", agent_type="developer", description="implement")
        runner = FakeRunner(
            results={
                bead.bead_id: AgentRunResult(
                    outcome="blocked",
                    summary="Blocked on external dependency",
                    verdict="needs_changes",
                    block_reason="External API unavailable",
                )
            }
        )
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))
        result = scheduler.run_once()
        self.assertEqual([bead.bead_id], result.blocked)
        self.assertEqual([], result.correctives_created)

    # ------------------------------------------------------------------
    # Corrective scope inheritance
    # ------------------------------------------------------------------

    def test_corrective_bead_inherits_changed_scope_from_review(self) -> None:
        bead = self.storage.create_bead(
            title="Review work",
            agent_type="review",
            description="inspect",
            expected_files=["src/agent_takt/scheduler.py"],
            touched_files=["src/agent_takt/scheduler.py"],
            changed_files=["src/agent_takt/scheduler.py", "tests/test_orchestrator.py"],
        )
        bead.status = BEAD_BLOCKED
        bead.block_reason = "Needs a bounded corrective fix."
        bead.handoff_summary.next_agent = "developer"
        self.storage.save_bead(bead)

        scheduler = Scheduler(self.storage, FakeRunner(results={}), WorktreeManager(self.root, self.storage.worktrees_dir))
        scheduler.run_once(max_workers=0)

        bead = self.storage.load_bead(bead.bead_id)
        corrective = self.storage.load_bead(bead.metadata["auto_corrective_bead_id"])
        self.assertEqual(["src/agent_takt/scheduler.py"], corrective.touched_files)
        self.assertEqual(
            ["src/agent_takt/scheduler.py", "tests/test_orchestrator.py"],
            corrective.changed_files,
        )

    def test_corrective_bead_backfills_scope_from_expected_files_when_review_scope_is_empty(self) -> None:
        bead = self.storage.create_bead(
            title="Review work",
            agent_type="review",
            description="inspect",
            expected_files=[
                "templates/agents/planner.md",
                "src/agent_takt/prompts.py",
                "src/agent_takt/scheduler.py",
                "tests/test_orchestrator.py",
            ],
        )
        bead.status = BEAD_BLOCKED
        bead.block_reason = "Needs a bounded corrective fix."
        bead.handoff_summary.next_agent = "developer"
        self.storage.save_bead(bead)

        scheduler = Scheduler(self.storage, FakeRunner(results={}), WorktreeManager(self.root, self.storage.worktrees_dir))
        scheduler.run_once(max_workers=0)

        bead = self.storage.load_bead(bead.bead_id)
        corrective = self.storage.load_bead(bead.metadata["auto_corrective_bead_id"])
        self.assertEqual(bead.expected_files, corrective.touched_files)
        self.assertEqual(bead.expected_files, corrective.changed_files)


if __name__ == "__main__":
    unittest.main()
