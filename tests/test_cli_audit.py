"""Tests for ``takt audit`` (cli/commands/audit.py) and the report builders in audit.py.

Covers:
- markdown report: nine sections in order, identical for root / child / unique prefix
- --json output: schema version and nine section keys
- --output FILE writes the report and prints nothing to stdout
- read-only guarantee: bead files and events.jsonl are untouched
- null criteria, zero assumptions, populated counts/listings, structural-before-local ordering
- gates: one prior needs_changes before approved on a review bead
- unknown / ambiguous prefixes and unwritable output paths exit non-zero
"""
from __future__ import annotations

import contextlib
import io
import json
import re
import sys
from argparse import Namespace
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_takt.audit import build_feature_audit, render_feature_audit_markdown
from agent_takt.cli.commands.audit import command_audit
from agent_takt.cli.parser import build_parser
from agent_takt.console import ConsoleReporter
from agent_takt.models import BEAD_DONE, BEAD_READY, ExecutionRecord
from agent_takt.storage import RepositoryStorage

RepositoryStorage._auto_commit = False

from helpers import OrchestratorTests as _OrchestratorBase  # noqa: E402

SECTION_KEYS = [
    "feature", "status", "beads", "gates", "assumptions",
    "criteria", "changed_files", "cost", "merges",
]
SECTION_HEADINGS = [
    "## Feature", "## Status", "## Beads", "## Gates", "## Assumptions",
    "## Criteria", "## Changed files", "## Cost", "## Merges",
]


def _strip_generated_at(text: str) -> str:
    """The report timestamp has second precision; drop it before comparing runs."""
    text = re.sub(r"^- Generated at: .*$", "- Generated at: X", text, flags=re.MULTILINE)
    return re.sub(r'"generated_at": "[^"]*"', '"generated_at": "X"', text)


class AuditTestBase(_OrchestratorBase):
    def setUp(self) -> None:
        super().setUp()
        self.err = io.StringIO()
        self.console = ConsoleReporter(stream=self.err)

    def run_audit(self, bead_id: str, *, json_out: bool = False, output: str | None = None) -> tuple[int, str]:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = command_audit(Namespace(bead_id=bead_id, json=json_out, output=output), self.storage, self.console)
        return code, buf.getvalue()

    def add_history(self, bead, event: str) -> None:
        bead.execution_history.append(
            ExecutionRecord(timestamp="2026-01-01T00:00:00+00:00", event=event, agent_type=bead.agent_type, summary=event)
        )

    def make_plain_tree(self):
        """Feature tree with no assumptions, criteria or verdict data at all."""
        root = self.storage.create_bead(title="Plain feature", agent_type="developer", description="root", status=BEAD_DONE)
        child = self.storage.create_bead(
            title="Plain child", agent_type="developer", description="child",
            parent_id=root.bead_id, status=BEAD_DONE,
        )
        return root, child

    def make_rich_tree(self):
        root = self.storage.create_bead(
            title="Rich feature", agent_type="developer", description="root", status=BEAD_DONE,
            linked_docs=["specs/x.md"],
        )
        dev = self.storage.create_bead(
            title="Dev", agent_type="developer", description="dev", parent_id=root.bead_id, status=BEAD_DONE,
            acceptance_criteria=["a", "b", "c"], changed_files=["src/b.py", "src/a.py"],
        )
        dev.handoff_summary.assumptions = [
            {"statement": "local one", "rationale": "r1", "scope": "local"},
            {"statement": "structural one", "rationale": "r2", "scope": "structural"},
            "not-a-dict",
        ]
        dev.handoff_summary.criteria_status = [
            {"index": 0, "criterion": "a", "status": "met", "evidence": "e"},
            {"index": 1, "criterion": "b", "status": "unmet", "evidence": "missing"},
            {"index": 2, "criterion": "c", "status": "descoped", "evidence": "other bead"},
        ]
        self.storage.save_bead(dev)
        review = self.storage.create_bead(
            title="Review", agent_type="review", description="rev", parent_id=root.bead_id,
            status=BEAD_DONE, dependencies=[dev.bead_id],
        )
        self.add_history(review, "blocked")
        self.add_history(review, "completed")
        review.handoff_summary.verdict = "approved"
        self.storage.save_bead(review)
        return root, dev, review


