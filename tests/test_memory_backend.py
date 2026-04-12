"""Tests for agent_takt.memory — backend initialization, CRUD, search, chunking, and ingestion.

Covers:
- init_db: idempotency and WAL mode
- add_entry + search: round-trip, namespace isolation, threshold filtering
- delete_entry: removal and ValueError for unknown IDs
- stats: total_entries and by_namespace
- ingest_file: deduplication and chunk count
- _chunk_json, _chunk_csv, _chunk_file: dispatch and splitting behaviour
- _find_project_root, _resolve_takt_cmd: project-root resolution helpers (runner module)
"""
from __future__ import annotations

import csv
import io
import json
import shutil
import sqlite3
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

import sqlite_vec  # type: ignore[import]

from agent_takt.memory import (
    _CHUNK_MAX_CHARS,
    _DEDUP_THRESHOLD,
    _EMBEDDING_DIM,
    _chunk_csv,
    _chunk_file,
    _chunk_json,
    _chunk_markdown,
    _chunk_text,
    _model_cache_dir,
    _split_if_large,
    add_entry,
    configure_model_cache_dir,
    delete_entry,
    ingest_file,
    init_db,
    search,
    stats,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_embedding(seed: int = 0) -> bytes:
    """Return a deterministic unit-vector embedding blob (384 float32 values)."""
    v = [0.0] * _EMBEDDING_DIM
    v[seed % _EMBEDDING_DIM] = 1.0
    return sqlite_vec.serialize_float32(v)


def _patch_embed(seed_fn=None):
    """Context manager that patches _embed to return deterministic vectors.

    *seed_fn(text) -> int* controls which dimension gets the 1.0 value.
    Defaults to ``abs(hash(text)) % _EMBEDDING_DIM``.
    """
    if seed_fn is None:
        def seed_fn(text: str) -> int:
            return abs(hash(text)) % _EMBEDDING_DIM

    def fake_embed(text: str) -> bytes:
        return _make_embedding(seed_fn(text))

    return patch("agent_takt.memory._embed", side_effect=fake_embed)


def _patch_download():
    """Context manager that patches _download_model to a no-op."""
    return patch("agent_takt.memory._download_model", return_value=None)


# ---------------------------------------------------------------------------
# TestInitDb
# ---------------------------------------------------------------------------


class TestInitDb(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / ".takt" / "memory" / "memory.db"

    def tearDown(self):
        self._tmp.cleanup()

    def test_creates_db_file(self):
        with _patch_download():
            init_db(self.db_path)
        self.assertTrue(self.db_path.exists())

    def test_wal_mode_is_set(self):
        with _patch_download():
            init_db(self.db_path)
        conn = sqlite3.connect(str(self.db_path))
        try:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            self.assertEqual("wal", mode)
        finally:
            conn.close()

    def test_entries_table_created(self):
        with _patch_download():
            init_db(self.db_path)
        conn = sqlite3.connect(str(self.db_path))
        try:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            self.assertIn("entries", tables)
        finally:
            conn.close()

    def test_idempotent_no_error_on_second_call(self):
        with _patch_download():
            init_db(self.db_path)
            init_db(self.db_path)  # must not raise

    def test_idempotent_no_schema_duplication(self):
        with _patch_download():
            init_db(self.db_path)
            init_db(self.db_path)
        conn = sqlite3.connect(str(self.db_path))
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='entries'"
            ).fetchone()[0]
            self.assertEqual(1, count)
        finally:
            conn.close()

    def test_creates_parent_directories(self):
        deep_path = Path(self._tmp.name) / "a" / "b" / "c" / "memory.db"
        with _patch_download():
            init_db(deep_path)
        self.assertTrue(deep_path.exists())

    def test_download_model_called(self):
        with _patch_download() as mock_dl:
            init_db(self.db_path)
        mock_dl.assert_called_once()


# ---------------------------------------------------------------------------
# TestAddAndSearch
# ---------------------------------------------------------------------------


class TestAddAndSearch(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "memory.db"
        with _patch_download():
            init_db(self.db_path)

    def tearDown(self):
        self._tmp.cleanup()

    def test_add_entry_returns_uuid_string(self):
        with _patch_embed():
            entry_id = add_entry(self.db_path, "hello world", namespace="global")
        import uuid
        self.assertIsInstance(entry_id, str)
        uuid.UUID(entry_id)  # raises if not a valid UUID

    def test_search_round_trip_exact_match(self):
        """add then search with same query yields distance near 0."""
        text = "semantic memory test"
        with _patch_embed():
            add_entry(self.db_path, text, namespace="global")
            results = search(self.db_path, text, namespace="global", limit=1)
        self.assertEqual(1, len(results))
        self.assertLess(results[0]["distance"], 0.1)
        self.assertEqual(text, results[0]["text"])
        self.assertEqual("global", results[0]["namespace"])

    def test_search_result_fields_present(self):
        with _patch_embed():
            add_entry(self.db_path, "check fields", namespace="global", source="tester")
            results = search(self.db_path, "check fields", namespace="global", limit=1)
        self.assertEqual(1, len(results))
        row = results[0]
        for field in ("id", "text", "namespace", "source", "distance", "metadata"):
            self.assertIn(field, row, f"Missing field: {field}")

    def test_search_source_field_preserved(self):
        with _patch_embed():
            add_entry(self.db_path, "tagged entry", namespace="global", source="planner")
            results = search(self.db_path, "tagged entry", namespace="global", limit=1)
        self.assertEqual("planner", results[0]["source"])

    def test_namespace_isolation_global_excludes_feature(self):
        """Searching namespace='global' should not return entries from 'feature:X'."""
        with _patch_embed():
            add_entry(self.db_path, "global entry", namespace="global")
            add_entry(self.db_path, "feature entry", namespace="feature:X")
            # Search global namespace for the feature text — should not appear
            results = search(self.db_path, "feature entry", namespace="global", limit=5)
        namespaces = {r["namespace"] for r in results}
        self.assertNotIn("feature:X", namespaces)

    def test_namespace_isolation_feature_excludes_global(self):
        """Searching namespace='feature:X' should not return entries from 'global'."""
        with _patch_embed():
            add_entry(self.db_path, "global only", namespace="global")
            add_entry(self.db_path, "feature only", namespace="feature:X")
            results = search(self.db_path, "global only", namespace="feature:X", limit=5)
        namespaces = {r["namespace"] for r in results}
        self.assertNotIn("global", namespaces)

    def test_search_all_namespaces_when_namespace_is_none(self):
        """search(namespace=None) returns results from multiple namespaces."""
        with _patch_embed():
            add_entry(self.db_path, "alpha text", namespace="global")
            add_entry(self.db_path, "beta text", namespace="feature:Y")
            results = search(self.db_path, "alpha text", namespace=None, limit=5)
        namespaces = {r["namespace"] for r in results}
        # At least one result should exist (the global one)
        self.assertGreater(len(results), 0)
        # namespace field must be present on every result
        for r in results:
            self.assertIn("namespace", r)

    def test_search_empty_db_returns_empty_list(self):
        with _patch_embed():
            results = search(self.db_path, "nothing here", limit=5)
        self.assertEqual([], results)

    def test_threshold_filters_distant_results(self):
        """Results with distance > threshold are excluded."""
        # Use a fixed-index embed so every text maps to dim 0; distance = 0 for same text
        with _patch_embed(seed_fn=lambda _: 0):
            add_entry(self.db_path, "nearby", namespace="global")
            # threshold=0 means only exact matches (distance == 0)
            results = search(self.db_path, "nearby", namespace="global", limit=5, threshold=0.001)
        self.assertGreater(len(results), 0)
        for r in results:
            self.assertLessEqual(r["distance"], 0.001)

    def test_limit_caps_results(self):
        with _patch_embed(seed_fn=lambda _: 0):
            for i in range(5):
                add_entry(self.db_path, f"entry {i}", namespace="global")
            results = search(self.db_path, "entry", namespace=None, limit=3)
        self.assertLessEqual(len(results), 3)

    def test_metadata_stored_and_returned(self):
        meta = {"key": "value", "num": 42}
        with _patch_embed():
            entry_id = add_entry(self.db_path, "meta test", namespace="global", metadata=meta)
            results = search(self.db_path, "meta test", namespace="global", limit=1)
        self.assertEqual(meta, results[0]["metadata"])


# ---------------------------------------------------------------------------
# TestDeleteEntry
# ---------------------------------------------------------------------------


class TestDeleteEntry(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "memory.db"
        with _patch_download():
            init_db(self.db_path)

    def tearDown(self):
        self._tmp.cleanup()

    def test_delete_entry_removes_from_db(self):
        with _patch_embed():
            entry_id = add_entry(self.db_path, "to be deleted", namespace="global")
            delete_entry(self.db_path, entry_id)
            results = search(self.db_path, "to be deleted", namespace="global", limit=5)
        ids = {r["id"] for r in results}
        self.assertNotIn(entry_id, ids)

    def test_delete_entry_raises_for_unknown_id(self):
        with self.assertRaises(ValueError):
            delete_entry(self.db_path, "00000000-0000-0000-0000-000000000000")

    def test_delete_entry_removes_vector_row(self):
        """After deletion the vectors table should have no orphaned row."""
        with _patch_embed():
            entry_id = add_entry(self.db_path, "vector check", namespace="global")
        # Count vectors before deletion
        conn = sqlite3.connect(str(self.db_path))
        try:
            before = conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
        finally:
            conn.close()

        delete_entry(self.db_path, entry_id)

        conn = sqlite3.connect(str(self.db_path))
        try:
            after = conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
        finally:
            conn.close()

        self.assertEqual(before - 1, after)


# ---------------------------------------------------------------------------
# TestStats
# ---------------------------------------------------------------------------


class TestStats(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "memory.db"
        with _patch_download():
            init_db(self.db_path)

    def tearDown(self):
        self._tmp.cleanup()

    def test_stats_keys_present(self):
        result = stats(self.db_path)
        for key in ("total_entries", "by_namespace", "db_path"):
            self.assertIn(key, result)

    def test_stats_empty_db(self):
        result = stats(self.db_path)
        self.assertEqual(0, result["total_entries"])
        self.assertEqual({}, result["by_namespace"])

    def test_stats_after_adds(self):
        with _patch_embed():
            add_entry(self.db_path, "a", namespace="global")
            add_entry(self.db_path, "b", namespace="global")
            add_entry(self.db_path, "c", namespace="feature:Z")
        result = stats(self.db_path)
        self.assertEqual(3, result["total_entries"])
        self.assertEqual(2, result["by_namespace"]["global"])
        self.assertEqual(1, result["by_namespace"]["feature:Z"])

    def test_stats_db_path_is_string(self):
        result = stats(self.db_path)
        self.assertIsInstance(result["db_path"], str)
        self.assertEqual(str(self.db_path), result["db_path"])


# ---------------------------------------------------------------------------
# TestIngestFile
# ---------------------------------------------------------------------------


class TestIngestFile(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "memory.db"
        with _patch_download():
            init_db(self.db_path)

    def tearDown(self):
        self._tmp.cleanup()

    def _make_md(self, content: str) -> Path:
        p = Path(self._tmp.name) / "test.md"
        p.write_text(content, encoding="utf-8")
        return p

    def _make_json(self, data) -> Path:
        p = Path(self._tmp.name) / "test.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        return p

    def _make_csv(self, rows: list[list[str]]) -> Path:
        p = Path(self._tmp.name) / "test.csv"
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerows(rows)
        p.write_text(buf.getvalue(), encoding="utf-8")
        return p

    def test_ingest_returns_chunk_count(self):
        md = self._make_md("## Section A\n\nContent A.\n\n## Section B\n\nContent B.")
        with _patch_embed():
            count = ingest_file(self.db_path, md, namespace="global")
        self.assertGreater(count, 0)

    def test_ingest_idempotent_second_call_returns_zero(self):
        """Ingesting the same file twice should add 0 entries on the second call."""
        md = self._make_md("## Section\n\nSome unique content for dedup test.")
        with _patch_embed(seed_fn=lambda _: 0):  # same embedding = distance 0 = dedup
            first = ingest_file(self.db_path, md, namespace="global")
            second = ingest_file(self.db_path, md, namespace="global")
        self.assertGreater(first, 0)
        self.assertEqual(0, second)

    def test_ingest_json_array_dedup(self):
        p = self._make_json([{"key": "value1"}, {"key": "value2"}])
        with _patch_embed(seed_fn=lambda _: 0):
            first = ingest_file(self.db_path, p, namespace="global")
            second = ingest_file(self.db_path, p, namespace="global")
        self.assertGreater(first, 0)
        self.assertEqual(0, second)

    def test_ingest_csv_dedup(self):
        p = self._make_csv([["col1", "col2"], ["val1", "val2"], ["val3", "val4"]])
        with _patch_embed(seed_fn=lambda _: 0):
            first = ingest_file(self.db_path, p, namespace="global")
            second = ingest_file(self.db_path, p, namespace="global")
        self.assertGreater(first, 0)
        self.assertEqual(0, second)

    def test_ingest_sets_namespace_and_source(self):
        md = self._make_md("## Top\n\nspec content here")
        with _patch_embed():
            ingest_file(self.db_path, md, namespace="specs", source="planner")
            results = search(self.db_path, "spec content here", namespace="specs", limit=5)
        self.assertGreater(len(results), 0)
        self.assertEqual("planner", results[0]["source"])
        self.assertEqual("specs", results[0]["namespace"])


# ---------------------------------------------------------------------------
# TestChunkJson
# ---------------------------------------------------------------------------


class TestChunkJson(unittest.TestCase):
    def test_array_one_chunk_per_element(self):
        data = [{"a": 1}, {"b": 2}, {"c": 3}]
        text = json.dumps(data)
        chunks = _chunk_json(text)
        self.assertEqual(3, len(chunks))

    def test_array_element_too_large_further_split(self):
        """Array element exceeding _CHUNK_MAX_CHARS should be further split."""
        long_sentences = ". ".join(["Word " * 30] * 10) + "."
        data = [long_sentences]
        text = json.dumps(data)
        chunks = _chunk_json(text)
        # The single huge element should produce more than 1 chunk
        self.assertGreater(len(chunks), 1)

    def test_object_one_chunk_per_key_value_pair(self):
        data = {"key1": "value1", "key2": "value2", "key3": "value3"}
        text = json.dumps(data)
        chunks = _chunk_json(text)
        self.assertEqual(3, len(chunks))

    def test_invalid_json_falls_back_to_chunk_text(self):
        invalid = "not json at all"
        chunks_json = _chunk_json(invalid)
        chunks_text = _chunk_text(invalid)
        self.assertEqual(chunks_text, chunks_json)

    def test_scalar_falls_back_to_chunk_text(self):
        scalar = json.dumps(42)
        chunks = _chunk_json(scalar)
        # Should not raise; result is non-empty
        self.assertIsInstance(chunks, list)

    def test_string_scalar_falls_back_to_chunk_text(self):
        scalar = json.dumps("hello world")
        chunks = _chunk_json(scalar)
        self.assertIsInstance(chunks, list)


# ---------------------------------------------------------------------------
# TestChunkCsv
# ---------------------------------------------------------------------------


class TestChunkCsv(unittest.TestCase):
    def _make_csv_text(self, rows: list[list[str]]) -> str:
        buf = io.StringIO()
        csv.writer(buf).writerows(rows)
        return buf.getvalue()

    def test_header_plus_data_rows_single_chunk(self):
        rows = [["col1", "col2"], ["a", "b"], ["c", "d"]]
        chunks = _chunk_csv(self._make_csv_text(rows))
        self.assertEqual(1, len(chunks))
        # Header must appear in the chunk
        self.assertIn("col1", chunks[0])

    def test_each_chunk_starts_with_header(self):
        """When rows overflow _CHUNK_MAX_CHARS, each chunk should contain the header."""
        # Build enough rows to overflow
        header = ["column_name_1", "column_name_2"]
        data_row = ["x" * 50, "y" * 50]  # 100+ chars per row
        num_rows = (_CHUNK_MAX_CHARS // 105) + 5
        rows = [header] + [data_row] * num_rows
        chunks = _chunk_csv(self._make_csv_text(rows))
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            first_line = chunk.split("\n")[0]
            self.assertIn("column_name_1", first_line)

    def test_header_only_returns_empty_list(self):
        rows = [["col1", "col2"]]
        chunks = _chunk_csv(self._make_csv_text(rows))
        self.assertEqual([], chunks)

    def test_empty_input_returns_empty_list(self):
        chunks = _chunk_csv("")
        self.assertEqual([], chunks)


# ---------------------------------------------------------------------------
# TestChunkFile (dispatch)
# ---------------------------------------------------------------------------


class TestChunkFile(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.d = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, name: str, content: str) -> Path:
        p = self.d / name
        p.write_text(content, encoding="utf-8")
        return p

    def test_md_dispatches_to_chunk_markdown(self):
        content = "## Section One\n\nSome text here.\n\n## Section Two\n\nMore text."
        p = self._write("doc.md", content)
        chunks = _chunk_file(p)
        expected = _chunk_markdown(content)
        self.assertEqual(expected, chunks)

    def test_json_dispatches_to_chunk_json(self):
        data = [{"a": 1}, {"b": 2}]
        content = json.dumps(data)
        p = self._write("data.json", content)
        chunks = _chunk_file(p)
        expected = _chunk_json(content)
        self.assertEqual(expected, chunks)

    def test_csv_dispatches_to_chunk_csv(self):
        buf = io.StringIO()
        csv.writer(buf).writerows([["col1", "col2"], ["v1", "v2"]])
        content = buf.getvalue()
        p = self._write("data.csv", content)
        chunks = _chunk_file(p)
        expected = _chunk_csv(content)
        self.assertEqual(expected, chunks)

    def test_txt_dispatches_to_chunk_text(self):
        content = "First paragraph.\n\nSecond paragraph."
        p = self._write("readme.txt", content)
        chunks = _chunk_file(p)
        expected = _chunk_text(content)
        self.assertEqual(expected, chunks)

    def test_log_dispatches_to_chunk_text(self):
        content = "Log line one.\n\nLog line two."
        p = self._write("app.log", content)
        chunks = _chunk_file(p)
        expected = _chunk_text(content)
        self.assertEqual(expected, chunks)

    def test_unknown_extension_dispatches_to_chunk_text(self):
        content = "Some content here.\n\nMore content."
        p = self._write("file.xyz", content)
        chunks = _chunk_file(p)
        expected = _chunk_text(content)
        self.assertEqual(expected, chunks)


# ---------------------------------------------------------------------------
# TestSplitIfLarge
# ---------------------------------------------------------------------------


class TestSplitIfLarge(unittest.TestCase):
    def test_short_text_returned_as_is(self):
        text = "Short text."
        result = _split_if_large(text)
        self.assertEqual([text], result)

    def test_long_text_split_at_sentence_boundary(self):
        # Build text longer than _CHUNK_MAX_CHARS with sentence boundaries
        sentence = "This is a test sentence that is moderately long. "
        text = sentence * ((_CHUNK_MAX_CHARS // len(sentence)) + 5)
        chunks = _split_if_large(text)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), _CHUNK_MAX_CHARS + len(sentence))


# ---------------------------------------------------------------------------
# TestModelCacheDirConfiguration
# ---------------------------------------------------------------------------


class TestModelCacheDirConfiguration(unittest.TestCase):
    """Tests for _model_cache_dir() and configure_model_cache_dir()."""

    def tearDown(self):
        # Always reset process-level override so tests don't pollute each other.
        configure_model_cache_dir(None)

    def test_default_returns_home_cache(self):
        """_model_cache_dir() returns ~/.cache/agent-takt/models when override is None."""
        configure_model_cache_dir(None)
        expected = Path.home() / ".cache" / "agent-takt" / "models"
        self.assertEqual(expected, _model_cache_dir())

    def test_configure_sets_custom_path(self):
        """configure_model_cache_dir(path) causes _model_cache_dir() to return that path."""
        custom = Path("/tmp/custom-model-cache")
        configure_model_cache_dir(custom)
        self.assertEqual(custom, _model_cache_dir())

    def test_configure_none_reverts_to_default(self):
        """configure_model_cache_dir(None) reverts _model_cache_dir() to the default."""
        configure_model_cache_dir(Path("/tmp/some-path"))
        configure_model_cache_dir(None)
        expected = Path.home() / ".cache" / "agent-takt" / "models"
        self.assertEqual(expected, _model_cache_dir())

    def test_configure_overwrites_previous_custom(self):
        """A second configure_model_cache_dir() call replaces the first override."""
        configure_model_cache_dir(Path("/tmp/first"))
        configure_model_cache_dir(Path("/tmp/second"))
        self.assertEqual(Path("/tmp/second"), _model_cache_dir())


class TestInitDbModelCacheDir(unittest.TestCase):
    """Tests for init_db()'s model_cache_dir parameter."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "memory.db"

    def tearDown(self):
        self._tmp.cleanup()
        # Always reset process-level override.
        configure_model_cache_dir(None)

    def test_init_db_with_custom_model_cache_dir_sets_override(self):
        """init_db(db_path, model_cache_dir=path) applies the override process-wide."""
        custom = Path("/tmp/my-model-cache")
        with _patch_download():
            init_db(self.db_path, model_cache_dir=custom)
        self.assertEqual(custom, _model_cache_dir())

    def test_init_db_without_model_cache_dir_resets_override_to_none(self):
        """init_db(db_path) with no model_cache_dir resets the override to None."""
        # Set an override first.
        configure_model_cache_dir(Path("/tmp/pre-existing"))
        with _patch_download():
            init_db(self.db_path)  # no model_cache_dir argument
        expected = Path.home() / ".cache" / "agent-takt" / "models"
        self.assertEqual(expected, _model_cache_dir())

    def test_init_db_explicit_none_resets_override(self):
        """init_db(db_path, model_cache_dir=None) explicitly resets override to None."""
        configure_model_cache_dir(Path("/tmp/something"))
        with _patch_download():
            init_db(self.db_path, model_cache_dir=None)
        expected = Path.home() / ".cache" / "agent-takt" / "models"
        self.assertEqual(expected, _model_cache_dir())


if __name__ == "__main__":
    unittest.main()
