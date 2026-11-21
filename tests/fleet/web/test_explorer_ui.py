"""Layer 2 + 3 (ADR-f42387c6): Playwright smoke tests and screenshots for the bead explorer.

Reuses the server/browser fixtures of ``test_ui`` (module-scoped browser, fail on console
errors, off-origin requests blocked, pinned clock). Skips without Chromium.
"""
from __future__ import annotations

import json
import os
import shutil

import pytest

from agent_takt_fleet.web import demo

from .conftest import clock
from .test_explorer_api import gov
from .test_ui import (  # noqa: F401  (fixtures are used by name)
    SCREENSHOT_DIR, at, browser, expect, goto, pw_sync, stack, ui,
)

pytestmark = pytest.mark.ui

PROJ = "demo-1-full"
G = gov()
DEV = f"{G}-dev"


def route(b=""):
    return f"projects/{PROJ}/{b}"


def project_path(ui):
    return next(p for p in ui.server.fleet.projects if p.name == PROJ).path


def demo_beads(ui):
    return [json.loads(f.read_text())
            for f in (project_path(ui) / ".takt" / "beads").glob("*.json")]


def set_hash(ui, r):
    ui.page.evaluate(f"location.hash = '#/{r}'")


# --- routing smoke -----------------------------------------------------------

@pytest.mark.parametrize("r,testid", [
    (route("features"), "screen-features"),
    (route(f"features/{G}"), "screen-feature-tree"),
    (route(f"features/{G}/audits/{demo.AUDIT_TIMESTAMPS[1]}"), "screen-audit"),
    (route("beads"), "screen-beads"),
    (route(f"beads/{DEV}"), "bead-detail"),
    (route(f"beads/{DEV}/traces/1"), "screen-trace"),
    (route(f"beads/{DEV}/telemetry/1"), "screen-telemetry"),
])
def test_each_new_route_renders(ui, r, testid):
    page = goto(ui, r)
    expect(page.get_by_test_id(testid)).to_be_visible()
    expect(page.get_by_test_id("breadcrumb")).to_be_visible()


# --- feature list --------------------------------------------------------------

def test_feature_list_screen(ui):
    page = goto(ui, route("features"))
    rows = page.get_by_test_id("feature-row")
    expect(rows.first).to_be_visible()
    assert rows.count() >= 3
    links = page.get_by_test_id("feature-link")
    hrefs = [links.nth(i).get_attribute("href") for i in range(links.count())]
    assert f"#/projects/{PROJ}/features/{G}" in hrefs
    # only the demo feature (in_progress root) is in flight
    assert page.get_by_test_id("feature-in-flight").count() == 1
    gov_row = page.locator(f'[data-testid="feature-row"]:has([data-testid="feature-link"][href$="/{G}"])')
    expect(gov_row.get_by_test_id("feature-last-merge")).not_to_contain_text("not merged")
    expect(gov_row.get_by_test_id("feature-audits")).to_have_text("2")
    solo = page.locator('[data-testid="feature-row"]:has([data-testid="feature-last-merge"]:text("not merged"))')
    assert solo.count() >= 1
    expect(solo.first.get_by_test_id("feature-cost")).to_have_text("—")


def test_feature_list_empty_state(ui):
    for f in (project_path(ui) / ".takt" / "beads").glob("*.json"):
        f.unlink()
    page = goto(ui, route("features"))
    expect(page.get_by_test_id("features-empty")).to_be_visible()


def test_feature_list_live_refetch(ui):
    page = goto(ui, route("features"))
    expect(page.get_by_test_id("feature-row").first).to_be_visible()
    before = page.get_by_test_id("feature-row").count()
    p = project_path(ui)
    root = f"B-{demo._hex8('late-feature')}"
    bead = demo._bead(root, "Late feature", "planner", "in_progress", demo.__dict__["_utc_now"](), root=root)
    (p / ".takt" / "beads" / f"{root}.json").write_text(json.dumps(bead))
    expect(page.get_by_test_id("feature-row")).to_have_count(before + 1)


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_no_horizontal_scroll_on_mobile(ui, scheme):
    ui.page.set_viewport_size({"width": 360, "height": 740})
    ui.page.emulate_media(color_scheme=scheme)
    for r in (route("features"), route(f"features/{G}"), route("beads")):
        page = goto(ui, r)
        page.wait_for_timeout(300)
        overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
        assert overflow <= 1, (r, overflow)


