from __future__ import annotations

import sys
from argparse import Namespace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_takt_fleet.adapter import AdapterError
from agent_takt_fleet.cli.commands.dispatch import command_dispatch
from agent_takt_fleet.models import Project


# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_project(name: str, tmp_path: Path) -> Project:
    d = tmp_path / name
    d.mkdir(exist_ok=True)
    return Project(name=name, path=d, tags=())


def _dispatch_args(**kwargs) -> Namespace:
    defaults = {
        "title": "Check dependencies",
        "description": "Audit third-party deps for CVEs",
        "agent": "developer",
        "label": [],
        "max_parallel": None,
        "tag": [],
        "project": [],
    }
    defaults.update(kwargs)
    return Namespace(**defaults)


# ── No-projects edge cases ────────────────────────────────────────────────────


def test_dispatch_no_projects_registered(capsys) -> None:
    with (
        patch("agent_takt_fleet.cli.commands.dispatch.load_registry", return_value=[]),
        patch("agent_takt_fleet.cli.commands.dispatch.write_run"),
        patch("agent_takt_fleet.cli.commands.dispatch.new_run_id", return_value="FR-nomatch"),
    ):
        rc = command_dispatch(_dispatch_args())
    assert rc == 0
    err = capsys.readouterr().err
    assert "No projects registered" in err


def test_dispatch_no_filter_match(tmp_path: Path, capsys) -> None:
    project = _make_project("api-svc", tmp_path)
    with (
        patch("agent_takt_fleet.cli.commands.dispatch.load_registry", return_value=[project]),
        patch("agent_takt_fleet.cli.commands.dispatch.write_run"),
        patch("agent_takt_fleet.cli.commands.dispatch.new_run_id", return_value="FR-nomatch"),
    ):
        rc = command_dispatch(_dispatch_args(tag=["nonexistent"]))
    assert rc == 0
    assert "No projects match" in capsys.readouterr().err


# ── Registry error ─────────────────────────────────────────────────────────────


def test_dispatch_registry_error_returns_1(capsys) -> None:
    from agent_takt_fleet.registry import RegistryError

    with patch(
        "agent_takt_fleet.cli.commands.dispatch.load_registry",
        side_effect=RegistryError("corrupt registry"),
    ):
        rc = command_dispatch(_dispatch_args())
    assert rc == 1
    assert "error" in capsys.readouterr().err.lower()


# ── Happy path ────────────────────────────────────────────────────────────────


def test_dispatch_creates_bead_per_project(tmp_path: Path, capsys) -> None:
    projects = [_make_project("api-svc", tmp_path), _make_project("web-ui", tmp_path)]
    written_runs = []

    with (
        patch("agent_takt_fleet.cli.commands.dispatch.load_registry", return_value=projects),
        patch("agent_takt_fleet.cli.commands.dispatch.TaktAdapter") as MockAdapter,
        patch("agent_takt_fleet.cli.commands.dispatch.write_run", side_effect=written_runs.append),
        patch("agent_takt_fleet.cli.commands.dispatch.new_run_id", return_value="FR-test1234"),
    ):
        MockAdapter.return_value.create_bead.side_effect = ["B-aaa00001", "B-bbb00002"]
        rc = command_dispatch(_dispatch_args())

    assert rc == 0
    # write_run called twice: initial + final
    assert len(written_runs) == 2
    final_run = written_runs[-1]
    assert final_run.run_id == "FR-test1234"
    assert final_run.command == "dispatch"
    assert final_run.finished_at is not None
    assert len(final_run.projects) == 2
    statuses = {p.name: p.status for p in final_run.projects}
    assert statuses["api-svc"] == "success"
    assert statuses["web-ui"] == "success"


def test_dispatch_records_bead_ids_in_run_log(tmp_path: Path, capsys) -> None:
    project = _make_project("api-svc", tmp_path)
    written_runs = []

    with (
        patch("agent_takt_fleet.cli.commands.dispatch.load_registry", return_value=[project]),
        patch("agent_takt_fleet.cli.commands.dispatch.TaktAdapter") as MockAdapter,
        patch("agent_takt_fleet.cli.commands.dispatch.write_run", side_effect=written_runs.append),
        patch("agent_takt_fleet.cli.commands.dispatch.new_run_id", return_value="FR-test1234"),
    ):
        MockAdapter.return_value.create_bead.return_value = "B-abc12345"
        rc = command_dispatch(_dispatch_args())

    assert rc == 0
    final_run = written_runs[-1]
    pr = final_run.projects[0]
    assert pr.outputs["created_beads"] == ["B-abc12345"]


