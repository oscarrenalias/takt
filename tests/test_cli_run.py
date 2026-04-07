from __future__ import annotations

import io
import json
import sys
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import MagicMock

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_takt.cli import command_run
from agent_takt.cli.commands.run import CliSchedulerReporter
from agent_takt.console import ConsoleReporter
from agent_takt.gitutils import WorktreeManager
from agent_takt.models import AgentRunResult
from agent_takt.scheduler import Scheduler
from agent_takt.storage import RepositoryStorage

RepositoryStorage._auto_commit = False

from helpers import FakeRunner, OrchestratorTests as _OrchestratorBase  # noqa: E402


def _parse_run_summary_json(output: str) -> dict:
    """Extract and parse the JSON block embedded in command_run console output.

    console.dump_json emits json.dumps(payload, indent=2) so the JSON block
    always starts with '{' alone on its own line.
    """
    lines = output.splitlines()
    for i, line in enumerate(lines):
        if line == "{":
            return json.loads("\n".join(lines[i:]))
    raise AssertionError(f"No JSON block found in output:\n{output}")


class CliSchedulerReporterTests(unittest.TestCase):
    """Unit tests for CliSchedulerReporter console output paths."""

    def _make_bead(self, bead_id: str = "B-test1234", agent_type: str = "developer", title: str = "Test bead"):
        bead = MagicMock()
        bead.bead_id = bead_id
        bead.agent_type = agent_type
        bead.title = title
        return bead

    def test_lease_expired_emits_warning(self) -> None:
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)
        reporter = CliSchedulerReporter(console, max_workers=1)
        reporter.lease_expired("B-test1234")
        self.assertIn("Lease expired", stream.getvalue())
        self.assertIn("B-test1234", stream.getvalue())

    def test_bead_deferred_emits_warning(self) -> None:
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)
        reporter = CliSchedulerReporter(console, max_workers=1)
        bead = self._make_bead()
        reporter.bead_deferred(bead, "conflict detected")
        output = stream.getvalue()
        self.assertIn("B-test1234", output)
        self.assertIn("deferred", output)

    def test_bead_blocked_emits_warning(self) -> None:
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)
        reporter = CliSchedulerReporter(console, max_workers=1)
        bead = self._make_bead()
        reporter.bead_started(bead)
        reporter.bead_blocked(bead, "dependency not ready")
        output = stream.getvalue()
        self.assertIn("dependency not ready", output)

    def test_bead_failed_emits_error(self) -> None:
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)
        reporter = CliSchedulerReporter(console, max_workers=1)
        bead = self._make_bead()
        reporter.bead_started(bead)
        reporter.bead_failed(bead, "runner crashed")
        output = stream.getvalue()
        self.assertIn("runner crashed", output)

    def test_bead_completed_emits_detail(self) -> None:
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)
        reporter = CliSchedulerReporter(console, max_workers=1)
        bead = self._make_bead()
        reporter.bead_started(bead)
        reporter.bead_completed(bead, "all done", [])
        output = stream.getvalue()
        self.assertIn("B-test1234", output)

    def test_bead_completed_reports_created_children(self) -> None:
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)
        reporter = CliSchedulerReporter(console, max_workers=1)
        bead = self._make_bead()
        child = self._make_bead(bead_id="B-child001", agent_type="tester", title="Test child")
        reporter.bead_started(bead)
        reporter.bead_completed(bead, "done", [child])
        output = stream.getvalue()
        self.assertIn("B-child001", output)

    def test_worktree_ready_emits_detail(self) -> None:
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)
        reporter = CliSchedulerReporter(console, max_workers=1)
        bead = self._make_bead()
        reporter.worktree_ready(bead, "feature/b-test1234", Path("/tmp/worktree"))
        output = stream.getvalue()
        self.assertIn("worktree", output)
        self.assertIn("feature/b-test1234", output)


