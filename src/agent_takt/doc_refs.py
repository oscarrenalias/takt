"""Parsing and resolution of ``spec:<id>`` / ``adr:<id>`` document references."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .documents.registry import get_store

__all__ = ["DocRef", "ResolvedDoc", "parse_ref", "resolve_ref"]

_KINDS = ("spec", "adr")


@dataclass(frozen=True)
class DocRef:
    kind: str
    id: str


@dataclass(frozen=True)
class ResolvedDoc:
    ref: DocRef
    id: str
    title: str
    status: str
    path: str
    source: str  # 'worktree' | 'main' | 'missing'


def parse_ref(entry: str) -> DocRef | None:
    """Return a ``DocRef`` for ``spec:<id>`` / ``adr:<id>``; ``None`` for anything else."""
    if not isinstance(entry, str):
        return None
    kind, sep, doc_id = entry.strip().partition(":")
    kind = kind.lower()
    doc_id = doc_id.strip()
    if not sep or kind not in _KINDS or not doc_id:
        return None
    return DocRef(kind, doc_id)


def _lookup(root: Path, ref: DocRef):
    try:
        return get_store(root, ref.kind).resolve(ref.id)
    except (LookupError, OSError):
        return None


def _title(doc) -> str:
    fm = doc.frontmatter or {}
    return str(fm.get("title") or fm.get("name") or "")


def resolve_ref(ref: DocRef, workspace_root: str | Path, main_root: str | Path) -> ResolvedDoc:
    """Locate the referenced document in the worktree first, then the main checkout."""
    workspace_root = Path(workspace_root)
    doc = _lookup(workspace_root, ref)
    if doc is not None:
        try:
            path = doc.path.relative_to(workspace_root)
        except ValueError:
            path = doc.path
        return ResolvedDoc(ref, doc.id, _title(doc), doc.status, str(path), "worktree")
    doc = _lookup(Path(main_root), ref)
    if doc is not None:
        return ResolvedDoc(ref, doc.id, _title(doc), doc.status, str(Path(doc.path).resolve()), "main")
    return ResolvedDoc(ref, ref.id, "", "", "", "missing")
