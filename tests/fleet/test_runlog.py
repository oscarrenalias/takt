from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_takt_fleet.models import FleetRun, ProjectResult, RunInputs
from agent_takt_fleet.runlog import (
    RunLogError,
    _CURRENT_VERSION,
    _parse_duration,
    _run_from_dict,
    _run_to_dict,
    compute_run_status,
    list_runs,
    load_run,
    new_run_id,
    resolve_run_id,
    write_run,
)


# ── Helpers ────────────────────────────────────────────────────────────────────


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


def _make_inputs(**kwargs) -> RunInputs:
    defaults: dict = {
        "bead": None,
        "tag_filter": (),
        "project_filter": (),
        "max_parallel": 4,
        "runner": None,
        "project_max_workers": None,
    }
    defaults.update(kwargs)
    return RunInputs(**defaults)


def _make_run(run_id: str = "FR-a1b2c3d4", command: str = "run", **kwargs) -> FleetRun:
    defaults: dict = {
        "run_id": run_id,
        "command": command,
        "started_at": _utcnow(),
        "finished_at": None,
        "inputs": _make_inputs(),
        "projects": [],
        "crashed": False,
    }
    defaults.update(kwargs)
    return FleetRun(**defaults)


def _make_project_result(name: str = "api-svc", status: str = "success") -> ProjectResult:
    now = _utcnow()
    return ProjectResult(
        name=name,
        path=Path(f"/tmp/{name}"),
        status=status,
        started_at=now,
        finished_at=now + timedelta(seconds=30),
        error=None if status != "error" else "takt binary not found",
        outputs={"created_beads": ["B-abcd1234"] if status == "success" else None, "run_summary": None},
    )


# ── new_run_id ─────────────────────────────────────────────────────────────────


def test_new_run_id_format() -> None:
    run_id = new_run_id()
    assert run_id.startswith("FR-")
    hex_part = run_id[3:]
    assert len(hex_part) == 8
    int(hex_part, 16)  # raises ValueError if not valid hex


def test_new_run_id_unique() -> None:
    ids = {new_run_id() for _ in range(50)}
    assert len(ids) == 50


# ── _run_to_dict / _run_from_dict ──────────────────────────────────────────────


def test_run_serialisation_roundtrip() -> None:
    now = _utcnow()
    run = FleetRun(
        run_id="FR-deadbeef",
        command="dispatch",
        started_at=now,
        finished_at=now + timedelta(minutes=5),
        inputs=_make_inputs(
            bead={"title": "Check deps", "agent_type": "developer", "labels": []},
            tag_filter=("python",),
            project_filter=(),
            max_parallel=2,
            runner="claude",
            project_max_workers=4,
        ),
        projects=[_make_project_result("api-svc", "success")],
        crashed=False,
    )
    data = _run_to_dict(run)
    assert data["version"] == _CURRENT_VERSION
    assert data["run_id"] == "FR-deadbeef"
    assert data["command"] == "dispatch"
    assert data["finished_at"] is not None

    restored = _run_from_dict(data)
    assert restored.run_id == run.run_id
    assert restored.command == run.command
    assert len(restored.projects) == 1
    assert restored.projects[0].name == "api-svc"
    assert restored.inputs.tag_filter == ("python",)
    assert restored.inputs.runner == "claude"


def test_run_serialisation_null_finished_at() -> None:
    run = _make_run()
    data = _run_to_dict(run)
    assert data["finished_at"] is None
    restored = _run_from_dict(data)
    assert restored.finished_at is None


def test_run_serialisation_version_field() -> None:
    run = _make_run()
    data = _run_to_dict(run)
    assert "version" in data
    assert data["version"] == 1


# ── write_run / load_run ───────────────────────────────────────────────────────


def test_write_run_creates_file(tmp_path: Path) -> None:
    runs_path = tmp_path / "runs"
    run = _make_run("FR-00000001")
    with patch("agent_takt_fleet.runlog.runs_dir", return_value=runs_path):
        write_run(run)
    assert (runs_path / "FR-00000001.json").exists()


def test_write_run_content_is_valid_json(tmp_path: Path) -> None:
    runs_path = tmp_path / "runs"
    run = _make_run("FR-00000002")
    with patch("agent_takt_fleet.runlog.runs_dir", return_value=runs_path):
        write_run(run)
    data = json.loads((runs_path / "FR-00000002.json").read_text())
    assert data["run_id"] == "FR-00000002"
    assert data["version"] == _CURRENT_VERSION


def test_write_run_atomic_no_tmp_left(tmp_path: Path) -> None:
    runs_path = tmp_path / "runs"
    run = _make_run("FR-00000003")
    with patch("agent_takt_fleet.runlog.runs_dir", return_value=runs_path):
        write_run(run)
    tmp_files = list(runs_path.glob("*.tmp"))
    assert tmp_files == [], f"leftover temp files: {tmp_files}"