@pytest.mark.xfail(strict=False, reason=(
    "known defect: the telemetry attempts table (.bd-table) on bead detail is wider than a "
    "360 px viewport and scrolls the page horizontally; reported as a discovered issue"))
def test_bead_detail_no_horizontal_scroll_on_mobile(ui):
    ui.page.set_viewport_size({"width": 360, "height": 740})
    page = goto(ui, route(f"beads/{DEV}"))
    expect(page.get_by_test_id("bead-detail")).to_be_visible()
    page.wait_for_timeout(300)
    overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
    assert overflow <= 1, overflow


# --- feature tree ------------------------------------------------------------------

def test_feature_tree_nesting_toggle_and_links(ui):
    page = goto(ui, route(f"features/{G}"))
    expect(page.get_by_test_id("bead-tree")).to_be_visible()
    expect(page.get_by_test_id(f"bead-tree-node-{G}")).to_be_visible()
    for suffix in ("dev", "test", "review", "dev2", "review-corrective"):
        expect(page.get_by_test_id(f"bead-tree-node-{G}-{suffix}")).to_be_visible()
    expect(page.get_by_test_id(f"bead-tree-node-{G}-dev2-recovery")).to_have_attribute("data-depth", "2")
    expect(page.get_by_test_id(f"bead-badge-corrective")).to_be_visible()
    # toggle collapses and expands; dependency list appears on expansion
    toggle = page.get_by_test_id(f"bead-tree-toggle-{G}-review")
    detail = page.get_by_test_id(f"bead-tree-detail-{G}-review")
    expect(detail).to_be_visible()  # depth < 2 is expanded by default
    dep = page.get_by_test_id(f"bead-dep-{G}-review-{G}-dev")
    expect(dep).to_have_attribute("href", f"#/projects/{PROJ}/beads/{G}-dev")
    toggle.click()
    expect(detail).to_have_count(0)
    toggle.click()
    expect(detail).to_be_visible()
    page.get_by_test_id(f"bead-link-{G}-dev").click()
    expect(page.get_by_test_id("bead-detail")).to_be_visible()
    assert page.evaluate("location.hash") == f"#/projects/{PROJ}/beads/{DEV}"


def test_feature_tree_merge_and_audit_links_and_empty(ui):
    page = goto(ui, route(f"features/{G}"))
    expect(page.get_by_test_id("merge-record-0")).to_contain_text("Demo Approver")
    links = page.locator('[data-testid^="audit-link-"]')
    expect(links).to_have_count(2)
    href = links.first.get_attribute("href")
    assert href == f"#/projects/{PROJ}/features/{G}/audits/{demo.AUDIT_TIMESTAMPS[1]}"
    links.first.click()
    expect(page.get_by_test_id("screen-audit")).to_be_visible()
    # a feature with no merge records and no audits
    demo_root = f"B-{demo._hex8(PROJ)}"
    set_hash(ui, route(f"features/{demo_root}"))
    expect(page.get_by_test_id("merge-records-empty")).to_be_visible()
    expect(page.get_by_test_id("audit-list-empty")).to_be_visible()


def test_feature_tree_keeps_expansion_on_live_update(ui):
    page = goto(ui, route(f"features/{G}"))
    detail = page.get_by_test_id(f"bead-tree-detail-{G}-review")
    expect(detail).to_be_visible()
    page.get_by_test_id(f"bead-tree-toggle-{G}-review").click()  # collapse
    expect(detail).to_have_count(0)
    demo.set_bead_status(project_path(ui), f"{G}-docs", "blocked", clock=clock)
    expect(page.get_by_test_id(f"bead-tree-node-{G}-docs")).to_have_attribute("data-status", "blocked")
    expect(detail).to_have_count(0)  # expansion state survives the refetch


