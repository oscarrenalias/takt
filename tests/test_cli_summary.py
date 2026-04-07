from __future__ import annotations

import io
import json
import sys
import unittest
from argparse import Namespace
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_takt.cli import command_handoff, command_retry, command_summary
from agent_takt.console import ConsoleReporter
from agent_takt.models import (
    BEAD_BLOCKED,
    BEAD_DONE,
    BEAD_HANDED_OFF,
    BEAD_IN_PROGRESS,
    BEAD_OPEN,
    BEAD_READY,
    HandoffSummary,
)
from agent_takt.storage import RepositoryStorage

RepositoryStorage._auto_commit = False

from helpers import OrchestratorTests as _OrchestratorBase  # noqa: E402


class CliSummaryTests(_OrchestratorBase):

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
        self.assertEqual(ready_ids[:5], [item["bead_id"] for item in summary["next_up"]])
        self.assertTrue(all(item["status"] == BEAD_READY for item in summary["next_up"]))

        self.assertEqual(5, len(summary["attention"]))
        self.assertEqual(
            blocked_ids[:5],
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
        exit_code = command_summary(Namespace(feature_root="B-nonexist"), self.storage, console)
        self.assertEqual(1, exit_code)

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

    def test_cli_handoff_resolves_prefix(self) -> None:
        bead = self.storage.create_bead(title="Handoff me", agent_type="developer", description="done")
        prefix = bead.bead_id[:4]
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)
        exit_code = command_handoff(
            Namespace(bead_id=prefix, to="tester", summary="Hand off to tester"),
            self.storage,
            console,
        )
        self.assertEqual(0, exit_code)
        beads = self.storage.list_beads()
        child_ids = [b.bead_id for b in beads if b.bead_id != bead.bead_id]
        self.assertEqual(1, len(child_ids))
        child = self.storage.load_bead(child_ids[0])
        self.assertEqual("tester", child.agent_type)
        self.assertIn(bead.bead_id, child.dependencies)

    def test_cli_retry_resolves_prefix(self) -> None:
        bead = self.storage.create_bead(title="Retry me", agent_type="developer", description="blocked")
        bead.status = BEAD_BLOCKED
        bead.block_reason = "something failed"
        self.storage.save_bead(bead)
        prefix = bead.bead_id[:4]
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)
        from agent_takt.cli import command_retry
        exit_code = command_retry(Namespace(bead_id=prefix), self.storage, console)
        self.assertEqual(0, exit_code)
        reloaded = self.storage.load_bead(bead.bead_id)
        self.assertEqual(BEAD_READY, reloaded.status)
        self.assertEqual("", reloaded.block_reason)

    def test_cli_summary_resolves_feature_root_prefix(self) -> None:
        bead = self.storage.create_bead(title="Feature root", agent_type="developer", description="work")
        prefix = bead.bead_id[:4]
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)
        exit_code = command_summary(Namespace(feature_root=prefix), self.storage, console)
        self.assertEqual(0, exit_code)
        data = json.loads(stream.getvalue())
        self.assertIn("counts", data)

    def test_cli_summary_returns_error_on_invalid_feature_root_prefix(self) -> None:
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)
        exit_code = command_summary(Namespace(feature_root="B-nonexist"), self.storage, console)
        self.assertEqual(1, exit_code)

    def test_cli_summary_no_feature_root_passes_none(self) -> None:
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)
        exit_code = command_summary(Namespace(feature_root=None), self.storage, console)
        self.assertEqual(0, exit_code)
        data = json.loads(stream.getvalue())
        self.assertIn("counts", data)


if __name__ == "__main__":
    unittest.main()