def test_write_run_overwrites_on_update(tmp_path: Path) -> None:
    runs_path = tmp_path / "runs"
    run = _make_run("FR-00000004")
    with patch("agent_takt_fleet.runlog.runs_dir", return_value=runs_path):
        write_run(run)
        run2 = FleetRun(
            run_id=run.run_id,
            command=run.command,
            started_at=run.started_at,
            finished_at=_utcnow(),
            inputs=run.inputs,
            projects=[_make_project_result()],
            crashed=False,
        )
        write_run(run2)
        loaded = load_run("FR-00000004")
    assert loaded.finished_at is not None
    assert len(loaded.projects) == 1


def test_load_run_missing_raises(tmp_path: Path) -> None:
    runs_path = tmp_path / "runs"
    runs_path.mkdir(parents=True)
    with patch("agent_takt_fleet.runlog.runs_dir", return_value=runs_path):
        with pytest.raises(RunLogError, match="Run not found"):
            load_run("FR-nonexistent")


def test_load_run_unknown_version_raises(tmp_path: Path) -> None:
    runs_path = tmp_path / "runs"
    runs_path.mkdir(parents=True)
    bad = runs_path / "FR-ffffffff.json"
    bad.write_text(json.dumps({"version": 99, "run_id": "FR-ffffffff"}))
    with patch("agent_takt_fleet.runlog.runs_dir", return_value=runs_path):
        with pytest.raises(RunLogError, match="Could not load"):
            load_run("FR-ffffffff")


def test_load_run_missing_version_raises(tmp_path: Path) -> None:
    runs_path = tmp_path / "runs"
    runs_path.mkdir(parents=True)
    bad = runs_path / "FR-eeeeeee0.json"
    bad.write_text(json.dumps({"run_id": "FR-eeeeeee0"}))
    with patch("agent_takt_fleet.runlog.runs_dir", return_value=runs_path):
        with pytest.raises(RunLogError, match="Could not load"):
            load_run("FR-eeeeeee0")


# ── resolve_run_id ─────────────────────────────────────────────────────────────


def test_resolve_run_id_exact_match(tmp_path: Path) -> None:
    runs_path = tmp_path / "runs"
    run = _make_run("FR-a1b2c3d4")
    with patch("agent_takt_fleet.runlog.runs_dir", return_value=runs_path):
        write_run(run)
        result = resolve_run_id("FR-a1b2c3d4")
    assert result == "FR-a1b2c3d4"


def test_resolve_run_id_prefix_match(tmp_path: Path) -> None:
    runs_path = tmp_path / "runs"
    run = _make_run("FR-a1b2c3d4")
    with patch("agent_takt_fleet.runlog.runs_dir", return_value=runs_path):
        write_run(run)
        result = resolve_run_id("FR-a1b2")
    assert result == "FR-a1b2c3d4"


def test_resolve_run_id_no_match_raises(tmp_path: Path) -> None:
    runs_path = tmp_path / "runs"
    runs_path.mkdir(parents=True)
    with patch("agent_takt_fleet.runlog.runs_dir", return_value=runs_path):
        with pytest.raises(RunLogError, match="No run found"):
            resolve_run_id("FR-zzzzzzzz")


def test_resolve_run_id_ambiguous_raises(tmp_path: Path) -> None:
    runs_path = tmp_path / "runs"
    run1 = _make_run("FR-a1111111")
    run2 = _make_run("FR-a1222222")
    with patch("agent_takt_fleet.runlog.runs_dir", return_value=runs_path):
        write_run(run1)
        write_run(run2)
        with pytest.raises(RunLogError, match="ambiguous"):
            resolve_run_id("FR-a1")


def test_resolve_run_id_empty_dir_raises(tmp_path: Path) -> None:
    runs_path = tmp_path / "runs"
    runs_path.mkdir(parents=True)
    with patch("agent_takt_fleet.runlog.runs_dir", return_value=runs_path):
        with pytest.raises(RunLogError):
            resolve_run_id("FR-a1b2c3d4")


# ── compute_run_status ─────────────────────────────────────────────────────────


def test_status_in_progress() -> None:
    run = _make_run(finished_at=None)
    assert compute_run_status(run) == "in_progress"


def test_status_success_all_projects_ok() -> None:
    now = _utcnow()
    run = _make_run(
        finished_at=now + timedelta(minutes=1),
        projects=[
            _make_project_result("a", "success"),
            _make_project_result("b", "success"),
        ],
    )
    assert compute_run_status(run) == "success"


def test_status_success_all_skipped() -> None:
    now = _utcnow()
    run = _make_run(
        finished_at=now + timedelta(minutes=1),
        projects=[_make_project_result("a", "skipped")],
    )
    assert compute_run_status(run) == "success"


def test_status_error_all_failed() -> None:
    now = _utcnow()
    run = _make_run(
        finished_at=now + timedelta(minutes=1),
        projects=[
            _make_project_result("a", "error"),
            _make_project_result("b", "error"),
        ],
    )
    assert compute_run_status(run) == "error"