class RunCommandTests(_OrchestratorBase):

    def test_cli_run_resolves_feature_root_prefix(self) -> None:
        bead = self.storage.create_bead(title="Feature root", agent_type="developer", description="work")
        prefix = bead.bead_id[:4]
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)
        worktrees = WorktreeManager(self.root, self.storage.worktrees_dir)
        scheduler = Scheduler(self.storage, FakeRunner(), worktrees)
        exit_code = command_run(
            Namespace(feature_root=prefix, max_workers=1, once=True),
            scheduler,
            console,
        )
        self.assertEqual(0, exit_code)

    def test_cli_run_returns_error_on_invalid_feature_root_prefix(self) -> None:
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)
        worktrees = WorktreeManager(self.root, self.storage.worktrees_dir)
        scheduler = Scheduler(self.storage, FakeRunner(), worktrees)
        exit_code = command_run(
            Namespace(feature_root="B-nonexist", max_workers=1, once=True),
            scheduler,
            console,
        )
        self.assertEqual(1, exit_code)

    def test_cli_run_summary_json_format(self) -> None:
        """command_run emits a JSON summary with sorted lists, deferred_count, and final_state."""
        bead = self.storage.create_bead(title="Work", agent_type="developer", description="do work")
        bead_id = bead.bead_id
        runner = FakeRunner(
            results={
                bead_id: AgentRunResult(
                    outcome="completed",
                    summary="done",
                    completed="implemented",
                    remaining="",
                    changed_files=[],
                    touched_files=[],
                ),
            }
        )
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)
        worktrees = WorktreeManager(self.root, self.storage.worktrees_dir)
        scheduler = Scheduler(self.storage, runner, worktrees)
        exit_code = command_run(
            Namespace(feature_root=None, max_workers=1, once=True),
            scheduler,
            console,
        )
        self.assertEqual(0, exit_code)
        data = _parse_run_summary_json(stream.getvalue())
        self.assertIn("started", data)
        self.assertIn("completed", data)
        self.assertIn("blocked", data)
        self.assertIn("correctives_created", data)
        self.assertIn("deferred_count", data)
        self.assertIn("final_state", data)
        self.assertIsInstance(data["started"], list)
        self.assertIsInstance(data["completed"], list)
        self.assertIsInstance(data["blocked"], list)
        self.assertIsInstance(data["correctives_created"], list)
        self.assertIsInstance(data["deferred_count"], int)
        self.assertIsInstance(data["final_state"], dict)

    def test_cli_run_summary_no_ready_beads_warns(self) -> None:
        """command_run emits a warning and still outputs final_state when no beads run."""
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)
        worktrees = WorktreeManager(self.root, self.storage.worktrees_dir)
        scheduler = Scheduler(self.storage, FakeRunner(), worktrees)
        exit_code = command_run(
            Namespace(feature_root=None, max_workers=1, once=True),
            scheduler,
            console,
        )
        self.assertEqual(0, exit_code)
        output = stream.getvalue()
        self.assertIn("No ready beads", output)
        data = _parse_run_summary_json(output)
        self.assertIn("final_state", data)
        self.assertEqual([], data["started"])
        self.assertEqual(0, data["deferred_count"])

    def test_cli_run_summary_final_state_counts_storage(self) -> None:
        """final_state in JSON summary counts beads by status from storage."""
        bead = self.storage.create_bead(title="Work", agent_type="developer", description="do work")
        bead_id = bead.bead_id
        runner = FakeRunner(
            results={
                bead_id: AgentRunResult(
                    outcome="completed",
                    summary="done",
                    completed="implemented",
                    remaining="",
                    changed_files=[],
                    touched_files=[],
                ),
            }
        )
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)
        worktrees = WorktreeManager(self.root, self.storage.worktrees_dir)
        scheduler = Scheduler(self.storage, runner, worktrees)
        command_run(
            Namespace(feature_root=None, max_workers=1, once=True),
            scheduler,
            console,
        )
        data = _parse_run_summary_json(stream.getvalue())
        # Developer bead completing triggers followup child beads (test/docs/review) which are ready.
        # The developer bead itself becomes done.
        final_state = data["final_state"]
        self.assertIn("done", final_state)
        self.assertGreaterEqual(final_state["done"], 1)

    def test_cli_run_summary_bead_ids_are_sorted(self) -> None:
        """started/completed/blocked lists in JSON summary are sorted."""
        bead = self.storage.create_bead(title="Work", agent_type="developer", description="do work")
        bead_id = bead.bead_id
        runner = FakeRunner(
            results={
                bead_id: AgentRunResult(
                    outcome="completed",
                    summary="done",
                    completed="implemented",
                    remaining="",
                    changed_files=[],
                    touched_files=[],
                ),
            }
        )
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)
        worktrees = WorktreeManager(self.root, self.storage.worktrees_dir)
        scheduler = Scheduler(self.storage, runner, worktrees)
        command_run(
            Namespace(feature_root=None, max_workers=1, once=True),
            scheduler,
            console,
        )
        data = _parse_run_summary_json(stream.getvalue())
        self.assertEqual(data["started"], sorted(data["started"]))
        self.assertEqual(data["completed"], sorted(data["completed"]))
        self.assertEqual(data["blocked"], sorted(data["blocked"]))

    def test_cli_run_summary_feature_root_scopes_final_state(self) -> None:
        """final_state is scoped to feature_root when one is given."""
        root_bead = self.storage.create_bead(title="Root", agent_type="developer", description="root work")
        self.storage.create_bead(title="Other", agent_type="developer", description="other work")
        # Run for the root bead only; other_bead should not appear in final_state counts
        runner = FakeRunner(results={})
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)
        worktrees = WorktreeManager(self.root, self.storage.worktrees_dir)
        scheduler = Scheduler(self.storage, runner, worktrees)
        exit_code = command_run(
            Namespace(feature_root=root_bead.bead_id, max_workers=1, once=True),
            scheduler,
            console,
        )
        self.assertEqual(0, exit_code)
        data = _parse_run_summary_json(stream.getvalue())
        # final_state should only count 1 bead (root_bead), not 2
        total = sum(data["final_state"].values())
        self.assertEqual(1, total)


if __name__ == "__main__":
    unittest.main()
