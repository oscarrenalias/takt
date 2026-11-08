"""CLI tests for ``takt adr search`` / ``takt adr reindex``.

Embedding calls are patched (memory._embed / memory._download_model); the model is never loaded.
"""
from __future__ import annotations

import io
import json
import subprocess
import sys
import types
import unittest
from argparse import Namespace
from contextlib import redirect_stderr
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_takt.cli.commands.adr import command_adr
from agent_takt.console import ConsoleReporter
from agent_takt.documents.registry import get_store

from test_documents_index import DocIndexBase, adr_text


def adr_ns(**kw) -> Namespace:
    base = dict(root=None, query="", status=[], tag=[], limit=10, chunks=False, plain=False,
                output_json=False, check=False, all=False, supersedes=[])
    base.update(kw)
    return Namespace(**base)


class AdrSearchBase(DocIndexBase):
    def setUp(self):
        super().setUp()
        for cmd in (["git", "init"], ["git", "config", "user.email", "t@example.com"],
                    ["git", "config", "user.name", "Test"], ["git", "config", "commit.gpgsign", "false"]):
            subprocess.run(cmd, cwd=self.root, check=True, capture_output=True)
        tmpl = self.root / "templates" / "adr"
        tmpl.mkdir(parents=True)
        (tmpl / "template.md").write_bytes((REPO_ROOT / "templates" / "adr" / "template.md").read_bytes())
        self.write("adr/drafts/adr-aaaa1111-draft.md", adr_text(
            "ADR-aaaa1111", "Draft quokka choice", body="## Summary\n\nWe pick quokka7 storage.\n", tags="[db]"))
        self.write("adr/approved/adr-bbbb2222-appr.md", adr_text(
            "ADR-bbbb2222", "Approved quokka choice", body="## Summary\n\nWe approved quokka7 queues.\n", tags="[db, mq]"))
        self.write("adr/superseded/adr-cccc3333-sup.md", adr_text(
            "ADR-cccc3333", "Superseded quokka choice", body="## Summary\n\nOld quokka7 cache.\n"))
        self.write("adr/rejected/adr-dddd4444-rej.md", adr_text(
            "ADR-dddd4444", "Rejected quokka choice", body="## Summary\n\nRejected quokka7 idea.\n"))

    def run_cli(self, command="search", **kw):
        kw.setdefault("root", str(self.root))
        args = adr_ns(adr_command=command, **kw)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stderr(err):
            rc = command_adr(args, types.SimpleNamespace(root=self.root), ConsoleReporter(stream=out))
        return rc, out.getvalue(), err.getvalue()

    def ids(self, **kw) -> set[str]:
        rc, out, err = self.run_cli(query="quokka7", output_json=True, limit=20, **kw)
        self.assertEqual(rc, 0, err)
        return {h["id"] for h in json.loads(out)}


