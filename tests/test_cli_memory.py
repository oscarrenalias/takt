"""Tests for the `takt memory` CLI subcommands.

Covers:
- init: creates memory.db under .takt/memory/
- add: with explicit --namespace returns a UUID in the JSON output
- search: against empty DB returns empty list
- ingest: single-file ingest; non-existent file; missing DB exits 1
- delete: on a missing UUID exits 1 with error
- stats: on an initialised DB returns total_entries, by_namespace, db_path
- namespace list: ordering by count desc; empty DB; missing DB exits 1
- namespace show: default/custom limits; non-existent namespace returns []; missing DB exits 1
- any subcommand when db_path does not exist exits 1 with error (except init)
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_takt.cli.commands.memory import command_memory
from agent_takt.console import ConsoleReporter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

import sqlite_vec  # type: ignore[import]

_EMBEDDING_DIM = 384


def _make_embedding(seed: int = 0) -> bytes:
    v = [0.0] * _EMBEDDING_DIM
    v[seed % _EMBEDDING_DIM] = 1.0
    return sqlite_vec.serialize_float32(v)


def _patch_embed(seed: int = 0):
    return patch(
        "agent_takt.memory._embed",
        side_effect=lambda text: _make_embedding(abs(hash(text)) % _EMBEDDING_DIM),
    )


def _patch_download():
    return patch("agent_takt.memory._download_model", return_value=None)


class MemoryCliTestBase(unittest.TestCase):
    """Base: creates a temp project root and a RepositoryStorage-alike mock."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        # Initialise the memory DB so subcommands that require it can find it
        self.db_path = self.root / ".takt" / "memory" / "memory.db"

        # Build a minimal mock storage
        self.storage = MagicMock()
        self.storage.root = self.root

    def tearDown(self):
        self._tmp.cleanup()

    def _console(self) -> tuple[ConsoleReporter, io.StringIO]:
        out = io.StringIO()
        return ConsoleReporter(stream=out), out

    def _extract_json(self, raw: str):
        """Extract the last JSON object/array from console output (which may have spinner text)."""
        # Find the last { or [ start and parse from there
        for start_char, end_char in [('{', '}'), ('[', ']')]:
            idx = raw.rfind(start_char)
            if idx >= 0:
                try:
                    return json.loads(raw[idx:raw.rfind(end_char) + 1])
                except json.JSONDecodeError:
                    pass
        return json.loads(raw)

    def _args(self, memory_command: str, **kwargs) -> Namespace:
        ns = Namespace()
        ns.memory_command = memory_command
        # Defaults
        ns.text = None
        ns.namespace = "global"
        ns.namespace_command = None
        ns.source = ""
        ns.query = None
        ns.limit = 5
        ns.threshold = None
        ns.path = None
        ns.entry_id = None
        for k, v in kwargs.items():
            setattr(ns, k, v)
        return ns

    def _init_db(self):
        """Helper: create and initialise the memory.db for tests that need it."""
        from agent_takt.memory import init_db
        with _patch_download():
            init_db(self.db_path)


# ---------------------------------------------------------------------------
# Init subcommand
# ---------------------------------------------------------------------------


class TestMemoryCliInit(MemoryCliTestBase):
    def test_init_creates_memory_db(self):
        console, _ = self._console()
        with _patch_download():
            rc = command_memory(self._args("init"), self.storage, console)
        self.assertEqual(0, rc)
        self.assertTrue(self.db_path.exists())

    def test_init_is_idempotent(self):
        console, _ = self._console()
        with _patch_download():
            command_memory(self._args("init"), self.storage, console)
            rc = command_memory(self._args("init"), self.storage, console)
        self.assertEqual(0, rc)


# ---------------------------------------------------------------------------
# Add subcommand
# ---------------------------------------------------------------------------


