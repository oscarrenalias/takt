"""Tolerant readers: beads, cache, events, scheduler state, version, health."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from agent_takt_fleet.web import demo, readers
from agent_takt_fleet.web.readers import (
    ProjectReader, bead_counts, compute_health_from_files, read_beads,
    read_recent_events, read_scheduler_state, read_takt_version,
)

from .conftest import clock


def _proj(fleet, variant):
    return next(p for p in fleet.projects if p.variant == variant)


def _beads_dir(p: Path) -> Path:
    return p / ".takt" / "beads"


def test_tolerant_bead_reading_variants(fleet):
    """Scenario: fleet-web-ui / Fleet overview data / Tolerant bead reading"""
    older = read_beads(_proj(fleet, "older").path)
    newer = read_beads(_proj(fleet, "newer").path)
    corrupt = read_beads(_proj(fleet, "corrupt").path)
    assert len(older.beads) == len(_proj(fleet, "older").bead_ids) and older.unreadable_beads == 0
    assert all("lease" not in b and "labels" not in b for b in older.beads)
    assert len(newer.beads) == len(_proj(fleet, "newer").bead_ids)
    assert all(b["risk_score"] == 0.42 for b in newer.beads)  # unknown fields preserved
    assert len(corrupt.beads) == len(_proj(fleet, "corrupt").bead_ids) and corrupt.unreadable_beads == 1


def test_bead_read_malformed_shapes(tmp_path):
    d = _beads_dir(tmp_path)
    d.mkdir(parents=True)
    (d / "a.json").write_text("{not json")
    (d / "b.json").write_text("[1, 2]")
    (d / "c.json").write_text("42")
    (d / "d.json").write_text("")
    (d / "e.json").write_text(json.dumps({"bead_id": "B-e"}))
    res = read_beads(tmp_path)
    assert [b["bead_id"] for b in res.beads] == ["B-e"]
    assert res.unreadable_beads == 4


def test_bead_read_missing_dir(tmp_path):
    res = read_beads(tmp_path / "nope")
    assert res.beads == [] and res.unreadable_beads == 0


def test_bead_with_no_optional_fields(tmp_path):
    d = _beads_dir(tmp_path)
    d.mkdir(parents=True)
    (d / "x.json").write_text(json.dumps({"bead_id": "B-x", "status": "open"}))
    res = read_beads(tmp_path)
    assert res.beads == [{"bead_id": "B-x", "status": "open"}]
    assert bead_counts(res.beads)["open"] == 1


def test_reader_cache_parse_count(fleet):
    p = _proj(fleet, "full")
    reader = ProjectReader(p.path)
    reader.read_beads()
    first = reader.parse_count
    assert first == len(p.bead_ids)
    reader.read_beads()
    assert reader.parse_count == first  # unchanged files not re-parsed
    demo.set_bead_status(p.path, p.bead_ids[1], "blocked", clock=clock)
    res = reader.read_beads()
    assert reader.parse_count == first + 1  # only the changed file
    assert {b["bead_id"]: b["status"] for b in res.beads}[p.bead_ids[1]] == "blocked"


def test_reader_cache_drops_deleted_files(fleet):
    p = _proj(fleet, "full")
    reader = ProjectReader(p.path)
    reader.read_beads()
    (_beads_dir(p.path) / f"{p.bead_ids[-1]}.json").unlink()
    res = reader.read_beads()
    assert len(res.beads) == len(p.bead_ids) - 1
    assert len(reader._cache) == len(p.bead_ids) - 1


def test_bead_counts_empty_and_unknown():
    assert bead_counts([]) == {s: 0 for s in readers.BEAD_STATUSES}
    assert len(bead_counts([])) == 6
    counts = bead_counts([{"status": "weird"}, {"status": None}, {}, "x"])  # type: ignore[list-item]
    assert counts["weird"] == 1
    assert sum(counts.values()) == 1


def test_recent_events_missing_and_limit(fleet, tmp_path):
    assert read_recent_events(tmp_path / "none") == []
    p = _proj(fleet, "full")
    all_events = read_recent_events(p.path, limit=1000)
    assert read_recent_events(p.path, limit=3) == all_events[-3:]
    assert read_recent_events(p.path, limit=0) == []


def test_recent_events_malformed_and_partial_first_line(tmp_path):
    path = tmp_path / ".takt" / "logs"
    path.mkdir(parents=True)
    f = path / "events.jsonl"
    lines = [json.dumps({"timestamp": f"t{i:05d}", "event_type": "x", "payload": {"pad": "y" * 100}})
             for i in range(5000)]
    f.write_text("\n".join(lines) + "\nnot json\n[1]\n")
    assert f.stat().st_size > readers._EVENT_TAIL_BYTES
    ev = read_recent_events(tmp_path, limit=10)
    assert len(ev) == 10
    assert ev[-1]["timestamp"] == "t04999"
    # a limit larger than the tail window still returns whole records only
    big = read_recent_events(tmp_path, limit=4000)
    assert len(big) == 4000 and all("timestamp" in e for e in big)


def test_scheduler_state_running_stale_idle(fleet):
    full = _proj(fleet, "full")
    st = read_scheduler_state(full.path, full.name)
    assert st["state"] == "running"
    assert st["run_id"] == full.active_run_id
    assert st["started_at"]
    older = _proj(fleet, "older")
    assert read_scheduler_state(older.path, older.name)["state"] == "idle"
    # stale: marker with a dead pid
    active = older.path / ".takt" / "runs" / "active"
    active.mkdir(parents=True)
    (active / "SR-dead.json").write_text(json.dumps(
        {"run_id": "SR-dead", "pid": 2**22 + 12345, "hostname": __import__("socket").gethostname(),
         "started_at": "2026-03-01T10:00:00+00:00"}))
    assert read_scheduler_state(older.path, older.name)["state"] == "stale"


def test_takt_version_variants(tmp_path, fleet):
    assert read_takt_version(tmp_path) is None
    v = tmp_path / ".takt"
    v.mkdir()
    (v / "version.json").write_text("{bad")
    assert read_takt_version(tmp_path) is None
    (v / "version.json").write_text("[1]")
    assert read_takt_version(tmp_path) is None
    (v / "version.json").write_text(json.dumps({"takt_version": "1.2.3"}))
    assert read_takt_version(tmp_path) == "1.2.3"
    nov = _proj(fleet, "no-version")
    assert not (nov.path / ".takt" / "version.json").exists()
    assert read_takt_version(nov.path) is None


def test_health(tmp_path, fleet):
    assert compute_health_from_files(tmp_path / "gone") == "missing"
    assert compute_health_from_files(tmp_path) == "no-takt"
    assert compute_health_from_files(_proj(fleet, "full").path) == "ok"


def test_readers_module_boundaries():
    src = Path(readers.__file__).read_text()
    import ast
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith("agent_takt."), node.module
            assert node.module != "subprocess"
        if isinstance(node, ast.Import):
            assert all(not a.name.startswith("agent_takt.") for a in node.names)
            assert all(a.name != "subprocess" for a in node.names)


def test_readers_transitive_imports_clean():
    """Importing readers must not drag in forbidden agent_takt domain modules."""
    code = (
        "import sys, agent_takt_fleet.web.readers\n"
        "bad=[m for m in sys.modules if m in ('agent_takt.storage','agent_takt.scheduler',"
        "'agent_takt.runner','agent_takt.gitutils','agent_takt.planner','agent_takt.audit') "
        "or m.startswith('agent_takt.cli')]\n"
        "print(bad)\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"


# --- bounded backward events reader -----------------------------------------

def _write_events(root: Path, lines: list[str]) -> Path:
    log = root / ".takt" / "logs" / "events.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return log


def _ev(i: int, ts: str | None = None) -> str:
    from datetime import datetime, timedelta, timezone
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    stamp = ts or (base + timedelta(minutes=i)).isoformat()
    return json.dumps({"timestamp": stamp, "event_type": "x", "payload": {"i": i}})


def test_iter_events_newest_first_small_blocks(tmp_path):
    _write_events(tmp_path, [_ev(i) for i in range(50)])
    got = [e["payload"]["i"] for e in readers.iter_events_newest_first(tmp_path, block_size=64)]
    assert got == list(range(49, -1, -1))


def test_iter_events_stops_at_cutoff_without_reading_prefix(tmp_path):
    from datetime import datetime, timedelta, timezone
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    _write_events(tmp_path, [_ev(i) for i in range(5000)])
    size = (tmp_path / ".takt" / "logs" / "events.jsonl").stat().st_size
    reads: list[int] = []
    cutoff = base + timedelta(minutes=4990)
    got = readers.read_events_since(tmp_path, cutoff, block_size=1024, on_block=reads.append)
    assert [e["payload"]["i"] for e in got] == list(range(4999, 4989, -1))
    assert sum(reads) < size // 10
    assert len(reads) < 10


def test_iter_events_skips_malformed_and_partial_lines(tmp_path):
    lines = [_ev(0), "not json", "[1, 2]", "", _ev(1), '{"timestamp": "2026-01-01T00:00:00+00:0']
    log = _write_events(tmp_path, lines)
    # Drop trailing newline so the last line is a truncated partial.
    log.write_text(log.read_text().rstrip("\n"), encoding="utf-8")
    got = [e["payload"]["i"] for e in readers.iter_events_newest_first(tmp_path, block_size=16)]
    assert got == [1, 0]


def test_iter_events_missing_file_yields_nothing(tmp_path):
    assert list(readers.iter_events_newest_first(tmp_path)) == []
    from datetime import datetime, timezone
    assert readers.read_events_since(tmp_path, datetime(2026, 1, 1, tzinfo=timezone.utc)) == []


def test_iter_events_untimestamped_event_does_not_stop(tmp_path):
    from datetime import datetime, timezone
    _write_events(tmp_path, [_ev(0), json.dumps({"event_type": "no-ts"}), _ev(2)])
    got = readers.read_events_since(tmp_path, datetime(2026, 1, 1, tzinfo=timezone.utc), block_size=8)
    assert len(got) == 3
