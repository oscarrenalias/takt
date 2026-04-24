from __future__ import annotations

import concurrent.futures
import sys
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_takt_fleet.adapter import AdapterError
from agent_takt_fleet.cli.commands.run import command_run
from agent_takt_fleet.models import Project


# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_project(name: str, tmp_path: Path) -> Project:
    d = tmp_path / name
    d.mkdir(exist_ok=True)
    return Project(name=name, path=d, tags=())


def _run_args(**kwargs) -> Namespace:
    defaults = {
        "max_parallel": None,
        "runner": None,
        "project_max_workers": None,
        "tag": [],
        "project": [],
    }
    defaults.update(kwargs)
    return Namespace(**defaults)


def _run_summary(done: int = 1, blocked: int = 0) -> dict:
    return {
        "started": [],
        "completed": [],
        "blocked": [],
        "correctives_created": [],
        "deferred_count": 0,
        "final_state": {"done": done, "blocked": blocked, "ready": 0, "in_progress": 0},
    }


# ── No-projects edge cases ────────────────────────────────────────────────────


def test_run_no_projects_registered(capsys) -> None:
    with (
        patch("agent_takt_fleet.cli.commands.run.load_registry", return_value=[]),
        patch("agent_takt_fleet.cli.commands.run.write_run"),
        patch("agent_takt_fleet.cli.commands.run.new_run_id", return_value="FR-nomatch"),
    ):
        rc = command_run(_run_args())
    assert rc == 0
    assert "No projects registered" in capsys.readouterr().err


def test_run_no_filter_match(tmp_path: Path, capsys) -> None:
    project = _make_project("api-svc", tmp_path)
    with (
        patch("agent_takt_fleet.cli.commands.run.load_registry", return_value=[project]),
        patch("agent_takt_fleet.cli.commands.run.write_run"),
        patch("agent_takt_fleet.cli.commands.run.new_run_id", return_value="FR-nomatch"),
    ):
        rc = command_run(_run_args(tag=["nonexistent"]))
    assert rc == 0
    assert "No projects match" in capsys.readouterr().err


# ── Registry error ─────────────────────────────────────────────────────────────


def test_run_registry_error_returns_1(capsys) -> None:
    from agent_takt_fleet.registry import RegistryError

    with patch(
        "agent_takt_fleet.cli.commands.run.load_registry",
        side_effect=RegistryError("corrupt registry"),
    ):
        rc = command_run(_run_args())
    assert rc == 1
    assert "error" in capsys.readouterr().err.lower()


# ── Happy path ────────────────────────────────────────────────────────────────


def test_run_fans_out_to_all_projects(tmp_path: Path, capsys) -> None:
    projects = [_make_project("api-svc", tmp_path), _make_project("web-ui", tmp_path)]
    written_runs = []

    with (
        patch("agent_takt_fleet.cli.commands.run.load_registry", return_value=projects),
        patch("agent_takt_fleet.cli.commands.run.TaktAdapter") as MockAdapter,
        patch("agent_takt_fleet.cli.commands.run.write_run", side_effect=written_runs.append),
        patch("agent_takt_fleet.cli.commands.run.new_run_id", return_value="FR-test1234"),
    ):
        MockAdapter.return_value.run.return_value = _run_summary()
        rc = command_run(_run_args())

    assert rc == 0
    # write_run called: initial + once per project + final
    assert len(written_runs) >= 3
    final_run = written_runs[-1]
    assert final_run.run_id == "FR-test1234"
    assert final_run.command == "run"
    assert final_run.finished_at is not None
    assert not final_run.crashed
    assert len(final_run.projects) == 2
    statuses = {p.name: p.status for p in final_run.projects}
    assert statuses["api-svc"] == "success"
    assert statuses["web-ui"] == "success"


