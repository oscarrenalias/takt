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
