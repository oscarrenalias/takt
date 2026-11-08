"""Tests for the document index (agent_takt.documents.index) and its hook.

The embedding model is never loaded: ``agent_takt.memory._embed`` and
``_download_model`` are always patched with a deterministic bag-of-words fake.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import os
import re
import sqlite3
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

import sqlite_vec  # type: ignore[import]

from agent_takt import memory
from agent_takt.documents import index
from agent_takt.documents.registry import get_store
from agent_takt.documents.types import Document

DIM = 384


def fake_embedding(text: str) -> bytes:
    """Deterministic bag-of-words embedding (unit vector)."""
    v = [0.0] * DIM
    for tok in re.findall(r"\w+", text.lower()):
        h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
        v[h % DIM] += 1.0
    norm = math.sqrt(sum(x * x for x in v)) or 1.0
    return sqlite_vec.serialize_float32([x / norm for x in v])


class CountingEmbedder:
    def __init__(self):
        self.calls = 0
        self._lock = threading.Lock()

    def __call__(self, text: str) -> bytes:
        with self._lock:
            self.calls += 1
        return fake_embedding(text)


def spec_text(spec_id="spec-aaaa1111", name="Alpha Spec", body="# Alpha Spec\n\n## Objective\n\nDo alpha things.\n",
              tags="[]", description="null") -> str:
    return (
        f"---\nname: {name}\nid: {spec_id}\ndescription: {description}\ndependencies: null\n"
        f"priority: null\ncomplexity: null\nstatus: draft\ntags: {tags}\n"
        f"scope:\n  in: null\n  out: null\nfeature_root_id: null\n---\n{body}"
    )


def adr_text(adr_id="ADR-aaaa1111", title="Use alpha", body="## Summary\n\nWe use alpha.\n", tags="[]") -> str:
    return (
        f"---\nid: {adr_id}\ntitle: {title}\ncreated_at: 2026-01-01T00:00:00+00:00\n"
        f"authors:\n- Tester\ntags: {tags}\n---\n{body}"
    )


class DocIndexBase(unittest.TestCase):
    """Temp project with .takt/, a patched embedder, and an isolated DB."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.root = Path(self._td.name).resolve()
        (self.root / ".takt").mkdir()
        self.db = self.root / ".takt" / "memory" / "memory.db"
        self.embedder = CountingEmbedder()
        env = patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("AGENT_MEMORY_DB", None)
        for p in (
            patch("agent_takt.memory._embed", side_effect=self.embedder),
            patch("agent_takt.memory._download_model", return_value=None),
        ):
            p.start()
            self.addCleanup(p.stop)

    def init_db(self):
        memory.init_db(self.db)
        index.ensure_index_schema(self.db)

    def write(self, rel: str, text: str) -> Path:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def load(self, type_name: str, doc_id: str) -> Document:
        return get_store(self.root, type_name).get(doc_id)

    def counts(self) -> dict[str, int]:
        conn = memory._open_conn(self.db)
        try:
            return {
                t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in (
                    "doc_index_documents", "doc_index_chunks", "doc_index_vectors", "doc_index_fts",
                )
            }
        finally:
            conn.close()

    def query(self, sql: str, params=()):
        conn = memory._open_conn(self.db)
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()


def mkdoc(path: Path, body: str, fm=None, doc_id="d1", status="draft") -> Document:
    return Document(id=doc_id, type="spec", status=status, path=path, frontmatter=fm or {}, body=body, legacy=False)


# ---------------------------------------------------------------------------
# DB location / schema
# ---------------------------------------------------------------------------


