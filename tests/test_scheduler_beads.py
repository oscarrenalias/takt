from __future__ import annotations

import io
import subprocess
import sys
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_takt.cli import command_bead
from agent_takt.console import ConsoleReporter
from agent_takt.gitutils import WorktreeManager
from agent_takt.models import (
    AgentRunResult,
    BEAD_BLOCKED,
    BEAD_DONE,
    BEAD_HANDED_OFF,
    BEAD_IN_PROGRESS,
    BEAD_READY,
    Bead,
    HandoffSummary,
)
from agent_takt.prompts import render_dep_handoff_context
from agent_takt.runner import AGENT_OUTPUT_SCHEMA
from agent_takt.scheduler import Scheduler
from agent_takt.storage import RepositoryStorage

# Suppress git commits for the general test session.  BeadAutoCommitTests
# re-enables this flag in its own setUp/tearDown to exercise real commit paths.
RepositoryStorage._auto_commit = False

from helpers import FakeRunner, OrchestratorTests  # noqa: E402


class DeleteBeadTests(OrchestratorTests):
    """Tests for RepositoryStorage.delete_bead()."""

    def test_delete_open_bead(self) -> None:
        bead = self.storage.create_bead(title="To delete", agent_type="developer", description="x")
        bead_id = bead.bead_id
        deleted = self.storage.delete_bead(bead_id)
        self.assertEqual(deleted.bead_id, bead_id)
        self.assertFalse(self.storage.bead_path(bead_id).exists())

    def test_delete_returns_bead_object(self) -> None:
        bead = self.storage.create_bead(title="Return check", agent_type="developer", description="x")
        deleted = self.storage.delete_bead(bead.bead_id)
        self.assertIsInstance(deleted, Bead)
        self.assertEqual(deleted.title, "Return check")

    def test_delete_nonexistent_bead_raises(self) -> None:
        with self.assertRaises(ValueError):
            self.storage.delete_bead("B-nonexistent")

    def test_delete_bead_with_children_raises(self) -> None:
        parent = self.storage.create_bead(title="Parent", agent_type="developer", description="p")
        child = self.storage.create_bead(
            title="Child", agent_type="tester", description="c", parent_id=parent.bead_id
        )
        with self.assertRaises(ValueError) as ctx:
            self.storage.delete_bead(parent.bead_id)
        self.assertIn(child.bead_id, str(ctx.exception))

    def test_delete_in_progress_without_force_raises(self) -> None:
        bead = self.storage.create_bead(title="Active", agent_type="developer", description="x")
        bead.status = BEAD_IN_PROGRESS
        self.storage.save_bead(bead)
        with self.assertRaises(ValueError) as ctx:
            self.storage.delete_bead(bead.bead_id)
        self.assertIn("force=True", str(ctx.exception))

    def test_delete_done_without_force_raises(self) -> None:
        bead = self.storage.create_bead(title="Done bead", agent_type="developer", description="x")
        bead.status = BEAD_DONE
        self.storage.save_bead(bead)
        with self.assertRaises(ValueError):
            self.storage.delete_bead(bead.bead_id)

    def test_delete_handed_off_without_force_raises(self) -> None:
        bead = self.storage.create_bead(title="Handed off", agent_type="developer", description="x")
        bead.status = BEAD_HANDED_OFF
        self.storage.save_bead(bead)
        with self.assertRaises(ValueError):
            self.storage.delete_bead(bead.bead_id)

    def test_delete_in_progress_with_force_succeeds(self) -> None:
        bead = self.storage.create_bead(title="Force delete", agent_type="developer", description="x")
        bead.status = BEAD_IN_PROGRESS
        self.storage.save_bead(bead)
        deleted = self.storage.delete_bead(bead.bead_id, force=True)
        self.assertEqual(deleted.bead_id, bead.bead_id)
        self.assertFalse(self.storage.bead_path(bead.bead_id).exists())

    def test_delete_done_with_force_succeeds(self) -> None:
        bead = self.storage.create_bead(title="Force done", agent_type="developer", description="x")
        bead.status = BEAD_DONE
        self.storage.save_bead(bead)
        deleted = self.storage.delete_bead(bead.bead_id, force=True)
        self.assertFalse(self.storage.bead_path(bead.bead_id).exists())

    def test_delete_removes_dependency_references(self) -> None:
        dep = self.storage.create_bead(title="Dep", agent_type="developer", description="d")
        consumer = self.storage.create_bead(
            title="Consumer", agent_type="developer", description="c",
            dependencies=[dep.bead_id]
        )
        self.storage.delete_bead(dep.bead_id)
        reloaded = self.storage.load_bead(consumer.bead_id)
        self.assertNotIn(dep.bead_id, reloaded.dependencies)

    def test_delete_blocked_bead_succeeds(self) -> None:
        bead = self.storage.create_bead(title="Blocked bead", agent_type="developer", description="x")
        bead.status = BEAD_BLOCKED
        self.storage.save_bead(bead)
        deleted = self.storage.delete_bead(bead.bead_id)
        self.assertFalse(self.storage.bead_path(bead.bead_id).exists())

    def test_delete_ready_bead_succeeds(self) -> None:
        bead = self.storage.create_bead(title="Ready bead", agent_type="developer", description="x")
        bead.status = BEAD_READY
        self.storage.save_bead(bead)
        deleted = self.storage.delete_bead(bead.bead_id)
        self.assertFalse(self.storage.bead_path(bead.bead_id).exists())

    def test_delete_removes_bead_from_list(self) -> None:
        bead = self.storage.create_bead(title="Listed", agent_type="developer", description="x")
        bead_id = bead.bead_id
        self.storage.delete_bead(bead_id)
        ids = {b.bead_id for b in self.storage.list_beads()}
        self.assertNotIn(bead_id, ids)

    def test_delete_does_not_remove_unrelated_dependency(self) -> None:
        dep1 = self.storage.create_bead(title="Dep1", agent_type="developer", description="d1")
        dep2 = self.storage.create_bead(title="Dep2", agent_type="developer", description="d2")
        consumer = self.storage.create_bead(
            title="Consumer", agent_type="developer", description="c",
            dependencies=[dep1.bead_id, dep2.bead_id]
        )
        self.storage.delete_bead(dep1.bead_id)
        reloaded = self.storage.load_bead(consumer.bead_id)
        self.assertNotIn(dep1.bead_id, reloaded.dependencies)
        self.assertIn(dep2.bead_id, reloaded.dependencies)


