"""Layer 2 + 3 (ADR-f42387c6): Playwright tests for the fleet UI usability pass.

Covers the project page, the incremental activity feed, the paged bead list, the runs
screens, and the readable bead detail page (history cells, font size, section colours,
360 px layouts). Reuses the server/browser fixtures of ``test_ui`` (module-scoped browser,
console errors and off-origin requests fail the test, pinned clock). Skips without Chromium.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import time
import urllib.request
from urllib.parse import urlparse

import pytest

from agent_takt_fleet.web import demo

from .test_explorer_api import gov
from .test_ui import (  # noqa: F401  (fixtures are used by name)
    SCREENSHOT_DIR, at, browser, expect, goto, stack, ui, wait_streams,
)

pytestmark = pytest.mark.ui

PROJ = "demo-1-full"
ROOT = f"B-{demo._hex8(PROJ)}"          # the "Feature: demo" tree (has a blocked bead)
G = gov()
DEV = f"{G}-dev"


def route(b=""):
    return f"projects/{PROJ}/{b}".rstrip("/")


def project_obj(ui, name=PROJ):
    return next(p for p in ui.server.fleet.projects if p.name == name)


def api(ui, path):
    with urllib.request.urlopen(ui.server.url + path) as r:
        return json.load(r)


def requests_to(ui, fragment):
    return [u for u in ui.requests if fragment in urlparse(u).path or fragment in u]


def running_run_id(ui):
    runs = api(ui, f"/api/projects/{PROJ}")["scheduler_runs"]
    return next(r["run_id"] for r in runs if r["status"] == "running")


def count_text(shown, total):
    """The bead-count label: 'N of M beads' while paged, 'M beads' once all loaded."""
    return f"{shown} of {total} beads" if shown < total else f"{total} {'bead' if total == 1 else 'beads'}"


def overflow(page):
    return page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")


def mobile(ui, scheme="light"):
    ui.page.set_viewport_size({"width": 360, "height": 740})
    ui.page.emulate_media(color_scheme=scheme)


def add_runs(path, count, start_minutes=-2000):
    """Append ``count`` finished runs (one bead each, no cost) to a project's log."""
    ids = []
    for i in range(count):
        rid = f"SR-{0xA0000 + i:08x}"
        ids.append(rid)
        base = start_minutes + i
        demo.append_event(path, "scheduler_run_started",
                          {"run_id": rid, "pid": 7, "hostname": "h", "runner": "claude", "max_workers": 1},
                          clock=at(base))
        demo.append_event(path, "bead_completed",
                          {"run_id": rid, "bead_id": f"B-x{i}", "agent_type": "developer", "duration_seconds": 3},
                          clock=at(base + 0.2))
        demo.append_event(path, "scheduler_run_finished",
                          {"run_id": rid, "pid": 7, "exit_reason": "quiescence", "cycles": 1},
                          clock=at(base + 0.5))
    return ids


# ------------------------------------------------------------------ project page


def test_project_page_content(ui):
    """Scenario: fleet-web-ui / Project page leads with recent work / Project page content"""
    feats = api(ui, f"/api/projects/{PROJ}/features")
    beads = api(ui, f"/api/projects/{PROJ}/beads")
    assert len(feats["features"]) >= 12 and beads["total"] >= 40
    page = goto(ui, route())
    expect(page.get_by_test_id("screen-project")).to_be_visible()
    frows = page.get_by_test_id("project-feature-row")
    brows = page.get_by_test_id("project-bead-row")
    expect(frows).to_have_count(8)
    expect(brows).to_have_count(10)
    # rows follow the API order: most recently active features, newest beads
    assert [frows.nth(i).get_attribute("data-root-id") for i in range(8)] == \
        [f["bead_id"] for f in feats["features"][:8]]
    assert [brows.nth(i).get_attribute("data-bead-id") for i in range(10)] == \
        [b["bead_id"] for b in beads["beads"][:10]]
    # link targets
    assert page.get_by_test_id("link-features").get_attribute("href") == f"#/projects/{PROJ}/features"
    assert page.get_by_test_id("link-beads").get_attribute("href") == f"#/projects/{PROJ}/beads"
    assert page.get_by_test_id("link-runs").get_attribute("href") == f"#/projects/{PROJ}/runs"
    assert page.get_by_test_id("link-activity").get_attribute("href") == "#/activity"
    first_feature = frows.first.get_by_test_id("feature-link").get_attribute("href")
    assert first_feature == f"#/projects/{PROJ}/features/{feats['features'][0]['bead_id']}"
    assert brows.first.get_by_test_id("bead-link").get_attribute("href") == \
        f"#/projects/{PROJ}/beads/{beads['beads'][0]['bead_id']}"
    # last run line links to the newest run page
    runs = api(ui, f"/api/projects/{PROJ}/runs")["runs"]
    expect(page.get_by_test_id("last-run-link")).to_have_attribute(
        "href", f"#/projects/{PROJ}/runs/{runs[0]['run_id']}")
    # short activity strip, no scheduler runs table
    expect(page.get_by_test_id("activity-item").first).to_be_visible()
    assert 0 < page.get_by_test_id("activity-item").count() <= 10
    expect(page.get_by_test_id("scheduler-runs")).to_have_count(0)
    expect(page.get_by_test_id("scheduler-run")).to_have_count(0)
    assert page.locator("table").count() == 0


def test_project_page_section_order_and_running_link(ui):
    page = goto(ui, route())
    top = lambda tid: page.get_by_test_id(tid).bounding_box()["y"]  # noqa: E731
    expect(page.get_by_test_id("project-footer")).to_be_visible()
    order = [top("project-running"), top("project-features"), top("project-beads"), top("project-footer")]
    assert order == sorted(order)
    link = page.get_by_test_id("running-since-link")
    assert link.get_attribute("href") == f"#/projects/{PROJ}/runs/{running_run_id(ui)}"
    # the footer holds the last run line before the activity strip
    last_y = page.get_by_test_id("project-last-run").bounding_box()["y"]
    assert last_y < page.get_by_test_id("project-activity").bounding_box()["y"]


