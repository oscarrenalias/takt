from __future__ import annotations

import io
import subprocess
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from codex_orchestrator.cli import command_bead, command_merge, command_plan
from codex_orchestrator.console import ConsoleReporter
from codex_orchestrator.gitutils import WorktreeManager
from codex_orchestrator.models import AgentRunResult, BEAD_BLOCKED, BEAD_DONE, BEAD_IN_PROGRESS, BEAD_READY, Bead, Lease, PlanChild, PlanProposal
from codex_orchestrator.planner import PlanningService
from codex_orchestrator.prompts import build_worker_prompt
from codex_orchestrator.prompts import render_context_snippets
from codex_orchestrator.runner import AGENT_OUTPUT_SCHEMA
from codex_orchestrator.scheduler import Scheduler
from codex_orchestrator.storage import RepositoryStorage


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

    def run_bead(self, bead: Bead, *, workdir: Path, context_paths: list[Path]) -> AgentRunResult:
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
        subprocess.run(["git", "add", "README.md"], cwd=self.root, check=True)
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
            }
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

    def test_cli_claims_outputs_active_scope(self) -> None:
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
        self.assertIn(bead.bead_id, stream.getvalue())
        self.assertIn("feature_root_id", stream.getvalue())
        self.assertIn("expected_files", stream.getvalue())

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


if __name__ == "__main__":
    unittest.main()