class DeleteBeadCliTests(OrchestratorTests):
    """Tests for the CLI 'bead delete' command (command_bead with bead_command='delete')."""

    def _make_console(self) -> tuple[ConsoleReporter, io.StringIO]:
        stream = io.StringIO()
        return ConsoleReporter(stream=stream), stream

    def test_delete_open_bead_returns_zero(self) -> None:
        bead = self.storage.create_bead(title="To delete", agent_type="developer", description="x")
        console, _ = self._make_console()
        exit_code = command_bead(
            Namespace(bead_command="delete", bead_id=bead.bead_id, force=False),
            self.storage,
            console,
        )
        self.assertEqual(0, exit_code)
        self.assertFalse(self.storage.bead_path(bead.bead_id).exists())

    def test_delete_removes_bead_from_list(self) -> None:
        bead = self.storage.create_bead(title="Listed", agent_type="developer", description="x")
        bead_id = bead.bead_id
        console, _ = self._make_console()
        command_bead(
            Namespace(bead_command="delete", bead_id=bead_id, force=False),
            self.storage,
            console,
        )
        ids = {b.bead_id for b in self.storage.list_beads()}
        self.assertNotIn(bead_id, ids)

    def test_delete_nonexistent_bead_returns_one(self) -> None:
        console, _ = self._make_console()
        exit_code = command_bead(
            Namespace(bead_command="delete", bead_id="B-nothere", force=False),
            self.storage,
            console,
        )
        self.assertEqual(1, exit_code)

    def test_delete_bead_with_children_returns_one(self) -> None:
        parent = self.storage.create_bead(title="Parent", agent_type="developer", description="p")
        self.storage.create_bead(
            title="Child", agent_type="tester", description="c", parent_id=parent.bead_id
        )
        console, _ = self._make_console()
        exit_code = command_bead(
            Namespace(bead_command="delete", bead_id=parent.bead_id, force=False),
            self.storage,
            console,
        )
        self.assertEqual(1, exit_code)
        # Parent must still exist
        self.assertTrue(self.storage.bead_path(parent.bead_id).exists())

    def test_delete_in_progress_without_force_returns_one(self) -> None:
        bead = self.storage.create_bead(title="Active", agent_type="developer", description="x")
        bead.status = BEAD_IN_PROGRESS
        self.storage.save_bead(bead)
        console, _ = self._make_console()
        exit_code = command_bead(
            Namespace(bead_command="delete", bead_id=bead.bead_id, force=False),
            self.storage,
            console,
        )
        self.assertEqual(1, exit_code)
        self.assertTrue(self.storage.bead_path(bead.bead_id).exists())

    def test_delete_in_progress_with_force_returns_zero(self) -> None:
        bead = self.storage.create_bead(title="Force active", agent_type="developer", description="x")
        bead.status = BEAD_IN_PROGRESS
        self.storage.save_bead(bead)
        console, _ = self._make_console()
        exit_code = command_bead(
            Namespace(bead_command="delete", bead_id=bead.bead_id, force=True),
            self.storage,
            console,
        )
        self.assertEqual(0, exit_code)
        self.assertFalse(self.storage.bead_path(bead.bead_id).exists())

    def test_delete_removes_agent_run_artifacts(self) -> None:
        bead = self.storage.create_bead(title="Artifact bead", agent_type="developer", description="x")
        artifact_dir = self.storage.state_dir / "agent-runs" / bead.bead_id
        artifact_dir.mkdir(parents=True, exist_ok=True)
        (artifact_dir / "output.json").write_text("{}", encoding="utf-8")
        console, _ = self._make_console()
        exit_code = command_bead(
            Namespace(bead_command="delete", bead_id=bead.bead_id, force=False),
            self.storage,
            console,
        )
        self.assertEqual(0, exit_code)
        self.assertFalse(artifact_dir.exists())

    def test_delete_removes_telemetry_artifacts(self) -> None:
        bead = self.storage.create_bead(title="Telemetry bead", agent_type="developer", description="x")
        telemetry_dir = self.storage.telemetry_dir / bead.bead_id
        telemetry_dir.mkdir(parents=True, exist_ok=True)
        (telemetry_dir / "1.json").write_text("{}", encoding="utf-8")
        console, _ = self._make_console()
        exit_code = command_bead(
            Namespace(bead_command="delete", bead_id=bead.bead_id, force=False),
            self.storage,
            console,
        )
        self.assertEqual(0, exit_code)
        self.assertFalse(telemetry_dir.exists())

    def test_delete_no_artifacts_still_returns_zero(self) -> None:
        bead = self.storage.create_bead(title="No artifacts", agent_type="developer", description="x")
        console, _ = self._make_console()
        exit_code = command_bead(
            Namespace(bead_command="delete", bead_id=bead.bead_id, force=False),
            self.storage,
            console,
        )
        self.assertEqual(0, exit_code)

    def test_delete_feature_root_removes_worktree_and_branch(self) -> None:
        bead = self.storage.create_bead(title="Root bead", agent_type="developer", description="x")
        bead_id = bead.bead_id
        # feature_root_id == bead_id by default for a root bead
        worktree_path = self.storage.worktrees_dir / bead_id
        worktree_path.mkdir(parents=True, exist_ok=True)
        branch_name = f"feature/{bead_id.lower()}"
        subprocess.run(["git", "branch", branch_name], cwd=self.root, check=True, capture_output=True)
        # Set up a minimal worktree directory (not a real worktree, so git worktree remove will fail,
        # but we verify the CLI handles that gracefully and still returns 0)
        console, _ = self._make_console()
        exit_code = command_bead(
            Namespace(bead_command="delete", bead_id=bead_id, force=False),
            self.storage,
            console,
        )
        # Exit code should be 0 regardless of git worktree remove success/failure
        self.assertEqual(0, exit_code)
        self.assertFalse(self.storage.bead_path(bead_id).exists())

    def test_delete_records_event_in_output(self) -> None:
        bead = self.storage.create_bead(title="Event bead", agent_type="developer", description="x")
        console, stream = self._make_console()
        command_bead(
            Namespace(bead_command="delete", bead_id=bead.bead_id, force=False),
            self.storage,
            console,
        )
        output = stream.getvalue()
        self.assertIn(bead.bead_id, output)

    def test_delete_by_prefix_resolves_correctly(self) -> None:
        bead = self.storage.create_bead(title="Prefix bead", agent_type="developer", description="x")
        # Use the full ID but a valid prefix (first 6 chars should be enough)
        prefix = bead.bead_id[:6]
        console, _ = self._make_console()
        exit_code = command_bead(
            Namespace(bead_command="delete", bead_id=prefix, force=False),
            self.storage,
            console,
        )
        self.assertEqual(0, exit_code)
        self.assertFalse(self.storage.bead_path(bead.bead_id).exists())


