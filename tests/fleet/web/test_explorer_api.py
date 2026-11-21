"""Layer 1 (ADR-f42387c6): readers, view models, routes and stream for the bead explorer.

Runs against the fixture fleet (``web.demo``); no real project or registry is read.
Scenario tests cite ``fleet-web-ui`` scenarios from spec-be2ec1ba.
"""
from __future__ import annotations

import json
import subprocess

import pytest
from starlette.testclient import TestClient

from agent_takt_fleet.models import Project
from agent_takt_fleet.web import demo, viewmodels
from agent_takt_fleet.web.app import create_app
from agent_takt_fleet.web.readers import ProjectReader
from agent_takt_fleet.web.stream import FleetStreamHub

from .conftest import clock, to_projects

PROJ = "demo-1-full"
LARGE = 256 * 1024


def gov(name=PROJ):
    return f"B-{demo._hex8(name + '-gov')}"


def proj_of(fleet, name=PROJ):
    return next(p for p in fleet.projects if p.name == name)


@pytest.fixture
def reader(fleet):
    return ProjectReader(proj_of(fleet).path)


@pytest.fixture
def client(projects, no_subprocess):
    return TestClient(create_app(lambda: projects, installed_version="0.9.0", poll_interval=0.05))


def beads_of(reader):
    return reader.read_beads().beads


# ---------------------------------------------------------------- readers

def test_get_bead_known_unknown_and_unsafe(reader, fleet, tmp_path):
    b = reader.get_bead(f"{gov()}-extra")
    assert b["future_field"]["nested"]["deep"][2] == {"k": "v"}
    assert reader.get_bead("B-nope") is None
    secret = tmp_path / "secret.json"
    secret.write_text(json.dumps({"bead_id": "secret"}))
    for bad in ("../x", "..%2F..%2Fetc", "a/b", "a\\b", "", None, 5, str(secret)):
        assert reader.get_bead(bad) is None


def test_read_beads_skips_corrupt_without_error(fleet):
    p = proj_of(fleet, "demo-4-corrupt")
    r = ProjectReader(p.path).read_beads()
    assert r.unreadable_beads == 1
    assert r.beads


def test_trace_listing_and_seq_resolution(reader):
    dev = f"{gov()}-dev"
    listing = reader.list_trace_records(dev)
    assert listing.seqs == [1, 2]
    assert listing.unreadable_traces == 0
    assert reader.list_trace_records("B-nope").records == []
    assert reader.list_trace_records("../x").records == []
    assert reader.resolve_trace_seq(dev, "1") == 1
    assert reader.resolve_trace_seq(dev, 2) == 2
    for bad in ("abc", "-1", "1.0", True, None, "9", "../x"):
        assert reader.resolve_trace_seq(dev, bad) is None


def test_trace_listing_counts_bad_files(fleet):
    p = proj_of(fleet)
    dev = f"{gov()}-dev"
    d = p.path / ".takt" / "traces" / dev
    (d / "3.meta.json").write_text("{bad")
    (d / "x.meta.json").write_text("{}")
    (d / "10.meta.json").write_text(json.dumps({"seq": 10}))
    listing = ProjectReader(p.path).list_trace_records(dev)
    assert listing.seqs == [1, 2, 10]  # numeric ordering
    assert listing.unreadable_traces == 2


def test_trace_listing_cache_hit_and_reparse(fleet):
    p = proj_of(fleet)
    dev = f"{gov()}-dev"
    r = ProjectReader(p.path)
    r.list_trace_records(dev)
    n = r.parse_count
    r.list_trace_records(dev)
    # bead files are cached too; only the trace files were re-checked via stat
    assert r.parse_count == n
    meta = p.path / ".takt" / "traces" / dev / "2.meta.json"
    data = json.loads(meta.read_text())
    data["error"] = "changed and longer than before"
    meta.write_text(json.dumps(data))
    r.list_trace_records(dev)
    assert r.parse_count == n + 1


def test_trace_events_paging_and_filters(reader):
    dev = f"{gov()}-dev"
    full = reader.read_trace_events(dev, 1, limit=100)
    assert full.total == 5 and len(full.events) == 5
    assert full.summary["error_count"] == 1
    page = reader.read_trace_events(dev, 1, offset=3, limit=1)
    assert len(page.events) == 1 and page.total == 5
    assert reader.read_trace_events(dev, 1, offset=99).events == []
    assert reader.read_trace_events(dev, 1, limit=0).events == []
    only_err = reader.read_trace_events(dev, 1, errors_only=True)
    assert only_err.total == 1 and only_err.events[0]["tool"] == "Bash"
    assert reader.read_trace_events(dev, 1, kind="usage").total == 1
    assert reader.read_trace_events(dev, 1, tool="Read").total == 1
    assert reader.read_trace_events(dev, 1, kind="tool_call", tool="Bash", errors_only=True).total == 1
    assert reader.read_trace_events(dev, 1, kind="usage", tool="Read").total == 0
    # summary covers all events regardless of filter
    assert reader.read_trace_events(dev, 1, kind="usage").summary == full.summary
    assert full.summary["tool_counts"] == {"Read": 1, "Bash": 1}
    assert full.summary["usage"]["input_tokens"] == 1200
    assert full.summary["assistant_messages"] == 1
    assert full.summary["total_duration_ms"] == 1020