class SchemaTests(DocIndexBase):
    def test_document_index_db_env_override(self):
        with patch.dict(os.environ, {"AGENT_MEMORY_DB": "/tmp/x/custom.db"}):
            self.assertEqual(index.document_index_db(self.root), Path("/tmp/x/custom.db"))

    def test_document_index_db_default(self):
        self.assertEqual(index.document_index_db(self.root), self.root / ".takt" / "memory" / "memory.db")

    def test_document_index_db_from_worktree(self):
        wt = self.root / ".takt" / "worktrees" / "X"
        (wt / ".takt").mkdir(parents=True)
        self.assertEqual(index.document_index_db(wt), self.root / ".takt" / "memory" / "memory.db")

    def test_ensure_schema_idempotent_and_creates_parent(self):
        db = self.root / "nested" / "dir" / "m.db"
        index.ensure_index_schema(db)
        index.ensure_index_schema(db)
        conn = memory._open_conn(db)
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
        conn.close()
        for t in ("doc_index_documents", "doc_index_chunks", "doc_index_vectors", "doc_index_fts"):
            self.assertIn(t, names)

    def test_schema_leaves_memory_tables_unchanged(self):
        memory.init_db(self.db)
        eid = memory.add_entry(self.db, "alpha memory fact", namespace="global")
        before = self.query("SELECT id, text, namespace FROM entries")
        before_v = self.query("SELECT COUNT(*) FROM vectors")[0][0]
        res_before = memory.search(self.db, "alpha memory", namespace="global")
        index.ensure_index_schema(self.db)
        self.write("specs/drafts/a.md", spec_text())
        index.sync_index(self.root, self.db, doc_types=("spec",))
        after = self.query("SELECT id, text, namespace FROM entries")
        self.assertEqual([tuple(r) for r in before], [tuple(r) for r in after])
        self.assertEqual(before_v, self.query("SELECT COUNT(*) FROM vectors")[0][0])
        res_after = memory.search(self.db, "alpha memory", namespace="global")
        self.assertEqual([r["id"] for r in res_before], [r["id"] for r in res_after])
        self.assertEqual(res_after[0]["id"], eid)

    def test_configure_embedding_cache_sets_override(self):
        (self.root / ".takt" / "config.yaml").write_text(
            "common:\n  memory_cache_dir: /tmp/model-cache-xyz\n", encoding="utf-8"
        )
        self.addCleanup(memory.configure_model_cache_dir, None)
        index.configure_embedding_cache(self.root)
        self.assertEqual(memory._model_cache_dir_override, Path("/tmp/model-cache-xyz"))

    def test_index_stats_never_creates(self):
        res = index.index_stats(self.db)
        self.assertEqual(res["spec"], {"documents": 0, "chunks": 0, "stale": 0})
        self.assertFalse(self.db.exists())
        memory.init_db(self.db)
        res = index.index_stats(self.db)
        self.assertEqual(res["adr"]["documents"], 0)
        self.assertEqual(self.query("SELECT name FROM sqlite_master WHERE name LIKE 'doc_index%'"), [])


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------


