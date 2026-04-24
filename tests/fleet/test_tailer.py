from __future__ import annotations

import json
import queue
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_takt_fleet.tailer import (
    EVENTS_JSONL,
    TailedEvent,
    _emit_line,
    _parse_timestamp,
    _replay_window,
    start_tailing,
)


# ── Helpers ────────────────────────────────────────────────────────────────────


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


def _event_line(event: str = "created", summary: str = "Bead created", ts: datetime | None = None) -> str:
    if ts is None:
        ts = _utcnow()
    return json.dumps({
        "timestamp": ts.isoformat(),
        "event": event,
        "agent_type": "scheduler",
        "summary": summary,
        "details": {},
    })


def _drain_queue(q: "queue.Queue[TailedEvent | None]", timeout: float = 0.2) -> list[TailedEvent]:
    events: list[TailedEvent] = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            item = q.get(timeout=0.05)
            if item is None:
                break
            events.append(item)
        except queue.Empty:
            break
    return events


# ── _parse_timestamp ───────────────────────────────────────────────────────────


def test_parse_timestamp_valid() -> None:
    ts_str = "2026-04-24T12:00:00+00:00"
    parsed = {"timestamp": ts_str}
    result = _parse_timestamp(parsed)
    assert result is not None
    assert result.tzinfo is not None
    assert result.year == 2026


def test_parse_timestamp_missing_key() -> None:
    assert _parse_timestamp({"event": "created"}) is None


def test_parse_timestamp_none_parsed() -> None:
    assert _parse_timestamp(None) is None


def test_parse_timestamp_invalid_string() -> None:
    assert _parse_timestamp({"timestamp": "not-a-date"}) is None


def test_parse_timestamp_empty_string() -> None:
    assert _parse_timestamp({"timestamp": ""}) is None


# ── _emit_line ─────────────────────────────────────────────────────────────────


def test_emit_line_valid_json() -> None:
    q: queue.Queue[TailedEvent | None] = queue.Queue()
    line = _event_line("created", "Bead created")
    _emit_line("my-project", line, q)
    event = q.get_nowait()
    assert event is not None
    assert event.project_name == "my-project"
    assert event.parsed is not None
    assert event.parsed["event"] == "created"
    assert event.timestamp is not None


def test_emit_line_invalid_json() -> None:
    q: queue.Queue[TailedEvent | None] = queue.Queue()
    _emit_line("proj", "this is not json", q)
    event = q.get_nowait()
    assert event is not None
    assert event.parsed is None
    assert event.raw_line == "this is not json"
    assert event.timestamp is None


def test_emit_line_empty_string_not_emitted() -> None:
    q: queue.Queue[TailedEvent | None] = queue.Queue()
    _emit_line("proj", "", q)
    _emit_line("proj", "\n", q)
    assert q.empty()


def test_emit_line_strips_newline() -> None:
    q: queue.Queue[TailedEvent | None] = queue.Queue()
    _emit_line("proj", '{"event": "x"}\n', q)
    event = q.get_nowait()
    assert "\n" not in event.raw_line


# ── _replay_window ─────────────────────────────────────────────────────────────


def test_replay_window_emits_events_after_cutoff(tmp_path: Path) -> None:
    now = _utcnow()
    old = now - timedelta(hours=2)
    recent = now - timedelta(minutes=5)

    events_file = tmp_path / "events.jsonl"
    events_file.write_text(
        _event_line("old_event", "old", ts=old) + "\n"
        + _event_line("recent_event", "recent", ts=recent) + "\n"
    )

    q: queue.Queue[TailedEvent | None] = queue.Queue()
    stop = threading.Event()
    cutoff = now - timedelta(hours=1)

    with events_file.open("r") as f:
        _replay_window("proj", f, cutoff, q, stop)

    events = _drain_queue(q)
    assert len(events) == 1
    assert events[0].parsed is not None
    assert events[0].parsed["event"] == "recent_event"


def test_replay_window_all_events_within_window(tmp_path: Path) -> None:
    now = _utcnow()
    events_file = tmp_path / "events.jsonl"
    lines = [_event_line("e1", ts=now - timedelta(minutes=10)),
             _event_line("e2", ts=now - timedelta(minutes=5))]
    events_file.write_text("\n".join(lines) + "\n")

    q: queue.Queue[TailedEvent | None] = queue.Queue()
    stop = threading.Event()
    cutoff = now - timedelta(hours=1)

    with events_file.open("r") as f:
        _replay_window("proj", f, cutoff, q, stop)

    events = _drain_queue(q)
    assert len(events) == 2


def test_replay_window_warns_on_short_history(tmp_path: Path, capsys) -> None:
    now = _utcnow()
    # Only event is 30 minutes old, but cutoff is 2 hours ago
    events_file = tmp_path / "events.jsonl"
    events_file.write_text(_event_line("e", ts=now - timedelta(minutes=30)) + "\n")

    q: queue.Queue[TailedEvent | None] = queue.Queue()
    stop = threading.Event()
    cutoff = now - timedelta(hours=2)

    with events_file.open("r") as f:
        _replay_window("proj", f, cutoff, q, stop)

    captured = capsys.readouterr()
    assert "warning" in captured.err.lower()
    assert "proj" in captured.err


