"""Tests for the recovery_for field added to the Bead model.

Covers:
1. Bead.from_dict omits recovery_for → field is None (backwards compat)
2. Bead.from_dict with recovery_for="B-abc" → field set correctly
3. bead.to_dict() includes recovery_for key
4. Round-trip Bead.from_dict(bead.to_dict()) preserves the value
5. recovery_for=None serialises as JSON null (not missing key)
"""
from __future__ import annotations

import json
import sys
import tempfile
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_takt.models import Bead
from agent_takt.storage import RepositoryStorage

RepositoryStorage._auto_commit = False


class RecoveryForModelTests(unittest.TestCase):
    """Unit tests for Bead.recovery_for field serialisation / deserialisation."""

    # --- scenario 1: legacy dict without recovery_for yields None ---

    def test_from_dict_missing_recovery_for_yields_none(self) -> None:
        data = {
            "bead_id": "B-legacy01",
            "title": "Legacy bead",
            "agent_type": "developer",
            "description": "old format without recovery_for",
        }
        bead = Bead.from_dict(data)
        self.assertIsNone(bead.recovery_for)

    # --- scenario 2: from_dict with recovery_for string sets the field ---

    def test_from_dict_with_recovery_for_sets_field(self) -> None:
        data = {
            "bead_id": "B-corrective",
            "title": "Corrective bead",
            "agent_type": "developer",
            "description": "retry for a failed bead",
            "recovery_for": "B-abc12345",
        }
        bead = Bead.from_dict(data)
        self.assertEqual("B-abc12345", bead.recovery_for)

    # --- scenario 3: to_dict() always includes the recovery_for key ---

    def test_to_dict_includes_recovery_for_key_when_none(self) -> None:
        bead = Bead(
            bead_id="B-test0001",
            title="Test bead",
            agent_type="developer",
            description="no recovery",
        )
        d = bead.to_dict()
        self.assertIn("recovery_for", d)
        self.assertIsNone(d["recovery_for"])

    def test_to_dict_includes_recovery_for_key_when_set(self) -> None:
        bead = Bead(
            bead_id="B-test0002",
            title="Corrective bead",
            agent_type="developer",
            description="retry",
            recovery_for="B-original",
        )
        d = bead.to_dict()
        self.assertIn("recovery_for", d)
        self.assertEqual("B-original", d["recovery_for"])

    # --- scenario 4: round-trip preserves the value ---

    def test_round_trip_preserves_recovery_for_value(self) -> None:
        bead = Bead(
            bead_id="B-roundtrip",
            title="Round-trip bead",
            agent_type="developer",
            description="check persistence",
            recovery_for="B-parent00",
        )
        restored = Bead.from_dict(bead.to_dict())
        self.assertEqual("B-parent00", restored.recovery_for)

    def test_round_trip_preserves_recovery_for_none(self) -> None:
        bead = Bead(
            bead_id="B-roundtr02",
            title="Round-trip none",
            agent_type="developer",
            description="check none",
            recovery_for=None,
        )
        restored = Bead.from_dict(bead.to_dict())
        self.assertIsNone(restored.recovery_for)

    # --- scenario 5: recovery_for=None serialises as JSON null, not missing ---

    def test_none_serialises_as_json_null_not_missing_key(self) -> None:
        bead = Bead(
            bead_id="B-nulltest",
            title="Null test",
            agent_type="developer",
            description="serialisation",
            recovery_for=None,
        )
        serialised = json.dumps(bead.to_dict())
        parsed = json.loads(serialised)
        self.assertIn("recovery_for", parsed)
        self.assertIsNone(parsed["recovery_for"])


class RecoveryForStorageTests(unittest.TestCase):
    """Integration tests: recovery_for survives a storage round-trip through JSON on disk."""

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

    def test_storage_persists_recovery_for_value(self) -> None:
        bead = self.storage.create_bead(
            title="Corrective bead",
            agent_type="developer",
            description="test recovery_for persistence",
        )
        bead.recovery_for = "B-original0"
        self.storage.save_bead(bead)
        reloaded = self.storage.load_bead(bead.bead_id)
        self.assertEqual("B-original0", reloaded.recovery_for)

    def test_storage_persists_recovery_for_none(self) -> None:
        bead = self.storage.create_bead(
            title="Normal bead",
            agent_type="developer",
            description="no recovery_for",
        )
        # recovery_for is None by default; verify it survives a save/load cycle
        self.storage.save_bead(bead)
        reloaded = self.storage.load_bead(bead.bead_id)
        self.assertIsNone(reloaded.recovery_for)


if __name__ == "__main__":
    unittest.main()