class ChunkingTests(unittest.TestCase):
    P = Path("/tmp/some-stem.md")

    def chunks(self, body, fm=None, path=None):
        return index.chunk_document(mkdoc(path or self.P, body, fm))

    def test_summary_chunk_with_and_without_description(self):
        c = self.chunks("body text", {"title": "T", "description": "Desc here"})
        self.assertEqual(c[0].section, "summary")
        self.assertEqual(c[0].text, "T\nDesc here")
        c = self.chunks("body text", {"title": "T"})
        self.assertEqual(c[0].text, "T")

    def test_sections_and_subsections(self):
        body = "## One\n\nalpha\n\n### Sub A\n\nbeta\n\n## Two\n\ngamma\n"
        c = self.chunks(body, {"title": "T"})
        self.assertEqual([x.section for x in c], ["summary", "One", "One / Sub A", "Two"])
        self.assertEqual([x.ord for x in c], [0, 1, 2, 3])

    def test_preamble_only(self):
        c = self.chunks("just some words\nmore words", {"title": "T"})
        self.assertEqual([x.section for x in c], ["summary", "(preamble)"])

    def test_preamble_before_headings(self):
        c = self.chunks("intro\n\n## One\n\nalpha", {"title": "T"})
        self.assertEqual([x.section for x in c], ["summary", "(preamble)", "One"])

    def test_fenced_headings_not_split_backticks_and_tildes(self):
        body = "## Real\n\n```\n## Not a heading\n```\n\n~~~\n### nor this\n~~~\n\ntail\n"
        c = self.chunks(body, {"title": "T"})
        self.assertEqual([x.section for x in c], ["summary", "Real"])
        self.assertIn("## Not a heading", c[1].text)
        self.assertIn("### nor this", c[1].text)

    def test_oversize_section_yields_multiple_chunks_same_section(self):
        big = "\n\n".join(f"Sentence number {i}. " + "word " * 120 + "end." for i in range(30))
        c = self.chunks(f"## Big\n\n{big}\n", {"title": "T"})
        big_chunks = [x for x in c if x.section == "Big"]
        self.assertGreater(len(big_chunks), 1)
        self.assertEqual([x.ord for x in c], list(range(len(c))))

    def test_title_fallback_chain(self):
        self.assertEqual(index.resolve_title(mkdoc(self.P, "# H\n", {"title": "FM", "name": "N"})), "FM")
        self.assertEqual(index.resolve_title(mkdoc(self.P, "# H\n", {"name": "N"})), "N")
        self.assertEqual(index.resolve_title(mkdoc(self.P, "text\n# Heading One\n", {})), "Heading One")
        self.assertEqual(index.resolve_title(mkdoc(self.P, "no heading", {})), "some-stem")

    def test_heading_in_fence_ignored_for_title(self):
        self.assertEqual(
            index.resolve_title(mkdoc(self.P, "```\n# fake\n```\n", {})), "some-stem"
        )

    def test_frontmatter_never_chunked(self):
        c = self.chunks("## One\n\nbody", {"title": "T", "secretkey": "SECRETVALUE"})
        self.assertFalse(any("SECRETVALUE" in x.text or "secretkey" in x.text for x in c))

    def test_embed_text_format(self):
        c = self.chunks("## One\n\nalpha", {"title": "T"})
        self.assertEqual(c[1].embed_text, "T — One\nalpha")


# ---------------------------------------------------------------------------
# index_document / mark_stale / remove_document
# ---------------------------------------------------------------------------


