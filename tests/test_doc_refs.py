import tempfile
import unittest
from pathlib import Path

from agent_takt.doc_refs import DocRef, normalize_linked_docs, parse_ref, resolve_entry, resolve_ref
from agent_takt.documents.registry import get_store


class ParseRefTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_ref("spec:spec-1cdb1b8a"), DocRef("spec", "spec-1cdb1b8a"))
        self.assertEqual(parse_ref("adr:ADR-44e7453e"), DocRef("adr", "ADR-44e7453e"))
        self.assertIsNone(parse_ref("docs/events.md"))
        self.assertIsNone(parse_ref("other:abc"))
        self.assertIsNone(parse_ref("spec:"))


class ResolveRefTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name).resolve()
        self.ws = base / "ws"
        self.main = base / "main"
        self.ws.mkdir()
        self.main.mkdir()

    def test_worktree(self):
        doc = get_store(self.ws, "spec").create("Alpha feature")
        r = resolve_ref(DocRef("spec", doc.id), self.ws, self.main)
        self.assertEqual(r.source, "worktree")
        self.assertFalse(Path(r.path).is_absolute())
        self.assertTrue((self.ws / r.path).exists())
        self.assertEqual(r.id, doc.id)
        self.assertEqual(r.title, "Alpha feature")
        self.assertEqual(r.status, doc.status)

    def test_main_only(self):
        doc = get_store(self.main, "spec").create("Beta feature")
        r = resolve_ref(DocRef("spec", doc.id), self.ws, self.main)
        self.assertEqual(r.source, "main")
        self.assertTrue(Path(r.path).is_absolute())
        self.assertTrue(Path(r.path).exists())
        self.assertEqual(r.title, "Beta feature")
        self.assertEqual(r.id, doc.id)

    def test_missing(self):
        r = resolve_ref(DocRef("spec", "spec-deadbeef"), self.ws, self.main)
        self.assertEqual((r.source, r.title, r.status, r.path), ("missing", "", "", ""))

    def test_adr_title_and_status(self):
        doc = get_store(self.main, "adr").create("Use X")
        r = resolve_ref(DocRef("adr", doc.id), self.ws, self.main)
        self.assertEqual(r.source, "main")
        self.assertEqual(r.title, "Use X")
        self.assertEqual(r.status, "draft")


class NormalizeLinkedDocsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve() / "ws"
        self.root.mkdir()

    def test_existing_spec_and_adr_paths(self):
        spec = get_store(self.root, "spec").create("Alpha feature")
        adr = get_store(self.root, "adr").create("Use X")
        sp = str(spec.path.relative_to(self.root))
        ap = str(adr.path.relative_to(self.root))
        out = normalize_linked_docs([sp, ap, str(spec.path)], self.root)
        self.assertEqual(out, [f"spec:{spec.id}", f"adr:{adr.id}"])

    def test_missing_path_matches_unique_stem(self):
        spec = get_store(self.root, "spec").create("Alpha feature")
        stem = spec.path.stem
        out = normalize_linked_docs([f"specs/drafts/{stem}.md"], self.root)
        self.assertEqual(out, [f"spec:{spec.id}"])

    def test_unmatched_and_ambiguous_unchanged(self):
        spec = get_store(self.root, "spec").create("Alpha feature")
        (self.root / "docs").mkdir()
        (self.root / "docs" / "events.md").write_text("x")
        # ambiguous: same stem in two status folders
        dup = spec.path.parent.parent / "planned"
        dup.mkdir(exist_ok=True)
        (dup / spec.path.name).write_text(spec.path.read_text())
        entries = ["docs/events.md", f"specs/gone/{spec.path.stem}.md", "docs/nothing.md"]
        self.assertEqual(normalize_linked_docs(entries, self.root), entries)

    def test_dedupe_preserves_order_and_refs_pass_through(self):
        spec = get_store(self.root, "spec").create("Alpha feature")
        sp = str(spec.path.relative_to(self.root))
        out = normalize_linked_docs(["docs/a.md", f"spec:{spec.id}", sp, "docs/a.md"], self.root)
        self.assertEqual(out, ["docs/a.md", f"spec:{spec.id}"])

    def test_resolve_entry_legacy_path(self):
        main = self.root.parent / "main"
        main.mkdir()
        doc = get_store(main, "spec").create("Beta feature")
        r = resolve_entry(f"specs/drafts/{doc.path.stem}.md", self.root, main)
        self.assertEqual((r.source, r.id, r.ref.kind), ("main", doc.id, "spec"))
        self.assertIsNone(resolve_entry("docs/events.md", self.root, main))
        r = resolve_entry(f"spec:{doc.id}", self.root, main)
        self.assertEqual(r.source, "main")


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------
# Feature-level scenarios: plan persistence, storage and audit resolution
# ---------------------------------------------------------------------------

