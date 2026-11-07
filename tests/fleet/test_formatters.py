from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from datetime import datetime, timezone

from agent_takt_fleet.formatters import (
    format_fleet_summary,
    format_project_list,
    format_table,
    format_watch_event_line,
)
from agent_takt_fleet.tailer import TailedEvent
from agent_takt_fleet.models import Project


# ── format_table ──────────────────────────────────────────────────────────────


def test_format_table_basic() -> None:
    headers = ["A", "B"]
    rows = [["x", "y"], ["longer", "z"]]
    output = format_table(headers, rows)
    lines = output.splitlines()
    assert lines[0].startswith("A")
    assert "B" in lines[0]
    assert "longer" in lines[3]


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
    output = format_table(["H1", "H2"], [["a", "b"], ["c", "d"]])
    lines = output.splitlines()
    assert lines[0].startswith("H1")
    assert set(lines[1].replace(" ", "")) == {"-"}
    assert lines[2].startswith("a")
    assert lines[3].startswith("c")
    assert len(lines) == 4


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


def test_format_project_list_no_tags_dash_in_default_mode(tmp_path: Path) -> None:
    p = Project(name="api", path=tmp_path / "api", tags=())
    lines = format_project_list([p], {"api": "ok"}).splitlines()
    tags_col = lines[0].index("TAGS")
    assert lines[2][tags_col:].startswith("-")
    plain = format_project_list([p], {"api": "ok"}, plain=True).splitlines()
    assert plain[1].split("\t")[2] == ""


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


# ── format_watch_event_line ───────────────────────────────────────────────────

_TS = datetime(2026, 11, 7, 21, 15, 38, tzinfo=timezone.utc)
_TS_STR = "2026-11-07T21:15:38Z"


def _ev(parsed) -> TailedEvent:
    return TailedEvent(project_name="proj", raw_line="raw", parsed=parsed, timestamp=_TS)


def test_watch_line_bead_completed() -> None:
    line = format_watch_event_line(_ev({
        "timestamp": "x", "event_type": "bead_completed",
        "payload": {"bead_id": "B-1", "agent_type": "developer", "summary": "Added thing"},
    }))
    assert line == f"[proj]  {_TS_STR}  {'bead_completed':<20}  B-1  Added thing"


def test_watch_line_bead_started_uses_title() -> None:
    line = format_watch_event_line(_ev({
        "event_type": "bead_started", "payload": {"bead_id": "B-2", "title": "Do work"},
    }))
    assert line.endswith("B-2  Do work")


def test_watch_line_bead_deferred_uses_reason() -> None:
    line = format_watch_event_line(_ev({
        "event_type": "bead_deferred", "payload": {"bead_id": "B-3", "reason": "dep not done"},
    }))
    assert line.endswith("B-3  dep not done")


def test_watch_line_feature_root_id_fallback() -> None:
    line = format_watch_event_line(_ev({
        "event_type": "scheduler_cycle_started", "payload": {"feature_root_id": "B-9", "cycle_index": 1},
    }))
    assert line == f"[proj]  {_TS_STR}  {'scheduler_cycle_started':<20}  B-9"


def test_watch_line_no_payload() -> None:
    assert format_watch_event_line(_ev({"event_type": "x"})) == f"[proj]  {_TS_STR}  {'x':<20}"
    assert format_watch_event_line(_ev({"event_type": "x", "payload": "oops"})) == f"[proj]  {_TS_STR}  {'x':<20}"


def test_watch_line_truncates_and_collapses_whitespace() -> None:
    summary = "line one\n\n  line   two " + "z" * 200
    line = format_watch_event_line(_ev({"event_type": "bead_failed", "payload": {"summary": summary}}))
    detail = line.split("  ")[-1] if False else line.split(f"{'bead_failed':<20}  ", 1)[1]
    assert len(detail) == 120
    assert detail.endswith("…")
    assert detail.startswith("line one line two zzz")
    assert "\n" not in line


def test_watch_line_unparseable_keeps_raw() -> None:
    ev = TailedEvent(project_name="proj", raw_line="garbage", parsed=None, timestamp=None)
    assert format_watch_event_line(ev) == "[proj]  garbage"
