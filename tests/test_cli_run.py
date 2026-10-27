from __future__ import annotations

import io
import json
import sys
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_takt.cli import command_run
from agent_takt.cli.commands.run import CliSchedulerReporter
from agent_takt.cli.parser import build_parser
from agent_takt.console import ConsoleReporter
from agent_takt.gitutils import WorktreeManager
from agent_takt.models import AgentRunResult
from agent_takt.scheduler import Scheduler, SchedulerResult
from agent_takt.storage import RepositoryStorage
from agent_takt.tui.state import TuiRuntimeState

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

    def test_bead_deferred_silent_when_verbose_false(self) -> None:
        """bead_deferred must not emit any output when verbose=False (the default)."""
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)
        reporter = CliSchedulerReporter(console, max_workers=1, verbose=False)
        bead = self._make_bead()
        reporter.bead_deferred(bead, "conflict detected")
        self.assertEqual("", stream.getvalue())

    def test_bead_deferred_emits_when_verbose_true(self) -> None:
        """bead_deferred must emit a detail line containing bead_id, title, and reason when verbose=True."""
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)
        reporter = CliSchedulerReporter(console, max_workers=1, verbose=True)
        bead = self._make_bead()
        reporter.bead_deferred(bead, "conflict detected")
        output = stream.getvalue()
        self.assertIn("B-test1234", output)
        self.assertIn("Test bead", output)
        self.assertIn("conflict detected", output)

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
            Namespace(feature_root=prefix, max_workers=1),
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
            Namespace(feature_root="B-nonexist", max_workers=1),
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
            Namespace(feature_root=None, max_workers=1),
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
            Namespace(feature_root=None, max_workers=1),
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
            Namespace(feature_root=None, max_workers=1),
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
            Namespace(feature_root=None, max_workers=1),
            scheduler,
            console,
        )
        data = _parse_run_summary_json(stream.getvalue())
        self.assertEqual(data["started"], sorted(data["started"]))
        self.assertEqual(data["completed"], sorted(data["completed"]))
        self.assertEqual(data["blocked"], sorted(data["blocked"]))

    def test_cli_run_summary_feature_root_scopes_final_state(self) -> None:
        """final_state is scoped to feature_root when one is given."""
        # Use planner agent type so the bead completes without triggering followup creation.
        root_bead = self.storage.create_bead(title="Root", agent_type="planner", description="root work")
        other_bead = self.storage.create_bead(title="Other", agent_type="planner", description="other work")
        runner = FakeRunner(results={root_bead.bead_id: AgentRunResult(outcome="completed", summary="done")})
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)
        worktrees = WorktreeManager(self.root, self.storage.worktrees_dir)
        scheduler = Scheduler(self.storage, runner, worktrees)
        exit_code = command_run(
            Namespace(feature_root=root_bead.bead_id, max_workers=1),
            scheduler,
            console,
        )
        self.assertEqual(0, exit_code)
        data = _parse_run_summary_json(stream.getvalue())
        # final_state should count only beads in root_bead's feature tree, not other_bead
        total = sum(data["final_state"].values())
        self.assertEqual(1, total)
        # Confirm other_bead was not touched (it is in a different feature tree)
        other_after = self.storage.load_bead(other_bead.bead_id)
        self.assertEqual("ready", other_after.status)


class RunParserVerboseTests(unittest.TestCase):
    """Verify the --verbose flag is wired up in the run sub-command parser."""

    def test_verbose_flag_exists_in_run_parser(self) -> None:
        """takt run --verbose must be accepted by the parser."""
        parser = build_parser()
        args = parser.parse_args(["run", "--verbose"])
        self.assertTrue(args.verbose)

    def test_verbose_flag_defaults_to_false(self) -> None:
        """takt run without --verbose should default verbose to False."""
        parser = build_parser()
        args = parser.parse_args(["run"])
        self.assertFalse(args.verbose)

    def test_once_flag_rejected(self) -> None:
        """takt run --once must be rejected as an unrecognised argument (flag was removed)."""
        parser = build_parser()
        with self.assertRaises(SystemExit) as ctx:
            parser.parse_args(["run", "--once"])
        self.assertEqual(2, ctx.exception.code)


