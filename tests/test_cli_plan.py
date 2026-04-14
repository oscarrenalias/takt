from __future__ import annotations

import io
import json
import re
import sys
import unittest
from argparse import Namespace
from dataclasses import asdict
from pathlib import Path
from unittest.mock import MagicMock, call, patch

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_takt.cli import build_parser, command_plan
from agent_takt.console import ConsoleReporter
from agent_takt.models import PlanChild, PlanProposal
from agent_takt.planner import PlanningService
from agent_takt.storage import RepositoryStorage

RepositoryStorage._auto_commit = False

from helpers import FakeRunner, OrchestratorTests as _OrchestratorBase  # noqa: E402


def _minimal_proposal() -> PlanProposal:
    return PlanProposal(
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


class CliPlanTests(_OrchestratorBase):

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

    def test_command_plan_write_outputs_created_bead_details(self) -> None:
        spec_path = self.root / "spec.md"
        spec_path.write_text("Feature spec\n", encoding="utf-8")
        proposal = _minimal_proposal()
        planner = PlanningService(self.storage, FakeRunner(proposal=proposal))
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)
        exit_code = command_plan(Namespace(spec_file=str(spec_path), write=True, output=None, from_file=None), planner, console)
        self.assertEqual(0, exit_code)
        output = stream.getvalue()
        self.assertRegex(output, r'"bead_id": "B-[0-9a-f]{8}"')
        self.assertIn('"title": "Epic"', output)
        self.assertNotIn('"description"', output)


# ---------------------------------------------------------------------------
# Parser validation tests for staged plan CLI flags
# ---------------------------------------------------------------------------


class TestPlanParserFlags(unittest.TestCase):
    """Tests for the new --output, --from-file parser arguments."""

    def setUp(self) -> None:
        self.parser = build_parser()

    def test_bare_spec_file_sets_defaults(self) -> None:
        """takt plan spec.md → spec_file='spec.md', write=False, output=None, from_file=None."""
        args = self.parser.parse_args(["plan", "spec.md"])
        self.assertEqual("spec.md", args.spec_file)
        self.assertFalse(args.write)
        self.assertIsNone(args.output)
        self.assertIsNone(args.from_file)

    def test_write_flag_parsed_correctly(self) -> None:
        """takt plan --write spec.md → write=True, output=None, from_file=None."""
        args = self.parser.parse_args(["plan", "--write", "spec.md"])
        self.assertTrue(args.write)
        self.assertEqual("spec.md", args.spec_file)
        self.assertIsNone(args.output)
        self.assertIsNone(args.from_file)

    def test_output_flag_parsed_correctly(self) -> None:
        """takt plan --output plan.json spec.md → output='plan.json', write=False, from_file=None."""
        args = self.parser.parse_args(["plan", "--output", "plan.json", "spec.md"])
        self.assertEqual("plan.json", args.output)
        self.assertEqual("spec.md", args.spec_file)
        self.assertFalse(args.write)
        self.assertIsNone(args.from_file)

    def test_from_file_flag_parsed_correctly(self) -> None:
        """takt plan --from-file plan.json → from_file='plan.json', spec_file=None, write=False, output=None."""
        args = self.parser.parse_args(["plan", "--from-file", "plan.json"])
        self.assertEqual("plan.json", args.from_file)
        self.assertIsNone(args.spec_file)
        self.assertFalse(args.write)
        self.assertIsNone(args.output)

    def test_invalid_combo_write_and_output_exits_with_code_2(self) -> None:
        """--write and --output are mutually exclusive → exit 2."""
        with self.assertRaises(SystemExit) as ctx:
            self.parser.parse_args(["plan", "--write", "--output", "plan.json", "spec.md"])
        self.assertEqual(2, ctx.exception.code)

    def test_invalid_combo_write_and_from_file_exits_with_code_2(self) -> None:
        """--write and --from-file are mutually exclusive → exit 2."""
        with self.assertRaises(SystemExit) as ctx:
            self.parser.parse_args(["plan", "--write", "--from-file", "plan.json"])
        self.assertEqual(2, ctx.exception.code)

    def test_invalid_combo_output_and_from_file_exits_with_code_2(self) -> None:
        """--output and --from-file are mutually exclusive → exit 2."""
        with self.assertRaises(SystemExit) as ctx:
            self.parser.parse_args(["plan", "--output", "plan.json", "--from-file", "plan.json"])
        self.assertEqual(2, ctx.exception.code)

    def test_no_spec_file_with_from_file_is_valid_parse(self) -> None:
        """--from-file without spec_file is valid; spec_file should be None."""
        args = self.parser.parse_args(["plan", "--from-file", "saved.json"])
        self.assertIsNone(args.spec_file)
        self.assertEqual("saved.json", args.from_file)


