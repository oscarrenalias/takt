from __future__ import annotations

import json
import sys
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_takt_fleet.cli.commands.summary import command_summary
from agent_takt_fleet.models import Project
from agent_takt_fleet.registry import save_registry


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def registry_file(tmp_path: Path):
    rf = tmp_path / "fleet.yaml"
    with patch("agent_takt_fleet.registry.registry_path", return_value=rf):
        yield rf


@pytest.fixture()
def project_dir(tmp_path: Path) -> Path:
    d = tmp_path / "myproject"
    d.mkdir()
    takt_dir = d / ".takt"
    takt_dir.mkdir()
    (takt_dir / "config.yaml").write_text("")
    return d


SAMPLE_COUNTS = {
    "open": 0,
    "ready": 3,
    "in_progress": 2,
    "blocked": 0,
    "done": 42,
    "handed_off": 1,
}
SAMPLE_SUMMARY = {"counts": SAMPLE_COUNTS, "next_up": [], "attention": []}


# ── Empty / no-match cases ────────────────────────────────────────────────────


def test_summary_no_projects_registered(registry_file: Path, capsys) -> None:
    args = Namespace(tag=[], project=[], output_json=False, plain=False)
    rc = command_summary(args)
    assert rc == 0
    err = capsys.readouterr().err
    assert "No projects" in err


def test_summary_no_filter_match(registry_file: Path, project_dir: Path, capsys) -> None:
    with patch("agent_takt_fleet.registry.registry_path", return_value=registry_file):
        save_registry([Project(name="myproject", path=project_dir, tags=())])
    args = Namespace(tag=["nonexistent"], project=[], output_json=False, plain=False)
    rc = command_summary(args)
    assert rc == 0
    err = capsys.readouterr().err
    assert "No projects match" in err


# ── Healthy project renders counts ────────────────────────────────────────────


def test_summary_healthy_project_renders_table(
    registry_file: Path, project_dir: Path, capsys
) -> None:
    with patch("agent_takt_fleet.registry.registry_path", return_value=registry_file):
        save_registry([Project(name="myproject", path=project_dir, tags=())])

    with (
        patch("agent_takt_fleet.cli.commands.summary.compute_health", return_value="ok"),
        patch("agent_takt_fleet.cli.commands.summary.TaktAdapter") as MockAdapter,
    ):
        MockAdapter.return_value.summary.return_value = SAMPLE_SUMMARY
        args = Namespace(tag=[], project=[], output_json=False, plain=False)
        rc = command_summary(args)

    assert rc == 0
    out = capsys.readouterr().out
    assert "myproject" in out
    assert "42" in out  # done count
    assert "3" in out   # ready count
    assert "ok" in out


def test_summary_plain_output(
    registry_file: Path, project_dir: Path, capsys
) -> None:
    with patch("agent_takt_fleet.registry.registry_path", return_value=registry_file):
        save_registry([Project(name="myproject", path=project_dir, tags=())])

    with (
        patch("agent_takt_fleet.cli.commands.summary.compute_health", return_value="ok"),
        patch("agent_takt_fleet.cli.commands.summary.TaktAdapter") as MockAdapter,
    ):
        MockAdapter.return_value.summary.return_value = SAMPLE_SUMMARY
        args = Namespace(tag=[], project=[], output_json=False, plain=True)
        rc = command_summary(args)

    assert rc == 0
    out = capsys.readouterr().out
    assert "\t" in out


def test_summary_json_output(
    registry_file: Path, project_dir: Path, capsys
) -> None:
    with patch("agent_takt_fleet.registry.registry_path", return_value=registry_file):
        save_registry([Project(name="myproject", path=project_dir, tags=())])

    with (
        patch("agent_takt_fleet.cli.commands.summary.compute_health", return_value="ok"),
        patch("agent_takt_fleet.cli.commands.summary.TaktAdapter") as MockAdapter,
    ):
        MockAdapter.return_value.summary.return_value = SAMPLE_SUMMARY
        args = Namespace(tag=[], project=[], output_json=True, plain=False)
        rc = command_summary(args)

    assert rc == 0
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert isinstance(parsed, list)
    assert len(parsed) == 1
    assert parsed[0]["name"] == "myproject"
    assert parsed[0]["health"] == "ok"
    assert parsed[0]["counts"]["done"] == 42
    assert parsed[0]["scheduler"] is None


