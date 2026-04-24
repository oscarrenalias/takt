from __future__ import annotations

import json
import sys
from argparse import Namespace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_takt_fleet.models import FleetRun, ProjectResult, RunInputs
from agent_takt_fleet.runlog import RunLogError, write_run
from agent_takt_fleet.cli.commands.runs import command_runs_list, command_runs_show


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
        finished_at=now + timedelta(seconds=45),
        error=None if status != "error" else "something went wrong",
        outputs={"created_beads": ["B-abcd1234"] if status == "success" else None, "run_summary": None},
    )


def _list_args(**kwargs) -> Namespace:
    defaults = {"limit": 20, "since": None, "status": None, "command": None, "plain": False}
    defaults.update(kwargs)
    return Namespace(**defaults)


def _show_args(run_id: str = "FR-a1b2c3d4", output_json: bool = False) -> Namespace:
    return Namespace(run_id=run_id, output_json=output_json)


# ── command_runs_list ──────────────────────────────────────────────────────────


def test_runs_list_no_runs_prints_message(tmp_path: Path, capsys) -> None:
    runs_path = tmp_path / "runs"
    with patch("agent_takt_fleet.runlog.runs_dir", return_value=runs_path):
        rc = command_runs_list(_list_args())
    assert rc == 0
    captured = capsys.readouterr()
    assert "No fleet runs" in captured.err


def test_runs_list_shows_runs(tmp_path: Path, capsys) -> None:
    runs_path = tmp_path / "runs"
    now = _utcnow()
    run = _make_run("FR-a1b2c3d4", finished_at=now, projects=[_make_project_result()])
    with patch("agent_takt_fleet.runlog.runs_dir", return_value=runs_path):
        write_run(run)
        rc = command_runs_list(_list_args())
    assert rc == 0
    out = capsys.readouterr().out
    assert "FR-a1b2c3d4" in out


def test_runs_list_plain_mode(tmp_path: Path, capsys) -> None:
    runs_path = tmp_path / "runs"
    now = _utcnow()
    run = _make_run("FR-b1b2c3d4", finished_at=now)
    with patch("agent_takt_fleet.runlog.runs_dir", return_value=runs_path):
        write_run(run)
        rc = command_runs_list(_list_args(plain=True))
    assert rc == 0
    out = capsys.readouterr().out
    # Plain mode uses tabs
    assert "\t" in out


def test_runs_list_filter_by_command(tmp_path: Path, capsys) -> None:
    runs_path = tmp_path / "runs"
    now = _utcnow()
    with patch("agent_takt_fleet.runlog.runs_dir", return_value=runs_path):
        write_run(_make_run("FR-c1c2c3c4", command="dispatch", finished_at=now))
        write_run(_make_run("FR-d1d2d3d4", command="run", finished_at=now))
        rc = command_runs_list(_list_args(command="dispatch"))
    assert rc == 0
    out = capsys.readouterr().out
    assert "FR-c1c2c3c4" in out
    assert "FR-d1d2d3d4" not in out


def test_runs_list_filter_by_status(tmp_path: Path, capsys) -> None:
    runs_path = tmp_path / "runs"
    now = _utcnow()
    run_done = _make_run(
        "FR-e1e2e3e4",
        finished_at=now,
        projects=[_make_project_result("a", "success")],
    )
    run_progress = _make_run("FR-f1f2f3f4", finished_at=None)
    with patch("agent_takt_fleet.runlog.runs_dir", return_value=runs_path):
        write_run(run_done)
        write_run(run_progress)
        rc_success = command_runs_list(_list_args(status="success"))
    assert rc_success == 0
    out = capsys.readouterr().out
    assert "FR-e1e2e3e4" in out
    assert "FR-f1f2f3f4" not in out


def test_runs_list_respects_limit(tmp_path: Path, capsys) -> None:
    runs_path = tmp_path / "runs"
    now = _utcnow()
    with patch("agent_takt_fleet.runlog.runs_dir", return_value=runs_path):
        for i in range(5):
            run = _make_run(f"FR-0000000{i}", started_at=now + timedelta(seconds=i))
            write_run(run)
        rc = command_runs_list(_list_args(limit=2))
    assert rc == 0
    out = capsys.readouterr().out
    # Count how many FR- IDs appear in the data rows (skip header)
    data_rows = [line for line in out.splitlines() if "FR-" in line]
    assert len(data_rows) == 2


