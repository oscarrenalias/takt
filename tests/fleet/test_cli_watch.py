from __future__ import annotations

import queue
import sys
import threading
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_takt_fleet.cli.commands.watch import command_watch
from agent_takt_fleet.models import Project
from agent_takt_fleet.tailer import TailedEvent


# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_project(name: str, tmp_path: Path) -> Project:
    d = tmp_path / name
    d.mkdir(exist_ok=True)
    return Project(name=name, path=d, tags=())


def _watch_args(**kwargs) -> Namespace:
    defaults = {"tag": [], "project": [], "since": None}
    defaults.update(kwargs)
    return Namespace(**defaults)


def _make_tailed_event(
    project_name: str = "proj",
    event: str = "created",
    summary: str = "Bead created",
) -> TailedEvent:
    from datetime import datetime, timezone
    return TailedEvent(
        project_name=project_name,
        raw_line=f'{{"event":"{event}","summary":"{summary}"}}',
        parsed={"event": event, "summary": summary},
        timestamp=datetime.now(tz=timezone.utc),
    )


def _build_mock_start_tailing(events: list[TailedEvent]) -> callable:
    """Return a mock start_tailing that pre-populates the queue and exits."""
    def _mock(project_pairs, since=None, events_file=None):
        q: queue.Queue[TailedEvent | None] = queue.Queue()
        stop = threading.Event()
        for ev in events:
            q.put(ev)
        # Sentinel per project
        for _ in project_pairs:
            q.put(None)
        return q, stop, [threading.Thread(target=lambda: None, daemon=True)]

    return _mock


# ── command_watch: error cases ─────────────────────────────────────────────────


def test_watch_empty_registry(capsys) -> None:
    with patch("agent_takt_fleet.cli.commands.watch.load_registry", return_value=[]):
        rc = command_watch(_watch_args())
    assert rc == 1
    assert "No projects match" in capsys.readouterr().err


def test_watch_registry_error(capsys) -> None:
    from agent_takt_fleet.registry import RegistryError
    with patch(
        "agent_takt_fleet.cli.commands.watch.load_registry",
        side_effect=RegistryError("bad registry"),
    ):
        rc = command_watch(_watch_args())
    assert rc == 1
    assert "error" in capsys.readouterr().err.lower()


def test_watch_no_matching_projects(tmp_path: Path, capsys) -> None:
    projects = [_make_project("api", tmp_path)]
    with patch("agent_takt_fleet.cli.commands.watch.load_registry", return_value=projects):
        rc = command_watch(_watch_args(project=["nonexistent"]))
    assert rc == 1
    assert "No projects match" in capsys.readouterr().err


# ── command_watch: output ──────────────────────────────────────────────────────


def test_watch_prints_project_prefixed_events(tmp_path: Path, capsys) -> None:
    projects = [_make_project("my-proj", tmp_path)]
    events = [_make_tailed_event("my-proj", "bead_started", "Worker started")]

    with (
        patch("agent_takt_fleet.cli.commands.watch.load_registry", return_value=projects),
        patch(
            "agent_takt_fleet.cli.commands.watch.start_tailing",
            side_effect=_build_mock_start_tailing(events),
        ),
    ):
        rc = command_watch(_watch_args())

    assert rc == 0
    out = capsys.readouterr().out
    assert "[my-proj]" in out
    assert "bead_started" in out


def test_watch_prints_multiple_events(tmp_path: Path, capsys) -> None:
    projects = [_make_project("svc", tmp_path)]
    events = [
        _make_tailed_event("svc", "created", "Bead created"),
        _make_tailed_event("svc", "started", "Worker started"),
    ]

    with (
        patch("agent_takt_fleet.cli.commands.watch.load_registry", return_value=projects),
        patch(
            "agent_takt_fleet.cli.commands.watch.start_tailing",
            side_effect=_build_mock_start_tailing(events),
        ),
    ):
        rc = command_watch(_watch_args())

    assert rc == 0
    out = capsys.readouterr().out
    lines = [l for l in out.splitlines() if l.strip()]
    assert len(lines) == 2


def test_watch_exits_cleanly_on_keyboard_interrupt(tmp_path: Path, capsys) -> None:
    projects = [_make_project("svc", tmp_path)]

    def _mock_start_tailing(project_pairs, since=None, events_file=None):
        q: queue.Queue[TailedEvent | None] = queue.Queue()
        stop = threading.Event()
        # Never push sentinel — simulates a live-running worker
        t = threading.Thread(target=lambda: None, daemon=True)
        return q, stop, [t]

    def _mock_get_raises(*args, **kwargs):
        raise KeyboardInterrupt

    with (
        patch("agent_takt_fleet.cli.commands.watch.load_registry", return_value=projects),
        patch(
            "agent_takt_fleet.cli.commands.watch.start_tailing",
            side_effect=_mock_start_tailing,
        ),
        patch("queue.Queue.get", side_effect=KeyboardInterrupt),
    ):
        rc = command_watch(_watch_args())

    assert rc == 0
    assert "(interrupted)" in capsys.readouterr().err


def test_watch_passes_since_to_start_tailing(tmp_path: Path) -> None:
    projects = [_make_project("svc", tmp_path)]
    captured_since: list[str | None] = []

    def _mock_start_tailing(project_pairs, since=None, events_file=None):
        captured_since.append(since)
        q: queue.Queue[TailedEvent | None] = queue.Queue()
        stop = threading.Event()
        for _ in project_pairs:
            q.put(None)
        return q, stop, [threading.Thread(target=lambda: None, daemon=True)]

    with (
        patch("agent_takt_fleet.cli.commands.watch.load_registry", return_value=projects),
        patch(
            "agent_takt_fleet.cli.commands.watch.start_tailing",
            side_effect=_mock_start_tailing,
        ),
    ):
        command_watch(_watch_args(since="30m"))

    assert captured_since == ["30m"]


def test_watch_applies_project_filter(tmp_path: Path, capsys) -> None:
    projects = [
        _make_project("api", tmp_path),
        _make_project("web", tmp_path),
    ]
    captured_pairs: list[list[tuple]] = []

    def _mock_start_tailing(project_pairs, since=None, events_file=None):
        captured_pairs.append(list(project_pairs))
        q: queue.Queue[TailedEvent | None] = queue.Queue()
        stop = threading.Event()
        for _ in project_pairs:
            q.put(None)
        return q, stop, [threading.Thread(target=lambda: None, daemon=True) for _ in project_pairs]

    with (
        patch("agent_takt_fleet.cli.commands.watch.load_registry", return_value=projects),
        patch(
            "agent_takt_fleet.cli.commands.watch.start_tailing",
            side_effect=_mock_start_tailing,
        ),
    ):
        command_watch(_watch_args(project=["api"]))

    assert len(captured_pairs) == 1
    assert len(captured_pairs[0]) == 1
    assert captured_pairs[0][0][0] == "api"


def test_watch_raw_unparseable_line_shown(tmp_path: Path, capsys) -> None:
    projects = [_make_project("svc", tmp_path)]
    raw_event = TailedEvent(
        project_name="svc",
        raw_line="this is not json at all",
        parsed=None,
        timestamp=None,
    )

    with (
        patch("agent_takt_fleet.cli.commands.watch.load_registry", return_value=projects),
        patch(
            "agent_takt_fleet.cli.commands.watch.start_tailing",
            side_effect=_build_mock_start_tailing([raw_event]),
        ),
    ):
        rc = command_watch(_watch_args())

    assert rc == 0
    out = capsys.readouterr().out
    assert "[svc]" in out
    assert "this is not json at all" in out