def test_run_captures_run_summary_in_run_log(tmp_path: Path) -> None:
    project = _make_project("api-svc", tmp_path)
    written_runs = []
    summary = _run_summary(done=3, blocked=1)

    with (
        patch("agent_takt_fleet.cli.commands.run.load_registry", return_value=[project]),
        patch("agent_takt_fleet.cli.commands.run.TaktAdapter") as MockAdapter,
        patch("agent_takt_fleet.cli.commands.run.write_run", side_effect=written_runs.append),
        patch("agent_takt_fleet.cli.commands.run.new_run_id", return_value="FR-cap"),
    ):
        MockAdapter.return_value.run.return_value = summary
        rc = command_run(_run_args())

    assert rc == 0
    final_run = written_runs[-1]
    pr = final_run.projects[0]
    assert pr.outputs["run_summary"] == summary
    assert pr.outputs["created_beads"] is None


def test_run_forwards_runner_and_max_workers(tmp_path: Path) -> None:
    project = _make_project("svc", tmp_path)
    written_runs = []

    with (
        patch("agent_takt_fleet.cli.commands.run.load_registry", return_value=[project]),
        patch("agent_takt_fleet.cli.commands.run.TaktAdapter") as MockAdapter,
        patch("agent_takt_fleet.cli.commands.run.write_run", side_effect=written_runs.append),
        patch("agent_takt_fleet.cli.commands.run.new_run_id", return_value="FR-fwd"),
    ):
        MockAdapter.return_value.run.return_value = _run_summary()
        rc = command_run(_run_args(runner="claude", project_max_workers=3))

    assert rc == 0
    MockAdapter.return_value.run.assert_called_once_with(runner="claude", max_workers=3)
    final_run = written_runs[-1]
    assert final_run.inputs.runner == "claude"
    assert final_run.inputs.project_max_workers == 3


def test_run_records_run_log_with_command_run(tmp_path: Path) -> None:
    project = _make_project("svc", tmp_path)
    written_runs = []

    with (
        patch("agent_takt_fleet.cli.commands.run.load_registry", return_value=[project]),
        patch("agent_takt_fleet.cli.commands.run.TaktAdapter") as MockAdapter,
        patch("agent_takt_fleet.cli.commands.run.write_run", side_effect=written_runs.append),
        patch("agent_takt_fleet.cli.commands.run.new_run_id", return_value="FR-cmd"),
    ):
        MockAdapter.return_value.run.return_value = _run_summary()
        command_run(_run_args(tag=["python"], project=["svc"]))

    final_run = written_runs[-1]
    assert final_run.command == "run"
    assert final_run.inputs.bead is None
    assert final_run.inputs.tag_filter == ("python",)
    assert final_run.inputs.project_filter == ("svc",)


# ── Failure handling ──────────────────────────────────────────────────────────


def test_run_adapter_error_recorded_as_project_error(tmp_path: Path, capsys) -> None:
    project = _make_project("broken-svc", tmp_path)
    written_runs = []

    with (
        patch("agent_takt_fleet.cli.commands.run.load_registry", return_value=[project]),
        patch("agent_takt_fleet.cli.commands.run.TaktAdapter") as MockAdapter,
        patch("agent_takt_fleet.cli.commands.run.write_run", side_effect=written_runs.append),
        patch("agent_takt_fleet.cli.commands.run.new_run_id", return_value="FR-err1"),
    ):
        MockAdapter.return_value.run.side_effect = AdapterError("takt not found")
        rc = command_run(_run_args())

    assert rc == 0
    final_run = written_runs[-1]
    pr = final_run.projects[0]
    assert pr.status == "error"
    assert pr.outputs["run_summary"] is None
    assert "takt not found" in (pr.error or "")