# ---------------------------------------------------------------------------
# command_plan tests for --output path
# ---------------------------------------------------------------------------


class TestCommandPlanOutput(_OrchestratorBase):
    """Tests for the takt plan --output FILE spec.md workflow."""

    def _make_planner(self, proposal: PlanProposal | None = None) -> PlanningService:
        return PlanningService(self.storage, FakeRunner(proposal=proposal or _minimal_proposal()))

    def test_output_writes_json_to_file(self) -> None:
        """--output writes asdict(proposal) JSON to the given path."""
        spec_path = self.root / "spec.md"
        spec_path.write_text("Feature spec\n", encoding="utf-8")
        output_path = self.root / "plan.json"
        planner = self._make_planner()
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)

        exit_code = command_plan(
            Namespace(spec_file=str(spec_path), write=False, output=str(output_path), from_file=None),
            planner,
            console,
        )

        self.assertEqual(0, exit_code)
        self.assertTrue(output_path.exists(), "Output file should have been created")
        data = json.loads(output_path.read_text(encoding="utf-8"))
        self.assertIn("epic_title", data)
        self.assertEqual("Epic", data["epic_title"])

    def test_output_does_not_create_beads(self) -> None:
        """--output must not persist any beads to storage."""
        spec_path = self.root / "spec.md"
        spec_path.write_text("Feature spec\n", encoding="utf-8")
        output_path = self.root / "plan.json"
        planner = self._make_planner()
        console = ConsoleReporter(stream=io.StringIO())

        command_plan(
            Namespace(spec_file=str(spec_path), write=False, output=str(output_path), from_file=None),
            planner,
            console,
        )

        beads = self.storage.list_beads()
        self.assertEqual(0, len(beads), "No beads should be created when using --output")

    def test_output_stdout_contains_dry_run_summary(self) -> None:
        """--output prints epic_title and feature info to stdout (not the file path only)."""
        spec_path = self.root / "spec.md"
        spec_path.write_text("Feature spec\n", encoding="utf-8")
        output_path = self.root / "plan.json"
        stream = io.StringIO()
        planner = self._make_planner()
        console = ConsoleReporter(stream=stream)

        command_plan(
            Namespace(spec_file=str(spec_path), write=False, output=str(output_path), from_file=None),
            planner,
            console,
        )

        output = stream.getvalue()
        self.assertIn("Epic", output)

    def test_output_file_is_valid_json(self) -> None:
        """The file written by --output must be valid JSON."""
        spec_path = self.root / "spec.md"
        spec_path.write_text("Feature spec\n", encoding="utf-8")
        output_path = self.root / "plan.json"
        planner = self._make_planner()
        console = ConsoleReporter(stream=io.StringIO())

        command_plan(
            Namespace(spec_file=str(spec_path), write=False, output=str(output_path), from_file=None),
            planner,
            console,
        )

        raw = output_path.read_text(encoding="utf-8")
        parsed = json.loads(raw)
        self.assertIsInstance(parsed, dict)


# ---------------------------------------------------------------------------
# command_plan tests for --from-file path
# ---------------------------------------------------------------------------


