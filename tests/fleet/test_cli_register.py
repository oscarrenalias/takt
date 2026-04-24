from __future__ import annotations

import subprocess
import sys
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_takt_fleet.cli.commands.register import (
    command_list,
    command_register,
    command_unregister,
)
from agent_takt_fleet.models import Project
from agent_takt_fleet.registry import load_registry, save_registry


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def registry_file(tmp_path: Path):
    """Patch registry_path to a temp file and yield its path."""
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


# ── command_register ──────────────────────────────────────────────────────────


def test_register_success(tmp_path: Path, registry_file: Path, project_dir: Path, capsys) -> None:
    args = Namespace(path=str(project_dir), name=None, tag=[])
    rc = command_register(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "myproject" in out

    with patch("agent_takt_fleet.registry.registry_path", return_value=registry_file):
        projects = load_registry()
    assert len(projects) == 1
    assert projects[0].name == "myproject"
    assert projects[0].path == project_dir


def test_register_custom_name(tmp_path: Path, registry_file: Path, project_dir: Path, capsys) -> None:
    args = Namespace(path=str(project_dir), name="my-api", tag=[])
    rc = command_register(args)
    assert rc == 0

    with patch("agent_takt_fleet.registry.registry_path", return_value=registry_file):
        projects = load_registry()
    assert projects[0].name == "my-api"


def test_register_with_tags(tmp_path: Path, registry_file: Path, project_dir: Path) -> None:
    args = Namespace(path=str(project_dir), name=None, tag=["python", "backend"])
    rc = command_register(args)
    assert rc == 0

    with patch("agent_takt_fleet.registry.registry_path", return_value=registry_file):
        projects = load_registry()
    assert projects[0].tags == ("python", "backend")


def test_register_nonexistent_path(tmp_path: Path, registry_file: Path, capsys) -> None:
    args = Namespace(path=str(tmp_path / "doesnotexist"), name=None, tag=[])
    rc = command_register(args)
    assert rc == 1
    assert "does not exist" in capsys.readouterr().err


def test_register_duplicate_path_rejected(
    tmp_path: Path, registry_file: Path, project_dir: Path, capsys
) -> None:
    args = Namespace(path=str(project_dir), name=None, tag=[])
    assert command_register(args) == 0
    rc = command_register(args)
    assert rc == 1
    assert "already registered" in capsys.readouterr().err


def test_register_warns_when_no_takt_dir(
    tmp_path: Path, registry_file: Path, capsys
) -> None:
    bare_dir = tmp_path / "bare"
    bare_dir.mkdir()
    args = Namespace(path=str(bare_dir), name=None, tag=[])
    rc = command_register(args)
    assert rc == 0
    assert ".takt/" in capsys.readouterr().err


# ── command_unregister ────────────────────────────────────────────────────────


def test_unregister_by_name(tmp_path: Path, registry_file: Path, project_dir: Path, capsys) -> None:
    with patch("agent_takt_fleet.registry.registry_path", return_value=registry_file):
        save_registry([Project(name="myproject", path=project_dir, tags=())])
    args = Namespace(path_or_name="myproject")
    rc = command_unregister(args)
    assert rc == 0

    with patch("agent_takt_fleet.registry.registry_path", return_value=registry_file):
        projects = load_registry()
    assert projects == []


def test_unregister_by_path(tmp_path: Path, registry_file: Path, project_dir: Path, capsys) -> None:
    with patch("agent_takt_fleet.registry.registry_path", return_value=registry_file):
        save_registry([Project(name="myproject", path=project_dir, tags=())])
    args = Namespace(path_or_name=str(project_dir))
    rc = command_unregister(args)
    assert rc == 0

    with patch("agent_takt_fleet.registry.registry_path", return_value=registry_file):
        assert load_registry() == []


def test_unregister_not_found(tmp_path: Path, registry_file: Path, capsys) -> None:
    args = Namespace(path_or_name="nonexistent")
    rc = command_unregister(args)
    assert rc == 1
    assert "no project found" in capsys.readouterr().err


def test_unregister_removes_only_target(
    tmp_path: Path, registry_file: Path, project_dir: Path
) -> None:
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    with patch("agent_takt_fleet.registry.registry_path", return_value=registry_file):
        save_registry([
            Project(name="myproject", path=project_dir, tags=()),
            Project(name="other", path=other_dir, tags=()),
        ])
    args = Namespace(path_or_name="myproject")
    command_unregister(args)

    with patch("agent_takt_fleet.registry.registry_path", return_value=registry_file):
        projects = load_registry()
    assert len(projects) == 1
    assert projects[0].name == "other"


# ── command_list ──────────────────────────────────────────────────────────────


def test_list_empty_registry(tmp_path: Path, registry_file: Path, capsys) -> None:
    args = Namespace(tag=[], plain=False)
    rc = command_list(args)
    assert rc == 0


def test_list_shows_projects(tmp_path: Path, registry_file: Path, project_dir: Path, capsys) -> None:
    with patch("agent_takt_fleet.registry.registry_path", return_value=registry_file):
        save_registry([Project(name="myproject", path=project_dir, tags=("python",))])

    mock_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="takt 0.1.0", stderr="")
    with patch("subprocess.run", return_value=mock_result):
        args = Namespace(tag=[], plain=False)
        rc = command_list(args)

    assert rc == 0
    out = capsys.readouterr().out
    assert "myproject" in out
    assert "python" in out


