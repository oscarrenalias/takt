"""Generic document store: read-side API (load, list, resolve, get)."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from .frontmatter import FrontmatterError, read_frontmatter_file
from .types import Document, DocumentType

__all__ = [
    "AmbiguousDocument",
    "DocumentNotFound",
    "DocumentStore",
    "LEGACY_STATUS",
]

LEGACY_STATUS = "legacy"


class DocumentNotFound(LookupError):
    """No document matched *query*."""

    def __init__(self, query: str, doc_type: str = "document") -> None:
        self.query = query
        self.doc_type = doc_type
        super().__init__(f"no {doc_type} matching {query!r}")


class AmbiguousDocument(LookupError):
    """*query* matched several documents; ``matches`` lists every matching ID."""

    def __init__(self, query: str, matches: list[str], doc_type: str = "document") -> None:
        self.query = query
        self.matches = list(matches)
        self.doc_type = doc_type
        super().__init__(f"{query!r} matches multiple {doc_type}s: {', '.join(self.matches)}")


class DocumentStore:
    """Read-side access to documents of one ``DocumentType`` under *root*."""

    def __init__(self, root: str | Path, doc_type: DocumentType) -> None:
        self.root = Path(root)
        self.doc_type = doc_type
        # Frontmatter parse errors from the last load_all(), keyed by str(path).
        self.errors: dict[str, FrontmatterError] = {}

    @property
    def root_path(self) -> Path:
        return self.root / self.doc_type.root_dir

    def _load_file(self, path: Path, status: str | None) -> Document:
        error: FrontmatterError | None = None
        fm: dict[str, Any] | None = None
        body = ""
        try:
            fm, body = read_frontmatter_file(path)
        except FrontmatterError as exc:
            error = exc
        except (OSError, UnicodeDecodeError) as exc:
            error = FrontmatterError(path, f"cannot read file: {exc}")
        if error is not None:
            self.errors[str(path)] = error
        if not fm or error is not None:
            return Document(
                id=path.stem, type=self.doc_type, status=LEGACY_STATUS, path=path,
                frontmatter={}, body=body, legacy=True,
            )
        raw_id = fm.get("id")
        doc_id = str(raw_id) if raw_id not in (None, "") else path.stem
        return Document(
            id=doc_id, type=self.doc_type, status=status or LEGACY_STATUS, path=path,
            frontmatter=dict(fm), body=body, legacy=False,
        )

    def load_all(self) -> list[Document]:
        """Load every document in status folders; reserved folders are skipped.

        Never raises for malformed files: they come back as legacy documents and the
        parse error is recorded in ``self.errors``.
        """
        self.errors = {}
        docs: list[Document] = []
        base = self.root_path
        if not base.is_dir():
            return docs
        for folder in sorted(p for p in base.iterdir() if p.is_dir()):
            status = self.doc_type.status_for_folder(folder.name)
            if status is None:
                continue
            for path in sorted(folder.glob("*.md")):
                if path.is_file():
                    docs.append(self._load_file(path, status))
        return docs

    def list(
        self,
        status: str | None = None,
        tags: Iterable[str] | None = None,
        priority: str | None = None,
    ) -> list[Document]:
        """Documents filtered by status, priority and tags (all tags must match)."""
        wanted = [str(t) for t in (tags or [])]
        result: list[Document] = []
        for doc in self.load_all():
            if status is not None and doc.status != status:
                continue
            if priority is not None and str(doc.frontmatter.get("priority") or "") != priority:
                continue
            if wanted:
                raw = doc.frontmatter.get("tags") or []
                have = {str(t) for t in raw} if isinstance(raw, (list, tuple)) else {str(raw)}
                if not all(t in have for t in wanted):
                    continue
            result.append(doc)
        return result

    def resolve(self, query: str, docs: list[Document] | None = None) -> Document:
        """Resolve *query* to one document per the type's resolution mode."""
        docs = self.load_all() if docs is None else docs
        name = self.doc_type.name
        q = (query or "").strip().lower()
        if not q:
            raise DocumentNotFound(query, name)

        exact = [d for d in docs if d.id.lower() == q]
        if len(exact) == 1:
            return exact[0]
        if len(exact) > 1:
            raise AmbiguousDocument(query, [d.id for d in exact], name)

        prefixes = [q]
        if self.doc_type.resolution == "id_prefix_or_filename":
            full = self.doc_type.id_prefix.lower() + q
            if full != q:
                prefixes.append(full)
        matched = [d for d in docs if any(d.id.lower().startswith(p) for p in prefixes)]
        if len(matched) == 1:
            return matched[0]
        if len(matched) > 1:
            raise AmbiguousDocument(query, [d.id for d in matched], name)

        if self.doc_type.resolution == "id_prefix_or_filename":
            by_name = [d for d in docs if q in d.path.stem.lower()]
            if len(by_name) == 1:
                return by_name[0]
            if len(by_name) > 1:
                raise AmbiguousDocument(query, [d.id for d in by_name], name)
        raise DocumentNotFound(query, name)

    def get(self, doc_id: str) -> Document:
        """Return the document whose ID equals *doc_id* (case-insensitive)."""
        wanted = (doc_id or "").lower()
        matches = [d for d in self.load_all() if d.id.lower() == wanted]
        if not matches:
            raise DocumentNotFound(doc_id, self.doc_type.name)
        if len(matches) > 1:
            raise AmbiguousDocument(doc_id, [d.id for d in matches], self.doc_type.name)
        return matches[0]