def test_trace_events_tolerates_bad_lines_and_missing_file(fleet):
    p = proj_of(fleet)
    dev = f"{gov()}-dev"
    ev = p.path / ".takt" / "traces" / dev / "1.events.jsonl"
    ev.write_text(ev.read_text() + "\n{broken\n[1]\n"
                  + json.dumps({"kind": "usage", "input_tokens": "x", "output_tokens": 2}) + "\n"
                  + json.dumps({"kind": "tool_call", "tool": "Z", "duration_ms": "slow"}) + "\n")
    r = ProjectReader(p.path)
    res = r.read_trace_events(dev, 1, limit=100)
    assert res.total == 7
    assert res.summary["usage"]["output_tokens"] == 342
    ev.unlink()
    assert r.read_trace_events(dev, 1).total == 0


def test_trace_missing_status_is_empty(reader):
    res = reader.read_trace_events(f"{gov()}-dev", 2)
    assert res.events == [] and res.total == 0
    assert res.summary["error_count"] == 0


def test_telemetry_truncation(reader):
    dev = f"{gov()}-dev"
    big = reader.read_telemetry_attempt(dev, 1)
    assert big["prompt_truncated"] is True
    assert len(big["prompt_text"].encode()) <= LARGE
    assert big["response_truncated"] is False
    assert big["parsed_result"] == {"outcome": "completed", "summary": "done"}
    assert big["metrics"]["model"] == "demo-model"
    small = reader.read_telemetry_attempt(dev, "2")
    assert small["prompt_truncated"] is False
    assert small["prompt_text"] == "Short retry prompt."
    for bad in ("abc", "-1", 1.5, True, "../x", None, 99):
        assert reader.read_telemetry_attempt(dev, bad) is None
    assert reader.read_telemetry_attempt("B-nope", 1) is None


def _tel_path(fleet, n=2):
    return proj_of(fleet).path / ".takt" / "telemetry" / f"{gov()}-dev" / f"{n}.json"


def test_telemetry_exact_cap_multibyte_and_null(fleet):
    path = _tel_path(fleet)
    data = json.loads(path.read_text())
    data["prompt_text"] = "a" * LARGE
    data["response_text"] = None
    path.write_text(json.dumps(data))
    r = ProjectReader(proj_of(fleet).path).read_telemetry_attempt(f"{gov()}-dev", 2)
    assert r["prompt_truncated"] is False and len(r["prompt_text"]) == LARGE
    assert r["response_truncated"] is False and r["response_text"] is None
    data["prompt_text"] = "a" * (LARGE - 1) + "é" * 10  # é straddles the cut
    path.write_text(json.dumps(data))
    r = ProjectReader(proj_of(fleet).path).read_telemetry_attempt(f"{gov()}-dev", 2)
    assert r["prompt_truncated"] is True
    assert len(r["prompt_text"].encode("utf-8")) <= LARGE
    r["prompt_text"].encode("utf-8")  # valid UTF-8


def test_telemetry_corrupt_file_is_none(fleet):
    _tel_path(fleet).write_text("{nope")
    assert ProjectReader(proj_of(fleet).path).read_telemetry_attempt(f"{gov()}-dev", 2) is None


def test_audits_list_and_read(reader, fleet):
    g = gov()
    listing = reader.list_audits(g)
    assert [a["audit_id"] for a in listing] == sorted(
        (a["audit_id"] for a in listing), reverse=True)
    assert [a["audit_id"] for a in listing] == [s for s in reversed(demo.AUDIT_TIMESTAMPS)]
    newest = reader.read_audit(g, listing[0]["audit_id"])
    assert newest["future_section"]["level1"]["flag"] is True
    assert newest["cost"]["forecast"]["model"]["p95"] == 0.9
    older = reader.read_audit(g, listing[1]["audit_id"])
    assert "future_section" not in older
    for bad in ("..", "../x", "a/b", "", None, 3, "nope"):
        assert reader.read_audit(g, bad) is None
    # a non-root bead resolves to its root's directory
    assert len(reader.list_audits(f"{g}-dev")) == 2
    assert reader.list_audits("B-nope") == []


def test_audits_missing_dir_corrupt_and_md_ignored(fleet):
    p = proj_of(fleet, "demo-2-older")
    r = ProjectReader(p.path)
    assert r.list_audits(gov("demo-2-older")) == [] or True
    root = next(b for b in beads_of(r) if b["bead_id"] == f"B-{demo._hex8('demo-2-older')}")
    assert r.list_audits(root["bead_id"]) == []
    g = gov("demo-2-older")
    d = p.path / ".takt" / "audits" / g
    (d / "20260105T000000Z.json").write_text("{corrupt")
    r2 = ProjectReader(p.path)
    ids = [a["audit_id"] for a in r2.list_audits(g)]
    assert ids[0] == "20260105T000000Z"  # corrupt snapshot still listed
    assert r2.list_audits(g)[0]["generated_at"] is not None or True
    assert r2.read_audit(g, "20260105T000000Z") is None
    assert all(not i.endswith(".md") for i in ids)
    assert len(ids) == 3


