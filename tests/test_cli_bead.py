from __future__ import annotations

import io
import json
import sys
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_takt.cli import (
    LIST_PLAIN_COLUMNS,
    build_parser,
    command_bead,
)
from agent_takt.console import ConsoleReporter
from agent_takt.gitutils import WorktreeManager
from agent_takt.models import (
    AgentRunResult,
    BEAD_IN_PROGRESS,
    Lease,
)
from agent_takt.scheduler import Scheduler
from agent_takt.storage import RepositoryStorage

RepositoryStorage._auto_commit = False

from helpers import FakeRunner, OrchestratorTests as _OrchestratorBase  # noqa: E402


class BeadCliTests(_OrchestratorBase):

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

    def test_cli_claims_defaults_to_json_output(self) -> None:
        bead = self.storage.create_bead(
            title="CLI bead",
            agent_type="developer",
            description="running",
            expected_files=["src/agent_takt/storage.py"],
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
        self.assertEqual(["src/agent_takt/storage.py"], claims[0]["expected_files"])
        self.assertEqual("developer:cli", claims[0]["lease"]["owner"])
        self.assertNotIn(" | ", rendered)

    def test_cli_claims_plain_outputs_compact_lines(self) -> None:
        bead = self.storage.create_bead(
            title="CLI bead plain",
            agent_type="developer",
            description="running",
            expected_files=["src/agent_takt/storage.py"],
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

    def test_cli_bead_graph_outputs_full_graph(self) -> None:
        upstream = self.storage.create_bead(
            title="Upstream",
            agent_type="planner",
            description="dependency",
            bead_id="B-graph-cli-upstream",
        )
        downstream = self.storage.create_bead(
            title="Downstream",
            agent_type="developer",
            description="dependent bead",
            dependencies=[upstream.bead_id],
            bead_id="B-graph-cli-downstream",
        )
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)

        exit_code = command_bead(
            Namespace(bead_command="graph", feature_root=None, output=None),
            self.storage,
            console,
        )

        self.assertEqual(0, exit_code)
        output = stream.getvalue()
        self.assertTrue(output.startswith("graph TD\n"))
        self.assertIn("B_graph_cli_upstream", output)
        self.assertIn("B_graph_cli_downstream", output)
        self.assertIn("B_graph_cli_upstream --> B_graph_cli_downstream", output)

    def test_cli_bead_graph_feature_root_filter_resolves_prefix_and_includes_epic_parent(self) -> None:
        epic = self.storage.create_bead(
            title="Epic root",
            agent_type="planner",
            description="epic",
            bead_type="epic",
            bead_id="B-graph-epic",
        )
        feature = self.storage.create_bead(
            title="Feature root",
            agent_type="developer",
            description="feature",
            parent_id=epic.bead_id,
            bead_id="B-graph-feature",
        )
        child = self.storage.create_bead(
            title="Feature child",
            agent_type="tester",
            description="inside feature",
            parent_id=feature.bead_id,
            dependencies=[feature.bead_id],
            bead_id="B-graph-feature-test",
        )
        other = self.storage.create_bead(
            title="Other root",
            agent_type="developer",
            description="outside feature",
            bead_id="B-graph-other",
        )
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)

        prefix = "B-graph-f"
        exit_code = command_bead(
            Namespace(bead_command="graph", feature_root=prefix, output=None),
            self.storage,
            console,
        )

        self.assertEqual(0, exit_code)
        output = stream.getvalue()
        self.assertIn("B_graph_epic", output)
        self.assertIn("B_graph_feature", output)
        self.assertIn("B_graph_feature_test", output)
        self.assertIn("B_graph_feature --> B_graph_feature_test", output)
        self.assertNotIn("B_graph_other", output)
        self.assertNotIn(other.bead_id, output)

    def test_cli_bead_graph_output_writes_fenced_mermaid_and_overwrites_existing_file(self) -> None:
        bead = self.storage.create_bead(
            title="Graph output bead",
            agent_type="developer",
            description="graph export",
            bead_id="B-graph-output",
        )
        output_path = self.root / "graph.md"
        output_path.write_text("stale\ncontent\n", encoding="utf-8")
        console = ConsoleReporter(stream=io.StringIO())
        stderr = io.StringIO()

        with patch("sys.stderr", stderr):
            exit_code = command_bead(
                Namespace(bead_command="graph", feature_root=None, output=str(output_path)),
                self.storage,
                console,
            )

        self.assertEqual(0, exit_code)
        self.assertEqual(
            f"```mermaid\ngraph TD\n    B_graph_output[\"{bead.bead_id}: Graph output bead [developer] ○\"]\n```\n",
            output_path.read_text(encoding="utf-8"),
        )
        self.assertIn(f"Wrote Mermaid graph to {output_path}", stderr.getvalue())

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
        epic = self.storage.create_bead(
            title="Epic Root",
            agent_type="planner",
            description="feature root placeholder",
            bead_type="epic",
        )
        child = self.storage.create_bead(
            title="Child Task",
            agent_type="developer",
            description="child task",
            parent_id=epic.bead_id,
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
        self.assertIn(epic.bead_id, lines[1])
        self.assertIn(child.bead_id, lines[2])
        self.assertIn(" - ", lines[1])  # feature_root_id and parent_id render as "-"
        self.assertNotIn('"bead_id"', output)
        self.assertFalse(output.lstrip().startswith("["))

    def test_cli_bead_list_plain_rows_are_sorted_by_creation_timestamp(self) -> None:
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

    def test_cli_bead_show_resolves_prefix(self) -> None:
        bead = self.storage.create_bead(title="Show me", agent_type="developer", description="work")
        prefix = bead.bead_id[:4]
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)
        exit_code = command_bead(Namespace(bead_command="show", bead_id=prefix), self.storage, console)
        self.assertEqual(0, exit_code)
        data = json.loads(stream.getvalue())
        self.assertEqual(bead.bead_id, data["bead_id"])

    def test_cli_bead_update_resolves_prefix(self) -> None:
        bead = self.storage.create_bead(title="Update me", agent_type="developer", description="old")
        prefix = bead.bead_id[:4]
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)
        exit_code = command_bead(
            Namespace(
                bead_command="update",
                bead_id=prefix,
                status=None,
                description="new",
                block_reason=None,
                expected_file=None,
                expected_glob=None,
                touched_file=None,
                conflict_risks=None,
                model=None,
            ),
            self.storage,
            console,
        )
        self.assertEqual(0, exit_code)
        updated = self.storage.load_bead(bead.bead_id)
        self.assertEqual("new", updated.description)

    def test_cli_bead_show_raises_on_ambiguous_prefix(self) -> None:
        self.storage.create_bead(title="A", agent_type="developer", description="a")
        self.storage.create_bead(title="B", agent_type="developer", description="b")
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)
        with self.assertRaises(ValueError) as ctx:
            command_bead(Namespace(bead_command="show", bead_id="B-"), self.storage, console)
        self.assertIn("Ambiguous prefix", str(ctx.exception))

    def test_cli_bead_show_raises_on_no_match(self) -> None:
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)
        with self.assertRaises(ValueError) as ctx:
            command_bead(Namespace(bead_command="show", bead_id="B-nonexist"), self.storage, console)
        self.assertIn("No bead found", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