class TestAuditMarkdown(AuditTestBase):
    def test_nine_sections_in_order(self) -> None:
        root, _ = self.make_plain_tree()
        code, out = self.run_audit(root.bead_id)
        self.assertEqual(0, code)
        positions = [out.index(h) for h in SECTION_HEADINGS]
        self.assertEqual(sorted(positions), positions)
        self.assertEqual(SECTION_HEADINGS, [ln for ln in out.splitlines() if ln.startswith("## ")])

    def test_same_report_for_root_child_and_prefix(self) -> None:
        root, child = self.make_plain_tree()
        _, from_root = self.run_audit(root.bead_id)
        _, from_child = self.run_audit(child.bead_id)
        _, from_prefix = self.run_audit(root.bead_id[:6])
        self.assertEqual(_strip_generated_at(from_root), _strip_generated_at(from_child))
        self.assertEqual(_strip_generated_at(from_root), _strip_generated_at(from_prefix))
        self.assertIn(root.bead_id, from_root)
        self.assertIn(child.bead_id, from_root)

    def test_null_criteria_and_zero_assumptions_fallbacks(self) -> None:
        root, _ = self.make_plain_tree()
        _, out = self.run_audit(root.bead_id)
        self.assertIn("## Criteria\n\nNot recorded.", out)
        self.assertIn("## Assumptions\n\nNone recorded.", out)
        self.assertIn("No merge records.", out)

    def test_populated_criteria_assumptions_and_files(self) -> None:
        root, dev, _ = self.make_rich_tree()
        _, out = self.run_audit(root.bead_id)
        self.assertIn("Met: 1, unmet: 1, descoped: 1", out)
        self.assertIn(f"{dev.bead_id} #1 [unmet] b — missing", out)
        self.assertIn(f"{dev.bead_id} #2 [descoped] c — other bead", out)
        self.assertIn("Structural: 1, local: 1", out)
        # sorted changed files
        self.assertLess(out.index("- src/a.py"), out.index("- src/b.py"))
        self.assertIn("specs/x.md", out)

    def test_structural_assumptions_listed_before_local(self) -> None:
        root, _, _ = self.make_rich_tree()
        _, out = self.run_audit(root.bead_id)
        self.assertLess(out.index("structural one"), out.index("local one"))
        data = json.loads(self.run_audit(root.bead_id, json_out=True)[1])
        scopes = [i["scope"] for i in data["assumptions"]["items"]]
        self.assertEqual(["structural", "local"], scopes)
        self.assertEqual({"structural": 1, "local": 1}, data["assumptions"]["counts"])  # non-dict ignored

    def test_gates_count_one_prior_needs_changes_before_approved(self) -> None:
        root, _, review = self.make_rich_tree()
        data = json.loads(self.run_audit(root.bead_id, json_out=True)[1])
        gate = next(g for g in data["gates"] if g["bead_id"] == review.bead_id)
        self.assertEqual("approved", gate["final_verdict"])
        self.assertEqual(1, gate["prior_needs_changes"])
        _, out = self.run_audit(root.bead_id)
        self.assertIn("final verdict approved, 1 prior needs_changes", out)

    def test_final_needs_changes_not_counted_as_prior(self) -> None:
        root, _, review = self.make_rich_tree()
        review = self.storage.load_bead(review.bead_id)
        review.execution_history.clear()
        self.add_history(review, "blocked")
        review.handoff_summary.verdict = "needs_changes"
        self.storage.save_bead(review)
        data = build_feature_audit(self.storage, root.bead_id)
        gate = next(g for g in data["gates"] if g["bead_id"] == review.bead_id)
        self.assertEqual(0, gate["prior_needs_changes"])

    def test_verdict_overridden_records_listed(self) -> None:
        root, _, review = self.make_rich_tree()
        review = self.storage.load_bead(review.bead_id)
        self.add_history(review, "verdict_overridden")
        self.storage.save_bead(review)
        data = build_feature_audit(self.storage, root.bead_id)
        gate = next(g for g in data["gates"] if g["bead_id"] == review.bead_id)
        self.assertEqual(1, len(gate["verdict_overridden"]))

    def test_legacy_bead_without_data_does_not_crash(self) -> None:
        root = self.storage.create_bead(title="Legacy", agent_type="review", description="x", status=BEAD_READY)
        audit = build_feature_audit(self.storage, root.bead_id)
        self.assertIsNone(audit["criteria"])
        self.assertEqual([], audit["merges"])
        out = render_feature_audit_markdown(audit)
        self.assertIn("Not done:", out)
        self.assertIn(root.bead_id, out)


