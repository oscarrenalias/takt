"""
Semantic memory backend for agent-takt.

Backed by SQLite + sqlite-vec with local ONNX embeddings
(BAAI/bge-small-en-v1.5, 384-dim).  The database lives at
``{project_root}/.takt/memory/memory.db`` and is shared by the operator
and all worker agents running in the same project.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MODEL_REPO = "BAAI/bge-small-en-v1.5"
_HF_BASE = "https://huggingface.co"
_EMBEDDING_DIM = 384

#: Process-level override set by :func:`configure_model_cache_dir` or :func:`init_db`.
_model_cache_dir_override: Path | None = None
_CHUNK_MAX_CHARS = 1000
_DEDUP_THRESHOLD = 0.05

#: Files to download from the HuggingFace repository on first use.
_MODEL_FILES = [
    "onnx/model.onnx",
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
]

# ---------------------------------------------------------------------------
# Module-level embedding model cache (loaded lazily, one copy per process)
# ---------------------------------------------------------------------------

_embed_lock = threading.Lock()
_ort_session: Any = None
_hf_tokenizer: Any = None


# ---------------------------------------------------------------------------
# Internal helpers — model cache
# ---------------------------------------------------------------------------


def _model_cache_dir() -> Path:
    """Return the effective ONNX model cache root directory.

    Returns the process-level override if one has been configured via
    :func:`configure_model_cache_dir` or :func:`init_db`; otherwise falls
    back to ``~/.cache/agent-takt/models``.
    """
    return (
        _model_cache_dir_override
        if _model_cache_dir_override is not None
        else Path.home() / ".cache" / "agent-takt" / "models"
    )


def configure_model_cache_dir(cache_dir: Path | None) -> None:
    """Set the ONNX model cache directory for this process.

    Call this before any embed operations when the default
    ``~/.cache/agent-takt/models`` location is unsuitable (e.g. CI
    environments without a writable home directory).  Pass ``None`` to
    revert to the default.
    """
    global _model_cache_dir_override  # noqa: PLW0603
    _model_cache_dir_override = cache_dir


def _local_model_dir() -> Path:
    """Return the local cache directory for the ONNX model files."""
    return _model_cache_dir() / _MODEL_REPO.replace("/", "--")


def _download_model() -> Path:
    """Download BAAI/bge-small-en-v1.5 ONNX model files to the cache.

    Idempotent — already-downloaded files are skipped.
    """
    dest = _local_model_dir()
    for rel in _MODEL_FILES:
        local = dest / rel
        local.parent.mkdir(parents=True, exist_ok=True)
        if local.exists():
            continue
        url = f"{_HF_BASE}/{_MODEL_REPO}/resolve/main/{rel}"
        logger.info("Downloading %s ...", url)
        urllib.request.urlretrieve(url, local)
        logger.info("Saved to %s", local)
    return dest


# ---------------------------------------------------------------------------
# Internal helpers — SQLite + sqlite-vec connection
# ---------------------------------------------------------------------------


def _load_sqlite_vec(conn: sqlite3.Connection) -> None:
    """Load the sqlite-vec extension into *conn*."""
    import sqlite_vec  # type: ignore[import]

    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)


def _open_conn(db_path: Path) -> sqlite3.Connection:
    """Open a SQLite connection with sqlite-vec loaded and row_factory set."""
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    _load_sqlite_vec(conn)
    return conn


# ---------------------------------------------------------------------------
# Internal helpers — embedding model
# ---------------------------------------------------------------------------


def _get_session_and_tokenizer() -> tuple[Any, Any]:
    """Return the cached (ort_session, tokenizer) pair, loading on first call."""
    global _ort_session, _hf_tokenizer  # noqa: PLW0603

    with _embed_lock:
        if _ort_session is None:
            import onnxruntime as ort  # type: ignore[import]

            _ort_session = ort.InferenceSession(
                str(_local_model_dir() / "onnx" / "model.onnx")
            )
        if _hf_tokenizer is None:
            from tokenizers import Tokenizer  # type: ignore[import]

            tok = Tokenizer.from_file(str(_local_model_dir() / "tokenizer.json"))
            # BGE is BERT-based; pad token id=0 ([PAD]), max length 512
            tok.enable_padding(pad_id=0, pad_token="[PAD]", length=512)
            tok.enable_truncation(max_length=512)
            _hf_tokenizer = tok

        return _ort_session, _hf_tokenizer


def _embed(text: str) -> bytes:
    """Return a serialised float32 embedding blob for *text* (for sqlite-vec)."""
    import numpy as np  # type: ignore[import]
    import sqlite_vec  # type: ignore[import]

    session, tokenizer = _get_session_and_tokenizer()

    encoded = tokenizer.encode(text)
    input_ids = np.array([encoded.ids], dtype=np.int64)
    attention_mask = np.array([encoded.attention_mask], dtype=np.int64)
    token_type_ids = np.zeros_like(input_ids)

    outputs = session.run(
        None,
        {
            "input_ids": input_ids,
            "attention_mask": attention_mask,
            "token_type_ids": token_type_ids,
        },
    )

    # outputs[0] may be (batch, seq_len, dim) or (batch, dim)
    token_embs = outputs[0].astype(np.float32)
    if token_embs.ndim == 3:
        # Mean-pool over non-padding tokens
        mask = attention_mask[..., np.newaxis].astype(np.float32)  # (1, seq, 1)
        pooled = (token_embs * mask).sum(axis=1) / mask.sum(axis=1).clip(min=1e-9)
    else:
        pooled = token_embs  # already (batch, dim)

    # L2-normalise
    norm = np.linalg.norm(pooled, axis=1, keepdims=True).clip(min=1e-9)
    vector: list[float] = (pooled / norm).flatten().tolist()
    return sqlite_vec.serialize_float32(vector)


# ---------------------------------------------------------------------------
# Internal helpers — text chunking
# ---------------------------------------------------------------------------


def _split_if_large(text: str) -> list[str]:
    """Split *text* at sentence boundaries when it exceeds *_CHUNK_MAX_CHARS*."""
    if len(text) <= _CHUNK_MAX_CHARS:
        return [text]
    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks: list[str] = []
    current: list[str] = []
    length = 0
    for sentence in sentences:
        if length + len(sentence) > _CHUNK_MAX_CHARS and current:
            chunks.append(" ".join(current))
            current = [sentence]
            length = len(sentence)
        else:
            current.append(sentence)
            length += len(sentence)
    if current:
        chunks.append(" ".join(current))
    return chunks


def _chunk_markdown(text: str) -> list[str]:
    """Split markdown at level-2 headings; further split oversized sections."""
    sections = re.split(r"(?m)^##\s", text)
    result: list[str] = []
    for section in sections:
        section = section.strip()
        if section:
            result.extend(_split_if_large(section))
    return result


def _chunk_text(text: str) -> list[str]:
    """Split text at paragraph boundaries; further split oversized paragraphs."""
    result: list[str] = []
    for para in text.split("\n\n"):
        para = para.strip()
        if para:
            result.extend(_split_if_large(para))
    return result


def _chunk_json(text: str) -> list[str]:
    """Split JSON content into chunks.

    - Arrays: each element becomes a chunk (JSON-encoded), further split if oversized.
    - Objects: each top-level key-value pair becomes a chunk, further split if oversized.
    - Scalars / parse errors: fall back to paragraph-boundary splitting on the raw text.
    """
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return _chunk_text(text)

    if isinstance(data, list):
        raw_chunks = [json.dumps(item, ensure_ascii=False) for item in data]
    elif isinstance(data, dict):
        raw_chunks = [
            json.dumps({k: v}, ensure_ascii=False) for k, v in data.items()
        ]
    else:
        return _chunk_text(text)

    result: list[str] = []
    for chunk in raw_chunks:
        result.extend(_split_if_large(chunk))
    return result


def _chunk_csv(text: str) -> list[str]:
    """Split CSV rows into chunks sized around *_CHUNK_MAX_CHARS*.

    The header row is prepended to every chunk so that column context is
    preserved when the chunk is later embedded.
    """
    import csv
    import io

    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        return []

    header = rows[0]
    header_line = ",".join(header)
    data_rows = rows[1:]

    result: list[str] = []
    current_lines: list[str] = [header_line]
    current_len = len(header_line)

    for row in data_rows:
        line = ",".join(row)
        # +1 for the newline separator
        if current_len + len(line) + 1 > _CHUNK_MAX_CHARS and len(current_lines) > 1:
            result.append("\n".join(current_lines))
            current_lines = [header_line, line]
            current_len = len(header_line) + len(line) + 1
        else:
            current_lines.append(line)
            current_len += len(line) + 1

    if len(current_lines) > 1:
        result.append("\n".join(current_lines))

    return result


def _chunk_file(path: Path) -> list[str]:
    """Return chunks from *path* appropriate to its file type.

    Supported types:
    - ``.md``  — level-2 heading splits, then oversized-section fallback
    - ``.txt`` — paragraph-boundary splits
    - ``.json``— per-element / per-key-value splits
    - ``.csv`` — row-group splits with header prepended to every chunk
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    suffix = path.suffix.lower()
    if suffix == ".md":
        return _chunk_markdown(text)
    if suffix == ".json":
        return _chunk_json(text)
    if suffix == ".csv":
        return _chunk_csv(text)
    # .txt and all other plain-text formats
    return _chunk_text(text)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def init_db(db_path: Path, model_cache_dir: Path | None = None) -> None:
    """Create the memory DB, enable WAL mode, load sqlite-vec, create schema.

    Idempotent — safe to call repeatedly on an already-initialised database.
    Also eagerly downloads the ONNX embedding model to the local cache so
    that the first ``add_entry`` / ``search`` call does not stall.

    Args:
        db_path: Path to the SQLite database file.
        model_cache_dir: Override the ONNX model cache directory.  When
            provided, this value is applied process-wide via
            :func:`configure_model_cache_dir` so that subsequent
            ``add_entry`` / ``search`` calls in the same process also use
            the configured location.  Defaults to
            ``~/.cache/agent-takt/models`` when ``None``.
    """
    configure_model_cache_dir(model_cache_dir)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = _open_conn(db_path)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS entries (
                rowid      INTEGER PRIMARY KEY AUTOINCREMENT,
                id         TEXT    UNIQUE NOT NULL,
                text       TEXT    NOT NULL,
                namespace  TEXT    NOT NULL,
                source     TEXT    NOT NULL DEFAULT '',
                metadata   TEXT    NOT NULL DEFAULT '{}',
                created_at TEXT    NOT NULL
            )
            """
        )
        conn.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS vectors USING vec0("
            f"embedding FLOAT[{_EMBEDDING_DIM}])"
        )
        conn.commit()
    finally:
        conn.close()

    _download_model()


def add_entry(
    db_path: Path,
    text: str,
    namespace: str = "global",
    source: str = "",
    metadata: dict | None = None,
) -> str:
    """Embed *text* and insert a new entry into the DB.

    Returns the UUID of the new entry.
    """
    entry_id = str(uuid.uuid4())
    embedding = _embed(text)
    meta_json = json.dumps(metadata or {})
    created_at = datetime.now(timezone.utc).isoformat()

    conn = _open_conn(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO entries (id, text, namespace, source, metadata, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (entry_id, text, namespace, source, meta_json, created_at),
        )
        conn.execute(
            "INSERT INTO vectors (rowid, embedding) VALUES (?, ?)",
            (cur.lastrowid, embedding),
        )
        conn.commit()
    finally:
        conn.close()

    return entry_id


def search(
    db_path: Path,
    query: str,
    namespace: str | None = None,
    limit: int = 5,
    threshold: float | None = None,
) -> list[dict]:
    """Semantic search over stored embeddings.

    If *namespace* is ``None``, searches all namespaces and returns a merged,
    distance-sorted result set.  Each result dict contains:
    ``id``, ``text``, ``namespace``, ``source``, ``distance``, ``metadata``.
    """
    embedding = _embed(query)
    # Over-fetch to allow post-filter by namespace when restricting
    fetch_limit = limit * 20 if namespace is not None else limit

    conn = _open_conn(db_path)
    try:
        rows = conn.execute(
            """
            SELECT e.id, e.text, e.namespace, e.source, e.metadata, v.distance
            FROM   vectors v
            JOIN   entries e ON e.rowid = v.rowid
            WHERE  v.embedding MATCH ? AND k = ?
            ORDER  BY v.distance
            """,
            (embedding, fetch_limit),
        ).fetchall()

        results: list[dict] = []
        for row in rows:
            if namespace is not None and row["namespace"] != namespace:
                continue
            dist = float(row["distance"])
            if threshold is not None and dist > threshold:
                break
            results.append(
                {
                    "id": row["id"],
                    "text": row["text"],
                    "namespace": row["namespace"],
                    "source": row["source"],
                    "distance": dist,
                    "metadata": json.loads(row["metadata"]),
                }
            )
            if len(results) >= limit:
                break

        return results
    finally:
        conn.close()


def ingest_file(
    db_path: Path,
    path: Path,
    namespace: str = "global",
    source: str = "",
) -> int:
    """Chunk and ingest *path* into the DB.

    Returns the number of entries added.  Skips chunks whose embedding is
    within *_DEDUP_THRESHOLD* of an existing entry in the same namespace so
    that re-ingestion is idempotent.
    """
    chunks = _chunk_file(path)
    added = 0
    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk:
            continue
        existing = search(db_path, chunk, namespace=namespace, limit=1)
        if existing and existing[0]["distance"] < _DEDUP_THRESHOLD:
            continue
        add_entry(db_path, chunk, namespace=namespace, source=source)
        added += 1
    return added


def delete_entry(db_path: Path, entry_id: str) -> None:
    """Delete the entry and its vector from the DB.

    Raises ``ValueError`` if *entry_id* does not exist.
    """
    conn = _open_conn(db_path)
    try:
        row = conn.execute(
            "SELECT rowid FROM entries WHERE id = ?", (entry_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Entry not found: {entry_id!r}")
        rowid = row["rowid"]
        conn.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
        conn.execute("DELETE FROM vectors WHERE rowid = ?", (rowid,))
        conn.commit()
    finally:
        conn.close()


def stats(db_path: Path) -> dict[str, Any]:
    """Return basic statistics about the memory database.

    Returns a dict with keys:
    - ``total_entries``: total number of stored entries
    - ``by_namespace``: mapping of namespace → count
    - ``db_path``: absolute path to the database file
    """
    conn = _open_conn(db_path)
    try:
        total: int = conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
        by_namespace: dict[str, int] = {
            row["namespace"]: row["cnt"]
            for row in conn.execute(
                "SELECT namespace, COUNT(*) AS cnt FROM entries GROUP BY namespace"
            ).fetchall()
        }
        return {
            "total_entries": total,
            "by_namespace": by_namespace,
            "db_path": str(db_path),
        }
    finally:
        conn.close()