class WritePathTests(DocIndexBase):
    def setUp(self):
        super().setUp()
        self.init_db()
        self.path = self.write(
            "specs/drafts/a.md", spec_text(body="# A\n\n## One\n\nalpha\n\n## Two\n\nbeta\n", tags="[x, y]")
        )

    def doc(self):
        return self.load("spec", "spec-aaaa1111")

    def test_first_index_populates_all_tables(self):
        index.index_document(self.db, "spec", self.doc())
        c = self.counts()
        self.assertEqual(c["doc_index_documents"], 1)
        self.assertEqual(c["doc_index_chunks"], 4)  # summary + preamble(#A) + One + Two
        self.assertEqual(c["doc_index_vectors"], c["doc_index_chunks"])
        self.assertEqual(c["doc_index_fts"], c["doc_index_chunks"])
        row = self.query("SELECT tags, content_hash, status FROM doc_index_documents")[0]
        self.assertEqual(json.loads(row["tags"]), ["x", "y"])
        self.assertEqual(row["content_hash"], hashlib.sha256(self.path.read_bytes()).hexdigest())
        self.assertEqual(row["status"], "draft")

    def test_reindex_same_file_is_noop_no_chunk_churn(self):
        index.index_document(self.db, "spec", self.doc())
        ids = self.query("SELECT chunk_id FROM doc_index_chunks ORDER BY chunk_id")
        index.index_document(self.db, "spec", self.doc())
        self.assertEqual(ids, self.query("SELECT chunk_id FROM doc_index_chunks ORDER BY chunk_id"))
        self.assertEqual(self.counts()["doc_index_chunks"], 4)

    def test_changed_hash_replaces_rows_without_leftovers(self):
        index.index_document(self.db, "spec", self.doc())
        self.path.write_text(spec_text(body="# A\n\n## Only\n\ngamma\n"), encoding="utf-8")
        index.index_document(self.db, "spec", self.doc())
        c = self.counts()
        self.assertEqual(c["doc_index_chunks"], 3)
        self.assertEqual(c["doc_index_vectors"], 3)
        self.assertEqual(c["doc_index_fts"], 3)
        sections = {r["section"] for r in self.query("SELECT section FROM doc_index_chunks")}
        self.assertNotIn("Two", sections)

    def test_changed_path_or_status_replaces(self):
        index.index_document(self.db, "spec", self.doc())
        new = self.root / "specs" / "planned" / "a.md"
        new.parent.mkdir(parents=True)
        self.path.rename(new)
        index.index_document(self.db, "spec", self.doc())
        row = self.query("SELECT path, status FROM doc_index_documents")[0]
        self.assertEqual(row["path"], str(new))
        self.assertEqual(row["status"], "planned")
        self.assertEqual(self.counts()["doc_index_chunks"], 4)

    def test_concurrent_indexing_no_duplicates(self):
        doc = self.doc()
        errors = []

        def work():
            try:
                index.index_document(self.db, "spec", doc)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=work) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        c = self.counts()
        self.assertEqual(c["doc_index_documents"], 1)
        self.assertEqual(c["doc_index_chunks"], 4)
        self.assertEqual(c["doc_index_vectors"], 4)
        self.assertEqual(c["doc_index_fts"], 4)

    def test_mark_stale_keeps_chunks_and_forces_reembed(self):
        index.index_document(self.db, "spec", self.doc())
        index.mark_stale(self.db, "spec", "spec-aaaa1111", self.path, "draft", "A", ["x"])
        row = self.query("SELECT content_hash FROM doc_index_documents")[0]
        self.assertIsNone(row["content_hash"])
        self.assertEqual(self.counts()["doc_index_chunks"], 4)
        ids = self.query("SELECT chunk_id FROM doc_index_chunks ORDER BY chunk_id")
        index.index_document(self.db, "spec", self.doc())
        self.assertIsNotNone(self.query("SELECT content_hash FROM doc_index_documents")[0]["content_hash"])
        self.assertNotEqual(ids, self.query("SELECT chunk_id FROM doc_index_chunks ORDER BY chunk_id"))
        self.assertEqual(self.counts()["doc_index_chunks"], 4)

    def test_mark_stale_inserts_new_row(self):
        index.mark_stale(self.db, "spec", "spec-new", self.path, "draft", "N", [])
        self.assertEqual(self.counts()["doc_index_documents"], 1)
        self.assertEqual(self.counts()["doc_index_chunks"], 0)

    def test_remove_document_clears_all_tables(self):
        index.index_document(self.db, "spec", self.doc())
        index.remove_document(self.db, "spec", "spec-aaaa1111")
        self.assertEqual(set(self.counts().values()), {0})

    def test_remove_unknown_is_noop(self):
        index.index_document(self.db, "spec", self.doc())
        before = self.counts()
        index.remove_document(self.db, "spec", "spec-nope")
        self.assertEqual(before, self.counts())

    def test_exception_mid_write_rolls_back(self):
        index.index_document(self.db, "spec", self.doc())
        before = self.query("SELECT chunk_id, text FROM doc_index_chunks ORDER BY chunk_id")
        self.path.write_text(spec_text(body="# A\n\n## New\n\nzzz\n"), encoding="utf-8")
        doc = self.doc()
        real_delete = index._delete_doc_rows

        def boom(conn, t, d):
            real_delete(conn, t, d)
            raise RuntimeError("mid-write failure")

        with patch.object(index, "_delete_doc_rows", boom):
            with self.assertRaises(RuntimeError):
                index.index_document(self.db, "spec", doc)
        after = self.query("SELECT chunk_id, text FROM doc_index_chunks ORDER BY chunk_id")
        self.assertEqual([tuple(r) for r in before], [tuple(r) for r in after])
        self.assertEqual(self.counts()["doc_index_vectors"], 4)
        self.assertEqual(self.counts()["doc_index_fts"], 4)


# ---------------------------------------------------------------------------
# sync_index
# ---------------------------------------------------------------------------


