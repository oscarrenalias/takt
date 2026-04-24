from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_takt_fleet.models import Project
from agent_takt_fleet.registry import (
    RegistryError,
    _CURRENT_VERSION,
    compute_health,
    filter_projects,
    load_registry,
    save_registry,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _write_registry(path: Path, content: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        yaml.safe_dump(content, f)


def _make_project(name: str, path: Path, tags: tuple[str, ...] = ()) -> Project:
    return Project(name=name, path=path, tags=tags)


# ── load_registry ─────────────────────────────────────────────────────────────


def test_load_registry_missing_file_returns_empty(tmp_path: Path) -> None:
    registry_file = tmp_path / "config" / "agent-takt" / "fleet.yaml"
    with patch("agent_takt_fleet.registry.registry_path", return_value=registry_file):
        result = load_registry()
    assert result == []


def test_load_registry_valid_file(tmp_path: Path) -> None:
    project_dir = tmp_path / "myproject"
    project_dir.mkdir()
    registry_file = tmp_path / "fleet.yaml"
    _write_registry(
        registry_file,
        {
            "version": 1,
            "projects": [
                {"name": "myproject", "path": str(project_dir), "tags": ["python"]},
            ],
        },
    )
    with patch("agent_takt_fleet.registry.registry_path", return_value=registry_file):
        projects = load_registry()

    assert len(projects) == 1
    assert projects[0].name == "myproject"
    assert projects[0].path == project_dir
    assert projects[0].tags == ("python",)


def test_load_registry_empty_projects_list(tmp_path: Path) -> None:
    registry_file = tmp_path / "fleet.yaml"
    _write_registry(registry_file, {"version": 1, "projects": []})
    with patch("agent_takt_fleet.registry.registry_path", return_value=registry_file):
        result = load_registry()
    assert result == []


def test_load_registry_missing_version_raises(tmp_path: Path) -> None:
    registry_file = tmp_path / "fleet.yaml"
    _write_registry(registry_file, {"projects": []})
    with patch("agent_takt_fleet.registry.registry_path", return_value=registry_file):
        with pytest.raises(RegistryError, match="missing a 'version' field"):
            load_registry()


def test_load_registry_higher_version_raises(tmp_path: Path) -> None:
    registry_file = tmp_path / "fleet.yaml"
    _write_registry(registry_file, {"version": _CURRENT_VERSION + 1, "projects": []})
    with patch("agent_takt_fleet.registry.registry_path", return_value=registry_file):
        with pytest.raises(RegistryError, match="newer takt-fleet"):
            load_registry()


def test_load_registry_non_integer_version_raises(tmp_path: Path) -> None:
    registry_file = tmp_path / "fleet.yaml"
    _write_registry(registry_file, {"version": "one", "projects": []})
    with patch("agent_takt_fleet.registry.registry_path", return_value=registry_file):
        with pytest.raises(RegistryError, match="must be an integer"):
            load_registry()


def test_load_registry_projects_without_tags(tmp_path: Path) -> None:
    registry_file = tmp_path / "fleet.yaml"
    _write_registry(
        registry_file,
        {
            "version": 1,
            "projects": [{"name": "bare", "path": "/some/path"}],
        },
    )
    with patch("agent_takt_fleet.registry.registry_path", return_value=registry_file):
        projects = load_registry()
    assert projects[0].tags == ()


# ── save_registry ─────────────────────────────────────────────────────────────


def test_save_registry_roundtrip(tmp_path: Path) -> None:
    registry_file = tmp_path / "config" / "fleet.yaml"
    projects = [
        Project(name="alpha", path=Path("/tmp/alpha"), tags=("a", "b")),
        Project(name="beta", path=Path("/tmp/beta"), tags=()),
    ]
    with patch("agent_takt_fleet.registry.registry_path", return_value=registry_file):
        save_registry(projects)
        loaded = load_registry()

    assert len(loaded) == 2
    assert loaded[0].name == "alpha"
    assert loaded[0].tags == ("a", "b")
    assert loaded[1].name == "beta"
    assert loaded[1].tags == ()


def test_save_registry_writes_version(tmp_path: Path) -> None:
    registry_file = tmp_path / "fleet.yaml"
    with patch("agent_takt_fleet.registry.registry_path", return_value=registry_file):
        save_registry([])

    with registry_file.open() as f:
        data = yaml.safe_load(f)
    assert data["version"] == _CURRENT_VERSION


def test_save_registry_atomic_creates_parent_dirs(tmp_path: Path) -> None:
    registry_file = tmp_path / "deeply" / "nested" / "fleet.yaml"
    with patch("agent_takt_fleet.registry.registry_path", return_value=registry_file):
        save_registry([])
    assert registry_file.exists()


# ── filter_projects ───────────────────────────────────────────────────────────


def test_filter_projects_no_filters(tmp_path: Path) -> None:
    projects = [
        _make_project("a", tmp_path, ("x",)),
        _make_project("b", tmp_path, ("y",)),
    ]
    assert filter_projects(projects) == projects


def test_filter_projects_by_single_tag(tmp_path: Path) -> None:
    projects = [
        _make_project("a", tmp_path, ("python", "backend")),
        _make_project("b", tmp_path, ("typescript",)),
    ]
    result = filter_projects(projects, tags=["python"])
    assert [p.name for p in result] == ["a"]


def test_filter_projects_by_multiple_tags_and_semantics(tmp_path: Path) -> None:
    projects = [
        _make_project("a", tmp_path, ("python", "backend")),
        _make_project("b", tmp_path, ("python",)),
        _make_project("c", tmp_path, ("backend",)),
    ]
    result = filter_projects(projects, tags=["python", "backend"])
    assert [p.name for p in result] == ["a"]


def test_filter_projects_by_name(tmp_path: Path) -> None:
    projects = [
        _make_project("alpha", tmp_path),
        _make_project("beta", tmp_path),
        _make_project("gamma", tmp_path),
    ]
    result = filter_projects(projects, names=["alpha", "gamma"])
    assert {p.name for p in result} == {"alpha", "gamma"}


def test_filter_projects_combined_tags_and_names(tmp_path: Path) -> None:
    projects = [
        _make_project("alpha", tmp_path, ("python",)),
        _make_project("beta", tmp_path, ("python",)),
        _make_project("gamma", tmp_path, ("ruby",)),
    ]
    result = filter_projects(projects, tags=["python"], names=["alpha"])
    assert [p.name for p in result] == ["alpha"]


def test_filter_projects_no_match_returns_empty(tmp_path: Path) -> None:
    projects = [_make_project("a", tmp_path, ("x",))]
    assert filter_projects(projects, tags=["y"]) == []


# ── compute_health ────────────────────────────────────────────────────────────


def test_compute_health_missing_path(tmp_path: Path) -> None:
    project = _make_project("x", tmp_path / "nonexistent")
    assert compute_health(project) == "missing"


def test_compute_health_no_takt_dir(tmp_path: Path) -> None:
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    project = _make_project("x", project_dir)
    assert compute_health(project) == "no-takt"


def test_compute_health_takt_dir_missing_config(tmp_path: Path) -> None:
    project_dir = tmp_path / "proj"
    (project_dir / ".takt").mkdir(parents=True)
    project = _make_project("x", project_dir)
    assert compute_health(project) == "no-takt"


def test_compute_health_takt_error_nonzero_exit(tmp_path: Path) -> None:
    project_dir = tmp_path / "proj"
    (project_dir / ".takt").mkdir(parents=True)
    (project_dir / ".takt" / "config.yaml").write_text("")
    project = _make_project("x", project_dir)

    mock_result = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="")
    with patch("subprocess.run", return_value=mock_result):
        assert compute_health(project) == "takt-error"


def test_compute_health_takt_error_timeout(tmp_path: Path) -> None:
    project_dir = tmp_path / "proj"
    (project_dir / ".takt").mkdir(parents=True)
    (project_dir / ".takt" / "config.yaml").write_text("")
    project = _make_project("x", project_dir)

    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd=[], timeout=5)):
        assert compute_health(project) == "takt-error"


def test_compute_health_ok(tmp_path: Path) -> None:
    project_dir = tmp_path / "proj"
    (project_dir / ".takt").mkdir(parents=True)
    (project_dir / ".takt" / "config.yaml").write_text("")
    project = _make_project("x", project_dir)

    mock_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="takt 0.1.0", stderr="")
    with patch("subprocess.run", return_value=mock_result):
        assert compute_health(project) == "ok"
