"""CLI tests for ``takt spec search`` / ``takt spec reindex`` (and the shared doc_search helpers).

Embedding calls are patched (memory._embed / memory._download_model); the model is never loaded.
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stderr
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_takt import memory
from agent_takt.cli.commands.spec import command_spec
from agent_takt.console import ConsoleReporter
from agent_takt.documents import index
from agent_takt.documents.registry import get_store

from test_documents_index import DocIndexBase, spec_text


def ns(**kw) -> Namespace:
    base = dict(root=None, query="", status=[], tag=[], limit=10, chunks=False, plain=False,
                output_json=False, check=False)
    base.update(kw)
    return Namespace(**base)


class SpecSearchCliBase(DocIndexBase):
    cmd = "search"

    def run_cli(self, **kw):
        kw.setdefault("root", str(self.root))
        args = ns(spec_command=kw.pop("spec_command", self.cmd), **kw)
        out = io.StringIO()
        err = io.StringIO()
        with redirect_stderr(err):
            rc = command_spec(args, ConsoleReporter(stream=out))
        return rc, out.getvalue(), err.getvalue()

    def seed(self):
        self.write("specs/drafts/a.md", spec_text(
            "spec-aaaa1111", "Alpha", tags="[api, sec]",
            body="# Alpha\n\n## Objective\n\nRotate the zebra32 credentials nightly.\n\n## Notes\n\nmore zebra32 notes\n"))
        self.write("specs/planned/b.md", spec_text(
            "spec-bbbb2222", "Beta", tags="[api]", body="# Beta\n\n## Objective\n\nTune zebra32 cache.\n"))
        self.write("specs/done/c.md", spec_text(
            "spec-cccc3333", "Gamma", tags="[docs]", body="# Gamma\n\n## Objective\n\nDocs for zebra32.\n"))


class SpecSearchTests(SpecSearchCliBase):
    def setUp(self):
        super().setUp()
        self.seed()

    def test_default_output(self):
        rc, out, _ = self.run_cli(query="credentials")
        self.assertEqual(rc, 0)
        self.assertIn("spec-aaaa1111  draft  Alpha", out)
        self.assertIn("  § Objective · ", out)

    def test_plain_output(self):
        rc, out, _ = self.run_cli(query="credentials", plain=True)
        self.assertEqual(rc, 0)
        first = out.splitlines()[0].split("\t")
        self.assertEqual(len(first), 5)
        self.assertEqual((first[0], first[1], first[2], first[4]), ("spec-aaaa1111", "draft", "Objective", "Alpha"))
        self.assertRegex(first[3], r"^\d\.\d{4}$")

    def test_json_output_has_identity_and_relative_path(self):
        rc, out, _ = self.run_cli(query="credentials", output_json=True)
        self.assertEqual(rc, 0)
        data = json.loads(out)
        top = data[0]
        self.assertEqual(set(top), {"id", "type", "status", "title", "path", "section", "snippet", "score"})
        self.assertEqual((top["id"], top["type"], top["status"], top["title"]),
                         ("spec-aaaa1111", "spec", "draft", "Alpha"))
        self.assertEqual(top["path"], "specs/drafts/a.md")
        self.assertIsInstance(top["score"], float)

    def test_no_matches_exit_zero(self):
        rc, out, _ = self.run_cli(query="zebra32", tag=["nonexistent"])
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "No matches.")

    def test_no_matches_json_is_empty_list(self):
        rc, out, _ = self.run_cli(query="zebra32", tag=["nonexistent"], output_json=True)
        self.assertEqual((rc, json.loads(out)), (0, []))

    def test_status_or_filter(self):
        rc, out, _ = self.run_cli(query="zebra32", status=["draft", "done"], output_json=True)
        self.assertEqual({h["id"] for h in json.loads(out)}, {"spec-aaaa1111", "spec-cccc3333"})

    def test_tag_and_filter(self):
        _, out, _ = self.run_cli(query="zebra32", tag=["api"], output_json=True)
        self.assertEqual({h["id"] for h in json.loads(out)}, {"spec-aaaa1111", "spec-bbbb2222"})
        _, out, _ = self.run_cli(query="zebra32", tag=["api", "sec"], output_json=True)
        self.assertEqual({h["id"] for h in json.loads(out)}, {"spec-aaaa1111"})

    def test_limit(self):
        _, out, _ = self.run_cli(query="zebra32", limit=2, output_json=True)
        self.assertEqual(len(json.loads(out)), 2)

    def test_chunks_returns_individual_chunks(self):
        _, doc_out, _ = self.run_cli(query="zebra32 notes", output_json=True)
        _, chunk_out, _ = self.run_cli(query="zebra32 notes", chunks=True, output_json=True)
        docs, chunks = json.loads(doc_out), json.loads(chunk_out)
        self.assertEqual(len({h["id"] for h in docs}), len(docs))
        self.assertGreater(len(chunks), len(docs))

    def test_snippets_have_no_highlight_markers_or_newlines(self):
        _, out, _ = self.run_cli(query="zebra32 credentials", chunks=True, output_json=True)
        for h in json.loads(out):
            self.assertNotIn("\n", h["snippet"])
            self.assertNotIn("<b>", h["snippet"])
            self.assertNotIn("</b>", h["snippet"])
            self.assertNotIn("**", h["snippet"])

    def test_query_with_fts_syntax_characters(self):
        for q in ['"zebra32', "zebra32*", "AND NOT (zebra32", "a:b"]:
            rc, _, err = self.run_cli(query=q)
            self.assertEqual(rc, 0, err)

    def test_search_picks_up_edit_outside_takt(self):
        self.run_cli(query="zebra32")
        (self.root / "specs" / "drafts" / "a.md").write_text(
            spec_text("spec-aaaa1111", "Alpha", body="# Alpha\n\n## Objective\n\nquokka7 now.\n"), encoding="utf-8")
        _, out, _ = self.run_cli(query="quokka7", output_json=True)
        self.assertEqual(json.loads(out)[0]["id"], "spec-aaaa1111")

    def test_search_picks_up_move_without_edit(self):
        self.run_cli(query="zebra32")
        src = self.root / "specs" / "drafts" / "a.md"
        dst = self.root / "specs" / "planned" / "a.md"
        src.rename(dst)
        _, out, _ = self.run_cli(query="credentials", output_json=True)
        top = json.loads(out)[0]
        self.assertEqual((top["status"], top["path"]), ("planned", "specs/planned/a.md"))

    def test_search_drops_deleted_document(self):
        self.run_cli(query="zebra32")
        (self.root / "specs" / "done" / "c.md").unlink()
        _, out, _ = self.run_cli(query="zebra32", output_json=True)
        self.assertNotIn("spec-cccc3333", {h["id"] for h in json.loads(out)})

    def test_status_change_updates_index_row(self):
        self.run_cli(query="zebra32")
        get_store(self.root, "spec").set_status("spec-aaaa1111", "planned")
        row = self.query("SELECT status, content_hash FROM doc_index_documents WHERE doc_id='spec-aaaa1111'")[0]
        self.assertEqual(row["status"], "planned")
        _, out, _ = self.run_cli(query="credentials", status=["planned"], output_json=True)
        self.assertEqual(json.loads(out)[0]["id"], "spec-aaaa1111")

    def test_unchanged_documents_not_reembedded_by_second_search(self):
        self.run_cli(query="zebra32")
        self.embedder.calls = 0
        self.run_cli(query="zebra32")
        self.assertEqual(self.embedder.calls, 1)  # query embedding only

    def test_missing_takt_dir_exits_1(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        bare = Path(td.name).resolve()
        rc, out, err = self.run_cli(root=str(bare), query="x")
        self.assertEqual(rc, 1)
        self.assertIn("error: no takt project at", err)
        self.assertIn("document search needs .takt/", err)
        self.assertFalse((bare / ".takt").exists())

    def test_db_created_when_takt_exists_without_one(self):
        self.assertFalse(self.db.exists())
        rc, _, _ = self.run_cli(query="zebra32")
        self.assertEqual(rc, 0)
        self.assertTrue(self.db.exists())

    def test_search_from_feature_worktree_uses_main_index(self):
        wt = self.root / ".takt" / "worktrees" / "B-feat"
        (wt / ".takt").mkdir(parents=True)
        rc, out, _ = self.run_cli(root=str(wt), query="credentials", output_json=True)
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)[0]["id"], "spec-aaaa1111")
        self.assertTrue(self.db.exists())
        self.assertFalse((wt / ".takt" / "memory").exists())

    def test_worktree_spec_edits_do_not_touch_shared_index(self):
        self.run_cli(query="zebra32")
        wt = self.root / ".takt" / "worktrees" / "B-feat"
        (wt / ".takt").mkdir(parents=True)
        (wt / "specs" / "drafts").mkdir(parents=True)
        before = [tuple(r) for r in self.query("SELECT * FROM doc_index_documents ORDER BY doc_id")]
        get_store(wt, "spec").create("Worktree only")
        get_store(wt, "spec").set_status(get_store(wt, "spec").load_all()[0].id, "planned")
        after = [tuple(r) for r in self.query("SELECT * FROM doc_index_documents ORDER BY doc_id")]
        self.assertEqual(before, after)

    def test_hook_does_not_create_takt_state(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        bare = Path(td.name).resolve()
        (bare / "specs" / "drafts").mkdir(parents=True)
        store = get_store(bare, "spec")
        doc = store.create("Plain")
        store.set_status(doc.id, "planned")
        self.assertFalse((bare / ".takt").exists())

    def test_hook_failure_does_not_fail_command(self):
        from unittest.mock import patch

        self.run_cli(query="zebra32")
        err = io.StringIO()
        with patch.object(index, "mark_stale", side_effect=RuntimeError("boom")), redirect_stderr(err):
            doc = get_store(self.root, "spec").set_status("spec-aaaa1111", "planned")
        self.assertEqual(doc.status, "planned")
        self.assertEqual(err.getvalue().count("warning: document index not updated"), 1)
        self.assertTrue((self.root / "specs" / "planned" / "a.md").exists())


class SpecReindexTests(SpecSearchCliBase):
    cmd = "reindex"

    def setUp(self):
        super().setUp()
        self.seed()

    def test_reindex_prints_counts_and_rebuilds(self):
        rc, out, _ = self.run_cli()
        self.assertEqual(rc, 0)
        m = self.query("SELECT COUNT(*) FROM doc_index_chunks")[0][0]
        self.assertEqual(out.strip(), f"indexed 3 spec documents ({m} chunks)")
        # drop one document from the index, then rebuild
        index.remove_document(self.db, "spec", "spec-bbbb2222")
        self.assertLess(self.query("SELECT COUNT(*) FROM doc_index_chunks")[0][0], m)
        rc, out, _ = self.run_cli()
        self.assertEqual(rc, 0)
        self.assertEqual(self.query("SELECT COUNT(*) FROM doc_index_chunks")[0][0], m)
        self.assertEqual(self.query("SELECT COUNT(*) FROM doc_index_vectors")[0][0], m)
        self.assertEqual(self.query("SELECT COUNT(*) FROM doc_index_fts")[0][0], m)

    def test_reindex_does_not_touch_memory_entries(self):
        memory.init_db(self.db)
        eid = memory.add_entry(self.db, "keep me", namespace="global")
        self.run_cli()
        self.assertEqual([r["id"] for r in self.query("SELECT id FROM entries")], [eid])

    def test_check_reports_and_writes_nothing(self):
        rc, out, _ = self.run_cli(check=True)
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "would add 3, update 0, remove 0 (0 unchanged)")
        self.assertFalse(self.db.exists())
        self.run_cli()
        before = self.counts()
        (self.root / "specs" / "done" / "c.md").unlink()
        rc, out, _ = self.run_cli(check=True)
        self.assertEqual(out.strip(), "would add 0, update 0, remove 1 (2 unchanged)")
        self.assertEqual(before, self.counts())

    def test_missing_takt_exits_1(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        rc, _, err = self.run_cli(root=str(Path(td.name).resolve()))
        self.assertEqual(rc, 1)
        self.assertIn("document search needs .takt/", err)

    def test_reindex_from_worktree_uses_main_db(self):
        wt = self.root / ".takt" / "worktrees" / "B-feat"
        (wt / ".takt").mkdir(parents=True)
        rc, _, _ = self.run_cli(root=str(wt))
        self.assertEqual(rc, 0)
        self.assertEqual(self.counts()["doc_index_documents"], 3)


if __name__ == "__main__":
    unittest.main()