def test_running_link_only_when_a_run_is_running(ui):
    page = goto(ui, "projects/demo-2-older")
    expect(page.get_by_test_id("screen-project")).to_be_visible()
    expect(page.get_by_test_id("project-footer")).to_be_visible()
    expect(page.get_by_test_id("running-since-link")).to_have_count(0)
    expect(page.get_by_test_id("project-running")).to_have_count(0)


def test_project_page_empty_states(ui):
    p = project_obj(ui, "demo-3-newer").path
    for f in (p / ".takt" / "beads").glob("*.json"):
        f.unlink()
    (p / ".takt" / "logs" / "events.jsonl").unlink()
    page = goto(ui, "projects/demo-3-newer")
    expect(page.get_by_test_id("project-features-empty")).to_be_visible()
    expect(page.get_by_test_id("project-beads-empty")).to_be_visible()
    expect(page.get_by_test_id("activity-empty")).to_be_visible()
    expect(page.get_by_test_id("project-last-run")).to_contain_text("No scheduler runs yet.")


def test_project_not_found_path(ui):
    ui.allow_network_errors = True  # the 404 is logged by Chromium
    page = goto(ui, "projects/nope")
    expect(page.get_by_test_id("project-notfound")).to_be_visible()


def test_last_run_opens_the_run_page(ui):
    """Scenario: fleet-web-ui / Project page leads with recent work / Last run opens the run page"""
    runs = api(ui, f"/api/projects/{PROJ}/runs")["runs"]
    detail = api(ui, f"/api/projects/{PROJ}/runs/{runs[0]['run_id']}")
    page = goto(ui, route())
    page.get_by_test_id("last-run-link").click()
    expect(page.get_by_test_id("screen-run")).to_be_visible()
    assert page.evaluate("location.hash") == f"#/projects/{PROJ}/runs/{runs[0]['run_id']}"
    expect(page.get_by_test_id("run-cycle")).to_have_count(len(detail["cycles"]))
    expect(page.get_by_test_id("run-bead")).to_have_count(len(detail["beads"]))
    assert len(detail["cycles"]) >= 1 and len(detail["beads"]) >= 1


def test_project_page_live_update_keeps_working(ui):
    page = goto(ui, route())
    expect(page.get_by_test_id("project-footer")).to_be_visible()
    demo.append_event(project_obj(ui).path, "bead_failed", {"summary": "usability-live"}, clock=at(30))
    expect(page.get_by_test_id("activity-item").first).to_contain_text("usability-live")
    expect(page.get_by_test_id("project-feature-row")).to_have_count(8)


# ------------------------------------------------------------------ activity feed


def event_keys_in_dom(page):
    return page.evaluate("""() => [...document.querySelectorAll('[data-testid=activity-item]')].map(li =>
        [li.dataset.project, li.querySelector('.time').title, li.querySelector('.summary').textContent].join('|'))""")


def api_keys(events):
    return [f"{e['project']}|{e['timestamp']}|{e['summary']}" for e in events]


def test_activity_initial_page_is_25(ui):
    page = goto(ui, "activity")
    expect(page.get_by_test_id("activity-item").first).to_be_visible()
    expect(page.get_by_test_id("activity-item")).to_have_count(25)
    expected = api(ui, "/api/events?limit=25")["events"]
    assert event_keys_in_dom(page) == api_keys(expected)


def test_activity_live_prepend_without_refetch(ui):
    """Scenario: fleet-web-ui / Incremental activity feed / Live prepend without refetch"""
    page = goto(ui, "activity")
    wait_streams(ui)
    expect(page.get_by_test_id("activity-item")).to_have_count(25)
    before = len(requests_to(ui, "/api/events"))
    assert before == 1
    page.evaluate("window.__marker = 11")
    demo.append_event(project_obj(ui, "demo-2-older").path, "bead_blocked",
                      {"summary": "live-one"}, clock=at(20))
    first = page.get_by_test_id("activity-item").first
    expect(first).to_contain_text("live-one")
    demo.append_event(project_obj(ui).path, "bead_completed", {"summary": "live-two"}, clock=at(21))
    expect(page.get_by_test_id("activity-item").first).to_contain_text("live-two")
    expect(page.get_by_test_id("activity-item").nth(1)).to_contain_text("live-one")
    page.wait_for_timeout(700)
    assert len(requests_to(ui, "/api/events")) == before  # no refetch of the list
    assert page.evaluate("window.__marker") == 11


def test_activity_selected_project_ignores_other_projects_live_events(ui):
    page = goto(ui, "activity")
    wait_streams(ui)
    expect(page.get_by_test_id("activity-item")).to_have_count(25)
    page.get_by_test_id("activity-filter").select_option(PROJ)
    expect(page.get_by_test_id("activity-item").first).to_be_visible()
    shown = lambda: {  # noqa: E731
        page.get_by_test_id("activity-item").nth(i).get_attribute("data-project")
        for i in range(page.get_by_test_id("activity-item").count())}
    assert shown() == {PROJ}
    demo.append_event(project_obj(ui, "demo-2-older").path, "bead_failed",
                      {"summary": "other-project"}, clock=at(22))
    page.wait_for_timeout(700)
    expect(page.get_by_text("other-project")).to_have_count(0)
    demo.append_event(project_obj(ui).path, "bead_failed", {"summary": "same-project"}, clock=at(23))
    expect(page.get_by_test_id("activity-item").first).to_contain_text("same-project")


