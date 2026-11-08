"""Document index storage: DB location and schema.

The index lives in the shared memory database (``memory.db``) in dedicated
``doc_index_*`` tables, and never touches the memory ``entries``/``vectors``
tables.  Embedding access goes through the ``agent_takt.memory`` module
attribute so tests can patch ``agent_takt.memory._embed`` / ``_download_model``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path

from .. import memory
from ..config import load_config
from ..paths import main_checkout_root
from .types import Document

EMBEDDING_DIM = 384


def document_index_db(root: Path) -> Path:
    """Return the index DB path: ``AGENT_MEMORY_DB`` or the main checkout's memory.db."""
    env_db = os.environ.get("AGENT_MEMORY_DB")
    if env_db:
        return Path(env_db)
    return main_checkout_root(Path(root)) / ".takt" / "memory" / "memory.db"


def configure_embedding_cache(root: Path) -> None:
    """Apply the configured model cache dir; call before any embedding."""
    main_root = main_checkout_root(Path(root))
    memory.configure_model_cache_dir(load_config(main_root).common.memory_cache_dir)


_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS doc_index_documents (
        doc_type TEXT NOT NULL,
        doc_id TEXT NOT NULL,
        path TEXT NOT NULL,
        status TEXT,
        title TEXT,
        tags TEXT,
        content_hash TEXT,
        indexed_at TEXT,
        PRIMARY KEY (doc_type, doc_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS doc_index_chunks (
        chunk_id INTEGER PRIMARY KEY AUTOINCREMENT,
        doc_type TEXT NOT NULL,
        doc_id TEXT NOT NULL,
        section TEXT,
        ord INTEGER NOT NULL,
        text TEXT NOT NULL
    )
    """,
    f"CREATE VIRTUAL TABLE IF NOT EXISTS doc_index_vectors USING vec0(embedding FLOAT[{EMBEDDING_DIM}])",
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS doc_index_fts USING fts5(
        text, section, title, tokenize='unicode61'
    )
    """,
)


def ensure_index_schema(db_path: Path) -> None:
    """Idempotently create the four ``doc_index_*`` tables in *db_path*."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = memory._open_conn(db_path)
    try:
        for stmt in _SCHEMA_STATEMENTS:
            conn.execute(stmt)
        conn.commit()
    finally:
        conn.close()


PREAMBLE_SECTION = "(preamble)"
SUMMARY_SECTION = "summary"

_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_HEADING_RE = re.compile(r"^(#{1,3})\s+(.*?)\s*#*\s*$")


@dataclass(frozen=True)
class Chunk:
    """One indexed piece of a document.

    ``text`` is the raw stored text; ``embed_text`` is what gets embedded
    (``"<title> — <section>\n<text>"``).
    """

    ord: int
    section: str
    text: str
    embed_text: str


def _iter_lines_with_fence_state(body: str):
    """Yield ``(line, in_fence)`` where in_fence is True for fence delimiters and contents."""
    fence: str | None = None
    for line in body.splitlines():
        m = _FENCE_RE.match(line)
        if fence is None:
            if m:
                fence = m.group(1)
                yield line, True
            else:
                yield line, False
        else:
            yield line, True
            if m and m.group(1) == fence:
                fence = None


def resolve_title(doc: Document) -> str:
    """Frontmatter ``title``/``name``, else first ``# `` heading, else filename stem."""
    fm = doc.frontmatter or {}
    for key in ("title", "name"):
        value = fm.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    for line, in_fence in _iter_lines_with_fence_state(doc.body or ""):
        if in_fence:
            continue
        m = _HEADING_RE.match(line)
        if m and len(m.group(1)) == 1 and m.group(2):
            return m.group(2)
    return Path(doc.path).stem


def _split_sections(body: str) -> list[tuple[str, str]]:
    """Split *body* into ``(section_name, raw_text)`` pairs, skipping empty ones."""
    sections: list[tuple[str, list[str]]] = [(PREAMBLE_SECTION, [])]
    h2: str | None = None
    for line, in_fence in _iter_lines_with_fence_state(body):
        m = None if in_fence else _HEADING_RE.match(line)
        if m and len(m.group(1)) == 2 and m.group(2):
            h2 = m.group(2)
            sections.append((h2, []))
        elif m and len(m.group(1)) == 3 and m.group(2) and h2 is not None:
            sections.append((f"{h2} / {m.group(2)}", []))
        else:
            sections[-1][1].append(line)
    out = []
    for name, lines in sections:
        text = "\n".join(lines).strip()
        if text:
            out.append((name, text))
    return out


def chunk_document(doc: Document) -> list[Chunk]:
    """Chunk *doc* for indexing; frontmatter is never chunked."""
    title = resolve_title(doc)
    description = (doc.frontmatter or {}).get("description")
    description = str(description).strip() if description is not None else ""
    summary = f"{title}\n{description}" if description else title

    pieces: list[tuple[str, str]] = [(SUMMARY_SECTION, summary)]
    for name, text in _split_sections(doc.body or ""):
        for part in memory._split_if_large(text):
            if part.strip():
                pieces.append((name, part))

    return [
        Chunk(ord=i, section=section, text=text, embed_text=f"{title} \u2014 {section}\n{text}")
        for i, (section, text) in enumerate(pieces)
    ]


# ---------------------------------------------------------------------------
# Write path: index / mark stale / remove
# ---------------------------------------------------------------------------


def _doc_type_name(doc_type) -> str:
    return getattr(doc_type, "name", doc_type)


def _tags_json(tags) -> str:
    return json.dumps(sorted(str(t) for t in (tags or [])))


def _delete_doc_rows(conn: sqlite3.Connection, type_name: str, doc_id: str) -> None:
    """Delete a document's rows from all four ``doc_index_*`` tables (caller owns the txn)."""
    chunk_ids = [
        r[0]
        for r in conn.execute(
            "SELECT chunk_id FROM doc_index_chunks WHERE doc_type = ? AND doc_id = ?",
            (type_name, doc_id),
        )
    ]
    for cid in chunk_ids:
        conn.execute("DELETE FROM doc_index_vectors WHERE rowid = ?", (cid,))
        conn.execute("DELETE FROM doc_index_fts WHERE rowid = ?", (cid,))
    conn.execute(
        "DELETE FROM doc_index_chunks WHERE doc_type = ? AND doc_id = ?", (type_name, doc_id)
    )
    conn.execute(
        "DELETE FROM doc_index_documents WHERE doc_type = ? AND doc_id = ?", (type_name, doc_id)
    )


def index_document(db_path: Path, doc_type, doc: Document) -> None:
    """Index *doc*: chunk, embed, and replace its rows atomically.

    All embedding happens before any connection is opened.  The write runs in a
    single ``BEGIN IMMEDIATE`` transaction; if the stored row already matches
    the file's content hash, path and status the call is a no-op.
    """
    db_path = Path(db_path)
    type_name = _doc_type_name(doc_type)
    doc_path = str(doc.path)
    content_hash = hashlib.sha256(Path(doc.path).read_bytes()).hexdigest()
    title = resolve_title(doc)
    tags = _tags_json((doc.frontmatter or {}).get("tags"))
    chunks = chunk_document(doc)
    embeddings = [memory._embed(c.embed_text) for c in chunks]  # before any connection

    conn = memory._open_conn(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT content_hash, path, status FROM doc_index_documents"
                " WHERE doc_type = ? AND doc_id = ?",
                (type_name, doc.id),
            ).fetchone()
            if (
                row is not None
                and row["content_hash"] == content_hash
                and row["path"] == doc_path
                and row["status"] == doc.status
            ):
                conn.rollback()
                return
            _delete_doc_rows(conn, type_name, doc.id)
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT INTO doc_index_documents"
                " (doc_type, doc_id, path, status, title, tags, content_hash, indexed_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (type_name, doc.id, doc_path, doc.status, title, tags, content_hash, now),
            )
            for chunk, embedding in zip(chunks, embeddings):
                cur = conn.execute(
                    "INSERT INTO doc_index_chunks (doc_type, doc_id, section, ord, text)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (type_name, doc.id, chunk.section, chunk.ord, chunk.text),
                )
                cid = cur.lastrowid
                conn.execute(
                    "INSERT INTO doc_index_vectors (rowid, embedding) VALUES (?, ?)",
                    (cid, embedding),
                )
                conn.execute(
                    "INSERT INTO doc_index_fts (rowid, text, section, title) VALUES (?, ?, ?, ?)",
                    (cid, chunk.text, chunk.section, title),
                )
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    finally:
        conn.close()


def mark_stale(
    db_path: Path,
    doc_type,
    doc_id: str,
    path,
    status: str,
    title: str | None,
    tags,
) -> None:
    """Upsert the document's metadata with ``content_hash`` NULL; chunks are untouched."""
    type_name = _doc_type_name(doc_type)
    conn = memory._open_conn(Path(db_path))
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                "INSERT INTO doc_index_documents"
                " (doc_type, doc_id, path, status, title, tags, content_hash, indexed_at)"
                " VALUES (?, ?, ?, ?, ?, ?, NULL, NULL)"
                " ON CONFLICT(doc_type, doc_id) DO UPDATE SET"
                " path = excluded.path, status = excluded.status, title = excluded.title,"
                " tags = excluded.tags, content_hash = NULL",
                (type_name, doc_id, str(path), status, title, _tags_json(tags)),
            )
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    finally:
        conn.close()


def remove_document(db_path: Path, doc_type, doc_id: str) -> None:
    """Delete the document's rows from all four ``doc_index_*`` tables in one transaction."""
    type_name = _doc_type_name(doc_type)
    conn = memory._open_conn(Path(db_path))
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            _delete_doc_rows(conn, type_name, doc_id)
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    finally:
        conn.close()