# --- bead list & search ------------------------------------------------------------

def test_bead_search_and_filters(ui):
    """Scenario: fleet-web-ui / Drill-down screens / Bead search"""
    page = goto(ui, route("beads"))
    rows = page.get_by_test_id("bead-row")
    expect(rows.first).to_be_visible()
    full = rows.count()
    assert full > 10
    page.get_by_test_id("bead-search").fill("governance")
    n = len([b for b in demo_beads(ui) if "governance" in b["title"].lower()
             or "governance" in b["bead_id"].lower()])
    assert 0 < n < full
    expect(rows).to_have_count(n)
    for i in range(n):
        assert "governance" in rows.nth(i).inner_text().lower()
    page.get_by_test_id("bead-search").fill(f"{G}-review".lower())
    expect(rows).to_have_count(2)
    page.get_by_test_id("bead-search").fill("")
    expect(rows).to_have_count(full)
    page.get_by_test_id("bead-filter-status").select_option("blocked")
    expect(rows).to_have_count(1)
    assert rows.first.get_attribute("data-status") == "blocked"
    expect(page.get_by_test_id("bead-count")).to_contain_text("1")
    page.get_by_test_id("bead-filter-status").select_option("")
    page.get_by_test_id("bead-filter-agent").select_option("tester")
    testers = len([b for b in demo_beads(ui) if b["agent_type"] == "tester"])
    expect(rows).to_have_count(testers)
    for i in range(rows.count()):
        assert "tester" in rows.nth(i).inner_text().lower()
    page.get_by_test_id("bead-filter-label").fill("governance")
    expect(page.get_by_test_id("beads-empty")).to_be_visible()  # AND: no tester bead has the label
    page.get_by_test_id("bead-filter-agent").select_option("developer")
    expect(rows).to_have_count(1)  # only the developer bead labelled governance
    page.get_by_test_id("bead-filter-clear").click()
    expect(rows).to_have_count(full)
    page.get_by_test_id("bead-search").fill("zzz-nothing")
    expect(page.get_by_test_id("beads-empty")).to_be_visible()


def test_bead_list_refetch_keeps_filters_and_scopes_to_project(ui):
    page = goto(ui, route("beads"))
    rows = page.get_by_test_id("bead-row")
    page.get_by_test_id("bead-filter-status").select_option("done")
    done_before = len([b for b in demo_beads(ui) if b["status"] == "done"])
    expect(rows).to_have_count(done_before)
    page.wait_for_timeout(300)
    bead_calls = lambda: [u for u in ui.requests if "/beads" in u and "/api/projects/" in u]
    n0 = len(bead_calls())
    other = next(p for p in ui.server.fleet.projects if p.name != PROJ)
    demo.set_bead_status(other.path, other.bead_ids[3], "blocked", clock=clock)
    page.wait_for_timeout(1000)
    assert len(bead_calls()) == n0  # another project's change does not refetch
    # this project: a bead flips to done; filter stays and the new row shows
    p = project_path(ui)
    demo.set_bead_status(p, f"B-{demo._hex8(PROJ)}-test", "done", clock=clock)
    expect(rows).to_have_count(done_before + 1)
    expect(page.get_by_test_id("bead-filter-status")).to_have_value("done")
    for i in range(rows.count()):
        assert rows.nth(i).get_attribute("data-status") == "done"


# --- bead detail -------------------------------------------------------------------

