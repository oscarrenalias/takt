"""Fixture fleet live-session data: fake Claude config dir and demo driver."""
from __future__ import annotations

import json
from pathlib import Path

from agent_takt_fleet.web.demo import DemoActivityDriver

from .conftest import to_projects


def _events(project):
    path = project.path / ".takt" / "logs" / "events.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def test_config_dir_under_fixture_root_and_passed_to_create_app(fleet):
    from agent_takt_fleet.web.app import create_app

    cfg = fleet.claude_config_dir
    assert cfg is not None and cfg.is_relative_to(fleet.root)
    app = create_app(lambda: to_projects(fleet), claude_config_dir=cfg)
    assert Path(app.state.claude_config_dir) == cfg


def test_sessions_growing_waiting_ended(fleet):
    kinds = {s.kind: s for s in fleet.sessions}
    assert set(kinds) == {"growing", "waiting", "ended"}
    assert kinds["growing"].transcript_path.is_file()
    assert kinds["waiting"].transcript_path is None
    assert not list(fleet.claude_config_dir.glob(
        f"projects/*/{kinds['waiting'].session_id}.jsonl"))
    full = next(p for p in fleet.projects if p.variant == "full")
    events = _events(full)
    started = {e["payload"]["session_id"] for e in events
               if e["event_type"] == "agent_session_started"}
    assert started == {s.session_id for s in fleet.sessions}
    captured = [e for e in events if e["event_type"] == "trace_captured"]
    assert [e["payload"]["session_id"] for e in captured] == [kinds["ended"].session_id]
    trace = full.path / ".takt" / "traces" / kinds["ended"].bead_id / "1.meta.json"
    assert json.loads(trace.read_text())["session_id"] == kinds["ended"].session_id


def test_driver_appends_transcript_lines(fleet):
    growing = next(s for s in fleet.sessions if s.kind == "growing")
    before = growing.transcript_path.read_text().count("\n")
    driver = DemoActivityDriver(fleet)
    for _ in range(3):
        driver.append_transcript_step()
    lines = [json.loads(x) for x in growing.transcript_path.read_text().splitlines()]
    assert len(lines) == before + 3
    content = [ln["message"]["content"][0]["type"] for ln in lines[-3:]]
    assert content == ["text", "tool_use", "tool_result"]


def test_nothing_touches_real_claude_home(fleet, tmp_path):
    real = Path.home()  # HOME is isolated to tmp_path by conftest
    assert real.is_relative_to(tmp_path)
    DemoActivityDriver(fleet).append_transcript_step()
    for path in fleet.root.rglob("*"):
        assert ".claude" not in path.parts
    assert not (real / ".claude").exists()
    assert fleet.claude_config_dir.is_relative_to(tmp_path)
