from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from datetime import datetime, timedelta, timezone

from agent_takt_fleet.web.live import clear_transcript_cache, locate_transcript, resolve_config_dir, last_ended_session, last_ended_sessions, live_sessions

NOW = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)


def _iso(delta: timedelta) -> str:
    return (NOW - delta).isoformat()


def start(bead="B-1", sid="s1", ago=timedelta(minutes=5), role="main"):
    return {
        "timestamp": _iso(ago),
        "event_type": "agent_session_started",
        "payload": {"bead_id": bead, "role": role, "session_id": sid, "started_at": _iso(ago)},
    }


def ev(etype, ago, **payload):
    return {"timestamp": _iso(ago), "event_type": etype, "payload": payload}


class LiveSessionsTests(unittest.TestCase):
    def test_start_without_end_is_live(self):
        out = live_sessions([start()], NOW)
        self.assertEqual([s["session_id"] for s in out], ["s1"])
        self.assertEqual(out[0]["bead_id"], "B-1")
        self.assertEqual(out[0]["role"], "main")

    def test_trace_captured_same_session_ends(self):
        events = [start(), ev("trace_captured", timedelta(minutes=1), session_id="s1", bead_id="B-1", seq=1)]
        self.assertEqual(live_sessions(events, NOW), [])

    def test_trace_captured_other_session_does_not_end(self):
        events = [start(), ev("trace_captured", timedelta(minutes=1), session_id="other", bead_id="B-1", seq=1)]
        self.assertEqual(len(live_sessions(events, NOW)), 1)

    def test_bead_end_events_end_session(self):
        for etype in ("bead_completed", "bead_blocked", "bead_failed"):
            with self.subTest(etype=etype):
                events = [start(), ev(etype, timedelta(minutes=1), bead_id="B-1")]
                self.assertEqual(live_sessions(events, NOW), [])

    def test_bead_end_for_other_bead_does_not_end(self):
        events = [start(), ev("bead_completed", timedelta(minutes=1), bead_id="B-2")]
        self.assertEqual(len(live_sessions(events, NOW)), 1)

    def test_earlier_end_event_does_not_end_later_session(self):
        events = [ev("bead_failed", timedelta(minutes=10), bead_id="B-1"), start(ago=timedelta(minutes=5))]
        self.assertEqual(len(live_sessions(events, NOW)), 1)

    def test_older_than_six_hours_not_live(self):
        self.assertEqual(live_sessions([start(ago=timedelta(hours=6, seconds=1))], NOW), [])
        self.assertEqual(live_sessions([start(ago=timedelta(hours=6))], NOW), [])
        self.assertEqual(len(live_sessions([start(ago=timedelta(hours=5, minutes=59))], NOW)), 1)

    def test_malformed_events_ignored(self):
        events = ["x", {"event_type": "agent_session_started", "payload": {}}, start()]
        self.assertEqual(len(live_sessions(events, NOW)), 1)


class LastEndedTests(unittest.TestCase):
    def test_ended_by_trace_has_reference(self):
        events = [start(), ev("trace_captured", timedelta(minutes=1), session_id="s1", bead_id="B-1", seq=3)]
        out = last_ended_session(events, "B-1")
        assert out is not None
        self.assertEqual(out["ended_at"], _iso(timedelta(minutes=1)))
        self.assertEqual(out["trace"], {"bead_id": "B-1", "seq": 3, "session_id": "s1"})
        self.assertEqual(out["end_reason"], "trace_captured")

    def test_ended_by_bead_event_has_no_trace(self):
        events = [start(), ev("bead_blocked", timedelta(minutes=1), bead_id="B-1")]
        out = last_ended_session(events, "B-1")
        assert out is not None
        self.assertIsNone(out["trace"])
        self.assertEqual(out["end_reason"], "bead_blocked")

    def test_most_recent_per_bead(self):
        events = [
            start(sid="a", ago=timedelta(minutes=30)),
            ev("trace_captured", timedelta(minutes=25), session_id="a", bead_id="B-1", seq=1),
            start(sid="b", ago=timedelta(minutes=20)),
            ev("trace_captured", timedelta(minutes=15), session_id="b", bead_id="B-1", seq=2),
            start(bead="B-2", sid="c", ago=timedelta(minutes=10)),
            ev("bead_completed", timedelta(minutes=5), bead_id="B-2"),
        ]
        out = last_ended_sessions(events)
        self.assertEqual(out["B-1"]["session_id"], "b")
        self.assertEqual(out["B-2"]["session_id"], "c")

    def test_unended_session_excluded(self):
        self.assertEqual(last_ended_sessions([start()]), {})
        self.assertIsNone(last_ended_session([start()], "B-1"))


if __name__ == "__main__":
    unittest.main()


SID = "123e4567-e89b-12d3-a456-426614174000"


