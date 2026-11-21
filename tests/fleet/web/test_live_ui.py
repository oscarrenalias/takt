"""Layer 2 (ADR-f42387c6): Playwright tests for the live session screens.

Live detection uses the real clock, so these tests stamp their own session events with
the current time and use a temporary Claude config dir (never the real ~/.claude).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from agent_takt_fleet.web import demo
from agent_takt_fleet.web.app import create_app

from .conftest import to_projects
from .test_ui import browser, expect, goto, pw_sync, ui  # noqa: F401  (fixtures by name)

pytestmark = pytest.mark.ui

PROJ = "demo-1-full"
SLUG = "-demo-live"


def _now():
    return datetime.now(timezone.utc)


def _proj(fleet):
    return next(p for p in fleet.projects if p.name == PROJ)


def _start(fleet, bead_id, sid):
    demo.append_event(_proj(fleet).path, "agent_session_started", {
        "bead_id": bead_id, "session_id": sid, "role": "main", "agent_type": "developer",
        "feature_root_id": bead_id, "backend": "claude", "started_at": _now().isoformat(),
    }, clock=_now)


def _say(cfg, sid, text):
    demo.append_transcript_line(cfg, SLUG, sid, _now(), "text", text=text)


@pytest.fixture
def stack(fleet, live_server_factory, tmp_path):
    projs = to_projects(fleet)
    cfg = tmp_path / "claude-config"
    app = create_app(lambda: projs, installed_version="0.9.0", poll_interval=0.1,
                     heartbeat_interval=0.3, claude_config_dir=cfg)
    srv = live_server_factory(app)
    srv.gate, srv.projs, srv.fleet, srv.cfg = app, projs, fleet, cfg
    return srv


def _bead(ui):
    from agent_takt_fleet.web.readers import ProjectReader

    return ProjectReader(_proj(ui.server.fleet).path).read_beads().beads[0]["bead_id"]


def test_live_now_strip_and_session_end(ui):
    """Scenario: fleet-web-ui / Live session screens / Session ends (overview strip)."""
    fleet, cfg = ui.server.fleet, ui.server.cfg
    bead, sid = _bead(ui), str(uuid.uuid4())
    _say(cfg, sid, "hello")
    page = goto(ui, "")
    expect(page.get_by_test_id("live-now")).to_have_count(0)  # hidden with no live sessions
    _start(fleet, bead, sid)
    link = page.get_by_test_id("live-now-link")
    expect(link).to_have_attribute("href", f"#/projects/{PROJ}/beads/{bead}/live/{sid}")
    demo.append_event(_proj(fleet).path, "trace_captured",
                      {"bead_id": bead, "session_id": sid, "seq": 1}, clock=_now)
    expect(page.get_by_test_id("live-now")).to_have_count(0)


def test_watch_live_appends_events_then_ends(ui):
    """Scenarios: Watch a session live / Session ends."""
    fleet, cfg = ui.server.fleet, ui.server.cfg
    bead, sid = _bead(ui), str(uuid.uuid4())
    _say(cfg, sid, "first message")
    _start(fleet, bead, sid)
    page = goto(ui, f"projects/{PROJ}/beads/{bead}/live/{sid}")
    expect(page.get_by_test_id("screen-live")).to_be_visible()
    expect(page.get_by_test_id("live-timeline")).to_contain_text("first message")
    expect(page.get_by_test_id("live-autoscroll")).to_be_checked()
    _say(cfg, sid, "second message")
    expect(page.get_by_test_id("live-timeline")).to_contain_text("second message")
    expect(page.get_by_test_id("live-ended-banner")).to_have_count(0)
    demo.append_event(_proj(fleet).path, "trace_captured",
                      {"bead_id": bead, "session_id": sid, "seq": 1}, clock=_now)
    expect(page.get_by_test_id("live-ended-banner")).to_be_visible()


def test_waiting_session_shows_notice(ui):
    fleet = ui.server.fleet
    bead, sid = _bead(ui), str(uuid.uuid4())
    _start(fleet, bead, sid)  # no transcript file
    page = goto(ui, f"projects/{PROJ}/beads/{bead}/live/{sid}")
    expect(page.get_by_test_id("live-waiting")).to_be_visible()


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_no_horizontal_scroll_on_mobile_live_attention_cost(ui, scheme):
    """No horizontal page scroll at 360 px on the live, attention and cost pages."""
    fleet, cfg = ui.server.fleet, ui.server.cfg
    bead, sid = _bead(ui), str(uuid.uuid4())
    _say(cfg, sid, "x" * 300)
    _start(fleet, bead, sid)
    ui.page.set_viewport_size({"width": 360, "height": 740})
    ui.page.emulate_media(color_scheme=scheme)
    for r in (f"projects/{PROJ}/beads/{bead}/live/{sid}", "attention", "cost", ""):
        page = goto(ui, r)
        page.wait_for_timeout(400)
        overflow = page.evaluate(
            "document.documentElement.scrollWidth - document.documentElement.clientWidth")
        assert overflow <= 1, (r, overflow)