def test_activity_load_older_appends_without_duplicates(ui):
    """Scenario: fleet-web-ui / Incremental activity feed / Load older"""
    page = goto(ui, "activity")
    older = page.get_by_test_id("activity-load-older")
    expect(older).to_be_visible()
    expect(page.get_by_test_id("activity-item")).to_have_count(25)
    older.click()
    expect(page.get_by_test_id("activity-item")).to_have_count(50)
    first = api(ui, "/api/events?limit=25")
    second = api(ui, "/api/events?limit=25&cursor=" + first["next_cursor"])
    expected = api_keys(first["events"] + second["events"])
    got = event_keys_in_dom(page)
    # strict append: the first page stays on top untouched and the older page follows in the
    # server's order, even when the boundary falls inside equal timestamps
    assert got == expected
    assert len(set(got)) == 50
    # keep going until the server says there is nothing older
    for _ in range(20):
        if page.get_by_test_id("activity-load-older").count() == 0:
            break
        n = page.get_by_test_id("activity-item").count()
        page.get_by_test_id("activity-load-older").click()
        expect(page.get_by_test_id("activity-item")).not_to_have_count(n)
    expect(page.get_by_test_id("activity-load-older")).to_have_count(0)
    final = event_keys_in_dom(page)
    assert len(final) == len(set(final))
    cursor, walked = None, []
    while True:
        r = api(ui, "/api/events?limit=200" + (f"&cursor={cursor}" if cursor else ""))
        walked += api_keys(r["events"])
        cursor = r["next_cursor"]
        if not cursor:
            break
    assert final == list(dict.fromkeys(walked))


def test_feed_helpers_cap_list_and_resume_without_gaps(ui):
    page = goto(ui, "activity")
    out = page.evaluate("""async () => {
      const m = await import('/static/js/feed.js');
      const mk = (i) => ({project: 'p', timestamp: new Date(1e12 + i * 1000).toISOString(),
                          event_type: 't', summary: 's' + i});
      const all = Array.from({length: 600}, (_, i) => mk(i));
      const merged = m.mergeEvents([], all);
      const newest = m.mergeEvents(merged, [mk(1000)]);
      const key = (e) => `${e.project}|${e.timestamp}|${e.event_type}|${e.summary}`;
      // two fetched pages of 25 each; trimming drops the oldest kept events
      const pageA = all.slice(575).reverse(), pageB = all.slice(550, 575).reverse();
      const pages = [
        {req: null, next: 'c1', keys: pageA.map(key)},
        {req: 'c1', next: 'c2', keys: pageB.map(key)},
      ];
      const full = m.resumeCursor(pages, [...pageA, ...pageB]);
      const partial = m.resumeCursor(pages, [...pageA, ...pageB.slice(0, 10)]);
      const dropped = m.resumeCursor(pages, pageA);
      return {n: merged.length, max: m.MAX_KEPT, page: m.PAGE_SIZE, newestFirst: newest[0].summary,
              newestLen: newest.length, lastKept: merged[merged.length - 1].summary, full, partial, dropped};
    }""")
    assert out["page"] == 25 and out["max"] == 500
    assert out["n"] == 500 and out["newestLen"] == 500          # never exceeds 500
    assert out["newestFirst"] == "s1000" and out["lastKept"] == "s100"  # oldest dropped
    assert out["full"] == "c2"       # last page fully kept: continue after it
    assert out["partial"] == "c1"    # partially kept page: re-request it (dupes are deduped)
    assert out["dropped"] == "c1"    # nothing of the second page is kept: resume after the first


def test_activity_refetches_only_on_reconnect(ui):
    ui.allow_network_errors = True
    page = goto(ui, "activity")
    wait_streams(ui)
    expect(page.get_by_test_id("activity-item")).to_have_count(25)
    assert len(requests_to(ui, "/api/events")) == 1
    demo.append_event(project_obj(ui).path, "bead_completed", {"summary": "no-refetch"}, clock=at(24))
    expect(page.get_by_test_id("activity-item").first).to_contain_text("no-refetch")
    page.wait_for_timeout(500)
    assert len(requests_to(ui, "/api/events")) == 1
    gate = ui.server.gate
    gate.block.set()
    gate.sever.set()
    expect(page.get_by_test_id("connection-state")).to_have_attribute("data-state", "reconnecting")
    gate.block.clear()
    gate.sever.clear()
    expect(page.get_by_test_id("connection-state")).to_have_attribute("data-state", "live", timeout=20000)
    deadline = time.time() + 10
    while len(requests_to(ui, "/api/events")) < 2 and time.time() < deadline:
        page.wait_for_timeout(100)
    assert len(requests_to(ui, "/api/events")) >= 2  # reconnect refetch


# ------------------------------------------------------------------ bead list


def test_bead_list_newest_first_with_load_older(ui):
    """Bead list UI test shows newest first with 'Load older'"""
    expected = api(ui, f"/api/projects/{PROJ}/beads?limit=200")
    total = expected["total"]
    assert total > 50
    page = goto(ui, route("beads"))
    rows = page.get_by_test_id("bead-row")
    expect(rows).to_have_count(25)
    expect(page.get_by_test_id("bead-count")).to_have_text(f"25 of {total} beads")
    ids = lambda: [rows.nth(i).get_attribute("data-bead-id") for i in range(rows.count())]  # noqa: E731
    assert ids() == [b["bead_id"] for b in expected["beads"][:25]]  # newest first
    page.get_by_test_id("bead-load-older").click()
    expect(rows).to_have_count(50)
    assert ids() == [b["bead_id"] for b in expected["beads"][:50]]
    assert len(set(ids())) == 50
    # keep loading until the button disappears; the count then reads "N beads"
    for _ in range(10):
        if page.get_by_test_id("bead-load-older").count() == 0:
            break
        n = rows.count()
        page.get_by_test_id("bead-load-older").click()
        expect(rows).not_to_have_count(n)
    expect(page.get_by_test_id("bead-load-older")).to_have_count(0)
    expect(rows).to_have_count(total)
    expect(page.get_by_test_id("bead-count")).to_have_text(f"{total} beads")
    assert ids() == [b["bead_id"] for b in expected["beads"]]


