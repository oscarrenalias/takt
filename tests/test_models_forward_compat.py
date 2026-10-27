from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent_takt.models import Bead


def _payload() -> dict:
    return {
        "bead_id": "B-aaaa1111",
        "title": "t",
        "agent_type": "developer",
        "description": "d",
        "status": "ready",
        "retries": 2,
        "future_top": {"a": 1},
        "handoff_summary": {"completed": "done", "future_handoff": [1, 2]},
        "lease": {"owner": "w1", "expires_at": "2030-01-01T00:00:00+00:00", "future_lease": "x"},
        "execution_history": [
            {
                "timestamp": "2030-01-01T00:00:00+00:00",
                "event": "created",
                "agent_type": "scheduler",
                "summary": "s",
                "details": {},
                "future_record": True,
            }
        ],
    }


class ForwardCompatTests(unittest.TestCase):
    def test_unknown_keys_load_and_known_fields_unchanged(self) -> None:
        bead = Bead.from_dict(_payload())
        self.assertEqual(bead.handoff_summary.completed, "done")
        self.assertEqual(bead.lease.owner, "w1")
        self.assertEqual(bead.execution_history[0].event, "created")
        self.assertEqual(bead.retries, 2)
        self.assertEqual(bead.status, "ready")
        self.assertFalse(hasattr(bead.handoff_summary, "future_handoff"))

    def test_unknown_keys_round_trip_through_json(self) -> None:
        bead = Bead.from_dict(_payload())
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "b.json"
            path.write_text(json.dumps(bead.to_dict(), indent=2), encoding="utf-8")
            reloaded = Bead.from_dict(json.loads(path.read_text(encoding="utf-8")))
        out = reloaded.to_dict()
        self.assertEqual(out["future_top"], {"a": 1})
        self.assertEqual(out["handoff_summary"]["future_handoff"], [1, 2])
        self.assertEqual(out["lease"]["future_lease"], "x")
        self.assertIs(out["execution_history"][0]["future_record"], True)
        self.assertNotIn("_extra", json.dumps(out))

    def test_no_extra_means_unchanged_dict(self) -> None:
        bead = Bead.from_dict({"bead_id": "B-1", "title": "t", "agent_type": "developer", "description": "d"})
        self.assertNotIn("_extra", bead.to_dict())
        self.assertEqual(Bead.from_dict(bead.to_dict()), bead)


if __name__ == "__main__":
    unittest.main()
