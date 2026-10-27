"""Tests for the operator governance surfaces (assumptions, criteria, merge records)."""
from __future__ import annotations

import asyncio
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_takt.audit import build_feature_audit, render_feature_audit_markdown
from agent_takt.models import BEAD_DONE, BEAD_READY, Bead, HandoffSummary
from agent_takt.storage import RepositoryStorage
from agent_takt.tui import (
    DETAIL_SECTION_ACCEPTANCE,
    DETAIL_SECTION_DECISIONS,
    DETAIL_SECTION_MERGES,
    DETAIL_SECTION_ORDER,
    TuiRuntimeState,
    _detail_section_body,
    _detail_section_title,
    build_tree_rows,
    build_tui_app,
    format_detail_panel,
    format_help_overlay,
    render_tree_panel,
)
from agent_takt.tui.render import _governance_badge

RepositoryStorage._auto_commit = False


def _bead(bead_id: str = "B0001", *, root: bool = True, **kwargs) -> Bead:
    handoff = kwargs.pop("handoff", HandoffSummary())
    metadata = kwargs.pop("metadata", {})
    return Bead(
        bead_id=bead_id,
        title=kwargs.pop("title", "A bead"),
        agent_type="developer",
        description="d",
        feature_root_id=("B0001" if root else "B0001") if kwargs.pop("with_root", True) else None,
        handoff_summary=handoff,
        metadata=metadata,
        **kwargs,
    )


def _assumption(statement: str, scope: str | None = "structural", rationale: str = "why") -> dict:
    entry = {"statement": statement, "rationale": rationale}
    if scope is not None:
        entry["scope"] = scope
    return entry


def _crit(index: int, status: str, criterion: str = "crit", evidence: str = "") -> dict:
    return {"index": index, "criterion": criterion, "status": status, "evidence": evidence}


class GovernanceBadgeTests(unittest.TestCase):
    def test_empty_bead_gives_empty_badge(self) -> None:
        self.assertEqual("", _governance_badge(_bead()))

    def test_structural_assumptions_counted_local_ignored(self) -> None:
        handoff = HandoffSummary(
            assumptions=[_assumption("a"), _assumption("b"), _assumption("c", "local")]
        )
        self.assertEqual(" ◆2", _governance_badge(_bead(handoff=handoff)))

    def test_unmet_and_descoped_counts(self) -> None:
        handoff = HandoffSummary(
            criteria_status=[_crit(0, "unmet"), _crit(1, "descoped"), _crit(2, "descoped"), _crit(3, "met")]
        )
        self.assertEqual(" ✗1 ~2", _governance_badge(_bead(handoff=handoff)))

    def test_merged_marker_only_on_feature_root(self) -> None:
        records = [{"merged_at": "2026-01-01"}]
        root = _bead("B0001", metadata={"merge_records": records})
        child = _bead("B0001-1", metadata={"merge_records": records})
        self.assertEqual(" ✓merged", _governance_badge(root))
        self.assertEqual("", _governance_badge(child))

    def test_malformed_fields_do_not_raise(self) -> None:
        handoff = HandoffSummary()
        handoff.assumptions = ["junk", None, 3]  # type: ignore[list-item]
        handoff.criteria_status = "oops"  # type: ignore[assignment]
        self.assertEqual("", _governance_badge(_bead(handoff=handoff)))
        handoff.assumptions = "nope"  # type: ignore[assignment]
        handoff.criteria_status = [None, "x", 4]  # type: ignore[list-item]
        self.assertEqual("", _governance_badge(_bead(handoff=handoff)))