def test_bead_detail_sections_and_raw(ui):
    page = goto(ui, route(f"beads/{DEV}"))
    detail = page.get_by_test_id("bead-detail")
    expect(detail).to_be_visible()
    for sec in ("summary", "description", "criteria", "handoff", "assumptions", "files",
                "relations", "history", "cost", "traces", "raw"):
        expect(page.get_by_test_id(f"bead-section-{sec}")).to_be_visible()
    items = page.get_by_test_id("criteria-item")
    assert [items.nth(i).get_attribute("data-status") for i in range(items.count())] == [
        "met", "unmet", "descoped"]
    scopes = page.get_by_test_id("assumption-item")
    assert [scopes.nth(i).get_attribute("data-scope") for i in range(scopes.count())] == [
        "structural", "local"]
    assert page.get_by_test_id("history-item").count() >= 2
    expect(page.get_by_test_id("telemetry-link").first).to_be_visible()
    expect(page.get_by_test_id("trace-link").first).to_be_visible()
    raw = page.get_by_test_id("bead-section-raw")
    assert raw.get_attribute("open") is None  # collapsed by default
    extra = goto(ui, route(f"beads/{G}-extra"))
    extra.get_by_test_id("bead-section-raw").locator("summary").click()
    expect(extra.get_by_test_id("bead-detail-raw")).to_contain_text("future_field")


def test_bead_detail_empty_states(ui):
    bare = demo._bead(f"{G}-bare", "Bare bead", "developer", "open", demo._utc_now(), root=G, parent=G)
    bare.update(acceptance_criteria=[], handoff_summary=None, execution_history=[], metadata=None)
    (project_path(ui) / ".takt" / "beads" / f"{G}-bare.json").write_text(json.dumps(bare))
    page = goto(ui, route(f"beads/{G}-bare"))
    expect(page.get_by_test_id("bead-detail")).to_be_visible()
    assert page.get_by_test_id("history-item").count() == 0
    assert page.get_by_test_id("assumption-item").count() == 0
    assert page.get_by_test_id("criteria-item").count() == 0
    assert page.get_by_test_id("trace-link").count() == 0
    assert page.get_by_test_id("telemetry-link").count() == 0


def test_bead_detail_live_update(ui):
    """Scenario: fleet-web-ui / Drill-down screens / Live bead detail"""
    page = goto(ui, route(f"beads/{DEV}"))
    items = page.get_by_test_id("history-item")
    expect(items.first).to_be_visible()
    n = items.count()
    page.evaluate("window.__marker = 11")
    demo.rewrite_bead_content(project_path(ui), DEV, "live note", clock=at(50))
    expect(items).to_have_count(n + 1)
    assert page.evaluate("window.__marker") == 11
    expect(page.get_by_test_id("bead-section-handoff")).to_contain_text("live note")


def test_drill_down_project_to_trace(ui):
    """Scenario: fleet-web-ui / Drill-down screens / Drill down from project to trace"""
    page = goto(ui, route(""))
    page.get_by_test_id("link-features").click()
    expect(page.get_by_test_id("screen-features")).to_be_visible()
    page.locator(f'[data-testid="feature-link"][href$="/{G}"]').click()
    expect(page.get_by_test_id("screen-feature-tree")).to_be_visible()
    page.get_by_test_id(f"bead-link-{DEV}").click()
    expect(page.get_by_test_id("bead-detail")).to_be_visible()
    page.get_by_test_id("trace-link").first.click()
    expect(page.get_by_test_id("screen-trace")).to_be_visible()
    assert page.evaluate("location.hash") == f"#/projects/{PROJ}/beads/{DEV}/traces/1"
    crumb = page.get_by_test_id("breadcrumb")
    expect(crumb.get_by_test_id("breadcrumb-bead")).to_be_visible()
    page.get_by_test_id("breadcrumb-project").click()
    expect(page.get_by_test_id("screen-project")).to_be_visible()