def test_list_plain_output(tmp_path: Path, registry_file: Path, project_dir: Path, capsys) -> None:
    with patch("agent_takt_fleet.registry.registry_path", return_value=registry_file):
        save_registry([Project(name="myproject", path=project_dir, tags=())])

    mock_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="takt 0.1.0", stderr="")
    with patch("subprocess.run", return_value=mock_result):
        args = Namespace(tag=[], plain=True)
        rc = command_list(args)

    assert rc == 0
    out = capsys.readouterr().out
    assert "\t" in out


def test_list_tag_filter(tmp_path: Path, registry_file: Path, tmp_path_factory) -> None:
    py_dir = tmp_path / "py_proj"
    py_dir.mkdir()
    rb_dir = tmp_path / "rb_proj"
    rb_dir.mkdir()

    with patch("agent_takt_fleet.registry.registry_path", return_value=tmp_path / "fleet.yaml"):
        save_registry([
            Project(name="py_proj", path=py_dir, tags=("python",)),
            Project(name="rb_proj", path=rb_dir, tags=("ruby",)),
        ])
        args = Namespace(tag=["python"], plain=False)
        mock_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="takt 0.1.0", stderr="")
        with patch("subprocess.run", return_value=mock_result):
            from io import StringIO
            import builtins

            output_lines: list[str] = []
            original_print = builtins.print

            def capture_print(*a, **kw):
                if kw.get("file") is None:
                    output_lines.append(" ".join(str(x) for x in a))
                else:
                    original_print(*a, **kw)

            with patch("builtins.print", side_effect=capture_print):
                rc = command_list(args)

    assert rc == 0
    combined = "\n".join(output_lines)
    assert "py_proj" in combined
    assert "rb_proj" not in combined


def test_list_health_columns_present(
    tmp_path: Path, registry_file: Path, project_dir: Path, capsys
) -> None:
    with patch("agent_takt_fleet.registry.registry_path", return_value=registry_file):
        save_registry([Project(name="myproject", path=project_dir, tags=())])

    mock_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="takt 0.1.0", stderr="")
    with patch("subprocess.run", return_value=mock_result):
        args = Namespace(tag=[], plain=False)
        rc = command_list(args)

    assert rc == 0
    out = capsys.readouterr().out
    assert "HEALTH" in out
    assert "ok" in out