def test_bead_list_filters_reset_to_first_page(ui):
    page = goto(ui, route("beads"))
    rows = page.get_by_test_id("bead-row")
    expect(rows).to_have_count(25)
    page.get_by_test_id("bead-load-older").click()
    expect(rows).to_have_count(50)
    page.get_by_test_id("bead-filter-agent").select_option("developer")
    dev_total = api(ui, f"/api/projects/{PROJ}/beads?agent=developer")["total"]
    expect(rows).to_have_count(min(25, dev_total))
    expect(page.get_by_test_id("bead-count")).to_have_text(count_text(min(25, dev_total), dev_total))
    if dev_total > 25:
        page.get_by_test_id("bead-load-older").click()
        expect(rows).to_have_count(min(50, dev_total))
    page.get_by_test_id("bead-filter-status").select_option("done")
    both = api(ui, f"/api/projects/{PROJ}/beads?agent=developer&status=done")["total"]
    expect(rows).to_have_count(min(25, both))
    page.get_by_test_id("bead-search").fill("billing")
    q_total = api(ui, f"/api/projects/{PROJ}/beads?agent=developer&status=done&q=billing")["total"]
    expect(rows).to_have_count(min(25, q_total))
    page.get_by_test_id("bead-filter-clear").click()
    expect(rows).to_have_count(25)
    page.get_by_test_id("bead-filter-label").fill("governance")
    lab_total = api(ui, f"/api/projects/{PROJ}/beads?label=governance")["total"]
    expect(rows).to_have_count(min(25, lab_total))
    if lab_total <= 25:
        expect(page.get_by_test_id("bead-count")).to_have_text(re.compile(rf"^{lab_total} beads?$"))


def test_bead_list_live_refresh_keeps_loaded_rows_and_shows_new_at_top(ui):
    page = goto(ui, route("beads"))
    rows = page.get_by_test_id("bead-row")
    expect(rows).to_have_count(25)
    page.get_by_test_id("bead-load-older").click()
    expect(rows).to_have_count(50)
    total = api(ui, f"/api/projects/{PROJ}/beads?limit=1")["total"]
    path = project_obj(ui).path / ".takt" / "beads" / "B-newest01.json"
    path.write_text(json.dumps({
        "bead_id": "B-newest01", "title": "Brand new bead", "agent_type": "developer", "status": "ready",
        "feature_root_id": ROOT,
        "execution_history": [{"timestamp": "2026-03-01T12:30:00+00:00", "event": "created",
                               "agent_type": "scheduler", "summary": "Bead created", "details": {}}],
    }))
    # the refresh re-covers the previously loaded count (50), so the new bead is on top
    expect(rows.first).to_have_attribute("data-bead-id", "B-newest01")
    expect(rows).to_have_count(50)
    expect(page.get_by_test_id("bead-count")).to_have_text(f"50 of {total + 1} beads")
    assert page.get_by_test_id("bead-load-older").count() == 1
    assert api(ui, f"/api/projects/{PROJ}/beads?limit=1")["total"] == total + 1


def test_bead_list_stale_responses_are_ignored(ui):
    """A slow response for an old filter must not overwrite the newest one."""
    page = goto(ui, route("beads"))
    expect(page.get_by_test_id("bead-row")).to_have_count(25)
    delayed = {"n": 0}

    def slow_first(route_):
        url = route_.request.url
        if "agent=tester" in url and delayed["n"] == 0:
            delayed["n"] += 1
            time.sleep(1.2)  # respond after the later request has already finished
        route_.continue_()

    page.context.route("**/api/projects/*/beads?*", slow_first)
    page.get_by_test_id("bead-filter-agent").select_option("tester")
    page.get_by_test_id("bead-filter-agent").select_option("developer")
    dev_total = api(ui, f"/api/projects/{PROJ}/beads?agent=developer")["total"]
    expect(page.get_by_test_id("bead-count")).to_have_text(count_text(min(25, dev_total), dev_total))
    page.wait_for_timeout(1800)
    agents = page.evaluate("[...document.querySelectorAll('[data-testid=bead-row] td:nth-child(4)')].map(e=>e.textContent)")
    assert agents and set(agents) == {"developer"}


# ------------------------------------------------------------------ runs screens


def test_runs_list_paging_and_row_click(ui):
    p = project_obj(ui, "demo-3-newer")
    ids = add_runs(p.path, 30)
    total = api(ui, "/api/projects/demo-3-newer/runs")["total"]
    assert total >= 31
    page = goto(ui, "projects/demo-3-newer/runs")
    rows = page.get_by_test_id("run-row")
    expect(rows).to_have_count(25)
    expect(page.get_by_test_id("run-count")).to_have_text(f"25 of {total} run(s)")
    page.get_by_test_id("run-load-older").click()
    expect(rows).to_have_count(total)
    expect(page.get_by_test_id("run-load-older")).to_have_count(0)
    shown = [rows.nth(i).get_attribute("data-run-id") for i in range(rows.count())]
    assert len(set(shown)) == total and set(ids) <= set(shown)
    # clicking the row (not the link) navigates to the run page
    rows.first.locator("td").nth(2).click()
    expect(page.get_by_test_id("screen-run")).to_be_visible()
    assert page.evaluate("location.hash") == f"#/projects/demo-3-newer/runs/{shown[0]}"


