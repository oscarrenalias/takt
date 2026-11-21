"""View models and event summaries."""
from __future__ import annotations

from agent_takt_fleet.event_summary import event_severity, summarize_event
from agent_takt_fleet.scheduler_runs import collect_scheduler_runs
from agent_takt_fleet.web import demo, viewmodels
from agent_takt_fleet.web.readers import BEAD_STATUSES, ProjectReader

from .conftest import clock


def _by_name(items, name):
    return next(i for i in items if i.name == name)


def test_version_drift_cases():
    d = viewmodels.version_drift
    assert d("0.3.0", "0.9.0") == "behind"
    assert d("0.9.0", "0.9.0") == "none"
    assert d("99.0.0", "0.9.0") == "none"
    assert d(None, "0.9.0") == "unknown"
    assert d("0.9.0rc1", "0.9.0") == "behind"
    assert d("garbage", "0.9.0") == "unknown"
    assert d("garbage", "garbage") == "none"


def test_card_missing_path_and_no_takt(projects, tmp_path):
    ghost = _by_name(projects, "ghost")
    card = viewmodels.build_project_card(ghost, None, "0.9.0")
    assert card["health"] == "missing"
    assert card["bead_counts"] == {s: 0 for s in BEAD_STATUSES}
    from agent_takt_fleet.models import Project
    bare = Project(name="bare", path=tmp_path, tags=())
    card = viewmodels.build_project_card(bare, None, "0.9.0")
    assert card["health"] == "no-takt"
    assert set(card["bead_counts"]) == set(BEAD_STATUSES)


def test_mixed_fleet_cards(fleet, projects):
    """Scenario: fleet-web-ui / Fleet overview data / Mixed fleet"""
    body = viewmodels.build_fleet(projects, "0.9.0", now=clock())
    assert set(body) == {"installed_version", "projects", "generated_at"}
    cards = {c["name"]: c for c in body["projects"]}
    assert len(cards) == 6
    assert cards["demo-1-full"]["scheduler"]["state"] == "running"
    assert cards["demo-1-full"]["health"] == "ok"
    assert cards["demo-1-full"]["bead_counts"]["blocked"] == 1
    assert cards["demo-2-older"]["scheduler"]["state"] == "idle"
    assert cards["ghost"]["health"] == "missing"
    assert cards["demo-4-corrupt"]["unreadable_beads"] == 1
    for c in cards.values():
        assert set(c) >= {"name", "path", "tags", "health", "takt_version", "version_drift",
                          "scheduler", "bead_counts", "unreadable_beads", "last_event_at"}
        assert set(c["bead_counts"]) >= set(BEAD_STATUSES)


def test_older_takt_version_drift(fleet, projects):
    """Scenario: fleet-web-ui / Fleet overview data / Older takt version"""
    cards = {c["name"]: c for c in viewmodels.build_fleet(projects, "0.9.0")["projects"]}
    assert cards["demo-2-older"]["takt_version"] == "0.3.0"
    assert cards["demo-2-older"]["version_drift"] == "behind"
    assert cards["demo-1-full"]["version_drift"] == "none"
    assert cards["demo-3-newer"]["version_drift"] == "none"
    assert cards["demo-5-no-version"]["takt_version"] is None
    assert cards["demo-5-no-version"]["version_drift"] == "unknown"


def test_last_event_at_is_newest_timestamp(fleet, projects):
    full = _by_name(projects, "demo-1-full")
    card = viewmodels.build_project_card(full, None, "0.9.0")
    from agent_takt_fleet.web.readers import read_recent_events
    newest = max(e["timestamp"] for e in read_recent_events(full.path, 50))
    assert card["last_event_at"] == newest
    demo.append_event(full.path, "bead_started", {"bead_id": "B-x"}, clock=lambda: clock().replace(hour=23))
    assert viewmodels.build_project_card(full, None, "0.9.0")["last_event_at"].startswith("2026-03-01T23")