def test_run_partial_failure_reflected_in_aggregate(tmp_path: Path, capsys) -> None:
    projects = [_make_project("good-svc", tmp_path), _make_project("bad-svc", tmp_path)]
    written_runs = []

    instance_call_count = [0]

    def _run_side_effect(**kwargs):
        instance_call_count[0] += 1
        if instance_call_count[0] == 2:
            raise AdapterError("bad project")
        return _run_summary()

    with (
        patch("agent_takt_fleet.cli.commands.run.load_registry", return_value=projects),
        patch("agent_takt_fleet.cli.commands.run.TaktAdapter") as MockAdapter,
        patch("agent_takt_fleet.cli.commands.run.write_run", side_effect=written_runs.append),
        patch("agent_takt_fleet.cli.commands.run.new_run_id", return_value="FR-partial"),
    ):
        MockAdapter.return_value.run.side_effect = _run_side_effect
        rc = command_run(_run_args(max_parallel=1))

    assert rc == 0
    final_run = written_runs[-1]
    agg = final_run.aggregate
    assert agg["total"] == 2
    assert agg["succeeded"] == 1
    assert agg["failed"] == 1

    out = capsys.readouterr().out
    assert "1 succeeded" in out
    assert "1 failed" in out


# ── Incremental writes ────────────────────────────────────────────────────────


def test_run_writes_after_each_project_completes(tmp_path: Path) -> None:
    projects = [_make_project(f"svc-{i}", tmp_path) for i in range(3)]
    written_runs = []

    with (
        patch("agent_takt_fleet.cli.commands.run.load_registry", return_value=projects),
        patch("agent_takt_fleet.cli.commands.run.TaktAdapter") as MockAdapter,
        patch("agent_takt_fleet.cli.commands.run.write_run", side_effect=written_runs.append),
        patch("agent_takt_fleet.cli.commands.run.new_run_id", return_value="FR-incr"),
    ):
        MockAdapter.return_value.run.return_value = _run_summary()
        rc = command_run(_run_args(max_parallel=1))

    assert rc == 0
    # initial write + 3 per-project writes + 1 final write = 5 total
    assert len(written_runs) == 5


# ── max_parallel defaults ─────────────────────────────────────────────────────


def test_run_max_parallel_defaults_to_min_projects_4(tmp_path: Path) -> None:
    projects = [_make_project(f"svc-{i}", tmp_path) for i in range(6)]
    written_runs = []
    submitted_max_workers = []

    original_executor = concurrent.futures.ThreadPoolExecutor

    class _CapturingExecutor(original_executor):
        def __init__(self, max_workers=None, **kwargs):
            submitted_max_workers.append(max_workers)
            super().__init__(max_workers=max_workers, **kwargs)

    with (
        patch("agent_takt_fleet.cli.commands.run.load_registry", return_value=projects),
        patch("agent_takt_fleet.cli.commands.run.TaktAdapter") as MockAdapter,
        patch("agent_takt_fleet.cli.commands.run.write_run", side_effect=written_runs.append),
        patch("agent_takt_fleet.cli.commands.run.new_run_id", return_value="FR-par"),
        patch("concurrent.futures.ThreadPoolExecutor", _CapturingExecutor),
    ):
        MockAdapter.return_value.run.return_value = _run_summary()
        rc = command_run(_run_args(max_parallel=None))

    assert rc == 0
    assert submitted_max_workers == [4]  # min(6, 4)


def test_run_max_parallel_explicit_overrides_default(tmp_path: Path) -> None:
    projects = [_make_project(f"svc-{i}", tmp_path) for i in range(3)]
    written_runs = []
    submitted_max_workers = []

    original_executor = concurrent.futures.ThreadPoolExecutor

    class _CapturingExecutor(original_executor):
        def __init__(self, max_workers=None, **kwargs):
            submitted_max_workers.append(max_workers)
            super().__init__(max_workers=max_workers, **kwargs)

    with (
        patch("agent_takt_fleet.cli.commands.run.load_registry", return_value=projects),
        patch("agent_takt_fleet.cli.commands.run.TaktAdapter") as MockAdapter,
        patch("agent_takt_fleet.cli.commands.run.write_run", side_effect=written_runs.append),
        patch("agent_takt_fleet.cli.commands.run.new_run_id", return_value="FR-par2"),
        patch("concurrent.futures.ThreadPoolExecutor", _CapturingExecutor),
    ):
        MockAdapter.return_value.run.return_value = _run_summary()
        rc = command_run(_run_args(max_parallel=2))

    assert rc == 0
    assert submitted_max_workers == [2]