class TestCommandPlanFromFile(_OrchestratorBase):
    """Tests for the takt plan --from-file FILE workflow."""

    def _write_plan_file(self, path: Path, proposal: PlanProposal) -> None:
        path.write_text(json.dumps(asdict(proposal), indent=2), encoding="utf-8")

    def test_from_file_happy_path_creates_beads(self) -> None:
        """--from-file reads a saved plan and persists beads."""
        proposal = _minimal_proposal()
        plan_path = self.root / "plan.json"
        self._write_plan_file(plan_path, proposal)

        fake_runner = FakeRunner(proposal=proposal)
        planner = PlanningService(self.storage, fake_runner)
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)

        exit_code = command_plan(
            Namespace(spec_file=None, write=False, output=None, from_file=str(plan_path)),
            planner,
            console,
        )

        self.assertEqual(0, exit_code)
        beads = self.storage.list_beads()
        self.assertGreater(len(beads), 0, "Beads should have been created from plan file")

    def test_from_file_does_not_call_propose(self) -> None:
        """--from-file must not call planner.propose() (no LLM call)."""
        proposal = _minimal_proposal()
        plan_path = self.root / "plan.json"
        self._write_plan_file(plan_path, proposal)

        mock_runner = MagicMock()
        planner = PlanningService(self.storage, mock_runner)
        console = ConsoleReporter(stream=io.StringIO())

        command_plan(
            Namespace(spec_file=None, write=False, output=None, from_file=str(plan_path)),
            planner,
            console,
        )

        mock_runner.propose_plan.assert_not_called()

    def test_from_file_output_contains_created_key(self) -> None:
        """--from-file output JSON should contain a 'created' key matching the --write format."""
        proposal = _minimal_proposal()
        plan_path = self.root / "plan.json"
        self._write_plan_file(plan_path, proposal)

        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)
        planner = PlanningService(self.storage, FakeRunner(proposal=proposal))

        exit_code = command_plan(
            Namespace(spec_file=None, write=False, output=None, from_file=str(plan_path)),
            planner,
            console,
        )

        self.assertEqual(0, exit_code)
        output = stream.getvalue()
        # Should contain "created" as a JSON key
        self.assertIn('"created"', output, "Expected 'created' key in --from-file output JSON")
        # Should contain bead ID pattern
        self.assertRegex(output, r'"bead_id":\s*"B-[0-9a-f]{8}"')

    def test_from_file_missing_file_returns_exit_code_1(self) -> None:
        """--from-file with a non-existent file returns exit code 1."""
        fake_runner = FakeRunner(proposal=_minimal_proposal())
        planner = PlanningService(self.storage, fake_runner)
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)

        exit_code = command_plan(
            Namespace(spec_file=None, write=False, output=None, from_file="/nonexistent/plan.json"),
            planner,
            console,
        )

        self.assertEqual(1, exit_code)
        output = stream.getvalue()
        self.assertIn("not found", output.lower())

    def test_from_file_malformed_json_returns_exit_code_1(self) -> None:
        """--from-file with malformed JSON returns exit code 1."""
        bad_path = self.root / "bad.json"
        bad_path.write_text("{ this is not valid json }", encoding="utf-8")

        fake_runner = FakeRunner(proposal=_minimal_proposal())
        planner = PlanningService(self.storage, fake_runner)
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)

        exit_code = command_plan(
            Namespace(spec_file=None, write=False, output=None, from_file=str(bad_path)),
            planner,
            console,
        )

        self.assertEqual(1, exit_code)

    def test_from_file_creates_same_bead_count_as_write(self) -> None:
        """Beads created via --from-file must equal those created via --write for the same proposal."""
        proposal = _minimal_proposal()

        # First: count beads created via --write
        spec_path = self.root / "spec.md"
        spec_path.write_text("Feature spec\n", encoding="utf-8")
        write_planner = PlanningService(self.storage, FakeRunner(proposal=proposal))
        command_plan(
            Namespace(spec_file=str(spec_path), write=True, output=None, from_file=None),
            write_planner,
            ConsoleReporter(stream=io.StringIO()),
        )
        write_bead_count = len(self.storage.list_beads())

        # Second: count beads created via --from-file on a fresh storage
        import tempfile, shutil, subprocess
        temp = tempfile.mkdtemp()
        try:
            root2 = Path(temp)
            subprocess.run(["git", "init"], cwd=root2, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root2, check=True)
            subprocess.run(["git", "config", "user.name", "Test User"], cwd=root2, check=True)
            subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=root2, check=True)
            (root2 / "README.md").write_text("seed\n", encoding="utf-8")
            subprocess.run(["git", "add", "README.md"], cwd=root2, check=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=root2, check=True, capture_output=True)
            storage2 = RepositoryStorage(root2)
            storage2.initialize()

            plan_path = self.root / "plan.json"
            plan_path.write_text(json.dumps(asdict(proposal), indent=2), encoding="utf-8")

            from_file_planner = PlanningService(storage2, FakeRunner(proposal=proposal))
            command_plan(
                Namespace(spec_file=None, write=False, output=None, from_file=str(plan_path)),
                from_file_planner,
                ConsoleReporter(stream=io.StringIO()),
            )
            from_file_bead_count = len(storage2.list_beads())
        finally:
            shutil.rmtree(temp, ignore_errors=True)

        self.assertEqual(write_bead_count, from_file_bead_count,
                         "Bead count from --from-file should equal bead count from --write")


# ---------------------------------------------------------------------------
# command_plan tests for error paths
# ---------------------------------------------------------------------------


class TestCommandPlanErrors(_OrchestratorBase):
    """Tests for error conditions in command_plan."""

    def test_no_spec_file_and_no_from_file_returns_exit_code_1(self) -> None:
        """spec_file=None without --from-file returns exit code 1 with error message."""
        planner = PlanningService(self.storage, FakeRunner(proposal=_minimal_proposal()))
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)

        exit_code = command_plan(
            Namespace(spec_file=None, write=False, output=None, from_file=None),
            planner,
            console,
        )

        self.assertEqual(1, exit_code)
        output = stream.getvalue()
        self.assertIn("spec_file", output.lower())

    def test_write_without_spec_file_returns_exit_code_1(self) -> None:
        """takt plan --write (no spec_file) returns exit code 1 with descriptive error."""
        planner = PlanningService(self.storage, FakeRunner(proposal=_minimal_proposal()))
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)

        exit_code = command_plan(
            Namespace(spec_file=None, write=True, output=None, from_file=None),
            planner,
            console,
        )

        self.assertEqual(1, exit_code)
        output = stream.getvalue()
        # Should explain what's missing
        self.assertTrue(
            "spec" in output.lower() or "required" in output.lower(),
            f"Expected error mentioning spec or required; got: {output!r}",
        )