class AdrSearchTests(AdrSearchBase):
    def test_default_excludes_superseded_and_rejected(self):
        self.assertEqual(self.ids(), {"ADR-aaaa1111", "ADR-bbbb2222"})

    def test_all_includes_everything(self):
        self.assertEqual(self.ids(all=True),
                         {"ADR-aaaa1111", "ADR-bbbb2222", "ADR-cccc3333", "ADR-dddd4444"})

    def test_explicit_status_superseded_included(self):
        self.assertEqual(self.ids(status=["superseded"]), {"ADR-cccc3333"})
        self.assertEqual(self.ids(status=["superseded", "rejected"]), {"ADR-cccc3333", "ADR-dddd4444"})

    def test_tag_and_filter(self):
        self.assertEqual(self.ids(tag=["db"]), {"ADR-aaaa1111", "ADR-bbbb2222"})
        self.assertEqual(self.ids(tag=["db", "mq"]), {"ADR-bbbb2222"})

    def test_json_output_identity(self):
        rc, out, _ = self.run_cli(query="queues", output_json=True)
        top = json.loads(out)[0]
        self.assertEqual((top["id"], top["type"], top["status"], top["title"]),
                         ("ADR-bbbb2222", "adr", "approved", "Approved quokka choice"))
        self.assertEqual(top["path"], "adr/approved/adr-bbbb2222-appr.md")
        self.assertEqual(set(top), {"id", "type", "status", "title", "path", "section", "snippet", "score"})

    def test_plain_output_matches_spec_search_shape(self):
        rc, out, _ = self.run_cli(query="queues", plain=True)
        cols = out.splitlines()[0].split("\t")
        self.assertEqual(len(cols), 5)
        self.assertEqual((cols[0], cols[1], cols[4]), ("ADR-bbbb2222", "approved", "Approved quokka choice"))
        self.assertRegex(cols[3], r"^\d\.\d{4}$")

    def test_no_matches(self):
        rc, out, _ = self.run_cli(query="quokka7", tag=["zzz"])
        self.assertEqual((rc, out.strip()), (0, "No matches."))

    def test_approval_is_searchable(self):
        self.run_cli(query="quokka7")  # builds the index
        valid = (
            "## Summary\n\nWe pick quokka7 storage.\n\n## Context\n\nReal constraints exist.\n\n"
            "## Decision Drivers\n\n* Real driver: performance\n\n## Considered Options\n\n"
            "### Option A \u2014 Real\n\n* Good: fast\n* Bad: costly\n\n### Option B \u2014 Alt\n\n"
            "* Good: cheap\n* Bad: slow\n\n## Decision\n\nWe chose Option A for performance.\n\n"
            "## Consequences\n\n### Positive\n\n* Fast.\n\n### Negative\n\n* Costly.\n"
        )
        from agent_takt.adr import Adr, AdrStore

        path = self.root / "adr" / "drafts" / "adr-aaaa1111-draft.md"
        AdrStore(self.root)._save_file(path, Adr(
            id="ADR-aaaa1111", title="Draft quokka choice", status="draft",
            created_at="2026-01-01T00:00:00+00:00", authors=["Test"], accepted_at=None,
        ), valid)
        self.run_cli(query="quokka7")
        rc, out, err = self.run_cli(command="approve", adr_id="ADR-aaaa1111")
        self.assertEqual(rc, 0, out + err)
        row = self.query("SELECT status, path FROM doc_index_documents WHERE doc_id='ADR-aaaa1111'")[0]
        self.assertEqual(row["status"], "approved")
        self.assertIn("approved", row["path"])
        rc, out, _ = self.run_cli(query="storage", status=["approved"], output_json=True)
        top = json.loads(out)[0]
        self.assertEqual((top["id"], top["status"]), ("ADR-aaaa1111", "approved"))
        self.assertIn("adr/approved/", top["path"])

    def test_hook_inherited_by_adr_store(self):
        self.run_cli(query="quokka7")
        get_store(self.root, "adr").set_status("ADR-aaaa1111", "rejected")
        row = self.query("SELECT status, content_hash FROM doc_index_documents WHERE doc_id='ADR-aaaa1111'")[0]
        self.assertEqual(row["status"], "rejected")
        self.assertIsNone(row["content_hash"])

    def test_search_isolated_from_spec_documents(self):
        self.write("specs/drafts/s.md", __import__("test_documents_index").spec_text(
            "spec-ssss0001", "Spec quokka7", body="# Spec\n\nquokka7 spec.\n"))
        self.assertNotIn("spec-ssss0001", self.ids(all=True))

    def test_missing_takt_exits_1(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            rc, _, err = self.run_cli(root=str(Path(td).resolve()), query="x")
        self.assertEqual(rc, 1)
        self.assertIn("error: no takt project at", err)

    def test_search_from_worktree_uses_main_index(self):
        wt = self.root / ".takt" / "worktrees" / "B-feat"
        (wt / ".takt").mkdir(parents=True)
        rc, out, _ = self.run_cli(root=str(wt), query="queues", output_json=True)
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)[0]["id"], "ADR-bbbb2222")


class AdrReindexTests(AdrSearchBase):
    def test_reindex_output_and_adr_only(self):
        rc, out, _ = self.run_cli(command="reindex")
        self.assertEqual(rc, 0)
        m = self.query("SELECT COUNT(*) FROM doc_index_chunks WHERE doc_type='adr'")[0][0]
        self.assertEqual(out.strip(), f"indexed 4 adr documents ({m} chunks)")
        self.assertEqual(self.query("SELECT COUNT(*) FROM doc_index_documents WHERE doc_type!='adr'")[0][0], 0)

    def test_check_writes_nothing(self):
        rc, out, _ = self.run_cli(command="reindex", check=True)
        self.assertEqual(out.strip(), "would add 4, update 0, remove 0 (0 unchanged)")
        self.assertFalse(self.db.exists())
        self.run_cli(command="reindex")
        before = self.counts()
        (self.root / "adr" / "rejected" / "adr-dddd4444-rej.md").unlink()
        _, out, _ = self.run_cli(command="reindex", check=True)
        self.assertEqual(out.strip(), "would add 0, update 0, remove 1 (3 unchanged)")
        self.assertEqual(before, self.counts())

    def test_missing_takt_exits_1(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            rc, _, err = self.run_cli(command="reindex", root=str(Path(td).resolve()))
        self.assertEqual(rc, 1)
        self.assertIn("document search needs .takt/", err)


if __name__ == "__main__":
    unittest.main()
