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
        subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=self.root, check=True)
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


class PriorityModelTests(unittest.TestCase):
    """Unit tests for Bead.priority field validation and serialisation."""

    def test_default_priority_is_none(self) -> None:
        bead = Bead(bead_id="B-p00001", title="T", agent_type="developer", description="d")
        self.assertIsNone(bead.priority)

    def test_high_priority_accepted(self) -> None:
        bead = Bead(bead_id="B-p00002", title="T", agent_type="developer", description="d", priority="high")
        self.assertEqual("high", bead.priority)

    def test_normal_priority_normalised_to_none(self) -> None:
        bead = Bead(bead_id="B-p00003", title="T", agent_type="developer", description="d", priority="normal")
        self.assertIsNone(bead.priority)

    def test_invalid_priority_raises_value_error(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            Bead(bead_id="B-p00004", title="T", agent_type="developer", description="d", priority="urgent")
        self.assertIn("urgent", str(ctx.exception))
        self.assertIn("high", str(ctx.exception))

    def test_from_dict_missing_priority_yields_none(self) -> None:
        data = {"bead_id": "B-p00005", "title": "T", "agent_type": "developer", "description": "d"}
        bead = Bead.from_dict(data)
        self.assertIsNone(bead.priority)

    def test_from_dict_with_high_priority(self) -> None:
        data = {"bead_id": "B-p00006", "title": "T", "agent_type": "developer", "description": "d", "priority": "high"}
        bead = Bead.from_dict(data)
        self.assertEqual("high", bead.priority)

    def test_from_dict_normal_normalised_to_none(self) -> None:
        data = {"bead_id": "B-p00007", "title": "T", "agent_type": "developer", "description": "d", "priority": "normal"}
        bead = Bead.from_dict(data)
        self.assertIsNone(bead.priority)

    def test_round_trip_priority_high(self) -> None:
        bead = Bead(bead_id="B-p00008", title="T", agent_type="developer", description="d", priority="high")
        restored = Bead.from_dict(bead.to_dict())
        self.assertEqual("high", restored.priority)

    def test_round_trip_priority_none(self) -> None:
        bead = Bead(bead_id="B-p00009", title="T", agent_type="developer", description="d", priority=None)
        restored = Bead.from_dict(bead.to_dict())
        self.assertIsNone(restored.priority)

    def test_to_dict_includes_priority_key(self) -> None:
        bead = Bead(bead_id="B-p00010", title="T", agent_type="developer", description="d", priority="high")
        d = bead.to_dict()
        self.assertIn("priority", d)
        self.assertEqual("high", d["priority"])

    def test_both_recovery_for_and_priority_set(self) -> None:
        bead = Bead(
            bead_id="B-p00011",
            title="T",
            agent_type="developer",
            description="d",
            recovery_for="B-original",
            priority="high",
        )
        self.assertEqual("B-original", bead.recovery_for)
        self.assertEqual("high", bead.priority)
        restored = Bead.from_dict(bead.to_dict())
        self.assertEqual("B-original", restored.recovery_for)
        self.assertEqual("high", restored.priority)


class PriorityStorageTests(unittest.TestCase):
    """Integration tests: priority field survives storage round-trip."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        subprocess.run(["git", "init"], cwd=self.root, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=self.root, check=True)
        self.storage = RepositoryStorage(self.root)
        self.storage.initialize()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_create_bead_with_high_priority(self) -> None:
        bead = self.storage.create_bead(
            title="High priority bead",
            agent_type="developer",
            description="urgent work",
            priority="high",
        )
        self.assertEqual("high", bead.priority)
        reloaded = self.storage.load_bead(bead.bead_id)
        self.assertEqual("high", reloaded.priority)

    def test_create_bead_default_priority_is_none(self) -> None:
        bead = self.storage.create_bead(
            title="Normal bead",
            agent_type="developer",
            description="normal work",
        )
        self.assertIsNone(bead.priority)
        reloaded = self.storage.load_bead(bead.bead_id)
        self.assertIsNone(reloaded.priority)

    def test_create_bead_normal_priority_stored_as_none(self) -> None:
        bead = self.storage.create_bead(
            title="Normal alias bead",
            agent_type="developer",
            description="normal alias",
            priority="normal",
        )
        self.assertIsNone(bead.priority)
        reloaded = self.storage.load_bead(bead.bead_id)
        self.assertIsNone(reloaded.priority)

    def test_create_bead_with_recovery_for_and_priority(self) -> None:
        original = self.storage.create_bead(
            title="Original bead",
            agent_type="developer",
            description="original",
        )
        recovery = self.storage.create_bead(
            title="Recovery bead",
            agent_type="recovery",
            description="recovery run",
            recovery_for=original.bead_id,
            priority="high",
        )
        self.assertEqual(original.bead_id, recovery.recovery_for)
        self.assertEqual("high", recovery.priority)
        reloaded = self.storage.load_bead(recovery.bead_id)
        self.assertEqual(original.bead_id, reloaded.recovery_for)
        self.assertEqual("high", reloaded.priority)


if __name__ == "__main__":
    unittest.main()