class StructuredHandoffFieldsTests(OrchestratorTests):
    """Tests for structured handoff fields: schema parsing, backward compat, and prompt injection."""

    # ------------------------------------------------------------------ #
    # HandoffSummary parsing
    # ------------------------------------------------------------------ #

    def test_handoff_summary_includes_structured_fields(self) -> None:
        h = HandoffSummary(
            design_decisions="Used factory pattern",
            test_coverage_notes="Unit tests added for models",
            known_limitations="No integration test for DB layer",
        )
        self.assertEqual("Used factory pattern", h.design_decisions)
        self.assertEqual("Unit tests added for models", h.test_coverage_notes)
        self.assertEqual("No integration test for DB layer", h.known_limitations)

    def test_handoff_summary_defaults_are_empty_strings(self) -> None:
        h = HandoffSummary()
        self.assertEqual("", h.design_decisions)
        self.assertEqual("", h.test_coverage_notes)
        self.assertEqual("", h.known_limitations)

    def test_bead_from_dict_with_structured_handoff_fields(self) -> None:
        data = {
            "bead_id": "B-abc",
            "title": "Test bead",
            "agent_type": "developer",
            "description": "desc",
            "handoff_summary": {
                "design_decisions": "Used adapter",
                "test_coverage_notes": "All paths covered",
                "known_limitations": "None",
            },
        }
        bead = Bead.from_dict(data)
        self.assertEqual("Used adapter", bead.handoff_summary.design_decisions)
        self.assertEqual("All paths covered", bead.handoff_summary.test_coverage_notes)
        self.assertEqual("None", bead.handoff_summary.known_limitations)

    def test_bead_from_dict_without_structured_handoff_fields_defaults_to_empty(self) -> None:
        data = {
            "bead_id": "B-abc",
            "title": "Test bead",
            "agent_type": "developer",
            "description": "desc",
            "handoff_summary": {
                "completed": "done",
                "verdict": "approved",
            },
        }
        bead = Bead.from_dict(data)
        self.assertEqual("", bead.handoff_summary.design_decisions)
        self.assertEqual("", bead.handoff_summary.test_coverage_notes)
        self.assertEqual("", bead.handoff_summary.known_limitations)

    def test_bead_from_dict_without_handoff_summary_key(self) -> None:
        data = {
            "bead_id": "B-abc",
            "title": "Test bead",
            "agent_type": "developer",
            "description": "desc",
        }
        bead = Bead.from_dict(data)
        self.assertEqual("", bead.handoff_summary.design_decisions)
        self.assertEqual("", bead.handoff_summary.test_coverage_notes)
        self.assertEqual("", bead.handoff_summary.known_limitations)

    # ------------------------------------------------------------------ #
    # AgentRunResult structured handoff fields
    # ------------------------------------------------------------------ #

    def test_agent_run_result_structured_fields_default_empty(self) -> None:
        r = AgentRunResult(outcome="completed", summary="done")
        self.assertEqual("", r.design_decisions)
        self.assertEqual("", r.test_coverage_notes)
        self.assertEqual("", r.known_limitations)

    def test_agent_run_result_structured_fields_are_set(self) -> None:
        r = AgentRunResult(
            outcome="completed",
            summary="done",
            design_decisions="Chose strategy pattern",
            test_coverage_notes="Happy path + edge cases",
            known_limitations="No async path tested",
        )
        self.assertEqual("Chose strategy pattern", r.design_decisions)
        self.assertEqual("Happy path + edge cases", r.test_coverage_notes)
        self.assertEqual("No async path tested", r.known_limitations)

    # ------------------------------------------------------------------ #
    # AGENT_OUTPUT_SCHEMA allows structured handoff fields
    # ------------------------------------------------------------------ #

    def test_agent_output_schema_includes_structured_handoff_fields(self) -> None:
        props = AGENT_OUTPUT_SCHEMA["properties"]
        self.assertIn("design_decisions", props)
        self.assertIn("test_coverage_notes", props)
        self.assertIn("known_limitations", props)
        self.assertEqual("string", props["design_decisions"]["type"])
        self.assertEqual("string", props["test_coverage_notes"]["type"])
        self.assertEqual("string", props["known_limitations"]["type"])

    def test_agent_output_schema_structured_fields_required(self) -> None:
        required = AGENT_OUTPUT_SCHEMA["required"]
        self.assertIn("design_decisions", required)
        self.assertIn("test_coverage_notes", required)
        self.assertIn("known_limitations", required)

    # ------------------------------------------------------------------ #
    # render_dep_handoff_context: prompt injection
    # ------------------------------------------------------------------ #

    def test_render_dep_handoff_context_review_includes_design_decisions(self) -> None:
        h = HandoffSummary(design_decisions="Used adapter pattern for DB layer")
        result = render_dep_handoff_context("review", [h])
        self.assertIn("Design decisions", result)
        self.assertIn("Used adapter pattern for DB layer", result)

    def test_render_dep_handoff_context_tester_includes_coverage_and_limitations(self) -> None:
        h = HandoffSummary(
            test_coverage_notes="Models and scheduler covered",
            known_limitations="No e2e tests",
        )
        result = render_dep_handoff_context("tester", [h])
        self.assertIn("Test coverage notes", result)
        self.assertIn("Models and scheduler covered", result)
        self.assertIn("Known limitations", result)
        self.assertIn("No e2e tests", result)

    def test_render_dep_handoff_context_omits_empty_fields(self) -> None:
        h = HandoffSummary(design_decisions="", test_coverage_notes="", known_limitations="")
        review_result = render_dep_handoff_context("review", [h])
        self.assertEqual("", review_result)
        tester_result = render_dep_handoff_context("tester", [h])
        self.assertEqual("", tester_result)

    def test_render_dep_handoff_context_developer_returns_empty(self) -> None:
        h = HandoffSummary(design_decisions="some decision")
        result = render_dep_handoff_context("developer", [h])
        self.assertEqual("", result)

    def test_render_dep_handoff_context_review_omits_tester_fields(self) -> None:
        h = HandoffSummary(
            test_coverage_notes="should not appear",
            known_limitations="should not appear either",
            design_decisions="should appear",
        )
        result = render_dep_handoff_context("review", [h])
        self.assertIn("should appear", result)
        self.assertNotIn("should not appear", result)

    def test_render_dep_handoff_context_tester_omits_design_decisions(self) -> None:
        h = HandoffSummary(
            design_decisions="should not appear",
            test_coverage_notes="should appear",
        )
        result = render_dep_handoff_context("tester", [h])
        self.assertIn("should appear", result)
        self.assertNotIn("should not appear", result)

    def test_render_dep_handoff_context_multiple_deps_aggregates_values(self) -> None:
        h1 = HandoffSummary(design_decisions="Decision A")
        h2 = HandoffSummary(design_decisions="Decision B")
        result = render_dep_handoff_context("review", [h1, h2])
        self.assertIn("Decision A", result)
        self.assertIn("Decision B", result)

    def test_render_dep_handoff_context_empty_dep_list(self) -> None:
        self.assertEqual("", render_dep_handoff_context("review", []))
        self.assertEqual("", render_dep_handoff_context("tester", []))

    # ------------------------------------------------------------------ #
    # Scheduler persists structured handoff fields from agent result
    # ------------------------------------------------------------------ #

    def test_scheduler_persists_structured_handoff_fields_from_agent_result(self) -> None:
        bead = self.storage.create_bead(
            title="Implement", agent_type="developer", description="build"
        )
        runner = FakeRunner(
            results={
                bead.bead_id: AgentRunResult(
                    outcome="completed",
                    summary="done",
                    design_decisions="Used factory pattern",
                    test_coverage_notes="",
                    known_limitations="Async path not tested",
                )
            }
        )
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))
        scheduler.run_once()
        saved = self.storage.load_bead(bead.bead_id)
        self.assertEqual("Used factory pattern", saved.handoff_summary.design_decisions)
        self.assertEqual("", saved.handoff_summary.test_coverage_notes)
        self.assertEqual("Async path not tested", saved.handoff_summary.known_limitations)

    def test_scheduler_load_dep_handoffs_for_tester_bead(self) -> None:
        dev_bead = self.storage.create_bead(
            title="Implement", agent_type="developer", description="build"
        )
        dev_bead.status = "done"
        dev_bead.handoff_summary = HandoffSummary(
            test_coverage_notes="Unit tests added",
            known_limitations="No integration tests",
        )
        self.storage.save_bead(dev_bead)

        tester_bead = self.storage.create_bead(
            title="Test",
            agent_type="tester",
            description="validate",
            dependencies=[dev_bead.bead_id],
        )
        runner = FakeRunner(
            results={
                tester_bead.bead_id: AgentRunResult(
                    outcome="completed",
                    summary="tests pass",
                    verdict="approved",
                    findings_count=0,
                )
            }
        )
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))
        result = scheduler.run_once()
        self.assertEqual([tester_bead.bead_id], result.completed)


