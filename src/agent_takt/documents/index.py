"""Document index storage: DB location and schema.

The index lives in the shared memory database (``memory.db``) in dedicated
``doc_index_*`` tables, and never touches the memory ``entries``/``vectors``
tables.  Embedding access goes through the ``agent_takt.memory`` module
attribute so tests can patch ``agent_takt.memory._embed`` / ``_download_model``.
"""

from __future__ import annotations

import os
from pathlib import Path

from .. import memory
from ..config import load_config
from ..paths import main_checkout_root

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