# ── command_runs_show ──────────────────────────────────────────────────────────


def test_runs_show_unknown_id_returns_error(tmp_path: Path, capsys) -> None:
    runs_path = tmp_path / "runs"
    runs_path.mkdir(parents=True)
    with patch("agent_takt_fleet.runlog.runs_dir", return_value=runs_path):
        rc = command_runs_show(_show_args("FR-zzzzzzzz"))
    assert rc == 1
    assert "error" in capsys.readouterr().err.lower()


def test_runs_show_json_flag_dumps_raw(tmp_path: Path, capsys) -> None:
    runs_path = tmp_path / "runs"
    now = _utcnow()
    run = _make_run("FR-a1b2c3d4", finished_at=now)
    with patch("agent_takt_fleet.runlog.runs_dir", return_value=runs_path):
        write_run(run)
        rc = command_runs_show(_show_args("FR-a1b2c3d4", output_json=True))
    assert rc == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["run_id"] == "FR-a1b2c3d4"
    assert "version" in data


def test_runs_show_json_with_prefix(tmp_path: Path, capsys) -> None:
    runs_path = tmp_path / "runs"
    now = _utcnow()
    run = _make_run("FR-a1b2c3d4", finished_at=now)
    with patch("agent_takt_fleet.runlog.runs_dir", return_value=runs_path):
        write_run(run)
        rc = command_runs_show(_show_args("FR-a1b2", output_json=True))
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["run_id"] == "FR-a1b2c3d4"


def test_runs_show_completed_run_prints_breakdown(tmp_path: Path, capsys) -> None:
    runs_path = tmp_path / "runs"
    now = _utcnow()
    run = _make_run(
        "FR-a1b2c3d4",
        finished_at=now,
        projects=[
            _make_project_result("api-svc", "success"),
            _make_project_result("web-ui", "error"),
        ],
    )
    with patch("agent_takt_fleet.runlog.runs_dir", return_value=runs_path):
        write_run(run)
        rc = command_runs_show(_show_args("FR-a1b2c3d4"))
    assert rc == 0
    out = capsys.readouterr().out
    assert "Fleet Run FR-a1b2c3d4" in out
    assert "api-svc" in out
    assert "web-ui" in out
    assert "Aggregate" in out


def test_runs_show_completed_run_shows_glyph(tmp_path: Path, capsys) -> None:
    runs_path = tmp_path / "runs"
    now = _utcnow()
    run = _make_run(
        "FR-a1b2c3d5",
        finished_at=now,
        projects=[_make_project_result("svc", "success")],
    )
    with patch("agent_takt_fleet.runlog.runs_dir", return_value=runs_path):
        write_run(run)
        rc = command_runs_show(_show_args("FR-a1b2c3d5"))
    assert rc == 0
    out = capsys.readouterr().out
    assert "✓" in out


def test_runs_show_completed_dispatch_shows_bead_id(tmp_path: Path, capsys) -> None:
    runs_path = tmp_path / "runs"
    now = _utcnow()
    run = FleetRun(
        run_id="FR-a1b2c3d6",
        command="dispatch",
        started_at=now - timedelta(minutes=3),
        finished_at=now,
        inputs=_make_inputs(bead={"title": "Check deps", "agent_type": "developer", "labels": []}),
        projects=[_make_project_result("api-svc", "success")],
        crashed=False,
    )
    with patch("agent_takt_fleet.runlog.runs_dir", return_value=runs_path):
        write_run(run)
        rc = command_runs_show(_show_args("FR-a1b2c3d6"))
    assert rc == 0
    out = capsys.readouterr().out
    assert "B-abcd1234" in out
    assert "Check deps" in out


def test_runs_show_ambiguous_prefix_returns_error(tmp_path: Path, capsys) -> None:
    runs_path = tmp_path / "runs"
    with patch("agent_takt_fleet.runlog.runs_dir", return_value=runs_path):
        write_run(_make_run("FR-a1111111"))
        write_run(_make_run("FR-a1222222"))
        rc = command_runs_show(_show_args("FR-a1"))
    assert rc == 1
    assert "error" in capsys.readouterr().err.lower()
