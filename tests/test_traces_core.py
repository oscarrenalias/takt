"""Tests for trace models, config keys, the transcript normaliser and TraceStore."""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

FIXTURE = REPO_ROOT / "tests" / "fixtures" / "traces" / "claude_session.jsonl"

from agent_takt.config import default_config, load_config
from agent_takt.models import AgentRunResult
from agent_takt.traces import (
    TraceRecordMeta,
    TraceSession,
    attach_trace_sessions,
)
from agent_takt.traces.normalize import count_tool_calls, normalize_claude_transcript
from agent_takt.traces.store import PLANNER_DIR, TraceStore


def _events(kind: str, events: list[dict]) -> list[dict]:
    return [e for e in events if e["kind"] == kind]


class ModelTests(unittest.TestCase):
    def test_session_round_trip_ignores_unknown_keys(self) -> None:
        s = TraceSession(backend="claude", session_id="x", started_at="t0")
        data = s.to_dict()
        data["bogus"] = 1
        self.assertEqual(s, TraceSession.from_dict(data))

    def test_meta_round_trip_ignores_unknown_keys(self) -> None:
        m = TraceRecordMeta(bead_id="B-1", seq=2, backend="claude", tool_calls=3)
        data = m.to_dict()
        self.assertEqual("trace_version", next(iter(data)))
        data["bogus"] = 1
        self.assertEqual(m, TraceRecordMeta.from_dict(data))

    def test_agent_run_result_trace_sessions_fresh_list(self) -> None:
        a, b = AgentRunResult(outcome="completed", summary="x"), AgentRunResult(outcome="completed", summary="x")
        self.assertEqual([], a.trace_sessions)
        a.trace_sessions.append(TraceSession(backend="claude"))
        self.assertEqual([], b.trace_sessions)

    def test_attach_trace_sessions_preserves_type_and_message(self) -> None:
        exc = attach_trace_sessions(RuntimeError("boom"), [TraceSession(backend="claude")])
        self.assertIsInstance(exc, RuntimeError)
        self.assertEqual("boom", str(exc))
        self.assertEqual(1, len(getattr(exc, "trace_sessions")))


class ConfigTests(unittest.TestCase):
    def test_defaults(self) -> None:
        common = default_config().common
        self.assertTrue(common.capture_traces)
        self.assertEqual(52428800, common.trace_max_bytes)

    def test_load_config_defaults_and_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".takt").mkdir()
            (root / ".takt" / "config.yaml").write_text("common:\n  test_command: x\n", encoding="utf-8")
            common = load_config(root).common
            self.assertTrue(common.capture_traces)
            self.assertEqual(52428800, common.trace_max_bytes)
            (root / ".takt" / "config.yaml").write_text(
                "common:\n  capture_traces: false\n  trace_max_bytes: 100\n", encoding="utf-8"
            )
            common = load_config(root).common
            self.assertFalse(common.capture_traces)
            self.assertEqual(100, common.trace_max_bytes)


