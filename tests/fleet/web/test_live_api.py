"""API tests for the live-session routes (fixture-driven, no real Claude dir)."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from agent_takt_fleet.web import demo, live
from agent_takt_fleet.web.app import create_app

from .conftest import to_projects  # noqa: F401

PROJ = "demo-1-full"
SID = str(uuid.uuid4())


def _bead_id(fleet):
    from agent_takt_fleet.web.readers import ProjectReader

    p = next(p for p in fleet.projects if p.name == PROJ)
    return p, ProjectReader(p.path).read_beads().beads[0]["bead_id"]


@pytest.fixture
def setup(fleet, projects, tmp_path):
    proj, bead_id = _bead_id(fleet)
    cfg = tmp_path / "claude"
    (cfg / "projects" / "x").mkdir(parents=True)
    (cfg / "projects" / "x" / f"{SID}.jsonl").write_text(
        json.dumps({"type": "user", "message": {"role": "user", "content": "hi"},
                    "timestamp": datetime.now(timezone.utc).isoformat()}) + "\n"
    )
    demo.append_event(proj.path, "agent_session_started", {
        "bead_id": bead_id, "session_id": SID, "role": "main",
        "started_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
    }, clock=lambda: datetime.now(timezone.utc))
    app = create_app(lambda: projects, installed_version="0.9.0", claude_config_dir=cfg)
    return TestClient(app), app, bead_id, cfg


def test_config_dir_override(setup):
    _, app, _, cfg = setup
    assert app.state.claude_config_dir == cfg


def test_live_routes_return_view_models(setup):
    client, app, bead_id, _ = setup
    assert app.state.live_state["last_request"] is None
    r = client.get("/api/live")
    assert r.status_code == 200 and r.json()["count"] >= 1
    r = client.get(f"/api/projects/{PROJ}/beads/{bead_id}/live")
    assert r.status_code == 200 and r.json()["sessions"][0]["session_id"] == SID
    r = client.get(f"/api/projects/{PROJ}/beads/{bead_id}/live/{SID}")
    assert r.status_code == 200 and r.json()["session_id"] == SID
    assert app.state.live_state["last_request"] is not None


def test_unsafe_session_ids_404_without_file_reads(setup, monkeypatch):
    client, _, bead_id, _ = setup

    def boom(*a, **k):
        raise AssertionError("no file read expected")

    monkeypatch.setattr(live, "locate_transcript", boom)
    for sid in ("not-a-uuid", "..%2F..%2Fetc%2Fpasswd", "a" * 36, f"{SID}.jsonl"):
        r = client.get(f"/api/projects/{PROJ}/beads/{bead_id}/live/{sid}")
        assert r.status_code == 404


def test_after_validation(setup):
    client, _, bead_id, _ = setup
    base = f"/api/projects/{PROJ}/beads/{bead_id}/live/{SID}"
    for bad in ("-1", "abc", "1.5", ""):
        assert client.get(base, params={"after": bad}).status_code == 400
    assert client.get(base, params={"after": "0"}).status_code == 200
    assert client.get(base).json()["after"] == 0
    assert client.get(base, params={"after": "99"}).json()["events"] == []


def test_unknown_project_and_bead(setup):
    client, _, bead_id, _ = setup
    assert client.get(f"/api/projects/nope/beads/{bead_id}/live").status_code == 404
    assert client.get(f"/api/projects/{PROJ}/beads/B-zzzzzzzz/live").status_code == 404


def test_get_only(setup):
    client, _, bead_id, _ = setup
    for url in ("/api/live", f"/api/projects/{PROJ}/beads/{bead_id}/live",
                f"/api/projects/{PROJ}/beads/{bead_id}/live/{SID}"):
        for method in ("post", "put", "delete", "patch"):
            assert getattr(client, method)(url).status_code == 405