def test_status_partial_mixed() -> None:
    now = _utcnow()
    run = _make_run(
        finished_at=now + timedelta(minutes=1),
        projects=[
            _make_project_result("a", "success"),
            _make_project_result("b", "error"),
        ],
    )
    assert compute_run_status(run) == "partial"


# ── _parse_duration ────────────────────────────────────────────────────────────


def test_parse_duration_seconds() -> None:
    assert _parse_duration("30s") == timedelta(seconds=30)


def test_parse_duration_minutes() -> None:
    assert _parse_duration("5m") == timedelta(minutes=5)


def test_parse_duration_hours() -> None:
    assert _parse_duration("24h") == timedelta(hours=24)


def test_parse_duration_days() -> None:
    assert _parse_duration("7d") == timedelta(days=7)


def test_parse_duration_invalid_unit_raises() -> None:
    with pytest.raises(ValueError, match="Unknown duration unit"):
        _parse_duration("5x")


def test_parse_duration_invalid_value_raises() -> None:
    with pytest.raises(ValueError, match="Invalid duration"):
        _parse_duration("abch")


def test_parse_duration_empty_raises() -> None:
    with pytest.raises(ValueError):
        _parse_duration("")


# ── list_runs ──────────────────────────────────────────────────────────────────


def test_list_runs_empty_dir(tmp_path: Path) -> None:
    runs_path = tmp_path / "runs"
    with patch("agent_takt_fleet.runlog.runs_dir", return_value=runs_path):
        result = list_runs()
    assert result == []


def test_list_runs_most_recent_first(tmp_path: Path) -> None:
    runs_path = tmp_path / "runs"
    now = _utcnow()
    run_old = _make_run("FR-00000010", started_at=now - timedelta(hours=2), finished_at=now - timedelta(hours=1))
    run_new = _make_run("FR-00000011", started_at=now - timedelta(minutes=10), finished_at=now)
    with patch("agent_takt_fleet.runlog.runs_dir", return_value=runs_path):
        write_run(run_old)
        write_run(run_new)
        result = list_runs()
    assert result[0].run_id == "FR-00000011"
    assert result[1].run_id == "FR-00000010"


def test_list_runs_limit(tmp_path: Path) -> None:
    runs_path = tmp_path / "runs"
    now = _utcnow()
    with patch("agent_takt_fleet.runlog.runs_dir", return_value=runs_path):
        for i in range(5):
            run = _make_run(f"FR-0000001{i}", started_at=now + timedelta(seconds=i))
            write_run(run)
        result = list_runs(limit=3)
    assert len(result) == 3


def test_list_runs_filter_by_command(tmp_path: Path) -> None:
    runs_path = tmp_path / "runs"
    with patch("agent_takt_fleet.runlog.runs_dir", return_value=runs_path):
        write_run(_make_run("FR-00000020", command="dispatch"))
        write_run(_make_run("FR-00000021", command="run"))
        result = list_runs(command="dispatch")
    assert all(r.command == "dispatch" for r in result)
    assert len(result) == 1


def test_list_runs_filter_by_status(tmp_path: Path) -> None:
    now = _utcnow()
    runs_path = tmp_path / "runs"
    with patch("agent_takt_fleet.runlog.runs_dir", return_value=runs_path):
        # in_progress
        write_run(_make_run("FR-00000030", finished_at=None))
        # success
        run_done = _make_run(
            "FR-00000031",
            finished_at=now,
            projects=[_make_project_result("a", "success")],
        )
        write_run(run_done)
        in_progress = list_runs(status="in_progress")
        success = list_runs(status="success")
    assert any(r.run_id == "FR-00000030" for r in in_progress)
    assert any(r.run_id == "FR-00000031" for r in success)


def test_list_runs_filter_by_since(tmp_path: Path) -> None:
    runs_path = tmp_path / "runs"
    now = _utcnow()
    run_recent = _make_run("FR-00000040", started_at=now - timedelta(minutes=30))
    run_old = _make_run("FR-00000041", started_at=now - timedelta(hours=48))
    with patch("agent_takt_fleet.runlog.runs_dir", return_value=runs_path):
        write_run(run_recent)
        write_run(run_old)
        result = list_runs(since="1h")
    assert any(r.run_id == "FR-00000040" for r in result)
    assert not any(r.run_id == "FR-00000041" for r in result)


def test_list_runs_skips_unknown_version_files(tmp_path: Path) -> None:
    runs_path = tmp_path / "runs"
    runs_path.mkdir(parents=True)
    bad = runs_path / "FR-badbadba.json"
    bad.write_text(json.dumps({"version": 999, "run_id": "FR-badbadba"}))
    run = _make_run("FR-00000050")
    with patch("agent_takt_fleet.runlog.runs_dir", return_value=runs_path):
        write_run(run)
        result = list_runs()
    ids = [r.run_id for r in result]
    assert "FR-00000050" in ids
    assert "FR-badbadba" not in ids
