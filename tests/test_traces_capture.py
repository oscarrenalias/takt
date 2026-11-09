"""Tests for trace capture, runner session ids, scheduler integration and planner capture/linking."""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import uuid
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

FIXTURE = REPO_ROOT / "tests" / "fixtures" / "traces" / "claude_session.jsonl"

from agent_takt.cli import command_plan
from agent_takt.config import BackendConfig, default_config
from agent_takt.console import ConsoleReporter
from agent_takt.gitutils import WorktreeManager
from agent_takt.models import AgentRunResult, PlanChild, PlanProposal
from agent_takt.planner import PlanningService
from agent_takt.runner import ClaudeCodeAgentRunner
from agent_takt.scheduler import Scheduler
from agent_takt.storage import RepositoryStorage
from agent_takt.traces import TraceSession, attach_trace_sessions
from agent_takt.traces.capture import capture_planner_traces, capture_traces
from agent_takt.traces.store import TraceStore

RepositoryStorage._auto_commit = False

from helpers import FakeRunner, OrchestratorTests  # noqa: E402

SID = "11111111-1111-4111-8111-111111111111"
UUID4_RE_VERSION = 4


def _is_uuid4(value: str) -> bool:
    try:
        return uuid.UUID(value).version == UUID4_RE_VERSION
    except ValueError:
        return False


class ConfigDirMixin:
    """Provides a temp CLAUDE_CONFIG_DIR and a helper to drop transcripts into it."""

    def setup_config_dir(self) -> None:
        self._cfg_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._cfg_tmp.cleanup)  # type: ignore[attr-defined]
        self.config_dir = Path(self._cfg_tmp.name)
        patcher = patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.config_dir)})
        patcher.start()
        self.addCleanup(patcher.stop)  # type: ignore[attr-defined]

    def put_transcript(self, session_id: str, source: Path = FIXTURE) -> Path:
        dest = self.config_dir / "projects" / "proj" / f"{session_id}.jsonl"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)
        return dest


def _config(capture: bool = True, max_bytes: int = 52428800) -> SimpleNamespace:
    return SimpleNamespace(common=SimpleNamespace(capture_traces=capture, trace_max_bytes=max_bytes))


