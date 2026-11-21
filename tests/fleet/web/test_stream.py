"""EventLogTail, FleetStreamHub and the /api/stream SSE endpoint."""
from __future__ import annotations

import json
import os

import pytest
from starlette.testclient import TestClient

from agent_takt_fleet.models import Project
from agent_takt_fleet.web import demo, viewmodels
from agent_takt_fleet.web.app import create_app
from agent_takt_fleet.web.readers import ProjectReader
from agent_takt_fleet.web.stream import EventLogTail, FleetStreamHub, format_sse

from .conftest import clock, to_projects


def _line(n, **extra):
    return json.dumps({"timestamp": f"t{n}", "event_type": "e", "payload": extra}) + "\n"


# ---- EventLogTail ----------------------------------------------------------

def test_tail_appended_lines_only(tmp_path):
    f = tmp_path / "e.jsonl"
    f.write_text(_line(0))
    tail = EventLogTail(f)
    assert tail.poll() == []
    f.open("a").write(_line(1) + _line(2))
    assert [r["timestamp"] for r in tail.poll()] == ["t1", "t2"]
    assert tail.poll() == []


def test_tail_partial_line_completed_later(tmp_path):
    f = tmp_path / "e.jsonl"
    f.write_text("")
    tail = EventLogTail(f)
    full = _line(1)
    with f.open("a") as fh:
        fh.write(full[:10])
    assert tail.poll() == []
    with f.open("a") as fh:
        fh.write(full[10:])
    assert [r["timestamp"] for r in tail.poll()] == ["t1"]


def test_tail_skips_malformed_and_non_dict(tmp_path):
    f = tmp_path / "e.jsonl"
    f.write_text("")
    tail = EventLogTail(f)
    with f.open("a") as fh:
        fh.write("{broken\n[1,2]\n42\n\n" + _line(1))
    assert [r["timestamp"] for r in tail.poll()] == ["t1"]


def test_tail_truncation_rereads_without_resending(tmp_path):
    f = tmp_path / "e.jsonl"
    f.write_text("")
    tail = EventLogTail(f)
    with f.open("a") as fh:
        fh.write(_line(1) + _line(2))
    assert len(tail.poll()) == 2
    with f.open("wb"):
        pass
    assert tail.poll() == []
    with f.open("a") as fh:
        fh.write(_line(3))
    assert [r["timestamp"] for r in tail.poll()] == ["t3"]


def test_tail_replacement_new_inode(tmp_path):
    f = tmp_path / "e.jsonl"
    f.write_text("")
    tail = EventLogTail(f)
    with f.open("a") as fh:
        fh.write(_line(1) + _line(2))
    tail.poll()
    new = tmp_path / "new.jsonl"
    new.write_text(_line(1) + _line(2) + _line(3))
    os.replace(new, f)
    assert [r["timestamp"] for r in tail.poll()] == ["t3"]  # t1/t2 not resent


def test_tail_missing_file_then_appears(tmp_path):
    f = tmp_path / "late.jsonl"
    tail = EventLogTail(f)
    assert tail.poll() == []
    f.write_text(_line(1) + _line(2))
    assert [r["timestamp"] for r in tail.poll()] == ["t1", "t2"]  # read in full


def test_tail_start_offset(tmp_path):
    f = tmp_path / "e.jsonl"
    first = _line(1)
    f.write_text(first + _line(2))
    tail = EventLogTail(f, start_offset=len(first))
    assert [r["timestamp"] for r in tail.poll()] == ["t2"]
    assert EventLogTail(f, start_offset=0).poll()[0]["timestamp"] == "t1"


# ---- hub (deterministic, no event loop timing) ------------------------------

def _hub(projects_list):
    readers = {}

    def reader(p):
        return readers.setdefault(p.name, ProjectReader(p.path))

    return FleetStreamHub(
        lambda: list(projects_list),
        lambda p: viewmodels.build_project_card(p, reader(p), "0.9.0"),
        lambda cur: viewmodels.build_fleet(cur, "0.9.0", readers={p.name: reader(p) for p in cur}),
        viewmodels.build_event_item,
        poll_interval=0.05,
    )


