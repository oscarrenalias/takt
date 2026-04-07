from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_takt.gitutils import GitError, WorktreeManager
from agent_takt.models import (
    BEAD_BLOCKED,
    BEAD_DONE,
    BEAD_READY,
    AgentRunResult,
)
from agent_takt.scheduler import Scheduler
from agent_takt.storage import RepositoryStorage

# Suppress git commits for the test session (mirrors test_orchestrator.py convention).
RepositoryStorage._auto_commit = False

from helpers import FakeRunner, OrchestratorTests  # noqa: E402


class SchedulerExecutionTests(OrchestratorTests):
    # ------------------------------------------------------------------
    # Worktree setup
    # ------------------------------------------------------------------

    def test_worktree_manager_creates_branch_and_directory(self) -> None:
        manager = WorktreeManager(self.root, self.storage.worktrees_dir)
        worktree = manager.ensure_worktree("B0001", "bead/b0001")
        self.assertTrue(worktree.exists())

    def test_default_execution_branch_name_uuid_format(self) -> None:
        # UUID-format IDs (B-xxxxxxxx) should produce lowercase branch names
        branch = self.storage.default_execution_branch_name("B-a7bc3f91")
        self.assertEqual("feature/b-a7bc3f91", branch)

    def test_default_execution_branch_name_child_uuid_format(self) -> None:
        # Child bead IDs with UUID prefix (B-xxxxxxxx-suffix) should also lowercase correctly
        branch = self.storage.default_execution_branch_name("B-a7bc3f91")
        self.assertTrue(branch.startswith("feature/"))
        self.assertEqual(branch, branch.lower())

    def test_worktree_path_with_hyphenated_bead_id(self) -> None:
        # worktree_path should preserve case and accept hyphenated IDs
        manager = WorktreeManager(self.root, self.storage.worktrees_dir)
        path = manager.worktree_path("B-a7bc3f91")
        self.assertEqual(self.storage.worktrees_dir / "B-a7bc3f91", path)

    def test_worktree_manager_uuid_format_creates_branch_and_directory(self) -> None:
        # ensure_worktree and merge_branch work with the new B-xxxxxxxx format
        manager = WorktreeManager(self.root, self.storage.worktrees_dir)
        branch = self.storage.default_execution_branch_name("B-a7bc3f91")
        worktree = manager.ensure_worktree("B-a7bc3f91", branch)
        self.assertTrue(worktree.exists())
        self.assertEqual(worktree, self.storage.worktrees_dir / "B-a7bc3f91")

    def test_scheduler_blocks_bead_when_git_is_unavailable(self) -> None:
        subprocess.run(["rm", "-rf", ".git"], cwd=self.root, check=True)
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
        result = scheduler.run_once()
        self.assertEqual([bead.bead_id], result.blocked)
        bead = self.storage.load_bead(bead.bead_id)
        self.assertEqual(BEAD_BLOCKED, bead.status)

    def test_scheduler_blocks_when_auto_commit_fails(self) -> None:
        bead = self.storage.create_bead(title="Implement", agent_type="developer", description="do work")
        runner = FakeRunner(
            results={
                bead.bead_id: AgentRunResult(
                    outcome="completed",
                    summary="done",
                )
            }
        )
        with patch.object(WorktreeManager, "commit_all", side_effect=GitError("commit failed")):
            scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))
            result = scheduler.run_once()
        self.assertEqual([bead.bead_id], result.blocked)
        self.assertEqual([], result.completed)
        bead = self.storage.load_bead(bead.bead_id)
        self.assertEqual(BEAD_BLOCKED, bead.status)
        self.assertIn("Auto-commit failed", bead.block_reason)

    def test_review_bead_runs_in_feature_worktree_when_feature_root_exists(self) -> None:
        epic = self.storage.create_bead(
            title="Epic",
            agent_type="planner",
            description="epic",
            status=BEAD_DONE,
            bead_type="epic",
        )
        feature = self.storage.create_bead(
            title="Feature root",
            agent_type="planner",
            description="feature",
            status=BEAD_DONE,
            bead_type="feature",
            parent_id=epic.bead_id,
        )
        bead = self.storage.create_bead(
            title="Review work",
            agent_type="review",
            description="inspect",
            parent_id=feature.bead_id,
        )
        runner = FakeRunner(results={bead.bead_id: AgentRunResult(outcome="completed", summary="done")})
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))

        result = scheduler.run_once()

        self.assertEqual([bead.bead_id], result.completed)
        expected_worktree = self.storage.worktrees_dir / feature.bead_id
        self.assertEqual(expected_worktree, runner.last_workdir_by_bead[bead.bead_id])

    # ------------------------------------------------------------------
    # Agent invocation — result persistence
    # ------------------------------------------------------------------

    def test_scheduler_persists_git_detected_touched_files(self) -> None:
        bead = self.storage.create_bead(
            title="Implement touched files",
            agent_type="developer",
            description="do work",
            expected_files=["src/new_file.py"],
        )
        runner = FakeRunner(
            results={
                bead.bead_id: AgentRunResult(
                    outcome="completed",
                    summary="done",
                    expected_files=["src/new_file.py"],
                    touched_files=[],
                    changed_files=[],
                )
            },
            writes={
                bead.bead_id: {
                    "src/new_file.py": "print('hello')\n",
                }
            },
        )
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))
        result = scheduler.run_once()
        self.assertEqual([bead.bead_id], result.completed)
        bead = self.storage.load_bead(bead.bead_id)
        self.assertEqual(["src/new_file.py"], bead.touched_files)
        self.assertEqual(["src/new_file.py"], bead.changed_files)

    # ------------------------------------------------------------------
    # Guardrail loading
    # ------------------------------------------------------------------

    def test_scheduler_persists_guardrail_metadata_and_prompt_context(self) -> None:
        bead = self.storage.create_bead(
            title="Implement with guardrails",
            agent_type="developer",
            description="do work",
            expected_files=["src/new_file.py"],
        )
        runner = FakeRunner(
            results={
                bead.bead_id: AgentRunResult(
                    outcome="completed",
                    summary="done",
                    expected_files=["src/new_file.py"],
                )
            }
        )
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))
        scheduler.run_once()

        bead = self.storage.load_bead(bead.bead_id)
        self.assertEqual("developer", bead.metadata["guardrails"]["agent_type"])
        self.assertTrue(bead.metadata["guardrails"]["template_path"].endswith("templates/agents/developer.md"))
        self.assertIn("Primary responsibility: Implement only the assigned bead", bead.metadata["guardrails"]["template_text"])
        self.assertTrue(bead.metadata["guardrails"]["captured_at"])
        self.assertEqual(bead.bead_id, bead.metadata["worker_prompt_context"]["bead_id"])
        guardrail_records = [record for record in bead.execution_history if record.event == "guardrails_applied"]
        self.assertEqual(1, len(guardrail_records))
        self.assertTrue(guardrail_records[0].details["template_path"].endswith("templates/agents/developer.md"))

    def test_scheduler_preserves_blocked_role_scope_handoff_details(self) -> None:
        bead = self.storage.create_bead(
            title="Review implementation work",
            agent_type="review",
            description="inspect",
            touched_files=["src/agent_takt/skills.py"],
            changed_files=["src/agent_takt/skills.py", "CLAUDE.md"],
            conflict_risks="Review is scoped to the rewritten skill rollout files.",
        )
        runner = FakeRunner(
            results={
                bead.bead_id: AgentRunResult(
                    outcome="blocked",
                    summary="Review guardrails prevent implementation changes",
                    completed="Reviewed current implementation and identified required code changes.",
                    remaining="Developer needs to update runtime behavior before review can continue.",
                    risks="Review signoff is blocked until implementation is complete.",
                    next_action="Hand off to a developer to implement the requested changes.",
                    next_agent="developer",
                    block_reason="The bead requires implementation work outside review scope.",
                )
            }
        )
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))
        result = scheduler.run_once()

        self.assertEqual([bead.bead_id], result.blocked)
        bead = self.storage.load_bead(bead.bead_id)
        self.assertEqual(BEAD_BLOCKED, bead.status)
        self.assertEqual(
            "Reviewed current implementation and identified required code changes.",
            bead.handoff_summary.completed,
        )
        self.assertEqual(
            "Developer needs to update runtime behavior before review can continue.",
            bead.handoff_summary.remaining,
        )
        self.assertEqual("Review signoff is blocked until implementation is complete.", bead.handoff_summary.risks)
        self.assertEqual(["src/agent_takt/skills.py"], bead.touched_files)
        self.assertEqual(["src/agent_takt/skills.py"], bead.handoff_summary.touched_files)
        self.assertEqual(
            ["src/agent_takt/skills.py", "CLAUDE.md"],
            bead.changed_files,
        )
        self.assertEqual(
            ["src/agent_takt/skills.py", "CLAUDE.md"],
            bead.handoff_summary.changed_files,
        )
        self.assertEqual(
            "Review is scoped to the rewritten skill rollout files.",
            bead.handoff_summary.conflict_risks,
        )
        self.assertEqual("Hand off to a developer to implement the requested changes.", bead.handoff_summary.next_action)
        self.assertEqual("developer", bead.handoff_summary.next_agent)
        self.assertEqual("The bead requires implementation work outside review scope.", bead.handoff_summary.block_reason)
        self.assertEqual("blocked", bead.metadata["last_agent_result"]["outcome"])
        self.assertEqual(
            "Review guardrails prevent implementation changes",
            bead.metadata["last_agent_result"]["summary"],
        )
        self.assertEqual("developer", bead.metadata["last_agent_result"]["next_agent"])
        self.assertEqual(
            "The bead requires implementation work outside review scope.",
            bead.metadata["last_agent_result"]["block_reason"],
        )
        self.assertIn("review.md", bead.metadata["guardrails"]["template_path"])
        self.assertIn("Inspect code, tests, docs, and acceptance criteria", bead.metadata["guardrails"]["template_text"])

    def test_tester_role_violation_block_preserves_next_agent_and_guardrails(self) -> None:
        bead = self.storage.create_bead(title="Implement runtime fix", agent_type="tester", description="validate coverage")
        runner = FakeRunner(
            results={
                bead.bead_id: AgentRunResult(
                    outcome="blocked",
                    summary="Tester guardrails prevent implementing the missing runtime fix",
                    completed="Confirmed the failing scenario and isolated the missing runtime behavior.",
                    remaining="A developer must implement the runtime fix before testing can finish.",
                    risks="Coverage remains incomplete until the implementation gap is resolved.",
                    next_action="Hand off to a developer to implement the runtime behavior, then rerun the tests.",
                    next_agent="developer",
                    block_reason="The bead requires feature logic changes outside tester scope.",
                )
            }
        )
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))
        result = scheduler.run_once()

        self.assertEqual([bead.bead_id], result.blocked)
        bead = self.storage.load_bead(bead.bead_id)
        self.assertEqual(BEAD_BLOCKED, bead.status)
        self.assertEqual("developer", bead.handoff_summary.next_agent)
        self.assertEqual("The bead requires feature logic changes outside tester scope.", bead.handoff_summary.block_reason)
        self.assertEqual("blocked", bead.metadata["last_agent_result"]["outcome"])
        self.assertEqual("developer", bead.metadata["last_agent_result"]["next_agent"])
        self.assertEqual(
            "The bead requires feature logic changes outside tester scope.",
            bead.metadata["last_agent_result"]["block_reason"],
        )
        self.assertIn("tester.md", bead.metadata["guardrails"]["template_path"])
        self.assertIn("Add or update automated tests", bead.metadata["guardrails"]["template_text"])


if __name__ == "__main__":
    unittest.main()
