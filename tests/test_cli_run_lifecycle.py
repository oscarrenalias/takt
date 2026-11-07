"""Tests for scheduler run lifecycle events, run context, active markers and enriched bead events.

Scenarios map to specs/drafts/scheduler-run-lifecycle-events.md (capability: scheduler-run-events).
"""
from __future__ import annotations

import io
import json
import os
import signal
import socket
import subprocess
import sys
import textwrap
import threading
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_takt import run_registry as rr
from agent_takt.cli import command_run
from agent_takt.cli.commands.run import CliSchedulerReporter
from agent_takt.console import ConsoleReporter
from agent_takt.gitutils import WorktreeManager
from agent_takt.models import AgentRunResult, HandoffSummary
from agent_takt.scheduler import Scheduler, SchedulerResult
from agent_takt.storage import RepositoryStorage

RepositoryStorage._auto_commit = False

from helpers import FakeRunner, OrchestratorTests as _OrchestratorBase  # noqa: E402


def _dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


class _LifecycleBase(_OrchestratorBase):
    def _scheduler(self, runner=None) -> Scheduler:
        worktrees = WorktreeManager(self.root, self.storage.worktrees_dir)
        return Scheduler(self.storage, runner or FakeRunner(), worktrees)

    def _events(self, *types: str) -> list[dict]:
        path = self.storage.logs_dir / "events.jsonl"
        if not path.exists():
            return []
        records = [json.loads(line) for line in path.read_text().splitlines()]
        if types:
            records = [r for r in records if r["event_type"] in types]
        return records

    def _run(self, scheduler: Scheduler, *, max_cycles: int = 0, feature_root=None) -> tuple[int, str]:
        stream = io.StringIO()
        rc = command_run(
            Namespace(feature_root=feature_root, max_workers=1, max_cycles=max_cycles),
            scheduler,
            ConsoleReporter(stream=stream),
        )
        return rc, stream.getvalue()

    def _active_markers(self) -> list[Path]:
        d = self.root / ".takt" / "runs" / "active"
        return sorted(d.glob("*.json")) if d.is_dir() else []

    def _assert_one_pair(self) -> tuple[dict, dict]:
        started = self._events("scheduler_run_started")
        finished = self._events("scheduler_run_finished")
        self.assertEqual(1, len(started))
        self.assertEqual(1, len(finished))
        self.assertEqual(started[0]["payload"]["run_id"], finished[0]["payload"]["run_id"])
        return started[0]["payload"], finished[0]["payload"]