def test_dispatch_passes_correct_args_to_adapter(tmp_path: Path, capsys) -> None:
    project = _make_project("api-svc", tmp_path)
    written_runs = []

    with (
        patch("agent_takt_fleet.cli.commands.dispatch.load_registry", return_value=[project]),
        patch("agent_takt_fleet.cli.commands.dispatch.TaktAdapter") as MockAdapter,
        patch("agent_takt_fleet.cli.commands.dispatch.write_run", side_effect=written_runs.append),
        patch("agent_takt_fleet.cli.commands.dispatch.new_run_id", return_value="FR-xx"),
    ):
        MockAdapter.return_value.create_bead.return_value = "B-aaa00001"
        rc = command_dispatch(
            _dispatch_args(
                title="Fix CVEs",
                description="Upgrade deps",
                agent="tester",
                label=["urgent", "security"],
            )
        )

    assert rc == 0
    MockAdapter.return_value.create_bead.assert_called_once_with(
        title="Fix CVEs",
        description="Upgrade deps",
        agent_type="tester",
        labels=["urgent", "security"],
    )


# ── Failure handling ──────────────────────────────────────────────────────────


def test_dispatch_adapter_error_recorded_as_project_error(tmp_path: Path, capsys) -> None:
    project = _make_project("broken-svc", tmp_path)
    written_runs = []

    with (
        patch("agent_takt_fleet.cli.commands.dispatch.load_registry", return_value=[project]),
        patch("agent_takt_fleet.cli.commands.dispatch.TaktAdapter") as MockAdapter,
        patch("agent_takt_fleet.cli.commands.dispatch.write_run", side_effect=written_runs.append),
        patch("agent_takt_fleet.cli.commands.dispatch.new_run_id", return_value="FR-err1"),
    ):
        MockAdapter.return_value.create_bead.side_effect = AdapterError("takt not found")
        rc = command_dispatch(_dispatch_args())

    assert rc == 0  # command succeeds even when projects fail
    final_run = written_runs[-1]
    pr = final_run.projects[0]
    assert pr.status == "error"
    assert pr.outputs["created_beads"] is None
    assert "takt not found" in (pr.error or "")


def test_dispatch_partial_failure_reflected_in_aggregate(tmp_path: Path, capsys) -> None:
    projects = [
        _make_project("good-svc", tmp_path),
        _make_project("bad-svc", tmp_path),
    ]
    written_runs = []

    def _create_bead_side_effect(*args, **kwargs):
        # MockAdapter is called once per project; distinguish by cwd
        ...

    with (
        patch("agent_takt_fleet.cli.commands.dispatch.load_registry", return_value=projects),
        patch("agent_takt_fleet.cli.commands.dispatch.TaktAdapter") as MockAdapter,
        patch("agent_takt_fleet.cli.commands.dispatch.write_run", side_effect=written_runs.append),
        patch("agent_takt_fleet.cli.commands.dispatch.new_run_id", return_value="FR-partial"),
    ):
        # First call succeeds, second raises
        MockAdapter.return_value.create_bead.side_effect = [
            "B-good0001",
            AdapterError("bad project"),
        ]
        rc = command_dispatch(_dispatch_args(max_parallel=1))

    assert rc == 0
    final_run = written_runs[-1]
    agg = final_run.aggregate
    assert agg["total"] == 2
    assert agg["succeeded"] == 1
    assert agg["failed"] == 1

    out = capsys.readouterr().out
    assert "1 succeeded" in out
    assert "1 failed" in out


# ── KeyboardInterrupt handling ────────────────────────────────────────────────


def test_dispatch_keyboard_interrupt_stamps_crashed_run(tmp_path: Path, capsys) -> None:
    project = _make_project("api-svc", tmp_path)
    written_runs = []

    with (
        patch("agent_takt_fleet.cli.commands.dispatch.load_registry", return_value=[project]),
        patch("agent_takt_fleet.cli.commands.dispatch.write_run", side_effect=written_runs.append),
        patch("agent_takt_fleet.cli.commands.dispatch.new_run_id", return_value="FR-intr"),
        patch(
            "agent_takt_fleet.cli.commands.dispatch.fan_out",
            side_effect=KeyboardInterrupt,
        ),
    ):
        rc = command_dispatch(_dispatch_args())

    assert rc == 130
    # write_run called twice: initial (before fan_out) + final (after interrupt)
    assert len(written_runs) == 2
    final_run = written_runs[-1]
    assert final_run.crashed is True
    assert final_run.finished_at is not None
    assert "Interrupted" in capsys.readouterr().err


# ── max_parallel defaults ─────────────────────────────────────────────────────


