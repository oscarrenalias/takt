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
    assert viewmodels.build_events(projects)["limit"] == 25
    assert viewmodels.build_events(projects, limit=0)["limit"] == 1
    assert viewmodels.build_events(projects, limit=-5)["limit"] == 1
    assert viewmodels.build_events(projects, limit=5000)["limit"] == 200
    assert viewmodels.build_events(projects, limit="abc")["limit"] == 25
    assert len(viewmodels.build_events(projects, limit=3)["events"]) == 3


def test_unknown_event_types_are_kept(fleet, projects):
    """Scenario: fleet-web-ui / Fleet activity / Unknown event types are kept"""
    p = fleet.projects[0]
    demo.append_event(p.path, "totally_new_event", {"summary": "shiny"}, clock=lambda: clock().replace(hour=22))
    out = viewmodels.build_events(projects, project=p.name, limit=5)
    item = out["events"][0]
    assert item["event_type"] == "totally_new_event"
    assert item["severity"] == "info"
    assert "totally_new_event" in item["summary"]
    assert "shiny" in item["summary"]


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
    long = summarize_event("bead_started", {"summary": "a" * 300})
    assert len(long) == 120 and long.endswith("…")


def _cost_project(tmp_path, name, events, beads=()):
    import json
    from agent_takt_fleet.models import Project
    root = tmp_path / name
    (root / ".takt" / "logs").mkdir(parents=True)
    (root / ".takt" / "beads").mkdir(parents=True)
    with (root / ".takt" / "logs" / "events.jsonl").open("w") as fh:
        for ts, etype, payload in events:
            fh.write(json.dumps({"timestamp": ts, "event_type": etype, "payload": payload}) + "\n")
    for bead_id, title in beads:
        (root / ".takt" / "beads" / f"{bead_id}.json").write_text(
            json.dumps({"bead_id": bead_id, "title": title}))
    return Project(name=name, path=root, tags=())


def test_build_cost_window_totals_top_and_nulls(tmp_path):
    from datetime import datetime, timezone
    now = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)
    a = _cost_project(tmp_path, "a", [
        ("2026-09-01T00:00:00+00:00", "bead_completed", {"bead_id": "B-old", "agent_type": "developer", "cost_usd": 99.0}),
        ("2026-11-20T23:00:00+00:00", "bead_completed", {"bead_id": "B-1", "agent_type": "developer", "cost_usd": 1.0}),
        ("2026-11-21T01:00:00+00:00", "bead_blocked", {"bead_id": "B-1", "agent_type": "developer", "cost_usd": 0.5}),
        ("2026-11-21T02:00:00+00:00", "bead_failed", {"bead_id": "B-2", "agent_type": "tester", "cost_usd": 0.25}),
        ("2026-11-21T03:00:00+00:00", "bead_completed", {"bead_id": "B-3", "agent_type": "tester", "cost_usd": None}),
        ("2026-11-21T04:00:00+00:00", "bead_started", {"bead_id": "B-3", "cost_usd": 50}),
    ], beads=[("B-1", "First")])
    b = _cost_project(tmp_path, "b", [
        ("2026-11-21T05:00:00+00:00", "bead_completed", {"bead_id": "B-9", "agent_type": "review", "cost_usd": 2.0}),
    ])
    out = viewmodels.build_cost([a, b], 7, now)
    assert out["days"] == 7
    assert out["total_cost_usd"] == 3.75
    assert out["priced_events"] == 4 and out["null_cost_events"] == 1
    projs = {p["project"]: p for p in out["projects"]}
    assert projs["a"]["cost_usd"] == 1.75 and projs["b"]["cost_usd"] == 2.0
    dates = out["dates"]
    assert projs["a"]["daily"][dates.index("2026-11-20")] == 1.0
    assert projs["a"]["daily"][dates.index("2026-11-21")] == 0.75
    assert projs["b"]["daily"][dates.index("2026-11-21")] == 2.0
    assert {x["agent_type"]: x["cost_usd"] for x in out["agent_types"]} == {
        "developer": 1.5, "tester": 0.25, "review": 2.0}
    top = out["top_beads"]
    assert [t["bead_id"] for t in top] == ["B-9", "B-1", "B-2"]
    assert top[1] == {"project": "a", "bead_id": "B-1", "title": "First",
                      "agent_type": "developer", "cost_usd": 1.5}
    assert top[0]["title"] is None
    # A wider window includes the old event.
    assert viewmodels.build_cost([a], 90, now)["total_cost_usd"] == 100.75


