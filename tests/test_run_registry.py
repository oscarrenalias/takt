from __future__ import annotations

import json
import os
import socket
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent_takt import run_registry as rr


class FakeStorage:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.events: list[tuple[str, dict]] = []

    def record_event(self, event_type: str, payload: dict) -> None:
        self.events.append((event_type, payload))


class RunRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.host = socket.gethostname()

    def _marker(self, run_id: str, pid: int, host: str | None = None) -> dict:
        return {"run_id": run_id, "pid": pid, "hostname": host or self.host}

    def test_write_update_remove(self) -> None:
        path = rr.write_active_marker(self.root, self._marker("R-1", 1))
        self.assertEqual(path, self.root / ".takt" / "runs" / "active" / "R-1.json")
        self.assertEqual(list(path.parent.glob("*.tmp")), [])
        updated = rr.update_active_marker(self.root, "R-1", cycles=3)
        self.assertEqual((updated or {})["cycles"], 3)
        self.assertEqual(json.loads(path.read_text())["cycles"], 3)
        self.assertIsNone(rr.update_active_marker(self.root, "missing", x=1))
        rr.remove_active_marker(self.root, "R-1")
        self.assertFalse(path.exists())
        rr.remove_active_marker(self.root, "R-1")

    def test_is_pid_alive(self) -> None:
        self.assertIsNone(rr.is_pid_alive("other-host", os.getpid()))
        self.assertTrue(rr.is_pid_alive(self.host, os.getpid()))
        with mock.patch("os.kill", side_effect=ProcessLookupError):
            self.assertFalse(rr.is_pid_alive(self.host, 99999))
        with mock.patch("os.kill", side_effect=PermissionError):
            self.assertTrue(rr.is_pid_alive(self.host, 1))

    def test_list_active_runs(self) -> None:
        rr.write_active_marker(self.root, self._marker("R-1", os.getpid()))
        rr.write_active_marker(self.root, self._marker("R-2", 1, "other-host"))
        (self.root / ".takt/runs/active/bad.json").write_text("{not json")
        runs = {r["run_id"]: r for r in rr.list_active_runs(self.root)}
        self.assertEqual(set(runs), {"R-1", "R-2"})
        self.assertTrue(runs["R-1"]["alive"])
        self.assertIsNone(runs["R-2"]["alive"])

    def test_reap_stale_runs(self) -> None:
        rr.write_active_marker(self.root, self._marker("R-dead", 4242))
        rr.write_active_marker(self.root, self._marker("R-live", os.getpid()))
        rr.write_active_marker(self.root, self._marker("R-foreign", 4243, "other-host"))
        bad = self.root / ".takt/runs/active/bad.json"
        bad.write_text("garbage")
        storage = FakeStorage(self.root)
        with mock.patch.object(rr, "is_pid_alive", side_effect=lambda h, p: None if h != self.host else p == os.getpid()):
            with self.assertLogs(rr.logger, level="WARNING"):
                reaped = rr.reap_stale_runs(storage, "R-new")
        self.assertEqual(reaped, ["R-dead"])
        self.assertEqual(len(storage.events), 1)
        etype, payload = storage.events[0]
        self.assertEqual(etype, "scheduler_run_finished")
        self.assertEqual(payload["run_id"], "R-dead")
        self.assertEqual(payload["exit_reason"], "crashed")
        self.assertEqual(payload["detected_by_run_id"], "R-new")
        self.assertIsNone(payload["error"])
        active = self.root / ".takt/runs/active"
        self.assertFalse((active / "R-dead.json").exists())
        self.assertFalse(bad.exists())
        self.assertTrue((active / "R-live.json").exists())
        self.assertTrue((active / "R-foreign.json").exists())

    def test_reap_skips_event_when_remove_lost_race(self) -> None:
        rr.write_active_marker(self.root, self._marker("R-dead", 4242))
        storage = FakeStorage(self.root)
        with mock.patch.object(rr, "is_pid_alive", return_value=False), \
                mock.patch("os.remove", side_effect=FileNotFoundError):
            reaped = rr.reap_stale_runs(storage, "R-new")
        self.assertEqual(reaped, [])
        self.assertEqual(storage.events, [])


if __name__ == "__main__":
    unittest.main()