class TestAuditLinkedDocs(AuditTestBase):
    def _root(self, linked: str):
        return self.storage.create_bead(
            title="Docs feature", agent_type="developer", description="root",
            status=BEAD_DONE, linked_docs=[linked],
        )

    def test_moved_doc_shows_current_path_and_recorded_note(self) -> None:
        done = self.storage.root / "specs" / "done"
        done.mkdir(parents=True)
        (done / "my-spec.md").write_text("x", encoding="utf-8")
        root = self._root("specs/drafts/my-spec.md")
        _, out = self.run_audit(root.bead_id)
        self.assertIn("- Linked docs: specs/done/my-spec.md (recorded as specs/drafts/my-spec.md)", out)
        _, js = self.run_audit(root.bead_id, json_out=True)
        self.assertEqual(
            [{"recorded": "specs/drafts/my-spec.md", "current": "specs/done/my-spec.md"}],
            json.loads(js)["feature"]["linked_docs"],
        )

    def test_unresolvable_doc_shows_not_found(self) -> None:
        root = self._root("specs/drafts/gone.md")
        _, out = self.run_audit(root.bead_id)
        self.assertIn("- Linked docs: specs/drafts/gone.md (not found)", out)
        _, js = self.run_audit(root.bead_id, json_out=True)
        self.assertEqual(
            [{"recorded": "specs/drafts/gone.md", "current": None}],
            json.loads(js)["feature"]["linked_docs"],
        )

    def test_unmoved_doc_renders_plain(self) -> None:
        (self.storage.root / "specs").mkdir()
        (self.storage.root / "specs" / "x.md").write_text("x", encoding="utf-8")
        root = self._root("specs/x.md")
        _, out = self.run_audit(root.bead_id)
        self.assertIn("- Linked docs: specs/x.md\n", out)