# ---------------------------------------------------------------------------
# capture_traces
# ---------------------------------------------------------------------------
class CaptureTests(ConfigDirMixin, OrchestratorTests):
    def setUp(self) -> None:
        super().setUp()
        self.setup_config_dir()
        self.store = TraceStore(self.root)
        self.bead = self.storage.create_bead(title="Cap", agent_type="developer", description="d")

    def _capture(self, sessions, config=None, backend_name=None, **kw):
        warnings: list[str] = []
        seqs = capture_traces(
            self.root, config or _config(), self.bead, sessions, "completed", None, backend_name,
            warnings=warnings, storage=self.storage, **kw,
        )
        return seqs, warnings

    def _events_log(self) -> list[dict]:
        path = self.storage.logs_dir / "events.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    def test_successful_run_is_captured(self) -> None:
        # Scenario: agent-traces / Session transcripts captured per agent run / Successful run is captured
        src = self.put_transcript(SID)
        seqs, warnings = self._capture([TraceSession("claude", SID, started_at="a", finished_at="b")])
        self.assertEqual([1], seqs)
        self.assertEqual([], warnings)
        meta = self.store.load_meta(self.bead.bead_id, 1)
        self.assertEqual("captured", meta.status)
        self.assertEqual(2, meta.tool_calls)  # StructuredOutput excluded
        self.assertEqual(2, meta.malformed_lines)
        self.assertEqual(src.read_bytes(), self.store.raw_path(self.bead.bead_id, 1).read_bytes())
        self.assertTrue(self.store.events_path(self.bead.bead_id, 1).exists())
        events = [e for e in self._events_log() if e["event_type"] == "trace_captured"]
        self.assertEqual(["captured"], [e["payload"]["status"] for e in events])

    def test_missing_transcript_is_recorded_not_fatal(self) -> None:
        # Scenario: agent-traces / Session transcripts captured per agent run / Missing transcript is recorded, not fatal
        seqs, warnings = self._capture([TraceSession("claude", SID)])
        self.assertEqual([1], seqs)
        meta = self.store.load_meta(self.bead.bead_id, 1)
        self.assertEqual("missing", meta.status)
        self.assertFalse(self.store.raw_path(self.bead.bead_id, 1).exists())
        self.assertEqual([], warnings)

    def test_capture_disabled(self) -> None:
        # Scenario: agent-traces / Session transcripts captured per agent run / Capture disabled
        self.put_transcript(SID)
        seqs, _ = self._capture([TraceSession("claude", SID)], config=_config(capture=False))
        meta = self.store.load_meta(self.bead.bead_id, seqs[0])
        self.assertEqual("disabled", meta.status)
        self.assertFalse(self.store.raw_path(self.bead.bead_id, 1).exists())
        self.assertFalse(self.store.events_path(self.bead.bead_id, 1).exists())

    def test_oversized_transcript(self) -> None:
        # Scenario: agent-traces / Session transcripts captured per agent run / Oversized transcript
        src = self.put_transcript(SID)
        seqs, _ = self._capture([TraceSession("claude", SID)], config=_config(max_bytes=10))
        meta = self.store.load_meta(self.bead.bead_id, seqs[0])
        self.assertEqual("too_large", meta.status)
        self.assertEqual(src.stat().st_size, meta.raw_bytes)
        self.assertFalse(self.store.raw_path(self.bead.bead_id, 1).exists())
        self.assertTrue(self.store.load_events(self.bead.bead_id, 1))
        self.assertEqual(2, meta.tool_calls)

    def test_codex_empty_sessions_gives_one_unsupported_record(self) -> None:
        # Scenario: agent-traces / Session transcripts captured per agent run / Codex runs are recorded as unsupported
        seqs, _ = self._capture([], backend_name="codex")
        self.assertEqual([1], seqs)
        meta = self.store.load_meta(self.bead.bead_id, 1)
        self.assertEqual(("unsupported", "codex", None), (meta.status, meta.backend, meta.session_id))

    def test_fake_runner_without_sessions_gives_none(self) -> None:
        seqs, warnings = self._capture([], backend_name=None)
        self.assertEqual(([], []), (seqs, warnings))
        self.assertEqual([], self.store.list_records(self.bead.bead_id))

    def test_claude_dir_from_env_argument_beats_os_environ(self) -> None:
        other = tempfile.TemporaryDirectory()
        self.addCleanup(other.cleanup)
        dest = Path(other.name) / "projects" / "p" / f"{SID}.jsonl"
        dest.parent.mkdir(parents=True)
        shutil.copyfile(FIXTURE, dest)
        seqs, _ = self._capture([TraceSession("claude", SID)], env={"CLAUDE_CONFIG_DIR": other.name})
        self.assertEqual("captured", self.store.load_meta(self.bead.bead_id, seqs[0]).status)
        # and without the env argument the (empty) os.environ dir is used
        seqs, _ = self._capture([TraceSession("claude", SID)])
        self.assertEqual("missing", self.store.load_meta(self.bead.bead_id, seqs[0]).status)

    def test_trace_captured_event_for_each_status(self) -> None:
        self.put_transcript(SID)
        self._capture([TraceSession("claude", SID)])
        self._capture([TraceSession("claude", "22222222-2222-4222-8222-222222222222")])
        self._capture([], backend_name="codex")
        self._capture([TraceSession("claude", SID)], config=_config(capture=False))
        statuses = [e["payload"]["status"] for e in self._events_log() if e["event_type"] == "trace_captured"]
        self.assertEqual(["captured", "missing", "unsupported", "disabled"], statuses)

    def test_store_error_gives_warning_and_error_record(self) -> None:
        self.put_transcript(SID)
        with patch.object(TraceStore, "write_events", side_effect=OSError("disk full")):
            seqs, warnings = self._capture([TraceSession("claude", SID)])
        self.assertEqual(1, len(seqs))
        self.assertTrue(any("disk full" in w for w in warnings))
        meta = self.store.load_meta(self.bead.bead_id, seqs[0])
        self.assertEqual("error", meta.status)
        self.assertIn("disk full", meta.error or "")

    def test_new_record_error_does_not_raise(self) -> None:
        with patch.object(TraceStore, "new_record", side_effect=OSError("nope")):
            seqs, warnings = self._capture([TraceSession("claude", SID)])
        self.assertEqual([], seqs)
        self.assertGreaterEqual(len(warnings), 1)

    def test_planner_variant_stored_under_planner_dir(self) -> None:
        self.put_transcript(SID)
        warnings: list[str] = []
        seqs = capture_planner_traces(
            self.root, _config(), [TraceSession("claude", SID, role="main")], "completed",
            spec_id="spec-1", warnings=warnings, storage=self.storage,
        )
        self.assertEqual([1], seqs)
        meta = self.store.load_meta(None, 1)
        self.assertEqual(("planner", "planner", "spec-1", None), (meta.role, meta.agent_type, meta.spec_id, meta.bead_id))
        self.assertEqual("captured", meta.status)
        self.assertTrue((self.root / ".takt" / "traces" / "_planner" / "1.raw.jsonl").exists())