def test_build_cost_top_ten_and_days_fallback(tmp_path):
    from datetime import datetime, timezone
    now = datetime(2026, 11, 22, tzinfo=timezone.utc)
    events = [("2026-11-21T00:00:00+00:00", "bead_completed",
               {"bead_id": f"B-{i}", "agent_type": "developer", "cost_usd": float(i)}) for i in range(1, 13)]
    proj = _cost_project(tmp_path, "p", events)
    out = viewmodels.build_cost([proj], 30, now)
    assert len(out["top_beads"]) == 10
    assert out["top_beads"][0]["bead_id"] == "B-12"
    for bad in (5, "abc", None, 0, -7, True):
        assert viewmodels.build_cost([proj], bad, now)["days"] == 30
    assert viewmodels.build_cost([proj], "7", now)["days"] == 7
    assert viewmodels.build_cost([], 30, now)["total_cost_usd"] == 0.0


def _attention_project(tmp_path, name, beads, max_attempts=None, stale=False):
    import json
    from agent_takt_fleet.models import Project
    root = tmp_path / name
    (root / ".takt" / "beads").mkdir(parents=True)
    cfg = "common: {}\n"
    if max_attempts is not None:
        cfg += f"scheduler:\n  max_corrective_attempts: {max_attempts}\n"
    (root / ".takt" / "config.yaml").write_text(cfg)
    for b in beads:
        (root / ".takt" / "beads" / f"{b['bead_id']}.json").write_text(json.dumps(b))
    if stale:
        active = root / ".takt" / "runs" / "active"
        active.mkdir(parents=True)
        (active / "SR-deadbeef.json").write_text(json.dumps(
            {"run_id": "SR-deadbeef", "pid": 2**22 + 12345, "started_at": "2026-11-20T00:00:00+00:00"}))
    return Project(name=name, path=root, tags=())


def _bead(bead_id, root=None, status="done", ts="2026-11-21T10:00:00+00:00", **kw):
    b = {"bead_id": bead_id, "feature_root_id": root or bead_id, "title": bead_id,
         "agent_type": "developer", "status": status,
         "execution_history": [{"timestamp": ts, "event": "created"}]}
    b.update(kw)
    return b


def test_build_attention_sections(tmp_path):
    from datetime import datetime, timezone
    now = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)
    cs = [{"index": 0, "criterion": "c0", "status": "unmet", "evidence": "x"},
          {"index": 1, "criterion": "c1", "status": "met", "evidence": "y"}]
    asm = [{"statement": "S", "rationale": "R", "scope": "structural"},
           {"statement": "L", "rationale": "R", "scope": "local"}]
    beads = [
        _bead("B-root1"),
        _bead("B-blk-old", "B-root1", "blocked", ts="2026-11-20T00:00:00+00:00",
              block_reason="old"),
        _bead("B-blk-new", "B-root1", "blocked", ts="2026-11-21T00:00:00+00:00",
              block_reason="new"),
        _bead("B-blk-new-corrective", "B-root1", "ready",
              metadata={"auto_corrective_for": "B-blk-new"}),
        _bead("B-disc", status="open", labels=["discovered"],
              metadata={"discovered_issue_of": "B-blk-new"}),
        _bead("B-open", status="open"),
        _bead("B-dev", "B-root1", "done",
              handoff_summary={"criteria_status": cs, "assumptions": asm}),
        _bead("B-merged", metadata={"merge_records": [{"approver": "a"}]}),
        _bead("B-mdev", "B-merged", "done",
              handoff_summary={"criteria_status": cs, "assumptions": asm}),
    ]
    proj = _attention_project(tmp_path, "p", beads, max_attempts=2, stale=True)
    out = viewmodels.build_attention([proj], now)

    items = out["blocked"]["items"]
    assert out["blocked"]["count"] == 2
    assert [i["bead_id"] for i in items] == ["B-blk-new", "B-blk-old"]
    assert items[0]["block_reason"] == "new"
    assert items[0]["correctives_remaining"] == 1 and items[0]["has_correctives_remaining"]
    assert items[1]["correctives_remaining"] == 2
    assert items[0]["project"] == "p" and items[0]["feature_root_id"] == "B-root1"

    assert [i["bead_id"] for i in out["discovered"]["items"]] == ["B-disc"]
    assert out["discovered"]["items"][0]["reported_by"] == "B-blk-new"
    assert out["discovered"]["items"][0]["age_seconds"] == 26 * 3600

    unmet = out["unmet_criteria"]["items"]
    assert [(i["bead_id"], i["criterion"]) for i in unmet] == [("B-dev", "c0")]
    assert [i["statement"] for i in out["structural_assumptions"]["items"]] == ["S"]
    assert out["structural_assumptions"]["items"][0]["bead_id"] == "B-dev"
    assert out["stale_runs"]["count"] == 1
    assert out["stale_runs"]["items"][0]["project"] == "p"
    assert out["total"] == 2 + 1 + 1 + 1 + 1


