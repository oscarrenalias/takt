"""Every endpoint via Starlette TestClient against fixture registries."""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from agent_takt_fleet.web import demo
from agent_takt_fleet.web.app import create_app

from .conftest import clock

METHODS = ("POST", "PUT", "PATCH", "DELETE")
ROUTES = ("/", "/api/fleet", "/api/projects/demo-1-full", "/api/events", "/api/stream",
          "/static/index.html", "/static/app.js")


@pytest.fixture
def client(projects, no_subprocess):
    app = create_app(lambda: projects, installed_version="0.9.0", poll_interval=0.05)
    return TestClient(app)  # no lifespan: stream hub not started


def test_fleet_shape(client):
    r = client.get("/api/fleet")
    assert r.status_code == 200
    body = r.json()
    assert body["installed_version"] == "0.9.0"
    assert [p["name"] for p in body["projects"]][-1] == "ghost"
    assert len(body["projects"]) == 6
    assert "generated_at" in body


def test_project_detail_and_404(client):
    r = client.get("/api/projects/demo-1-full")
    assert r.status_code == 200
    assert r.json()["name"] == "demo-1-full"
    assert isinstance(r.json()["scheduler_runs"], list)
    r = client.get("/api/projects/nope")
    assert r.status_code == 404
    assert r.json() == {"error": "unknown project: nope"}


def test_events_params(client):
    body = client.get("/api/events").json()
    assert body["limit"] == 25 and body["events"]
    body = client.get("/api/events", params={"project": "demo-2-older", "limit": 2}).json()
    assert body["limit"] == 2 and len(body["events"]) == 2
    assert {e["project"] for e in body["events"]} == {"demo-2-older"}
    assert client.get("/api/events", params={"limit": 99999}).json()["limit"] == 200


def test_index_placeholder_html(client):
    r = client.get("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "<html" in r.text.lower()
    assert 'id="app"' in r.text


@pytest.mark.parametrize("path", ROUTES)
@pytest.mark.parametrize("method", METHODS)
def test_non_get_rejected(client, path, method):
    """Scenario: fleet-web-ui / Read-only local server / Non-GET request is rejected"""
    r = client.request(method, path)
    assert r.status_code == 405


def test_unknown_route_404(client):
    assert client.get("/api/nothing").status_code == 404


def test_registry_changes_are_picked_up(fleet, tmp_path):
    from agent_takt_fleet.models import Project
    current = [Project("a", fleet.projects[0].path, ())]
    c = TestClient(create_app(lambda: current, installed_version="0.9.0"))
    assert len(c.get("/api/fleet").json()["projects"]) == 1
    current.append(Project("b", fleet.projects[1].path, ()))
    assert len(c.get("/api/fleet").json()["projects"]) == 2


def test_no_subprocess_in_any_endpoint(client, no_subprocess):
    """Scenario: fleet-web-ui / Fleet overview data / No subprocesses"""
    for path in ("/", "/api/fleet", "/api/projects/demo-1-full", "/api/events",
                 "/api/projects/missing", "/static/app.js"):
        client.get(path)  # any spawn raises AssertionError via the patched subprocess


def test_app_does_not_use_adapter():
    import ast
    import inspect

    import agent_takt_fleet.web.app as app_mod
    tree = ast.parse(inspect.getsource(app_mod))
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert not ({"TaktAdapter", "subprocess"} & names)
    assert not ({"create_bead", "run"} & attrs)