# ---------------------------------------------------------------------------
# Runner: --session-id and exception propagation
# ---------------------------------------------------------------------------
def _runner() -> ClaudeCodeAgentRunner:
    backend = BackendConfig(binary="claude", skills_dir=".claude", flags=[], timeout_seconds=30)
    return ClaudeCodeAgentRunner(config=default_config(), backend=backend)


def _proc(stdout: str = "", returncode: int = 0, stderr: str = "") -> MagicMock:
    return MagicMock(returncode=returncode, stdout=stdout, stderr=stderr)


def _session_id_of(cmd: list[str]) -> str:
    return cmd[cmd.index("--session-id") + 1]


class RunnerSessionTests(unittest.TestCase):
    def _call(self, runner: ClaudeCodeAgentRunner, sessions: list):
        return runner._exec_json_with_response(
            "p", schema={"type": "object", "properties": {}}, workdir=Path("/tmp"), agent_type="developer",
            sessions=sessions,
        )

    @patch("agent_takt.runner.subprocess.run")
    def test_command_line_carries_session_id(self, mock_run) -> None:
        # Scenario: agent-traces / Session id assigned before invocation / Command line carries the session id
        mock_run.side_effect = [
            _proc(json.dumps({"result": "just prose", "is_error": False})),  # no structured output
            _proc(json.dumps({"structured_output": {"ok": 1}})),             # retry succeeds
        ]
        sessions: list[TraceSession] = []
        payload, _ = self._call(_runner(), sessions)
        self.assertEqual({"ok": 1}, payload)
        cmds = [c.args[0] for c in mock_run.call_args_list]
        self.assertEqual(2, len(cmds))
        for cmd in cmds:
            self.assertTrue(_is_uuid4(_session_id_of(cmd)))
            self.assertNotIn("--resume", cmd)
            self.assertNotIn("--continue", cmd)
        self.assertNotEqual(_session_id_of(cmds[0]), _session_id_of(cmds[1]))
        self.assertEqual(["main", "structured_output_retry"], [s.role for s in sessions])
        self.assertEqual([_session_id_of(c) for c in cmds], [s.session_id for s in sessions])
        for s in sessions:
            self.assertTrue(s.started_at and s.finished_at)

    @patch("agent_takt.runner.subprocess.run")
    def test_success_has_single_main_session(self, mock_run) -> None:
        mock_run.return_value = _proc(json.dumps({"structured_output": {"ok": 1}}))
        sessions: list[TraceSession] = []
        self._call(_runner(), sessions)
        self.assertEqual(["main"], [s.role for s in sessions])
        self.assertTrue(sessions[0].started_at and sessions[0].finished_at)

    @patch("agent_takt.runner.subprocess.run")
    def test_session_id_survives_a_raise_timeout(self, mock_run) -> None:
        # Scenario: agent-traces / Session id assigned before invocation / Session id survives a raise
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="claude", timeout=30)
        with self.assertRaises(RuntimeError) as ctx:
            self._call(_runner(), [])
        self.assertEqual("Agent timed out after 30 seconds", str(ctx.exception))
        sessions = ctx.exception.trace_sessions  # type: ignore[attr-defined]
        self.assertEqual(["main"], [s.role for s in sessions])
        self.assertTrue(sessions[0].finished_at)
        self.assertEqual(_session_id_of(mock_run.call_args.args[0]), sessions[0].session_id)

    @patch("agent_takt.runner.subprocess.run")
    def test_session_id_survives_a_raise_nonzero_nonjson_and_no_output(self, mock_run) -> None:
        for proc in (
            _proc("", returncode=2, stderr="bad"),
            _proc("not json"),
            _proc(json.dumps({"result": "", "is_error": True})),
        ):
            mock_run.side_effect = None
            mock_run.return_value = proc
            with self.assertRaises(RuntimeError) as ctx:
                self._call(_runner(), [])
            self.assertEqual(1, len(ctx.exception.trace_sessions))  # type: ignore[attr-defined]

    @patch("agent_takt.runner.subprocess.run")
    def test_retry_raising_carries_both_sessions(self, mock_run) -> None:
        mock_run.side_effect = [
            _proc(json.dumps({"result": "prose", "is_error": False})),
            subprocess.TimeoutExpired(cmd="claude", timeout=5),
        ]
        with self.assertRaises(RuntimeError) as ctx:
            self._call(_runner(), [])
        roles = [s.role for s in ctx.exception.trace_sessions]  # type: ignore[attr-defined]
        self.assertEqual(["main", "structured_output_retry"], roles)

    @patch("agent_takt.runner.subprocess.run")
    def test_plain_exception_gets_empty_attribute_default_in_scheduler(self, mock_run) -> None:
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="claude", timeout=30)
        with self.assertRaises(RuntimeError) as ctx:
            self._call(_runner(), [])
        self.assertEqual(1, len(ctx.exception.trace_sessions))  # type: ignore[attr-defined]


