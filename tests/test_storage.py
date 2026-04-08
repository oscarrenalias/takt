from __future__ import annotations

import shutil
import sys
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_takt.models import Bead
from agent_takt.storage import RepositoryStorage

RepositoryStorage._auto_commit = False

from helpers import OrchestratorTests as _OrchestratorBase  # noqa: E402


class StorageTests(_OrchestratorBase):

    def test_allocate_bead_id_returns_uuid_format(self) -> None:
        import re
        bead_id = self.storage.allocate_bead_id()
        self.assertRegex(bead_id, r"^B-[0-9a-f]{8}$")

    def test_allocate_bead_id_returns_unique_ids(self) -> None:
        ids = {self.storage.allocate_bead_id() for _ in range(20)}
        self.assertEqual(20, len(ids))

    def test_allocate_bead_id_via_create_bead_uses_uuid_format(self) -> None:
        import re
        bead = self.storage.create_bead(title="UUID test", agent_type="developer", description="work")
        self.assertRegex(bead.bead_id, r"^B-[0-9a-f]{8}$")

    def test_resolve_bead_id_exact_match(self) -> None:
        bead = self.storage.create_bead(title="Exact", agent_type="developer", description="work")
        resolved = self.storage.resolve_bead_id(bead.bead_id)
        self.assertEqual(bead.bead_id, resolved)

    def test_resolve_bead_id_prefix_match(self) -> None:
        bead = self.storage.create_bead(title="Prefix", agent_type="developer", description="work")
        # Use a 4-char prefix (B- plus 2 hex chars) that is unambiguous
        prefix = bead.bead_id[:4]
        # If only one bead exists, the prefix resolves to it
        resolved = self.storage.resolve_bead_id(prefix)
        self.assertEqual(bead.bead_id, resolved)

    def test_resolve_bead_id_no_match_raises(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            self.storage.resolve_bead_id("B-nonexist")
        self.assertIn("No bead found", str(ctx.exception))

    def test_resolve_bead_id_ambiguous_raises(self) -> None:
        # Create two beads then find a common prefix
        bead_a = self.storage.create_bead(title="A", agent_type="developer", description="a")
        bead_b = self.storage.create_bead(title="B", agent_type="developer", description="b")
        # Find a shared prefix (both start with "B-")
        with self.assertRaises(ValueError) as ctx:
            self.storage.resolve_bead_id("B-")
        self.assertIn("Ambiguous prefix", str(ctx.exception))
        self.assertIn(bead_a.bead_id, str(ctx.exception))
        self.assertIn(bead_b.bead_id, str(ctx.exception))

    def test_resolve_bead_id_no_beads_dir_raises(self) -> None:
        shutil.rmtree(self.storage.beads_dir)
        with self.assertRaises(ValueError) as ctx:
            self.storage.resolve_bead_id("B-anything")
        self.assertIn("No bead found", str(ctx.exception))

    def test_list_beads_sorted_by_creation_time(self) -> None:
        """list_beads() returns beads ordered by creation timestamp, not by ID."""
        bead_a = self.storage.create_bead(title="Alpha", agent_type="developer", description="first")
        time.sleep(0.01)  # ensure distinct timestamps
        bead_b = self.storage.create_bead(title="Beta", agent_type="developer", description="second")
        beads = self.storage.list_beads()
        ids = [b.bead_id for b in beads]
        self.assertEqual([bead_a.bead_id, bead_b.bead_id], ids)

    def test_old_sequential_ids_coexist_with_uuid_ids(self) -> None:
        """Beads with old sequential IDs (B0001) load alongside new UUID-format IDs."""
        import re
        # Create a bead with the old sequential format
        old_bead = self.storage.create_bead(
            bead_id="B0001",
            title="Legacy bead",
            agent_type="developer",
            description="old format",
        )
        # Create a bead with the new UUID format (auto-allocated)
        new_bead = self.storage.create_bead(title="UUID bead", agent_type="developer", description="new format")
        self.assertRegex(new_bead.bead_id, r"^B-[0-9a-f]{8}$")

        beads = self.storage.list_beads()
        bead_ids = {b.bead_id for b in beads}
        self.assertIn("B0001", bead_ids)
        self.assertIn(new_bead.bead_id, bead_ids)
        # Both load successfully
        loaded_old = self.storage.load_bead("B0001")
        self.assertEqual("Legacy bead", loaded_old.title)
        loaded_new = self.storage.load_bead(new_bead.bead_id)
        self.assertEqual("UUID bead", loaded_new.title)

    def test_create_bead_default_priority_is_none(self) -> None:
        bead = self.storage.create_bead(title="Priority default", agent_type="developer", description="x")
        self.assertIsNone(bead.priority)
        loaded = self.storage.load_bead(bead.bead_id)
        self.assertIsNone(loaded.priority)

    def test_create_bead_priority_high_persists(self) -> None:
        bead = self.storage.create_bead(title="High priority", agent_type="developer", description="x", priority="high")
        self.assertEqual("high", bead.priority)
        loaded = self.storage.load_bead(bead.bead_id)
        self.assertEqual("high", loaded.priority)


class BeadModelTests(unittest.TestCase):
    """Bead model priority validation and serialization."""

    def _minimal_dict(self) -> dict:
        return {
            "bead_id": "B-00000001",
            "title": "Test bead",
            "agent_type": "developer",
            "description": "test",
        }

    def test_bead_priority_defaults_to_none(self) -> None:
        bead = Bead(bead_id="B-00000001", title="T", agent_type="developer", description="d")
        self.assertIsNone(bead.priority)

    def test_bead_priority_high_valid(self) -> None:
        bead = Bead(bead_id="B-00000001", title="T", agent_type="developer", description="d", priority="high")
        self.assertEqual("high", bead.priority)

    def test_bead_priority_normal_normalizes_to_none(self) -> None:
        bead = Bead(bead_id="B-00000001", title="T", agent_type="developer", description="d", priority="normal")
        self.assertIsNone(bead.priority)

    def test_bead_priority_invalid_raises_value_error(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            Bead(bead_id="B-00000001", title="T", agent_type="developer", description="d", priority="urgent")
        msg = str(ctx.exception)
        self.assertIn("urgent", msg)
        # Error message should list valid values
        self.assertIn("high", msg)
        self.assertIn("normal", msg)

    def test_bead_from_dict_missing_priority_defaults_to_none(self) -> None:
        d = self._minimal_dict()
        bead = Bead.from_dict(d)
        self.assertIsNone(bead.priority)

    def test_bead_from_dict_priority_none(self) -> None:
        d = self._minimal_dict()
        d["priority"] = None
        bead = Bead.from_dict(d)
        self.assertIsNone(bead.priority)

    def test_bead_from_dict_priority_high(self) -> None:
        d = self._minimal_dict()
        d["priority"] = "high"
        bead = Bead.from_dict(d)
        self.assertEqual("high", bead.priority)

    def test_bead_to_dict_includes_priority_key(self) -> None:
        bead = Bead(bead_id="B-00000001", title="T", agent_type="developer", description="d")
        d = bead.to_dict()
        self.assertIn("priority", d)

    def test_bead_round_trip_priority_high_preserved(self) -> None:
        bead = Bead(bead_id="B-00000001", title="T", agent_type="developer", description="d", priority="high")
        restored = Bead.from_dict(bead.to_dict())
        self.assertEqual("high", restored.priority)

    def test_bead_round_trip_priority_none_preserved(self) -> None:
        bead = Bead(bead_id="B-00000001", title="T", agent_type="developer", description="d")
        restored = Bead.from_dict(bead.to_dict())
        self.assertIsNone(restored.priority)


if __name__ == "__main__":
    unittest.main()