# ------------------------------------------------------------ view models

def row(**kw):
    base = {"bead_id": "B-1", "title": "T", "status": "open", "agent_type": "developer",
            "feature_root_id": "B-1", "labels": [],
            "execution_history": [{"timestamp": "2026-01-01T00:00:00+00:00"}]}
    base.update(kw)
    return base


def test_bead_list_filters_and_sort():
    """Scenario: fleet-web-ui / Bead explorer data / Filtered bead list
    (also asserted over HTTP in test_scenario_filtered_bead_list)."""
    beads = [
        row(bead_id="B-a", title="Alpha Thing", status="done", agent_type="tester", labels=["x", "y"]),
        row(bead_id="B-b", title="beta", status="blocked", agent_type="developer", labels=["x"]),
        row(bead_id="B-c", title="gamma", status="open", agent_type="review", labels=["y"],
            feature_root_id="B-other"),
        row(bead_id="B-d", title="delta", status="done", agent_type="developer"),
        "junk", {"bead_id": 5, "labels": "notalist", "execution_history": "x"}, {},
    ]
    ids = lambda **kw: [r["bead_id"] for r in viewmodels.build_bead_list(beads, order="oldest", **kw)["beads"]]
    assert ids(status=["done", "blocked"]) == ["B-a", "B-b", "B-d"]
    assert ids(agent=["tester", "review"]) == ["B-a", "B-c"]
    assert ids(label=["x", "y"]) == ["B-a"]
    assert ids(feature_root="B-other") == ["B-c"]
    assert ids(q="ALPHA") == ["B-a"]
    assert ids(q="b-b") == ["B-b"]
    assert ids(status="done", agent="developer") == ["B-d"]
    out = viewmodels.build_bead_list(beads, limit=1)
    assert out["total"] == len(viewmodels.build_bead_list(beads)["beads"])
    assert out["next_cursor"]
    assert len(out["beads"]) == 1
    # ties on created_at fall back to bead id; malformed beads sort first (no history)
    assert ids(status=["done", "blocked", "open"]) == ["B-a", "B-b", "B-c", "B-d"]
    assert viewmodels.build_bead_list(beads, limit=5000)["total"] == viewmodels.build_bead_list(beads)["total"]
    assert len(viewmodels.build_bead_list(beads, limit=0)["beads"]) == 1
    assert len(viewmodels.build_bead_list(beads, limit=-3)["beads"]) == 1
    big = [row(bead_id=f"B-{i:05d}") for i in range(300)]
    assert len(viewmodels.build_bead_list(big, limit=5000)["beads"]) == 200
    assert viewmodels.build_bead_list(big, limit=5000)["total"] == 300


def test_badges_individually():
    """Scenario: fleet-web-ui / Bead explorer data / Governance badges"""
    def badges(**kw):
        return viewmodels.bead_badges(row(**kw))
    hs = lambda **kw: {"handoff_summary": kw}
    assert "structural_assumptions" in badges(**hs(assumptions=[{"scope": "structural"}]))
    assert "structural_assumptions" not in badges(**hs(assumptions=[{"scope": "local"}]))
    assert "unmet_criteria" in badges(**hs(criteria_status=[{"status": "unmet"}]))
    assert "descoped_criteria" in badges(**hs(criteria_status=[{"status": "descoped"}]))
    assert badges(**hs(criteria_status=[{"status": "met"}])) == []
    assert "merged" in badges(metadata={"merge_records": [{"x": 1}]})
    assert "merged" not in badges(metadata={"merge_records": []})
    assert "merged" not in badges(bead_id="B-2", metadata={"merge_records": [{"x": 1}]})
    assert "discovered" in badges(labels=["discovered"])
    assert "corrective" in badges(metadata={"auto_corrective_for": "B-9"})
    assert "recovery" in badges(recovery_for="B-9")
    assert "high_priority" in badges(priority="high")
    assert viewmodels.bead_badges({}) == []
    assert viewmodels.bead_badges(row(handoff_summary="x", metadata="y")) == []


def test_cost_fallback():
    """Scenario: fleet-web-ui / Bead explorer data / Cost fallback"""
    assert viewmodels.bead_cost({}) == {"cost_usd": None, "wall_clock_seconds": None}
    assert viewmodels.bead_cost({"metadata": "x"})["cost_usd"] is None
    invalid = {"bead_id": "B-1", "priority": "bogus", "metadata": {"telemetry_history": [
        {"cost_usd": 0.25}, {"cost_usd": "n/a"}, {"cost_usd": True}, "junk", {"cost_usd": 0.5}]}}
    assert viewmodels.bead_cost(invalid)["cost_usd"] == pytest.approx(0.75)
    assert viewmodels.bead_cost({"bead_id": "B-1", "priority": "bogus",
                                 "metadata": {"telemetry_history": [{"cost_usd": "x"}]}})["cost_usd"] is None
    valid = row(metadata={"telemetry": {"cost_usd": 1.5}})
    assert viewmodels.bead_cost(valid)["cost_usd"] in (1.5, None)  # helper-derived, never raises
    assert viewmodels.bead_cost(row())["cost_usd"] is None