class RunnerConcurrencyTests(OrchestratorTests):
    @patch("agent_takt.runner.subprocess.run")
    def test_concurrent_run_bead_calls_do_not_share_sessions(self, mock_run) -> None:
        # Scenario: agent-traces / Session transcripts captured per agent run / Concurrent beads keep their own sessions
        payload = {
            "outcome": "completed", "summary": "d", "completed": "", "remaining": "", "risks": "",
            "verdict": "approved", "findings_count": 0, "requires_followup": False,
            "expected_files": [], "expected_globs": [], "touched_files": [], "changed_files": [],
            "updated_docs": [], "next_action": "", "next_agent": "", "block_reason": "",
            "conflict_risks": "", "new_beads": [],
        }
        barrier = threading.Barrier(2, timeout=10)

        def fake_run(cmd, **kwargs):
            barrier.wait()
            return _proc(json.dumps({"structured_output": payload, "session_id": _session_id_of(cmd)}))

        mock_run.side_effect = fake_run
        beads = [
            self.storage.create_bead(title=f"B{i}", agent_type="developer", description="d") for i in range(2)
        ]
        runner = _runner()
        results: dict[str, AgentRunResult] = {}

        def work(bead):
            results[bead.bead_id] = runner.run_bead(bead, workdir=self.root, context_paths=[])

        threads = [threading.Thread(target=work, args=(b,)) for b in beads]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        ids = [[s.session_id for s in results[b.bead_id].trace_sessions] for b in beads]
        self.assertEqual([1, 1], [len(i) for i in ids])
        self.assertNotEqual(ids[0][0], ids[1][0])


# ---------------------------------------------------------------------------
# Scheduler finalize integration
# ---------------------------------------------------------------------------
class _CodexFakeRunner(FakeRunner):
    backend_name = "codex"


def _telemetry() -> dict:
    return {"source": "provider", "cost_usd": 0.1, "duration_ms": 5, "session_id": SID,
            "prompt_text": "p", "response_text": "r"}