class RunLifecycleEventTests(_LifecycleBase):
    def test_run_reaches_quiescence(self) -> None:
        """Scenario: scheduler-run-events / Run lifecycle events / Run reaches quiescence"""
        scheduler = self._scheduler()
        with patch.object(
            scheduler, "run_once",
            side_effect=[SchedulerResult(started=["B-1"], completed=["B-1"]), SchedulerResult(started=[])],
        ):
            rc, _ = self._run(scheduler)
        self.assertEqual(0, rc)
        started, finished = self._assert_one_pair()
        self.assertTrue(started["run_id"].startswith("SR-"))
        self.assertEqual(os.getpid(), started["pid"])
        self.assertEqual(1, started["max_workers"])
        self.assertEqual("quiescence", finished["exit_reason"])
        self.assertIsNone(finished["error"])
        self.assertEqual(2, finished["cycles"])
        self.assertEqual(1, finished["started_count"])
        self.assertEqual(1, finished["completed_count"])
        self.assertIsInstance(finished["final_state"], dict)
        self.assertIsNotNone(finished["duration_seconds"])

    def test_run_hits_cycle_cap(self) -> None:
        """Scenario: scheduler-run-events / Run lifecycle events / Run hits the cycle cap"""
        scheduler = self._scheduler()
        with patch.object(scheduler, "run_once", return_value=SchedulerResult(started=["B-N"])):
            rc, _ = self._run(scheduler, max_cycles=3)
        self.assertEqual(0, rc)
        _, finished = self._assert_one_pair()
        self.assertEqual("max_cycles", finished["exit_reason"])
        self.assertEqual(3, finished["cycles"])

    def test_run_interrupted(self) -> None:
        """Scenario: scheduler-run-events / Run lifecycle events / Run is interrupted"""
        scheduler = self._scheduler()
        with patch.object(scheduler, "run_once", side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt):
                self._run(scheduler)
        _, finished = self._assert_one_pair()
        self.assertEqual("interrupted", finished["exit_reason"])
        self.assertEqual([], self._active_markers())

    def test_run_fails_with_exception(self) -> None:
        """Scenario: scheduler-run-events / Run lifecycle events / Run fails with an exception"""
        scheduler = self._scheduler()
        with patch.object(scheduler, "run_once", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                self._run(scheduler)
        _, finished = self._assert_one_pair()
        self.assertEqual("error", finished["exit_reason"])
        self.assertEqual("RuntimeError: boom", finished["error"])
        self.assertEqual([], self._active_markers())

    def test_run_receives_sigterm(self) -> None:
        """Scenario: scheduler-run-events / Run lifecycle events / Run receives SIGTERM"""
        scheduler = self._scheduler()
        before = signal.getsignal(signal.SIGTERM)

        def _kill_self(**_kwargs):
            os.kill(os.getpid(), signal.SIGTERM)
            return SchedulerResult(started=[])

        with patch.object(scheduler, "run_once", side_effect=_kill_self):
            with self.assertRaises(SystemExit) as ctx:
                self._run(scheduler)
        self.assertEqual(143, ctx.exception.code)
        _, finished = self._assert_one_pair()
        self.assertEqual("terminated", finished["exit_reason"])
        self.assertEqual([], self._active_markers())
        self.assertEqual(before, signal.getsignal(signal.SIGTERM))

    def test_sigterm_handler_not_installed_off_main_thread(self) -> None:
        """Handler install is skipped when command_run runs outside the main thread."""
        scheduler = self._scheduler()
        seen: dict = {}
        before = signal.getsignal(signal.SIGTERM)

        def _inspect(**_kwargs):
            seen["handler"] = signal.getsignal(signal.SIGTERM)
            return SchedulerResult(started=[])

        def _target() -> None:
            with patch.object(scheduler, "run_once", side_effect=_inspect):
                self._run(scheduler)

        thread = threading.Thread(target=_target)
        thread.start()
        thread.join()
        self.assertEqual(before, seen["handler"])
        self.assertEqual("quiescence", self._events("scheduler_run_finished")[0]["payload"]["exit_reason"])

    def test_unrelated_system_exit_is_error(self) -> None:
        """Scenario: scheduler-run-events / Run lifecycle events / Unrelated SystemExit is an error"""
        scheduler = self._scheduler()
        with patch.object(scheduler, "run_once", side_effect=SystemExit(2)):
            with self.assertRaises(SystemExit):
                self._run(scheduler)
        _, finished = self._assert_one_pair()
        self.assertEqual("error", finished["exit_reason"])
        self.assertIsNotNone(finished["error"])
        self.assertTrue(finished["error"].startswith("SystemExit"))

    def test_unresolvable_feature_root_starts_no_run(self) -> None:
        """Scenario: scheduler-run-events / Run lifecycle events / Unresolvable feature root starts no run"""
        scheduler = self._scheduler()
        rc, _ = self._run(scheduler, feature_root="B-doesnotexist")
        self.assertEqual(1, rc)
        self.assertEqual([], self._events("scheduler_run_started", "scheduler_run_finished"))
        self.assertEqual([], self._active_markers())

    def test_final_state_null_when_list_beads_raises(self) -> None:
        scheduler = self._scheduler()

        def _flaky(*_args, **_kwargs):
            raise RuntimeError("list failed")

        with patch.object(scheduler, "run_once", return_value=SchedulerResult(started=[])):
            with patch.object(self.storage, "list_beads", side_effect=_flaky):
                with self.assertRaises(RuntimeError):
                    self._run(scheduler)
        finished = self._events("scheduler_run_finished")[0]["payload"]
        self.assertIsNone(finished["final_state"])

    def test_stdout_json_summary_unchanged(self) -> None:
        scheduler = self._scheduler()
        with patch.object(scheduler, "run_once", return_value=SchedulerResult(started=[])):
            rc, out = self._run(scheduler)
        self.assertEqual(0, rc)
        lines = out.splitlines()
        idx = lines.index("{")
        data = json.loads("\n".join(lines[idx:]))
        self.assertEqual(
            {"started", "completed", "blocked", "correctives_created", "deferred_count", "final_state"},
            set(data),
        )


class RunContextTests(_LifecycleBase):
    def test_bead_and_finalize_events_carry_run_id(self) -> None:
        """Scenario: scheduler-run-events / Run context on all events / Bead and finalize events carry run_id"""
        bead = self.storage.create_bead(title="Work", agent_type="developer", description="do work")
        runner = FakeRunner(results={
            bead.bead_id: AgentRunResult(
                outcome="completed", summary="done", completed="x", remaining="",
                changed_files=[], touched_files=[],
            ),
        })
        scheduler = self._scheduler(runner)
        rc, _ = self._run(scheduler)
        self.assertEqual(0, rc)
        started, _ = self._assert_one_pair()
        run_id = started["run_id"]
        types = {r["event_type"] for r in self._events()}
        self.assertIn("bead_started", types)
        self.assertIn("bead_completed", types)
        self.assertIn("scheduler_cycle_started", types)
        for record in self._events("bead_started", "bead_completed", "worktree_ready",
                                   "scheduler_cycle_started", "scheduler_cycle_completed"):
            self.assertEqual(run_id, record["payload"].get("run_id"), record["event_type"])

    def test_events_outside_run_unchanged(self) -> None:
        """Scenario: scheduler-run-events / Run context on all events / Events outside a run are unchanged"""
        self.storage.record_event("merge_gate_failed", {"bead_id": "B-1"})
        scheduler = self._scheduler()
        with patch.object(scheduler, "run_once", return_value=SchedulerResult(started=[])):
            self._run(scheduler)
        # context is cleared after the run
        self.storage.record_event("after_run", {"a": 1})
        records = self._events()
        before = [r for r in records if r["event_type"] == "merge_gate_failed"][0]
        after = [r for r in records if r["event_type"] == "after_run"][0]
        self.assertEqual({"bead_id": "B-1"}, before["payload"])
        self.assertEqual({"a": 1}, after["payload"])
        self.assertEqual({"timestamp", "event_type", "payload"}, set(after))


class ActiveMarkerTests(_LifecycleBase):
    def test_marker_exists_during_and_removed_after_run(self) -> None:
        """Scenario: scheduler-run-events / Active run marker / Marker exists during and is removed after a run"""
        scheduler = self._scheduler()
        seen: list[dict] = []

        def _inspect(**_kwargs):
            seen.append({m.stem: json.loads(m.read_text()) for m in self._active_markers()})
            return SchedulerResult(started=["B-1"] if len(seen) < 2 else [])

        with patch.object(scheduler, "run_once", side_effect=_inspect):
            self._run(scheduler)
        self.assertEqual(2, len(seen))
        run_id = self._events("scheduler_run_started")[0]["payload"]["run_id"]
        for markers in seen:
            self.assertEqual({run_id}, set(markers))
            marker = markers[run_id]
            self.assertEqual(os.getpid(), marker["pid"])
            self.assertEqual(socket.gethostname(), marker["hostname"])
        self.assertEqual(1, seen[0][run_id]["cycle_index"])
        self.assertEqual(2, seen[1][run_id]["cycle_index"])
        self.assertEqual([], self._active_markers())

    def test_stale_marker_reaped_as_crashed_run(self) -> None:
        """Scenario: scheduler-run-events / Active run marker / Stale marker is reaped as a crashed run"""
        stale = {"run_id": "SR-deadbeef", "pid": _dead_pid(), "hostname": socket.gethostname()}
        rr.write_active_marker(self.root, stale)
        scheduler = self._scheduler()
        with patch.object(scheduler, "run_once", return_value=SchedulerResult(started=[])):
            self._run(scheduler)
        finished = self._events("scheduler_run_finished")
        by_id = {r["payload"]["run_id"]: r["payload"] for r in finished}
        self.assertIn("SR-deadbeef", by_id)
        crashed = by_id["SR-deadbeef"]
        self.assertEqual("crashed", crashed["exit_reason"])
        new_run_id = self._events("scheduler_run_started")[0]["payload"]["run_id"]
        self.assertEqual(new_run_id, crashed["detected_by_run_id"])
        self.assertIsNone(crashed["cycles"])
        self.assertEqual([], self._active_markers())

    def test_two_runs_reap_same_stale_marker(self) -> None:
        """Scenario: scheduler-run-events / Active run marker / Two runs reap the same stale marker"""
        rr.write_active_marker(
            self.root, {"run_id": "SR-stale001", "pid": _dead_pid(), "hostname": socket.gethostname()}
        )
        other = RepositoryStorage(self.root)
        real = rr.is_pid_alive
        state = {"nested": False}
        nested_result: list[list[str]] = []

        def _racing_alive(hostname, pid):
            # The competing reaper claims the marker between our read and our remove.
            if not state["nested"]:
                state["nested"] = True
                nested_result.append(rr.reap_stale_runs(other, "SR-other"))
            return real(hostname, pid)

        with patch.object(rr, "is_pid_alive", side_effect=_racing_alive):
            mine = rr.reap_stale_runs(self.storage, "SR-mine")
        self.assertEqual([["SR-stale001"]], nested_result)
        self.assertEqual([], mine)
        crashed = [r for r in self._events("scheduler_run_finished") if r["payload"]["run_id"] == "SR-stale001"]
        self.assertEqual(1, len(crashed))
        self.assertEqual([], self._active_markers())


class EnrichedBeadEventTests(_LifecycleBase):
    def _reporter(self) -> CliSchedulerReporter:
        return CliSchedulerReporter(ConsoleReporter(stream=io.StringIO()), self.storage, max_workers=1)

    def _bead(self, **meta):
        bead = self.storage.create_bead(title="Review", agent_type="review", description="d")
        bead.metadata = dict(meta)
        return bead

    def test_completed_review_bead_reports_verdict_and_cost(self) -> None:
        """Scenario: scheduler-run-events / Enriched bead events / Completed review bead reports verdict and cost"""
        reporter = self._reporter()
        bead = self._bead()
        bead.handoff_summary = HandoffSummary(verdict="approved")
        reporter.bead_started(bead)  # snapshot None: first run
        bead.metadata["telemetry"] = {"attempt": 1, "duration_ms": 12340, "cost_usd": 0.12345}
        reporter.bead_completed(bead, "ok", [])
        payload = self._events("bead_completed")[-1]["payload"]
        self.assertEqual("approved", payload["verdict"])
        self.assertEqual(0.12345, payload["cost_usd"])
        self.assertEqual(12.3, payload["duration_seconds"])
        self.assertEqual(1, payload["attempt"])
        self.assertEqual(bead.bead_id, payload["feature_root_id"])
        self.assertEqual(0.1235, reporter.run_cost_usd)

    def test_stale_telemetry_from_earlier_attempt_not_reported(self) -> None:
        """Scenario: scheduler-run-events / Enriched bead events / Stale telemetry from an earlier attempt is not reported"""
        reporter = self._reporter()
        bead = self._bead(telemetry={"attempt": 2, "duration_ms": 5000, "cost_usd": 1.0})
        reporter.bead_started(bead)  # snapshot attempt == 2; no new telemetry written
        reporter.bead_failed(bead, "no output")
        payload = self._events("bead_failed")[-1]["payload"]
        self.assertIsNone(payload["duration_seconds"])
        self.assertIsNone(payload["cost_usd"])
        self.assertIsNone(payload["attempt"])
        self.assertIsNone(reporter.run_cost_usd)

    def test_first_run_telemetry_reported_and_blocked_carries_fields(self) -> None:
        reporter = self._reporter()
        bead = self._bead()
        reporter.bead_started(bead)  # snapshot None
        bead.metadata["telemetry"] = {"attempt": 1, "duration_ms": 2000, "cost_usd": 0.5}
        reporter.bead_blocked(bead, "blocked")
        payload = self._events("bead_blocked")[-1]["payload"]
        self.assertEqual(2.0, payload["duration_seconds"])
        self.assertEqual(0.5, payload["cost_usd"])
        self.assertEqual(1, payload["attempt"])

    def test_empty_verdict_is_null_and_cost_none_without_costs(self) -> None:
        reporter = self._reporter()
        bead = self._bead()
        reporter.bead_started(bead)
        reporter.bead_completed(bead, "ok", [])
        payload = self._events("bead_completed")[-1]["payload"]
        self.assertIsNone(payload["verdict"])
        self.assertIsNone(reporter.run_cost_usd)


class EventWriteConcurrencyTests(_LifecycleBase):
    def test_multithreaded_record_event_lines_parse(self) -> None:
        """Every events.jsonl line parses as JSON when recorded from several threads."""
        threads_n, per_thread = 8, 100
        big = "x" * 2000

        def _work(tid: int) -> None:
            for i in range(per_thread):
                self.storage.record_event("t", {"tid": tid, "i": i, "pad": big})

        threads = [threading.Thread(target=_work, args=(t,)) for t in range(threads_n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        lines = (self.storage.logs_dir / "events.jsonl").read_text().splitlines()
        records = [json.loads(line) for line in lines]  # raises on any torn line
        self.assertEqual(threads_n * per_thread, len([r for r in records if r["event_type"] == "t"]))

    def test_two_process_append_yields_parseable_lines(self) -> None:
        """Two processes appending 200 events each yield 400 parseable lines."""
        script = textwrap.dedent(
            """
            import sys
            sys.path.insert(0, sys.argv[1])
            from pathlib import Path
            from agent_takt.storage import RepositoryStorage
            RepositoryStorage._auto_commit = False
            s = RepositoryStorage(Path(sys.argv[2]))
            for i in range(200):
                s.record_event("mp", {"proc": sys.argv[3], "i": i, "pad": "y" * 2000})
            """
        )
        procs = [
            subprocess.Popen([sys.executable, "-c", script, str(SRC_ROOT), str(self.root), name])
            for name in ("a", "b")
        ]
        for p in procs:
            self.assertEqual(0, p.wait(timeout=120))
        lines = (self.storage.logs_dir / "events.jsonl").read_text().splitlines()
        records = [json.loads(line) for line in lines]
        mp = [r for r in records if r["event_type"] == "mp"]
        self.assertEqual(400, len(mp))
        self.assertEqual(200, len([r for r in mp if r["payload"]["proc"] == "a"]))


if __name__ == "__main__":
    unittest.main()