import re  # noqa: E402
import subprocess  # noqa: E402

from agent_takt.audit import build_feature_audit, render_feature_audit_markdown  # noqa: E402
from agent_takt.models import PlanChild, PlanProposal  # noqa: E402
from agent_takt.planner import PlanningService  # noqa: E402
from agent_takt.storage import RepositoryStorage  # noqa: E402

from helpers import FakeRunner, OrchestratorTests  # noqa: E402

RepositoryStorage._auto_commit = False


def _proposal(linked: list[str]) -> PlanProposal:
    child = PlanChild(title="Impl", agent_type="developer", description="d", linked_docs=list(linked))
    feature = PlanChild(title="Feature", agent_type="planner", description="f",
                        linked_docs=list(linked), children=[child])
    return PlanProposal(epic_title="Epic", epic_description="e", linked_docs=list(linked), feature=feature)


class DocumentsReferencedByIdTests(OrchestratorTests):
    def setUp(self):
        super().setUp()
        self.spec = get_store(self.root, "spec").create("Context feature")
        self.planner = PlanningService(self.storage, FakeRunner())

    def _paths(self, bead):
        return [p.resolve() for p in self.storage.linked_context_paths(bead)]

    def _all_linked(self, ids):
        return [self.storage.load_bead(i).linked_docs for i in ids]

    def test_planned_beads_reference_spec_by_id(self):
        """Scenario: agent-context / Documents referenced by ID / Planned beads reference the spec by ID"""
        stem = self.spec.path.stem
        legacy = f"specs/drafts/{stem}.md"
        ids = self.planner.write_plan(_proposal([legacy]), spec_path=self.spec.path)
        self.assertEqual(3, len(ids))
        for linked in self._all_linked(ids):
            self.assertEqual([f"spec:{self.spec.id}"], linked)

    def test_plan_ref_not_duplicated_and_missing_spec_path_persists(self):
        ids = self.planner.write_plan(_proposal([f"spec:{self.spec.id}"]), spec_path=self.spec.path)
        for linked in self._all_linked(ids):
            self.assertEqual([f"spec:{self.spec.id}"], linked)
        # No spec_path / unreadable spec_path: persists without a ref.
        ids = self.planner.write_plan(_proposal(["docs/events.md"]), spec_path=self.root / "nope.md")
        for linked in self._all_linked(ids):
            self.assertEqual(["docs/events.md"], linked)
        ids = self.planner.write_plan(_proposal([]), spec_path=None)
        for linked in self._all_linked(ids):
            self.assertEqual([], linked)

    def test_reference_resolves_after_spec_moved(self):
        """Scenario: agent-context / Documents referenced by ID / Reference resolves after the spec moved"""
        bead = self.storage.create_bead(title="B", agent_type="developer", description="d",
                                        linked_docs=[f"spec:{self.spec.id}"])
        before = self._paths(bead)
        self.assertIn(self.spec.path.resolve(), before)
        moved = get_store(self.root, "spec").set_status(self.spec.id, "planned")
        self.assertNotEqual(self.spec.path, moved.path)
        after = self._paths(bead)
        self.assertIn(moved.path.resolve(), after)
        self.assertNotIn(self.spec.path.resolve(), after)

    def test_legacy_path_entry_still_resolves(self):
        """Scenario: agent-context / Documents referenced by ID / Legacy path entry still resolves"""
        legacy = f"specs/drafts/{self.spec.path.stem}.md"
        moved = get_store(self.root, "spec").set_status(self.spec.id, "planned")
        bead = self.storage.create_bead(title="B", agent_type="developer", description="d",
                                        linked_docs=[legacy])
        self.assertIn(moved.path.resolve(), self._paths(bead))
        resolved = resolve_entry(legacy, self.root, self.root)
        self.assertEqual(self.spec.id, resolved.id)

    def test_linked_context_paths_unknown_ref_and_dedupe(self):
        bead = self.storage.create_bead(
            title="B", agent_type="developer", description="d",
            linked_docs=["spec:spec-deadbeef", f"spec:{self.spec.id}", f"spec:{self.spec.id}"],
        )
        paths = self._paths(bead)
        self.assertEqual(1, paths.count(self.spec.path.resolve()))

    def test_audit_resolves_references(self):
        """Scenario: agent-context / Documents referenced by ID / Audit resolves references"""
        moved_spec = get_store(self.root, "spec").set_status(self.spec.id, "planned")
        root_bead = self.storage.create_bead(
            title="Feature", agent_type="planner", description="f", bead_type="feature",
            linked_docs=[f"spec:{self.spec.id}", "spec:spec-deadbeef", "docs/nothing.md"],
        )
        audit = build_feature_audit(self.storage, root_bead.bead_id)
        docs = {d["recorded"]: d for d in audit["feature"]["linked_docs"]}
        d = docs[f"spec:{self.spec.id}"]
        self.assertEqual(self.spec.id, d["id"])
        self.assertEqual("Context feature", d["title"])
        self.assertEqual("planned", d["status"])
        self.assertEqual(moved_spec.path.relative_to(self.root).as_posix(), d["current"])
        self.assertIsNone(docs["spec:spec-deadbeef"]["current"])
        self.assertEqual({"recorded", "current"}, set(docs["docs/nothing.md"]))
        md = render_feature_audit_markdown(audit)
        self.assertIn(f"{self.spec.id} Context feature [planned] → ", md)
        self.assertIn("(not found)", md)


