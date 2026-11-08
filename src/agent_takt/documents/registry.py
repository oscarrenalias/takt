"""Registry of document types and the ``get_store`` factory."""
from __future__ import annotations

from pathlib import Path

from .adr_type import ADR_TYPE
from .spec_type import SPEC_TYPE
from .store import DocumentStore
from .types import DocumentType

__all__ = ["DOCUMENT_TYPES", "get_store", "get_type"]

DOCUMENT_TYPES: dict[str, DocumentType] = {
    SPEC_TYPE.name: SPEC_TYPE,
    ADR_TYPE.name: ADR_TYPE,
}


def get_type(name: str) -> DocumentType:
    """Return the registered ``DocumentType`` called *name*; ``KeyError`` if unknown."""
    try:
        return DOCUMENT_TYPES[name]
    except KeyError:
        raise KeyError(f"unknown document type {name!r}") from None


def get_store(root: str | Path, name: str) -> DocumentStore:
    """Return a ``DocumentStore`` for the document type *name* under *root*."""
    return DocumentStore(root, get_type(name))