def test_build_attention_defaults_and_empty(tmp_path):
    beads = [_bead("B-r", status="blocked")] + [
        _bead(f"B-c{i}", status="ready", metadata={"auto_corrective_for": "B-r"})
        for i in range(7)]
    proj = _attention_project(tmp_path, "q", beads)
    out = viewmodels.build_attention([proj])
    assert out["blocked"]["items"][0]["correctives_remaining"] == 0
    assert out["blocked"]["items"][0]["has_correctives_remaining"] is False
    assert out["stale_runs"] == {"count": 0, "items": []}
    empty = viewmodels.build_attention([])
    assert empty["total"] == 0 and empty["blocked"] == {"count": 0, "items": []}


# --- Live session view models ----------------------------------------------

import json as _json
from datetime import datetime as _dt, timedelta as _td, timezone as _tz
from types import SimpleNamespace as _NS

from agent_takt_fleet.web import live as _live

_LNOW = _dt(2026, 11, 22, 12, 0, tzinfo=_tz.utc)
_SID = "11111111-2222-3333-4444-555555555555"
_SID2 = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def _ts(delta):
    return (_LNOW - delta).isoformat()


def _start_event(sid, bead="B-1", ago=_td(minutes=5)):
    return {"timestamp": _ts(ago), "event_type": "agent_session_started",
            "payload": {"bead_id": bead, "role": "main", "session_id": sid,
                        "started_at": _ts(ago), "agent_type": "developer",
                        "feature_root_id": "B-1"}}


def _line(**kw):
    return _json.dumps(kw) + "\n"


def _tool_use(cid, name, ts):
    return _line(type="assistant", sessionId=_SID, cwd="/x", timestamp=ts,
                 message={"id": "m" + cid, "content": [
                     {"type": "tool_use", "id": cid, "name": name, "input": {}}]})


def _tool_result(cid, ts, error=False):
    return _line(type="user", timestamp=ts,
                 message={"content": [{"type": "tool_result", "tool_use_id": cid,
                                       "content": "ok", "is_error": error}]})


class _LiveEnv:
    def __init__(self, tmp_path, events):
        self.project = _NS(name="p", path=tmp_path / "proj")
        logs = self.project.path / ".takt" / "logs"
        logs.mkdir(parents=True)
        (self.project.path / ".takt" / "beads").mkdir()
        (logs / "events.jsonl").write_text(
            "".join(_json.dumps(e) + "\n" for e in events))
        (self.project.path / ".takt" / "beads" / "B-1.json").write_text(
            _json.dumps({"bead_id": "B-1", "title": "Do thing", "agent_type": "developer",
                         "feature_root_id": "B-1", "status": "in_progress"}))
        self.cfg = tmp_path / "claude"
        (self.cfg / "projects" / "proj").mkdir(parents=True)
        self.registry = viewmodels.LiveSessionRegistry(self.cfg)

    def transcript(self, sid=_SID):
        return self.cfg / "projects" / "proj" / f"{sid}.jsonl"


def _env(tmp_path, events):
    _live.clear_transcript_cache()
    return _LiveEnv(tmp_path, events)


def test_live_fleet_documented_fields(tmp_path):
    env = _env(tmp_path, [_start_event(_SID)])
    env.transcript().write_text(
        _tool_use("c1", "Read", _ts(_td(minutes=4)))
        + _tool_result("c1", _ts(_td(minutes=3)), error=True))
    body = viewmodels.build_live_fleet([env.project], env.registry, _LNOW)
    assert body["count"] == 1
    row = body["sessions"][0]
    assert set(row) >= {"project", "bead_id", "bead_title", "agent_type", "feature_root_id",
                        "role", "session_id", "started_at", "status", "event_count",
                        "last_event_at", "tool_calls", "errors", "last_tool"}
    assert (row["project"], row["bead_id"], row["bead_title"]) == ("p", "B-1", "Do thing")
    assert row["status"] == "live"
    assert row["tool_calls"] == 1 and row["errors"] == 1 and row["last_tool"] == "Read"


