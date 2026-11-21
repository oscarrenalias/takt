"""Layer 1 (ADR-f42387c6): paged activity, paged beads and scheduler run APIs.

Everything runs against synthetic projects written under ``tmp_path``.
"""
from __future__ import annotations

import base64
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from agent_takt_fleet.models import Project
from agent_takt_fleet.web import readers, viewmodels
from agent_takt_fleet.web.app import create_app
from agent_takt_fleet.web.readers import (
    EventPosition,
    InvalidCursor,
    decode_cursor,
    encode_cursor,
    read_events_page,
    read_recent_events,
)

T0 = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)


def ts(seconds: float) -> str:
    return (T0 + timedelta(seconds=seconds)).isoformat()


def events_path(root: Path) -> Path:
    return root / ".takt" / "logs" / "events.jsonl"


def write_events(root: Path, records: list, *, raw: bool = False) -> Path:
    path = events_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = records if raw else [json.dumps(r) for r in records]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def ev(seq: int, at: float | None = None, kind: str = "bead_completed", **payload) -> dict:
    return {"timestamp": ts(seq if at is None else at), "event_type": kind,
            "payload": {"seq": seq, **payload}}


def make_project(tmp_path: Path, name: str) -> Project:
    root = tmp_path / name
    root.mkdir(parents=True, exist_ok=True)
    return Project(name=name, path=root, tags=())


def client_for(projects: list[Project]) -> TestClient:
    return TestClient(create_app(lambda: projects, installed_version="0.9.0", poll_interval=0.05))


def walk_events(client: TestClient, **params) -> list[list[dict]]:
    """Follow next_cursor until null; return the pages."""
    pages, cursor = [], None
    for _ in range(200):
        q = dict(params)
        if cursor:
            q["cursor"] = cursor
        r = client.get("/api/events", params=q)
        assert r.status_code == 200, r.text
        body = r.json()
        pages.append(body["events"])
        cursor = body["next_cursor"]
        if cursor is None:
            return pages
    raise AssertionError("cursor never terminated")


# ------------------------------------------------------------ reader: pages


def test_first_page_bounded_read_on_multi_mb_file(tmp_path):
    """Scenario: fleet-web-ui / Paged activity API / First page reads only the end of the log"""
    root = tmp_path / "p"
    filler = "x" * 400
    records = [ev(i, kind="bead_started", pad=filler) for i in range(9000)]
    path = write_events(root, records)
    size = path.stat().st_size
    assert size > 3_000_000
    page = read_events_page(root, 25)
    assert [e["payload"]["seq"] for e in page.events] == list(range(8999, 8974, -1))
    assert page.bytes_read < size // 10
    assert page.bytes_read <= 4 * readers.EVENT_BLOCK_BYTES
    assert page.next_position is not None


def test_api_first_page_does_not_read_whole_log(tmp_path, monkeypatch):
    """Scenario: fleet-web-ui / Paged activity API / First page reads only the end of the log"""
    proj = make_project(tmp_path, "big")
    pad = "y" * 500
    path = write_events(proj.path, [ev(i, pad=pad) for i in range(8000)])
    assert path.stat().st_size > 4_000_000
    spy: list[int] = []
    real = viewmodels.read_events_page

    def recording(*a, **k):
        page = real(*a, **k)
        spy.append(page.bytes_read)
        return page

    monkeypatch.setattr(viewmodels, "read_events_page", recording)
    body = client_for([proj]).get("/api/events").json()
    assert [e["payload"]["seq"] for e in body["events"]] == list(range(7999, 7974, -1))
    assert body["next_cursor"] is not None
    assert spy and max(spy) < path.stat().st_size // 10


@pytest.mark.parametrize("garbage", [False, True])
@pytest.mark.parametrize("same_ts", [False, True])
def test_full_walk_small_blocks(tmp_path, garbage, same_ts):
    root = tmp_path / "p"
    lines = []
    for i in range(40):
        lines.append(json.dumps(ev(i, at=0 if same_ts else i)))
        if garbage and i % 5 == 0:
            lines += ["", "not json {", json.dumps([1, 2]), "   "]
    write_events(root, lines, raw=True)
    seen, pos = [], None
    for _ in range(100):
        page = read_events_page(root, 7, pos, block_size=64)
        seen += [e["payload"]["seq"] for e in page.events]
        if page.next_position is None:
            break
        pos = page.next_position
    assert seen == list(range(39, -1, -1))