class NormalizerTests(unittest.TestCase):
    def setUp(self) -> None:
        with FIXTURE.open("rb") as handle:
            self.result = normalize_claude_transcript(handle)
        self.events = self.result.events

    def test_duplicate_tool_use_entries_are_collapsed(self) -> None:
        # Scenario: agent-traces / Backend-neutral normalised events / Duplicate tool_use entries are collapsed
        calls = _events("tool_call", self.events)
        self.assertEqual(["toolu_1", "toolu_2", "toolu_3"], [c["call_id"] for c in calls])
        self.assertEqual({"command": "ls"}, calls[0]["input"])  # first input wins

    def test_split_message_usage_counted_once(self) -> None:
        # Scenario: agent-traces / Backend-neutral normalised events / Split message usage counted once
        usage = _events("usage", self.events)
        ids = [u["message_id"] for u in usage]
        self.assertEqual(1, ids.count("msg_1"))
        first = usage[0]
        self.assertEqual((10, 5, 3, 2), (
            first["input_tokens"], first["output_tokens"], first["cache_read_tokens"], first["cache_creation_tokens"],
        ))

    def test_errors_and_durations_are_recorded(self) -> None:
        # Scenario: agent-traces / Backend-neutral normalised events / Errors and durations are recorded
        calls = {c["call_id"]: c for c in _events("tool_call", self.events)}
        self.assertEqual(2000, calls["toolu_1"]["duration_ms"])
        self.assertFalse(calls["toolu_1"]["is_error"])
        self.assertEqual("a.txt\nb.txt", calls["toolu_1"]["result_excerpt"])
        self.assertEqual(1500, calls["toolu_2"]["duration_ms"])
        self.assertTrue(calls["toolu_2"]["is_error"])
        self.assertEqual("boom\nbang", calls["toolu_2"]["result_excerpt"])  # list content joined
        self.assertIsNone(calls["toolu_3"]["duration_ms"])

    def test_unknown_and_malformed_lines_are_skipped(self) -> None:
        # Scenario: agent-traces / Backend-neutral normalised events / Unknown and malformed lines are skipped
        self.assertEqual(2, self.result.malformed_lines)  # bad JSON + non-dict JSON
        kinds = {e["kind"] for e in self.events}
        self.assertEqual({"session", "assistant_text", "tool_call", "usage"}, kinds)
        session = self.events[0]
        self.assertEqual("session", session["kind"])
        self.assertEqual("/work/wt", session["cwd"])
        self.assertEqual("claude-x", session["model"])

    def test_count_tool_calls_excludes_structured_output(self) -> None:
        self.assertEqual(3, len(_events("tool_call", self.events)))
        self.assertEqual(2, count_tool_calls(self.events))

    def test_bytes_input_and_blank_lines(self) -> None:
        line = json.dumps({"type": "assistant", "sessionId": "s", "message": {"id": "m", "content": "hi", "usage": {}}})
        result = normalize_claude_transcript([line.encode(), b"", b"   \n"])
        self.assertEqual(0, result.malformed_lines)
        self.assertEqual({"session", "usage", "assistant_text"}, {e["kind"] for e in result.events})

    def test_unknown_tool_result_ignored_and_is_error_defaults_false(self) -> None:
        lines = [
            json.dumps({"type": "assistant", "timestamp": "2026-01-01T00:00:00Z", "message": {"content": [
                {"type": "tool_use", "id": "a", "name": "Read", "input": {}}]}}),
            json.dumps({"type": "user", "timestamp": "2026-01-01T00:00:01Z", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "zzz", "content": "x", "is_error": True},
                {"type": "tool_result", "tool_use_id": "a", "content": "ok"}]}}),
        ]
        calls = _events("tool_call", normalize_claude_transcript(lines).events)
        self.assertEqual(1, len(calls))
        self.assertFalse(calls[0]["is_error"])

    def test_excerpt_limits(self) -> None:
        big = "x" * 2000
        lines = [
            json.dumps({"type": "assistant", "timestamp": "2026-01-01T00:00:00Z", "message": {"content": [
                {"type": "text", "text": big},
                {"type": "tool_use", "id": "a", "name": "Bash", "input": {}}]}}),
            json.dumps({"type": "user", "timestamp": "2026-01-01T00:00:01Z", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "a", "content": big}]}}),
        ]
        events = normalize_claude_transcript(lines).events
        text = _events("assistant_text", events)[0]
        call = _events("tool_call", events)[0]
        self.assertEqual(500, len(text["text_excerpt"]))
        self.assertEqual(2000, text["text_chars"])
        self.assertEqual(1000, len(call["result_excerpt"]))
        self.assertEqual(2000, call["result_chars"])

    def test_naive_vs_aware_timestamps_give_no_duration(self) -> None:
        lines = [
            json.dumps({"type": "assistant", "timestamp": "2026-01-01T00:00:00", "message": {"content": [
                {"type": "tool_use", "id": "a", "name": "Bash", "input": {}}]}}),
            json.dumps({"type": "user", "timestamp": "2026-01-01T00:00:01Z", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "a", "content": "ok"}]}}),
        ]
        call = _events("tool_call", normalize_claude_transcript(lines).events)[0]
        self.assertIsNone(call["duration_ms"])


class TraceStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.store = TraceStore(self.root)

    def test_seq_starts_at_one_per_directory(self) -> None:
        self.assertEqual(1, self.store.new_record("B-1", backend="claude").seq)
        self.assertEqual(2, self.store.new_record("B-1", backend="claude").seq)
        self.assertEqual(1, self.store.new_record("B-2", backend="claude").seq)
        self.assertEqual(1, self.store.new_record(None, backend="claude").seq)
        self.assertTrue((self.root / ".takt" / "traces" / PLANNER_DIR / "1.meta.json").exists())

    def test_caller_supplied_seq_and_bead_id_ignored(self) -> None:
        meta = self.store.new_record("B-1", seq=99, backend="claude")
        self.assertEqual((1, "B-1"), (meta.seq, meta.bead_id))

    def test_concurrent_new_record_unique_seqs(self) -> None:
        seqs: list[tuple[str, int]] = []
        lock = threading.Lock()

        def work(bead: str) -> None:
            for _ in range(10):
                m = self.store.new_record(bead, backend="claude")
                with lock:
                    seqs.append((bead, m.seq))

        threads = [threading.Thread(target=work, args=(b,)) for b in ("B-1", "B-1", "B-2", "B-2")]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        for bead in ("B-1", "B-2"):
            got = sorted(s for b, s in seqs if b == bead)
            self.assertEqual(list(range(1, 21)), got)
        self.assertEqual([], list((self.root / ".takt" / "traces").rglob(".tmp-*.part")))

    def test_list_sorted_and_find_record(self) -> None:
        for i in range(3):
            self.store.new_record("B-1", backend="claude", session_id=f"s{i}")
        self.assertEqual([1, 2, 3], [r.seq for r in self.store.list_records("B-1")])
        found = self.store.find_record("B-1", "s1")
        self.assertIsNotNone(found)
        self.assertEqual(2, found.seq if found else None)
        self.assertIsNone(self.store.find_record("B-1", "nope"))
        self.assertEqual([], self.store.list_records("B-none"))

    def test_write_raw_is_byte_identical_and_events_round_trip(self) -> None:
        meta = self.store.new_record("B-1", backend="claude")
        dest = self.store.write_raw("B-1", meta.seq, FIXTURE)
        self.assertEqual(FIXTURE.read_bytes(), dest.read_bytes())
        events = [{"kind": "usage", "n": 1}, {"kind": "tool_call", "tool": "Bash"}]
        self.store.write_events("B-1", meta.seq, events)
        self.assertEqual(events, self.store.load_events("B-1", meta.seq))

    def test_load_events_missing_and_malformed(self) -> None:
        self.assertEqual([], self.store.load_events("B-1", 1))
        meta = self.store.new_record("B-1", backend="claude")
        self.store.events_path("B-1", meta.seq).write_text('{"kind":"a"}\nnot json\n[1]\n\n{"kind":"b"}\n', encoding="utf-8")
        self.assertEqual([{"kind": "a"}, {"kind": "b"}], self.store.load_events("B-1", meta.seq))

    def test_update_meta_rejects_unknown_and_protected_fields(self) -> None:
        meta = self.store.new_record("B-1", backend="claude")
        with self.assertRaises(TypeError):
            self.store.update_meta("B-1", meta.seq, nope=1)
        with self.assertRaises(TypeError):
            self.store.update_meta("B-1", meta.seq, **{"bead_id": "X"})
        with self.assertRaises(TypeError):
            self.store.update_meta("B-1", meta.seq, **{"seq": 5})
        updated = self.store.update_meta("B-1", meta.seq, status="missing", tool_calls=4)
        self.assertEqual("missing", self.store.load_meta("B-1", meta.seq).status)
        self.assertEqual(4, updated.tool_calls)

    def test_delete_bead_traces_leaves_planner(self) -> None:
        self.store.new_record("B-1", backend="claude")
        self.store.new_record(None, backend="claude")
        self.store.delete_bead_traces("B-1")
        self.assertFalse(self.store.bead_dir("B-1").exists())
        self.assertTrue(self.store.bead_dir(None).exists())
        self.store.delete_bead_traces(PLANNER_DIR)
        self.assertTrue(self.store.bead_dir(None).exists())

    def test_invalid_bead_ids_raise(self) -> None:
        for bad in ("", ".", "..", "a/b", "a\\b"):
            with self.assertRaises(ValueError):
                self.store.new_record(bad, backend="claude")

    def test_no_tmp_files_left_behind(self) -> None:
        meta = self.store.new_record("B-1", backend="claude")
        self.store.write_raw("B-1", meta.seq, FIXTURE)
        self.store.write_events("B-1", meta.seq, [{"kind": "x"}])
        self.store.update_meta("B-1", meta.seq, status="captured")
        self.assertEqual([], list((self.root / ".takt" / "traces").rglob(".tmp-*.part")))


if __name__ == "__main__":
    unittest.main()
