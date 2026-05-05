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
    BEAD_BLOCKED,
    BEAD_IN_PROGRESS,
    BEAD_READY,
    ExecutionRecord,
    HandoffSummary,
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

    # ------------------------------------------------------------------
    # Priority: bead create --priority
    # ------------------------------------------------------------------

    def _create_ns(self, **overrides):
        """Build a Namespace for bead create with minimal required fields."""
        defaults = dict(
            bead_command="create",
            title="Test bead",
            agent="developer",
            description="desc",
            parent_id=None,
            dependency=[],
            criterion=[],
            linked_doc=[],
            expected_file=[],
            expected_glob=[],
            touched_file=[],
            conflict_risks="",
            label=[],
            priority=None,
        )
        defaults.update(overrides)
        return Namespace(**defaults)

    def test_cli_bead_create_with_priority_high(self) -> None:
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)
        exit_code = command_bead(self._create_ns(priority="high"), self.storage, console)
        self.assertEqual(0, exit_code)
        output = stream.getvalue()
        bead_id = output.strip().split()[-1]
        bead = self.storage.load_bead(bead_id)
        self.assertEqual("high", bead.priority)

    def test_cli_bead_create_without_priority_default_none(self) -> None:
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)
        exit_code = command_bead(self._create_ns(priority=None), self.storage, console)
        self.assertEqual(0, exit_code)
        output = stream.getvalue()
        bead_id = output.strip().split()[-1]
        bead = self.storage.load_bead(bead_id)
        self.assertIsNone(bead.priority)

    # ------------------------------------------------------------------
    # Priority: bead set-priority
    # ------------------------------------------------------------------

    def test_cli_set_priority_high_updates_bead(self) -> None:
        bead = self.storage.create_bead(title="Target bead", agent_type="developer", description="work")
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)
        exit_code = command_bead(
            Namespace(bead_command="set-priority", bead_id=bead.bead_id, priority="high"),
            self.storage,
            console,
        )
        self.assertEqual(0, exit_code)
        updated = self.storage.load_bead(bead.bead_id)
        self.assertEqual("high", updated.priority)

    def test_cli_set_priority_normal_clears_bead_priority(self) -> None:
        bead = self.storage.create_bead(
            title="High bead", agent_type="developer", description="work", priority="high"
        )
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)
        exit_code = command_bead(
            Namespace(bead_command="set-priority", bead_id=bead.bead_id, priority="normal"),
            self.storage,
            console,
        )
        self.assertEqual(0, exit_code)
        updated = self.storage.load_bead(bead.bead_id)
        self.assertIsNone(updated.priority)

    # ------------------------------------------------------------------
    # Priority: parser validation
    # ------------------------------------------------------------------

    def test_build_parser_create_accepts_priority_high(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            ["bead", "create", "--title", "T", "--agent", "developer", "--description", "d", "--priority", "high"]
        )
        self.assertEqual("high", args.priority)

    def test_build_parser_create_default_priority_is_none(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["bead", "create", "--title", "T", "--agent", "developer", "--description", "d"])
        self.assertIsNone(args.priority)

    def test_build_parser_create_invalid_priority_exits(self) -> None:
        parser = build_parser()
        with self.assertRaises(SystemExit) as ctx:
            parser.parse_args(
                ["bead", "create", "--title", "T", "--agent", "developer", "--description", "d", "--priority", "urgent"]
            )
        self.assertEqual(2, ctx.exception.code)

    def test_build_parser_set_priority_invalid_choice_exits(self) -> None:
        parser = build_parser()
        with self.assertRaises(SystemExit) as ctx:
            parser.parse_args(["bead", "set-priority", "B-00000001", "urgent"])
        self.assertEqual(2, ctx.exception.code)

    def test_build_parser_set_priority_accepts_high_and_normal(self) -> None:
        parser = build_parser()
        args_high = parser.parse_args(["bead", "set-priority", "B-00000001", "high"])
        args_normal = parser.parse_args(["bead", "set-priority", "B-00000001", "normal"])
        self.assertEqual("high", args_high.priority)
        self.assertEqual("normal", args_normal.priority)

    # ------------------------------------------------------------------
    # Priority: bead list --plain PRIORITY column
    # ------------------------------------------------------------------

    def test_cli_bead_list_plain_normal_priority_bead_priority_column_blank(self) -> None:
        self.storage.create_bead(title="Normal bead", agent_type="developer", description="normal")
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)
        exit_code = command_bead(Namespace(bead_command="list", plain=True), self.storage, console)
        self.assertEqual(0, exit_code)
        lines = stream.getvalue().splitlines()
        self.assertIn("PRIORITY", lines[0])
        # Data row should not contain "high"
        self.assertNotIn("high", lines[1])

    def test_cli_bead_list_plain_high_priority_bead_shows_high_in_column(self) -> None:
        self.storage.create_bead(
            title="High bead", agent_type="developer", description="high priority", priority="high"
        )
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)
        exit_code = command_bead(Namespace(bead_command="list", plain=True), self.storage, console)
        self.assertEqual(0, exit_code)
        lines = stream.getvalue().splitlines()
        self.assertIn("PRIORITY", lines[0])
        self.assertIn("high", lines[1])

    def test_cli_bead_list_plain_mixed_priority_correct_per_row(self) -> None:
        self.storage.create_bead(title="Normal bead", agent_type="developer", description="normal")
        self.storage.create_bead(
            title="High bead", agent_type="developer", description="high", priority="high"
        )
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)
        exit_code = command_bead(Namespace(bead_command="list", plain=True), self.storage, console)
        self.assertEqual(0, exit_code)
        lines = stream.getvalue().splitlines()
        self.assertEqual(3, len(lines))  # header + 2 data rows
        self.assertIn("PRIORITY", lines[0])
        # Both rows present, at least one contains "high"
        data_text = "\n".join(lines[1:])
        self.assertIn("high", data_text)

    # ------------------------------------------------------------------
    # Priority: bead show JSON output
    # ------------------------------------------------------------------

    def test_cli_bead_show_normal_priority_omits_priority_key(self) -> None:
        bead = self.storage.create_bead(title="Normal bead", agent_type="developer", description="work")
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)
        exit_code = command_bead(Namespace(bead_command="show", bead_id=bead.bead_id), self.storage, console)
        self.assertEqual(0, exit_code)
        data = json.loads(stream.getvalue())
        self.assertNotIn("priority", data)

    def test_cli_bead_show_high_priority_includes_priority_key(self) -> None:
        bead = self.storage.create_bead(
            title="High bead", agent_type="developer", description="work", priority="high"
        )
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)
        exit_code = command_bead(Namespace(bead_command="show", bead_id=bead.bead_id), self.storage, console)
        self.assertEqual(0, exit_code)
        data = json.loads(stream.getvalue())
        self.assertIn("priority", data)
        self.assertEqual("high", data["priority"])


    # ================================================================
    # Tests 1-5: bead history
    # ================================================================

    def test_01_history_default_output_ascending_timestamp(self) -> None:
        bead = self.storage.create_bead(title="History bead", agent_type="developer", description="work")
        bead.execution_history = [
            ExecutionRecord(timestamp="2026-01-01T00:00:03+00:00", event="completed", agent_type="developer", summary="done"),
            ExecutionRecord(timestamp="2026-01-01T00:00:01+00:00", event="created", agent_type="scheduler", summary="Bead created"),
            ExecutionRecord(timestamp="2026-01-01T00:00:02+00:00", event="started", agent_type="developer", summary="starting work"),
        ]
        self.storage.save_bead(bead)
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)
        exit_code = command_bead(
            Namespace(bead_command="history", bead_id=bead.bead_id, event_filter=[], limit=None, output_json=False, plain=False),
            self.storage, console,
        )
        self.assertEqual(0, exit_code)
        lines = stream.getvalue().strip().splitlines()
        self.assertEqual(3, len(lines))
        self.assertIn("2026-01-01T00:00:01", lines[0])
        self.assertIn("2026-01-01T00:00:02", lines[1])
        self.assertIn("2026-01-01T00:00:03", lines[2])
        self.assertIn("created", lines[0])
        self.assertIn("started", lines[1])
        self.assertIn("completed", lines[2])

    def test_02_history_limit_returns_last_n_entries(self) -> None:
        bead = self.storage.create_bead(title="History limit bead", agent_type="developer", description="work")
        bead.execution_history = [
            ExecutionRecord(timestamp="2026-01-01T00:00:01+00:00", event="created", agent_type="scheduler", summary="Bead created"),
            ExecutionRecord(timestamp="2026-01-01T00:00:02+00:00", event="started", agent_type="developer", summary="starting"),
            ExecutionRecord(timestamp="2026-01-01T00:00:03+00:00", event="completed", agent_type="developer", summary="done"),
        ]
        self.storage.save_bead(bead)
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)
        exit_code = command_bead(
            Namespace(bead_command="history", bead_id=bead.bead_id, event_filter=[], limit=2, output_json=False, plain=False),
            self.storage, console,
        )
        self.assertEqual(0, exit_code)
        lines = stream.getvalue().strip().splitlines()
        self.assertEqual(2, len(lines))
        self.assertIn("started", lines[0])
        self.assertIn("completed", lines[1])

    def test_03_history_event_filter_single_event(self) -> None:
        bead = self.storage.create_bead(title="History single event bead", agent_type="developer", description="work")
        bead.execution_history = [
            ExecutionRecord(timestamp="2026-01-01T00:00:01+00:00", event="created", agent_type="scheduler", summary="Bead created"),
            ExecutionRecord(timestamp="2026-01-01T00:00:02+00:00", event="started", agent_type="developer", summary="starting"),
            ExecutionRecord(timestamp="2026-01-01T00:00:03+00:00", event="completed", agent_type="developer", summary="done"),
        ]
        self.storage.save_bead(bead)
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)
        exit_code = command_bead(
            Namespace(bead_command="history", bead_id=bead.bead_id, event_filter=["started"], limit=None, output_json=False, plain=False),
            self.storage, console,
        )
        self.assertEqual(0, exit_code)
        lines = stream.getvalue().strip().splitlines()
        self.assertEqual(1, len(lines))
        self.assertIn("started", lines[0])
        self.assertNotIn("created", lines[0])
        self.assertNotIn("completed", lines[0])

    def test_04_history_event_filter_multi_or_semantics(self) -> None:
        bead = self.storage.create_bead(title="History multi event bead", agent_type="developer", description="work")
        bead.execution_history = [
            ExecutionRecord(timestamp="2026-01-01T00:00:01+00:00", event="created", agent_type="scheduler", summary="Bead created"),
            ExecutionRecord(timestamp="2026-01-01T00:00:02+00:00", event="started", agent_type="developer", summary="starting"),
            ExecutionRecord(timestamp="2026-01-01T00:00:03+00:00", event="completed", agent_type="developer", summary="done"),
        ]
        self.storage.save_bead(bead)
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)
        exit_code = command_bead(
            Namespace(bead_command="history", bead_id=bead.bead_id, event_filter=["created", "completed"], limit=None, output_json=False, plain=False),
            self.storage, console,
        )
        self.assertEqual(0, exit_code)
        lines = stream.getvalue().strip().splitlines()
        self.assertEqual(2, len(lines))
        self.assertIn("created", lines[0])
        self.assertIn("completed", lines[1])

    def test_05_history_json_output_is_valid_array(self) -> None:
        bead = self.storage.create_bead(title="History JSON bead", agent_type="developer", description="work")
        bead.execution_history = [
            ExecutionRecord(timestamp="2026-01-01T00:00:01+00:00", event="created", agent_type="scheduler", summary="Bead created"),
            ExecutionRecord(timestamp="2026-01-01T00:00:02+00:00", event="started", agent_type="developer", summary="starting"),
        ]
        self.storage.save_bead(bead)
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)
        exit_code = command_bead(
            Namespace(bead_command="history", bead_id=bead.bead_id, event_filter=[], limit=None, output_json=True, plain=False),
            self.storage, console,
        )
        self.assertEqual(0, exit_code)
        data = json.loads(stream.getvalue())
        self.assertIsInstance(data, list)
        self.assertEqual(2, len(data))
        self.assertEqual("created", data[0]["event"])
        self.assertEqual("started", data[1]["event"])

    # ================================================================
    # Tests 6-10: show --field
    # ================================================================

    def test_06_show_field_scalar_status(self) -> None:
        bead = self.storage.create_bead(title="Field scalar bead", agent_type="developer", description="work")
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)
        exit_code = command_bead(
            Namespace(bead_command="show", bead_id=bead.bead_id, field="status"),
            self.storage, console,
        )
        self.assertEqual(0, exit_code)
        # create_bead defaults to BEAD_READY; verify --field returns that status
        self.assertEqual(bead.status + "\n", stream.getvalue())

    def test_07_show_field_nested_handoff_summary_completed(self) -> None:
        bead = self.storage.create_bead(title="Field nested bead", agent_type="developer", description="work")
        bead.handoff_summary = HandoffSummary(completed="all done")
        self.storage.save_bead(bead)
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)
        exit_code = command_bead(
            Namespace(bead_command="show", bead_id=bead.bead_id, field="handoff_summary.completed"),
            self.storage, console,
        )
        self.assertEqual(0, exit_code)
        self.assertEqual("all done\n", stream.getvalue())

    def test_08_show_field_array_negative_index(self) -> None:
        bead = self.storage.create_bead(title="Field array index bead", agent_type="developer", description="work")
        bead.execution_history = [
            ExecutionRecord(timestamp="2026-01-01T00:00:01+00:00", event="created", agent_type="scheduler", summary="Bead created"),
            ExecutionRecord(timestamp="2026-01-01T00:00:02+00:00", event="started", agent_type="developer", summary="starting"),
        ]
        self.storage.save_bead(bead)
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)
        exit_code = command_bead(
            Namespace(bead_command="show", bead_id=bead.bead_id, field="execution_history[-1].event"),
            self.storage, console,
        )
        self.assertEqual(0, exit_code)
        self.assertEqual("started\n", stream.getvalue())

    def test_09_show_field_missing_key_exits_nonzero(self) -> None:
        bead = self.storage.create_bead(title="Field missing bead", agent_type="developer", description="work")
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)
        stderr = io.StringIO()
        with patch("sys.stderr", stderr):
            exit_code = command_bead(
                Namespace(bead_command="show", bead_id=bead.bead_id, field="nonexistent_field"),
                self.storage, console,
            )
        self.assertEqual(1, exit_code)
        self.assertEqual("", stream.getvalue())
        self.assertIn("nonexistent_field", stderr.getvalue())

    def test_10_show_field_object_value_pretty_json(self) -> None:
        bead = self.storage.create_bead(title="Field object bead", agent_type="developer", description="work")
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)
        exit_code = command_bead(
            Namespace(bead_command="show", bead_id=bead.bead_id, field="handoff_summary"),
            self.storage, console,
        )
        self.assertEqual(0, exit_code)
        output_data = json.loads(stream.getvalue())
        self.assertIsInstance(output_data, dict)
        self.assertIn("completed", output_data)

    # ================================================================
    # Tests 11-16: list filters
    # ================================================================

    def test_11_list_status_single_filter(self) -> None:
        open_bead = self.storage.create_bead(title="Open bead", agent_type="developer", description="work")
        ip_bead = self.storage.create_bead(title="In progress bead", agent_type="developer", description="work")
        ip_bead.status = BEAD_IN_PROGRESS
        self.storage.save_bead(ip_bead)
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)
        exit_code = command_bead(
            Namespace(bead_command="list", status_filter=["in_progress"], agent_filter=[], feature_root=None, label_filter=[], plain=False),
            self.storage, console,
        )
        self.assertEqual(0, exit_code)
        data = json.loads(stream.getvalue())
        bead_ids = [b["bead_id"] for b in data]
        self.assertIn(ip_bead.bead_id, bead_ids)
        self.assertNotIn(open_bead.bead_id, bead_ids)

    def test_12_list_status_multi_or_semantics(self) -> None:
        # create_bead defaults to BEAD_READY; use in_progress as the non-matching bead
        non_matching = self.storage.create_bead(title="In-progress bead", agent_type="developer", description="work")
        non_matching.status = BEAD_IN_PROGRESS
        self.storage.save_bead(non_matching)
        ready_bead = self.storage.create_bead(title="Ready bead", agent_type="developer", description="work")
        blocked_bead = self.storage.create_bead(title="Blocked bead", agent_type="developer", description="work")
        blocked_bead.status = BEAD_BLOCKED
        self.storage.save_bead(blocked_bead)
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)
        exit_code = command_bead(
            Namespace(bead_command="list", status_filter=["ready", "blocked"], agent_filter=[], feature_root=None, label_filter=[], plain=False),
            self.storage, console,
        )
        self.assertEqual(0, exit_code)
        data = json.loads(stream.getvalue())
        bead_ids = [b["bead_id"] for b in data]
        self.assertIn(ready_bead.bead_id, bead_ids)
        self.assertIn(blocked_bead.bead_id, bead_ids)
        self.assertNotIn(non_matching.bead_id, bead_ids)

    def test_13_list_status_invalid_exits_nonzero(self) -> None:
        parser = build_parser()
        with self.assertRaises(SystemExit) as ctx:
            parser.parse_args(["bead", "list", "--status", "invalid_status"])
        self.assertEqual(2, ctx.exception.code)

    def test_14_list_agent_and_status_intersection(self) -> None:
        dev_ip = self.storage.create_bead(title="Dev in progress", agent_type="developer", description="work")
        tester_ip = self.storage.create_bead(title="Tester in progress", agent_type="tester", description="work")
        dev_ip.status = BEAD_IN_PROGRESS
        tester_ip.status = BEAD_IN_PROGRESS
        self.storage.save_bead(dev_ip)
        self.storage.save_bead(tester_ip)
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)
        exit_code = command_bead(
            Namespace(bead_command="list", status_filter=["in_progress"], agent_filter=["developer"], feature_root=None, label_filter=[], plain=False),
            self.storage, console,
        )
        self.assertEqual(0, exit_code)
        data = json.loads(stream.getvalue())
        bead_ids = [b["bead_id"] for b in data]
        self.assertIn(dev_ip.bead_id, bead_ids)
        self.assertNotIn(tester_ip.bead_id, bead_ids)

    def test_15_list_agent_invalid_exits_nonzero(self) -> None:
        parser = build_parser()
        with self.assertRaises(SystemExit) as ctx:
            parser.parse_args(["bead", "list", "--agent", "invalid_agent"])
        self.assertEqual(2, ctx.exception.code)

    def test_16_list_feature_root_restricts_to_tree(self) -> None:
        root_a = self.storage.create_bead(title="Root A", agent_type="developer", description="root")
        child_a = self.storage.create_bead(title="Child A", agent_type="tester", description="child", parent_id=root_a.bead_id)
        root_b = self.storage.create_bead(title="Root B", agent_type="developer", description="other root")
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)
        exit_code = command_bead(
            Namespace(bead_command="list", status_filter=[], agent_filter=[], feature_root=root_a.bead_id, label_filter=[], plain=False),
            self.storage, console,
        )
        self.assertEqual(0, exit_code)
        data = json.loads(stream.getvalue())
        bead_ids = [b["bead_id"] for b in data]
        self.assertIn(root_a.bead_id, bead_ids)
        self.assertIn(child_a.bead_id, bead_ids)
        self.assertNotIn(root_b.bead_id, bead_ids)

    # ================================================================
    # Tests 17-20: more show --field
    # ================================================================

    def test_17_show_field_none_value_exits_zero_empty_line(self) -> None:
        bead = self.storage.create_bead(title="Null field bead", agent_type="developer", description="work")
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)
        exit_code = command_bead(
            Namespace(bead_command="show", bead_id=bead.bead_id, field="parent_id"),
            self.storage, console,
        )
        self.assertEqual(0, exit_code)
        self.assertEqual("\n", stream.getvalue())

    def test_18_show_field_bool_value_lowercase(self) -> None:
        bead = self.storage.create_bead(title="Bool field bead", agent_type="developer", description="work")
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)
        exit_code = command_bead(
            Namespace(bead_command="show", bead_id=bead.bead_id, field="handoff_summary.requires_followup"),
            self.storage, console,
        )
        self.assertEqual(0, exit_code)
        self.assertEqual("false\n", stream.getvalue())

    def test_19_show_field_int_value(self) -> None:
        bead = self.storage.create_bead(title="Int field bead", agent_type="developer", description="work")
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)
        exit_code = command_bead(
            Namespace(bead_command="show", bead_id=bead.bead_id, field="handoff_summary.findings_count"),
            self.storage, console,
        )
        self.assertEqual(0, exit_code)
        self.assertEqual("0\n", stream.getvalue())

    def test_20_show_field_array_oob_exits_nonzero_with_length(self) -> None:
        bead = self.storage.create_bead(title="OOB array bead", agent_type="developer", description="work")
        bead.execution_history = [
            ExecutionRecord(timestamp="2026-01-01T00:00:01+00:00", event="created", agent_type="scheduler", summary="Bead created"),
        ]
        self.storage.save_bead(bead)
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)
        stderr = io.StringIO()
        with patch("sys.stderr", stderr):
            exit_code = command_bead(
                Namespace(bead_command="show", bead_id=bead.bead_id, field="execution_history[5].event"),
                self.storage, console,
            )
        self.assertEqual(1, exit_code)
        self.assertEqual("", stream.getvalue())
        self.assertIn("(length 1)", stderr.getvalue())

    # ================================================================
    # Test 21: backwards compatibility
    # ================================================================

    def test_21_backwards_compat_list_show_unchanged_label_works(self) -> None:
        bead_a = self.storage.create_bead(title="Label bead A", agent_type="developer", description="work", labels=["api"])
        bead_b = self.storage.create_bead(title="Label bead B", agent_type="developer", description="work", labels=["web"])

        # no-flag list returns full JSON
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)
        exit_code = command_bead(Namespace(bead_command="list"), self.storage, console)
        self.assertEqual(0, exit_code)
        data = json.loads(stream.getvalue())
        self.assertEqual(2, len(data))

        # show without --field returns full JSON
        stream2 = io.StringIO()
        console2 = ConsoleReporter(stream=stream2)
        exit_code2 = command_bead(Namespace(bead_command="show", bead_id=bead_a.bead_id), self.storage, console2)
        self.assertEqual(0, exit_code2)
        bead_data = json.loads(stream2.getvalue())
        self.assertEqual(bead_a.bead_id, bead_data["bead_id"])

        # --label filter still works
        stream3 = io.StringIO()
        console3 = ConsoleReporter(stream=stream3)
        exit_code3 = command_bead(
            Namespace(bead_command="list", label_filter=["api"]),
            self.storage, console3,
        )
        self.assertEqual(0, exit_code3)
        data3 = json.loads(stream3.getvalue())
        bead_ids = [b["bead_id"] for b in data3]
        self.assertIn(bead_a.bead_id, bead_ids)
        self.assertNotIn(bead_b.bead_id, bead_ids)


if __name__ == "__main__":
    unittest.main()
