"""Core document handling (frontmatter and related helpers) for specs and ADRs."""

from .hooks import document_index_hook
from .index import (
    Chunk,
    chunk_document,
    configure_embedding_cache,
    document_index_db,
    ensure_index_schema,
    index_document,
    mark_stale,
    remove_document,
    resolve_title,
)

__all__ = [
    "Chunk",
    "chunk_document",
    "configure_embedding_cache",
    "document_index_db",
    "document_index_hook",
    "ensure_index_schema",
    "index_document",
    "mark_stale",
    "remove_document",
    "resolve_title",
]