def test_event_items_unknown_type_and_payload(projects):
    item = viewmodels.build_event_item("p", {"timestamp": "2026-01-01T00:00:00+00:00",
                                              "event_type": "from_the_future", "payload": "oops"})
    assert item["severity"] == "info"
    assert "from_the_future" in item["summary"]
    assert item["payload"] == {}
    nots = viewmodels.build_event_item("p", {"event_type": "x"})
    assert nots["timestamp"] == ""


def test_events_merged_across_projects(fleet, projects):
    """Scenario: fleet-web-ui / Fleet activity / Merged across projects"""
    a, b = fleet.projects[0], fleet.projects[1]
    t = clock()
    from datetime import timedelta
    demo.append_event(a.path, "bead_started", {"bead_id": "B-a"}, clock=lambda: t + timedelta(minutes=5))
    demo.append_event(b.path, "bead_started", {"bead_id": "B-b"}, clock=lambda: t + timedelta(minutes=6))
    out = viewmodels.build_events(projects)
    stamps = [e["timestamp"] for e in out["events"]]
    assert stamps == sorted(stamps, reverse=True)
    assert out["events"][0]["project"] == b.name
    assert out["events"][1]["project"] == a.name
    assert {e["project"] for e in out["events"]} >= {a.name, b.name}
    only = viewmodels.build_events(projects, project=a.name)
    assert {e["project"] for e in only["events"]} == {a.name}


def test_events_limit_clamp(projects):
    assert viewmodels.build_events(projects)["limit"] == 200
    assert viewmodels.build_events(projects, limit=0)["limit"] == 1
    assert viewmodels.build_events(projects, limit=-5)["limit"] == 1
    assert viewmodels.build_events(projects, limit=5000)["limit"] == 1000
    assert viewmodels.build_events(projects, limit="abc")["limit"] == 200
    assert len(viewmodels.build_events(projects, limit=3)["events"]) == 3


def test_unknown_event_types_are_kept(fleet, projects):
    """Scenario: fleet-web-ui / Fleet activity / Unknown event types are kept"""
    p = fleet.projects[0]
    demo.append_event(p.path, "totally_new_event", {"summary": "shiny"}, clock=lambda: clock().replace(hour=22))
    out = viewmodels.build_events(projects, project=p.name, limit=5)
    item = out["events"][0]
    assert item["event_type"] == "totally_new_event"
    assert item["severity"] == "info"
    assert item["summary"] == "shiny"


def test_events_missing_project_path_is_empty(projects):
    assert viewmodels.build_events(projects, project="ghost")["events"] == []


def test_project_detail_runs(fleet, projects):
    full = _by_name(projects, "demo-1-full")
    detail = viewmodels.build_project_detail(full, ProjectReader(full.path), "0.9.0")
    card = viewmodels.build_project_card(full, None, "0.9.0")
    assert {k: v for k, v in detail.items() if k != "scheduler_runs"} == card
    expected = collect_scheduler_runs([(full.name, full.path)], limit=20)
    assert detail["scheduler_runs"] == expected
    assert 1 <= len(detail["scheduler_runs"]) <= 20
    starts = [r["started_at"] for r in detail["scheduler_runs"]]
    assert starts == sorted(starts, reverse=True)


def test_event_summary_and_severity():
    assert event_severity("bead_failed", {}) == "error"
    assert event_severity("bead_blocked", {}) == "warning"
    assert event_severity("bead_completed", {}) == "success"
    assert event_severity("scheduler_run_finished", {"exit_reason": "crashed"}) == "error"
    assert event_severity("scheduler_run_finished", {"exit_reason": "quiescence"}) == "info"
    assert event_severity("mystery", None) == "info"
    assert "mystery" in summarize_event("mystery", None)
    assert summarize_event("x", "not-a-dict") == "x"
    long = summarize_event("x", {"summary": "a" * 300})
    assert len(long) == 120 and long.endswith("…")