class SchedulerTraceIntegrationTests(ConfigDirMixin, OrchestratorTests):
    def setUp(self) -> None:
        super().setUp()
        self.setup_config_dir()
        self.store = TraceStore(self.root)

    def _run(self, runner) -> None:
        Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir)).run_once()

    def _bead(self, **kw):
        return self.storage.create_bead(title="T", agent_type=kw.pop("agent_type", "developer"), description="d", **kw)

    def _result(self, sessions, **kw) -> AgentRunResult:
        return AgentRunResult(
            outcome=kw.pop("outcome", "completed"), summary="s", verdict="approved",
            trace_sessions=sessions, **kw,
        )

    def test_completed_bead_gets_trace_refs_in_telemetry_artifact(self) -> None:
        # Scenario: agent-traces / Session transcripts captured per agent run / Telemetry artifact references its traces
        self.put_transcript(SID)
        bead = self._bead()
        self._run(FakeRunner(results={bead.bead_id: self._result([TraceSession("claude", SID)], telemetry=_telemetry())}))
        artifact = json.loads((self.storage.telemetry_dir / bead.bead_id / "1.json").read_text())
        self.assertEqual([1], artifact["trace_refs"])
        self.assertEqual(1, artifact["telemetry_version"])
        meta = self.store.load_meta(bead.bead_id, 1)
        self.assertEqual(("captured", 1), (meta.status, meta.telemetry_attempt))
        self.assertEqual("completed", meta.outcome)

    def test_successful_run_end_to_end_captured(self) -> None:
        self.put_transcript(SID)
        bead = self._bead()
        self._run(FakeRunner(results={bead.bead_id: self._result([TraceSession("claude", SID)])}))
        self.assertEqual("done", self.storage.load_bead(bead.bead_id).status)
        self.assertEqual("captured", self.store.list_records(bead.bead_id)[0].status)

    def test_blocked_and_failed_beads_still_capture(self) -> None:
        # Scenario: agent-traces / Session transcripts captured per agent run / Timed-out run is still captured
        sid2 = "22222222-2222-4222-8222-222222222222"
        self.put_transcript(SID)
        self.put_transcript(sid2)
        blocked = self._bead()

        class RaisingRunner(FakeRunner):
            def run_bead(self, bead, **kw):
                if bead.bead_id == failed_id:
                    raise attach_trace_sessions(RuntimeError("Agent timed out after 30 seconds"),
                                                [TraceSession("claude", sid2, finished_at="x")])
                return super().run_bead(bead, **kw)

        failed = self._bead()
        failed_id = failed.bead_id
        runner = RaisingRunner(results={blocked.bead_id: self._result(
            [TraceSession("claude", SID)], outcome="blocked", block_reason="needs input")})
        self._run(runner)
        self.assertEqual("blocked", self.store.list_records(blocked.bead_id)[0].outcome)
        rec = self.store.list_records(failed.bead_id)
        self.assertEqual(1, len(rec))
        self.assertEqual(("captured", "failed", sid2), (rec[0].status, rec[0].outcome, rec[0].session_id))
        failed_events = [r.event for r in self.storage.load_bead(failed.bead_id).execution_history]
        self.assertIn("failed", failed_events)

    def test_plain_exception_gives_no_sessions_and_unchanged_block_reason(self) -> None:
        bead = self._bead()

        class Boom(FakeRunner):
            def run_bead(self, bead, **kw):
                raise RuntimeError("kaboom")

        self._run(Boom())
        stored = self.storage.load_bead(bead.bead_id)
        self.assertIn("kaboom", stored.block_reason)
        self.assertEqual([], self.store.list_records(bead.bead_id))

    def test_capture_oserror_leaves_outcome_and_adds_warning(self) -> None:
        # Scenario: agent-traces / Session transcripts captured per agent run / Capture exception does not change the outcome
        bead = self._bead()
        runner = FakeRunner(results={bead.bead_id: self._result([TraceSession("claude", SID)], telemetry=_telemetry())})
        with patch("agent_takt.traces.capture.capture_traces", side_effect=OSError("disk gone")):
            self._run(runner)
        stored = self.storage.load_bead(bead.bead_id)
        self.assertEqual("done", stored.status)
        self.assertIn("telemetry", stored.metadata)
        warnings = [r for r in stored.execution_history if r.event == "trace_capture_warning"]
        self.assertEqual(1, len(warnings))
        self.assertIn("disk gone", warnings[0].summary)
        artifact = json.loads((self.storage.telemetry_dir / bead.bead_id / "1.json").read_text())
        self.assertNotIn("trace_refs", artifact)

    def test_capture_warnings_become_history_entry(self) -> None:
        bead = self._bead()
        runner = FakeRunner(results={bead.bead_id: self._result([TraceSession("claude", SID)])})
        with patch.object(TraceStore, "new_record", side_effect=OSError("ro fs")):
            self._run(runner)
        stored = self.storage.load_bead(bead.bead_id)
        self.assertEqual("done", stored.status)
        self.assertTrue([r for r in stored.execution_history if r.event == "trace_capture_warning"])

    def test_codex_backend_without_sessions_records_unsupported(self) -> None:
        # Scenario: agent-traces / Session transcripts captured per agent run / Codex runs are recorded as unsupported
        bead = self._bead()
        self._run(_CodexFakeRunner(results={bead.bead_id: self._result([])}))
        records = self.store.list_records(bead.bead_id)
        self.assertEqual(["unsupported"], [r.status for r in records])
        self.assertIsNone(records[0].session_id)

    def test_no_telemetry_captures_without_artifact_rewrite(self) -> None:
        self.put_transcript(SID)
        bead = self._bead()
        with patch.object(RepositoryStorage, "add_trace_refs_to_telemetry_artifact") as rewrite:
            self._run(FakeRunner(results={bead.bead_id: self._result([TraceSession("claude", SID)])}))
        rewrite.assert_not_called()
        meta = self.store.list_records(bead.bead_id)[0]
        self.assertEqual("captured", meta.status)
        self.assertIsNone(meta.telemetry_attempt)

    def test_two_beads_keep_their_own_sessions(self) -> None:
        # Scenario: agent-traces / Session transcripts captured per agent run / Concurrent beads keep their own sessions
        sid2 = "22222222-2222-4222-8222-222222222222"
        self.put_transcript(SID)
        self.put_transcript(sid2)
        a, b = self._bead(), self._bead()
        runner = FakeRunner(results={
            a.bead_id: self._result([TraceSession("claude", SID)]),
            b.bead_id: self._result([TraceSession("claude", sid2)]),
        })
        Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir)).run_once(max_workers=2)
        self.assertEqual(SID, self.store.list_records(a.bead_id)[0].session_id)
        self.assertEqual(sid2, self.store.list_records(b.bead_id)[0].session_id)

    def test_structured_output_retry_gets_its_own_trace(self) -> None:
        # Scenario: agent-traces / Session transcripts captured per agent run / Structured-output retry gets its own trace
        sid2 = "22222222-2222-4222-8222-222222222222"
        self.put_transcript(SID)
        self.put_transcript(sid2)
        bead = self._bead()
        sessions = [TraceSession("claude", SID, role="main"), TraceSession("claude", sid2, role="structured_output_retry")]
        self._run(FakeRunner(results={bead.bead_id: self._result(sessions, telemetry=_telemetry())}))
        records = self.store.list_records(bead.bead_id)
        self.assertEqual([("main", 1), ("structured_output_retry", 2)], [(r.role, r.seq) for r in records])
        artifact = json.loads((self.storage.telemetry_dir / bead.bead_id / "1.json").read_text())
        self.assertEqual([1, 2], artifact["trace_refs"])

    def test_finalize_receives_backend_name(self) -> None:
        bead = self._bead()
        runner = FakeRunner(results={bead.bead_id: self._result([])})
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))
        with patch("agent_takt.scheduler.execution.BeadFinalizer.finalize", autospec=True) as fin:
            scheduler.run_once()
        self.assertIsNone(fin.call_args.kwargs.get("backend_name"))
        bead2 = self._bead()
        runner2 = _CodexFakeRunner(results={bead2.bead_id: self._result([])})
        scheduler2 = Scheduler(self.storage, runner2, WorktreeManager(self.root, self.storage.worktrees_dir))
        with patch("agent_takt.scheduler.execution.BeadFinalizer.finalize", autospec=True) as fin2:
            scheduler2.run_once()
        self.assertEqual("codex", fin2.call_args.kwargs.get("backend_name"))