def test_runs_list_columns_and_stale_badge(ui):
    p = project_obj(ui, "demo-2-older").path
    rid = "SR-5ca1e000"
    demo.append_event(p, "scheduler_run_started", {"run_id": rid, "pid": 1, "hostname": "h",
                                                   "runner": "codex", "max_workers": 7}, clock=at(-60))
    rows_api = api(ui, "/api/projects/demo-2-older/runs?limit=200")["runs"]
    page = goto(ui, "projects/demo-2-older/runs")
    expect(page.get_by_test_id("run-row").first).to_be_visible()
    for h in ("Exit reason", "Workers", "Runner"):
        expect(page.locator("th", has_text=h)).to_have_count(1)
    finished = next(r for r in rows_api if r["status"] == "quiescence")
    row = page.locator(f'[data-run-id="{finished["run_id"]}"]')
    expect(row.get_by_test_id("run-row-exit-reason")).to_have_text("quiescence")
    expect(row.get_by_test_id("run-row-workers")).to_have_text(str(finished["max_workers"]))
    expect(row.get_by_test_id("run-row-runner")).to_have_text(finished["runner"])
    stale = [r for r in rows_api if r["status"] == "stale"]
    assert stale, "demo fleet should contain a stale run"
    badge = page.locator(f'[data-run-id="{stale[0]["run_id"]}"]').get_by_test_id("run-status-badge")
    expect(badge).to_have_class(re.compile(r"status-stale"))
    expect(badge).not_to_have_class(re.compile(r"status-warn"))


def test_run_page_header_cycles_beads_and_cost(ui):
    runs = api(ui, f"/api/projects/{PROJ}/runs")["runs"]
    finished = next(r for r in runs if r["status"] == "quiescence")
    detail = api(ui, f"/api/projects/{PROJ}/runs/{finished['run_id']}")
    page = goto(ui, route(f"runs/{finished['run_id']}"))
    expect(page.get_by_test_id("screen-run")).to_be_visible()
    expect(page.get_by_test_id("run-status")).to_have_text("quiescence")
    expect(page.get_by_test_id("run-total-cost")).to_have_text(f"${detail['total_cost_usd']:.2f}")
    expect(page.get_by_test_id("run-cycle")).to_have_count(len(detail["cycles"]))
    expect(page.get_by_test_id("run-bead")).to_have_count(len(detail["beads"]))
    link = page.get_by_test_id("run-bead-link").first
    bead_id = detail["beads"][0]["bead_id"]
    assert link.get_attribute("href") == f"#/projects/{PROJ}/beads/{bead_id}"
    # runs list links back here and the breadcrumb is present
    expect(page.get_by_test_id("breadcrumb")).to_be_visible()


def test_run_page_outcome_colours_and_null_cost(ui):
    p = project_obj(ui, "demo-2-older").path
    rid = "SR-00c0ffee"
    ev = lambda kind, **k: demo.append_event(p, kind, {"run_id": rid, **k}, clock=at(-50))  # noqa: E731
    ev("scheduler_run_started", pid=9, hostname="h", runner="codex", max_workers=3)
    ev("bead_started", bead_id="B-ok", agent_type="developer", title="Fine")
    ev("bead_completed", bead_id="B-ok", agent_type="developer", duration_seconds=5)
    ev("bead_blocked", bead_id="B-blk", agent_type="tester", duration_seconds=5)
    ev("bead_failed", bead_id="B-bad", agent_type="review", duration_seconds=5)
    ev("bead_started", bead_id="B-run", agent_type="developer")
    ev("scheduler_run_finished", pid=9, exit_reason="max_cycles", cycles=0)
    page = goto(ui, f"projects/demo-2-older/runs/{rid}")
    expect(page.get_by_test_id("run-bead")).to_have_count(4)
    expect(page.get_by_test_id("run-total-cost")).to_have_text("—")  # no cost_usd anywhere
    expect(page.get_by_test_id("run-runner")).to_have_text("codex")
    expect(page.get_by_test_id("run-workers")).to_have_text("3")
    klass = {"completed": "status-done", "blocked": "status-blocked",
             "failed": "status-failed", "in_progress": "status-running"}
    colours = set()
    for outcome, css in klass.items():
        row = page.locator(f'[data-testid="run-bead"][data-outcome="{outcome}"]')
        expect(row).to_have_count(1)
        badge = row.locator(".badge")
        expect(badge).to_have_class(re.compile(rf"\b{css}\b"))
        colours.add(badge.evaluate("e => getComputedStyle(e).color + '|' + getComputedStyle(e).backgroundColor"))
    assert len(colours) >= 3  # outcomes are visually distinguishable


def test_unknown_run_shows_not_found(ui):
    ui.allow_network_errors = True  # the API 404 is logged by Chromium
    page = goto(ui, route("runs/SR-deadbeef"))
    body = page.get_by_test_id("view")
    expect(body).to_be_visible()
    expect(body).to_contain_text(re.compile(r"Run not found|Failed to load"))
    expect(page.get_by_test_id("screen-run")).to_have_count(0)