class QuiescenceLoopTests(_OrchestratorBase):
    """Tests that verify command_run's quiescence loop by mocking Scheduler.run_once directly."""

    def _make_scheduler(self) -> Scheduler:
        worktrees = WorktreeManager(self.root, self.storage.worktrees_dir)
        return Scheduler(self.storage, FakeRunner(), worktrees)

    def test_run_loops_until_quiescence(self) -> None:
        """run_once is called 3 times: two cycles start beads, the third starts none and exits."""
        scheduler = self._make_scheduler()
        side_effects = [
            SchedulerResult(started=["B-1"]),
            SchedulerResult(started=["B-2"]),
            SchedulerResult(started=[]),
        ]
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)
        with patch.object(scheduler, "run_once", side_effect=side_effects) as mock_run:
            exit_code = command_run(
                Namespace(feature_root=None, max_workers=1, max_cycles=0),
                scheduler,
                console,
            )
        self.assertEqual(0, exit_code)
        self.assertEqual(3, mock_run.call_count)
        data = _parse_run_summary_json(stream.getvalue())
        self.assertIn("B-1", data["started"])
        self.assertIn("B-2", data["started"])

    def test_run_stops_when_no_beads_started(self) -> None:
        """run_once returns started=[] immediately; loop exits after one call."""
        scheduler = self._make_scheduler()
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)
        with patch.object(
            scheduler, "run_once", return_value=SchedulerResult(started=[])
        ) as mock_run:
            exit_code = command_run(
                Namespace(feature_root=None, max_workers=1, max_cycles=0),
                scheduler,
                console,
            )
        self.assertEqual(0, exit_code)
        self.assertEqual(1, mock_run.call_count)
        self.assertIn("No ready beads", stream.getvalue())

    def test_run_respects_max_cycles_cap(self) -> None:
        """run_once always starts beads; loop exits after max_cycles=5 with a warning."""
        scheduler = self._make_scheduler()
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)
        with patch.object(
            scheduler, "run_once", return_value=SchedulerResult(started=["B-N"])
        ) as mock_run:
            exit_code = command_run(
                Namespace(feature_root=None, max_workers=1, max_cycles=5),
                scheduler,
                console,
            )
        self.assertEqual(0, exit_code)
        self.assertEqual(5, mock_run.call_count)
        self.assertIn("max_cycles", stream.getvalue())

    def test_run_quiescence_with_deferred_only_exits(self) -> None:
        """Deferred beads do not count as progress; loop exits after one call."""
        scheduler = self._make_scheduler()
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)
        with patch.object(
            scheduler, "run_once", return_value=SchedulerResult(started=[], deferred=["B-X"])
        ) as mock_run:
            exit_code = command_run(
                Namespace(feature_root=None, max_workers=1, max_cycles=0),
                scheduler,
                console,
            )
        self.assertEqual(0, exit_code)
        self.assertEqual(1, mock_run.call_count)


class CliSchedulerReporterGovernanceTests(unittest.TestCase):
    """Detail lines for structural assumptions and criteria gaps on completion."""

    def _complete(self, handoff) -> str:
        stream = io.StringIO()
        reporter = CliSchedulerReporter(ConsoleReporter(stream=stream), max_workers=1)
        bead = MagicMock()
        bead.bead_id = "B-gov12345"
        bead.agent_type = "developer"
        bead.title = "Gov bead"
        bead.handoff_summary = handoff
        reporter.bead_started(bead)
        reporter.bead_completed(bead, "all done", [])
        return stream.getvalue()

    def test_structural_assumptions_line_printed(self) -> None:
        from agent_takt.models import HandoffSummary
        handoff = HandoffSummary(assumptions=[
            {"statement": "a", "rationale": "r", "scope": "structural"},
            {"statement": "b", "rationale": "r", "scope": "structural"},
            {"statement": "c", "rationale": "r", "scope": "local"},
        ])
        output = self._complete(handoff)
        self.assertIn("2 structural assumption(s) recorded", output)

    def test_local_only_assumptions_print_nothing(self) -> None:
        from agent_takt.models import HandoffSummary
        handoff = HandoffSummary(assumptions=[{"statement": "c", "rationale": "r", "scope": "local"}])
        self.assertNotIn("structural assumption", self._complete(handoff))

    def test_criteria_line_printed_for_unmet_or_descoped(self) -> None:
        from agent_takt.models import HandoffSummary
        handoff = HandoffSummary(criteria_status=[
            {"index": 0, "criterion": "a", "status": "unmet", "evidence": ""},
            {"index": 1, "criterion": "b", "status": "descoped", "evidence": "x"},
            {"index": 2, "criterion": "c", "status": "descoped", "evidence": "x"},
            {"index": 3, "criterion": "d", "status": "met", "evidence": "y"},
        ])
        self.assertIn("criteria: 1 unmet, 2 descoped", self._complete(handoff))

    def test_met_only_criteria_print_nothing(self) -> None:
        from agent_takt.models import HandoffSummary
        handoff = HandoffSummary(criteria_status=[{"index": 0, "criterion": "a", "status": "met", "evidence": "y"}])
        self.assertNotIn("criteria:", self._complete(handoff))

    def test_empty_handoff_prints_neither_line(self) -> None:
        from agent_takt.models import HandoffSummary
        output = self._complete(HandoffSummary())
        self.assertNotIn("structural assumption", output)
        self.assertNotIn("criteria:", output)
        self.assertIn("all done", output)

    def test_bead_blocked_output_unchanged(self) -> None:
        stream = io.StringIO()
        reporter = CliSchedulerReporter(ConsoleReporter(stream=stream), max_workers=1)
        bead = MagicMock()
        bead.bead_id = "B-gov12345"
        bead.agent_type = "developer"
        bead.title = "Gov bead"
        reporter.bead_started(bead)
        reporter.bead_blocked(bead, "dependency not ready")
        output = stream.getvalue()
        self.assertIn("dependency not ready", output)
        self.assertNotIn("structural assumption", output)
        self.assertNotIn("criteria:", output)


if __name__ == "__main__":
    unittest.main()