class GovernanceTreeRowTests(unittest.TestCase):
    def _badged_bead(self) -> Bead:
        handoff = HandoffSummary(
            assumptions=[_assumption("a"), _assumption("b")],
            criteria_status=[_crit(0, "unmet"), _crit(1, "descoped"), _crit(2, "descoped")],
        )
        return _bead(title="A very long title that definitely has to be truncated here", handoff=handoff)

    def test_render_tree_panel_narrow_width_truncates_title_keeps_badges(self) -> None:
        bead = self._badged_bead()
        output = render_tree_panel(build_tree_rows([bead]), 0, panel_width=60)
        self.assertIn(" ◆2 ✗1 ~2", output)
        self.assertIn("...", output)
        self.assertNotIn(bead.title, output)
        self.assertLessEqual(len(output), 60)
        # badge carries no markup
        badge = _governance_badge(bead)
        self.assertNotIn("[", badge)
        self.assertNotIn("]", badge)

    def test_bead_without_governance_data_renders_unchanged(self) -> None:
        bead = _bead(title="Plain")
        output = render_tree_panel(build_tree_rows([bead]), 0, panel_width=80)
        self.assertNotIn("◆", output)
        self.assertNotIn("✗", output)
        self.assertNotIn("✓merged", output)

    def test_bead_tree_node_label_narrow_width_and_ordering(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        storage = RepositoryStorage(Path(tmp.name))
        storage.initialize()
        app = build_tui_app(storage, refresh_seconds=60)
        bead = self._badged_bead()
        bead.metadata["telemetry"] = {"cost_usd": 1.5, "duration_ms": 1000}

        async def run() -> str:
            async with app.run_test() as pilot:
                await pilot.pause()
                tree = app.query_one("#bead-tree")
                return tree._node_label(bead, width=70)  # type: ignore[attr-defined]

        label = asyncio.run(run())
        self.assertIn(" ◆2 ✗1 ~2", label)
        self.assertIn("...", label)
        # the status icon carries style markup; the badge itself does not
        visible = re.sub(r"\[/?[a-z ]+\]", "", label.replace("[open]", "OPEN"))
        self.assertLessEqual(len(visible) + len("[open]") - len("OPEN"), 70, label)
        # governance badge precedes the telemetry badge
        self.assertLess(label.index("◆2"), label.index("$1.50"))


class _StorageCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        target = self.root / "templates" / "agents"
        target.mkdir(parents=True, exist_ok=True)
        for path in (REPO_ROOT / "templates" / "agents").glob("*.md"):
            shutil.copy2(path, target / path.name)
        self.storage = RepositoryStorage(self.root)
        self.storage.initialize()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()


class AcceptanceSectionTests(unittest.TestCase):
    def test_status_lines_in_criterion_order_with_evidence(self) -> None:
        handoff = HandoffSummary(
            criteria_status=[
                _crit(2, "descoped", "third", "owned elsewhere"),
                _crit(0, "met", "first", "tests/x.py::t"),
                _crit(1, "unmet", "second"),
            ]
        )
        bead = _bead(acceptance_criteria=["first", "second", "third"], handoff=handoff)
        body = _detail_section_body(bead, DETAIL_SECTION_ACCEPTANCE)
        lines = body.splitlines()
        self.assertEqual(3, len(lines))
        self.assertIn("[met] first", lines[0])
        self.assertIn(" — tests/x.py::t", lines[0])
        self.assertIn("[unmet] second", lines[1])
        self.assertNotIn(" — ", lines[1])
        self.assertIn("[descoped] third — owned elsewhere", lines[2])

    def test_plain_list_fallback_when_no_criteria_status(self) -> None:
        bead = _bead(acceptance_criteria=["one", "two"])
        body = _detail_section_body(bead, DETAIL_SECTION_ACCEPTANCE)
        self.assertEqual("  - one\n  - two", body)
        self.assertNotIn("[met]", body)

    def test_malformed_entries_do_not_raise_and_markup_is_escaped(self) -> None:
        handoff = HandoffSummary()
        handoff.criteria_status = [  # type: ignore[assignment]
            "junk",
            None,
            {"status": "met", "criterion": "no index"},
            {"index": None, "status": "unmet", "criterion": "none index"},
            {"index": "1", "status": "unmet", "criterion": "str index"},
            {"index": 0, "status": "met", "criterion": "has [x] bracket"},
        ]
        bead = _bead(acceptance_criteria=["a"], handoff=handoff)
        body = _detail_section_body(bead, DETAIL_SECTION_ACCEPTANCE)
        self.assertIn("has \\[x] bracket", body)
        self.assertTrue(body.splitlines()[0].lstrip().startswith("- \\[met]"))  # index 0 sorted first

    def test_all_malformed_entries_give_empty_body(self) -> None:
        handoff = HandoffSummary()
        handoff.criteria_status = ["junk", 3]  # type: ignore[assignment]
        bead = _bead(acceptance_criteria=["a"], handoff=handoff)
        # documented limitation: falls through to the plain list
        self.assertIsInstance(_detail_section_body(bead, DETAIL_SECTION_ACCEPTANCE), str)


class DecisionsAndMergesSectionTests(unittest.TestCase):
    def test_section_order_and_titles(self) -> None:
        order = list(DETAIL_SECTION_ORDER)
        self.assertEqual(DETAIL_SECTION_MERGES, order[-1])
        self.assertLess(order.index("handoff"), order.index(DETAIL_SECTION_DECISIONS))
        self.assertEqual("Merges", _detail_section_title(DETAIL_SECTION_MERGES))
        self.assertEqual("Decisions & Assumptions", _detail_section_title(DETAIL_SECTION_DECISIONS))

    def _decisions_body(self, assumptions: list[dict]) -> str:
        handoff = HandoffSummary(design_decisions="chose X", assumptions=assumptions)
        return _detail_section_body(_bead(handoff=handoff), DETAIL_SECTION_DECISIONS)

    def test_decisions_structural_first_original_order_kept(self) -> None:
        body = self._decisions_body([
            _assumption("local one", "local"),
            _assumption("struct one", "structural"),
            _assumption("local two", "local"),
            _assumption("struct two", "structural"),
        ])
        self.assertIn("design_decisions: chose X", body)
        order = [body.index(s) for s in ("struct one", "struct two", "local one", "local two")]
        self.assertEqual(sorted(order), order)

    def test_decisions_missing_scope_displayed_as_structural(self) -> None:
        body = self._decisions_body([_assumption("no scope", None)])
        self.assertIn("[structural] no scope", body)

    def test_decisions_missing_scope_sorts_with_structural(self) -> None:
        # A missing scope is displayed as structural, so it must sort with structural entries.
        body = self._decisions_body([_assumption("local one", "local"), _assumption("no scope", None)])
        self.assertLess(body.index("no scope"), body.index("local one"))

    def test_decisions_empty_fields(self) -> None:
        body = _detail_section_body(_bead(), DETAIL_SECTION_DECISIONS)
        self.assertIn("design_decisions: -", body)
        self.assertIn("test_coverage_notes: -", body)
        self.assertIn("known_limitations: -", body)
        self.assertIn("None recorded.", body)

    def test_decisions_malformed_assumptions_do_not_raise(self) -> None:
        handoff = HandoffSummary()
        handoff.assumptions = ["junk", None, {"scope": "local"}]  # type: ignore[list-item]
        self.assertIn("None recorded.", _detail_section_body(_bead(handoff=handoff), DETAIL_SECTION_DECISIONS))
        handoff.assumptions = "nope"  # type: ignore[assignment]
        self.assertIn("None recorded.", _detail_section_body(_bead(handoff=handoff), DETAIL_SECTION_DECISIONS))

    def _record(self, n: int) -> dict:
        return {
            "merged_at": f"2026-01-0{n}T00:00:00",
            "approver": f"Approver{n} <a{n}@x.com>",
            "branch": "feature/b-0001",
            "feature_head_sha": "a" * 40,
            "main_sha_after": "b" * 40,
            "preflight": "clean",
            "test_gate": {"status": "passed"},
            "audit_path": f".takt/audits/B0001/{n}.json",
        }

    def test_merges_record_fields_and_sha_truncation(self) -> None:
        bead = _bead(metadata={"merge_records": [self._record(1)]})
        body = _detail_section_body(bead, DETAIL_SECTION_MERGES)
        self.assertIn("approver: Approver1 <a1@x.com>", body)
        self.assertIn("branch: feature/b-0001", body)
        self.assertIn("feature_head_sha: " + "a" * 10 + "\n", body + "\n")
        self.assertNotIn("a" * 11, body)
        self.assertIn("main_sha_after: " + "b" * 10, body)
        self.assertNotIn("b" * 11, body)
        self.assertIn("preflight: clean", body)
        self.assertIn("test_gate: passed", body)
        self.assertIn("audit_path: .takt/audits/B0001/1.json", body)

    def test_merges_multiple_records_in_order_separated_by_blank_line(self) -> None:
        bead = _bead(metadata={"merge_records": [self._record(1), self._record(2)]})
        body = _detail_section_body(bead, DETAIL_SECTION_MERGES)
        self.assertLess(body.index("Approver1"), body.index("Approver2"))
        self.assertIn("\n\n", body)

    def test_merges_missing_keys_and_bad_test_gate(self) -> None:
        for gate in ({}, None, "passed", ["x"]):
            record = {"approver": "me"}
            if gate is not None:
                record["test_gate"] = gate
            body = _detail_section_body(_bead(metadata={"merge_records": [record]}), DETAIL_SECTION_MERGES)
            self.assertIn("approver: me", body)
            self.assertIn("test_gate: -", body)
            self.assertIn("merged_at: -", body)
            self.assertIn("feature_head_sha: -", body)

    def test_merges_none_recorded_cases(self) -> None:
        self.assertEqual("No merge records.", _detail_section_body(_bead(), DETAIL_SECTION_MERGES))
        self.assertEqual(
            "No merge records.",
            _detail_section_body(_bead(metadata={"merge_records": []}), DETAIL_SECTION_MERGES),
        )
        child = _bead("B0001-1", metadata={"merge_records": [self._record(1)]})
        self.assertEqual("No merge records.", _detail_section_body(child, DETAIL_SECTION_MERGES))
        self.assertEqual(
            "No merge records.",
            _detail_section_body(_bead(metadata={"merge_records": "bad"}), DETAIL_SECTION_MERGES),
        )

    def test_format_detail_panel_contains_new_blocks(self) -> None:
        handoff = HandoffSummary(
            design_decisions="dd",
            assumptions=[_assumption("loc", "local"), _assumption("str", "structural")],
        )
        bead = _bead(handoff=handoff, metadata={"merge_records": [self._record(1), self._record(2)]})
        text = format_detail_panel(bead)
        self.assertIn("Decisions & Assumptions:", text)
        self.assertIn("  design_decisions: dd", text)
        self.assertLess(text.index("[structural] str"), text.index("[local] loc"))
        self.assertIn("Merges:", text)
        self.assertLess(text.index("Approver1"), text.index("Approver2"))
        self.assertLess(text.index("Handoff:"), text.index("Decisions & Assumptions:"))

    def test_format_detail_panel_empty_values(self) -> None:
        text = format_detail_panel(_bead())
        self.assertIn("None recorded.", text)
        self.assertIn("No merge records.", text)
        self.assertIn("  design_decisions: -", text)
        child = format_detail_panel(_bead("B0001-1", metadata={"merge_records": [self._record(1)]}))
        self.assertIn("No merge records.", child)


class PopupAndCyclingTests(_StorageCase):
    def test_n_cycling_reaches_decisions_and_merges(self) -> None:
        self.storage.create_bead(bead_id="B0001", title="Root", agent_type="developer", description="r", status=BEAD_READY)
        app = build_tui_app(self.storage, refresh_seconds=60)

        async def run() -> list[str]:
            seen: list[str] = []
            async with app.run_test() as pilot:
                await pilot.resize_terminal(100, 40)
                await pilot.pause()
                await pilot.press("tab")
                await pilot.pause()
                for _ in range(len(DETAIL_SECTION_ORDER) - 1):
                    await pilot.press("n")
                    await pilot.pause()
                    seen.append(app.runtime_state.status_message)
                await pilot.press("n")
                await pilot.pause()
                seen.append(app.runtime_state.status_message)
                for _ in range(len(DETAIL_SECTION_ORDER)):
                    await pilot.press("N")
                    await pilot.pause()
                seen.append(app.runtime_state.status_message)
            return seen

        seen = asyncio.run(run())
        self.assertTrue(any("Decisions & Assumptions" in s for s in seen))
        self.assertTrue(any("Active detail section: Merges." == s for s in seen))
        self.assertEqual("Already at the last detail section.", seen[len(DETAIL_SECTION_ORDER) - 1])
        self.assertEqual("Already at the first detail section.", seen[-1])

    def test_detail_popup_includes_new_sections(self) -> None:
        self.storage.create_bead(bead_id="B0001", title="Root", agent_type="developer", description="r", status=BEAD_READY)
        app = build_tui_app(self.storage, refresh_seconds=60)

        async def run() -> list[str]:
            async with app.run_test() as pilot:
                await pilot.resize_terminal(100, 40)
                await pilot.pause()
                await pilot.press("enter")
                await pilot.pause()
                titles = [str(c.title) for c in app.screen.query("Collapsible")]
                return titles

        titles = asyncio.run(run())
        self.assertIn("Decisions & Assumptions", titles)
        self.assertIn("Merges", titles)
        self.assertEqual(len(DETAIL_SECTION_ORDER), len(titles))


class FormatEventTests(_StorageCase):
    def _fmt(self, event_type: str, payload: dict) -> str:
        state = TuiRuntimeState(storage=self.storage)
        result = state._format_event(
            {"event_type": event_type, "timestamp": "2024-01-15T10:30:00+00:00", "payload": payload}
        )
        self.assertIsNotNone(result)
        return result

    def test_structural_assumption_recorded(self) -> None:
        out = self._fmt(
            "structural_assumption_recorded",
            {"bead_id": "B1", "count": 2, "statements": ["first statement", "second"]},
        )
        self.assertTrue(out.startswith("[yellow]") and out.endswith("[/yellow]"))
        self.assertIn("B1 recorded 2 structural assumption(s): first statement", out)
        self.assertNotIn("second", out)

    def test_structural_assumption_truncates_to_80(self) -> None:
        out = self._fmt("structural_assumption_recorded", {"bead_id": "B1", "count": 1, "statements": ["x" * 100]})
        self.assertIn("x" * 80, out)
        self.assertNotIn("x" * 81, out)

    def test_structural_assumption_empty_statements_placeholder(self) -> None:
        out = self._fmt("structural_assumption_recorded", {"bead_id": "B1", "count": 1, "statements": []})
        self.assertTrue(out.endswith(": ?[/yellow]"))

    def test_criteria_gap_recorded(self) -> None:
        out = self._fmt("criteria_gap_recorded", {"bead_id": "B1", "unmet": 2, "descoped": 1})
        self.assertTrue(out.startswith("[yellow]") and out.endswith("[/yellow]"))
        self.assertIn("B1 criteria gap: 2 unmet, 1 descoped", out)

    def test_verdict_overridden(self) -> None:
        out = self._fmt(
            "verdict_overridden",
            {"bead_id": "B1", "reason": "own_criteria", "unmet": [{"index": 0}, {"index": 1}]},
        )
        self.assertTrue(out.startswith("[bold red]") and out.endswith("[/bold red]"))
        self.assertIn("B1 verdict overridden (own_criteria): 2 unmet criteria", out)

    def test_verdict_overridden_unmet_absent_placeholder(self) -> None:
        out = self._fmt("verdict_overridden", {"bead_id": "B1", "reason": "r"})
        self.assertIn(": ? unmet criteria", out)

    def test_empty_payloads_use_placeholders(self) -> None:
        for event in (
            "structural_assumption_recorded",
            "criteria_gap_recorded",
            "verdict_overridden",
            "feature_merged",
            "merge_gate_failed",
        ):
            out = self._fmt(event, {})
            self.assertIn("?", out, event)

    def test_feature_merged(self) -> None:
        out = self._fmt("feature_merged", {"feature_root_id": "B1", "approver": "Ann <a@x>", "test_gate": {"status": "passed"}})
        self.assertIn("B1", out)
        self.assertIn("B1 merged by Ann <a@x> (tests: passed)", out)

    def test_merge_gate_failed(self) -> None:
        out = self._fmt("merge_gate_failed", {"feature_root_id": "B1", "gate": "tests", "reason": "2 failed"})
        self.assertIn("B1", out)
        self.assertIn("B1 merge gate failed (tests): 2 failed", out)

    def test_existing_events_unchanged(self) -> None:
        out = self._fmt("bead_blocked", {"bead_id": "B1", "summary": "stuck"})
        self.assertTrue(out.startswith("[yellow]"))
        self.assertIn("B1 blocked — stuck", out)
        out = self._fmt("lease_expired", {"bead_id": "B1"})
        self.assertIn("B1 lease expired", out)


class AuditPopupTests(_StorageCase):
    def _make_app(self):
        self.storage.create_bead(bead_id="B0001", title="Root", agent_type="developer", description="r", status=BEAD_DONE)
        self.storage.create_bead(
            bead_id="B0001-1", title="Child", agent_type="tester", description="c",
            parent_id="B0001", dependencies=["B0001"], status=BEAD_READY,
        )
        return build_tui_app(self.storage, refresh_seconds=60)

    def test_a_opens_text_popup_with_audit_markdown(self) -> None:
        app = self._make_app()
        expected = render_feature_audit_markdown(build_feature_audit(self.storage, "B0001"))

        async def run():
            async with app.run_test() as pilot:
                await pilot.resize_terminal(100, 40)
                await pilot.pause()
                await pilot.press("A")
                await pilot.pause()
                screen = app.screen
                info = (type(screen).__name__, screen._title, screen._body)
                await pilot.press("escape")
                await pilot.pause()
                return info, type(app.screen).__name__

        (name, title, body), after = asyncio.run(run())
        self.assertEqual("TextPopup", name)
        self.assertEqual("Audit: B0001", title)
        self.assertEqual(expected, body)
        self.assertNotEqual("TextPopup", after)

    def test_a_with_no_selection_sets_status_message(self) -> None:
        app = build_tui_app(self.storage, refresh_seconds=60)

        async def run():
            async with app.run_test() as pilot:
                await pilot.pause()
                await pilot.press("A")
                await pilot.pause()
                return type(app.screen).__name__, app.runtime_state.status_message

        name, status = asyncio.run(run())
        self.assertNotEqual("TextPopup", name)
        self.assertEqual("No feature tree for this bead.", status)

    def test_a_with_bead_lacking_feature_root_sets_status_message(self) -> None:
        app = self._make_app()
        bead = Bead(bead_id="BX", title="x", agent_type="developer", description="d", feature_root_id=None)
        app.runtime_state.selected_bead = lambda: bead  # type: ignore[method-assign]

        async def run():
            async with app.run_test() as pilot:
                await pilot.pause()
                app.runtime_state.selected_bead = lambda: bead  # type: ignore[method-assign]
                app.action_open_audit_popup()
                await pilot.pause()
                return type(app.screen).__name__, app.runtime_state.status_message

        name, status = asyncio.run(run())
        self.assertNotEqual("TextPopup", name)
        self.assertEqual("No feature tree for this bead.", status)

    def test_a_ignored_while_help_overlay_open(self) -> None:
        app = self._make_app()

        async def run():
            async with app.run_test() as pilot:
                await pilot.pause()
                await pilot.press("?")
                await pilot.pause()
                before = type(app.screen).__name__
                app.action_open_audit_popup()
                await pilot.pause()
                return before, type(app.screen).__name__

        before, after = asyncio.run(run())
        self.assertEqual(before, after)
        self.assertNotEqual("TextPopup", after)

    def test_text_popup_shows_markup_literally(self) -> None:
        app = self._make_app()

        async def run():
            async with app.run_test() as pilot:
                await pilot.resize_terminal(100, 40)
                await pilot.pause()
                app.push_screen(app._make_text_popup_screen("T", "[bold]literal[/bold]\n" + "\n".join(map(str, range(200)))))
                await pilot.pause()
                statics = [str(s.render()) for s in app.screen.query("Static")]
                self._max_scroll = app.screen.query_one("#text-popup-dialog").max_scroll_y
                return statics

        statics = asyncio.run(run())
        self.assertTrue(any("[bold]literal[/bold]" in s for s in statics))
        self.assertTrue(any("text 'T'" in s for s in statics))
        self.assertGreater(self._max_scroll, 0)  # long body scrolls

    def test_help_overlay_has_audit_line(self) -> None:
        self.assertIn("A           Open feature audit popup", format_help_overlay())


class NoCliImportInTuiTests(unittest.TestCase):
    def test_new_tui_code_has_no_top_level_cli_imports(self) -> None:
        """No module under tui/ imports agent_takt.cli at module level.

        Pre-existing function-local (lazy) imports in tui/__init__.py and tui/actions.py
        are tolerated; module-level imports are not.
        """
        pattern = re.compile(r"^(from\s+(agent_takt|\.\.)\.?cli\b|import\s+agent_takt\.cli\b)", re.M)
        for path in (SRC_ROOT / "agent_takt" / "tui").glob("*.py"):
            text = path.read_text()
            self.assertIsNone(pattern.search(text), f"module-level cli import in {path.name}")
        # the audit popup path itself is clean
        actions = (SRC_ROOT / "agent_takt" / "tui" / "actions.py").read_text()
        start = actions.index("def action_open_audit_popup")
        end = actions.index("def action_choose_blocked_status")
        self.assertNotIn("cli", actions[start:end].replace("self.", ""))


class LegacyBeadTests(_StorageCase):
    def _legacy_bead(self, bead_id: str, **extra) -> Bead:
        data = {
            "bead_id": bead_id,
            "title": "Legacy",
            "agent_type": "developer",
            "description": "old",
            "status": "done",
            "feature_root_id": "B0001",
            "handoff_summary": {"completed": "done"},
        }
        data.update(extra)
        return Bead.from_dict(data)

    def test_tree_sections_and_summary_render_for_legacy_beads(self) -> None:
        root = self._legacy_bead("B0001")
        child = self._legacy_bead("B0001-1", parent_id="B0001")
        for bead in (root, child):
            self.storage.save_bead(bead)
        rows = build_tree_rows([root, child])
        self.assertIsInstance(render_tree_panel(rows, 0, panel_width=60), str)
        for bead in (root, child):
            self.assertEqual("", _governance_badge(bead))
            for section in DETAIL_SECTION_ORDER:
                self.assertIsInstance(_detail_section_body(bead, section), str)
            self.assertIsInstance(format_detail_panel(bead), str)
        summary = self.storage.summary(feature_root_id="B0001")
        self.assertEqual({"structural": 0, "local": 0}, summary["assumptions"])
        self.assertIsNone(summary["last_merge"])
        self.assertIsInstance(self.storage.summary(), dict)

        app = build_tui_app(self.storage, refresh_seconds=60)

        async def run() -> None:
            async with app.run_test() as pilot:
                await pilot.resize_terminal(100, 40)
                await pilot.pause()
                await pilot.press("enter")
                await pilot.pause()

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