class TestMemoryCliAdd(MemoryCliTestBase):
    def test_add_returns_uuid_json(self):
        self._init_db()
        console, out = self._console()
        with _patch_embed():
            rc = command_memory(
                self._args("add", text="hello world", namespace="global"),
                self.storage,
                console,
            )
        self.assertEqual(0, rc)
        output = out.getvalue()
        data = json.loads(output)
        self.assertIn("entry_id", data)
        import uuid
        uuid.UUID(data["entry_id"])  # validates format

    def test_add_with_explicit_namespace(self):
        self._init_db()
        console, out = self._console()
        with _patch_embed():
            rc = command_memory(
                self._args("add", text="feature text", namespace="feature:abc"),
                self.storage,
                console,
            )
        self.assertEqual(0, rc)
        data = json.loads(out.getvalue())
        self.assertEqual("feature:abc", data["namespace"])

    def test_add_without_db_exits_1(self):
        # db_path does not exist
        console, out = self._console()
        rc = command_memory(
            self._args("add", text="x"),
            self.storage,
            console,
        )
        self.assertEqual(1, rc)


# ---------------------------------------------------------------------------
# Search subcommand
# ---------------------------------------------------------------------------


class TestMemoryCliSearch(MemoryCliTestBase):
    def test_search_without_db_exits_1(self):
        """search subcommand must exit 1 with an error when db does not exist."""
        console, out = self._console()
        rc = command_memory(
            self._args("search", query="query text"),
            self.storage,
            console,
        )
        self.assertEqual(1, rc)
        # Error text should mention how to fix (run takt memory init)
        self.assertIn("init", out.getvalue())


# ---------------------------------------------------------------------------
# Ingest subcommand
# ---------------------------------------------------------------------------


class TestMemoryCliIngest(MemoryCliTestBase):
    def test_ingest_single_file(self):
        self._init_db()
        md_file = self.root / "spec.md"
        md_file.write_text("## Spec\n\nSome spec content.")
        console, out = self._console()
        # Patch ingest_file at the CLI level to bypass the sqlite-vec KNN constraint
        with patch("agent_takt.cli.commands.memory.ingest_file", return_value=3) as mock_ingest:
            rc = command_memory(
                self._args("ingest", path=str(md_file)),
                self.storage,
                console,
            )
        self.assertEqual(0, rc)
        data = self._extract_json(out.getvalue())
        self.assertIn("entries_added", data)
        mock_ingest.assert_called_once()

    def test_ingest_nonexistent_file_exits_1(self):
        self._init_db()
        console, out = self._console()
        rc = command_memory(
            self._args("ingest", path="/nonexistent/file.md"),
            self.storage,
            console,
        )
        self.assertEqual(1, rc)

    def test_ingest_without_db_exits_1(self):
        console, out = self._console()
        rc = command_memory(
            self._args("ingest", path=str(self.root / "x.md")),
            self.storage,
            console,
        )
        self.assertEqual(1, rc)


# ---------------------------------------------------------------------------
# Delete subcommand
# ---------------------------------------------------------------------------


class TestMemoryCliDelete(MemoryCliTestBase):
    def test_delete_missing_uuid_exits_1(self):
        self._init_db()
        console, out = self._console()
        rc = command_memory(
            self._args("delete", entry_id="00000000-0000-0000-0000-000000000000"),
            self.storage,
            console,
        )
        self.assertEqual(1, rc)
        # Error message should mention the ID
        self.assertIn("00000000-0000-0000-0000-000000000000", out.getvalue())

    def test_delete_valid_entry_succeeds(self):
        self._init_db()
        from agent_takt.memory import add_entry
        with _patch_embed():
            entry_id = add_entry(self.db_path, "delete me", namespace="global")
        console, out = self._console()
        rc = command_memory(
            self._args("delete", entry_id=entry_id),
            self.storage,
            console,
        )
        self.assertEqual(0, rc)

    def test_delete_without_db_exits_1(self):
        console, out = self._console()
        rc = command_memory(
            self._args("delete", entry_id="00000000-0000-0000-0000-000000000000"),
            self.storage,
            console,
        )
        self.assertEqual(1, rc)


# ---------------------------------------------------------------------------
# Stats subcommand
# ---------------------------------------------------------------------------


class TestMemoryCliStats(MemoryCliTestBase):
    def test_stats_returns_required_keys(self):
        self._init_db()
        console, out = self._console()
        rc = command_memory(self._args("stats"), self.storage, console)
        self.assertEqual(0, rc)
        data = json.loads(out.getvalue())
        for key in ("total_entries", "by_namespace", "db_path"):
            self.assertIn(key, data)

    def test_stats_without_db_exits_1(self):
        console, out = self._console()
        rc = command_memory(self._args("stats"), self.storage, console)
        self.assertEqual(1, rc)


