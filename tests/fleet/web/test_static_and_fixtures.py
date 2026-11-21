"""Static asset policy (no external URLs, vendored checksum) and fixture helpers."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from agent_takt_fleet.web import demo
from agent_takt_fleet.web.readers import read_recent_events

from .conftest import clock

PKG = Path(demo.__file__).parent
STATIC = PKG / "static"


def test_vendored_checksum_matches_versions_md():
    mjs = STATIC / "vendor" / "htm-preact-standalone-3.1.1.mjs"
    digest = hashlib.sha256(mjs.read_bytes()).hexdigest()
    assert digest in (STATIC / "vendor" / "VERSIONS.md").read_text()


def test_no_external_urls_in_html_css():
    for name in ("index.html", "styles.css"):
        assert not re.search(r"https?://", (STATIC / name).read_text()), name


def test_css_has_dark_scheme():
    assert "prefers-color-scheme: dark" in (STATIC / "styles.css").read_text()


def test_no_package_json_or_node_build():
    assert not list((PKG.parent).rglob("package.json"))


def test_append_event_and_raw(tmp_path):
    ev = demo.append_event(tmp_path, "x", {"a": 1}, clock=clock)
    line = (tmp_path / ".takt/logs/events.jsonl").read_text()
    assert json.loads(line) == ev and ev["timestamp"] == clock().isoformat()
    demo.append_raw(tmp_path, '{"partial')
    assert not (tmp_path / ".takt/logs/events.jsonl").read_text().endswith("\n")
    demo.truncate_events(tmp_path)
    assert (tmp_path / ".takt/logs/events.jsonl").stat().st_size == 0


def test_set_bead_status_preserves_unknown_fields(fleet):
    newer = next(p for p in fleet.projects if p.variant == "newer")
    older = next(p for p in fleet.projects if p.variant == "older")
    out = demo.set_bead_status(newer.path, newer.bead_ids[0], "done", clock=clock)
    assert out["status"] == "done" and out["risk_score"] == 0.42
    out = demo.set_bead_status(older.path, older.bead_ids[0], "blocked", clock=clock)
    assert out["status"] == "blocked" and "lease" not in out


def test_remove_project_dir_safe_when_absent(tmp_path):
    demo.remove_project_dir(tmp_path / "nope")


def test_fixture_variants_present(fleet):
    assert {p.variant for p in fleet.projects} == set(demo.VARIANTS)
    nov = next(p for p in fleet.projects if p.variant == "no-version")
    assert not (nov.path / ".takt" / "version.json").exists()
    assert all(isinstance(e, dict) for p in fleet.projects for e in read_recent_events(p.path, 1000))
    full = next(p for p in fleet.projects if p.variant == "full")
    from agent_takt_fleet.scheduler_runs import _alive_markers
    assert full.active_run_id in _alive_markers(full.path)