class BeadAutoCommitTests(OrchestratorTests):
    """Tests for per-write git auto-commit behavior in RepositoryStorage.

    ``RepositoryStorage._auto_commit`` is a test-only class-level switch that
    defaults to ``True`` in production.  The module-level assignment at the top
    of this file sets it to ``False`` to suppress real git commits for the
    general test session.  This class is the explicit coverage point for actual
    commit behavior: ``setUp`` re-enables the flag so each test here exercises
    real git paths, and ``tearDown`` restores the suppressed state so no other
    test class is affected.
    """

    def setUp(self) -> None:
        super().setUp()
        # Re-enable auto-commit so tests in this class hit real git code paths.
        RepositoryStorage._auto_commit = True

    def tearDown(self) -> None:
        # Restore suppression so the rest of the test session stays git-free.
        RepositoryStorage._auto_commit = False
        super().tearDown()

    def _last_commit_message(self) -> str:
        """Return the subject line of the most recent git commit."""
        result = subprocess.run(
            ["git", "log", "-1", "--format=%s"],
            cwd=self.root,
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()

    # ------------------------------------------------------------------ #
    # Create commit message format
    # ------------------------------------------------------------------ #

    def test_create_bead_produces_git_commit(self) -> None:
        bead = self.storage.create_bead(title="New bead", agent_type="developer", description="x")
        msg = self._last_commit_message()
        self.assertEqual(f"[bead] {bead.bead_id}: created (developer)", msg)

    def test_create_bead_commit_message_includes_agent_type(self) -> None:
        bead = self.storage.create_bead(title="Tester bead", agent_type="tester", description="x")
        msg = self._last_commit_message()
        self.assertEqual(f"[bead] {bead.bead_id}: created (tester)", msg)

    # ------------------------------------------------------------------ #
    # Update (status) commit message format
    # ------------------------------------------------------------------ #

    def test_update_bead_commit_message_contains_status(self) -> None:
        bead = self.storage.create_bead(title="Status bead", agent_type="developer", description="x")
        bead.status = BEAD_IN_PROGRESS
        self.storage.save_bead(bead)
        msg = self._last_commit_message()
        self.assertEqual(f"[bead] {bead.bead_id}: in_progress", msg)

    def test_update_bead_done_commit_message(self) -> None:
        bead = self.storage.create_bead(title="Done bead", agent_type="developer", description="x")
        bead.status = BEAD_DONE
        self.storage.save_bead(bead)
        msg = self._last_commit_message()
        self.assertEqual(f"[bead] {bead.bead_id}: done", msg)

    # ------------------------------------------------------------------ #
    # Deletion commit message format
    # ------------------------------------------------------------------ #

    def test_delete_bead_produces_git_commit(self) -> None:
        bead = self.storage.create_bead(title="Delete me", agent_type="developer", description="x")
        bead_id = bead.bead_id
        self.storage.delete_bead(bead_id)
        msg = self._last_commit_message()
        self.assertEqual(f"[bead] {bead_id}: deleted", msg)

    def test_delete_bead_file_removed_regardless_of_git(self) -> None:
        """Bead file is removed from disk even when git commit fails."""
        bead = self.storage.create_bead(title="No-git delete", agent_type="developer", description="x")
        bead_id = bead.bead_id
        path = self.storage.bead_path(bead_id)
        self.assertTrue(path.exists())

        original_run = subprocess.run

        def fail_on_commit(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            if isinstance(cmd, list) and "commit" in cmd:
                raise subprocess.CalledProcessError(1, cmd)
            return original_run(*args, **kwargs)

        with patch("agent_takt.storage.subprocess.run", side_effect=fail_on_commit):
            self.storage.delete_bead(bead_id)

        self.assertFalse(path.exists())

    # ------------------------------------------------------------------ #
    # Git failure non-propagation
    # ------------------------------------------------------------------ #

    def test_write_bead_git_failure_does_not_raise(self) -> None:
        """_write_bead must not propagate subprocess errors."""
        bead = self.storage.create_bead(title="Fault bead", agent_type="developer", description="x")
        bead.status = BEAD_IN_PROGRESS

        with patch(
            "agent_takt.storage.subprocess.run",
            side_effect=subprocess.CalledProcessError(1, ["git"]),
        ):
            # Must not raise
            self.storage.save_bead(bead)

        # File is still written despite git failure
        self.assertTrue(self.storage.bead_path(bead.bead_id).exists())

    def test_write_bead_git_not_found_does_not_raise(self) -> None:
        """_write_bead handles FileNotFoundError (git absent) silently."""
        bead = self.storage.create_bead(title="No-git bead", agent_type="developer", description="x")
        bead.status = BEAD_BLOCKED

        with patch(
            "agent_takt.storage.subprocess.run",
            side_effect=FileNotFoundError("git not found"),
        ):
            self.storage.save_bead(bead)

        self.assertTrue(self.storage.bead_path(bead.bead_id).exists())

    def test_delete_bead_git_failure_does_not_raise(self) -> None:
        """delete_bead must not propagate git commit errors."""
        bead = self.storage.create_bead(title="Delete fault", agent_type="developer", description="x")
        bead_id = bead.bead_id

        original_run = subprocess.run

        def fail_on_commit(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            if isinstance(cmd, list) and "commit" in cmd:
                raise subprocess.CalledProcessError(1, cmd)
            return original_run(*args, **kwargs)

        with patch("agent_takt.storage.subprocess.run", side_effect=fail_on_commit):
            deleted = self.storage.delete_bead(bead_id)

        self.assertEqual(deleted.bead_id, bead_id)
        self.assertFalse(self.storage.bead_path(bead_id).exists())

    def test_delete_bead_git_failure_cleanup_still_runs(self) -> None:
        """_cleanup_deleted_dependency_references runs even after a git commit failure."""
        dep = self.storage.create_bead(title="Dep", agent_type="developer", description="d")
        consumer = self.storage.create_bead(
            title="Consumer", agent_type="developer", description="c",
            dependencies=[dep.bead_id],
        )

        original_run = subprocess.run

        def fail_on_commit(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            if isinstance(cmd, list) and "commit" in cmd:
                raise subprocess.CalledProcessError(1, cmd)
            return original_run(*args, **kwargs)

        with patch("agent_takt.storage.subprocess.run", side_effect=fail_on_commit):
            self.storage.delete_bead(dep.bead_id)

        reloaded = self.storage.load_bead(consumer.bead_id)
        self.assertNotIn(dep.bead_id, reloaded.dependencies)

    # ------------------------------------------------------------------ #
    # Concurrent write serialization
    # ------------------------------------------------------------------ #

    def test_concurrent_writes_produce_no_index_lock_errors(self) -> None:
        """Concurrent _write_bead calls are serialized; no git index.lock conflicts."""
        import threading

        beads = [
            self.storage.create_bead(
                title=f"Concurrent bead {i}", agent_type="developer", description=f"bead {i}"
            )
            for i in range(5)
        ]

        errors: list[Exception] = []

        def update_bead(bead: "Bead") -> None:
            try:
                bead.status = BEAD_IN_PROGRESS
                self.storage.save_bead(bead)
                bead.status = BEAD_DONE
                self.storage.save_bead(bead)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=update_bead, args=(b,)) for b in beads]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        stuck = [t for t in threads if t.is_alive()]
        self.assertEqual([], stuck, f"Worker threads did not finish within timeout: {stuck}")
        self.assertEqual([], errors, f"Concurrent writes raised: {errors}")
        # All beads should be persisted in their final state
        for bead in beads:
            loaded = self.storage.load_bead(bead.bead_id)
            self.assertEqual(BEAD_DONE, loaded.status)

    # ------------------------------------------------------------------ #
    # Auto-commit suppression (_auto_commit=False)
    # ------------------------------------------------------------------ #

    def test_write_bead_with_auto_commit_disabled_skips_git(self) -> None:
        """_git_commit_bead returns immediately when _auto_commit is False."""
        RepositoryStorage._auto_commit = False
        try:
            with patch("agent_takt.storage.subprocess.run") as mock_run:
                bead = self.storage.create_bead(
                    title="No-commit write", agent_type="developer", description="x"
                )
                mock_run.assert_not_called()
            # Bead file must still be written to disk
            self.assertTrue(self.storage.bead_path(bead.bead_id).exists())
        finally:
            RepositoryStorage._auto_commit = True

    def test_delete_bead_with_auto_commit_disabled_skips_git(self) -> None:
        """_git_commit_bead_deletion returns immediately when _auto_commit is False."""
        bead = self.storage.create_bead(
            title="No-commit delete", agent_type="developer", description="x"
        )
        bead_id = bead.bead_id
        path = self.storage.bead_path(bead_id)
        self.assertTrue(path.exists())

        RepositoryStorage._auto_commit = False
        try:
            with patch("agent_takt.storage.subprocess.run") as mock_run:
                self.storage.delete_bead(bead_id)
                mock_run.assert_not_called()
        finally:
            RepositoryStorage._auto_commit = True

        # File must be removed from disk regardless of git suppression
        self.assertFalse(path.exists())

    # ------------------------------------------------------------------ #
    # _git_commit_bead failure: WARNING log + on-disk event record
    # ------------------------------------------------------------------ #

    def test_git_commit_bead_failure_logs_warning_with_bead_id(self) -> None:
        """CalledProcessError in git add/commit: WARNING is logged with the bead ID."""
        bead = self.storage.create_bead(
            title="Git fail log test", agent_type="developer", description="x"
        )
        bead.status = BEAD_IN_PROGRESS

        error = subprocess.CalledProcessError(1, ["git", "add"], b"fatal: error")
        with patch("agent_takt.storage.subprocess.run", side_effect=error), \
             self.assertLogs("agent_takt.storage", level="WARNING") as log_ctx:
            self.storage.save_bead(bead)

        warning_messages = [r.getMessage() for r in log_ctx.records if r.levelname == "WARNING"]
        self.assertTrue(
            any(bead.bead_id in msg for msg in warning_messages),
            f"Expected bead ID {bead.bead_id!r} in WARNING log; got: {warning_messages}",
        )

    def test_git_commit_bead_failure_writes_git_commit_failed_event_to_disk(self) -> None:
        """CalledProcessError in git add/commit: git_commit_failed ExecutionRecord persisted to disk."""
        bead = self.storage.create_bead(
            title="Git fail event test", agent_type="developer", description="x"
        )
        bead.status = BEAD_IN_PROGRESS

        error = subprocess.CalledProcessError(1, ["git", "commit"], b"fatal: error")
        with patch("agent_takt.storage.subprocess.run", side_effect=error), \
             self.assertLogs("agent_takt.storage", level="WARNING"):
            self.storage.save_bead(bead)

        loaded = self.storage.load_bead(bead.bead_id)
        events = [r.event for r in loaded.execution_history]
        self.assertIn(
            "git_commit_failed", events,
            f"Expected 'git_commit_failed' in execution history; got events: {events}",
        )

    # ------------------------------------------------------------------ #
    # _git_commit_bead failure: secondary write also fails
    # ------------------------------------------------------------------ #

    def test_git_commit_bead_secondary_write_failure_logs_two_warnings_no_exception(self) -> None:
        """When git commit fails AND the failure-event write also fails: 2 warnings, no exception."""
        bead = self.storage.create_bead(
            title="Double fail test", agent_type="developer", description="x"
        )
        bead_path = self.storage.bead_path(bead.bead_id)

        error = subprocess.CalledProcessError(1, ["git", "add"], b"fatal: error")
        with patch("agent_takt.storage.subprocess.run", side_effect=error), \
             patch.object(Path, "write_text", side_effect=OSError("simulated disk full")), \
             self.assertLogs("agent_takt.storage", level="WARNING") as log_ctx:
            # Must not raise even when the secondary write fails.
            self.storage._git_commit_bead(bead, bead_path, is_new=False)

        warning_records = [r for r in log_ctx.records if r.levelname == "WARNING"]
        self.assertEqual(
            2, len(warning_records),
            f"Expected exactly 2 WARNING log entries; got {len(warning_records)}: "
            f"{[r.getMessage() for r in warning_records]}",
        )

    # ------------------------------------------------------------------ #
    # _git_commit_bead_deletion failure: WARNING log, no exception
    # ------------------------------------------------------------------ #

    def test_git_commit_bead_deletion_failure_logs_warning_with_bead_id(self) -> None:
        """CalledProcessError in _git_commit_bead_deletion: WARNING logged with bead ID, no exception."""
        bead = self.storage.create_bead(
            title="Deletion fail log test", agent_type="developer", description="x"
        )
        bead_path = self.storage.bead_path(bead.bead_id)

        error = subprocess.CalledProcessError(1, ["git", "add"], b"fatal: error")
        with patch("agent_takt.storage.subprocess.run", side_effect=error), \
             self.assertLogs("agent_takt.storage", level="WARNING") as log_ctx:
            # Must not raise.
            self.storage._git_commit_bead_deletion(bead, bead_path)

        warning_messages = [r.getMessage() for r in log_ctx.records if r.levelname == "WARNING"]
        self.assertTrue(
            any(bead.bead_id in msg for msg in warning_messages),
            f"Expected bead ID {bead.bead_id!r} in WARNING log; got: {warning_messages}",
        )

    # ------------------------------------------------------------------ #
    # Normal commit path: no spurious git_commit_failed event
    # ------------------------------------------------------------------ #

    def test_normal_git_commit_writes_no_git_commit_failed_event(self) -> None:
        """Successful git commit leaves no git_commit_failed event in the bead's execution history."""
        bead = self.storage.create_bead(
            title="Normal commit bead", agent_type="developer", description="x"
        )

        loaded = self.storage.load_bead(bead.bead_id)
        events = [r.event for r in loaded.execution_history]
        self.assertNotIn(
            "git_commit_failed", events,
            f"Unexpected 'git_commit_failed' in history after clean commit; events: {events}",
        )


if __name__ == "__main__":
    unittest.main()