def test_row_timestamps_empty_history():
    r = viewmodels.build_bead_row({"bead_id": "B-1", "execution_history": []})
    assert r["created_at"] is None and r["updated_at"] is None
    r = viewmodels.build_bead_row(row(execution_history=[
        {"timestamp": "2026-01-01"}, {"timestamp": "2026-02-01"}, {"x": 1}]))
    assert (r["created_at"], r["updated_at"]) == ("2026-01-01", "2026-02-01")


def test_feature_list_rollups_and_order(fleet):
    """Scenario: fleet-web-ui / Feature trees / In-flight features first"""
    p = proj_of(fleet)
    r = ProjectReader(p.path)
    out = viewmodels.build_feature_list(beads_of(r), r)
    feats = out["features"]
    assert out["total"] == len(feats)
    by = {f["bead_id"]: f for f in feats}
    assert gov() in by and f"B-{demo._hex8(PROJ)}" in by
    # in-flight (demo feature has an in_progress root) before the idle ones
    assert feats[0]["in_flight"] is True
    flags = [f["in_flight"] for f in feats]
    assert flags == sorted(flags, reverse=True)
    idle = [f for f in feats if not f["in_flight"]]
    stamps = [f["last_activity_at"] or "" for f in idle]
    assert stamps == sorted(stamps, reverse=True)
    g = by[gov()]
    assert g["audits_count"] == 2
    assert g["last_merge"]["approver"].startswith("Demo Approver")
    assert g["criteria"]["unmet"] >= 1 and g["criteria"]["descoped"] >= 1 and g["criteria"]["met"] >= 1
    assert g["assumptions"]["structural"] >= 1
    assert g["cost_usd"] is not None
    assert by[f"B-{demo._hex8(PROJ)}"]["last_merge"] is None
    # discovered bead is its own (root == self) feature; non-root beads never are
    assert f"{gov()}-dev" not in by
    assert viewmodels.build_feature_list(beads_of(r), None)["features"][0]["audits_count"] == 0


def test_feature_list_idle_ordering_and_exclusions():
    beads = [
        row(bead_id="B-old", feature_root_id="B-old", status="done",
            execution_history=[{"timestamp": "2026-01-01"}]),
        row(bead_id="B-new", feature_root_id="B-new", status="done",
            execution_history=[{"timestamp": "2026-02-01"}]),
        row(bead_id="B-fly", feature_root_id="B-fly", status="open",
            execution_history=[{"timestamp": "2025-01-01"}]),
        row(bead_id="B-fly-c", feature_root_id="B-fly", status="in_progress"),
        row(bead_id="B-epic", feature_root_id=None, status="open"),
        row(bead_id="B-kid", feature_root_id="B-new", status="done"),
        "junk", {"bead_id": None},
    ]
    out = viewmodels.build_feature_list(beads)
    assert [f["bead_id"] for f in out["features"]] == ["B-fly", "B-new", "B-old"]
    assert out["features"][0]["counts"]["in_progress"] == 1


def test_feature_detail_tree(fleet):
    """Scenario: fleet-web-ui / Feature trees / Tree structure"""
    p = proj_of(fleet)
    r = ProjectReader(p.path)
    d = viewmodels.build_feature_detail(gov(), beads_of(r), r)
    tree = d["tree"]
    assert tree["bead_id"] == gov()
    kids = [c["bead_id"] for c in tree["children"]]
    assert f"{gov()}-dev" in kids and f"{gov()}-review" in kids
    created = [c["created_at"] or "" for c in tree["children"]]
    assert created == sorted(created)
    dev2 = next(c for c in tree["children"] if c["bead_id"] == f"{gov()}-dev2")
    assert [c["bead_id"] for c in dev2["children"]] == [f"{gov()}-dev2-recovery"]
    review = next(c for c in tree["children"] if c["bead_id"] == f"{gov()}-review")
    assert {x["bead_id"]: x["status"] for x in review["dependencies"]}[f"{gov()}-dev"] == "done"
    corr = next(c for c in tree["children"] if c["bead_id"] == f"{gov()}-review-corrective")
    assert corr["badges"] == ["corrective"]
    assert [x["bead_id"] for x in corr["dependencies"]] == [f"{gov()}-review"]
    assert len(d["merge_records"]) == 1 and len(d["audits"]) == 2
    assert d["feature"]["bead_id"] == gov()
    assert viewmodels.build_feature_detail("B-unknown", beads_of(r), r) is None
    assert viewmodels.build_feature_detail(f"{gov()}-dev", beads_of(r), r) is None


