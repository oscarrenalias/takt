import tempfile
import unittest
from pathlib import Path

from agent_takt.doc_refs import DocRef, parse_ref, resolve_ref
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


if __name__ == "__main__":
    unittest.main()
