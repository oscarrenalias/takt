from __future__ import annotations

import json
import io
import shutil
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from codex_orchestrator.cli import (
    LIST_PLAIN_COLUMNS,
    build_parser,
    command_bead,
    command_merge,
    command_plan,
    command_summary,
    command_tui,
)
from codex_orchestrator.console import ConsoleReporter
from codex_orchestrator.gitutils import GitError, WorktreeManager
from codex_orchestrator.models import (
    AgentRunResult,
    BEAD_BLOCKED,
    BEAD_DONE,
    BEAD_HANDED_OFF,
    BEAD_IN_PROGRESS,
    BEAD_OPEN,
    BEAD_READY,
    Bead,
    ExecutionRecord,
    HandoffSummary,
    Lease,
    PlanChild,
    PlanProposal,
)
from codex_orchestrator.planner import PlanningService
from codex_orchestrator.prompts import (
    BUILT_IN_AGENT_TYPES,
    build_worker_prompt,
    guardrail_template_path,
    load_guardrail_template,
    render_context_snippets,
)
from codex_orchestrator.runner import AGENT_OUTPUT_SCHEMA
from codex_orchestrator.scheduler import Scheduler
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
    format_footer,
    render_tree_panel,
    run_tui,
    resolve_selected_bead,
    resolve_selected_index,
    supported_filter_modes,
)


class FakeRunner:
    def __init__(
        self,
        results: dict[str, AgentRunResult] | None = None,
        proposal: PlanProposal | None = None,
        writes: dict[str, dict[str, str]] | None = None,
    ) -> None:
        self.results = results or {}
        self.proposal_value = proposal
        self.writes = writes or {}
        self.last_workdir_by_bead: dict[str, Path] = {}

    def run_bead(
        self,
        bead: Bead,
        *,
        workdir: Path,
        context_paths: list[Path],
        execution_env: dict[str, str] | None = None,
    ) -> AgentRunResult:
        self.last_workdir_by_bead[bead.bead_id] = workdir
        for relative_path, content in self.writes.get(bead.bead_id, {}).items():
            target = workdir / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        return self.results[bead.bead_id]

    def propose_plan(self, spec_text: str) -> PlanProposal:
        if self.proposal_value is None:
            raise AssertionError("No plan proposal configured")
        return self.proposal_value


class OrchestratorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        subprocess.run(["git", "init"], cwd=self.root, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=self.root, check=True)
        (self.root / "README.md").write_text("seed\n", encoding="utf-8")
        source_templates = Path(__file__).resolve().parents[1] / "templates" / "agents"
        target_templates = self.root / "templates" / "agents"
        target_templates.mkdir(parents=True, exist_ok=True)
        for template in BUILT_IN_AGENT_TYPES:
            shutil.copy2(source_templates / f"{template}.md", target_templates / f"{template}.md")
        subprocess.run(["git", "add", "README.md"], cwd=self.root, check=True)
        subprocess.run(["git", "add", "templates/agents"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=self.root, check=True, capture_output=True)
        self.storage = RepositoryStorage(self.root)
        self.storage.initialize()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

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

    def test_review_with_remaining_findings_is_forced_blocked(self) -> None:
        bead = self.storage.create_bead(title="Review work", agent_type="review", description="inspect")
        runner = FakeRunner(
            results={
                bead.bead_id: AgentRunResult(
                    outcome="completed",
                    summary="Review finished",
                    completed="Validated the current implementation state.",
                    remaining="Unresolved defect in prompt template resolution.",
                    risks="Review sign-off cannot complete until the defect is fixed.",
                    next_action="Hand off to developer for the fix, then retry review.",
                    next_agent="developer",
                )
            }
        )
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))
        result = scheduler.run_once()
        self.assertEqual([bead.bead_id], result.blocked)
        self.assertEqual([], result.completed)
        bead = self.storage.load_bead(bead.bead_id)
        self.assertEqual(BEAD_BLOCKED, bead.status)
        self.assertIn("unresolved", bead.block_reason.lower())
        self.assertEqual("Validated the current implementation state.", bead.handoff_summary.completed)
        self.assertEqual("developer", bead.handoff_summary.next_agent)
        self.assertIn("unresolved", bead.handoff_summary.block_reason.lower())
        self.assertEqual("blocked", bead.metadata["last_agent_result"]["outcome"])
        self.assertEqual("developer", bead.metadata["last_agent_result"]["next_agent"])
        self.assertIn("unresolved", bead.metadata["last_agent_result"]["block_reason"].lower())

    def test_tester_with_remaining_findings_is_forced_blocked(self) -> None:
        bead = self.storage.create_bead(title="Test work", agent_type="tester", description="validate")
        runner = FakeRunner(
            results={
                bead.bead_id: AgentRunResult(
                    outcome="completed",
                    summary="Tests run complete",
                    completed="Executed the available regression checks.",
                    remaining="Known failing test remains unresolved.",
                    risks="Test sign-off is blocked until the runtime fix lands.",
                    next_action="Hand off to developer for the runtime fix, then rerun tests.",
                    next_agent="developer",
                )
            }
        )
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))
        result = scheduler.run_once()
        self.assertEqual([bead.bead_id], result.blocked)
        self.assertEqual([], result.completed)
        bead = self.storage.load_bead(bead.bead_id)
        self.assertEqual(BEAD_BLOCKED, bead.status)
        self.assertIn("unresolved", bead.block_reason.lower())
        self.assertEqual("Executed the available regression checks.", bead.handoff_summary.completed)
        self.assertEqual("developer", bead.handoff_summary.next_agent)
        self.assertIn("unresolved", bead.handoff_summary.block_reason.lower())
        self.assertEqual("blocked", bead.metadata["last_agent_result"]["outcome"])
        self.assertEqual("developer", bead.metadata["last_agent_result"]["next_agent"])
        self.assertIn("unresolved", bead.metadata["last_agent_result"]["block_reason"].lower())

    def test_tester_with_no_additional_work_remaining_stays_completed(self) -> None:
        bead = self.storage.create_bead(title="Test work", agent_type="tester", description="validate")
        runner = FakeRunner(
            results={
                bead.bead_id: AgentRunResult(
                    outcome="completed",
                    summary="Tests run complete",
                    remaining="No additional tester-scope work required for this bead.",
                )
            }
        )
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))
        result = scheduler.run_once()
        self.assertEqual([bead.bead_id], result.completed)
        self.assertEqual([], result.blocked)
        bead = self.storage.load_bead(bead.bead_id)
        self.assertEqual(BEAD_DONE, bead.status)

    def test_tester_with_no_tester_scope_work_remains_stays_completed(self) -> None:
        bead = self.storage.create_bead(title="Test work", agent_type="tester", description="validate")
        runner = FakeRunner(
            results={
                bead.bead_id: AgentRunResult(
                    outcome="completed",
                    summary="Tests run complete",
                    remaining="No tester-scope work remains for this bead.",
                )
            }
        )
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))
        result = scheduler.run_once()
        self.assertEqual([bead.bead_id], result.completed)
        self.assertEqual([], result.blocked)
        bead = self.storage.load_bead(bead.bead_id)
        self.assertEqual(BEAD_DONE, bead.status)

    def test_review_with_none_for_this_bead_remaining_stays_completed(self) -> None:
        bead = self.storage.create_bead(title="Review work", agent_type="review", description="inspect")
        runner = FakeRunner(
            results={
                bead.bead_id: AgentRunResult(
                    outcome="completed",
                    summary="Review finished",
                    remaining="None for this bead.",
                )
            }
        )
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))
        result = scheduler.run_once()
        self.assertEqual([bead.bead_id], result.completed)
        self.assertEqual([], result.blocked)
        bead = self.storage.load_bead(bead.bead_id)
        self.assertEqual(BEAD_DONE, bead.status)

    def test_review_with_no_gaps_identified_remaining_stays_completed(self) -> None:
        bead = self.storage.create_bead(title="Review work", agent_type="review", description="inspect")
        runner = FakeRunner(
            results={
                bead.bead_id: AgentRunResult(
                    outcome="completed",
                    summary="Review finished",
                    remaining="No correctness, coverage, or documentation gaps were identified in the reviewed scope.",
                )
            }
        )
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))
        result = scheduler.run_once()
        self.assertEqual([bead.bead_id], result.completed)
        self.assertEqual([], result.blocked)
        bead = self.storage.load_bead(bead.bead_id)
        self.assertEqual(BEAD_DONE, bead.status)

    def test_review_with_no_findings_discovered_remaining_stays_completed(self) -> None:
        bead = self.storage.create_bead(title="Review work", agent_type="review", description="inspect")
        runner = FakeRunner(
            results={
                bead.bead_id: AgentRunResult(
                    outcome="completed",
                    summary="Review finished",
                    remaining="No findings discovered in this review pass.",
                )
            }
        )
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))
        result = scheduler.run_once()
        self.assertEqual([bead.bead_id], result.completed)
        self.assertEqual([], result.blocked)
        bead = self.storage.load_bead(bead.bead_id)
        self.assertEqual(BEAD_DONE, bead.status)

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
        scheduler = Scheduler(self.storage, FakeRunner(results={}), WorktreeManager(self.root, self.storage.worktrees_dir))
        scheduler.run_once(max_workers=0)
        bead = self.storage.load_bead(bead.bead_id)
        self.assertTrue(bead.metadata.get("needs_human_intervention"))
        self.assertIn("Exceeded corrective attempt budget", bead.metadata.get("escalation_reason", ""))

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
                        expected_files=["src/codex_orchestrator/scheduler.py"],
                        children=[
                            PlanChild(
                                title="Review",
                                agent_type="review",
                                description="check",
                                acceptance_criteria=["approved"],
                                dependencies=["Implement"],
                                expected_globs=["src/codex_orchestrator/*.py"],
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
        self.assertEqual(["src/codex_orchestrator/scheduler.py"], implement.expected_files)
        self.assertEqual(["src/codex_orchestrator/*.py"], review.expected_globs)

    def test_worktree_manager_creates_branch_and_directory(self) -> None:
        manager = WorktreeManager(self.root, self.storage.worktrees_dir)
        worktree = manager.ensure_worktree("B0001", "bead/b0001")
        self.assertTrue(worktree.exists())

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

    def test_scheduler_defers_overlapping_claims(self) -> None:
        bead1 = self.storage.create_bead(
            title="Scheduler conflict A",
            agent_type="developer",
            description="one",
            expected_files=["src/codex_orchestrator/scheduler.py"],
        )
        bead2 = self.storage.create_bead(
            title="Scheduler conflict B",
            agent_type="developer",
            description="two",
            expected_files=["src/codex_orchestrator/scheduler.py"],
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
            expected_files=["src/codex_orchestrator/planner.py"],
        )
        bead2 = self.storage.create_bead(
            title="Storage scope",
            agent_type="developer",
            description="two",
            expected_files=["src/codex_orchestrator/storage.py"],
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
        bead = self.storage.create_bead(title="Review implementation work", agent_type="review", description="inspect")
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

    def test_cli_bead_show_exposes_guardrail_template_context(self) -> None:
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

        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)
        exit_code = command_bead(Namespace(bead_command="show", bead_id=bead.bead_id), self.storage, console)

        self.assertEqual(0, exit_code)
        payload = stream.getvalue()
        self.assertIn('"guardrails"', payload)
        self.assertIn('"template_path"', payload)
        self.assertIn("templates/agents/developer.md", payload)
        self.assertIn('"worker_prompt_context"', payload)
        self.assertIn('"guardrails_applied"', payload)

    def test_active_claims_report_in_progress_scope(self) -> None:
        bead = self.storage.create_bead(
            title="Active bead",
            agent_type="developer",
            description="running",
            expected_files=["src/codex_orchestrator/scheduler.py"],
            touched_files=["src/codex_orchestrator/scheduler.py"],
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
        self.assertEqual(["src/codex_orchestrator/scheduler.py"], claims[0]["touched_files"])

    def test_cli_claims_defaults_to_json_output(self) -> None:
        bead = self.storage.create_bead(
            title="CLI bead",
            agent_type="developer",
            description="running",
            expected_files=["src/codex_orchestrator/storage.py"],
        )
        bead.status = BEAD_IN_PROGRESS
        bead.lease = Lease(owner="developer:cli", expires_at="2099-01-01T00:00:00+00:00")
        self.storage.save_bead(bead)
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)
        exit_code = command_bead(Namespace(bead_command="claims"), self.storage, console)
        self.assertEqual(0, exit_code)
        rendered = stream.getvalue()
        claims = json.loads(rendered)
        self.assertEqual(1, len(claims))
        self.assertEqual(bead.bead_id, claims[0]["bead_id"])
        self.assertEqual("developer", claims[0]["agent_type"])
        self.assertEqual(bead.bead_id, claims[0]["feature_root_id"])
        self.assertEqual("expected_files", claims[0]["scope_source"])
        self.assertEqual(["src/codex_orchestrator/storage.py"], claims[0]["expected_files"])
        self.assertEqual("developer:cli", claims[0]["lease"]["owner"])
        self.assertNotIn(" | ", rendered)

    def test_cli_claims_plain_outputs_compact_lines(self) -> None:
        bead = self.storage.create_bead(
            title="CLI bead plain",
            agent_type="developer",
            description="running",
            expected_files=["src/codex_orchestrator/storage.py"],
        )
        bead.status = BEAD_IN_PROGRESS
        bead.lease = Lease(owner="developer:plain", expires_at="2099-01-01T00:00:00+00:00")
        self.storage.save_bead(bead)
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)

        exit_code = command_bead(Namespace(bead_command="claims", plain=True), self.storage, console)

        self.assertEqual(0, exit_code)
        line = stream.getvalue().strip()
        self.assertIn(bead.bead_id, line)
        self.assertIn("developer", line)
        self.assertIn(f"feature={bead.bead_id}", line)
        self.assertIn("lease=developer:plain", line)
        self.assertEqual(3, line.count("|"))

    def test_cli_claims_plain_outputs_multiple_claims_in_bead_order(self) -> None:
        first = self.storage.create_bead(
            title="First active bead",
            agent_type="developer",
            description="running",
        )
        first.status = BEAD_IN_PROGRESS
        first.lease = Lease(owner="developer:first", expires_at="2099-01-01T00:00:00+00:00")
        self.storage.save_bead(first)

        second = self.storage.create_bead(
            title="Second active bead",
            agent_type="tester",
            description="running",
        )
        second.status = BEAD_IN_PROGRESS
        second.lease = Lease(owner="tester:second", expires_at="2099-01-01T00:00:00+00:00")
        self.storage.save_bead(second)

        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)

        exit_code = command_bead(Namespace(bead_command="claims", plain=True), self.storage, console)

        self.assertEqual(0, exit_code)
        self.assertEqual(
            [
                f"{first.bead_id} | developer | feature={first.bead_id} | lease=developer:first",
                f"{second.bead_id} | tester | feature={second.bead_id} | lease=tester:second",
            ],
            stream.getvalue().strip().splitlines(),
        )

    def test_cli_claims_plain_empty_state(self) -> None:
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)

        exit_code = command_bead(Namespace(bead_command="claims", plain=True), self.storage, console)

        self.assertEqual(0, exit_code)
        self.assertEqual("No active claims.\n", stream.getvalue())

    def test_build_parser_accepts_bead_claims_plain_flag(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["bead", "claims", "--plain"])
        self.assertEqual("bead", args.command)
        self.assertEqual("claims", args.bead_command)
        self.assertTrue(args.plain)

    def test_build_parser_accepts_tui_options_and_defaults(self) -> None:
        parser = build_parser()

        args = parser.parse_args(["tui", "--feature-root", "B0030"])

        self.assertEqual("tui", args.command)
        self.assertEqual("B0030", args.feature_root)
        self.assertEqual(3, args.refresh_seconds)

    def test_build_parser_rejects_tui_refresh_seconds_below_one(self) -> None:
        parser = build_parser()

        with self.assertRaises(SystemExit):
            parser.parse_args(["tui", "--refresh-seconds", "0"])

    def test_cli_bead_list_defaults_to_json(self) -> None:
        bead = self.storage.create_bead(
            title="List bead",
            agent_type="developer",
            description="for json output",
        )
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)
        exit_code = command_bead(Namespace(bead_command="list"), self.storage, console)
        self.assertEqual(0, exit_code)
        rendered = stream.getvalue()
        payload = json.loads(rendered)
        self.assertEqual(1, len(payload))
        self.assertEqual(bead.bead_id, payload[0]["bead_id"])
        self.assertEqual("developer", payload[0]["agent_type"])
        self.assertEqual("task", payload[0]["bead_type"])
        self.assertIn("title", payload[0])
        self.assertNotIn("BEAD_ID", rendered)

    def test_cli_bead_list_plain_outputs_headers_rows_and_missing_values(self) -> None:
        self.storage.create_bead(
            title="Epic Root",
            agent_type="planner",
            description="feature root placeholder",
            bead_type="epic",
        )
        self.storage.create_bead(
            title="Child Task",
            agent_type="developer",
            description="child task",
            parent_id="B0001",
        )
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)
        exit_code = command_bead(Namespace(bead_command="list", plain=True), self.storage, console)
        self.assertEqual(0, exit_code)
        output = stream.getvalue()
        lines = output.splitlines()
        self.assertEqual(3, len(lines))
        for header, _ in LIST_PLAIN_COLUMNS:
            self.assertIn(header, lines[0])
        self.assertIn("B0001", lines[1])
        self.assertIn("B0002", lines[2])
        self.assertIn(" - ", lines[1])  # feature_root_id and parent_id render as "-"
        self.assertNotIn('"bead_id"', output)
        self.assertFalse(output.lstrip().startswith("["))

    def test_cli_bead_list_plain_rows_are_sorted_by_bead_id(self) -> None:
        bead_a = self.storage.create_bead(
            title="A bead",
            agent_type="developer",
            description="first bead",
        )
        bead_b = self.storage.create_bead(
            title="B bead",
            agent_type="developer",
            description="second bead",
        )
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)
        with patch.object(self.storage, "list_beads", return_value=[bead_b, bead_a]):
            exit_code = command_bead(Namespace(bead_command="list", plain=True), self.storage, console)
        self.assertEqual(0, exit_code)
        lines = stream.getvalue().splitlines()
        self.assertEqual(bead_a.bead_id, lines[1].split()[0])
        self.assertEqual(bead_b.bead_id, lines[2].split()[0])

    def test_cli_bead_list_plain_empty_state(self) -> None:
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)
        exit_code = command_bead(Namespace(bead_command="list", plain=True), self.storage, console)
        self.assertEqual(0, exit_code)
        self.assertEqual("No beads found.\n", stream.getvalue())

    def test_command_plan_write_outputs_created_bead_details(self) -> None:
        spec_path = self.root / "spec.md"
        spec_path.write_text("Feature spec\n", encoding="utf-8")
        proposal = PlanProposal(
            epic_title="Epic",
            epic_description="Parent task",
            linked_docs=["spec.md"],
            feature=PlanChild(
                title="Feature root",
                agent_type="planner",
                description="shared execution root",
                acceptance_criteria=["works"],
                children=[
                    PlanChild(
                        title="Implement",
                        agent_type="developer",
                        description="build",
                        acceptance_criteria=["works"],
                    )
                ],
            ),
        )
        planner = PlanningService(self.storage, FakeRunner(proposal=proposal))
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)
        exit_code = command_plan(Namespace(spec_file=str(spec_path), write=True), planner, console)
        self.assertEqual(0, exit_code)
        output = stream.getvalue()
        self.assertIn('"bead_id": "B0001"', output)
        self.assertIn('"title": "Epic"', output)
        self.assertNotIn('"description"', output)

    def test_summary_counts_and_lists_are_sorted_and_limited(self) -> None:
        ready_ids = []
        for idx in range(7):
            bead = self.storage.create_bead(
                title=f"Ready {idx}",
                agent_type="developer",
                description="ready work",
                status=BEAD_READY,
            )
            ready_ids.append(bead.bead_id)

        blocked_ids = []
        for idx in range(6):
            blocked = self.storage.create_bead(
                title=f"Blocked {idx}",
                agent_type="tester",
                description="blocked work",
                status=BEAD_BLOCKED,
            )
            blocked_ids.append(blocked.bead_id)
            if idx == 0:
                blocked.handoff_summary = HandoffSummary(block_reason="Needs dependency fix")
            else:
                blocked.block_reason = f"blocked-{idx}"
            self.storage.save_bead(blocked)

        self.storage.create_bead(title="Open", agent_type="planner", description="open", status=BEAD_OPEN)
        self.storage.create_bead(title="In progress", agent_type="developer", description="running", status=BEAD_IN_PROGRESS)
        self.storage.create_bead(title="Done", agent_type="review", description="finished", status=BEAD_DONE)
        self.storage.create_bead(title="Handed off", agent_type="documentation", description="handoff", status=BEAD_HANDED_OFF)

        summary = self.storage.summary()
        self.assertEqual(
            [BEAD_OPEN, BEAD_READY, BEAD_IN_PROGRESS, BEAD_BLOCKED, BEAD_DONE, BEAD_HANDED_OFF],
            list(summary["counts"].keys()),
        )
        self.assertEqual(1, summary["counts"][BEAD_OPEN])
        self.assertEqual(7, summary["counts"][BEAD_READY])
        self.assertEqual(1, summary["counts"][BEAD_IN_PROGRESS])
        self.assertEqual(6, summary["counts"][BEAD_BLOCKED])
        self.assertEqual(1, summary["counts"][BEAD_DONE])
        self.assertEqual(1, summary["counts"][BEAD_HANDED_OFF])

        self.assertEqual(5, len(summary["next_up"]))
        self.assertEqual(sorted(ready_ids)[:5], [item["bead_id"] for item in summary["next_up"]])
        self.assertTrue(all(item["status"] == BEAD_READY for item in summary["next_up"]))

        self.assertEqual(5, len(summary["attention"]))
        self.assertEqual(
            sorted(blocked_ids)[:5],
            [item["bead_id"] for item in summary["attention"]],
        )
        self.assertTrue(all(item["status"] == BEAD_BLOCKED for item in summary["attention"]))
        self.assertEqual("Needs dependency fix", summary["attention"][0]["block_reason"])

    def test_summary_can_filter_to_feature_root_tree(self) -> None:
        epic = self.storage.create_bead(title="Epic", agent_type="planner", description="root", status=BEAD_DONE, bead_type="epic")
        root_a = self.storage.create_bead(title="Feature A", agent_type="developer", description="A", parent_id=epic.bead_id, status=BEAD_DONE)
        root_b = self.storage.create_bead(title="Feature B", agent_type="developer", description="B", parent_id=epic.bead_id, status=BEAD_DONE)
        child_a1 = self.storage.create_bead(
            title="Feature A task 1",
            agent_type="developer",
            description="A1",
            parent_id=root_a.bead_id,
            dependencies=[root_a.bead_id],
            status=BEAD_READY,
        )
        child_a2 = self.storage.create_bead(
            title="Feature A task 2",
            agent_type="tester",
            description="A2",
            parent_id=root_a.bead_id,
            dependencies=[root_a.bead_id],
            status=BEAD_BLOCKED,
        )
        self.storage.create_bead(
            title="Feature B task 1",
            agent_type="developer",
            description="B1",
            parent_id=root_b.bead_id,
            dependencies=[root_b.bead_id],
            status=BEAD_READY,
        )

        summary = self.storage.summary(feature_root_id=root_a.bead_id)
        self.assertEqual(1, summary["counts"][BEAD_DONE])  # root_a
        self.assertEqual(1, summary["counts"][BEAD_READY])  # child_a1
        self.assertEqual(1, summary["counts"][BEAD_BLOCKED])  # child_a2
        self.assertEqual(0, summary["counts"][BEAD_OPEN])
        self.assertEqual(0, summary["counts"][BEAD_IN_PROGRESS])
        self.assertEqual(0, summary["counts"][BEAD_HANDED_OFF])
        self.assertEqual([child_a1.bead_id], [item["bead_id"] for item in summary["next_up"]])
        self.assertEqual([child_a2.bead_id], [item["bead_id"] for item in summary["attention"]])

        missing = self.storage.summary(feature_root_id="B9999")
        self.assertEqual(
            {
                BEAD_OPEN: 0,
                BEAD_READY: 0,
                BEAD_IN_PROGRESS: 0,
                BEAD_BLOCKED: 0,
                BEAD_DONE: 0,
                BEAD_HANDED_OFF: 0,
            },
            missing["counts"],
        )
        self.assertEqual([], missing["next_up"])
        self.assertEqual([], missing["attention"])

    def test_command_summary_outputs_json(self) -> None:
        self.storage.create_bead(title="Ready", agent_type="developer", description="work", status=BEAD_READY)
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)

        exit_code = command_summary(Namespace(feature_root=None), self.storage, console)

        self.assertEqual(0, exit_code)
        payload = json.loads(stream.getvalue())
        self.assertEqual(["counts", "next_up", "attention"], list(payload.keys()))
        self.assertEqual(
            [BEAD_OPEN, BEAD_READY, BEAD_IN_PROGRESS, BEAD_BLOCKED, BEAD_DONE, BEAD_HANDED_OFF],
            list(payload["counts"].keys()),
        )
        self.assertEqual(1, payload["counts"][BEAD_READY])
        self.assertEqual(1, len(payload["next_up"]))
        self.assertEqual([], payload["attention"])

    def test_command_summary_filters_by_feature_root_and_handles_unknown_root(self) -> None:
        epic = self.storage.create_bead(title="Epic", agent_type="planner", description="root", status=BEAD_DONE, bead_type="epic")
        root_a = self.storage.create_bead(title="Feature A", agent_type="developer", description="A", parent_id=epic.bead_id, status=BEAD_DONE)
        root_b = self.storage.create_bead(title="Feature B", agent_type="developer", description="B", parent_id=epic.bead_id, status=BEAD_DONE)
        child_a = self.storage.create_bead(
            title="Feature A task",
            agent_type="developer",
            description="A1",
            parent_id=root_a.bead_id,
            dependencies=[root_a.bead_id],
            status=BEAD_READY,
        )
        self.storage.create_bead(
            title="Feature B task",
            agent_type="developer",
            description="B1",
            parent_id=root_b.bead_id,
            dependencies=[root_b.bead_id],
            status=BEAD_READY,
        )

        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)
        exit_code = command_summary(Namespace(feature_root=root_a.bead_id), self.storage, console)
        self.assertEqual(0, exit_code)
        filtered_payload = json.loads(stream.getvalue())
        self.assertEqual(1, filtered_payload["counts"][BEAD_DONE])  # root_a only
        self.assertEqual(1, filtered_payload["counts"][BEAD_READY])  # child_a only
        self.assertEqual([child_a.bead_id], [item["bead_id"] for item in filtered_payload["next_up"]])

        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)
        exit_code = command_summary(Namespace(feature_root="B9999"), self.storage, console)
        self.assertEqual(0, exit_code)
        missing_payload = json.loads(stream.getvalue())
        self.assertEqual(
            {
                BEAD_OPEN: 0,
                BEAD_READY: 0,
                BEAD_IN_PROGRESS: 0,
                BEAD_BLOCKED: 0,
                BEAD_DONE: 0,
                BEAD_HANDED_OFF: 0,
            },
            missing_payload["counts"],
        )
        self.assertEqual([], missing_payload["next_up"])
        self.assertEqual([], missing_payload["attention"])

    def test_command_summary_ignores_non_feature_root_scope(self) -> None:
        epic = self.storage.create_bead(title="Epic", agent_type="planner", description="root", status=BEAD_DONE, bead_type="epic")
        root = self.storage.create_bead(title="Feature A", agent_type="developer", description="A", parent_id=epic.bead_id, status=BEAD_DONE)
        child = self.storage.create_bead(
            title="Feature A task",
            agent_type="developer",
            description="A1",
            parent_id=root.bead_id,
            dependencies=[root.bead_id],
            status=BEAD_READY,
        )

        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)
        exit_code = command_summary(Namespace(feature_root=child.bead_id), self.storage, console)

        self.assertEqual(0, exit_code)
        payload = json.loads(stream.getvalue())
        self.assertEqual(0, payload["counts"][BEAD_DONE])
        self.assertEqual(0, payload["counts"][BEAD_READY])
        self.assertEqual([], payload["next_up"])
        self.assertEqual([], payload["attention"])

    def test_command_tui_reports_missing_render_dependency_without_mutating_state(self) -> None:
        bead = self.storage.create_bead(title="Ready", agent_type="developer", description="work", status=BEAD_READY)
        original = self.storage.load_bead(bead.bead_id).to_dict()
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)

        with patch("codex_orchestrator.tui.load_textual_runtime", side_effect=RuntimeError("missing textual")):
            exit_code = command_tui(Namespace(feature_root=None, refresh_seconds=3), self.storage, console)

        self.assertEqual(1, exit_code)
        self.assertIn("missing textual", stream.getvalue())
        self.assertEqual(original, self.storage.load_bead(bead.bead_id).to_dict())

    def test_command_tui_forwards_feature_root_refresh_and_console_stream(self) -> None:
        epic = self.storage.create_bead(title="Epic", agent_type="planner", description="root", status=BEAD_DONE, bead_type="epic")
        root = self.storage.create_bead(title="Feature A", agent_type="developer", description="A", parent_id=epic.bead_id, status=BEAD_DONE)
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)

        with patch("codex_orchestrator.tui.run_tui", return_value=0) as run_tui:
            exit_code = command_tui(Namespace(feature_root=root.bead_id, refresh_seconds=9), self.storage, console)

        self.assertEqual(0, exit_code)
        run_tui.assert_called_once_with(
            self.storage,
            feature_root_id=root.bead_id,
            refresh_seconds=9,
            stream=stream,
        )

    def test_command_tui_rejects_unknown_feature_root(self) -> None:
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)

        with patch("codex_orchestrator.tui.run_tui") as run_tui:
            exit_code = command_tui(Namespace(feature_root="B9999", refresh_seconds=3), self.storage, console)

        self.assertEqual(1, exit_code)
        self.assertIn("B9999 is not a valid feature root", stream.getvalue())
        run_tui.assert_not_called()

    def test_command_tui_rejects_non_feature_root_scope(self) -> None:
        epic = self.storage.create_bead(title="Epic", agent_type="planner", description="root", status=BEAD_DONE, bead_type="epic")
        root = self.storage.create_bead(title="Feature A", agent_type="developer", description="A", parent_id=epic.bead_id, status=BEAD_DONE)
        child = self.storage.create_bead(
            title="Feature A task",
            agent_type="developer",
            description="A1",
            parent_id=root.bead_id,
            dependencies=[root.bead_id],
            status=BEAD_READY,
        )
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)

        with patch("codex_orchestrator.tui.run_tui") as run_tui:
            exit_code = command_tui(Namespace(feature_root=child.bead_id, refresh_seconds=3), self.storage, console)

        self.assertEqual(1, exit_code)
        self.assertIn(f"{child.bead_id} is not a valid feature root", stream.getvalue())
        run_tui.assert_not_called()

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

    def test_same_feature_tree_non_overlapping_mutations_can_run_in_parallel(self) -> None:
        epic = self.storage.create_bead(title="Epic", agent_type="planner", description="root", status=BEAD_DONE, bead_type="epic")
        root = self.storage.create_bead(title="Feature root", agent_type="developer", description="feature", parent_id=epic.bead_id, status=BEAD_DONE)
        bead1 = self.storage.create_bead(
            title="Planner scope",
            agent_type="developer",
            description="one",
            parent_id=root.bead_id,
            dependencies=[root.bead_id],
            expected_files=["src/codex_orchestrator/planner.py"],
        )
        bead2 = self.storage.create_bead(
            title="Storage scope",
            agent_type="developer",
            description="two",
            parent_id=root.bead_id,
            dependencies=[root.bead_id],
            expected_files=["src/codex_orchestrator/storage.py"],
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

    def test_worker_prompt_includes_shared_feature_execution_context(self) -> None:
        bead = self.storage.create_bead(title="Implement", agent_type="developer", description="do work")
        prompt = build_worker_prompt(bead, [], self.root)
        self.assertIn('"feature_root_id"', prompt)
        self.assertIn('"execution_branch_name"', prompt)
        self.assertIn("shared feature worktree", prompt)
        self.assertIn("Agent guardrails:", prompt)
        self.assertIn(str(guardrail_template_path("developer", root=self.root)), prompt)
        self.assertIn("Primary responsibility: Implement only the assigned bead", prompt)

    def test_worker_prompt_loads_matching_guardrail_template_for_review(self) -> None:
        bead = self.storage.create_bead(title="Review", agent_type="review", description="inspect changes")
        prompt = build_worker_prompt(bead, [], self.root)
        self.assertIn(str(guardrail_template_path("review", root=self.root)), prompt)
        self.assertIn("Primary responsibility: Inspect code, tests, docs, and acceptance criteria", prompt)
        self.assertIn("return a blocked result with block_reason and next_agent", prompt)

    def test_load_guardrail_template_returns_path_and_trimmed_contents_for_each_builtin_agent(self) -> None:
        for agent_type in BUILT_IN_AGENT_TYPES:
            with self.subTest(agent_type=agent_type):
                path, template_text = load_guardrail_template(agent_type, root=self.root)
                self.assertEqual(guardrail_template_path(agent_type, root=self.root), path)
                self.assertTrue(template_text.startswith(f"# {agent_type.capitalize()} Guardrails"))
                self.assertFalse(template_text.endswith("\n"))

    def test_worker_prompt_references_every_builtin_template_file(self) -> None:
        for agent_type in BUILT_IN_AGENT_TYPES:
            with self.subTest(agent_type=agent_type):
                bead = self.storage.create_bead(title=f"{agent_type} bead", agent_type=agent_type, description="scoped work")
                prompt = build_worker_prompt(bead, [], self.root)
                self.assertIn(f"Template: {guardrail_template_path(agent_type, root=self.root)}", prompt)

    def test_worker_prompt_uses_templates_from_provided_root(self) -> None:
        alt_root = self.root / "alt-root"
        for agent_type in BUILT_IN_AGENT_TYPES:
            template_path = alt_root / "templates" / "agents" / f"{agent_type}.md"
            template_path.parent.mkdir(parents=True, exist_ok=True)
            template_path.write_text(f"# {agent_type.capitalize()} Guardrails\n\nRoot marker: alt-root\n", encoding="utf-8")

        bead = self.storage.create_bead(title="Implement", agent_type="developer", description="do work")
        prompt = build_worker_prompt(bead, [], alt_root)
        self.assertIn(f"Template: {guardrail_template_path('developer', root=alt_root)}", prompt)
        self.assertIn("Root marker: alt-root", prompt)

    def test_linked_context_paths_falls_back_to_unique_basename_match(self) -> None:
        context_file = self.root / "simple-claims-plain-command.md"
        context_file.write_text("plain claims spec\n", encoding="utf-8")
        bead = self.storage.create_bead(
            title="Implement plain claims output",
            agent_type="developer",
            description="do work",
            linked_docs=["specs/simple-claims-plain-command.md"],
        )

        context_paths = self.storage.linked_context_paths(bead)

        self.assertIn(context_file.resolve(), [path.resolve() for path in context_paths])

    def test_linked_context_paths_skips_ambiguous_basename_matches(self) -> None:
        first = self.root / "docs" / "simple-claims-plain-command.md"
        second = self.root / "specs" / "simple-claims-plain-command.md"
        first.parent.mkdir(parents=True, exist_ok=True)
        second.parent.mkdir(parents=True, exist_ok=True)
        first.write_text("one\n", encoding="utf-8")
        second.write_text("two\n", encoding="utf-8")
        bead = self.storage.create_bead(
            title="Implement plain claims output",
            agent_type="developer",
            description="do work",
            linked_docs=["missing/simple-claims-plain-command.md"],
        )

        context_paths = self.storage.linked_context_paths(bead)

        resolved_context_paths = [path.resolve() for path in context_paths]
        self.assertNotIn(first.resolve(), resolved_context_paths)
        self.assertNotIn(second.resolve(), resolved_context_paths)

    def test_worker_prompt_raises_clear_error_when_guardrail_template_missing(self) -> None:
        template_path = guardrail_template_path("developer", root=self.root)
        original_text = template_path.read_text(encoding="utf-8")
        template_path.unlink()

        def restore_template() -> None:
            template_path.parent.mkdir(parents=True, exist_ok=True)
            template_path.write_text(original_text, encoding="utf-8")

        self.addCleanup(restore_template)

        bead = self.storage.create_bead(title="Implement", agent_type="developer", description="do work")
        with self.assertRaisesRegex(FileNotFoundError, "Missing guardrail template for built-in agent 'developer'"):
            build_worker_prompt(bead, [], self.root)

    def test_merge_uses_feature_root_branch_for_descendants(self) -> None:
        epic = self.storage.create_bead(title="Epic", agent_type="planner", description="root", status=BEAD_DONE, bead_type="epic")
        root = self.storage.create_bead(title="Feature root", agent_type="developer", description="feature", parent_id=epic.bead_id)
        child = self.storage.create_bead(
            title="Child task",
            agent_type="developer",
            description="subtask",
            parent_id=root.bead_id,
            dependencies=[root.bead_id],
        )
        root.execution_branch_name = "feature/b0001"
        root.branch_name = "feature/b0001"
        self.storage.save_bead(root)
        console = ConsoleReporter(stream=io.StringIO())
        with patch("codex_orchestrator.cli.WorktreeManager.merge_branch") as merge_branch:
            exit_code = command_merge(Namespace(bead_id=child.bead_id), self.storage, console)
        self.assertEqual(0, exit_code)
        merge_branch.assert_called_once_with("feature/b0001")

    def test_render_context_snippets_handles_paths_outside_worktree_root(self) -> None:
        repo_file = self.root / "specs" / "example.md"
        repo_file.parent.mkdir(parents=True, exist_ok=True)
        repo_file.write_text("spec\n", encoding="utf-8")
        worktree_root = self.root / ".orchestrator" / "worktrees" / "B0002"
        worktree_root.mkdir(parents=True, exist_ok=True)
        rendered = render_context_snippets([repo_file], worktree_root)
        self.assertIn("example.md", rendered)

    def test_agent_output_schema_requires_all_new_bead_fields(self) -> None:
        required = AGENT_OUTPUT_SCHEMA["properties"]["new_beads"]["items"]["required"]
        self.assertEqual(
            ["title", "agent_type", "description", "acceptance_criteria", "dependencies", "linked_docs", "expected_files", "expected_globs"],
            required,
        )

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
        self.assertEqual("  B0001-1 · Child A1", rows[1].label)
        self.assertEqual("    B0001-1-1 · Grandchild", rows[2].label)

    def test_tui_selection_preserves_selected_bead_when_visible(self) -> None:
        first = Bead(bead_id="B0001", title="First", agent_type="developer", description="one")
        second = Bead(bead_id="B0002", title="Second", agent_type="developer", description="two")
        rows = build_tree_rows([first, second])

        self.assertEqual(1, resolve_selected_index(rows, selected_bead_id="B0002", previous_index=0))
        self.assertEqual("B0002", resolve_selected_bead(rows, selected_bead_id="B0002", previous_index=0).bead_id)
        self.assertEqual(1, resolve_selected_index(rows, selected_bead_id="B9999", previous_index=3))
        self.assertEqual("B0001", resolve_selected_bead(rows, previous_index=None).bead_id)

    def test_tui_detail_panel_and_footer_include_handoff_scope_and_counts(self) -> None:
        bead = Bead(
            bead_id="B0099",
            title="Implement TUI",
            agent_type="developer",
            description="build helpers",
            status=BEAD_BLOCKED,
            parent_id="B0090",
            feature_root_id="B0030",
            dependencies=["B0098"],
            acceptance_criteria=["Build rows", "Format detail panel"],
            expected_files=["src/codex_orchestrator/tui.py"],
            expected_globs=["tests/test_tui*.py"],
            touched_files=["src/codex_orchestrator/tui.py"],
            changed_files=["src/codex_orchestrator/tui.py", "tests/test_orchestrator.py"],
            updated_docs=["docs/tui.md"],
            block_reason="Waiting on review",
            conflict_risks="Coordinate with review bead on footer text.",
            handoff_summary=HandoffSummary(
                completed="Implemented the TUI helpers.",
                remaining="Need review signoff.",
                risks="Footer wording may change with runtime integration.",
                changed_files=["src/codex_orchestrator/tui.py", "tests/test_orchestrator.py"],
                updated_docs=["docs/tui.md"],
                next_action="Run the review bead.",
                next_agent="review",
                block_reason="Waiting on review",
                expected_files=["src/codex_orchestrator/tui.py"],
                expected_globs=["tests/test_tui*.py"],
                touched_files=["src/codex_orchestrator/tui.py"],
                conflict_risks="Coordinate with review bead on footer text.",
            ),
        )

        detail = format_detail_panel(bead)
        footer = format_footer([bead], filter_mode=FILTER_DEFAULT, selected_index=0, total_rows=1)

        self.assertIn("Bead: B0099", detail)
        self.assertIn("Status: blocked", detail)
        self.assertIn("Parent: B0090", detail)
        self.assertIn("Feature Root: B0030", detail)
        self.assertIn("Dependencies: B0098", detail)
        self.assertIn("  - Build rows", detail)
        self.assertIn("  changed: src/codex_orchestrator/tui.py, tests/test_orchestrator.py", detail)
        self.assertIn("  next_agent: review", detail)
        self.assertIn("  conflict_risks: Coordinate with review bead on footer text.", detail)
        self.assertEqual(
            "filter=default | rows=1 | selected=1 | open=0 | ready=0 | in_progress=0 | blocked=1 | handed_off=0 | done=0",
            footer,
        )

    def test_tui_detail_panel_handles_empty_selection_and_empty_scope_lists(self) -> None:
        self.assertEqual("No bead selected.", format_detail_panel(None))

        bead = Bead(
            bead_id="B0100",
            title="Empty detail state",
            agent_type="tester",
            description="verify formatter fallbacks",
        )

        detail = format_detail_panel(bead)

        self.assertIn("Dependencies: -", detail)
        self.assertIn("Acceptance Criteria:\n  -", detail)
        self.assertIn("Block Reason: -", detail)
        self.assertIn("  expected: -", detail)
        self.assertIn("  conflict_risks: -", detail)

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

    def test_tui_runtime_merge_rejects_non_done_beads(self) -> None:
        self.storage.create_bead(bead_id="B0001", title="Ready", agent_type="developer", description="one", status=BEAD_READY)
        state = TuiRuntimeState(self.storage)

        state.request_merge()

        self.assertFalse(state.awaiting_merge_confirmation)
        self.assertIn("only done beads can be merged", state.status_message)

    def test_tui_runtime_merge_uses_existing_merge_path_and_survives_failure(self) -> None:
        bead = self.storage.create_bead(bead_id="B0001", title="Done", agent_type="developer", description="one", status=BEAD_DONE)
        state = TuiRuntimeState(self.storage, filter_mode=FILTER_ALL)

        state.request_merge()
        self.assertTrue(state.awaiting_merge_confirmation)

        merge_calls: list[str] = []

        def fake_merge(args: Namespace, storage: RepositoryStorage, console: ConsoleReporter) -> int:
            merge_calls.append(args.bead_id)
            raise RuntimeError("merge conflict")

        merged = state.confirm_merge(fake_merge)

        self.assertFalse(merged)
        self.assertEqual([bead.bead_id], merge_calls)
        self.assertFalse(state.awaiting_merge_confirmation)
        self.assertIn("Merge failed for B0001", state.status_message)

    def test_tui_runtime_merge_handles_system_exit_without_terminating_runtime(self) -> None:
        bead = self.storage.create_bead(bead_id="B0001", title="Done", agent_type="developer", description="one", status=BEAD_DONE)
        state = TuiRuntimeState(self.storage, filter_mode=FILTER_ALL)

        state.request_merge()
        self.assertTrue(state.awaiting_merge_confirmation)

        def fake_merge(args: Namespace, storage: RepositoryStorage, console: ConsoleReporter) -> int:
            raise SystemExit(f"{args.bead_id} has no feature branch to merge")

        merged = state.confirm_merge(fake_merge)

        self.assertFalse(merged)
        self.assertFalse(state.awaiting_merge_confirmation)
        self.assertEqual(f"Merge failed for {bead.bead_id}.", state.status_message)
        self.assertIn("has no feature branch to merge", state.activity_message)

    def test_tui_runtime_merge_confirms_success_and_refreshes_messages(self) -> None:
        bead = self.storage.create_bead(bead_id="B0001", title="Done", agent_type="developer", description="one", status=BEAD_DONE)
        state = TuiRuntimeState(self.storage, filter_mode=FILTER_ALL)

        state.request_merge()
        self.assertTrue(state.awaiting_merge_confirmation)

        def fake_merge(args: Namespace, storage: RepositoryStorage, console: ConsoleReporter) -> int:
            self.assertEqual(bead.bead_id, args.bead_id)
            console.info("merge ok")
            return 0

        merged = state.confirm_merge(fake_merge)

        self.assertTrue(merged)
        self.assertFalse(state.awaiting_merge_confirmation)
        self.assertEqual(f"Merged {bead.bead_id}.", state.status_message)
        self.assertIn("merge ok", state.activity_message)
        self.assertEqual(bead.bead_id, state.selected_bead_id)

    def test_tui_render_tree_panel_marks_selected_row(self) -> None:
        rows = build_tree_rows([
            Bead(bead_id="B0001", title="One", agent_type="developer", description="one", status=BEAD_READY),
            Bead(bead_id="B0002", title="Two", agent_type="developer", description="two", status=BEAD_BLOCKED),
        ])

        panel = render_tree_panel(rows, 1)

        self.assertIn("> B0002 · Two [blocked]", panel)
        self.assertIn("  B0001 · One [ready]", panel)

    def test_run_tui_returns_nonzero_and_hint_when_textual_missing(self) -> None:
        stream = io.StringIO()

        with patch("codex_orchestrator.tui.load_textual_runtime", side_effect=RuntimeError("missing textual")):
            exit_code = run_tui(self.storage, stream=stream)

        self.assertEqual(1, exit_code)
        self.assertIn("Hint: install project dependencies", stream.getvalue())


if __name__ == "__main__":
    unittest.main()