def test_summary_json_includes_scheduler(
    registry_file: Path, project_dir: Path, capsys
) -> None:
    with patch("agent_takt_fleet.registry.registry_path", return_value=registry_file):
        save_registry([Project(name="myproject", path=project_dir, tags=())])

    sched = {"state": "running"}
    with (
        patch("agent_takt_fleet.cli.commands.summary.compute_health", return_value="ok"),
        patch("agent_takt_fleet.cli.commands.summary.TaktAdapter") as MockAdapter,
    ):
        MockAdapter.return_value.summary.return_value = {**SAMPLE_SUMMARY, "scheduler": sched}
        args = Namespace(tag=[], project=[], output_json=True, plain=False)
        rc = command_summary(args)

    assert rc == 0
    parsed = json.loads(capsys.readouterr().out)
    assert parsed[0]["scheduler"] == sched


# ── Degraded health: dashes in columns ───────────────────────────────────────


def test_summary_missing_project_shows_dashes(
    registry_file: Path, tmp_path: Path, capsys
) -> None:
    missing = tmp_path / "gone"
    with patch("agent_takt_fleet.registry.registry_path", return_value=registry_file):
        save_registry([Project(name="gone", path=missing, tags=())])

    args = Namespace(tag=[], project=[], output_json=False, plain=False)
    rc = command_summary(args)

    assert rc == 0
    out = capsys.readouterr().out
    assert "-" in out
    assert "missing" in out


def test_summary_no_takt_project_shows_no_takt(
    registry_file: Path, tmp_path: Path, capsys
) -> None:
    bare = tmp_path / "bare"
    bare.mkdir()
    with patch("agent_takt_fleet.registry.registry_path", return_value=registry_file):
        save_registry([Project(name="bare", path=bare, tags=())])

    args = Namespace(tag=[], project=[], output_json=False, plain=False)
    rc = command_summary(args)

    assert rc == 0
    out = capsys.readouterr().out
    assert "no-takt" in out


def test_summary_adapter_error_falls_back_to_takt_error(
    registry_file: Path, project_dir: Path, capsys
) -> None:
    with patch("agent_takt_fleet.registry.registry_path", return_value=registry_file):
        save_registry([Project(name="myproject", path=project_dir, tags=())])

    from agent_takt_fleet.adapter import AdapterError

    with (
        patch("agent_takt_fleet.cli.commands.summary.compute_health", return_value="ok"),
        patch("agent_takt_fleet.cli.commands.summary.TaktAdapter") as MockAdapter,
    ):
        MockAdapter.return_value.summary.side_effect = AdapterError("broken")
        args = Namespace(tag=[], project=[], output_json=False, plain=False)
        rc = command_summary(args)

    assert rc == 0
    out = capsys.readouterr().out
    assert "takt-error" in out
    assert "-" in out


# ── Mixed healthy + unhealthy projects ───────────────────────────────────────


def test_summary_mixed_projects_no_abort(
    registry_file: Path, project_dir: Path, tmp_path: Path, capsys
) -> None:
    missing = tmp_path / "gone"
    with patch("agent_takt_fleet.registry.registry_path", return_value=registry_file):
        save_registry([
            Project(name="good", path=project_dir, tags=()),
            Project(name="gone", path=missing, tags=()),
        ])

    def health_side_effect(project: Project) -> str:
        return "ok" if project.name == "good" else "missing"

    with (
        patch("agent_takt_fleet.cli.commands.summary.compute_health", side_effect=health_side_effect),
        patch("agent_takt_fleet.cli.commands.summary.TaktAdapter") as MockAdapter,
    ):
        MockAdapter.return_value.summary.return_value = SAMPLE_SUMMARY
        args = Namespace(tag=[], project=[], output_json=False, plain=False)
        rc = command_summary(args)

    assert rc == 0
    out = capsys.readouterr().out
    assert "good" in out
    assert "gone" in out
    assert "42" in out      # healthy project has counts
    assert "missing" in out  # unhealthy shows health value