class StorageTelemetryRefsTests(OrchestratorTests):
    def _write(self, **kw) -> Path:
        return self.storage.write_telemetry_artifact(
            bead_id="B-x", agent_type="developer", attempt=1, started_at="a", finished_at="b",
            outcome="completed", prompt_text=None, response_text=None, parsed_result=None,
            metrics={"k": 1}, error=None, **kw,
        )

    def test_write_without_and_with_trace_refs(self) -> None:
        path = self._write()
        self.assertNotIn("trace_refs", json.loads(path.read_text()))
        path = self._write(trace_refs=[1, 2])
        data = json.loads(path.read_text())
        self.assertEqual([1, 2], data["trace_refs"])
        self.assertEqual(1, data["telemetry_version"])

    def test_add_trace_refs_helper(self) -> None:
        path = self._write()
        self.storage.add_trace_refs_to_telemetry_artifact("B-x", 1, [3])
        data = json.loads(path.read_text())
        self.assertEqual(([3], {"k": 1}, "completed"), (data["trace_refs"], data["metrics"], data["outcome"]))
        self.assertEqual([], list(path.parent.glob("*.tmp")))
        with self.assertRaises(FileNotFoundError):
            self.storage.add_trace_refs_to_telemetry_artifact("B-x", 9, [1])