def test_replay_window_includes_unparseable_timestamp_lines(tmp_path: Path) -> None:
    events_file = tmp_path / "events.jsonl"
    events_file.write_text('{"event": "x", "timestamp": "not-a-date"}\n')

    q: queue.Queue[TailedEvent | None] = queue.Queue()
    stop = threading.Event()
    cutoff = _utcnow() - timedelta(hours=1)

    with events_file.open("r") as f:
        _replay_window("proj", f, cutoff, q, stop)

    events = _drain_queue(q)
    assert len(events) == 1  # included best-effort


def test_replay_window_respects_stop_event(tmp_path: Path) -> None:
    now = _utcnow()
    events_file = tmp_path / "events.jsonl"
    # Write 10 events, all in-window
    lines = [_event_line("e", ts=now - timedelta(seconds=i)) for i in range(10)]
    events_file.write_text("\n".join(lines) + "\n")

    q: queue.Queue[TailedEvent | None] = queue.Queue()
    stop = threading.Event()
    stop.set()  # already stopped
    cutoff = now - timedelta(hours=1)

    with events_file.open("r") as f:
        _replay_window("proj", f, cutoff, q, stop)

    # replay loop exits early; no events emitted after stop
    events = _drain_queue(q)
    assert len(events) == 0


# ── start_tailing ──────────────────────────────────────────────────────────────


def test_start_tailing_returns_queue_and_threads(tmp_path: Path) -> None:
    project_path = tmp_path / "proj1"
    project_path.mkdir()
    events_path = project_path / EVENTS_JSONL
    events_path.parent.mkdir(parents=True, exist_ok=True)
    events_path.write_text("")

    q, stop, threads = start_tailing([("proj1", project_path)], since=None)
    assert len(threads) == 1
    assert threads[0].is_alive()
    stop.set()
    threads[0].join(timeout=3)


def test_start_tailing_replays_since_window(tmp_path: Path) -> None:
    project_path = tmp_path / "proj1"
    project_path.mkdir()
    events_path = project_path / EVENTS_JSONL
    events_path.parent.mkdir(parents=True, exist_ok=True)

    now = _utcnow()
    recent_line = _event_line("recent", ts=now - timedelta(minutes=3))
    old_line = _event_line("old_event", ts=now - timedelta(hours=2))
    events_path.write_text(old_line + "\n" + recent_line + "\n")

    q, stop, threads = start_tailing([("proj1", project_path)], since="1h")

    # Give worker time to replay and block on live-tail
    collected: list[TailedEvent] = []
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        try:
            ev = q.get(timeout=0.1)
            if ev is None:
                break
            collected.append(ev)
        except queue.Empty:
            if collected:
                break

    stop.set()
    for t in threads:
        t.join(timeout=3)

    event_names = [e.parsed["event"] for e in collected if e.parsed]
    assert "recent" in event_names
    assert "old_event" not in event_names


def test_start_tailing_multiple_projects(tmp_path: Path) -> None:
    pairs: list[tuple[str, Path]] = []
    for name in ("alpha", "beta"):
        p = tmp_path / name
        p.mkdir()
        events_path = p / EVENTS_JSONL
        events_path.parent.mkdir(parents=True, exist_ok=True)
        events_path.write_text("")
        pairs.append((name, p))

    q, stop, threads = start_tailing(pairs, since=None)
    assert len(threads) == 2
    stop.set()
    for t in threads:
        t.join(timeout=3)


def test_start_tailing_waits_for_file(tmp_path: Path) -> None:
    project_path = tmp_path / "proj"
    project_path.mkdir()
    # Don't create the events file yet

    q, stop, threads = start_tailing([("proj", project_path)], since=None)
    assert threads[0].is_alive()

    # Create the file after worker has started
    events_path = project_path / EVENTS_JSONL
    events_path.parent.mkdir(parents=True, exist_ok=True)
    events_path.write_text("")

    stop.set()
    threads[0].join(timeout=5)
    # Worker should exit cleanly


def test_start_tailing_picks_up_live_events(tmp_path: Path) -> None:
    project_path = tmp_path / "proj"
    project_path.mkdir()
    events_path = project_path / EVENTS_JSONL
    events_path.parent.mkdir(parents=True, exist_ok=True)
    events_path.write_text("")

    q, stop, threads = start_tailing([("proj", project_path)], since=None)

    # Write a new event after the worker has positioned at EOF
    time.sleep(0.2)
    with events_path.open("a") as f:
        f.write(_event_line("live_event") + "\n")
        f.flush()

    collected: list[TailedEvent] = []
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        try:
            ev = q.get(timeout=0.2)
            if ev is not None:
                collected.append(ev)
                break
        except queue.Empty:
            continue

    stop.set()
    for t in threads:
        t.join(timeout=3)

    assert len(collected) == 1
    assert collected[0].project_name == "proj"
    assert collected[0].parsed is not None
    assert collected[0].parsed["event"] == "live_event"
