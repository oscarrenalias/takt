from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from agent_takt_fleet.web.live import last_ended_session, last_ended_sessions, live_sessions

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
