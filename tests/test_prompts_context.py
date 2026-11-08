"""Prompt context blocks: linked docs, ADR index/text, planner prompt, review rule, size budget."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_takt.documents.registry import get_store
from agent_takt.models import Bead
from agent_takt.prompts import (
    build_planner_prompt,
    build_recovery_prompt,
    build_worker_prompt,
    render_approved_adr_index,
)
from agent_takt.storage import RepositoryStorage

RepositoryStorage._auto_commit = False


def _adr_body(decision: str) -> str:
    return (
        "## Summary\n\nWe chose a thing for good reasons.\n\n"
        "## Context\n\nThe system needed a durable store.\n\n"
        "## Considered Options\n\n### Option A\n\nUse a file.\n\n### Option B\n\nUse a database.\n\n"
        f"## Decision\n\n{decision}\n\n"
        "## Consequences\n\n### Positive\n\n* Simple\n\n### Negative\n\n* Limited\n"
    )


def _approved_adr(root: Path, title: str, description: str = "", decision: str = "We use option B.",
                  accepted_at: str | None = None):
    store = get_store(root, "adr")
    doc = store.create(title, description=description) if description else store.create(title)
    text = doc.path.read_text(encoding="utf-8")
    head, sep, _ = text.partition("\n---\n")
    doc.path.write_text(head + sep + "\n" + _adr_body(decision), encoding="utf-8")
    doc = store.set_status(doc.id, "approved")
    if accepted_at:
        store.set_field(doc.id, "accepted_at", accepted_at)
    return doc


def _bead(agent_type="developer", linked_docs=None, **kw) -> Bead:
    return Bead(
        bead_id="B-test0001", title="t", agent_type=agent_type, description="d",
        linked_docs=linked_docs or [], **kw,
    )


class PromptBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name).resolve()
        self.ws = base / "ws"
        self.main = base / "main"
        self.ws.mkdir()
        self.main.mkdir()
        # guardrail templates for build_worker_prompt
        (self.ws / "templates" / "agents").mkdir(parents=True)
        for f in (REPO_ROOT / "templates" / "agents").glob("*.md"):
            (self.ws / "templates" / "agents" / f.name).write_bytes(f.read_bytes())

    def worker(self, bead):
        return build_worker_prompt(bead, [], self.ws, workspace_root=self.ws, main_root=self.main)


class WorkerContextTests(PromptBase):
    def test_worker_sees_adr_index(self):
        """Scenario: agent-context / Approved ADRs pushed to agents / Worker sees the ADR index"""
        adr = _approved_adr(self.main, "Use SQLite", "Persist memory in SQLite")
        draft = get_store(self.main, "adr").create("Draft idea")
        prompt = self.worker(_bead())
        self.assertIn("Approved architecture decisions", prompt)
        self.assertIn(adr.id, prompt)
        self.assertIn("Use SQLite", prompt)
        self.assertNotIn(draft.id, prompt)
        self.assertIn("adr search", prompt)

    def test_linked_adr_text_included(self):
        """Scenario: agent-context / Approved ADRs pushed to agents / Linked ADR text is included"""
        adr = _approved_adr(self.main, "Use SQLite", "d")
        store = get_store(self.main, "adr")
        text = "We use option B."
        prompt = self.worker(_bead(linked_docs=[f"adr:{adr.id}"]))
        self.assertIn("Linked ADRs (approved, binding)", prompt)
        self.assertIn(f"### {adr.id}", prompt)
        self.assertIn(text, prompt)

    def test_linked_adr_text_limit_and_non_approved(self):
        adrs = [_approved_adr(self.main, f"Decision {i}", "d") for i in range(4)]
        draft = get_store(self.main, "adr").create("Drafty")
        refs = [f"adr:{a.id}" for a in adrs] + [f"adr:{draft.id}"]
        prompt = self.worker(_bead(linked_docs=refs))
        self.assertEqual(3, prompt.count("### ADR-"))
        self.assertIn("Further linked ADRs", prompt)
        self.assertNotIn(f"### {draft.id}", prompt)
        self.assertNotIn(f"### {adrs[3].id}", prompt)

    def test_no_adrs_omits_blocks(self):
        prompt = self.worker(_bead())
        self.assertNotIn("Approved architecture decisions", prompt)
        self.assertNotIn("Linked ADRs", prompt)
        self.assertNotIn("Linked documents:", prompt)

    def test_superseded_excluded_from_index(self):
        a = _approved_adr(self.main, "Old", "d")
        b = _approved_adr(self.main, "New", "d")
        get_store(self.main, "adr").set_status(a.id, "superseded")
        index = render_approved_adr_index(self.main)
        self.assertNotIn(a.id, index)
        self.assertIn(b.id, index)

    def test_lookup_block_for_non_recovery_types(self):
        for t in ("developer", "tester", "review", "documentation", "defect"):
            self.assertIn("Looking things up", self.worker(_bead(agent_type=t)), t)

    def test_recovery_prompt_omits_context_blocks(self):
        """Scenario: agent-context / Approved ADRs pushed to agents / Recovery prompt omits context blocks"""
        adr = _approved_adr(self.main, "Use SQLite", "d")
        bead = _bead(agent_type="recovery", linked_docs=[f"adr:{adr.id}"], bead_type="recovery")
        prompt = self.worker(bead)
        for needle in ("Linked documents:", "Linked ADRs", "Approved architecture decisions", "Looking things up"):
            self.assertNotIn(needle, prompt)
        original = _bead(linked_docs=[f"adr:{adr.id}"])
        rp = build_recovery_prompt(original, "prose", "diff")
        self.assertNotIn("Approved architecture decisions", rp)
        self.assertNotIn("Looking things up", rp)

    def test_linked_documents_block_statuses(self):
        spec = get_store(self.ws, "spec").create("Alpha feature")
        adr_main = _approved_adr(self.main, "Main only", "d")
        stem = spec.path.stem
        prompt = self.worker(_bead(linked_docs=[
            f"spec:{spec.id}", f"specs/drafts/{stem}.md", f"adr:{adr_main.id}", "spec:spec-deadbeef",
        ]))
        self.assertIn(f"- {spec.id}  Alpha feature [{spec.status}]", prompt)
        self.assertIn("(from main checkout)", prompt)
        self.assertIn("spec:spec-deadbeef  (not found)", prompt)


class IndexCapTests(PromptBase):
    def test_index_cap_60_latest_kept_sorted_with_overflow(self):
        """ADR index cap at 60 (latest accepted_at kept, sorted by ID, overflow line)"""
        docs = []
        for i in range(63):
            docs.append(_approved_adr(self.main, f"Decision number {i}", "d",
                                      accepted_at=f"2026-01-01T00:{i // 60:02d}:{i % 60:02d}+00:00"))
        index = render_approved_adr_index(self.main)
        lines = index.splitlines()
        entries = [l for l in lines if l.startswith("ADR-")]
        self.assertEqual(60, len(entries))
        self.assertEqual(sorted(entries), entries)
        self.assertEqual("… and 3 more: $TAKT_CMD adr list --status approved", lines[-1])
        kept_ids = {e.split(" ")[0] for e in entries}
        oldest = {d.id for d in docs[:3]}
        self.assertTrue(oldest.isdisjoint(kept_ids))
        self.assertTrue({d.id for d in docs[3:]} <= kept_ids)

    def test_description_truncated_and_empty_store(self):
        self.assertEqual("", render_approved_adr_index(self.main))
        _approved_adr(self.main, "Long", "x" * 400)
        line = [l for l in render_approved_adr_index(self.main).splitlines() if l.startswith("ADR-")][0]
        self.assertTrue(line.endswith("x" * 160))
        self.assertFalse(line.endswith("x" * 161))


class PlannerPromptTests(PromptBase):
    def test_planner_told_to_link_adrs(self):
        """Scenario: agent-context / Approved ADRs pushed to agents / Planner is told to link ADRs"""
        adr = _approved_adr(self.main, "Use SQLite", "d")
        prompt = build_planner_prompt("spec text", root=self.main, spec_id="spec-1234abcd")
        self.assertIn(adr.id, prompt)
        self.assertIn("adr:<id>", prompt)
        self.assertIn("`spec:spec-1234abcd`", prompt)

    def test_planner_prompt_unchanged_without_context(self):
        base = build_planner_prompt("spec text")
        self.assertEqual(base, build_planner_prompt("spec text", root=None))
        self.assertEqual(base, build_planner_prompt("spec text", root=self.main))  # no ADRs
        self.assertNotIn("adr:<id>", base)


class ReviewRuleTests(PromptBase):
    def test_review_prompt_carries_adr_rule(self):
        """Scenario: agent-context / Reviewer enforces approved ADRs / Review prompt carries the ADR rule"""
        review = self.worker(_bead(agent_type="review"))
        self.assertIn("ADR conflict: ADR-", review)
        self.assertIn("needs_changes", review)
        for t in ("developer", "tester", "documentation", "defect"):
            self.assertNotIn("ADR conflict:", self.worker(_bead(agent_type=t)), t)


class SizeBudgetTests(PromptBase):
    def test_added_blocks_within_6000_chars(self):
        """Prompt size: one linked spec + 18 realistic ADR index lines fit in 6,000 chars"""
        spec = get_store(self.ws, "spec").create("Agent context feature")
        for i in range(18):
            _approved_adr(
                self.main, f"Realistic decision title number {i} about architecture",
                "A realistic one-sentence description of the architectural decision that "
                "was made and why, long enough to be near the truncation limit of 160 chars.",
            )
        bead = _bead(linked_docs=[f"spec:{spec.id}"])
        with_ctx = self.worker(bead)
        # Baseline: same prompt with no linked docs and an empty main (no ADRs, no lookup block).
        empty_main = self.main.parent / "empty"
        empty_main.mkdir()
        baseline = build_worker_prompt(
            _bead(linked_docs=[f"spec:{spec.id}"]), [], self.ws, workspace_root=self.ws, main_root=empty_main
        )
        added = len(with_ctx) - len(baseline)
        self.assertGreater(added, 0)
        index = render_approved_adr_index(self.main)
        self.assertEqual(18, len([l for l in index.splitlines() if l.startswith("ADR-")]))
        self.assertLessEqual(added, 6000)


if __name__ == "__main__":
    unittest.main()
