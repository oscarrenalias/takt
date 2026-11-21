"""Layer 2 (ADR-f42387c6): Playwright smoke tests against a real server.

Marked ``ui``; they skip with a clear reason when Playwright's Chromium is not
installed (``uv run playwright install chromium``).  Every test fails on any
console error, uncaught page exception or off-origin request.  The browser clock
is pinned to the fixture clock with Playwright's clock control, animations are
disabled and no network beyond the test server is reachable.
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import threading
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlparse

import pytest

from agent_takt_fleet.models import Project
from agent_takt_fleet.web import demo
from agent_takt_fleet.web.app import create_app

from .conftest import FIXED_NOW, clock, to_projects

pytestmark = pytest.mark.ui

pw_sync = pytest.importorskip("playwright.sync_api", reason="playwright is not installed")
expect = pw_sync.expect
TIMEOUT = 8000
SCREENSHOT_DIR = Path(__file__).resolve().parents[3] / "docs" / "assets" / "fleet-ui"


def at(minutes: float):
    return lambda: FIXED_NOW + timedelta(minutes=minutes)


@pytest.fixture(scope="module")
def browser():
    # Module scope: Playwright's sync API keeps an asyncio loop running on the main
    # thread until stopped, which would break later asyncio.run() tests in the process.
    pw = pw_sync.sync_playwright().start()
    try:
        b = pw.chromium.launch()
    except Exception as exc:  # executable missing
        pw.stop()
        first = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
        pytest.skip(
            "Playwright Chromium is not installed; run `uv run playwright install chromium` "
            f"to enable ui tests ({first})"
        )
    yield b
    b.close()
    pw.stop()


class Gate:
    """ASGI wrapper that can sever the SSE stream and refuse reconnects."""

    def __init__(self, app):
        self.app = app
        self.sever = threading.Event()
        self.block = threading.Event()

    def __getattr__(self, name):
        return getattr(self.app, name)

    async def __call__(self, scope, receive, send):
        is_stream = scope["type"] == "http" and scope["path"] == "/api/stream"
        if is_stream and self.block.is_set():
            await send({"type": "http.response.start", "status": 503, "headers": []})
            await send({"type": "http.response.body", "body": b"unavailable"})
            return
        if not is_stream:
            return await self.app(scope, receive, send)

        async def guarded(message):
            if self.sever.is_set():
                raise asyncio.CancelledError  # ends the generator, closes the connection
            await send(message)

        await self.app(scope, receive, guarded)


class Ctx:
    def __init__(self):
        self.errors: list[str] = []
        self.offorigin: list[str] = []
        self.requests: list[str] = []
        self.allow_network_errors = False


@pytest.fixture
def stack(fleet, live_server_factory, browser):
    """(server, gate, projects list, fleet) for a fixture fleet with a fast poll."""
    projs = to_projects(fleet)
    gate = Gate(create_app(lambda: projs, installed_version="0.9.0",
                           poll_interval=0.1, heartbeat_interval=0.3))
    srv = live_server_factory(gate)
    srv.gate, srv.projs, srv.fleet = gate, projs, fleet
    return srv


@pytest.fixture
def ui(stack, browser):
    ctx = Ctx()
    context = browser.new_context(viewport={"width": 1280, "height": 800},
                                  color_scheme="light", reduced_motion="reduce")
    origin = urlparse(stack.url).netloc

    def guard(route):
        url = route.request.url
        ctx.requests.append(url)
        if urlparse(url).netloc != origin:
            ctx.offorigin.append(url)
            return route.abort()
        return route.continue_()

    context.route("**/*", guard)
    page = context.new_page()
    page.set_default_timeout(TIMEOUT)
    page.clock.install(time=FIXED_NOW)
    page.on("pageerror", lambda e: ctx.errors.append(f"pageerror: {e}"))
    page.on("console", lambda m: ctx.errors.append(f"console.{m.type}: {m.text}")
            if m.type == "error" else None)
    page.on("requestfailed", lambda r: None)
    ctx.page, ctx.server = page, stack
    yield ctx
    context.close()
    errors = ctx.errors
    if ctx.allow_network_errors:
        errors = [e for e in errors if "Failed to load resource" not in e and "EventSource" not in e]
    assert not errors, f"browser errors: {errors}"
    assert not ctx.offorigin, f"off-origin requests: {ctx.offorigin}"


def goto(ui, route=""):
    ui.page.goto(f"{ui.server.url}/#/{route}")
    expect(ui.page.get_by_test_id("connection-state")).to_have_attribute("data-state", "live")
    return ui.page


# --- screens & navigation ---------------------------------------------------------

@pytest.mark.parametrize("route,testid", [
    ("", "screen-overview"),
    ("activity", "screen-activity"),
    ("projects/demo-1-full", "screen-project"),
    ("projects/ghost", "screen-project"),
    ("projects/nope", "screen-project-notfound"),
    ("bogus/route", "screen-notfound"),
])
def test_each_route_renders(ui, route, testid):
    # the unknown-project 404 is deliberate; Chromium logs it as a console error
    ui.allow_network_errors = route == "projects/nope"
    page = goto(ui, route)
    expect(page.get_by_test_id(testid)).to_be_visible()


def test_overview_renders_cards(ui):
    """Scenario: fleet-web-ui / Browser screens / Overview renders and updates live"""
    page = goto(ui)
    cards = page.get_by_test_id("project-card")
    expect(cards).to_have_count(6)
    for p in ui.server.projs:
        expect(page.get_by_test_id(f"project-card-{p.name}")).to_be_visible()
    full = page.locator('[data-testid="project-card"][data-name="demo-1-full"]')
    expect(full.get_by_test_id("scheduler-state")).to_have_attribute("data-state", "running")
    expect(full.get_by_test_id("scheduler-elapsed")).to_be_visible()
    idle = page.locator('[data-testid="project-card"][data-name="demo-2-older"]')
    expect(idle.get_by_test_id("scheduler-state")).to_have_attribute("data-state", "idle")
    expect(idle.get_by_test_id("scheduler-elapsed")).to_have_count(0)
    for status in ("open", "ready", "in_progress", "done", "blocked", "handed_off"):
        expect(full.get_by_test_id(f"bead-count-{status}")).to_have_count(1)
    expect(full.get_by_test_id("bead-count-blocked")).to_have_class(re.compile(r"\bemphasis\b"))
    expect(full.get_by_test_id("bead-count-done")).not_to_have_class(re.compile(r"emphasis"))
    # version drift only for behind / unknown
    expect(idle.get_by_test_id("version-drift")).to_be_visible()
    expect(page.locator('[data-name="demo-5-no-version"]').get_by_test_id("version-drift")).to_be_visible()
    expect(full.get_by_test_id("version-drift")).to_have_count(0)


def test_overview_updates_live_without_reload(ui):
    """Scenario: fleet-web-ui / Browser screens / Overview renders and updates live"""
    page = goto(ui)
    srv = ui.server
    p = next(x for x in srv.fleet.projects if x.variant == "older")
    card = page.locator(f'[data-name="{p.name}"]')
    expect(card.get_by_test_id("bead-count-blocked")).to_have_attribute("data-count", "1")
    page.evaluate("window.__marker = 42")
    demo.set_bead_status(p.path, p.bead_ids[3], "blocked", clock=clock)
    expect(card.get_by_test_id("bead-count-blocked")).to_have_attribute("data-count", "2")
    assert page.evaluate("window.__marker") == 42  # no reload happened
    # registry change adds a card
    extra = srv.fleet.projects[2]
    srv.projs.append(Project("added-live", extra.path, ("x",)))
    expect(page.get_by_test_id("project-card")).to_have_count(7)
    expect(page.get_by_test_id("project-card-added-live")).to_be_visible()


def test_navigate_to_a_project(ui):
    """Scenario: fleet-web-ui / Browser screens / Navigate to a project"""
    page = goto(ui)
    page.get_by_test_id("project-link-demo-1-full").click()
    expect(page.get_by_test_id("screen-project")).to_be_visible()
    assert page.evaluate("location.hash") == "#/projects/demo-1-full"
    expect(page.get_by_test_id("scheduler-runs")).to_be_visible()
    runs = page.get_by_test_id("scheduler-run")
    assert runs.count() >= 2
    expect(page.locator('[data-testid="scheduler-run"][data-status="running"]')
           .get_by_test_id("run-status")).to_have_text("running")
    items = page.get_by_test_id("activity-item")
    assert items.count() > 0
    projects = {items.nth(i).get_attribute("data-project") for i in range(items.count())}
    assert projects == {"demo-1-full"}


def test_project_live_update_prepends_event(ui):
    page = goto(ui, "projects/demo-1-full")
    p = next(x for x in ui.server.fleet.projects if x.variant == "full")
    demo.append_event(p.path, "bead_failed", {"summary": "project-live"}, clock=at(30))
    first = page.get_by_test_id("activity-item").first
    expect(first).to_contain_text("project-live")
    expect(first).to_have_attribute("data-severity", "error")
    # another project's event does not appear here
    other = ui.server.fleet.projects[1]
    demo.append_event(other.path, "bead_failed", {"summary": "elsewhere"}, clock=at(31))
    page.wait_for_timeout(600)
    expect(page.get_by_text("elsewhere")).to_have_count(0)


def test_activity_feed_receives_new_events(ui):
    """Scenario: fleet-web-ui / Browser screens / Activity feed receives new events"""
    page = goto(ui, "activity")
    items = page.get_by_test_id("activity-item")
    expect(items.first).to_be_visible()
    stamps = [items.nth(i).locator(".time").get_attribute("title") for i in range(min(items.count(), 30))]
    assert stamps == sorted(stamps, reverse=True)  # newest first
    page.evaluate("window.__marker = 7")
    p = ui.server.fleet.projects[1]
    demo.append_event(p.path, "bead_blocked", {"summary": "fresh-event"}, clock=at(20))
    first = items.first
    expect(first).to_contain_text("fresh-event")
    expect(first).to_have_attribute("data-severity", "warning")
    assert page.evaluate("window.__marker") == 7
    # filter hides other projects
    page.get_by_test_id("activity-filter").select_option(p.name)
    shown = {items.nth(i).get_attribute("data-project") for i in range(items.count())}
    assert shown == {p.name}


def test_activity_unknown_event_defaults_to_info(ui):
    page = goto(ui, "activity")
    p = ui.server.fleet.projects[0]
    demo.append_event(p.path, "future_event_type", {}, clock=at(25))
    first = page.get_by_test_id("activity-item").first
    expect(first).to_contain_text("future_event_type")
    expect(first).to_have_attribute("data-severity", "info")


def test_connection_recovers(ui):
    """Scenario: fleet-web-ui / Browser screens / Connection recovers"""
    ui.allow_network_errors = True  # a refused EventSource logs a network error
    page = goto(ui, "activity")
    gate = ui.server.gate
    gate.block.set()
    gate.sever.set()
    expect(page.get_by_test_id("connection-state")).to_have_attribute("data-state", "reconnecting")
    # change happens while disconnected
    p = ui.server.fleet.projects[1]
    demo.append_event(p.path, "bead_completed", {"summary": "while-offline"}, clock=at(40))
    gate.block.clear()
    gate.sever.clear()
    expect(page.get_by_test_id("connection-state")).to_have_attribute(
        "data-state", "live", timeout=20000)
    expect(page.get_by_text("while-offline")).to_be_visible()  # refetched after reconnect


def test_no_external_requests(ui):
    """Scenario: fleet-web-ui / Browser screens / No external requests"""
    page = goto(ui)
    page.get_by_test_id("nav-activity").click()
    expect(page.get_by_test_id("screen-activity")).to_be_visible()
    page.get_by_test_id("nav-overview").click()
    page.get_by_test_id("project-link-demo-1-full").click()
    expect(page.get_by_test_id("screen-project")).to_be_visible()
    origin = urlparse(ui.server.url).netloc
    assert ui.requests and all(urlparse(u).netloc == origin for u in ui.requests)
    assert not ui.offorigin


def test_no_stray_placeholder_text_in_app_root(ui):
    page = goto(ui)
    texts = page.evaluate(
        "[...document.getElementById('app').childNodes]"
        ".filter(n => n.nodeType === 3).map(n => n.textContent.trim()).filter(Boolean)")
    assert texts == []


# --- Layer 3: screenshots from demo data ---------------------------------------------

def test_screenshots(ui, tmp_path):
    """Render overview, activity and project from the demo fixture data (light, 1280x800).

    Saved under tmp_path always; copied to docs/assets/fleet-ui/ when
    TAKT_UI_SCREENSHOTS=1 so reviewers can inspect them.
    """
    page = goto(ui)
    expect(page.get_by_test_id("project-card")).to_have_count(6)
    shots = {"overview": "", "activity": "activity", "project": "projects/demo-1-full"}
    for name, route in shots.items():
        page.evaluate(f"location.hash = '#/{route}'")
        expect(page.get_by_test_id(
            {"overview": "screen-overview", "activity": "screen-activity",
             "project": "screen-project"}[name])).to_be_visible()
        page.wait_for_timeout(400)
        out = tmp_path / f"{name}.png"
        page.screenshot(path=str(out))
        assert out.stat().st_size > 2000
        if os.environ.get("TAKT_UI_SCREENSHOTS") == "1":
            SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(out, SCREENSHOT_DIR / f"{name}.png")