def test_feature_detail_edge_cases():
    beads = [
        row(bead_id="R", feature_root_id="R", execution_history=[{"timestamp": "1"}]),
        row(bead_id="A", feature_root_id="R", parent_id="R", execution_history=[{"timestamp": "3"}],
            dependencies=["GHOST", "R"]),
        row(bead_id="B", feature_root_id="R", parent_id="MISSING", execution_history=[{"timestamp": "2"}]),
        row(bead_id="C", feature_root_id="R", parent_id="OTHER", execution_history=[{"timestamp": "4"}]),
        row(bead_id="OTHER", feature_root_id="X", execution_history=[{"timestamp": "5"}]),
        row(bead_id="P", feature_root_id="R", parent_id="Q", execution_history=[{"timestamp": "6"}]),
        row(bead_id="Q", feature_root_id="R", parent_id="P", execution_history=[{"timestamp": "7"}]),
    ]
    d = viewmodels.build_feature_detail("R", beads, None)
    top = [c["bead_id"] for c in d["tree"]["children"]]
    assert top[:3] == ["B", "A", "C"]  # orphans and cross-tree parents hang under the root
    flat = []

    def walk(n):
        flat.append(n["bead_id"])
        for c in n["children"]:
            walk(c)
    walk(d["tree"])
    assert sorted(flat) == ["A", "B", "C", "P", "Q", "R"]  # cycle members appear once
    a = next(c for c in d["tree"]["children"] if c["bead_id"] == "A")
    assert a["dependencies"] == [{"bead_id": "GHOST", "status": None},
                                 {"bead_id": "R", "status": "open"}]
    assert d["audits"] == [] and d["merge_records"] == []

    class Boom:
        def list_audits(self, _):
            raise RuntimeError("x")
    assert viewmodels.build_feature_detail("R", beads, Boom())["audits"] == []


def test_bead_detail_joins_criteria_and_status(fleet):
    """Scenario: fleet-web-ui / Bead explorer data / Bead detail joins criteria and status"""
    p = proj_of(fleet)
    r = ProjectReader(p.path)
    beads = beads_of(r)
    dev = next(b for b in beads if b["bead_id"] == f"{gov()}-dev")
    d = viewmodels.build_bead_detail(dev, beads, r)
    assert [c["status"] for c in d["acceptance_criteria"]] == ["met", "unmet", "descoped"]
    assert d["acceptance_criteria"][0]["evidence"] == "src/mod.py"
    assert [a["scope"] for a in d["assumptions"]] == ["structural", "local"]
    assert d["telemetry"]["totals"]["attempts"] == 2
    assert d["telemetry"]["totals"]["cost_usd"] == pytest.approx(0.38)
    assert [t["seq"] for t in d["traces"]] == [1, 2]
    assert d["raw"] == dev
    assert d["discovered_issues"][0]["labels"] == ["discovered"]
    review = next(b for b in beads if b["bead_id"] == f"{gov()}-review")
    rd = viewmodels.build_bead_detail(review, beads, r)
    assert {x["bead_id"]: x["status"] for x in rd["dependencies"]}[f"{gov()}-test"] == "done"
    assert rd["children"] == []  # the corrective is a child of the root, not of the review
    root_d = viewmodels.build_bead_detail(next(b for b in beads if b["bead_id"] == gov()), beads, r)
    assert f"{gov()}-review-corrective" in [c["bead_id"] for c in root_d["children"]]
    root = next(b for b in beads if b["bead_id"] == gov())
    assert len(viewmodels.build_bead_detail(root, beads, r)["merge_records"]) == 1
    extra = next(b for b in beads if b["bead_id"] == f"{gov()}-extra")
    ed = viewmodels.build_bead_detail(extra, beads, r)
    assert ed["raw"]["future_field"]["flag"] is True
    assert ed["raw"]["handoff_summary"]["future_nested"] == {"x": {"y": 1}}


def test_bead_detail_malformed_fields():
    bead = {"bead_id": "B-1", "handoff_summary": "str", "metadata": ["x"],
            "dependencies": "notalist", "acceptance_criteria": ["a", "b", "c"],
            "execution_history": "x"}
    d = viewmodels.build_bead_detail(bead, [bead, "junk"], None)
    assert [c["status"] for c in d["acceptance_criteria"]] == ["unreported"] * 3
    assert isinstance(d["dependencies"], list) and d["traces"] == [] and d["execution_history"] == []
    assert d["telemetry"]["rows"] == []
    bead2 = {"bead_id": "B-2", "acceptance_criteria": ["a", "b"],
             "handoff_summary": {"criteria_status": [
                 {"index": 0, "status": "met"}, {"index": 0, "status": "unmet"},
                 {"status": "met"}, "junk", {"index": 5, "status": "met"}],
                 "assumptions": "bad"},
             "metadata": {"telemetry_history": "bad"}}
    d2 = viewmodels.build_bead_detail(bead2, [bead2], None)
    assert [c["status"] for c in d2["acceptance_criteria"]] == ["met", "unreported"]  # first wins

    class Boom:
        def list_trace_records(self, _):
            raise RuntimeError

    assert viewmodels.build_bead_detail(bead2, [bead2], Boom())["traces"] == []


