"""Every event type recorded via record_event must be documented in docs/events.md."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src" / "agent_takt"
EVENTS_DOC = REPO_ROOT / "docs" / "events.md"
_PATTERN = re.compile(r"""record_event\(\s*["']([a-z_]+)["']""", re.S)


def _recorded_event_names() -> set[str]:
    names: set[str] = set()
    for path in SRC_ROOT.rglob("*.py"):
        names.update(_PATTERN.findall(path.read_text(encoding="utf-8")))
    return names


class EventsDocTests(unittest.TestCase):
    def test_scan_finds_known_events(self) -> None:
        names = _recorded_event_names()
        self.assertIn("merge_gate_failed", names)
        self.assertIn("scheduler_run_finished", names)

    def test_every_recorded_event_is_documented(self) -> None:
        self.assertTrue(EVENTS_DOC.is_file(), "docs/events.md is missing")
        doc = EVENTS_DOC.read_text(encoding="utf-8")
        missing = sorted(n for n in _recorded_event_names() if n not in doc)
        self.assertEqual([], missing, f"events not documented in docs/events.md: {missing}")


if __name__ == "__main__":
    unittest.main()