def _drain(sub):
    out = []
    while not sub.queue.empty():
        out.append(sub.queue.get_nowait())
    return out


def test_hub_appended_event_pushed_not_history(fleet):
    """Scenario: fleet-web-ui / Live updates over SSE / Appended event is pushed"""
    projs = to_projects(fleet)
    hub = _hub(projs)
    hub.poll_once()
    sub = hub.subscribe()
    hub.poll_once()
    assert [m for m in _drain(sub) if m[0] == "fleet_event"] == []  # nothing from before connect
    p = fleet.projects[1]
    demo.append_event(p.path, "bead_failed", {"summary": "boom"}, clock=clock)
    hub.poll_once()
    events = [d for n, d in _drain(sub) if n == "fleet_event"]
    assert len(events) == 1
    assert events[0]["project"] == p.name
    assert events[0]["severity"] == "error" and events[0]["summary"] == "boom"


def test_hub_partial_and_malformed_lines(fleet):
    """Scenario: fleet-web-ui / Live updates over SSE / Partial and malformed lines"""
    projs = to_projects(fleet)
    hub = _hub(projs)
    hub.poll_once()
    sub = hub.subscribe()
    p = fleet.projects[1]
    full = json.dumps({"timestamp": clock().isoformat(), "event_type": "bead_started", "payload": {}})
    demo.append_raw(p.path, "{broken json\n" + full[:20])
    hub.poll_once()
    assert [m for m in _drain(sub) if m[0] == "fleet_event"] == []
    demo.append_raw(p.path, full[20:] + "\n")
    hub.poll_once()
    events = [d for n, d in _drain(sub) if n == "fleet_event"]
    assert [e["event_type"] for e in events] == ["bead_started"]


def test_hub_bead_status_change_updates_card(fleet):
    """Scenario: fleet-web-ui / Live updates over SSE / Bead status change updates the card"""
    projs = to_projects(fleet)
    hub = _hub(projs)
    hub.poll_once()
    sub = hub.subscribe()
    p = fleet.projects[0]
    hub.poll_once()
    assert [m for m in _drain(sub) if m[0] == "project_updated"] == []  # unchanged → silent
    demo.set_bead_status(p.path, p.bead_ids[3], "blocked", clock=clock)
    hub.poll_once()
    updates = [d for n, d in _drain(sub) if n == "project_updated"]
    assert len(updates) == 1 and updates[0]["name"] == p.name
    assert updates[0]["bead_counts"]["blocked"] == 2
    # project directory removed → health missing
    demo.remove_project_dir(p.path)
    hub.poll_once()
    updates = [d for n, d in _drain(sub) if n == "project_updated"]
    assert updates and updates[-1]["health"] == "missing"


def test_hub_registry_change(fleet, tmp_path):
    """Scenario: fleet-web-ui / Live updates over SSE / Registry change"""
    projs = to_projects(fleet)[:2]
    hub = _hub(projs)
    hub.poll_once()
    sub = hub.subscribe()
    hub.poll_once()
    assert [m for m in _drain(sub) if m[0] == "fleet_changed"] == []
    extra = to_projects(fleet)[2]
    projs.append(Project(extra.name, extra.path, extra.tags))
    hub.poll_once()
    changed = [d for n, d in _drain(sub) if n == "fleet_changed"]
    assert len(changed) == 1
    assert [c["name"] for c in changed[0]["projects"]] == [p.name for p in projs]
    assert extra.name in sub.tails  # new project's log is now tailed


def test_hub_truncated_log(fleet):
    """Scenario: fleet-web-ui / Live updates over SSE / Truncated log"""
    projs = to_projects(fleet)
    hub = _hub(projs)
    hub.poll_once()
    sub = hub.subscribe()
    p = fleet.projects[1]
    demo.append_event(p.path, "bead_started", {"summary": "one"}, clock=clock)
    hub.poll_once()
    assert len([m for m in _drain(sub) if m[0] == "fleet_event"]) == 1
    demo.truncate_events(p.path)
    hub.poll_once()
    assert [m for m in _drain(sub) if m[0] == "fleet_event"] == []  # no crash, nothing resent
    demo.append_event(p.path, "bead_started", {"summary": "two"}, clock=clock)
    hub.poll_once()
    events = [d for n, d in _drain(sub) if n == "fleet_event"]
    assert [e["summary"] for e in events] == ["two"]