class SyncTests(DocIndexBase):
    def sync(self, **kw):
        return index.sync_index(self.root, self.db, doc_types=("spec",), **kw)

    def setUp(self):
        super().setUp()
        self.p1 = self.write("specs/drafts/a.md", spec_text("spec-aaaa1111", "Alpha"))
        self.p2 = self.write("specs/drafts/b.md", spec_text("spec-bbbb2222", "Beta", body="# Beta\n\nbeta words\n"))

    def test_first_sync_adds_then_second_embeds_nothing(self):
        r = self.sync()
        self.assertEqual((r.added, r.updated, r.removed, r.unchanged), (2, 0, 0, 0))
        self.assertGreater(self.embedder.calls, 0)
        self.embedder.calls = 0
        r = self.sync()
        self.assertEqual((r.added, r.updated, r.removed, r.unchanged), (0, 0, 0, 2))
        self.assertEqual(self.embedder.calls, 0)

    def test_triggers_missing_row_null_hash_changed_content_path_status(self):
        self.sync()
        # NULL hash
        index.mark_stale(self.db, "spec", "spec-aaaa1111", self.p1, "draft", "Alpha", [])
        self.embedder.calls = 0
        r = self.sync()
        self.assertEqual((r.updated, r.unchanged), (1, 1))
        self.assertGreater(self.embedder.calls, 0)
        # changed content
        self.p2.write_text(spec_text("spec-bbbb2222", "Beta", body="# Beta\n\nchanged\n"), encoding="utf-8")
        self.assertEqual(self.sync().updated, 1)
        # changed path (move without edit) + status
        dst = self.root / "specs" / "planned" / "a.md"
        dst.parent.mkdir(parents=True)
        self.p1.rename(dst)
        r = self.sync()
        self.assertEqual((r.updated, r.unchanged), (1, 1))
        row = self.query("SELECT path, status FROM doc_index_documents WHERE doc_id='spec-aaaa1111'")[0]
        self.assertEqual((row["path"], row["status"]), (str(dst), "planned"))
        # missing row
        self.write("specs/drafts/c.md", spec_text("spec-cccc3333", "Gamma"))
        self.assertEqual(self.sync().added, 1)

    def test_status_only_change_detected(self):
        self.sync()
        index.mark_stale(self.db, "spec", "spec-aaaa1111", self.p1, "draft", "Alpha", [])
        self.sync()
        conn = memory._open_conn(self.db)
        conn.execute("UPDATE doc_index_documents SET status='bogus' WHERE doc_id='spec-aaaa1111'")
        conn.commit()
        conn.close()
        self.assertEqual(self.sync().updated, 1)
        self.assertEqual(self.query("SELECT status FROM doc_index_documents WHERE doc_id='spec-aaaa1111'")[0][0], "draft")

    def test_removed_files_deleted_from_index(self):
        self.sync()
        self.p2.unlink()
        r = self.sync()
        self.assertEqual((r.removed, r.unchanged), (1, 1))
        ids = [x[0] for x in self.query("SELECT doc_id FROM doc_index_documents")]
        self.assertEqual(ids, ["spec-aaaa1111"])
        self.assertEqual(self.counts()["doc_index_chunks"], self.counts()["doc_index_fts"])

    def test_dry_run_changes_nothing_with_db(self):
        self.sync()
        before = self.counts()
        self.p2.unlink()
        self.write("specs/drafts/c.md", spec_text("spec-cccc3333", "Gamma"))
        self.embedder.calls = 0
        r = self.sync(dry_run=True)
        self.assertEqual((r.added, r.removed), (1, 1))
        self.assertEqual(before, self.counts())
        self.assertEqual(self.embedder.calls, 0)

    def test_dry_run_with_absent_db(self):
        r = self.sync(dry_run=True)
        self.assertEqual(r.added, 2)
        self.assertFalse(self.db.exists())

    def test_duplicate_id_warning(self):
        self.write("specs/drafts/a-dup.md", spec_text("spec-aaaa1111", "Alpha dup"))
        buf = io.StringIO()
        with redirect_stderr(buf):
            r = self.sync()
        self.assertIn("duplicate spec document id", buf.getvalue())
        self.assertEqual(r.added, 2)

    def test_progress_line_over_threshold(self):
        for i in range(12):
            self.write(f"specs/drafts/n{i}.md", spec_text(f"spec-n{i:07d}", f"N{i}"))
        buf = io.StringIO()
        with redirect_stderr(buf):
            self.sync()
        self.assertIn("indexing 14 spec documents", buf.getvalue())

    def test_no_progress_line_at_or_below_threshold(self):
        buf = io.StringIO()
        with redirect_stderr(buf):
            self.sync()
        self.assertNotIn("indexing", buf.getvalue())

    def test_concurrent_sync_no_duplicates(self):
        errors = []

        def work():
            try:
                self.sync()
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        index.ensure_index_schema(self.db)
        ts = [threading.Thread(target=work) for _ in range(3)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(errors, [])
        c = self.counts()
        self.assertEqual(c["doc_index_documents"], 2)
        self.assertEqual(c["doc_index_chunks"], c["doc_index_vectors"])
        self.assertEqual(c["doc_index_chunks"], c["doc_index_fts"])
        per = self.query("SELECT doc_id, COUNT(*) n, COUNT(DISTINCT ord) d FROM doc_index_chunks GROUP BY doc_id")
        for r in per:
            self.assertEqual(r["n"], r["d"])


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


class SearchTests(DocIndexBase):
    def setUp(self):
        super().setUp()
        self.write("specs/drafts/a.md", spec_text("spec-aaaa1111", "Alpha",
                   body="# Alpha\n\n## Objective\n\nRotate the zebra32 credentials nightly.\n\n## Other\n\nunrelated cats\n",
                   tags="[api, sec]"))
        self.write("specs/planned/b.md", spec_text("spec-bbbb2222", "Beta",
                   body="# Beta\n\n## Objective\n\nTune the database cache.\n", tags="[api]"))
        self.write("specs/done/c.md", spec_text("spec-cccc3333", "Gamma",
                   body="# Gamma\n\n## Objective\n\nWrite docs about cats.\n", tags="[docs]"))
        index.sync_index(self.root, self.db, doc_types=("spec",))

    def s(self, q, **kw):
        return index.search(self.db, q, "spec", **kw)

    def test_exact_identifier_found_by_keyword(self):
        hits = self.s("zebra32")
        self.assertEqual(hits[0].id, "spec-aaaa1111")
        self.assertEqual(hits[0].section, "Objective")

    def test_hit_identity_fields(self):
        h = self.s("zebra32")[0]
        self.assertEqual((h.type, h.status, h.title), ("spec", "draft", "Alpha"))
        self.assertTrue(h.path.endswith("a.md"))
        self.assertGreater(h.score, 0)

    def test_fts_syntax_characters_do_not_raise(self):
        for q in ['"unbalanced', "foo*", "AND", "NOT cats", "(a OR b", "a:b", "x AND NOT y)", "'; DROP TABLE x;--"]:
            self.s(q)

    def test_status_or_filter(self):
        hits = self.s("cats database", statuses=["draft", "planned"])
        self.assertEqual({h.status for h in hits} - {"draft", "planned"}, set())
        self.assertTrue(hits)
        self.assertEqual({h.id for h in self.s("cats database", statuses=["done"])}, {"spec-cccc3333"})

    def test_tag_and_filter(self):
        self.assertEqual({h.id for h in self.s("cats database credentials", tags=["api"])},
                         {"spec-aaaa1111", "spec-bbbb2222"})
        self.assertEqual({h.id for h in self.s("cats database credentials", tags=["api", "sec"])},
                         {"spec-aaaa1111"})
        self.assertEqual(self.s("cats", tags=["nonexistent"]), [])

    def test_per_document_and_chunks(self):
        per_doc = self.s("alpha cats unrelated credentials")
        ids = [h.id for h in per_doc]
        self.assertEqual(len(ids), len(set(ids)))
        chunks = self.s("alpha cats unrelated credentials", per_document=False)
        self.assertGreater(len(chunks), len(per_doc))

    def test_limit_truncates(self):
        self.assertEqual(len(self.s("cats database credentials", limit=1)), 1)
        self.assertEqual(self.s("cats", limit=0), [])

    def test_snippets_clean(self):
        for h in self.s("zebra32 cats database", per_document=False):
            self.assertNotIn("\n", h.snippet)
            self.assertNotIn("<b>", h.snippet)
            self.assertTrue(h.snippet)

    def test_vector_only_hit_snippet_is_first_160_chars(self):
        long = "uniqueterm " + "filler " * 60
        self.write("specs/drafts/long.md", spec_text("spec-long0001", "Long", body=f"# Long\n\n## S\n\n{long}\n"))
        index.sync_index(self.root, self.db, doc_types=("spec",))
        hits = self.s("completelyabsentword", per_document=False, limit=50)
        self.assertTrue(hits)  # vector leg always returns neighbours
        for h in hits:
            self.assertLessEqual(len(h.snippet), 160)
            self.assertNotIn("\n", h.snippet)

    def test_empty_db_and_empty_query(self):
        empty = self.root / "empty.db"
        memory.init_db(empty)
        index.ensure_index_schema(empty)
        self.assertEqual(index.search(empty, "anything", "spec"), [])
        self.assertEqual(self.s(""), [])
        self.assertEqual(self.s("   "), [])

    def test_type_isolation(self):
        self.assertEqual(index.search(self.db, "zebra32", "adr"), [])

    def test_rrf_score_is_sum_of_reciprocal_ranks(self):
        hits = self.s("zebra32", per_document=False, limit=100)
        top = hits[0]
        # Upper bound: rank 1 in both legs; lower bound: appears in at least one leg
        self.assertLessEqual(top.score, 2.0 / 61 + 1e-12)
        total = self.query("SELECT COUNT(*) FROM doc_index_chunks")[0][0]
        for h in hits:
            self.assertGreaterEqual(h.score, 1.0 / (60 + total) - 1e-12)

    def test_rrf_exact_two_leg_sum(self):
        # Single-chunk-matching keyword: keyword rank 1; vector rank computed from fake embedder.
        qv = fake_embedding("zebra32")
        conn = memory._open_conn(self.db)
        ranked = [r["rowid"] for r in conn.execute(
            "SELECT rowid, distance FROM doc_index_vectors WHERE embedding MATCH ? AND k = 100 ORDER BY distance", (qv,))]
        cid = conn.execute(
            "SELECT chunk_id FROM doc_index_chunks WHERE text LIKE '%zebra32%'").fetchone()[0]
        conn.close()
        vrank = ranked.index(cid) + 1
        top = self.s("zebra32", per_document=False)[0]
        self.assertAlmostEqual(top.score, 1 / 61 + 1 / (60 + vrank), places=9)

    def test_filters_do_not_starve_results(self):
        # >50 chunks of another status crowd the vector top-k; the selective match must still be found.
        for i in range(30):
            self.write(f"specs/drafts/f{i}.md", spec_text(
                f"spec-f{i:07d}", f"Filler {i}",
                body=f"# Filler {i}\n\n## A\n\nzebra32 filler text {i}\n\n## B\n\nzebra32 more {i}\n"))
        self.write("specs/done/target.md", spec_text(
            "spec-target01", "Target", body="# Target\n\n## Z\n\nsomething entirely different\n", tags="[rare]"))
        index.sync_index(self.root, self.db, doc_types=("spec",))
        self.assertGreater(self.query("SELECT COUNT(*) FROM doc_index_chunks")[0][0], 60)
        hits = self.s("zebra32 filler", statuses=["done"], tags=["rare"], limit=1)
        self.assertEqual([h.id for h in hits], ["spec-target01"])


# ---------------------------------------------------------------------------
# Hook
# ---------------------------------------------------------------------------


class HookTests(DocIndexBase):
    def store(self, t="spec"):
        return get_store(self.root, t)

    def test_noop_when_db_missing_and_no_takt_created(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        bare = Path(td.name).resolve()
        (bare / "specs" / "drafts").mkdir(parents=True)
        st = get_store(bare, "spec")
        st.create("Hello")
        self.assertFalse((bare / ".takt").exists())

    def test_noop_in_worktree_root(self):
        self.init_db()
        wt = self.root / ".takt" / "worktrees" / "B-1"
        (wt / ".takt").mkdir(parents=True)
        (wt / "specs" / "drafts").mkdir(parents=True)
        get_store(wt, "spec").create("In worktree")
        self.assertEqual(self.counts()["doc_index_documents"], 0)

    def test_create_update_transition_mark_stale(self):
        self.init_db()
        st = self.store()
        doc = st.create("Hello Hook")
        row = self.query("SELECT * FROM doc_index_documents")[0]
        self.assertEqual((row["doc_id"], row["status"], row["title"]), (doc.id, "draft", "Hello Hook"))
        self.assertIsNone(row["content_hash"])
        index.sync_index(self.root, self.db, doc_types=("spec",))
        self.assertIsNotNone(self.query("SELECT content_hash FROM doc_index_documents")[0][0])
        st.set_field(doc.id, "priority", "high")
        self.assertIsNone(self.query("SELECT content_hash FROM doc_index_documents")[0][0])
        index.sync_index(self.root, self.db, doc_types=("spec",))
        st.set_status(doc.id, "planned")
        row = self.query("SELECT status, path, content_hash FROM doc_index_documents")[0]
        self.assertEqual(row["status"], "planned")
        self.assertIn("planned", row["path"])
        self.assertIsNone(row["content_hash"])

    def test_hook_never_embeds(self):
        self.init_db()
        self.store().create("No embed")
        self.assertEqual(self.embedder.calls, 0)

    def test_remove_drops_row(self):
        self.init_db()
        st = self.store()
        doc = st.create("Gone soon")
        index.sync_index(self.root, self.db, doc_types=("spec",))
        self.assertGreater(self.counts()["doc_index_chunks"], 0)
        st.remove(doc.id)
        self.assertEqual(set(self.counts().values()), {0})

    def test_malformed_frontmatter_falls_back(self):
        from agent_takt.documents.hooks import document_index_hook
        from agent_takt.documents.spec_type import SPEC_TYPE

        self.init_db()
        p = self.write("specs/drafts/broken-stem.md", "---\nid: [unclosed\n  : :\n---\nbody\n")
        buf = io.StringIO()
        with redirect_stderr(buf):
            document_index_hook(SPEC_TYPE, "spec-broken", p)
        self.assertEqual(buf.getvalue(), "")
        row = self.query("SELECT title, status FROM doc_index_documents")[0]
        self.assertEqual(row["title"], "broken-stem")
        self.assertEqual(row["status"], "draft")

    def test_forced_error_single_warning_no_raise(self):
        from agent_takt.documents.hooks import document_index_hook
        from agent_takt.documents.spec_type import SPEC_TYPE

        self.init_db()
        p = self.write("specs/drafts/a.md", spec_text())
        buf = io.StringIO()
        with patch.object(index, "mark_stale", side_effect=RuntimeError("kaboom")), redirect_stderr(buf):
            document_index_hook(SPEC_TYPE, "spec-aaaa1111", p)
        lines = [l for l in buf.getvalue().splitlines() if l]
        self.assertEqual(len(lines), 1)
        self.assertIn("warning: document index not updated for spec-aaaa1111", lines[0])
        self.assertIn("kaboom", lines[0])

    def test_adr_store_inherits_hook(self):
        self.init_db()
        st = self.store("adr")
        doc = st.create("Adopt hooks")
        row = self.query("SELECT doc_type, doc_id, status FROM doc_index_documents")[0]
        self.assertEqual((row["doc_type"], row["doc_id"], row["status"]), ("adr", doc.id, "draft"))
        st.set_status(doc.id, "rejected")
        row = self.query("SELECT status, path FROM doc_index_documents")[0]
        self.assertEqual(row["status"], "rejected")
        self.assertIn("rejected", row["path"])


if __name__ == "__main__":
    unittest.main()
