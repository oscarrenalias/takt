"""Shared fixtures for the fleet web UI tests (ADR-f42387c6).

Everything runs against synthetic projects built by ``web.demo`` under
``tmp_path``; the real registry and real projects are never read.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

pytest.importorskip("starlette", reason="fleet-ui extra not installed")

from agent_takt_fleet.models import Project  # noqa: E402
from agent_takt_fleet.web.demo import build_fixture_fleet  # noqa: E402

FIXED_NOW = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)


def clock() -> datetime:
    return FIXED_NOW


@pytest.fixture
def fleet(tmp_path):
    return build_fixture_fleet(tmp_path / "fleet", count=5, clock=clock)


def to_projects(fleet) -> list[Project]:
    return [Project(name=p.name, path=p.path, tags=p.tags)
            for p in (*fleet.projects, *fleet.missing)]


@pytest.fixture
def projects(fleet):
    return to_projects(fleet)


@pytest.fixture
def no_subprocess(monkeypatch):
    """Make any subprocess spawn fail the test."""
    import subprocess

    def boom(*a, **k):
        raise AssertionError("subprocess must not be used by the web UI")

    for name in ("run", "Popen", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, name, boom)
    return boom


class LiveServer:
    """uvicorn running a fleet app in a background thread on a random port."""

    def __init__(self, app):
        import socket
        import threading

        import uvicorn

        self.app = app
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.bind(("127.0.0.1", 0))
        self.port = self._sock.getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self.server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
        self._thread = threading.Thread(
            target=self.server.run, kwargs={"sockets": [self._sock]}, daemon=True)

    def start(self):
        import time

        self._thread.start()
        deadline = time.time() + 10
        while not self.server.started and time.time() < deadline:
            time.sleep(0.02)
        assert self.server.started, "server failed to start"
        return self

    def stop(self):
        self.server.should_exit = True
        self._thread.join(timeout=10)
        self._sock.close()


@pytest.fixture
def live_server_factory():
    servers: list[LiveServer] = []

    def make(app):
        s = LiveServer(app).start()
        servers.append(s)
        return s

    yield make
    for s in servers:
        s.stop()
