from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_takt_fleet.formatters import format_fleet_summary, format_project_list, format_table
from agent_takt_fleet.models import Project


# ── format_table ──────────────────────────────────────────────────────────────


def test_format_table_basic() -> None:
    headers = ["A", "B"]
    rows = [["x", "y"], ["longer", "z"]]
    output = format_table(headers, rows)
    lines = output.splitlines()
    assert lines[0].startswith("A")
    assert "B" in lines[0]
    assert "longer" in lines[2]


def test_format_table_plain_uses_tabs() -> None:
    headers = ["NAME", "VALUE"]
    rows = [["foo", "bar"]]
    output = format_table(headers, rows, plain=True)
    assert "\t" in output
    assert "---" not in output


def test_format_table_plain_no_separator_line() -> None:
    output = format_table(["H1", "H2"], [["a", "b"]], plain=True)
    assert "---" not in output


def test_format_table_empty() -> None:
    assert format_table([], [], plain=False) == ""


def test_format_table_separator_line_in_default_mode() -> None:
    output = format_table(["H1", "H2"], [["a", "b"]])
    lines = output.splitlines()
    assert any("---" in line for line in lines)


# ── format_project_list ───────────────────────────────────────────────────────


def test_format_project_list_basic(tmp_path: Path) -> None:
    p = Project(name="api", path=tmp_path / "api", tags=("python",))
    health_map = {"api": "ok"}
    output = format_project_list([p], health_map)
    assert "api" in output
    assert "python" in output
    assert "ok" in output
    assert "HEALTH" in output


def test_format_project_list_empty_tags(tmp_path: Path) -> None:
    p = Project(name="api", path=tmp_path / "api", tags=())
    output = format_project_list([p], {"api": "ok"})
    assert "api" in output


def test_format_project_list_plain(tmp_path: Path) -> None:
    p = Project(name="api", path=tmp_path / "api", tags=())
    output = format_project_list([p], {"api": "ok"}, plain=True)
    assert "\t" in output


# ── format_fleet_summary ──────────────────────────────────────────────────────


def test_format_fleet_summary_healthy_row() -> None:
    rows = [
        {
            "name": "api-svc",
            "health": "ok",
            "counts": {"done": 42, "ready": 3, "in_progress": 2, "blocked": 0, "handed_off": 1},
        }
    ]
    output = format_fleet_summary(rows)
    assert "api-svc" in output
    assert "42" in output
    assert "3" in output
    assert "ok" in output
    assert "PROJECT" in output
    assert "DONE" in output
    assert "HEALTH" in output


def test_format_fleet_summary_degraded_row_shows_dashes() -> None:
    rows = [
        {"name": "legacy", "health": "no-takt", "counts": None},
    ]
    output = format_fleet_summary(rows)
    assert "legacy" in output
    assert "no-takt" in output
    assert "-" in output
    assert "PROJECT" in output


def test_format_fleet_summary_mixed_rows() -> None:
    rows = [
        {
            "name": "good",
            "health": "ok",
            "counts": {"done": 10, "ready": 1, "in_progress": 0, "blocked": 0, "handed_off": 0},
        },
        {"name": "bad", "health": "missing", "counts": None},
    ]
    output = format_fleet_summary(rows)
    assert "good" in output
    assert "bad" in output
    assert "10" in output
    assert "missing" in output
    assert "-" in output


def test_format_fleet_summary_plain_output() -> None:
    rows = [
        {
            "name": "proj",
            "health": "ok",
            "counts": {"done": 5, "ready": 0, "in_progress": 0, "blocked": 0, "handed_off": 0},
        }
    ]
    output = format_fleet_summary(rows, plain=True)
    assert "\t" in output
    assert "---" not in output


def test_format_fleet_summary_empty_rows() -> None:
    output = format_fleet_summary([])
    assert output == ""


def test_format_fleet_summary_counts_default_zero_when_missing() -> None:
    rows = [
        {
            "name": "proj",
            "health": "ok",
            "counts": {},
        }
    ]
    output = format_fleet_summary(rows)
    # All count columns should default to "0"
    assert "0" in output


def test_format_fleet_summary_all_columns_present() -> None:
    rows = [{"name": "p", "health": "ok", "counts": {"done": 1, "ready": 2, "in_progress": 3, "blocked": 4, "handed_off": 5}}]
    output = format_fleet_summary(rows)
    for col in ("PROJECT", "DONE", "READY", "IN_PROGRESS", "BLOCKED", "HANDED_OFF", "HEALTH"):
        assert col in output