class LocateTranscriptTests(unittest.TestCase):
    def setUp(self):
        clear_transcript_cache()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = Path(self.tmp.name) / "cfg"
        (self.cfg / "projects" / "proj").mkdir(parents=True)

    def test_found_and_cached(self):
        f = self.cfg / "projects" / "proj" / f"{SID}.jsonl"
        f.write_text("{}")
        res = locate_transcript(SID, self.cfg)
        self.assertTrue(res.found)
        self.assertEqual(res.path, f.resolve())
        with mock.patch.object(Path, "glob", side_effect=AssertionError("glob")):
            self.assertTrue(locate_transcript(SID, self.cfg).found)

    def test_non_uuid_rejected_without_fs_read(self):
        with mock.patch.object(Path, "glob", side_effect=AssertionError), mock.patch.object(
            Path, "resolve", side_effect=AssertionError
        ):
            for bad in ("abc", "../x", f"a/{SID}", "..\\" + SID, "", None, SID + "/..", "*"):
                self.assertEqual(locate_transcript(bad, self.cfg).status, "rejected", bad)

    def test_symlink_escape_rejected(self):
        outside = Path(self.tmp.name) / "outside.jsonl"
        outside.write_text("secret")
        (self.cfg / "projects" / "proj" / f"{SID}.jsonl").symlink_to(outside)
        res = locate_transcript(SID, self.cfg)
        self.assertEqual(res.status, "rejected")
        self.assertIsNone(res.path)

    def test_missing_is_waiting_and_creates_nothing(self):
        res = locate_transcript(SID, self.cfg)
        self.assertEqual(res.status, "waiting")
        empty = Path(self.tmp.name) / "nocfg"
        self.assertEqual(locate_transcript(SID, empty).status, "waiting")
        self.assertFalse(empty.exists())

    def test_config_dir_order(self):
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": "/env/dir"}):
            self.assertEqual(resolve_config_dir("/override"), Path("/override"))
            self.assertEqual(resolve_config_dir(), Path("/env/dir"))
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
            self.assertEqual(resolve_config_dir(), Path.home() / ".claude")

    def test_env_override_used_for_lookup(self):
        f = self.cfg / "projects" / "proj" / f"{SID}.jsonl"
        f.write_text("{}")
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.cfg)}):
            self.assertTrue(locate_transcript(SID).found)


# --- TranscriptFollower (spec: Live transcript following) -------------------------

import json  # noqa: E402

from agent_takt_fleet.web import live as _live  # noqa: E402


def _line(kind, i, text="x"):
    return json.dumps({
        "type": kind, "timestamp": f"2026-11-22T12:00:{i:02d}+00:00", "sessionId": "s",
        "message": {"role": kind, "content": [{"type": "text", "text": text}]},
    })


def test_follower_incremental_events_read_only_appended_bytes(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text(_line("assistant", 1) + "\n")
    f = _live.TranscriptFollower(p)
    s1 = f.refresh()
    assert s1["status"] == "ok" and s1["summary"]["assistant_messages"] == 1
    first_read = f.bytes_read
    assert first_read == p.stat().st_size
    f.refresh()  # nothing appended: no further reads
    assert f.bytes_read == first_read
    with p.open("a") as fh:
        fh.write(_line("assistant", 2) + "\n")
    s2 = f.refresh()
    assert s2["summary"]["assistant_messages"] == 2
    assert f.bytes_read == p.stat().st_size  # only the appended bytes were read
    assert s2["offset"] == p.stat().st_size


def test_follower_partial_line_held_until_newline(tmp_path):
    p = tmp_path / "t.jsonl"
    full = _line("assistant", 1)
    p.write_text(full[:20])
    f = _live.TranscriptFollower(p)
    s = f.refresh()
    assert s["events"] == [] and s["summary"]["event_count"] == 0
    with p.open("a") as fh:
        fh.write(full[20:])  # still no newline
    assert f.refresh()["events"] == []
    with p.open("a") as fh:
        fh.write("\n")
    assert f.refresh()["summary"]["assistant_messages"] == 1


def test_follower_missing_file_is_waiting(tmp_path):
    f = _live.TranscriptFollower(tmp_path / "nope.jsonl")
    assert f.refresh()["status"] == "waiting"
    (tmp_path / "nope.jsonl").write_text(_line("assistant", 1) + "\n")
    assert f.refresh()["status"] == "ok"


def test_follower_too_large_stops_reading_for_good(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text(_line("assistant", 1) + "\n")
    f = _live.TranscriptFollower(p, max_bytes=10)
    assert f.refresh()["status"] == "too_large"
    assert f.bytes_read == 0
    p.write_text("")  # even if it shrinks
    assert f.refresh()["status"] == "too_large" and f.bytes_read == 0


def test_follower_truncation_resets(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text(_line("assistant", 1) + "\n" + _line("assistant", 2) + "\n")
    f = _live.TranscriptFollower(p)
    assert f.refresh()["summary"]["assistant_messages"] == 2
    p.write_text(_line("assistant", 3) + "\n")
    s = f.refresh()
    assert s["summary"]["assistant_messages"] == 1 and s["offset"] == p.stat().st_size


def test_follower_normaliser_failure_gives_error_note(tmp_path, monkeypatch):
    p = tmp_path / "t.jsonl"
    p.write_text(_line("assistant", 1) + "\n")

    def boom(lines):
        raise RuntimeError("bad")

    monkeypatch.setattr(_live, "normalize_claude_transcript", boom)
    s = _live.TranscriptFollower(p).refresh()
    assert s["events"] == [] and "normalisation failed" in s["error"]


def test_locate_transcript_unsafe_session_id_rejected(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("no filesystem access expected")

    monkeypatch.setattr(Path, "glob", boom)
    for sid in ("../../etc/passwd", "a/b", "not-a-uuid", ""):
        res = locate_transcript(tmp_path, sid)
        assert not res.found and res.status == "rejected"
