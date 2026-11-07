"""Tests for the fleet scheduler-run reader and `takt-fleet scheduler-runs list`.

Fixtures are tmp-path project directories with real {timestamp, event_type, payload} records;
the user's registry is never touched.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
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

from agent_takt_fleet.cli import build_parser
from agent_takt_fleet.cli.commands.scheduler_runs import command_scheduler_runs_list
from agent_takt_fleet.models import Project
from agent_takt_fleet.registry import save_registry
from agent_takt_fleet.scheduler_runs import collect_scheduler_runs, read_project_runs


def _ts(delta: timedelta = timedelta(0)) -> str:
    return (datetime.now(tz=timezone.utc) - delta).isoformat()


def _rec(event_type: str, payload: dict, ts: str | None = None) -> str:
    return json.dumps({"timestamp": ts or _ts(), "event_type": event_type, "payload": payload})


def _make_project(base: Path, name: str, lines: list[str] | None = None) -> Path:
    d = base / name
    (d / ".takt" / "logs").mkdir(parents=True)
    if lines is not None:
        (d / ".takt" / "logs" / "events.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return d


def _finished(run_id: str, reason: str = "quiescence", **extra) -> dict:
    return {
        "run_id": run_id, "exit_reason": reason, "cycles": 2, "started_count": 3,
        "completed_count": 2, "blocked_count": 1, "cost_usd": 1.5, "duration_seconds": 10.0, **extra,
    }


def _dead_pid() -> int:
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


def _marker(project: Path, run_id: str, pid: int) -> None:
    active = project / ".takt" / "runs" / "active"
    active.mkdir(parents=True, exist_ok=True)
    (active / f"{run_id}.json").write_text(
        json.dumps({"run_id": run_id, "pid": pid, "hostname": socket.gethostname()}), encoding="utf-8"
    )


# ── Reader ────────────────────────────────────────────────────────────────────


def test_finished_and_running_runs_across_projects(tmp_path: Path) -> None:
    """Scenario: fleet-scheduler-visibility / Scheduler run history / Finished and running runs across projects"""
    a = _make_project(tmp_path, "a", [
        _rec("scheduler_run_started", {"run_id": "SR-a1", "runner": "claude"}, _ts(timedelta(hours=2))),
        _rec("scheduler_run_finished", _finished("SR-a1"), _ts(timedelta(hours=1))),
    ])
    b = _make_project(tmp_path, "b", [
        _rec("scheduler_run_started", {"run_id": "SR-b1", "runner": "codex"}, _ts(timedelta(minutes=5))),
        _rec("scheduler_run_started", {"run_id": "SR-b2"}, _ts(timedelta(minutes=30))),
    ])
    _marker(b, "SR-b1", os.getpid())  # live
    _marker(b, "SR-b2", _dead_pid())  # dead -> unknown

    rows = collect_scheduler_runs([("a", a), ("b", b)])
    assert [r["run_id"] for r in rows] == ["SR-b1", "SR-b2", "SR-a1"]  # newest first
    by_id = {r["run_id"]: r for r in rows}
    assert by_id["SR-a1"]["status"] == "quiescence"
    assert by_id["SR-a1"]["project"] == "a"
    assert by_id["SR-a1"]["started_at"] and by_id["SR-a1"]["finished_at"]
    assert by_id["SR-a1"]["completed_count"] == 2
    assert by_id["SR-b1"]["status"] == "running"
    assert by_id["SR-b1"]["finished_at"] == ""
    assert by_id["SR-b2"]["status"] == "unknown"


def test_legacy_log_without_run_events(tmp_path: Path) -> None:
    """Scenario: fleet-scheduler-visibility / Scheduler run history / Legacy log without run events"""
    legacy = _make_project(tmp_path, "legacy", [
        _rec("bead_started", {"bead_id": "B-1"}),
        _rec("scheduler_cycle_started", {"cycle_index": 1}),  # no run_id
        _rec("bead_completed", {"bead_id": "B-1", "run_id": "SR-x"}),  # not a run lifecycle event
    ])
    assert read_project_runs("legacy", legacy) == []
    assert collect_scheduler_runs([("legacy", legacy)]) == []
    nolog = tmp_path / "nolog"
    (nolog / ".takt").mkdir(parents=True)
    assert read_project_runs("nolog", nolog) == []

    rc_out = _run_cli(tmp_path, [("legacy", legacy)], Namespace(output_json=False, plain=False))
    assert rc_out == (0, "No scheduler runs found.\n")


def test_finished_only_rows_and_bad_lines_skipped(tmp_path: Path) -> None:
    p = _make_project(tmp_path, "p", [
        "not json{",
        "",
        json.dumps([1, 2]),
        _rec("scheduler_run_finished", _finished("SR-crash", "crashed")),
        _rec("scheduler_run_started", {"pid": 1}),  # no run_id -> skipped
        _rec("scheduler_run_finished", {"exit_reason": "error"}),  # no run_id -> skipped
    ])
    rows = read_project_runs("p", p)
    assert [r["run_id"] for r in rows] == ["SR-crash"]
    assert rows[0]["started_at"] == ""
    assert rows[0]["status"] == "crashed"


def test_since_sort_and_limit(tmp_path: Path) -> None:
    p1 = _make_project(tmp_path, "p1", [
        _rec("scheduler_run_started", {"run_id": "SR-old"}, _ts(timedelta(days=3))),
        _rec("scheduler_run_started", {"run_id": "SR-new"}, _ts(timedelta(minutes=1))),
    ])
    p2 = _make_project(tmp_path, "p2", [
        _rec("scheduler_run_started", {"run_id": "SR-mid"}, _ts(timedelta(hours=1))),
    ])
    projects = [("p1", p1), ("p2", p2)]
    assert [r["run_id"] for r in collect_scheduler_runs(projects)] == ["SR-new", "SR-mid", "SR-old"]
    assert [r["run_id"] for r in collect_scheduler_runs(projects, since="2h")] == ["SR-new", "SR-mid"]
    assert [r["run_id"] for r in collect_scheduler_runs(projects, limit=1)] == ["SR-new"]


def test_reader_module_does_not_import_agent_takt() -> None:
    src = (SRC_ROOT / "agent_takt_fleet" / "scheduler_runs.py").read_text()
    assert "import agent_takt\n" not in src
    assert "from agent_takt " not in src and "from agent_takt." not in src


# ── CLI ───────────────────────────────────────────────────────────────────────


def _run_cli(tmp_path: Path, projects: list[tuple[str, Path]], ns: Namespace, capsys=None):
    rf = tmp_path / "fleet.yaml"
    defaults = dict(tag=[], project=[], since=None, limit=20)
    with patch("agent_takt_fleet.registry.registry_path", return_value=rf):
        save_registry([Project(name=n, path=p, tags=("api",) if n == "a" else ()) for n, p in projects])
        for k, v in defaults.items():
            if not hasattr(ns, k):
                setattr(ns, k, v)
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = command_scheduler_runs_list(ns)
    return rc, buf.getvalue()


@pytest.fixture()
def two_projects(tmp_path: Path):
    a = _make_project(tmp_path, "a", [
        _rec("scheduler_run_started", {"run_id": "SR-a1"}, _ts(timedelta(hours=2))),
        _rec("scheduler_run_finished", _finished("SR-a1", duration_seconds=0, cost_usd=0.0), _ts(timedelta(hours=1))),
    ])
    b = _make_project(tmp_path, "b", [
        _rec("scheduler_run_started", {"run_id": "SR-b1"}, _ts(timedelta(minutes=5))),
        _rec("scheduler_run_finished", {"run_id": "SR-b1", "exit_reason": "crashed"}, _ts(timedelta(minutes=4))),
    ])
    return tmp_path, [("a", a), ("b", b)]


def test_table_output_header_nulls_and_cost(two_projects) -> None:
    tmp, projects = two_projects
    rc, out = _run_cli(tmp, projects, Namespace(output_json=False, plain=False))
    assert rc == 0
    header = out.splitlines()[0].split()
    assert header == ["PROJECT", "RUN_ID", "STARTED_AT", "DURATION", "STATUS", "CYCLES", "BEADS", "DONE", "BLOCKED", "COST"]
    assert "$0.00" in out
    b_row = [ln for ln in out.splitlines() if "SR-b1" in ln][0].split()
    assert b_row[-1] == "-" and "crashed" in b_row  # null cost renders as "-"


def test_plain_output_is_tab_separated(two_projects) -> None:
    tmp, projects = two_projects
    rc, out = _run_cli(tmp, projects, Namespace(output_json=False, plain=True))
    assert rc == 0
    assert out.splitlines()[0].split("\t")[:2] == ["PROJECT", "RUN_ID"]


def test_json_output_and_empty_json(tmp_path: Path, two_projects) -> None:
    tmp, projects = two_projects
    rc, out = _run_cli(tmp, projects, Namespace(output_json=True, plain=False))
    assert rc == 0
    assert {r["run_id"] for r in json.loads(out)} == {"SR-a1", "SR-b1"}
    empty = _make_project(tmp_path, "empty")
    rc, out = _run_cli(tmp_path, [("empty", empty)], Namespace(output_json=True, plain=False))
    assert (rc, json.loads(out)) == (0, [])


def test_filters_tag_project_limit_since(two_projects) -> None:
    tmp, projects = two_projects
    rc, out = _run_cli(tmp, projects, Namespace(output_json=True, plain=False, tag=["api"]))
    assert [r["run_id"] for r in json.loads(out)] == ["SR-a1"]
    rc, out = _run_cli(tmp, projects, Namespace(output_json=True, plain=False, project=["b"]))
    assert [r["run_id"] for r in json.loads(out)] == ["SR-b1"]
    rc, out = _run_cli(tmp, projects, Namespace(output_json=True, plain=False, limit=1))
    assert [r["run_id"] for r in json.loads(out)] == ["SR-b1"]
    rc, out = _run_cli(tmp, projects, Namespace(output_json=True, plain=False, since="30m"))
    assert [r["run_id"] for r in json.loads(out)] == ["SR-b1"]


def test_invalid_since_exits_1(two_projects, capsys) -> None:
    tmp, projects = two_projects
    rc, _ = _run_cli(tmp, projects, Namespace(output_json=False, plain=False, since="bogus"))
    assert rc == 1
    assert "error" in capsys.readouterr().err


def test_runs_help_mentions_scheduler_runs(capsys) -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["runs", "--help"])
    assert "scheduler-runs" in capsys.readouterr().out