# ── Crash safety (Ctrl-C) ─────────────────────────────────────────────────────


def test_run_ctrl_c_marks_crashed_and_finalizes_run(tmp_path: Path, capsys) -> None:
    project = _make_project("svc", tmp_path)
    written_runs = []

    with (
        patch("agent_takt_fleet.cli.commands.run.load_registry", return_value=[project]),
        patch("agent_takt_fleet.cli.commands.run.TaktAdapter") as MockAdapter,
        patch("agent_takt_fleet.cli.commands.run.write_run", side_effect=written_runs.append),
        patch("agent_takt_fleet.cli.commands.run.new_run_id", return_value="FR-crash"),
        patch("concurrent.futures.as_completed", side_effect=KeyboardInterrupt),
    ):
        MockAdapter.return_value.run.return_value = _run_summary()
        rc = command_run(_run_args())

    assert rc == 130
    assert len(written_runs) >= 2  # initial + final
    final_run = written_runs[-1]
    assert final_run.crashed is True
    assert final_run.finished_at is not None
    assert "Interrupted" in capsys.readouterr().err


def test_run_ctrl_c_preserves_completed_projects(tmp_path: Path, capsys) -> None:
    projects = [_make_project("svc-a", tmp_path), _make_project("svc-b", tmp_path)]
    written_runs = []
    call_count = [0]

    real_as_completed = concurrent.futures.as_completed

    def _interrupt_after_first(fs, **kwargs):
        for future in real_as_completed(fs, **kwargs):
            call_count[0] += 1
            yield future
            raise KeyboardInterrupt

    with (
        patch("agent_takt_fleet.cli.commands.run.load_registry", return_value=projects),
        patch("agent_takt_fleet.cli.commands.run.TaktAdapter") as MockAdapter,
        patch("agent_takt_fleet.cli.commands.run.write_run", side_effect=written_runs.append),
        patch("agent_takt_fleet.cli.commands.run.new_run_id", return_value="FR-partial-crash"),
        patch("concurrent.futures.as_completed", _interrupt_after_first),
    ):
        MockAdapter.return_value.run.return_value = _run_summary()
        rc = command_run(_run_args(max_parallel=2))

    assert rc == 130
    final_run = written_runs[-1]
    assert final_run.crashed is True
    assert len(final_run.projects) == 1  # one completed before interrupt


# ── Output formatting ─────────────────────────────────────────────────────────


def test_run_output_shows_project_and_status(tmp_path: Path, capsys) -> None:
    project = _make_project("api-svc", tmp_path)

    with (
        patch("agent_takt_fleet.cli.commands.run.load_registry", return_value=[project]),
        patch("agent_takt_fleet.cli.commands.run.TaktAdapter") as MockAdapter,
        patch("agent_takt_fleet.cli.commands.run.write_run"),
        patch("agent_takt_fleet.cli.commands.run.new_run_id", return_value="FR-out"),
    ):
        MockAdapter.return_value.run.return_value = _run_summary(done=2, blocked=0)
        rc = command_run(_run_args())

    assert rc == 0
    out = capsys.readouterr().out
    assert "api-svc" in out
    assert "success" in out
    assert "1 succeeded" in out


def test_run_output_shows_done_and_blocked_counts(tmp_path: Path, capsys) -> None:
    project = _make_project("api-svc", tmp_path)

    with (
        patch("agent_takt_fleet.cli.commands.run.load_registry", return_value=[project]),
        patch("agent_takt_fleet.cli.commands.run.TaktAdapter") as MockAdapter,
        patch("agent_takt_fleet.cli.commands.run.write_run"),
        patch("agent_takt_fleet.cli.commands.run.new_run_id", return_value="FR-counts"),
    ):
        MockAdapter.return_value.run.return_value = _run_summary(done=5, blocked=2)
        rc = command_run(_run_args())

    assert rc == 0
    out = capsys.readouterr().out
    assert "5" in out
    assert "2" in out