def test_format_sse_single_line_json():
    frame = format_sse("fleet_event", {"a": "x\ny"})
    assert frame.startswith("event: fleet_event\ndata: ") and frame.endswith("\n\n")
    assert frame.count("\n") == 3
    assert json.loads(frame.split("data: ")[1]) == {"a": "x\ny"}


# ---- SSE endpoint over a real server ----------------------------------------
# TestClient buffers streaming bodies, so the stream is exercised with uvicorn.

class SSE:
    """Incremental SSE parser over one httpx streaming response."""

    def __init__(self, resp):
        self._lines = resp.iter_lines()
        self.frames: list[dict] = []
        self._cur: dict = {}

    def until(self, pred, max_lines=1000):
        for _ in range(max_lines):
            if pred(self.frames):
                break
            line = next(self._lines)
            if line == "":
                if self._cur:
                    self.frames.append(self._cur)
                    self._cur = {}
            elif line.startswith(":"):
                self.frames.append({"comment": line[1:].strip()})
            else:
                k, _, v = line.partition(": ")
                self._cur[k] = v
        return self.frames

    def has(self, key, value):
        return lambda f: any(x.get(key) == value for x in f)


def _wait_for(pred, timeout=5.0):
    import time

    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    return pred()


def _serve(fleet, factory, **kw):
    projs = to_projects(fleet)
    app = create_app(lambda: projs, installed_version="0.9.0", poll_interval=0.05,
                     heartbeat_interval=kw.pop("heartbeat_interval", 0.2), **kw)
    return factory(app)


def test_sse_endpoint_delivers_appended_event(fleet, live_server_factory):
    import httpx

    srv = _serve(fleet, live_server_factory)
    p = fleet.projects[1]
    demo.append_event(p.path, "bead_started", {"summary": "before-connect"}, clock=clock)
    with httpx.Client(timeout=10) as c:
        with c.stream("GET", srv.url + "/api/stream") as resp:
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/event-stream")
            sse = SSE(resp)
            sse.until(sse.has("comment", "connected"))
            assert _wait_for(lambda: srv.app.state.hub.subscriber_count == 1)
            demo.append_event(p.path, "bead_blocked", {"summary": "sse-live"}, clock=clock)
            frames = sse.until(sse.has("event", "fleet_event"))
    events = [json.loads(x["data"]) for x in frames if x.get("event") == "fleet_event"]
    assert [e["summary"] for e in events] == ["sse-live"]  # not the pre-connect one
    assert events[0]["severity"] == "warning"
    assert _wait_for(lambda: srv.app.state.hub.subscriber_count == 0)


def test_sse_heartbeat_and_card_update(fleet, live_server_factory):
    import httpx

    srv = _serve(fleet, live_server_factory, heartbeat_interval=0.15)
    p = fleet.projects[0]
    with httpx.Client(timeout=10) as c:
        with c.stream("GET", srv.url + "/api/stream") as resp:
            sse = SSE(resp)
            frames = sse.until(sse.has("comment", "ping"))
            assert any(x.get("comment") == "ping" for x in frames)
            demo.set_bead_status(p.path, p.bead_ids[3], "blocked", clock=clock)
            frames = sse.until(sse.has("event", "project_updated"))
    upd = next(x for x in frames if x.get("event") == "project_updated")
    assert json.loads(upd["data"])["name"] == p.name


def test_sse_registry_change_event(fleet, live_server_factory):
    import httpx

    projs = to_projects(fleet)[:2]
    app = create_app(lambda: projs, installed_version="0.9.0", poll_interval=0.05,
                     heartbeat_interval=0.2)
    srv = live_server_factory(app)
    with httpx.Client(timeout=10) as c:
        with c.stream("GET", srv.url + "/api/stream") as resp:
            sse = SSE(resp)
            sse.until(sse.has("comment", "connected"))
            projs.append(to_projects(fleet)[2])
            frames = sse.until(sse.has("event", "fleet_changed"))
    body = json.loads(next(x for x in frames if x.get("event") == "fleet_changed")["data"])
    assert len(body["projects"]) == 3


