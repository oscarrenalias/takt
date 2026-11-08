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


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------

PROGRESS_THRESHOLD = 10


@dataclass(frozen=True)
class SyncResult:
    """Counts from :func:`sync_index`."""

    added: int = 0
    updated: int = 0
    removed: int = 0
    unchanged: int = 0


def _existing_rows(db_path: Path, type_name: str) -> dict[str, sqlite3.Row]:
    """Return ``{doc_id: row}`` for *type_name*; empty if the DB or table is absent."""
    if not Path(db_path).exists():
        return {}
    conn = memory._open_conn(Path(db_path))
    try:
        try:
            rows = conn.execute(
                "SELECT doc_id, path, status, content_hash FROM doc_index_documents"
                " WHERE doc_type = ?",
                (type_name,),
            ).fetchall()
        except sqlite3.OperationalError:
            return {}
        return {r["doc_id"]: r for r in rows}
    finally:
        conn.close()


def sync_index(
    root: Path,
    db_path: Path,
    doc_types=("spec", "adr"),
    dry_run: bool = False,
) -> SyncResult:
    """Reconcile the index with the documents on disk in the main checkout.

    A document is (re-)indexed when it has no row, a NULL or differing content
    hash, or a differing path or status.  Rows for documents no longer on disk
    are removed.  Unchanged documents are never re-embedded.  With *dry_run*
    only counts are computed and nothing is written.
    """
    from .registry import get_store

    main_root = main_checkout_root(Path(root))
    db_path = Path(db_path)
    added = updated = removed = unchanged = 0
    plan: list[tuple[str, list[Document]]] = []
    stale_ids: list[tuple[str, list[str]]] = []

    for dt in doc_types:
        type_name = _doc_type_name(dt)
        docs = sorted(get_store(main_root, type_name).load_all(), key=lambda d: str(d.path))
        unique: dict[str, Document] = {}
        for d in docs:
            if d.id in unique:
                continue
            unique[d.id] = d
        dup_count = len(docs) - len(unique)
        if dup_count:
            import sys

            print(
                f"warning: {dup_count} duplicate {type_name} document id(s) found;"
                " indexing the first in sorted path order",
                file=sys.stderr,
            )

        rows = _existing_rows(db_path, type_name)
        to_index: list[Document] = []
        for doc_id, d in unique.items():
            row = rows.get(doc_id)
            if row is None:
                added += 1
                to_index.append(d)
                continue
            try:
                current_hash = hashlib.sha256(Path(d.path).read_bytes()).hexdigest()
            except OSError:
                current_hash = None
            if (
                row["content_hash"] is None
                or row["content_hash"] != current_hash
                or row["path"] != str(d.path)
                or row["status"] != d.status
            ):
                updated += 1
                to_index.append(d)
            else:
                unchanged += 1
        gone = [i for i in rows if i not in unique]
        removed += len(gone)
        plan.append((type_name, to_index))
        stale_ids.append((type_name, gone))

    if not dry_run:
        import sys

        if any(to_index for _, to_index in plan):
            ensure_index_schema(db_path)
            configure_embedding_cache(root)
        for type_name, to_index in plan:
            if len(to_index) > PROGRESS_THRESHOLD:
                print(f"indexing {len(to_index)} {type_name} documents…", file=sys.stderr)
            for d in to_index:
                index_document(db_path, type_name, d)
        for type_name, gone in stale_ids:
            for doc_id in gone:
                remove_document(db_path, type_name, doc_id)

    return SyncResult(added=added, updated=updated, removed=removed, unchanged=unchanged)


# ---------------------------------------------------------------------------
# Hybrid search
# ---------------------------------------------------------------------------

RRF_K = 60
SNIPPET_FALLBACK_CHARS = 160


@dataclass(frozen=True)
class Hit:
    """One search result (a document, or a single chunk when ``per_document=False``)."""

    id: str
    type: str
    status: str | None
    title: str | None
    path: str
    section: str | None
    snippet: str
    score: float


def _collapse_ws(text: str) -> str:
    return " ".join((text or "").split())


def _fts_query(query: str) -> str:
    """Quote every whitespace-separated token and OR them together."""
    tokens = [t.replace('"', '""') for t in query.split()]
    return " OR ".join(f'"{t}"' for t in tokens)