def test_activity_links_to_beads(ui):
    """Scenario: fleet-web-ui / Drill-down screens / Activity links to beads"""
    p = next(x for x in ui.server.fleet.projects if x.name == PROJ)
    demo.append_event(p.path, "bead_completed", {"bead_id": DEV, "summary": "with-bead"}, clock=at(20))
    demo.append_event(p.path, "scheduler_run_started", {"summary": "no-bead"}, clock=at(21))
    page = goto(ui, "activity")
    with_bead = page.locator('[data-testid="activity-item"]:has-text("with-bead")')
    expect(with_bead.get_by_test_id("activity-bead-link")).to_have_attribute(
        "href", f"#/projects/{PROJ}/beads/{DEV}")
    no_bead = page.locator('[data-testid="activity-item"]:has-text("no-bead")')
    expect(no_bead).to_have_count(1)
    expect(no_bead.get_by_test_id("activity-bead-link")).to_have_count(0)
    with_bead.get_by_test_id("activity-bead-link").click()
    expect(page.get_by_test_id("bead-detail")).to_be_visible()


# --- trace -------------------------------------------------------------------------

def write_big_trace(ui, total=450, errors=3):
    p = project_path(ui)
    d = p / ".takt" / "traces" / DEV
    lines = []
    for i in range(total):
        if i < errors:
            ev = {"kind": "tool_call", "ts": f"2026-01-01T00:00:{i % 60:02d}+00:00", "tool": "Bash",
                  "input": {"command": f"fail {i}"}, "is_error": True, "duration_ms": 5,
                  "result_excerpt": "boom"}
        elif i % 2:
            ev = {"kind": "tool_call", "ts": "2026-01-01T00:01:00+00:00", "tool": "Read",
                  "input": {"file_path": f"f{i}"}, "is_error": False, "duration_ms": 1}
        else:
            ev = {"kind": "assistant_text", "ts": "2026-01-01T00:02:00+00:00",
                  "text_excerpt": f"message {i}", "text_chars": 10}
        lines.append(json.dumps(ev))
    (d / "1.events.jsonl").write_text("\n".join(lines) + "\n")


def test_trace_paging_and_filters(ui):
    write_big_trace(ui)
    page = goto(ui, route(f"beads/{DEV}/traces/1"))
    events = page.get_by_test_id("trace-event")
    expect(events).to_have_count(200)
    expect(page.get_by_test_id("trace-count")).to_have_text("Showing 200 of 450")
    page.get_by_test_id("trace-load-more").click()
    expect(events).to_have_count(400)
    page.get_by_test_id("trace-load-more").click()
    expect(events).to_have_count(450)
    expect(page.get_by_test_id("trace-load-more")).to_have_count(0)
    page.get_by_test_id("trace-filter-errors").check()
    expect(events).to_have_count(3)
    expect(page.get_by_test_id("trace-error-marker")).to_have_count(3)
    expect(page.get_by_test_id("trace-count")).to_have_text("Showing 3 of 3")
    page.get_by_test_id("trace-filter-errors").uncheck()
    page.get_by_test_id("trace-filter-kind").select_option("assistant_text")
    expect(page.get_by_test_id("trace-count")).to_contain_text("of 223")
    page.get_by_test_id("trace-filter-kind").select_option("")
    page.get_by_test_id("trace-filter-tool").select_option("Read")
    expect(page.get_by_test_id("trace-count")).to_contain_text("of 224")
    # summary refresh keeps filters
    page.evaluate("window.__marker = 5")
    demo.rewrite_bead_content(project_path(ui), DEV, "touch", clock=at(60))
    page.wait_for_timeout(800)
    expect(page.get_by_test_id("trace-filter-tool")).to_have_value("Read")
    assert page.evaluate("window.__marker") == 5
    expect(page.get_by_test_id("trace-summary")).to_be_visible()


def test_trace_missing_status(ui):
    page = goto(ui, route(f"beads/{DEV}/traces/2"))
    expect(page.get_by_test_id("trace-meta")).to_be_visible()
    expect(page.get_by_test_id("trace-empty")).to_contain_text("not captured")
    assert page.get_by_test_id("trace-event").count() == 0


# --- telemetry ---------------------------------------------------------------------