def test_dispatch_max_parallel_defaults_to_min_projects_4(tmp_path: Path, capsys) -> None:
    projects = [_make_project(f"svc-{i}", tmp_path) for i in range(6)]
    written_runs = []
    captured_max_parallel = []

    original_fan_out = __import__(
        "agent_takt_fleet.executor", fromlist=["fan_out"]
    ).fan_out

    def _capturing_fan_out(items, fn, max_parallel):
        captured_max_parallel.append(max_parallel)
        return original_fan_out(items, fn, max_parallel)

    with (
        patch("agent_takt_fleet.cli.commands.dispatch.load_registry", return_value=projects),
        patch("agent_takt_fleet.cli.commands.dispatch.TaktAdapter") as MockAdapter,
        patch("agent_takt_fleet.cli.commands.dispatch.write_run", side_effect=written_runs.append),
        patch("agent_takt_fleet.cli.commands.dispatch.new_run_id", return_value="FR-par"),
        patch("agent_takt_fleet.cli.commands.dispatch.fan_out", side_effect=_capturing_fan_out),
    ):
        MockAdapter.return_value.create_bead.return_value = "B-0000aaaa"
        rc = command_dispatch(_dispatch_args(max_parallel=None))

    assert rc == 0
    assert captured_max_parallel == [4]  # min(6, 4)


def test_dispatch_max_parallel_explicit_overrides_default(tmp_path: Path, capsys) -> None:
    projects = [_make_project(f"svc-{i}", tmp_path) for i in range(3)]
    written_runs = []
    captured_max_parallel = []

    original_fan_out = __import__(
        "agent_takt_fleet.executor", fromlist=["fan_out"]
    ).fan_out

    def _capturing_fan_out(items, fn, max_parallel):
        captured_max_parallel.append(max_parallel)
        return original_fan_out(items, fn, max_parallel)

    with (
        patch("agent_takt_fleet.cli.commands.dispatch.load_registry", return_value=projects),
        patch("agent_takt_fleet.cli.commands.dispatch.TaktAdapter") as MockAdapter,
        patch("agent_takt_fleet.cli.commands.dispatch.write_run", side_effect=written_runs.append),
        patch("agent_takt_fleet.cli.commands.dispatch.new_run_id", return_value="FR-par2"),
        patch("agent_takt_fleet.cli.commands.dispatch.fan_out", side_effect=_capturing_fan_out),
    ):
        MockAdapter.return_value.create_bead.return_value = "B-0000bbbb"
        rc = command_dispatch(_dispatch_args(max_parallel=2))

    assert rc == 0
    assert captured_max_parallel == [2]


# ── Run log inputs ────────────────────────────────────────────────────────────


def test_dispatch_run_log_records_inputs(tmp_path: Path, capsys) -> None:
    project = _make_project("api-svc", tmp_path)
    written_runs = []

    with (
        patch("agent_takt_fleet.cli.commands.dispatch.load_registry", return_value=[project]),
        patch("agent_takt_fleet.cli.commands.dispatch.TaktAdapter") as MockAdapter,
        patch("agent_takt_fleet.cli.commands.dispatch.write_run", side_effect=written_runs.append),
        patch("agent_takt_fleet.cli.commands.dispatch.new_run_id", return_value="FR-inputs"),
    ):
        MockAdapter.return_value.create_bead.return_value = "B-abc12345"
        rc = command_dispatch(
            _dispatch_args(
                title="My task",
                description="Do the thing",
                agent="documentation",
                label=["batch"],
                tag=["python"],
            )
        )

    assert rc == 0
    final_run = written_runs[-1]
    inputs = final_run.inputs
    assert inputs.bead == {"title": "My task", "agent_type": "documentation", "labels": ["batch"]}
    assert inputs.tag_filter == ("python",)
    assert inputs.project_filter == ()


# ── Output formatting ─────────────────────────────────────────────────────────


def test_dispatch_output_shows_project_and_bead(tmp_path: Path, capsys) -> None:
    project = _make_project("api-svc", tmp_path)

    with (
        patch("agent_takt_fleet.cli.commands.dispatch.load_registry", return_value=[project]),
        patch("agent_takt_fleet.cli.commands.dispatch.TaktAdapter") as MockAdapter,
        patch("agent_takt_fleet.cli.commands.dispatch.write_run"),
        patch("agent_takt_fleet.cli.commands.dispatch.new_run_id", return_value="FR-out"),
    ):
        MockAdapter.return_value.create_bead.return_value = "B-abc12345"
        rc = command_dispatch(_dispatch_args())

    assert rc == 0
    out = capsys.readouterr().out
    assert "api-svc" in out
    assert "B-abc12345" in out
    assert "success" in out


def test_dispatch_output_shows_error_message(tmp_path: Path, capsys) -> None:
    project = _make_project("bad-svc", tmp_path)

    with (
        patch("agent_takt_fleet.cli.commands.dispatch.load_registry", return_value=[project]),
        patch("agent_takt_fleet.cli.commands.dispatch.TaktAdapter") as MockAdapter,
        patch("agent_takt_fleet.cli.commands.dispatch.write_run"),
        patch("agent_takt_fleet.cli.commands.dispatch.new_run_id", return_value="FR-err"),
    ):
        MockAdapter.return_value.create_bead.side_effect = AdapterError("connection refused")
        rc = command_dispatch(_dispatch_args())

    assert rc == 0
    out = capsys.readouterr().out
    assert "bad-svc" in out
    assert "error" in out