def test_empty_and_missing_files(tmp_path):
    assert read_events_page(tmp_path / "nope", 25).events == []
    assert read_events_page(tmp_path / "nope", 25).next_position is None
    root = tmp_path / "p"
    write_events(root, [], raw=True)
    events_path(root).write_text("", encoding="utf-8")
    page = read_events_page(root, 25)
    assert page.events == [] and page.next_position is None
    assert read_events_page(root, 0).events == []


def test_line_longer_than_block(tmp_path):
    root = tmp_path / "p"
    write_events(root, [ev(0), ev(1, blob="z" * 5000), ev(2)])
    seen, pos = [], None
    for _ in range(10):
        page = read_events_page(root, 1, pos, block_size=128)
        seen += [e["payload"]["seq"] for e in page.events]
        if page.next_position is None:
            break
        pos = page.next_position
    assert seen == [2, 1, 0]


def test_inode_change_and_offset_beyond_size_reset(tmp_path):
    root = tmp_path / "p"
    path = write_events(root, [ev(i) for i in range(10)])
    first = read_events_page(root, 3)
    assert first.next_position and not first.reset
    wrong_inode = EventPosition(first.next_position.offset, first.next_position.identity + 1)
    page = read_events_page(root, 3, wrong_inode)
    assert page.reset is True
    assert [e["payload"]["seq"] for e in page.events] == [9, 8, 7]  # restarted at newest
    too_far = EventPosition(path.stat().st_size + 100, first.next_position.identity)
    assert read_events_page(root, 3, too_far).reset is True
    ok = read_events_page(root, 3, first.next_position)
    assert ok.reset is False and [e["payload"]["seq"] for e in ok.events] == [6, 5, 4]


def test_cursor_round_trip_and_invalid():
    pos = {"a": EventPosition(10, 3), "b": EventPosition(0, 0)}
    assert decode_cursor(encode_cursor(pos)) == pos

    def enc(obj) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    bad = [
        "not-a-cursor", "!!!", "",
        enc({"v": 2, "p": {}}),                      # wrong version
        enc({"p": {}}),                              # missing version
        enc([1, 2]),                                 # wrong shape
        enc({"v": 1, "p": []}),                      # positions not a dict
        enc({"v": 1, "p": {"a": [1]}}),              # wrong arity
        enc({"v": 1, "p": {"a": [-1, 0]}}),          # negative
        enc({"v": 1, "p": {"a": [True, 0]}}),        # bool
        enc({"v": 1, "p": {"a": ["1", 0]}}),         # not int
    ]
    for cursor in bad:
        with pytest.raises(InvalidCursor):
            decode_cursor(cursor)


def test_read_recent_events_oldest_first_and_tolerant(tmp_path):
    root = tmp_path / "p"
    write_events(root, [json.dumps(ev(i)) for i in range(10)] + ["junk"], raw=True)
    got = read_recent_events(root, 4)
    assert [e["payload"]["seq"] for e in got] == [6, 7, 8, 9]
    assert read_recent_events(tmp_path / "missing") == []


# ------------------------------------------------------- API: activity paging


