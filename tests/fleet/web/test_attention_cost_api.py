"""API tests for /api/attention and /api/cost (fixture-driven)."""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from agent_takt_fleet.web.app import create_app

from .conftest import to_projects  # noqa: F401


@pytest.fixture
def client(projects):
    return TestClient(create_app(lambda: projects, installed_version="0.9.0"))


def test_attention_returns_sections_and_total(client):
    resp = client.get("/api/attention")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body["total"], int)
    sections = [k for k, v in body.items() if isinstance(v, dict) and "count" in v]
    assert {"blocked", "discovered", "unmet_criteria", "structural_assumptions", "stale_runs"} <= set(sections)
    for key in sections:
        assert body[key]["count"] == len(body[key]["items"]) or body[key]["count"] >= len(body[key]["items"])
    assert body["total"] == sum(body[k]["count"] for k in sections)


def test_cost_defaults_to_30_days(client):
    resp = client.get("/api/cost")
    assert resp.status_code == 200
    assert resp.json()["days"] == 30


@pytest.mark.parametrize("days", [7, 30, 90])
def test_cost_accepts_allowed_days(client, days):
    resp = client.get(f"/api/cost?days={days}")
    assert resp.status_code == 200
    assert resp.json()["days"] == days


@pytest.mark.parametrize("days", ["5", "0", "-7", "abc", "", "7.5", "9999"])
def test_cost_invalid_days_clamp_to_default(client, days):
    resp = client.get("/api/cost", params={"days": days})
    assert resp.status_code == 200
    assert resp.json()["days"] == 30


@pytest.mark.parametrize("path", ["/api/attention", "/api/cost"])
@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_routes_are_get_only(client, path, method):
    assert getattr(client, method)(path).status_code == 405
