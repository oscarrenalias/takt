"""Tests for label support on beads.

Covers:
1. bead create --label stores multiple labels
2. bead label <id> existing-label does not duplicate
3. bead unlabel <id> present-label removes it
4. bead unlabel <id> absent-label exits 0 without error
5. bead list --label filters to matching beads only
6. bead list --label A --label B requires both (AND semantics)
7. bead show JSON includes 'labels' key
8. Loading a legacy JSON without 'labels' key yields empty list
"""
from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_takt.cli import command_bead
from agent_takt.console import ConsoleReporter
from agent_takt.models import Bead
from agent_takt.storage import RepositoryStorage

RepositoryStorage._auto_commit = False


class LabelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        subprocess.run(["git", "init"], cwd=self.root, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=self.root, check=True)
        self.storage = RepositoryStorage(self.root)
        self.storage.initialize()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _console(self) -> tuple[ConsoleReporter, io.StringIO]:
        stream = io.StringIO()
        return ConsoleReporter(stream=stream), stream

    # --- scenario 1: create with multiple labels ---

    def test_create_with_labels_stores_all(self) -> None:
        console, _ = self._console()
        exit_code = command_bead(
            Namespace(
                bead_command="create",
                title="My bead",
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
                label=["refactor", "hotfix"],
                priority=None,
            ),
            self.storage,
            console,
        )
        self.assertEqual(0, exit_code)
        beads = self.storage.list_beads()
        self.assertEqual(1, len(beads))
        self.assertIn("refactor", beads[0].labels)
        self.assertIn("hotfix", beads[0].labels)
        self.assertEqual(2, len(beads[0].labels))

    # --- scenario 2: label command does not duplicate ---

    def test_label_command_no_duplicate(self) -> None:
        bead = self.storage.create_bead(
            title="Bead", agent_type="developer", description="d", labels=["existing"]
        )
        console, stream = self._console()
        exit_code = command_bead(
            Namespace(bead_command="label", bead_id=bead.bead_id, labels=["existing"]),
            self.storage,
            console,
        )
        self.assertEqual(0, exit_code)
        reloaded = self.storage.load_bead(bead.bead_id)
        self.assertEqual(["existing"], reloaded.labels)

    # --- scenario 3: unlabel removes present label ---

    def test_unlabel_removes_present_label(self) -> None:
        bead = self.storage.create_bead(
            title="Bead", agent_type="developer", description="d", labels=["to-remove", "keep"]
        )
        console, _ = self._console()
        exit_code = command_bead(
            Namespace(bead_command="unlabel", bead_id=bead.bead_id, label="to-remove"),
            self.storage,
            console,
        )
        self.assertEqual(0, exit_code)
        reloaded = self.storage.load_bead(bead.bead_id)
        self.assertNotIn("to-remove", reloaded.labels)
        self.assertIn("keep", reloaded.labels)

    # --- scenario 4: unlabel absent label exits 0 ---

    def test_unlabel_absent_label_exits_zero(self) -> None:
        bead = self.storage.create_bead(
            title="Bead", agent_type="developer", description="d", labels=[]
        )
        console, _ = self._console()
        exit_code = command_bead(
            Namespace(bead_command="unlabel", bead_id=bead.bead_id, label="nonexistent"),
            self.storage,
            console,
        )
        self.assertEqual(0, exit_code)

    # --- scenario 5: list --label returns only matching ---

    def test_list_label_filter_returns_matching_only(self) -> None:
        self.storage.create_bead(
            title="Alpha", agent_type="developer", description="d", labels=["refactor"]
        )
        self.storage.create_bead(
            title="Beta", agent_type="developer", description="d", labels=["hotfix"]
        )
        console, stream = self._console()
        exit_code = command_bead(
            Namespace(bead_command="list", plain=False, label_filter=["refactor"]),
            self.storage,
            console,
        )
        self.assertEqual(0, exit_code)
        result = json.loads(stream.getvalue())
        self.assertEqual(1, len(result))
        self.assertIn("refactor", result[0]["labels"])

    # --- scenario 6: list --label A --label B (AND semantics) ---

    def test_list_multi_label_filter_requires_all(self) -> None:
        self.storage.create_bead(
            title="Has-both", agent_type="developer", description="d", labels=["A", "B"]
        )
        self.storage.create_bead(
            title="Has-A-only", agent_type="developer", description="d", labels=["A"]
        )
        self.storage.create_bead(
            title="Has-neither", agent_type="developer", description="d", labels=[]
        )
        console, stream = self._console()
        exit_code = command_bead(
            Namespace(bead_command="list", plain=False, label_filter=["A", "B"]),
            self.storage,
            console,
        )
        self.assertEqual(0, exit_code)
        result = json.loads(stream.getvalue())
        self.assertEqual(1, len(result))
        self.assertEqual("Has-both", result[0]["title"])

    # --- scenario 7: bead show JSON includes labels key ---

    def test_show_includes_labels_key(self) -> None:
        bead = self.storage.create_bead(
            title="Bead", agent_type="developer", description="d", labels=["x"]
        )
        console, stream = self._console()
        exit_code = command_bead(
            Namespace(bead_command="show", bead_id=bead.bead_id),
            self.storage,
            console,
        )
        self.assertEqual(0, exit_code)
        data = json.loads(stream.getvalue())
        self.assertIn("labels", data)
        self.assertEqual(["x"], data["labels"])

    # --- scenario 8: legacy JSON without labels key yields empty list ---

    def test_from_dict_legacy_missing_labels_yields_empty(self) -> None:
        legacy_data = {
            "bead_id": "B-legacy",
            "title": "Old bead",
            "agent_type": "developer",
            "description": "legacy",
        }
        bead = Bead.from_dict(legacy_data)
        self.assertEqual([], bead.labels)


if __name__ == "__main__":
    unittest.main()