def test_run_page_live_updates_without_refetch(ui):
    rid = running_run_id(ui)
    p = project_obj(ui).path
    page = goto(ui, route(f"runs/{rid}"))
    wait_streams(ui)
    expect(page.get_by_test_id("run-bead")).to_have_count(2)
    expect(page.get_by_test_id("run-total-cost")).to_have_text("$0.43")
    run_url = lambda: [u for u in ui.requests if f"/runs/{rid}" in u and "/api/" in u]  # noqa: E731
    assert len(run_url()) == 1
    demo.append_event(p, "bead_started", {"run_id": rid, "bead_id": "B-live1", "agent_type": "developer",
                                           "title": "Live bead"}, clock=at(26))
    row = page.locator('[data-testid="run-bead"][data-bead-id="B-live1"]')
    expect(row).to_have_attribute("data-outcome", "in_progress")
    demo.append_event(p, "bead_completed", {"run_id": rid, "bead_id": "B-live1", "agent_type": "developer",
                                             "duration_seconds": 12, "cost_usd": 0.5}, clock=at(27))
    expect(row).to_have_attribute("data-outcome", "completed")
    expect(page.get_by_test_id("run-total-cost")).to_have_text("$0.93")
    # another run's event and another project's event are ignored
    demo.append_event(p, "bead_started", {"run_id": "SR-ffffffff", "bead_id": "B-foreign"}, clock=at(28))
    demo.append_event(project_obj(ui, "demo-2-older").path, "bead_started",
                      {"run_id": rid, "bead_id": "B-elsewhere"}, clock=at(28))
    page.wait_for_timeout(700)
    expect(page.get_by_test_id("run-bead")).to_have_count(3)
    assert len(run_url()) == 1  # no refetch for bead events
    # a cycle end triggers exactly one refetch
    demo.append_event(p, "scheduler_cycle_completed", {"run_id": rid, "cycle_index": 2, "started_count": 3,
                                                        "completed_count": 3, "blocked_count": 0,
                                                        "deferred_count": 0}, clock=at(29))
    deadline = time.time() + 10
    while len(run_url()) < 2 and time.time() < deadline:
        page.wait_for_timeout(100)
    page.wait_for_timeout(600)
    assert len(run_url()) == 2
    # after navigating away, further run events cause no run activity
    page.evaluate("location.hash = '#/activity'")
    expect(page.get_by_test_id("screen-activity")).to_be_visible()
    n = len(run_url())
    demo.append_event(p, "bead_started", {"run_id": rid, "bead_id": "B-late"}, clock=at(30))
    demo.append_event(p, "scheduler_run_finished", {"run_id": rid, "pid": 1, "exit_reason": "quiescence"},
                      clock=at(31))
    page.wait_for_timeout(900)
    assert len(run_url()) == n


def test_apply_run_event_helper(ui):
    page = goto(ui, route(f"runs/{running_run_id(ui)}"))
    out = page.evaluate("""async () => {
      const m = await import('/static/js/runs.js');
      const base = {run: {run_id: 'SR-1', status: 'running'}, beads: [], total_cost_usd: null};
      const ev = (type, payload, project = 'p') => ({project, event_type: type, payload: {run_id: 'SR-1', ...payload}});
      const started = m.applyRunEvent(base, ev('bead_started', {bead_id: 'B1', title: 'T', agent_type: 'developer'}), 'p', 'SR-1');
      const done = m.applyRunEvent(started, ev('bead_completed', {bead_id: 'B1', duration_seconds: 4, cost_usd: 0.25}), 'p', 'SR-1');
      const again = m.applyRunEvent(done, ev('bead_completed', {bead_id: 'B1', cost_usd: 0.25}), 'p', 'SR-1');
      const otherRun = m.applyRunEvent(base, {project: 'p', event_type: 'bead_started', payload: {run_id: 'SR-2', bead_id: 'X'}}, 'p', 'SR-1');
      const otherProj = m.applyRunEvent(base, ev('bead_started', {bead_id: 'X'}, 'q'), 'p', 'SR-1');
      return {started: started.beads, done: done.beads[0], total: done.total_cost_usd,
              againCost: again.beads[0].cost_usd, againTotal: again.total_cost_usd,
              otherRun: otherRun === base, otherProj: otherProj === base, count: again.beads.length};
    }""")
    assert out["started"][0]["outcome"] == "in_progress" and out["started"][0]["title"] == "T"
    assert out["done"]["outcome"] == "completed" and out["done"]["duration_seconds"] == 4
    assert out["done"]["cost_usd"] == 0.25 and out["total"] == 0.25
    assert out["againCost"] == 0.5 and out["againTotal"] == 0.5 and out["count"] == 1  # accumulates
    assert out["otherRun"] and out["otherProj"]


# ------------------------------------------------------------------ bead detail


def hist_api(ui, bead_id=DEV):
    return api(ui, f"/api/projects/{PROJ}/beads/{bead_id}")


def test_history_cells_are_separated(ui):
    """Scenario: fleet-web-ui / Readable bead detail / History cells are separated"""
    detail = hist_api(ui)
    page = goto(ui, route(f"beads/{DEV}"))
    items = page.get_by_test_id("history-item")
    n = len(detail["execution_history"])
    assert n >= 2
    expect(items).to_have_count(n)
    for i in range(n):
        item = items.nth(i)
        cells = [item.get_by_test_id(t) for t in
                 ("history-timestamp", "history-event", "history-agent", "history-summary")]
        boxes = [c.bounding_box() for c in cells]
        assert all(b and b["width"] > 0 for b in boxes), i
        # separate, non-overlapping, left to right with a visible gap between neighbours
        for a, b in zip(boxes, boxes[1:]):
            assert b["x"] - (a["x"] + a["width"]) >= 4, (i, a, b)
        text = item.locator(".bd-hist-row").inner_text()
        assert not re.search(r"\d{2}:\d{2}:\d{2}[A-Za-z]", text), text
        # the cell texts match the API entry and are distinct elements
        h = detail["execution_history"][i]
        assert cells[1].inner_text().strip() == h["event"]
        assert cells[3].inner_text().strip() == (h["summary"] or "")
        # whitespace or a gap between every adjacent pair of cells
        gaps = page.evaluate("""(el) => [...el.children].slice(0, -1).map((c, k) => {
              const a = c.getBoundingClientRect(), b = el.children[k + 1].getBoundingClientRect();
              return b.left - a.right; })""", item.locator(".bd-hist-row").element_handle())
        assert all(g >= 4 for g in gaps), gaps


