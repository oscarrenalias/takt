"""``takt-fleet serve`` command: host policy, missing extra, demo isolation."""
from __future__ import annotations

import hashlib
import socket
import sys
import threading
import time
from argparse import Namespace
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent_takt_fleet.cli.commands import serve as serve_mod
from agent_takt_fleet.cli.commands.serve import command_serve
from agent_takt_fleet.cli.parser import build_parser
from agent_takt_fleet.web import demo as demo_mod


def _args(**kw):
    base = dict(host="127.0.0.1", port=0, allow_remote=False, poll_interval=1.0,
                demo=False, open=False)
    base.update(kw)
    return Namespace(**base)


@pytest.fixture
def fake_uvicorn():
    """Replace uvicorn.Server.run so no server is actually started."""
    import uvicorn

    with patch.object(uvicorn.Server, "run", autospec=True) as run:
        yield run


@pytest.fixture
def xdg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    return tmp_path


def test_remote_bind_requires_opt_in(capsys):
    """Scenario: fleet-web-ui / Read-only local server / Remote bind requires opt-in"""
    import uvicorn

    with patch.object(uvicorn.Server, "run") as run, patch.object(uvicorn, "Config") as cfg:
        rc = command_serve(_args(host="0.0.0.0"))
    assert rc == 2
    run.assert_not_called()
    cfg.assert_not_called()
    assert "--allow-remote" in capsys.readouterr().err


def test_allow_remote_prints_warning(capsys, fake_uvicorn, xdg):
    rc = command_serve(_args(host="0.0.0.0", allow_remote=True))
    assert rc == 0
    err = capsys.readouterr().err
    assert "warning" in err.lower() and "authentication" in err
    fake_uvicorn.assert_called_once()


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
def test_local_aliases_need_no_opt_in(host, fake_uvicorn, xdg, capsys):
    if host == "::1" and not socket.has_ipv6:
        pytest.skip("no IPv6")
    rc = command_serve(_args(host=host))
    assert rc in (0, 1)  # 1 only if the platform cannot bind that alias
    assert "--allow-remote" not in capsys.readouterr().err


def test_missing_web_extra_exits_1(capsys, monkeypatch):
    """Scenario: fleet-web-ui / Read-only local server / Missing web extra"""
    for mod in ("starlette", "uvicorn", "agent_takt_fleet.web.app"):
        monkeypatch.setitem(sys.modules, mod, None)
    rc = command_serve(_args())
    assert rc == 1
    err = capsys.readouterr().err
    assert "fleet-ui" in err


def test_list_works_without_starlette(monkeypatch, xdg):
    for mod in ("starlette", "uvicorn"):
        monkeypatch.setitem(sys.modules, mod, None)
    from agent_takt_fleet.cli import main
    monkeypatch.setattr(sys, "argv", ["takt-fleet", "list"])
    assert main() == 0


def test_url_uses_bound_port_for_port_zero(capsys, fake_uvicorn, xdg):
    rc = command_serve(_args(port=0))
    assert rc == 0
    out = capsys.readouterr().out
    line = next(l for l in out.splitlines() if "serving at" in l)
    port = int(line.split("http://127.0.0.1:")[1].split()[0])
    assert port > 0


def test_keyboard_interrupt_returns_0(fake_uvicorn, xdg):
    fake_uvicorn.side_effect = KeyboardInterrupt
    assert command_serve(_args()) == 0


def test_open_flag_calls_webbrowser(fake_uvicorn, xdg):
    with patch("webbrowser.open") as opened, patch.object(
            threading, "Timer", side_effect=lambda delay, fn, args=(): _Immediate(fn, args)):
        command_serve(_args(open=True))
    opened.assert_called_once()
    assert opened.call_args.args[0].startswith("http://127.0.0.1:")


def test_no_open_flag_no_browser(fake_uvicorn, xdg):
    with patch("webbrowser.open") as opened:
        command_serve(_args(open=False))
    opened.assert_not_called()


class _Immediate:
    def __init__(self, fn, args):
        self._fn, self._args = fn, args

    def start(self):
        self._fn(*self._args)


def test_bind_failure_exits_1(capsys, fake_uvicorn, xdg):
    busy = socket.socket()
    busy.bind(("127.0.0.1", 0))
    busy.listen(1)
    try:
        rc = command_serve(_args(port=busy.getsockname()[1]))
    finally:
        busy.close()
    # SO_REUSEADDR may allow a bind on some platforms while listening; accept the
    # documented failure path whenever bind is refused.
    if rc == 1:
        assert "cannot bind" in capsys.readouterr().err
    else:
        pytest.skip("platform allowed binding an occupied port")