def test_sse_post_rejected(fleet):
    client = TestClient(create_app(lambda: to_projects(fleet)))
    assert client.post("/api/stream").status_code == 405


# ---- live session events ---------------------------------------------------

class _FakeRegistry:
    def __init__(self):
        self.counts = {}
        self.calls = []

    def refresh(self, project, session_id):
        self.calls.append((project, session_id))
        return {"summary": {"event_count": self.counts.get(session_id, 0)}}


def _live_hub(tmp_path, registry, last_request=None, now=lambda: 100.0):
    proj = Project(name="p", path=str(tmp_path), tags=())
    log = tmp_path / ".takt" / "logs"
    log.mkdir(parents=True)
    path = log / "events.jsonl"
    path.write_text("")
    hub = FleetStreamHub(
        lambda: [proj], lambda p: {"name": p.name}, lambda ps: {}, lambda n, r: r,
        live_registry=registry, live_seed=lambda p: [],
        live_last_request=lambda: last_request, clock=now,
    )
    return hub, path


def _append(path, etype, **payload):
    with path.open("a") as fh:
        fh.write(json.dumps({"timestamp": "2026-01-01T00:00:00+00:00",
                             "event_type": etype, "payload": payload}) + "\n")


def _drain_live(sub):
    out = []
    while not sub.queue.empty():
        out.append(sub.queue.get_nowait())
    return [(e, d) for e, d in out if e.startswith("live_")]


def test_live_started_updated_ended(tmp_path):
    reg = _FakeRegistry()
    hub, path = _live_hub(tmp_path, reg)
    hub._prime(hub._projects())
    sub = hub.subscribe()
    _append(path, "agent_session_started", bead_id="B-1", session_id="S1")
    reg.counts["S1"] = 3
    hub.poll_once()
    assert _drain_live(sub) == [
        ("live_session_started", {"project": "p", "bead_id": "B-1", "session_id": "S1"}),
        ("live_session_updated",
         {"project": "p", "bead_id": "B-1", "session_id": "S1", "event_count": 3}),
    ]
    hub.poll_once()  # no new events: no update
    assert _drain_live(sub) == []
    reg.counts["S1"] = 5
    hub.poll_once()
    hub.poll_once()
    assert _drain_live(sub) == [
        ("live_session_updated",
         {"project": "p", "bead_id": "B-1", "session_id": "S1", "event_count": 5})]
    _append(path, "trace_captured", bead_id="B-1", session_id="S1", seq=1)
    hub.poll_once()
    assert _drain_live(sub) == [
        ("live_session_ended", {"project": "p", "bead_id": "B-1", "session_id": "S1"})]
    calls = len(reg.calls)
    hub.poll_once()
    assert len(reg.calls) == calls


def test_live_ended_by_bead_event(tmp_path):
    hub, path = _live_hub(tmp_path, _FakeRegistry())
    hub._prime(hub._projects())
    sub = hub.subscribe()
    _append(path, "agent_session_started", bead_id="B-1", session_id="S1")
    _append(path, "bead_blocked", bead_id="B-1")
    hub.poll_once()
    assert [e for e, _ in _drain_live(sub)] == ["live_session_started", "live_session_ended"]


def test_live_transcripts_gated_without_demand(tmp_path):
    reg = _FakeRegistry()
    hub, path = _live_hub(tmp_path, reg, last_request=None)
    hub._prime(hub._projects())
    _append(path, "agent_session_started", bead_id="B-1", session_id="S1")
    hub.poll_once()  # no subscribers, no live request
    assert reg.calls == []
    assert not hub.live_demand()
    sub = hub.subscribe()  # an SSE client turns demand on
    reg.counts["S1"] = 1
    hub.poll_once()
    assert reg.calls == [("p", "S1")]
    assert _drain_live(sub)[0][0] == "live_session_updated"


def test_live_demand_window(tmp_path):
    t = {"now": 100.0}
    hub, _ = _live_hub(tmp_path, _FakeRegistry(), last_request=50.0, now=lambda: t["now"])
    assert hub.live_demand()  # 50s ago
    t["now"] = 111.0  # 61s ago
    assert not hub.live_demand()