def test_history_timestamp_local_time_with_iso_title_and_families(ui):
    detail = hist_api(ui)
    page = goto(ui, route(f"beads/{DEV}"))
    items = page.get_by_test_id("history-item")
    expect(items).to_have_count(len(detail["execution_history"]))
    for i in range(len(detail["execution_history"])):
        ts = items.nth(i).get_by_test_id("history-timestamp")
        iso = detail["execution_history"][i]["timestamp"]
        assert ts.get_attribute("title") == iso
        shown = ts.inner_text().strip()
        assert re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d", shown), shown
        local = page.evaluate(
            "(iso) => { const d = new Date(iso); const p = n => String(n).padStart(2, '0');"
            " return `${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`; }",
            iso)
        assert shown == local
        fam = items.nth(i).get_by_test_id("history-event").get_attribute("data-family")
        assert fam in {"lifecycle", "failure", "verdict", "scheduler"}
    # a failure-ish event gets the failure family; a plain lifecycle one does not
    detail2 = hist_api(ui, f"{ROOT}-dev-b")
    page2 = goto(ui, route(f"beads/{ROOT}-dev-b"))
    events = page2.get_by_test_id("history-event")
    expect(events).to_have_count(len(detail2["execution_history"]))
    created = page2.locator('[data-testid="history-event"]', has_text="created").first
    expect(created).to_have_attribute("data-family", "lifecycle")


def test_history_details_toggle_only_on_rows_with_details(ui):
    bead = f"{ROOT}-dev-b"
    detail = hist_api(ui, bead)
    page = goto(ui, route(f"beads/{bead}"))
    items = page.get_by_test_id("history-item")
    expect(items).to_have_count(len(detail["execution_history"]))
    with_details = [bool(h.get("details")) for h in detail["execution_history"]]
    for i, has in enumerate(with_details):
        expect(items.nth(i).get_by_test_id("history-details")).to_have_count(1 if has else 0)
    # the shared fixture bead used by older tests keeps its testids
    page = goto(ui, route(f"beads/{DEV}"))
    assert page.get_by_test_id("history-item").count() >= 2


def test_history_rows_with_details_exist_and_expand(ui):
    p = project_obj(ui).path
    bead = json.loads((p / ".takt" / "beads" / f"{ROOT}-dev-b.json").read_text())
    bead["execution_history"].append({"timestamp": "2026-03-01T11:59:00+00:00", "event": "failed",
                                      "agent_type": "developer", "summary": "Worker failed",
                                      "details": {"error": "boom", "attempt": 2}})
    (p / ".takt" / "beads" / f"{ROOT}-dev-b.json").write_text(json.dumps(bead))
    page = goto(ui, route(f"beads/{ROOT}-dev-b"))
    toggle = page.get_by_test_id("history-details")
    expect(toggle).to_have_count(1)
    expect(toggle.locator("pre")).not_to_be_visible()
    toggle.locator("summary").click()
    expect(toggle.locator("pre")).to_contain_text('"error": "boom"')
    failed = page.locator('[data-testid="history-item"][data-event="failed"] [data-testid="history-event"]')
    expect(failed).to_have_attribute("data-family", "failure")


def test_history_rows_wrap_to_two_lines_at_360(ui):
    mobile(ui)
    n = len(hist_api(ui)["execution_history"])
    page = goto(ui, route(f"beads/{DEV}"))
    items = page.get_by_test_id("history-item")
    expect(items).to_have_count(n)
    for i in range(n):
        item = items.nth(i)
        ts = item.get_by_test_id("history-timestamp").bounding_box()
        ev = item.get_by_test_id("history-event").bounding_box()
        summary = item.get_by_test_id("history-summary").bounding_box()
        assert abs(ts["y"] - ev["y"]) < 6  # first line: timestamp + event
        assert summary["y"] >= ts["y"] + ts["height"] - 2  # second line: agent + summary
    assert overflow(page) <= 1


def test_bead_detail_font_size_is_1_1_times_body(ui):
    """Scenario: fleet-web-ui / Readable bead detail / Font size and section colours"""
    page = goto(ui, route(f"beads/{DEV}"))
    expect(page.get_by_test_id("bead-detail")).to_be_visible()
    sizes = page.evaluate("""() => ({
        detail: parseFloat(getComputedStyle(document.querySelector('[data-testid=bead-detail]')).fontSize),
        body: parseFloat(getComputedStyle(document.body).fontSize)})""")
    assert sizes["body"] > 0
    assert sizes["detail"] / sizes["body"] == pytest.approx(1.1, abs=0.01)


SECTIONS = ("summary", "description", "criteria", "handoff", "assumptions", "files",
            "relations", "history", "cost", "traces", "discovered", "raw")


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_section_accent_colours_are_distinct(ui, scheme):
    """Scenario: fleet-web-ui / Readable bead detail / Font size and section colours"""
    ui.page.emulate_media(color_scheme=scheme)
    page = goto(ui, route(f"beads/{DEV}"))
    expect(page.get_by_test_id("bead-detail")).to_be_visible()
    colours = {}
    for sec in SECTIONS:
        el = page.get_by_test_id(f"bead-section-{sec}")
        expect(el).to_be_visible() if sec != "raw" else expect(el).to_be_attached()
        colours[sec] = el.evaluate("e => getComputedStyle(e).borderLeftColor")
    assert len(set(colours.values())) == len(SECTIONS), colours
    for sec in ("criteria", "handoff", "assumptions", "history"):
        assert colours[sec] not in ("rgba(0, 0, 0, 0)", "transparent")
    tints = {sec: page.get_by_test_id(f"bead-section-{sec}").locator("> summary").evaluate(
        "e => getComputedStyle(e).backgroundColor") for sec in ("criteria", "handoff", "assumptions", "history")}
    assert len(set(tints.values())) == 4, tints
    assert all(t not in ("rgba(0, 0, 0, 0)", "transparent") for t in tints.values())


