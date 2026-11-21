"""agent_session_started is announced via events.jsonl while the session is still running."""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_takt.gitutils import WorktreeManager
from agent_takt.models import BEAD_DONE, AgentRunResult
from agent_takt.runner import ClaudeCodeAgentRunner, CodexAgentRunner
from agent_takt.scheduler import Scheduler
from agent_takt.storage import RepositoryStorage

RepositoryStorage._auto_commit = False

from helpers import OrchestratorTests  # noqa: E402

_REAL_RUN = subprocess.run
_OK_PAYLOAD = {"outcome": "completed", "summary": "done"}


def _session_id_from(cmd: list[str]) -> str:
    return cmd[cmd.index("--session-id") + 1]


class SessionStartedTests(OrchestratorTests):
    def _make_bead(self):
        epic = self.storage.create_bead(
            title="Epic", agent_type="planner", description="e",
            status=BEAD_DONE, bead_type="epic",
        )
        feature = self.storage.create_bead(
            title="Feature", agent_type="planner", description="f",
            status=BEAD_DONE, bead_type="feature", parent_id=epic.bead_id,
        )
        return self.storage.create_bead(
            title="Work", agent_type="developer", description="work",
            parent_id=feature.bead_id,
        )

    def _events(self) -> list[dict]:
        path = self.storage.logs_dir / "events.jsonl"
        if not path.exists():
            return []
        return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]

    def _started(self, bead_id: str | None = None) -> list[dict]:
        return [
            e for e in self._events()
            if e["event_type"] == "agent_session_started"
            and (bead_id is None or e["payload"]["bead_id"] == bead_id)
        ]

    def _run(self, runner) -> None:
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))
        scheduler.run_once()

    def _claude_runner_patch(self, responses: list[dict], seen: list):
        """Patch subprocess.run for the claude binary only; git etc. run for real."""
        runner = ClaudeCodeAgentRunner()
        binary = runner.backend.binary
        queue = list(responses)

        def fake_run(cmd, *args, **kwargs):
            if not (isinstance(cmd, list) and cmd and cmd[0] == binary):
                return _REAL_RUN(cmd, *args, **kwargs)
            # Snapshot what events.jsonl holds *during* the subprocess call.
            seen.append((_session_id_from(cmd), [
                e for e in self._events() if e["event_type"] == "agent_session_started"
            ]))
            reply = queue.pop(0) if queue else {"structured_output": _OK_PAYLOAD}
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(reply), stderr="")

        return runner, patch("agent_takt.runner.subprocess.run", side_effect=fake_run)

    def test_event_visible_while_session_running(self) -> None:
        bead = self._make_bead()
        seen: list = []
        runner, patcher = self._claude_runner_patch([{"structured_output": _OK_PAYLOAD}], seen)
        with patcher:
            self._run(runner)
        session_id, events_during_call = seen[0]
        self.assertEqual(1, len(events_during_call))  # first call: only this session announced
        payload = events_during_call[0]["payload"]
        self.assertEqual(bead.bead_id, payload["bead_id"])
        self.assertEqual("main", payload["role"])
        self.assertEqual(session_id, payload["session_id"])
        self.assertEqual("claude", payload["backend"])
        self.assertEqual("developer", payload["agent_type"])
        self.assertEqual(bead.feature_root_id, payload["feature_root_id"])
        self.assertTrue(payload["started_at"])

    def test_structured_output_retry_gets_own_announcement(self) -> None:
        bead = self._make_bead()
        seen: list = []
        runner, patcher = self._claude_runner_patch(
            [
                {"result": "I did the work, no JSON here", "is_error": False},
                {"structured_output": _OK_PAYLOAD},
            ],
            seen,
        )
        with patcher:
            self._run(runner)
        started = self._started(bead.bead_id)
        self.assertEqual(["main", "structured_output_retry"], [e["payload"]["role"] for e in started])
        self.assertEqual([s[0] for s in seen[:2]], [e["payload"]["session_id"] for e in started])
        self.assertEqual(2, len({e["payload"]["session_id"] for e in started}))
        # The retry announcement was already visible during the retry subprocess call.
        self.assertEqual(2, len(seen[1][1]))

    def test_codex_runs_are_not_announced(self) -> None:
        self._make_bead()
        runner = CodexAgentRunner()
        with patch.object(CodexAgentRunner, "_exec_json", return_value=dict(_OK_PAYLOAD)):
            self._run(runner)
        self.assertEqual([], self._started())

    def test_record_event_failure_does_not_change_outcome(self) -> None:
        bead = self._make_bead()
        seen: list = []
        runner, patcher = self._claude_runner_patch([{"structured_output": _OK_PAYLOAD}], seen)
        real_record = self.storage.record_event

        def flaky(event_type, payload):
            if event_type == "agent_session_started":
                raise OSError("disk full")
            return real_record(event_type, payload)

        with patcher, patch.object(self.storage, "record_event", side_effect=flaky):
            with self.assertLogs("agent_takt.scheduler.execution", level="WARNING") as logs:
                self._run(runner)
        self.assertTrue(any("agent_session_started" in m for m in logs.output))
        self.assertGreaterEqual(len(seen), 1)  # subprocess still ran
        self.assertEqual(BEAD_DONE, self.storage.load_bead(bead.bead_id).status)
        self.assertEqual([], self._started())


if __name__ == "__main__":
    unittest.main()
