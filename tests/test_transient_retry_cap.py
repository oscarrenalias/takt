from __future__ import annotations

import io
import json
import sys
import tempfile
from argparse import Namespace
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from agent_takt.cli import command_retry  # noqa: E402
from agent_takt.config import SchedulerConfig, default_config, load_config  # noqa: E402
from agent_takt.console import ConsoleReporter  # noqa: E402
from agent_takt.gitutils import WorktreeManager  # noqa: E402
from agent_takt.models import BEAD_BLOCKED, BEAD_READY  # noqa: E402
from agent_takt.scheduler import Scheduler  # noqa: E402

from helpers import FakeRunner, OrchestratorTests  # noqa: E402


class TransientRetryCapTests(OrchestratorTests):
    def _events(self, name: str) -> list[dict]:
        path = self.storage.logs_dir / "events.jsonl"
        if not path.exists():
            return []
        recs = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        return [r for r in recs if r["event_type"] == name]

    def _blocked_bead(self):
        bead = self.storage.create_bead(title="Flaky", agent_type="developer", description="x")
        bead.status = BEAD_BLOCKED
        bead.block_reason = "Agent timed out"
        self.storage.save_bead(bead)
        return bead

    def test_config_default_and_load(self) -> None:
        self.assertEqual(5, SchedulerConfig().max_transient_retries)
        self.assertEqual(5, default_config().scheduler.max_transient_retries)
        (self.root / ".takt").mkdir(exist_ok=True)
        (self.root / ".takt" / "config.yaml").write_text(
            "common:\n  scheduler:\n    max_transient_retries: 2\n", encoding="utf-8"
        )
        self.assertEqual(2, load_config(self.root).scheduler.max_transient_retries)

    def test_requeued_exactly_cap_times_then_exhausted_once(self) -> None:
        scheduler = Scheduler(self.storage, FakeRunner(), WorktreeManager(self.root, self.storage.worktrees_dir))
        bead = self._blocked_bead()
        cap = scheduler.max_transient_retries
        for i in range(cap):
            scheduler._reevaluate_blocked(feature_root_id=None)
            b = self.storage.load_bead(bead.bead_id)
            self.assertEqual(BEAD_READY, b.status)
            self.assertEqual(i + 1, b.metadata["transient_retry_count"])
            b.status = BEAD_BLOCKED
            b.block_reason = "Agent timed out"
            self.storage.save_bead(b)
        for _ in range(3):
            scheduler._reevaluate_blocked(feature_root_id=None)
        b = self.storage.load_bead(bead.bead_id)
        self.assertEqual(BEAD_BLOCKED, b.status)
        self.assertIn(f"(transient retries exhausted: {cap}/{cap})", b.block_reason)
        self.assertEqual(1, len([h for h in b.execution_history if h.event == "transient_retries_exhausted"]))
        events = self._events("transient_retries_exhausted")
        self.assertEqual(1, len(events))
        self.assertEqual(cap, events[0]["payload"]["retries"])
        self.assertEqual("Agent timed out", events[0]["payload"]["last_reason"])
        self.assertEqual([], [x for x in self.storage.list_beads() if x.bead_id != bead.bead_id])

    def test_retry_command_resets_counter(self) -> None:
        bead = self._blocked_bead()
        bead.metadata["transient_retry_count"] = 5
        bead.metadata["transient_retries_exhausted"] = True
        self.storage.save_bead(bead)
        command_retry(Namespace(bead_id=bead.bead_id), self.storage, ConsoleReporter(stream=io.StringIO()))
        b = self.storage.load_bead(bead.bead_id)
        self.assertNotIn("transient_retry_count", b.metadata)
        self.assertNotIn("transient_retries_exhausted", b.metadata)

    def test_counter_reset_on_done(self) -> None:
        from agent_takt.models import AgentRunResult
        bead = self.storage.create_bead(title="Flaky", agent_type="developer", description="x")
        bead.metadata["transient_retry_count"] = 3
        self.storage.save_bead(bead)
        runner = FakeRunner()
        runner.results[bead.bead_id] = AgentRunResult(outcome="completed", summary="ok", verdict="approved")
        Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir)).run_once()
        b = self.storage.load_bead(bead.bead_id)
        self.assertEqual("done", b.status)
        self.assertNotIn("transient_retry_count", b.metadata)