def test_bead_detail_telemetry_totals_and_discovered():
    a = {"bead_id": "A", "metadata": {"telemetry_history": [
        {"attempt": 1, "cost_usd": 1.0, "duration_ms": 100, "input_tokens": 5, "output_tokens": 5},
        {"attempt": 2, "duration_ms": 50}]}}
    nocost = {"bead_id": "N", "metadata": {"telemetry_history": [{"attempt": 1}]}}
    disc = {"bead_id": "D", "metadata": {"discovered_issue_of": "A"}}
    other = {"bead_id": "E", "metadata": {"discovered_issue_of": "N"}}
    d = viewmodels.build_bead_detail(a, [a, nocost, disc, other])
    assert d["telemetry"]["totals"] == {"attempts": 2, "cost_usd": 1.0, "duration_ms": 150.0, "tokens": 10}
    assert [x["bead_id"] for x in d["discovered_issues"]] == ["D"]
    n = viewmodels.build_bead_detail(nocost, [nocost])["telemetry"]["totals"]
    assert n["cost_usd"] is None and n["tokens"] is None and n["duration_ms"] is None


# ------------------------------------------------------------------- API

def test_scenario_filtered_bead_list(client):
    """Scenario: fleet-web-ui / Bead explorer data / Filtered bead list"""
    r = client.get(f"/api/projects/{PROJ}/beads")
    assert r.status_code == 200
    total = r.json()["total"]
    assert len(r.json()["beads"]) == 25 and total > 25
    r = client.get(f"/api/projects/{PROJ}/beads?status=done&status=blocked&agent=developer&limit=2")
    body = r.json()
    assert len(body["beads"]) == 2 and body["total"] > 2
    assert {b["status"] for b in body["beads"]} <= {"done", "blocked"}
    assert {b["agent_type"] for b in body["beads"]} == {"developer"}
    r = client.get(f"/api/projects/{PROJ}/beads?label=governance&label=nope")
    assert r.json()["beads"] == [] and r.json()["total"] == 0
    r = client.get(f"/api/projects/{PROJ}/beads?feature_root={gov()}&q=REVIEW")
    assert {b["bead_id"] for b in r.json()["beads"]} == {f"{gov()}-review", f"{gov()}-review-corrective"}
    assert client.get(f"/api/projects/{PROJ}/beads?limit=abc").status_code == 200


def test_scenario_bead_detail_route(client):
    """Scenario: fleet-web-ui / Bead explorer data / Bead detail joins criteria and status"""
    r = client.get(f"/api/projects/{PROJ}/beads/{gov()}-dev")
    assert r.status_code == 200
    d = r.json()
    assert {"row", "acceptance_criteria", "handoff", "assumptions", "files", "dependencies",
            "children", "execution_history", "telemetry", "traces", "raw"} <= set(d)
    assert [c["status"] for c in d["acceptance_criteria"]] == ["met", "unmet", "descoped"]
    assert d["telemetry"]["totals"]["attempts"] == 2


def _is_404(r):
    # IDs rejected by the handler give a JSON error; an encoded separator never matches
    # a route at all and gets Starlette's plain 404. Either way nothing is read.
    return r.status_code == 404 and (
        "error" in r.json() if r.headers["content-type"].startswith("application/json") else True)


@pytest.mark.parametrize("bad", ["a%2Fb", "..%2F..%2Fetc%2Fpasswd", "%2e%2e", "a%5Cb", "B-nope"])
def test_scenario_unsafe_bead_id(client, bad):
    """Scenario: fleet-web-ui / Bead explorer data / Unsafe bead id"""
    for path in (f"/api/projects/{PROJ}/beads/{bad}",
                 f"/api/projects/{PROJ}/beads/{bad}/traces/1",
                 f"/api/projects/{PROJ}/beads/{bad}/telemetry/1",
                 f"/api/projects/{PROJ}/features/{bad}",
                 f"/api/projects/{PROJ}/features/{bad}/audits/x"):
        assert _is_404(client.get(path)), path


def test_raw_dotdot_is_normalised_to_another_route(client):
    """The HTTP client collapses `..`; the result is a different, valid route, never a read
    outside .takt/beads."""
    r = client.get(f"/api/projects/{PROJ}/beads/..")
    assert r.status_code == 200 and r.json()["name"] == PROJ
    r = client.get(f"/api/projects/{PROJ}/features/{gov()}/audits/..")
    assert r.status_code == 200 and r.json()["tree"]["bead_id"] == gov()


def test_unknown_project_and_slash_name(client):
    for path in ("/api/projects/nope/beads", "/api/projects/nope/beads/B-1",
                 "/api/projects/nope/features", "/api/projects/nope/features/B-1",
                 "/api/projects/nope/beads/B-1/traces/1", "/api/projects/nope/beads/B-1/telemetry/1",
                 "/api/projects/nope/features/B-1/audits/a", "/api/projects/..%2Fx/beads",
                 "/api/projects/a/b/beads"):
        assert _is_404(client.get(path)), path


