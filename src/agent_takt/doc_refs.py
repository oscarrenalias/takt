"""Parsing and resolution of ``spec:<id>`` / ``adr:<id>`` document references."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .documents.registry import get_store

__all__ = ["DocRef", "ResolvedDoc", "parse_ref", "resolve_ref", "resolve_entry", "normalize_linked_docs"]

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


def _worktree_doc(ref: DocRef, doc, workspace_root: Path) -> ResolvedDoc:
    try:
        path = doc.path.relative_to(workspace_root)
    except ValueError:
        path = doc.path
    return ResolvedDoc(ref, doc.id, _title(doc), doc.status, str(path), "worktree")


def _main_doc(ref: DocRef, doc) -> ResolvedDoc:
    return ResolvedDoc(ref, doc.id, _title(doc), doc.status, str(Path(doc.path).resolve()), "main")


def resolve_ref(ref: DocRef, workspace_root: str | Path, main_root: str | Path) -> ResolvedDoc:
    """Locate the referenced document in the worktree first, then the main checkout."""
    workspace_root = Path(workspace_root)
    doc = _lookup(workspace_root, ref)
    if doc is not None:
        return _worktree_doc(ref, doc, workspace_root)
    doc = _lookup(Path(main_root), ref)
    if doc is not None:
        return _main_doc(ref, doc)
    return ResolvedDoc(ref, ref.id, "", "", "", "missing")


def _load_docs(root: Path) -> list[tuple[str, object]]:
    docs: list[tuple[str, object]] = []
    for kind in _KINDS:
        try:
            docs.extend((kind, d) for d in get_store(root, kind).load_all())
        except (LookupError, OSError):
            continue
    return docs


def _match_path_entry(entry: str, root: Path) -> tuple[str, object] | None:
    """Match a legacy path *entry* to a spec/ADR document under *root*.

    Uses ``load_all()`` and exact filename stems (never ``store.resolve()``): an
    existing file matches by path; a missing ``.md`` path matches only when exactly
    one document has that filename stem.
    """
    if not isinstance(entry, str):
        return None
    text = entry.strip()
    if not text or parse_ref(text) is not None:
        return None
    candidate = Path(text)
    if not candidate.is_absolute():
        candidate = root / candidate
    docs = _load_docs(root)
    try:
        exists = candidate.is_file()
    except OSError:
        exists = False
    if exists:
        resolved = candidate.resolve()
        for kind, doc in docs:
            try:
                if doc.path.resolve() == resolved:
                    return kind, doc
            except OSError:
                continue
        return None
    if candidate.suffix.lower() != ".md":
        return None
    matches = [(k, d) for k, d in docs if d.path.stem == candidate.stem]
    return matches[0] if len(matches) == 1 else None


def resolve_entry(
    entry: str, workspace_root: str | Path, main_root: str | Path
) -> ResolvedDoc | None:
    """Resolve a ``spec:``/``adr:`` reference or a legacy spec/ADR path entry.

    Returns ``None`` for a path entry that does not identify exactly one document.
    """
    ref = parse_ref(entry)
    if ref is not None:
        return resolve_ref(ref, workspace_root, main_root)
    workspace_root = Path(workspace_root)
    match = _match_path_entry(entry, workspace_root)
    if match is not None:
        kind, doc = match
        return _worktree_doc(DocRef(kind, doc.id), doc, workspace_root)
    match = _match_path_entry(entry, Path(main_root))
    if match is not None:
        kind, doc = match
        return _main_doc(DocRef(kind, doc.id), doc)
    return None


def normalize_linked_docs(entries: list[str], root: str | Path) -> list[str]:
    """Convert spec/ADR path entries to ``spec:<id>``/``adr:<id>``; dedupe in order.

    References pass through; ambiguous or unmatched entries are left unchanged.
    """
    root = Path(root)
    out: list[str] = []
    seen: set[str] = set()
    for entry in entries or []:
        value = entry
        if isinstance(entry, str) and parse_ref(entry) is None:
            match = _match_path_entry(entry, root)
            if match is not None:
                kind, doc = match
                value = f"{kind}:{doc.id}"
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out