def _keyword_leg(conn, query, type_name, statuses, tags, n):
    """Return ``[(chunk_id, meta_row, snippet)]`` ordered by bm25."""
    fts = _fts_query(query)
    if not fts:
        return []
    sql = (
        "SELECT c.chunk_id AS chunk_id, c.doc_id AS doc_id, c.section AS section,"
        " d.doc_type AS doc_type, d.status AS status, d.title AS title, d.path AS path,"
        " snippet(doc_index_fts, 0, '', '', '…', 16) AS snip"
        " FROM doc_index_fts"
        " JOIN doc_index_chunks c ON c.chunk_id = doc_index_fts.rowid"
        " JOIN doc_index_documents d ON d.doc_type = c.doc_type AND d.doc_id = c.doc_id"
        " WHERE doc_index_fts MATCH ? AND d.doc_type = ?"
    )
    params: list = [fts, type_name]
    if statuses:
        sql += f" AND d.status IN ({','.join('?' * len(statuses))})"
        params.extend(statuses)
    for tag in tags:
        sql += " AND EXISTS (SELECT 1 FROM json_each(d.tags) WHERE value = ?)"
        params.append(tag)
    sql += " ORDER BY bm25(doc_index_fts) LIMIT ?"
    params.append(n)
    return [(r["chunk_id"], r, _collapse_ws(r["snip"])) for r in conn.execute(sql, params)]


def _doc_tags(raw) -> set[str]:
    try:
        return {str(t) for t in json.loads(raw or "[]")}
    except (ValueError, TypeError):
        return set()


def _vector_leg(conn, query_vec, type_name, statuses, tags, limit, k):
    """Return ``[(chunk_id, meta_row, snippet)]`` ordered by distance, post-filtered."""
    total = conn.execute("SELECT COUNT(*) FROM doc_index_chunks").fetchone()[0]
    if total == 0:
        return []
    wanted_tags = set(tags)
    while True:
        k = min(k, total)
        hits = []
        docs: set[str] = set()
        ranked = conn.execute(
            "SELECT rowid, distance FROM doc_index_vectors"
            " WHERE embedding MATCH ? AND k = ? ORDER BY distance",
            (query_vec, k),
        ).fetchall()
        for rv in ranked:
            r = conn.execute(
                "SELECT c.chunk_id AS chunk_id, c.doc_id AS doc_id, c.section AS section,"
                " c.text AS text, d.doc_type AS doc_type, d.status AS status,"
                " d.title AS title, d.path AS path, d.tags AS tags"
                " FROM doc_index_chunks c"
                " JOIN doc_index_documents d ON d.doc_type = c.doc_type AND d.doc_id = c.doc_id"
                " WHERE c.chunk_id = ?",
                (rv["rowid"],),
            ).fetchone()
            if r is None or r["doc_type"] != type_name:
                continue
            if statuses and r["status"] not in statuses:
                continue
            if wanted_tags and not wanted_tags <= _doc_tags(r["tags"]):
                continue
            hits.append((r["chunk_id"], r, _collapse_ws(r["text"])[:SNIPPET_FALLBACK_CHARS]))
            docs.add(r["doc_id"])
        if len(docs) >= limit or k >= total:
            return hits
        k *= 2


def search(
    db_path: Path,
    query: str,
    doc_type,
    statuses=None,
    tags=(),
    limit: int = 10,
    per_document: bool = True,
) -> list[Hit]:
    """Hybrid keyword (FTS5 bm25) + vector search fused with RRF (k=60).

    *statuses* is an OR filter, *tags* an AND filter.  With *per_document* the
    best chunk per document is returned; otherwise individual chunks.
    """
    type_name = _doc_type_name(doc_type)
    statuses = list(statuses or [])
    tags = list(tags or [])
    if not query.split() or limit <= 0:
        return []
    n = max(limit * 5, 50)
    query_vec = memory._embed(query)  # before opening any connection

    conn = memory._open_conn(Path(db_path))
    try:
        keyword = _keyword_leg(conn, query, type_name, statuses, tags, n)
        vector = _vector_leg(conn, query_vec, type_name, statuses, tags, limit, n)
    finally:
        conn.close()

    scores: dict[int, float] = {}
    info: dict[int, tuple] = {}
    for leg in (keyword, vector):
        for rank, (cid, row, snip) in enumerate(leg, start=1):
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (RRF_K + rank)
            info.setdefault(cid, (row, snip))  # keyword-leg snippet wins (listed first)

    order = sorted(scores, key=lambda c: -scores[c])
    results: list[Hit] = []
    seen: set[str] = set()
    for cid in order:
        row, snip = info[cid]
        if per_document:
            if row["doc_id"] in seen:
                continue
            seen.add(row["doc_id"])
        results.append(
            Hit(
                id=row["doc_id"],
                type=row["doc_type"],
                status=row["status"],
                title=row["title"],
                path=row["path"],
                section=row["section"],
                snippet=snip,
                score=scores[cid],
            )
        )
        if len(results) >= limit:
            break
    return results