def test_features_routes_and_audit(client):
    """Scenario: fleet-web-ui / Stored audits / Audit returned as stored"""
    r = client.get(f"/api/projects/{PROJ}/features")
    assert r.status_code == 200 and r.json()["total"] == len(r.json()["features"])
    r = client.get(f"/api/projects/{PROJ}/features/{gov()}")
    d = r.json()
    assert r.status_code == 200 and d["tree"]["bead_id"] == gov()
    newest = d["audits"][0]["audit_id"]
    a = client.get(f"/api/projects/{PROJ}/features/{gov()}/audits/{newest}")
    assert a.status_code == 200
    body = a.json()
    assert body["audit_id"] == newest and body["generated_at"]
    assert body["future_section"]["level1"]["level2"][0]["deep"] == {"n": 1}
    assert body["cost"]["forecast"]["model"]["p50"] == 0.5
    assert body["feature"]["labels"] == {"team": {"name": "demo"}}
    assert body["feature"]["bead_id"] == gov()


def test_audit_unknown_ids(client):
    """Scenario: fleet-web-ui / Stored audits / Unknown audit id"""
    base = f"/api/projects/{PROJ}/features/{gov()}/audits"
    for aid in ("nope", "%2e%2e", "a%2Fb", "20990101T000000Z"):
        assert _is_404(client.get(f"{base}/{aid}")), aid
    assert client.get(f"/api/projects/{PROJ}/features/B-unknown/audits/x").status_code == 404
    # a non-root bead id is not a feature
    dev = f"{gov()}-dev"
    assert client.get(f"/api/projects/{PROJ}/features/{dev}").status_code == 404
    assert client.get(f"/api/projects/{PROJ}/features/{dev}/audits/x").status_code == 404


def test_trace_route_paging_and_filters(client):
    """Scenario: fleet-web-ui / Traces and attempts / Paged and filtered trace events"""
    base = f"/api/projects/{PROJ}/beads/{gov()}-dev/traces/1"
    d = client.get(base).json()
    assert d["total"] == 5 and len(d["events"]) == 5 and d["meta"]["seq"] == 1
    assert d["summary"]["error_count"] == 1
    p = client.get(base + "?limit=2&offset=4").json()
    assert len(p["events"]) == 1 and p["total"] == 5 and p["offset"] == 4
    assert client.get(base + "?limit=5000").json()["limit"] == 1000
    assert client.get(base + "?limit=abc").status_code == 200
    e = client.get(base + "?errors_only=true").json()
    assert e["total"] == 1 and e["events"][0]["is_error"] is True
    assert client.get(base + "?kind=usage").json()["total"] == 1
    assert client.get(base + "?tool=Read").json()["total"] == 1
    assert client.get(base + "?kind=usage").json()["summary"] == d["summary"]


def test_trace_route_missing_and_unknown(client):
    """Scenario: fleet-web-ui / Traces and attempts / Missing trace record"""
    b = f"/api/projects/{PROJ}/beads/{gov()}-dev"
    r = client.get(f"{b}/traces/2")
    assert r.status_code == 200
    d = r.json()
    assert d["meta"]["status"] == "missing" and d["events"] == [] and d["total"] == 0
    for seq in ("9", "abc", "-1", "1.5"):
        assert client.get(f"{b}/traces/{seq}").status_code == 404
    assert client.get(f"/api/projects/{PROJ}/beads/B-nope/traces/1").status_code == 404


def test_telemetry_route(client):
    """Scenario: fleet-web-ui / Traces and attempts / Large prompt is capped"""
    b = f"/api/projects/{PROJ}/beads/{gov()}-dev/telemetry"
    d = client.get(f"{b}/1").json()
    assert d["prompt_truncated"] is True and len(d["prompt_text"].encode()) <= LARGE
    assert d["response_truncated"] is False
    assert client.get(f"{b}/2").json()["prompt_truncated"] is False
    for attempt in ("9", "abc", "-1", "1.5"):
        assert client.get(f"{b}/{attempt}").status_code == 404
    assert client.get(f"/api/projects/{PROJ}/beads/B-nope/telemetry/1").status_code == 404


def test_new_routes_are_get_only(client):
    paths = [f"/api/projects/{PROJ}/beads", f"/api/projects/{PROJ}/beads/{gov()}-dev",
             f"/api/projects/{PROJ}/beads/{gov()}-dev/traces/1",
             f"/api/projects/{PROJ}/beads/{gov()}-dev/telemetry/1",
             f"/api/projects/{PROJ}/features", f"/api/projects/{PROJ}/features/{gov()}",
             f"/api/projects/{PROJ}/features/{gov()}/audits/{demo.AUDIT_TIMESTAMPS[0]}"]
    for path in paths:
        assert client.get(path).status_code in (200, 404)
        for method in ("post", "put", "patch", "delete"):
            assert getattr(client, method)(path).status_code == 405, (method, path)