def test_telemetry_large_prompt_and_nulls(ui):
    page = goto(ui, route(f"beads/{DEV}/telemetry/1"))
    expect(page.get_by_test_id("telemetry-prompt-truncated")).to_be_visible()
    expect(page.get_by_test_id("telemetry-prompt-text")).to_be_visible()
    expect(page.get_by_test_id("telemetry-response-truncated")).to_have_count(0)
    expect(page.get_by_test_id("telemetry-parsed-json")).to_contain_text("completed")
    expect(page.get_by_test_id("telemetry-metrics")).to_contain_text("demo-model")
    # nulls
    f = project_path(ui) / ".takt" / "telemetry" / DEV / "2.json"
    data = json.loads(f.read_text())
    data.update(prompt_text=None, response_text=None, parsed_result=None, metrics=None,
                error={"type": "Timeout", "message": "slow"})
    f.write_text(json.dumps(data))
    page = goto(ui, route(f"beads/{DEV}/telemetry/2"))
    expect(page.get_by_test_id("screen-telemetry")).to_be_visible()
    expect(page.get_by_test_id("telemetry-prompt-text")).to_have_count(0)
    expect(page.get_by_test_id("telemetry-error")).to_contain_text("Timeout")


# --- audit -------------------------------------------------------------------------

def test_audit_sections_and_extra(ui):
    older, newer = demo.AUDIT_TIMESTAMPS
    page = goto(ui, route(f"features/{G}/audits/{newer}"))
    sections = page.get_by_test_id("audit-section")
    expect(sections.first).to_be_visible()
    names = {sections.nth(i).get_attribute("data-section") for i in range(sections.count())}
    assert {"feature", "status", "beads", "gates", "assumptions", "criteria", "cost"} <= names
    assert "other" in names  # future_section
    expect(page.get_by_test_id("audit-extra").first).to_be_visible()
    expect(page.get_by_test_id("breadcrumb-feature")).to_be_visible()
    page.get_by_test_id("audit-back").click()
    expect(page.get_by_test_id("screen-feature-tree")).to_be_visible()
    # the snapshot without extras has no "other" section
    page = goto(ui, route(f"features/{G}/audits/{older}"))
    expect(page.get_by_test_id("audit-section").nth(5)).to_be_visible()
    names = {page.get_by_test_id("audit-section").nth(i).get_attribute("data-section")
             for i in range(page.get_by_test_id("audit-section").count())}
    assert "other" not in names and "cost" in names


def test_audit_odd_values_fall_back(ui):
    older = demo.AUDIT_TIMESTAMPS[0]
    f = project_path(ui) / ".takt" / "audits" / G / f"{older}.json"
    data = json.loads(f.read_text())
    data.update(criteria=None, trace=None, gates=[], merges=[], assumptions=[],
                status="weird", cost=["x"])
    f.write_text(json.dumps(data))
    page = goto(ui, route(f"features/{G}/audits/{older}"))
    expect(page.get_by_test_id("screen-audit")).to_be_visible()
    expect(page.get_by_test_id("audit-section").first).to_be_visible()


# --- Layer 3: screenshots ---------------------------------------------------------------

def test_explorer_screenshots(ui, tmp_path):
    """Render feature list, feature tree, bead detail, trace view and audit view from demo data."""
    newer = demo.AUDIT_TIMESTAMPS[1]
    shots = {
        "feature-list": (route("features"), "screen-features"),
        "feature-tree": (route(f"features/{G}"), "screen-feature-tree"),
        "bead-detail": (route(f"beads/{DEV}"), "bead-detail"),
        "trace-view": (route(f"beads/{DEV}/traces/1"), "screen-trace"),
        "audit-view": (route(f"features/{G}/audits/{newer}"), "screen-audit"),
    }
    page = goto(ui, shots["feature-list"][0])
    for name, (r, testid) in shots.items():
        set_hash(ui, r)
        expect(page.get_by_test_id(testid)).to_be_visible()
        page.wait_for_timeout(500)
        out = tmp_path / f"{name}.png"
        page.screenshot(path=str(out), full_page=True)
        assert out.stat().st_size > 2000
        if os.environ.get("TAKT_UI_SCREENSHOTS") == "1":
            SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(out, SCREENSHOT_DIR / f"{name}.png")