class TestAuditJsonAndOutput(AuditTestBase):
    def test_json_has_schema_version_and_nine_keys(self) -> None:
        root, _ = self.make_plain_tree()
        code, out = self.run_audit(root.bead_id, json_out=True)
        self.assertEqual(0, code)
        data = json.loads(out)
        self.assertEqual(1, data["audit_schema_version"])
        self.assertEqual(SECTION_KEYS, [k for k in data if k != "audit_schema_version"])
        self.assertIsNone(data["criteria"])
        self.assertEqual([], data["merges"])
        self.assertEqual(0, data["assumptions"]["counts"]["structural"])
        self.assertEqual([], data["assumptions"]["items"])

    def test_json_null_cost_without_telemetry(self) -> None:
        root, _ = self.make_plain_tree()
        data = json.loads(self.run_audit(root.bead_id, json_out=True)[1])
        self.assertIsNone(data["cost"]["total_cost_usd"])
        _, md = self.run_audit(root.bead_id)
        self.assertIn("Total cost: not recorded", md)

    def test_json_from_child_equals_root(self) -> None:
        root, child = self.make_plain_tree()
        a = json.loads(_strip_generated_at(self.run_audit(root.bead_id, json_out=True)[1]))
        b = json.loads(_strip_generated_at(self.run_audit(child.bead_id, json_out=True)[1]))
        self.assertEqual(a, b)

    def test_output_file_markdown_prints_nothing(self) -> None:
        root, _ = self.make_plain_tree()
        target = self.root / "report.md"
        code, out = self.run_audit(root.bead_id, output=str(target))
        self.assertEqual(0, code)
        self.assertEqual("", out)
        text = target.read_text(encoding="utf-8")
        for heading in SECTION_HEADINGS:
            self.assertIn(heading, text)

    def test_output_file_json_prints_nothing(self) -> None:
        root, _ = self.make_plain_tree()
        target = self.root / "report.json"
        code, out = self.run_audit(root.bead_id, json_out=True, output=str(target))
        self.assertEqual(0, code)
        self.assertEqual("", out)
        self.assertEqual(1, json.loads(target.read_text(encoding="utf-8"))["audit_schema_version"])

    def test_unwritable_output_exits_one(self) -> None:
        root, _ = self.make_plain_tree()
        code, out = self.run_audit(root.bead_id, output=str(self.root / "no-such-dir" / "r.md"))
        self.assertEqual(1, code)
        self.assertEqual("", out)
        self.assertIn("cannot write", self.err.getvalue())

    def test_unknown_prefix_exits_nonzero(self) -> None:
        self.make_plain_tree()
        code, out = self.run_audit("B-zzzzzzzz")
        self.assertNotEqual(0, code)
        self.assertEqual("", out)

    def test_ambiguous_prefix_exits_nonzero(self) -> None:
        self.storage.create_bead(title="One", agent_type="developer", description="x", bead_id="B-aaaa0001")
        self.storage.create_bead(title="Two", agent_type="developer", description="x", bead_id="B-aaaa0002")
        code, out = self.run_audit("B-aaaa")
        self.assertNotEqual(0, code)
        self.assertEqual("", out)

    def test_parser_accepts_audit_flags(self) -> None:
        args = build_parser().parse_args(["audit", "B-abc", "--json", "--output", "f.json"])
        self.assertEqual(("B-abc", True, "f.json"), (args.bead_id, args.json, args.output))


class TestAuditReadOnly(AuditTestBase):
    def _snapshot(self) -> dict[str, bytes]:
        files = {p.name: p.read_bytes() for p in self.storage.beads_dir.glob("*.json")}
        events = self.storage.logs_dir / "events.jsonl"
        files["events.jsonl"] = events.read_bytes() if events.exists() else b"<absent>"
        return files

    def test_audit_does_not_modify_beads_or_events(self) -> None:
        root, _, _ = self.make_rich_tree()
        self.storage.record_event("seed", {"x": 1})
        before = self._snapshot()
        for kwargs in ({}, {"json_out": True}, {"output": str(self.root / "o.md")}):
            self.run_audit(root.bead_id, **kwargs)
        self.assertEqual(before, self._snapshot())

    def test_audit_does_not_create_events_file(self) -> None:
        root, _ = self.make_plain_tree()
        events = self.storage.logs_dir / "events.jsonl"
        self.assertFalse(events.exists())
        self.run_audit(root.bead_id)
        self.assertFalse(events.exists())


class TestAuditMergeRecords(AuditTestBase):
    def test_merges_returned_in_stored_order_and_rendered(self) -> None:
        root, _ = self.make_plain_tree()
        root = self.storage.load_bead(root.bead_id)
        root.metadata["merge_records"] = [
            {"merged_at": "t1", "approver": "A", "approver_source": "flag", "test_gate": {"status": "passed"}},
            {"merged_at": "t2", "approver": "B", "approver_source": "git-config", "test_gate": {"status": "skipped"}},
        ]
        self.storage.save_bead(root)
        data = json.loads(self.run_audit(root.bead_id, json_out=True)[1])
        self.assertEqual(["t1", "t2"], [m["merged_at"] for m in data["merges"]])
        _, out = self.run_audit(root.bead_id)
        self.assertLess(out.index("t1 by A"), out.index("t2 by B"))


if __name__ == "__main__":
    import unittest

    unittest.main()