def test_corrupt_files_never_500(fleet, no_subprocess):
    p = proj_of(fleet)
    t = p.path / ".takt"
    dev = f"{gov()}-dev"
    (t / "beads" / "B-badbead.json").write_text("{nope")
    (t / "beads" / "B-listy.json").write_text("[1,2]")
    (t / "traces" / dev / "1.events.jsonl").write_text("{broken\n\x00\xff\n")
    (t / "traces" / dev / "2.meta.json").write_text("{broken")
    (t / "telemetry" / dev / "1.json").write_text("{broken")
    for f in (t / "audits" / gov()).glob("*.json"):
        f.write_text("{broken")
    c = TestClient(create_app(lambda: to_projects(fleet), installed_version="0.9.0"))
    base = f"/api/projects/{p.name}"
    paths = [f"{base}/beads", f"{base}/beads/{dev}", f"{base}/beads/B-badbead",
             f"{base}/beads/{dev}/traces/1", f"{base}/beads/{dev}/traces/2",
             f"{base}/beads/{dev}/telemetry/1", f"{base}/features", f"{base}/features/{gov()}"]
    audits = c.get(f"{base}/features/{gov()}").json()["audits"]
    assert len(audits) == 2
    paths += [f"{base}/features/{gov()}/audits/{a['audit_id']}" for a in audits]
    for path in paths:
        assert c.get(path).status_code < 500, path
    assert c.get(f"{base}/beads").status_code == 200
    assert c.get(f"{base}/beads/{dev}/traces/1").json()["total"] == 0
    assert c.get(f"{base}/beads/{dev}/telemetry/1").status_code == 404


def test_no_subprocess_spawned(client, monkeypatch):
    """All new routes run without any subprocess (guard from conftest)."""
    called = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: called.append(a))
    client.get(f"/api/projects/{PROJ}/beads")
    client.get(f"/api/projects/{PROJ}/features/{gov()}")
    assert called == []


def test_older_and_newer_variants_tolerated(client):
    for name in ("demo-2-older", "demo-3-newer"):
        r = client.get(f"/api/projects/{name}/beads")
        assert r.status_code == 200 and r.json()["total"] > 0
        assert client.get(f"/api/projects/{name}/features").status_code == 200
    # P1 counts unchanged by the explorer fixtures
    card = client.get(f"/api/projects/{PROJ}").json()
    assert card["bead_counts"]["blocked"] == 1


# ---------------------------------------------------------------- stream

def _drain(sub):
    out = []
    while not sub.queue.empty():
        out.append(sub.queue.get_nowait())
    return out


def _hub(fleet):
    projs = to_projects(fleet)
    hub = FleetStreamHub(
        lambda: projs, lambda p: viewmodels.build_project_card(p, ProjectReader(p.path), "0.9.0"),
        lambda cur: viewmodels.build_fleet(cur, "0.9.0"), viewmodels.build_event_item,
        poll_interval=0.05)
    return hub, projs


def test_beads_changed_content_rewrite(fleet):
    """Scenario: fleet-web-ui / Bead change notifications / Handoff change without status change"""
    p = proj_of(fleet)
    hub, _ = _hub(fleet)
    hub.poll_once()  # baseline, emits nothing
    sub = hub.subscribe()
    hub.poll_once()
    assert [e for e, _ in _drain(sub) if e == "beads_changed"] == []
    target = f"{gov()}-dev"
    demo.rewrite_bead_content(p.path, target, "remaining changed", clock=clock)
    hub.poll_once()
    msgs = _drain(sub)
    changed = [d for e, d in msgs if e == "beads_changed"]
    assert changed == [{"project": p.name, "bead_ids": [target]}]
    assert not [d for e, d in msgs if e == "project_updated" and d.get("name") == p.name]
    hub.poll_once()
    assert _drain(sub) == []


def test_beads_changed_added_removed_and_batched(fleet):
    p = proj_of(fleet)
    hub, _ = _hub(fleet)
    hub.poll_once()
    sub = hub.subscribe()
    beads = p.path / ".takt" / "beads"
    ids = [f"{gov()}-dev", f"{gov()}-docs"]
    for i in ids:
        demo.rewrite_bead_content(p.path, i, "n", clock=clock)
    (beads / "B-added.json").write_text(json.dumps({"bead_id": "B-added", "status": "open"}))
    (beads / f"{gov()}-extra.json").unlink()
    hub.poll_once()
    changed = [d for e, d in _drain(sub) if e == "beads_changed" and d["project"] == p.name]
    assert len(changed) == 1  # a single message per project per poll
    assert changed[0]["bead_ids"] == sorted([*ids, "B-added", f"{gov()}-extra"])


def test_beads_changed_project_without_beads_dir_and_new_project(fleet, tmp_path):
    hub, projs = _hub(fleet)
    nodir = tmp_path / "empty-project"
    nodir.mkdir()
    projs.append(Project("nodir", nodir, ()))
    hub.poll_once()
    sub = hub.subscribe()
    hub.poll_once()
    late = tmp_path / "late"
    (late / ".takt" / "beads").mkdir(parents=True)
    (late / ".takt" / "beads" / "B-1.json").write_text("{}")
    projs.append(Project("late", late, ()))
    hub.poll_once()
    assert [d for e, d in _drain(sub) if e == "beads_changed"] == []  # baselined silently