def test_live_fleet_waiting_without_transcript(tmp_path):
    env = _env(tmp_path, [_start_event(_SID)])
    row = viewmodels.build_live_fleet([env.project], env.registry, _LNOW)["sessions"][0]
    assert row["status"] == "waiting" and row["event_count"] == 0


def test_bead_live_includes_recently_ended(tmp_path):
    ended = {"timestamp": _ts(_td(minutes=2)), "event_type": "trace_captured",
             "payload": {"bead_id": "B-1", "session_id": _SID, "seq": 1}}
    env = _env(tmp_path, [_start_event(_SID, ago=_td(minutes=20)), ended])
    body = viewmodels.build_bead_live(env.project, "B-1", env.registry, _LNOW)
    assert [s["session_id"] for s in body["sessions"]] == [_SID]
    s = body["sessions"][0]
    assert s["status"] == "ended" and s["ended"] is True
    assert s["trace"] == {"bead_id": "B-1", "seq": 1, "session_id": _SID}


def test_bead_live_hides_session_ended_over_ten_minutes_ago(tmp_path):
    ended = {"timestamp": _ts(_td(minutes=11)), "event_type": "bead_completed",
             "payload": {"bead_id": "B-1"}}
    env = _env(tmp_path, [_start_event(_SID, ago=_td(minutes=30)), ended])
    assert viewmodels.build_bead_live(env.project, "B-1", env.registry, _LNOW)["sessions"] == []


def test_session_detail_after_and_updated_earlier_events(tmp_path):
    env = _env(tmp_path, [_start_event(_SID)])
    env.transcript().write_text(_tool_use("c1", "Bash", _ts(_td(minutes=4))))
    first = viewmodels.build_session_detail(env.project, "B-1", _SID, 0, env.registry, _LNOW)
    assert [e["index"] for e in first["events"]] == [0, 1]
    pending = first["events"][1]
    assert pending["kind"] == "tool_call" and pending["result_ts"] is None

    with open(env.transcript(), "a") as fh:
        fh.write(_tool_result("c1", _ts(_td(minutes=3))))
        fh.write(_tool_use("c2", "Edit", _ts(_td(minutes=2))))
    later = viewmodels.build_session_detail(env.project, "B-1", _SID, 2, env.registry, _LNOW)
    assert [e["index"] for e in later["events"]] == [2]
    assert later["event_count"] == 3
    refetch = viewmodels.build_session_detail(env.project, "B-1", _SID, 1, env.registry, _LNOW)
    assert [e["index"] for e in refetch["events"]] == [1, 2]
    assert refetch["events"][0]["result_ts"] is not None
    assert refetch["events"][0]["duration_ms"] == 60000
    assert refetch["summary"]["tool_counts"] == {"Bash": 1, "Edit": 1}


def test_session_detail_not_found_without_file_reads(tmp_path, monkeypatch):
    env = _env(tmp_path, [_start_event(_SID)])
    env.transcript().write_text(_tool_use("c1", "Read", _ts(_td(minutes=4))))

    def boom(*a, **k):
        raise AssertionError("no file reads expected")

    monkeypatch.setattr(viewmodels, "read_events_since", boom)
    monkeypatch.setattr(_live, "locate_transcript", boom)
    monkeypatch.setattr(_live.TranscriptFollower, "refresh", boom)
    for bad in ("../../etc/passwd", "a/b", "..", "", "not-a-uuid", None, _SID + "/x"):
        assert viewmodels.build_session_detail(env.project, "B-1", bad, 0, env.registry, _LNOW) is None


def test_session_detail_unknown_session_returns_none(tmp_path):
    env = _env(tmp_path, [_start_event(_SID)])
    env.transcript(_SID2).write_text(_tool_use("c1", "Read", _ts(_td(minutes=4))))
    assert viewmodels.build_session_detail(env.project, "B-1", _SID2, 0, env.registry, _LNOW) is None
    # right session, wrong bead
    assert viewmodels.build_session_detail(env.project, "B-9", _SID, 0, env.registry, _LNOW) is None


def test_registry_keeps_follower_across_requests(tmp_path):
    env = _env(tmp_path, [_start_event(_SID)])
    env.transcript().write_text(_tool_use("c1", "Read", _ts(_td(minutes=4))))
    viewmodels.build_live_fleet([env.project], env.registry, _LNOW)
    follower = env.registry._followers[("p", _SID)]
    read_once = follower.bytes_read
    viewmodels.build_live_fleet([env.project], env.registry, _LNOW)
    assert env.registry._followers[("p", _SID)] is follower
    assert follower.bytes_read == read_once