def test_parser_serve_defaults():
    ns = build_parser().parse_args(["serve"])
    assert ns.host == "127.0.0.1" and ns.port == 8765
    assert ns.allow_remote is False and ns.demo is False and ns.open is False


# ---- demo mode ---------------------------------------------------------------

def _digest(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def test_demo_leaves_registry_alone(capsys, fake_uvicorn, xdg):
    """Scenario: fleet-web-ui / Demo mode / Demo leaves the registry alone"""
    from agent_takt_fleet.registry import save_registry
    from agent_takt_fleet.models import Project
    from agent_takt_fleet.paths import registry_path

    real = xdg / "real-project"
    real.mkdir()
    save_registry([Project("real", real, ())])
    before = _digest(registry_path())
    assert before is not None
    seen = {}

    def capture(self, sockets=None):
        from starlette.testclient import TestClient
        c = TestClient(self.config.app)
        seen["names"] = [p["name"] for p in c.get("/api/fleet").json()["projects"]]

    fake_uvicorn.side_effect = capture
    with patch("agent_takt_fleet.registry.load_registry",
               side_effect=AssertionError("demo must not read the registry")):
        rc = command_serve(_args(demo=True))
    assert rc == 0
    assert "DEMO MODE" in capsys.readouterr().out
    assert "real" not in seen["names"]
    assert all(n.startswith("demo-") or n == "ghost" for n in seen["names"])
    assert _digest(registry_path()) == before


def test_demo_temp_dir_removed_after_exit(fake_uvicorn, xdg):
    created = []
    real_mkdtemp = __import__("tempfile").mkdtemp

    def spy(*a, **k):
        d = real_mkdtemp(*a, **k)
        created.append(d)
        return d

    with patch("tempfile.mkdtemp", side_effect=spy):
        command_serve(_args(demo=True))
    assert created and not Path(created[0]).exists()


def test_demo_driver_started_and_stopped(fake_uvicorn, xdg):
    with patch.object(demo_mod.DemoActivityDriver, "start") as start, \
            patch.object(demo_mod.DemoActivityDriver, "stop") as stop:
        command_serve(_args(demo=True))
    start.assert_called_once()
    stop.assert_called_once()


def test_demo_activity_driver_emits_events(tmp_path):
    from agent_takt_fleet.web.readers import read_recent_events

    fleet = demo_mod.build_fixture_fleet(tmp_path / "f")
    target = next(p for p in fleet.projects if p.variant == "full")
    before = len(read_recent_events(target.path, 1000))
    driver = demo_mod.DemoActivityDriver(fleet, interval=0.02)
    driver.start()
    deadline = time.time() + 10
    types: set[str] = set()
    while time.time() < deadline:
        evs = read_recent_events(target.path, 1000)[before:]
        types = {e["event_type"] for e in evs}
        if {"bead_started", "bead_completed", "bead_blocked", "scheduler_run_finished"} <= types:
            break
        time.sleep(0.05)
    driver.stop()
    assert {"bead_started", "bead_completed", "bead_blocked", "scheduler_run_finished"} <= types
    assert driver._thread is None
    n = len(read_recent_events(target.path, 1000))
    time.sleep(0.15)
    assert len(read_recent_events(target.path, 1000)) == n  # stopped cleanly


def test_fixture_fleet_deterministic_and_registry_compatible(tmp_path, monkeypatch):
    from agent_takt_fleet.registry import load_registry
    from datetime import datetime, timezone

    now = datetime(2026, 3, 1, tzinfo=timezone.utc)
    a = demo_mod.build_fixture_fleet(tmp_path / "a", clock=lambda: now)
    b = demo_mod.build_fixture_fleet(tmp_path / "b", clock=lambda: now)
    ea = (a.projects[1].path / ".takt/logs/events.jsonl").read_text()
    eb = (b.projects[1].path / ".takt/logs/events.jsonl").read_text()
    assert ea == eb
    assert len(a.projects) == 5 and not a.missing[0].path.exists()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    (tmp_path / "xdg" / "agent-takt").mkdir(parents=True)
    (tmp_path / "xdg" / "agent-takt" / "fleet.yaml").write_text(a.registry_path.read_text())
    assert [p.name for p in load_registry()][-1] == "ghost"