# ---------------------------------------------------------------------------
# Planner capture and linking
# ---------------------------------------------------------------------------
def _proposal(sessions=None) -> PlanProposal:
    return PlanProposal(
        epic_title="Epic", epic_description="d", linked_docs=[],
        feature=PlanChild(title="Feature root", agent_type="planner", description="r", acceptance_criteria=["w"],
                          children=[PlanChild(title="Impl", agent_type="developer", description="b",
                                              acceptance_criteria=["w"])]),
        trace_sessions=list(sessions or []),
    )


class _PlanRunner(FakeRunner):
    def __init__(self, proposal=None, raises=None):
        super().__init__(proposal=proposal)
        self.raises = raises

    def propose_plan(self, spec_text, *, root=None, spec_id=None):
        if self.raises:
            raise self.raises
        return self.proposal_value


class RunnerPlannerTests(unittest.TestCase):
    @patch("agent_takt.runner.subprocess.run")
    def test_propose_plan_session_and_failures(self, mock_run) -> None:
        payload = {
            "epic_title": "E", "epic_description": "d", "linked_docs": [],
            "feature": {"title": "F", "agent_type": "planner", "description": "d", "acceptance_criteria": [],
                        "children": []},
        }
        mock_run.return_value = _proc(json.dumps({"structured_output": payload}))
        runner = _runner()
        proposal = runner.propose_plan("spec")
        self.assertTrue(_is_uuid4(_session_id_of(mock_run.call_args.args[0])))
        self.assertEqual(["planner"], [s.role for s in proposal.trace_sessions])
        self.assertEqual([], PlanProposal("e", "d", [], None).trace_sessions)  # type: ignore[arg-type]

        mock_run.return_value = _proc("", returncode=1, stderr="nope")
        with self.assertRaises(RuntimeError) as ctx:
            runner.propose_plan("spec")
        self.assertEqual("nope", str(ctx.exception))
        self.assertEqual(["planner"], [s.role for s in ctx.exception.trace_sessions])  # type: ignore[attr-defined]

        mock_run.side_effect = subprocess.TimeoutExpired(cmd="claude", timeout=30)
        with self.assertRaises(RuntimeError) as ctx:
            runner.propose_plan("spec")
        self.assertEqual(["planner"], [s.role for s in ctx.exception.trace_sessions])  # type: ignore[attr-defined]

        mock_run.side_effect = None
        mock_run.return_value = _proc(json.dumps({"structured_output": {"epic_title": "E"}}))
        with self.assertRaises(KeyError) as kctx:
            runner.propose_plan("spec")
        self.assertEqual(["planner"], [s.role for s in kctx.exception.trace_sessions])  # type: ignore[attr-defined]