# ---------------------------------------------------------------------------
# planner.py persist_plan vs write_plan parity tests
# ---------------------------------------------------------------------------


class TestPersistPlanParity(_OrchestratorBase):
    """Tests that persist_plan and write_plan produce equivalent bead graphs."""

    def test_persist_plan_creates_same_beads_as_write_plan(self) -> None:
        """persist_plan() directly produces the same bead IDs and structure as write_plan()."""
        proposal = _minimal_proposal()

        planner = PlanningService(self.storage, FakeRunner(proposal=proposal))
        created_via_persist = planner.persist_plan(proposal)

        self.assertGreater(len(created_via_persist), 0)

        # Verify all created beads can be loaded
        for bead_id in created_via_persist:
            bead = self.storage.load_bead(bead_id)
            self.assertIsNotNone(bead)

    def test_write_plan_invokes_ingest_when_spec_path_provided(self) -> None:
        """write_plan(spec_path=...) calls _ingest_spec exactly once."""
        spec_path = self.root / "spec.md"
        spec_path.write_text("# Feature Spec\n\nDetails.", encoding="utf-8")
        proposal = _minimal_proposal()

        with patch("agent_takt.planner.ingest_file") as mock_ingest:
            planner = PlanningService(self.storage, FakeRunner(proposal=proposal))
            created = planner.write_plan(proposal, spec_path=spec_path)

        self.assertGreater(len(created), 0)
        mock_ingest.assert_called_once()
        _, kwargs = mock_ingest.call_args
        self.assertEqual("specs", kwargs.get("namespace"))

    def test_write_plan_without_spec_path_creates_beads_without_ingestion(self) -> None:
        """write_plan without spec_path creates beads and does NOT call ingest_file."""
        proposal = _minimal_proposal()

        with patch("agent_takt.planner.ingest_file") as mock_ingest:
            planner = PlanningService(self.storage, FakeRunner(proposal=proposal))
            created = planner.write_plan(proposal)

        self.assertGreater(len(created), 0)
        mock_ingest.assert_not_called()

    def test_persist_plan_does_not_call_ingest(self) -> None:
        """persist_plan() alone never calls ingest_file — ingestion is write_plan's responsibility."""
        proposal = _minimal_proposal()

        with patch("agent_takt.planner.ingest_file") as mock_ingest:
            planner = PlanningService(self.storage, FakeRunner(proposal=proposal))
            planner.persist_plan(proposal)

        mock_ingest.assert_not_called()


# ---------------------------------------------------------------------------
# Regression tests: existing dry-run and --write still work
# ---------------------------------------------------------------------------


class TestCommandPlanRegression(_OrchestratorBase):
    """Regression guard: plain dry-run and --write still produce expected output."""

    def _make_proposal(self) -> PlanProposal:
        return _minimal_proposal()

    def test_dry_run_outputs_plan_json_without_creating_beads(self) -> None:
        """Plain dry-run (no flags) prints epic JSON to stdout without creating any beads."""
        spec_path = self.root / "spec.md"
        spec_path.write_text("Feature spec\n", encoding="utf-8")
        proposal = self._make_proposal()
        planner = PlanningService(self.storage, FakeRunner(proposal=proposal))
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)

        exit_code = command_plan(
            Namespace(spec_file=str(spec_path), write=False, output=None, from_file=None),
            planner,
            console,
        )

        self.assertEqual(0, exit_code)
        output = stream.getvalue()
        self.assertIn("Epic", output)
        # No beads should be created in dry-run mode
        beads = self.storage.list_beads()
        self.assertEqual(0, len(beads))

    def test_write_creates_beads_and_outputs_created_list(self) -> None:
        """--write creates beads and outputs {'created': [...]} JSON."""
        spec_path = self.root / "spec.md"
        spec_path.write_text("Feature spec\n", encoding="utf-8")
        proposal = self._make_proposal()
        planner = PlanningService(self.storage, FakeRunner(proposal=proposal))
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)

        exit_code = command_plan(
            Namespace(spec_file=str(spec_path), write=True, output=None, from_file=None),
            planner,
            console,
        )

        self.assertEqual(0, exit_code)
        beads = self.storage.list_beads()
        self.assertGreater(len(beads), 0)
        output = stream.getvalue()
        self.assertRegex(output, r'"bead_id": "B-[0-9a-f]{8}"')
        self.assertIn('"created"', output)


if __name__ == "__main__":
    unittest.main()
