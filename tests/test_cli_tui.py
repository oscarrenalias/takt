from __future__ import annotations

import io
import sys
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_takt.cli import command_tui
from agent_takt.console import ConsoleReporter
from agent_takt.models import BEAD_DONE, BEAD_READY
from agent_takt.storage import RepositoryStorage

RepositoryStorage._auto_commit = False

from helpers import OrchestratorTests as _OrchestratorBase  # noqa: E402


class CliTuiTests(_OrchestratorBase):

    def test_command_tui_reports_missing_render_dependency_without_mutating_state(self) -> None:
        bead = self.storage.create_bead(title="Ready", agent_type="developer", description="work", status=BEAD_READY)
        original = self.storage.load_bead(bead.bead_id).to_dict()
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)

        with patch("agent_takt.tui.load_textual_runtime", side_effect=RuntimeError("missing textual")):
            exit_code = command_tui(Namespace(feature_root=None, refresh_seconds=3, max_workers=1), self.storage, console)

        self.assertEqual(1, exit_code)
        self.assertIn("missing textual", stream.getvalue())
        self.assertEqual(original, self.storage.load_bead(bead.bead_id).to_dict())

    def test_command_tui_forwards_feature_root_refresh_and_console_stream(self) -> None:
        epic = self.storage.create_bead(title="Epic", agent_type="planner", description="root", status=BEAD_DONE, bead_type="epic")
        root = self.storage.create_bead(title="Feature A", agent_type="developer", description="A", parent_id=epic.bead_id, status=BEAD_DONE)
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)

        with patch("agent_takt.tui.run_tui", return_value=0) as run_tui:
            exit_code = command_tui(Namespace(feature_root=root.bead_id, refresh_seconds=9, max_workers=1), self.storage, console)

        self.assertEqual(0, exit_code)
        run_tui.assert_called_once_with(
            self.storage,
            feature_root_id=root.bead_id,
            refresh_seconds=9,
            max_workers=1,
            stream=stream,
        )

    def test_command_tui_rejects_unknown_feature_root(self) -> None:
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)

        with patch("agent_takt.tui.run_tui") as run_tui:
            exit_code = command_tui(Namespace(feature_root="B9999", refresh_seconds=3, max_workers=1), self.storage, console)

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

        with patch("agent_takt.tui.run_tui") as run_tui:
            exit_code = command_tui(Namespace(feature_root=child.bead_id, refresh_seconds=3, max_workers=1), self.storage, console)

        self.assertEqual(1, exit_code)
        self.assertIn(f"{child.bead_id} is not a valid feature root", stream.getvalue())
        run_tui.assert_not_called()


if __name__ == "__main__":
    unittest.main()