# ---------------------------------------------------------------------------
# Namespace subcommand
# ---------------------------------------------------------------------------


class TestMemoryCliNamespace(MemoryCliTestBase):
    """Tests for `takt memory namespace list` and `takt memory namespace show`."""

    def _ns_args(self, namespace_command: str, **kwargs) -> Namespace:
        """Build args for `takt memory namespace <namespace_command>`."""
        ns = self._args("namespace", **kwargs)
        ns.namespace_command = namespace_command
        return ns

    # -- namespace list --

    def test_namespace_list_ordering(self):
        """namespace list returns namespaces ordered by count descending."""
        self._init_db()
        from agent_takt.memory import add_entry
        with _patch_embed():
            for i in range(3):
                add_entry(self.db_path, f"global text {i}", namespace="global")
            add_entry(self.db_path, "feature text", namespace="feature:x")
            for i in range(2):
                add_entry(self.db_path, f"specs text {i}", namespace="specs")

        console, out = self._console()
        rc = command_memory(self._ns_args("list"), self.storage, console)
        self.assertEqual(0, rc)
        data = json.loads(out.getvalue())
        self.assertIsInstance(data, list)
        # Verify descending order by count
        counts = [item["count"] for item in data]
        self.assertEqual(sorted(counts, reverse=True), counts)
        # global has the most entries and must appear first
        self.assertEqual("global", data[0]["namespace"])
        self.assertEqual(3, data[0]["count"])

    def test_namespace_list_empty_db(self):
        """namespace list on empty DB returns an empty JSON array."""
        self._init_db()
        console, out = self._console()
        rc = command_memory(self._ns_args("list"), self.storage, console)
        self.assertEqual(0, rc)
        data = json.loads(out.getvalue())
        self.assertEqual([], data)

    def test_namespace_list_without_db_exits_1(self):
        """namespace list without an initialised DB exits 1."""
        console, out = self._console()
        rc = command_memory(self._ns_args("list"), self.storage, console)
        self.assertEqual(1, rc)

    # -- namespace show --

    def test_namespace_show_default_limit(self):
        """namespace show returns at most 5 entries (default limit)."""
        self._init_db()
        from agent_takt.memory import add_entry
        with _patch_embed():
            for i in range(7):
                add_entry(self.db_path, f"entry {i}", namespace="global")

        console, out = self._console()
        rc = command_memory(
            self._ns_args("show", namespace="global"),
            self.storage,
            console,
        )
        self.assertEqual(0, rc)
        data = json.loads(out.getvalue())
        self.assertIsInstance(data, list)
        self.assertLessEqual(len(data), 5)
        for entry in data:
            self.assertIn("id", entry)
            self.assertIn("namespace", entry)
            self.assertIn("text", entry)

    def test_namespace_show_custom_limit(self):
        """namespace show respects a custom --limit value."""
        self._init_db()
        from agent_takt.memory import add_entry
        with _patch_embed():
            for i in range(10):
                add_entry(self.db_path, f"entry {i}", namespace="global")

        console, out = self._console()
        rc = command_memory(
            self._ns_args("show", namespace="global", limit=3),
            self.storage,
            console,
        )
        self.assertEqual(0, rc)
        data = json.loads(out.getvalue())
        self.assertIsInstance(data, list)
        self.assertLessEqual(len(data), 3)

    def test_namespace_show_nonexistent_namespace(self):
        """namespace show on a non-existent namespace returns an empty JSON array."""
        self._init_db()
        console, out = self._console()
        rc = command_memory(
            self._ns_args("show", namespace="does-not-exist"),
            self.storage,
            console,
        )
        self.assertEqual(0, rc)
        data = json.loads(out.getvalue())
        self.assertEqual([], data)

    def test_namespace_show_without_db_exits_1(self):
        """namespace show without an initialised DB exits 1."""
        console, out = self._console()
        rc = command_memory(
            self._ns_args("show", namespace="global"),
            self.storage,
            console,
        )
        self.assertEqual(1, rc)


if __name__ == "__main__":
    unittest.main()