# ── Registry error ────────────────────────────────────────────────────────────


def test_summary_registry_error_returns_1(tmp_path: Path, capsys) -> None:
    bad_registry = tmp_path / "fleet.yaml"
    bad_registry.write_text("not valid yaml: [[[")

    with patch("agent_takt_fleet.registry.registry_path", return_value=bad_registry):
        args = Namespace(tag=[], project=[], output_json=False, plain=False)
        rc = command_summary(args)

    assert rc == 1


# ── Project filter ────────────────────────────────────────────────────────────


def test_summary_project_name_filter(
    registry_file: Path, project_dir: Path, tmp_path: Path, capsys
) -> None:
    other = tmp_path / "other"
    other.mkdir()
    with patch("agent_takt_fleet.registry.registry_path", return_value=registry_file):
        save_registry([
            Project(name="target", path=project_dir, tags=()),
            Project(name="other", path=other, tags=()),
        ])

    with (
        patch("agent_takt_fleet.cli.commands.summary.compute_health", return_value="ok"),
        patch("agent_takt_fleet.cli.commands.summary.TaktAdapter") as MockAdapter,
    ):
        MockAdapter.return_value.summary.return_value = SAMPLE_SUMMARY
        args = Namespace(tag=[], project=["target"], output_json=False, plain=False)
        rc = command_summary(args)

    assert rc == 0
    out = capsys.readouterr().out
    assert "target" in out
    assert "other" not in out


def test_summary_mixed_fleet_scheduler_column(registry_file: Path, tmp_path: Path, capsys) -> None:
    """Scenario: fleet-scheduler-visibility / Scheduler column in fleet summary / Mixed fleet"""
    dirs = {}
    for name in ("alpha", "beta", "gamma", "delta"):
        d = tmp_path / name
        (d / ".takt").mkdir(parents=True)
        dirs[name] = d
    with patch("agent_takt_fleet.registry.registry_path", return_value=registry_file):
        save_registry([Project(name=n, path=p, tags=()) for n, p in dirs.items()])

    by_path = {
        str(dirs["alpha"]): {**SAMPLE_SUMMARY, "scheduler": {"state": "running"}},
        str(dirs["beta"]): {**SAMPLE_SUMMARY, "scheduler": {"state": "idle"}},
        str(dirs["gamma"]): {**SAMPLE_SUMMARY},  # older takt: no scheduler key
    }

    def _health(project):
        return "missing" if project.name == "delta" else "ok"

    def _adapter(path):
        m = type("A", (), {})()
        m.summary = lambda: by_path[str(path)]
        return m

    with (
        patch("agent_takt_fleet.cli.commands.summary.compute_health", side_effect=_health),
        patch("agent_takt_fleet.cli.commands.summary.TaktAdapter", side_effect=_adapter),
    ):
        rc = command_summary(Namespace(tag=[], project=[], output_json=False, plain=True))
        assert rc == 0
        plain_out = capsys.readouterr().out
        rc = command_summary(Namespace(tag=[], project=[], output_json=True, plain=False))
        assert rc == 0
        rows = {r["name"]: r for r in json.loads(capsys.readouterr().out)}

    lines = [ln.split("\t") if "\t" in ln else ln.split() for ln in plain_out.strip().splitlines()]
    header = lines[0]
    assert header.index("SCHEDULER") == header.index("HANDED_OFF") + 1
    assert header.index("SCHEDULER") + 1 == header.index("HEALTH")
    col = header.index("SCHEDULER")
    cells = {ln[0]: ln[col] for ln in lines[1:]}
    assert cells == {"alpha": "running", "beta": "idle", "gamma": "-", "delta": "-"}
    assert rows["alpha"]["scheduler"] == {"state": "running"}
    assert rows["gamma"]["scheduler"] is None
    assert rows["delta"]["scheduler"] is None