class PlannerTraceTests(ConfigDirMixin, OrchestratorTests):
    def setUp(self) -> None:
        super().setUp()
        self.setup_config_dir()
        self.store = TraceStore(self.root)
        self.spec = self.root / "spec.md"
        self.spec.write_text("---\nid: spec-abc12345\n---\nSpec\n", encoding="utf-8")
        self.console = ConsoleReporter(stream=io.StringIO())

    def _plan(self, runner, **kw):
        planner = PlanningService(self.storage, runner)
        ns = dict(spec_file=str(self.spec), write=False, output=None, from_file=None)
        ns.update(kw)
        return command_plan(Namespace(**ns), planner, self.console)

    def _sessions(self, sid=SID):
        return [TraceSession("claude", sid, role="planner", started_at="a", finished_at="b")]

    def test_record_exists_for_dry_run_output_write_and_failure(self) -> None:
        self.put_transcript(SID)
        self.assertEqual(0, self._plan(_PlanRunner(_proposal(self._sessions()))))
        self.assertEqual(0, self._plan(_PlanRunner(_proposal(self._sessions())), output=str(self.root / "p.json")))
        self.assertEqual(0, self._plan(_PlanRunner(_proposal(self._sessions())), write=True))
        records = self.store.list_planner_records()
        self.assertEqual(3, len(records))
        self.assertTrue(all(r.role == "planner" and r.outcome == "completed" for r in records))
        self.assertTrue(all(r.spec_id == "spec-abc12345" for r in records))
        exc = attach_trace_sessions(RuntimeError("planner died"), self._sessions())
        with self.assertRaises(RuntimeError):
            self._plan(_PlanRunner(raises=exc))
        last = self.store.list_planner_records()[-1]
        self.assertEqual(("failed", "spec-abc12345"), (last.outcome, last.spec_id))

    def test_planner_trace_is_linked_on_persist(self) -> None:
        # Scenario: agent-traces / Session transcripts captured per agent run / Planner trace is linked on persist
        self.put_transcript(SID)
        self.assertEqual(0, self._plan(_PlanRunner(_proposal(self._sessions())), write=True))
        record = self.store.list_planner_records()[0]
        feature_root = next(b.bead_id for b in self.storage.list_beads() if b.agent_type == "planner"
                            and b.feature_root_id == b.bead_id)
        self.assertEqual(feature_root, record.feature_root_id)

    def test_dry_run_leaves_record_unlinked(self) -> None:
        self.assertEqual(0, self._plan(_PlanRunner(_proposal(self._sessions()))))
        self.assertIsNone(self.store.list_planner_records()[0].feature_root_id)

    def test_staged_plan_links_the_exact_planner_session(self) -> None:
        # Scenario: agent-traces / Session transcripts captured per agent run / Staged plan links the exact planner session
        sid2 = "33333333-3333-4333-8333-333333333333"
        out = self.root / "plan.json"
        self._plan(_PlanRunner(_proposal(self._sessions(SID))), output=str(out))
        self._plan(_PlanRunner(_proposal(self._sessions(sid2))), output=str(self.root / "other.json"))
        data = json.loads(out.read_text())
        self.assertEqual(SID, data["planner_session_id"])
        self.assertNotIn("trace_sessions", data)
        planner = PlanningService(self.storage, FakeRunner())
        code = command_plan(Namespace(spec_file=None, write=False, output=None, from_file=str(out)),
                            planner, self.console)
        self.assertEqual(0, code)
        by_sid = {r.session_id: r for r in self.store.list_planner_records()}
        self.assertIsNotNone(by_sid[SID].feature_root_id)
        self.assertIsNone(by_sid[sid2].feature_root_id)

    def test_from_file_with_missing_or_unknown_session_id_does_nothing(self) -> None:
        self._plan(_PlanRunner(_proposal(self._sessions())), output=str(self.root / "p.json"))
        base = json.loads((self.root / "p.json").read_text())
        for value in (None, "no-such-session"):
            data = dict(base)
            if value is None:
                data.pop("planner_session_id", None)
            else:
                data["planner_session_id"] = value
            path = self.root / "p2.json"
            path.write_text(json.dumps(data))
            code = command_plan(Namespace(spec_file=None, write=False, output=None, from_file=str(path)),
                                PlanningService(self.storage, FakeRunner()), self.console)
            self.assertEqual(0, code)
        self.assertIsNone(self.store.list_planner_records()[0].feature_root_id)

    def test_failing_capture_leaves_exit_code_zero(self) -> None:
        with patch("agent_takt.traces.capture.capture_planner_traces", side_effect=OSError("x")):
            code = self._plan(_PlanRunner(_proposal(self._sessions())), write=True)
        self.assertEqual(0, code)


if __name__ == "__main__":
    unittest.main()