@pytest.fixture
def two_projects(tmp_path):
    """70 events over two projects; many share timestamps (every 3 events)."""
    a, b = make_project(tmp_path, "alpha"), make_project(tmp_path, "beta")
    ra = [ev(i, at=i // 3, who="alpha") for i in range(0, 40)]
    rb = [ev(i, at=i // 3, who="beta") for i in range(0, 30)]
    write_events(a.path, ra)
    write_events(b.path, rb)
    return [a, b]


@pytest.mark.parametrize("limit", [None, 1, 7, 10, 25, 69, 70, 200])
def test_paging_without_gaps_or_duplicates(two_projects, limit):
    """Scenario: fleet-web-ui / Paged activity API / Paging without gaps or duplicates"""
    client = client_for(two_projects)
    params = {} if limit is None else {"limit": limit}
    pages = walk_events(client, **params)
    flat = [e for page in pages for e in page]
    ids = [(e["project"], e["payload"]["seq"]) for e in flat]
    assert len(ids) == 70 and len(set(ids)) == 70
    stamps = [e["timestamp"] for e in flat]
    assert stamps == sorted(stamps, reverse=True)
    size = 25 if limit is None else min(limit, 200)
    assert all(len(p) == size for p in pages[:-1])
    assert client.get("/api/events", params=params).json()["limit"] == size


def test_next_cursor_null_once_consumed_and_older_remain_beyond_first_page(two_projects):
    client = client_for(two_projects)
    first = client.get("/api/events", params={"limit": 25}).json()
    assert len(first["events"]) == 25 and first["next_cursor"]
    pages = walk_events(client, limit=200)
    assert len(pages) == 1 and len(pages[0]) == 70
    r = client.get("/api/events", params={"limit": 200}).json()
    assert r["next_cursor"] is None


def test_events_defaults_and_clamps(two_projects):
    client = client_for(two_projects)
    assert client.get("/api/events").json()["limit"] == 25
    assert len(client.get("/api/events").json()["events"]) == 25
    assert client.get("/api/events", params={"limit": 500}).json()["limit"] == 200
    assert client.get("/api/events", params={"limit": "abc"}).json()["limit"] == 25
    assert client.get("/api/events", params={"limit": 0}).json()["limit"] == 1


def test_project_filtered_paging(two_projects):
    client = client_for(two_projects)
    pages = walk_events(client, project="beta", limit=8)
    flat = [e for p in pages for e in p]
    assert {e["project"] for e in flat} == {"beta"}
    assert sorted(e["payload"]["seq"] for e in flat) == list(range(30))


def test_partial_consumption_resumes_in_the_right_place(tmp_path):
    """Small limit across interleaved projects: a page takes unevenly from each project."""
    a, b = make_project(tmp_path, "a"), make_project(tmp_path, "b")
    write_events(a.path, [ev(i, at=i * 2, who="a") for i in range(15)])      # even seconds
    write_events(b.path, [ev(i, at=i * 2 + 1, who="b") for i in range(15)])  # odd seconds
    client = client_for([a, b])
    for limit in (1, 2, 3, 5):
        flat = [e for page in walk_events(client, limit=limit) for e in page]
        keys = [(e["project"], e["payload"]["seq"]) for e in flat]
        assert len(keys) == 30 and len(set(keys)) == 30
        assert [e["timestamp"] for e in flat] == sorted((e["timestamp"] for e in flat), reverse=True)


def test_missing_and_empty_events_contribute_nothing(tmp_path):
    a, b, c = (make_project(tmp_path, n) for n in ("a", "b", "c"))
    write_events(a.path, [ev(i) for i in range(5)])
    events_path(b.path).parent.mkdir(parents=True)
    events_path(b.path).write_text("")
    flat = [e for p in walk_events(client_for([a, b, c]), limit=2) for e in p]
    assert {e["project"] for e in flat} == {"a"} and len(flat) == 5


@pytest.mark.parametrize("kind", ["garbage", "wrong_version", "bad_positions", "empty_positions_shape"])
def test_malformed_cursor_is_400(two_projects, kind):
    """Scenario: fleet-web-ui / Paged activity API / Malformed cursor"""
    def enc(obj) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    cursor = {
        "garbage": "not-a-cursor",
        "wrong_version": enc({"v": 99, "p": {"alpha": [0, 0]}}),
        "bad_positions": enc({"v": 1, "p": {"alpha": [-5, "x"]}}),
        "empty_positions_shape": enc({"v": 1, "p": [1, 2, 3]}),
    }[kind]
    r = TestClient(create_app(lambda: two_projects, installed_version="0.9.0")).get(
        "/api/events", params={"cursor": cursor})
    assert r.status_code == 400
    body = r.json()
    assert "error" in body and isinstance(body["error"], str)


def test_viewmodel_raises_invalid_cursor(two_projects):
    with pytest.raises(InvalidCursor):
        viewmodels.build_events(two_projects, cursor="not-a-cursor")


# ------------------------------------------------------------- API: bead list


def write_bead(root: Path, bead_id: str, agent: str, created: str, status: str = "done", **extra) -> None:
    d = root / ".takt" / "beads"
    d.mkdir(parents=True, exist_ok=True)
    bead = {
        "bead_id": bead_id, "title": f"Title {bead_id}", "agent_type": agent, "status": status,
        "feature_root_id": extra.pop("feature_root_id", "B-root"),
        "execution_history": [{"timestamp": created, "event": "created", "summary": "c", "details": {}}],
        **extra,
    }
    (d / f"{bead_id}.json").write_text(json.dumps(bead), encoding="utf-8")


@pytest.fixture
def bead_project(tmp_path):
    proj = make_project(tmp_path, "beady")
    for i in range(60):
        agent = "developer" if i % 2 == 0 else "tester"
        status = "done" if i % 3 else "blocked"
        labels = ["x"] if i % 4 == 0 else []
        root = "B-root" if i < 40 else "B-other"
        write_bead(proj.path, f"B-{i:03d}", agent, ts(i * 10), status,
                   labels=labels, feature_root_id=root)
    return proj


def test_bead_list_newest_first_with_filters_paging(bead_project):
    """Scenario: fleet-web-ui / Paged bead list / Newest first with filters"""
    client = client_for([bead_project])
    base = f"/api/projects/{bead_project.name}/beads"
    first = client.get(base, params={"agent": "developer"}).json()
    assert first["total"] == 30 and len(first["beads"]) == 25 and first["limit"] == 25
    ids = [b["bead_id"] for b in first["beads"]]
    assert ids == sorted(ids, reverse=True)
    assert all(b["agent_type"] == "developer" for b in first["beads"])
    assert ids[0] == "B-058"
    assert first["next_cursor"]
    second = client.get(base, params={"agent": "developer", "cursor": first["next_cursor"]}).json()
    assert len(second["beads"]) == 5 and second["total"] == 30
    assert second["next_cursor"] is None
    all_ids = ids + [b["bead_id"] for b in second["beads"]]
    assert len(set(all_ids)) == 30 and all_ids == sorted(all_ids, reverse=True)


def test_bead_list_order_oldest_ties_and_limits(tmp_path, bead_project):
    client = client_for([bead_project])
    base = f"/api/projects/{bead_project.name}/beads"
    oldest = client.get(base, params={"order": "oldest", "limit": 5}).json()
    assert [b["bead_id"] for b in oldest["beads"]] == [f"B-{i:03d}" for i in range(5)]
    assert client.get(base, params={"limit": 999}).json()["limit"] == 200
    assert client.get(base, params={"limit": 0}).json()["limit"] == 1
    assert client.get(base, params={"limit": "abc"}).json()["limit"] == 25
    # identical timestamps: ties broken by bead id descending (ascending when oldest)
    proj = make_project(tmp_path, "ties")
    for n in ("B-a", "B-c", "B-b"):
        write_bead(proj.path, n, "developer", ts(0))
    tc = client_for([proj])
    tb = f"/api/projects/{proj.name}/beads"
    assert [b["bead_id"] for b in tc.get(tb).json()["beads"]] == ["B-c", "B-b", "B-a"]
    assert [b["bead_id"] for b in tc.get(tb, params={"order": "oldest"}).json()["beads"]] == ["B-a", "B-b", "B-c"]


def test_bead_filters_apply_before_paging(bead_project):
    client = client_for([bead_project])
    base = f"/api/projects/{bead_project.name}/beads"
    body = client.get(base, params={"status": "blocked", "label": "x", "feature_root": "B-root",
                                    "limit": 3}).json()
    expected = [f"B-{i:03d}" for i in range(40) if i % 3 == 0 and i % 4 == 0]
    assert body["total"] == len(expected)
    got = [b["bead_id"] for b in body["beads"]]
    assert got == sorted(expected, reverse=True)[:3]
    q = client.get(base, params={"q": "B-05", "limit": 4}).json()
    assert q["total"] == 10 and len(q["beads"]) == 4 and q["next_cursor"]


def test_bead_list_bad_cursor_and_order_and_unknown_project(bead_project):
    client = client_for([bead_project])
    base = f"/api/projects/{bead_project.name}/beads"
    r = client.get(base, params={"cursor": "garbage"})
    assert r.status_code == 400 and "error" in r.json()
    # an events-style cursor has no "beads" key
    events_cursor = encode_cursor({"alpha": EventPosition(3, 1)})
    r = client.get(base, params={"cursor": events_cursor})
    assert r.status_code == 400 and "error" in r.json()
    r = client.get(base, params={"order": "sideways"})
    assert r.status_code == 400 and "error" in r.json()
    assert client.get("/api/projects/nope/beads").status_code == 404


# ------------------------------------------------------------------- runs API

RUN = "SR-0a1b2c3d"


def run_event(at: float, kind: str, **payload) -> dict:
    return {"timestamp": ts(at), "event_type": kind, "payload": {"run_id": RUN, **payload}}


def write_run(root: Path, *, finished: bool = True, extra: list | None = None) -> None:
    records = [
        run_event(0, "scheduler_run_started", pid=1, hostname="h", runner="claude",
                  max_workers=2, takt_version="0.9.0", feature_root_id=None),
        run_event(1, "scheduler_cycle_started", cycle_index=1),
        run_event(2, "bead_started", bead_id="B-1-dev", agent_type="developer", title="Dev one"),
        run_event(10, "bead_completed", bead_id="B-1-dev", agent_type="developer",
                  duration_seconds=8.0, cost_usd=0.5, created_bead_ids=[]),
        run_event(11, "bead_blocked", bead_id="B-1-test", agent_type="tester",
                  duration_seconds=3.0, cost_usd=0.25,
                  created_bead_ids=["B-1-test-corrective"]),
        run_event(12, "scheduler_cycle_completed", cycle_index=1, started_count=2,
                  completed_count=1, blocked_count=1, deferred_count=0),
        run_event(13, "scheduler_cycle_started", cycle_index=2),
        run_event(14, "bead_started", bead_id="B-1-test-corrective", agent_type="developer"),
        run_event(20, "bead_completed", bead_id="B-1-test-corrective", agent_type="developer",
                  duration_seconds=6.0),  # no cost_usd -> null
        run_event(21, "scheduler_cycle_completed", cycle_index=2, started_count=1,
                  completed_count=1, blocked_count=0, deferred_count=2),
        # another run's event interleaved must be ignored
        {"timestamp": ts(22), "event_type": "bead_completed",
         "payload": {"run_id": "SR-ffffffff", "bead_id": "B-other", "cost_usd": 99}},
    ]
    if finished:
        records.append(run_event(30, "scheduler_run_finished", pid=1, exit_reason="quiescence",
                                 cycles=2, started_count=3, completed_count=2, blocked_count=1,
                                 correctives_created_count=1, deferred_count=2, error=None))
    records += extra or []
    write_events(root, records)


@pytest.fixture
def run_project(tmp_path):
    proj = make_project(tmp_path, "runny")
    write_run(proj.path)
    return proj


def test_run_detail_cycles_beads_correctives_and_cost(run_project):
    """Scenario: fleet-web-ui / Scheduler run view / Run detail"""
    d = viewmodels.build_run_detail(run_project, RUN, readers.ProjectReader(run_project.path))
    assert d is not None
    assert d["run"]["run_id"] == RUN and d["run"]["status"] == "quiescence"
    assert d["run"]["duration_seconds"] == 30.0
    cycles = d["cycles"]
    assert [c["index"] for c in cycles] == [1, 2]
    assert (cycles[0]["started_count"], cycles[0]["completed_count"], cycles[0]["blocked_count"]) == (2, 1, 1)
    assert (cycles[1]["completed_count"], cycles[1]["deferred_count"]) == (1, 2)
    beads = {b["bead_id"]: b for b in d["beads"]}
    assert set(beads) == {"B-1-dev", "B-1-test", "B-1-test-corrective"}
    assert beads["B-1-dev"]["outcome"] == "completed" and beads["B-1-dev"]["cost_usd"] == 0.5
    assert beads["B-1-dev"]["title"] == "Dev one" and beads["B-1-dev"]["cycle_index"] == 1
    assert beads["B-1-test"]["outcome"] == "blocked" and beads["B-1-test"]["cost_usd"] == 0.25
    assert beads["B-1-test-corrective"]["cost_usd"] is None
    assert beads["B-1-test-corrective"]["cycle_index"] == 2
    assert d["correctives_created"] == ["B-1-test-corrective"]
    assert d["total_cost_usd"] == pytest.approx(0.75)


def test_run_detail_via_route(run_project):
    """Scenario: fleet-web-ui / Scheduler run view / Run detail"""
    client = client_for([run_project])
    r = client.get(f"/api/projects/{run_project.name}/runs/{RUN}")
    assert r.status_code == 200
    body = r.json()
    assert {"run", "cycles", "beads", "correctives_created", "total_cost_usd"} <= set(body)
    assert body["total_cost_usd"] == pytest.approx(0.75)
    assert len(body["cycles"]) == 2 and len(body["beads"]) == 3


def test_run_detail_no_cost_and_retried_bead(tmp_path):
    proj = make_project(tmp_path, "nocost")
    write_events(proj.path, [
        run_event(0, "scheduler_run_started", pid=1, hostname="h"),
        run_event(1, "bead_started", bead_id="B-x", agent_type="developer"),
        run_event(2, "bead_blocked", bead_id="B-x", agent_type="developer"),
        run_event(3, "bead_started", bead_id="B-x", agent_type="developer"),
        run_event(4, "bead_completed", bead_id="B-x", agent_type="developer"),
    ])
    d = viewmodels.build_run_detail(proj, RUN)
    assert d is not None
    assert d["total_cost_usd"] is None
    assert [b["outcome"] for b in d["beads"]] == ["completed"]  # last outcome wins
    assert d["beads"][0]["cycle_index"] is None  # no cycle events


def test_runs_list_paging_newest_first_and_duration(tmp_path):
    proj = make_project(tmp_path, "many")
    records = []
    for i in range(30):
        rid = f"SR-{i:08x}"
        start = i * 100
        records.append({"timestamp": ts(start), "event_type": "scheduler_run_started",
                        "payload": {"run_id": rid, "pid": 1, "hostname": "h"}})
        if i != 29:  # the newest has no finish event and no marker: stale, duration None
            records.append({"timestamp": ts(start + 50), "event_type": "scheduler_run_finished",
                            "payload": {"run_id": rid, "pid": 1, "exit_reason": "quiescence", "cycles": 1}})
    write_events(proj.path, records)
    client = client_for([proj])
    base = f"/api/projects/{proj.name}/runs"
    first = client.get(base, params={"limit": 10}).json()
    assert first["total"] == 30 and len(first["runs"]) == 10 and first["next_cursor"]
    assert first["runs"][0]["run_id"] == "SR-0000001d"
    assert first["runs"][0]["duration_seconds"] is None
    assert first["runs"][1]["duration_seconds"] == 50.0
    ids = [r["run_id"] for r in first["runs"]]
    cursor, last = first["next_cursor"], None
    while cursor:
        page = client.get(base, params={"limit": 10, "cursor": cursor}).json()
        ids += [r["run_id"] for r in page["runs"]]
        cursor, last = page["next_cursor"], page
    assert len(ids) == 30 and len(set(ids)) == 30 and ids == sorted(ids, reverse=True)
    assert last["next_cursor"] is None


@pytest.mark.parametrize("exit_reason", ["quiescence", "max_cycles", "interrupted", "terminated", "error"])
def test_runs_list_finished_exit_reasons(tmp_path, exit_reason):
    proj = make_project(tmp_path, "x")
    write_events(proj.path, [
        run_event(0, "scheduler_run_started", pid=1, hostname="h"),
        run_event(5, "scheduler_run_finished", pid=1, exit_reason=exit_reason, cycles=2),
    ])
    rows = readers.list_project_runs(proj.name, proj.path)
    assert [r["status"] for r in rows] == [exit_reason]
    assert rows[0]["finished_at"] and rows[0]["cycles"] == 2


def test_runs_list_stale_and_crashed_null_counts(tmp_path):
    stale = make_project(tmp_path, "stale")
    write_events(stale.path, [run_event(0, "scheduler_run_started", pid=1, hostname="h")])
    rows = readers.list_project_runs(stale.name, stale.path)
    assert rows[0]["status"] == "stale" and rows[0]["finished_at"] is None
    crashed = make_project(tmp_path, "crashed")
    write_events(crashed.path, [
        run_event(0, "scheduler_run_started", pid=1, hostname="h"),
        run_event(5, "scheduler_run_finished", pid=1, exit_reason="crashed"),
    ])
    row = readers.list_project_runs(crashed.name, crashed.path)[0]
    assert row["status"] == "crashed" and row["cycles"] is None and row["started_count"] is None


def test_runs_list_running_with_live_marker(fleet):
    """Running run: live marker and no finish event; finished runs listed alongside."""
    full = fleet.projects[0]
    rows = readers.list_project_runs(full.name, full.path)
    by_status = {r["status"] for r in rows}
    assert "running" in by_status and len(by_status) > 1
    running = next(r for r in rows if r["status"] == "running")
    assert running["finished_at"] is None


@pytest.mark.parametrize("bad", ["x", "SR-zz", "SR-ABCDEF01", "SR-0a1b2c3", "SR-0a1b2c3d0", "SR-0a1b2c3d\n", "sr-0a1b2c3d", ""])
def test_malformed_run_ids_return_none_without_reading(run_project, monkeypatch, bad):
    """Scenario: fleet-web-ui / Scheduler run view / Unknown run"""
    assert readers.is_valid_run_id(bad) is False
    opened: list = []
    real_open = Path.open
    monkeypatch.setattr(Path, "open", lambda self, *a, **k: (opened.append(self), real_open(self, *a, **k))[1])
    assert readers.read_run_events(run_project.path, bad) is None
    assert opened == []
    assert viewmodels.build_run_detail(run_project, bad) is None


def test_non_string_run_ids_are_rejected(run_project):
    bads: list = [None, 5, b"SR-0a1b2c3d", ["SR-0a1b2c3d"]]
    for bad in bads:
        assert readers.read_run_events(run_project.path, bad) is None
        assert viewmodels.build_run_detail(run_project, bad) is None


def test_read_run_events_order_malformed_and_missing(tmp_path):
    root = tmp_path / "p"
    lines = [
        json.dumps(run_event(1, "a")), "junk", json.dumps([1]),
        json.dumps({"timestamp": ts(2), "event_type": "norun", "payload": {}}),
        json.dumps({"timestamp": ts(3), "event_type": "str", "payload": "SR-0a1b2c3d"}),
        json.dumps(run_event(4, "b")),
        json.dumps({"timestamp": ts(5), "event_type": "other",
                    "payload": {"run_id": "SR-ffffffff", "note": RUN}}),
    ]
    write_events(root, lines, raw=True)
    got = readers.read_run_events(root, RUN)
    assert got is not None
    assert [e["event_type"] for e in got] == ["a", "b"]
    assert readers.read_run_events(tmp_path / "nolog", RUN) == []


def test_unknown_and_malformed_run_routes_404(run_project):
    """Scenario: fleet-web-ui / Scheduler run view / Unknown run"""
    client = client_for([run_project])
    name = run_project.name
    for rid in ("x", "SR-zz", "SR-ABCDEF01", "SR-deadbeef"):  # malformed x3, then valid-but-absent
        r = client.get(f"/api/projects/{name}/runs/{rid}")
        assert r.status_code == 404, rid
        assert "error" in r.json()
    r = client.get(f"/api/projects/nope/runs/{RUN}")
    assert r.status_code == 404
    assert client.get(f"/api/projects/{name}/runs").status_code == 200
    r = client.get(f"/api/projects/{name}/runs", params={"cursor": "garbage"})
    assert r.status_code == 400 and "error" in r.json()
    assert client.get("/api/projects/nope/runs").status_code == 404


def test_unknown_run_id_absent_from_log_is_none(run_project):
    """Scenario: fleet-web-ui / Scheduler run view / Unknown run"""
    assert viewmodels.build_run_detail(run_project, "SR-deadbeef") is None
    assert viewmodels.build_run_detail(run_project, "SR-zz") is None
    assert viewmodels.build_run_detail(run_project, "x") is None