def test_header_tint_persists_when_collapsed(ui):
    page = goto(ui, route(f"beads/{DEV}"))
    sec = page.get_by_test_id("bead-section-criteria")
    summary = sec.locator("> summary")
    bg = lambda: summary.evaluate("e => getComputedStyle(e).backgroundColor")  # noqa: E731
    open_bg = bg()
    assert open_bg not in ("rgba(0, 0, 0, 0)", "transparent")
    summary.click()
    expect(sec).not_to_have_attribute("open", "")
    assert bg() == open_bg
    assert sec.evaluate("e => getComputedStyle(e).borderLeftColor") != "rgba(0, 0, 0, 0)"


def test_count_pills_and_criteria_status_colours(ui):
    detail = hist_api(ui)
    page = goto(ui, route(f"beads/{DEV}"))
    expect(page.get_by_test_id("bead-detail")).to_be_visible()
    for sec in ("criteria", "handoff", "assumptions", "files", "relations", "history", "cost",
                "traces", "discovered"):
        expect(page.get_by_test_id(f"bead-section-count-{sec}")).to_be_visible()
    count = lambda sec: int(page.get_by_test_id(f"bead-section-count-{sec}").inner_text())  # noqa: E731
    assert count("criteria") == len(detail["acceptance_criteria"]) == 3
    assert count("history") == len(detail["execution_history"])
    assert count("assumptions") == len(detail["assumptions"]) == 2
    assert count("traces") == len(detail["traces"])
    assert count("discovered") == len(detail["discovered_issues"])
    assert count("relations") == len(detail["dependencies"]) + len(detail["children"])
    # criteria status classes map to distinct colours (met / unmet / descoped)
    items = page.get_by_test_id("criteria-item")
    assert [items.nth(i).get_attribute("data-status") for i in range(3)] == ["met", "unmet", "descoped"]
    col = {s: page.locator(f'[data-testid="criteria-item"][data-status="{s}"] [data-testid="criteria-status"]')
           .evaluate("e => getComputedStyle(e).color") for s in ("met", "unmet", "descoped")}
    assert len(set(col.values())) == 3, col
    border = {s: page.locator(f'[data-testid="criteria-item"][data-status="{s}"]')
              .evaluate("e => getComputedStyle(e).borderLeftColor") for s in ("met", "unmet", "descoped")}
    assert len(set(border.values())) == 3, border


def test_structural_assumption_highlight_and_verdict_and_block_reason(ui):
    page = goto(ui, route(f"beads/{DEV}"))
    structural = page.locator('[data-testid="assumption-item"][data-scope="structural"]')
    local = page.locator('[data-testid="assumption-item"][data-scope="local"]')
    expect(structural).to_have_count(1)
    expect(structural).to_have_class(re.compile(r"\bbd-structural\b"))
    expect(structural.locator(".bd-pill")).to_have_text("structural")
    expect(local).not_to_have_class(re.compile(r"bd-structural"))
    amber = structural.evaluate("e => getComputedStyle(e).backgroundColor")
    assert amber != local.evaluate("e => getComputedStyle(e).backgroundColor")
    verdict = page.get_by_test_id("bead-verdict")
    expect(verdict).to_have_text("approved")
    page2 = goto(ui, route(f"beads/{ROOT}-dev-b"))
    reason = page2.get_by_test_id("bead-block-reason")
    expect(reason).to_be_visible()
    expect(reason).to_contain_text("Blocked")
    expect(reason.locator(".bd-badge")).to_be_visible()


# ------------------------------------------------------------------ 360 px layouts


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_project_run_and_bead_pages_do_not_scroll_horizontally_at_360(ui, scheme):
    """Scenario: fleet-web-ui / Readable bead detail / Mobile layout"""
    mobile(ui, scheme)
    rid = running_run_id(ui)
    for r in (route(), route(f"runs/{rid}"), route("runs"), route(f"beads/{DEV}"),
              route(f"beads/{ROOT}-dev-b"), route("beads")):
        page = goto(ui, r)
        expect(page.get_by_test_id("view")).to_be_visible()
        page.wait_for_timeout(300)
        assert overflow(page) <= 1, (r, overflow(page))
        assert page.evaluate("document.documentElement.scrollWidth") <= 360, r


# ------------------------------------------------------------------ layer 3: screenshots


def test_usability_screenshots(ui, tmp_path):
    """Render the new screens from demo data; copied to docs when TAKT_UI_SCREENSHOTS=1."""
    page = goto(ui)
    rid = running_run_id(ui)
    shots = {
        "project-page": (route(), "screen-project"),
        "run-page": (route(f"runs/{rid}"), "screen-run"),
        "runs-list": (route("runs"), "screen-runs"),
        "bead-detail-readable": (route(f"beads/{DEV}"), "bead-detail"),
    }
    for name, (r, testid) in shots.items():
        page.evaluate(f"location.hash = '#/{r}'")
        expect(page.get_by_test_id(testid)).to_be_visible()
        page.wait_for_timeout(400)
        out = tmp_path / f"{name}.png"
        page.screenshot(path=str(out), full_page=True)
        assert out.stat().st_size > 2000
        if os.environ.get("TAKT_UI_SCREENSHOTS") == "1":
            SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(out, SCREENSHOT_DIR / f"{name}.png")
