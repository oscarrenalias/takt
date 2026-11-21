"""Layer 2 (ADR-f42387c6): Playwright tests for the attention and cost pages."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent_takt_fleet.web.app import create_app
from agent_takt_fleet.web.demo import build_fixture_fleet

from .conftest import to_projects
from .test_ui import browser, expect, goto, pw_sync, ui  # noqa: F401  (fixtures by name)

pytestmark = pytest.mark.ui



def _real_now():
    return datetime.now(timezone.utc)


@pytest.fixture
def stack(tmp_path, live_server_factory):
    """Fixture fleet stamped with the real clock so cost events fall inside the windows."""
    fleet = build_fixture_fleet(tmp_path / "rt-fleet", count=5, clock=_real_now)
    projs = to_projects(fleet)
    srv = live_server_factory(create_app(lambda: projs, installed_version="0.9.0",
                                         poll_interval=0.1, heartbeat_interval=0.3))
    srv.gate, srv.projs, srv.fleet = srv.app, projs, fleet
    return srv


SECTIONS = ("blocked", "discovered", "unmet_criteria", "structural_assumptions", "stale_runs")


def test_attention_sections_and_badge(ui):
    """Scenario: fleet-web-ui / Attention page / Attention sections."""
    page = goto(ui, "attention")
    expect(page.get_by_test_id("screen-attention")).to_be_visible()
    for sec in SECTIONS:
        expect(page.get_by_test_id(f"attention-section-{sec}")).to_be_visible()
        expect(page.get_by_test_id(f"attention-count-{sec}")).to_be_visible()
    total = ui.page.evaluate("fetch('/api/attention').then(r => r.json()).then(d => d.total)")
    expect(page.get_by_test_id("attention-total")).to_have_text(str(total))
    if total:
        expect(page.get_by_test_id("nav-attention-badge")).to_have_text(str(total))


def test_attention_navigate_to_bead(ui):
    """Scenario: fleet-web-ui / Attention page / Navigate from attention."""
    page = goto(ui, "attention")
    link = page.get_by_test_id("attention-section-blocked").get_by_test_id(
        "attention-item-link").first
    expect(link).to_be_visible()
    href = link.get_attribute("href")
    assert href.startswith("#/projects/") and "/beads/" in href
    link.click()
    expect(page.get_by_test_id("bead-detail")).to_be_visible()


def test_cost_chart_renders_and_window_links(ui):
    """Scenario: fleet-web-ui / Cost overview / Cost chart renders (+ window aggregation)."""
    page = goto(ui, "cost")
    expect(page.get_by_test_id("screen-cost")).to_be_visible()
    expect(page.get_by_test_id("cost-chart")).to_be_visible()
    assert page.get_by_test_id("cost-legend-item").count() >= 1
    assert page.get_by_test_id("cost-bar").count() >= 1
    expect(page.get_by_test_id("cost-null-count")).to_be_visible()
    page.get_by_test_id("cost-segment").first.hover()
    expect(page.get_by_test_id("cost-tooltip")).to_be_visible()
    bars30 = page.get_by_test_id("cost-bar").count()
    page.get_by_test_id("cost-window-7").click()
    expect(page).to_have_url(__import__("re").compile(r"days=7"))
    page.wait_for_function(
        "n => document.querySelectorAll('[data-testid=cost-bar]').length < n", arg=bars30)


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_cost_with_data_no_horizontal_page_scroll_at_360(ui, scheme):
    ui.page.set_viewport_size({"width": 360, "height": 740})
    ui.page.emulate_media(color_scheme=scheme)
    for r in ("cost", "attention"):
        page = goto(ui, r)
        page.wait_for_timeout(400)
        overflow = page.evaluate(
            "document.documentElement.scrollWidth - document.documentElement.clientWidth")
        assert overflow <= 1, (r, overflow)