class AdrOnMainTests(unittest.TestCase):
    def test_adr_only_on_main_is_reachable(self):
        """Scenario: agent-context / Documents referenced by ID / ADR only on main is reachable"""
        with tempfile.TemporaryDirectory() as tmp:
            main = Path(tmp).resolve() / "main"
            ws = main / ".takt" / "worktrees" / "B-feat0001"
            ws.mkdir(parents=True)
            subprocess.run(["git", "init"], cwd=ws, check=True, capture_output=True)
            adr = get_store(main, "adr").create("Main only decision")
            storage = RepositoryStorage(ws)
            storage.initialize()
            bead = storage.create_bead(title="B", agent_type="developer", description="d",
                                       linked_docs=[f"adr:{adr.id}"])
            self.assertIn(adr.path.resolve(), [p.resolve() for p in storage.linked_context_paths(bead)])
            resolved = resolve_ref(DocRef("adr", adr.id), ws, main)
            self.assertEqual("main", resolved.source)
            self.assertTrue(Path(resolved.path).is_absolute())
            audit_docs = build_feature_audit(storage, bead.bead_id)["feature"]["linked_docs"]
            self.assertEqual(adr.path.resolve(), Path(audit_docs[0]["current"]).resolve())
            self.assertEqual("Main only decision", audit_docs[0]["title"])


class TemplateGuidanceTests(unittest.TestCase):
    def test_no_template_instructs_bead_create(self):
        """No file under templates/ or src/agent_takt/_data/templates/ instructs agents to run `takt bead create`"""
        repo = Path(__file__).resolve().parents[1]
        roots = [repo / "templates", repo / "src" / "agent_takt" / "_data" / "templates"]
        negation = re.compile(r"\b(do not|don't|never|must not|not)\b", re.IGNORECASE)
        offenders = []
        for root in roots:
            for path in root.rglob("*"):
                if not path.is_file():
                    continue
                try:
                    text = path.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    continue
                for n, line in enumerate(text.splitlines(), 1):
                    if "bead create" in line and not negation.search(line):
                        offenders.append(f"{path}:{n}: {line.strip()}")
        self.assertEqual([], offenders)

    def test_old_separate_bead_wording_gone(self):
        repo = Path(__file__).resolve().parents[1]
        for path in (repo / "templates").rglob("*.md"):
            self.assertNotIn("file a separate bead", path.read_text(encoding="utf-8").lower(), str(path))
