"""ADR store: a thin typed wrapper over the generic document core.

All frontmatter reading/writing, file moves, rollback and body checks live in
``agent_takt.documents``; this module keeps the ``Adr`` dataclass as a typed view
and the CLI-facing error messages.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .documents.adr_type import (
    ADR_STATUSES as _ADR_STATUS_TUPLE,
    ADR_TYPE,
    TEMPLATE_PATH,
    adr_validate_document,
    check_body_sections as _check_body_sections,
)
from .documents.frontmatter import ADR_STYLE, read_frontmatter, split_frontmatter
from .documents.store import DocumentStore
from .documents.types import Document

ADR_DRAFT = "draft"
ADR_APPROVED = "approved"
ADR_SUPERSEDED = "superseded"
ADR_REJECTED = "rejected"

ADR_STATUSES = set(_ADR_STATUS_TUPLE)

# The CLI layer owns git commits for ADR transitions (one commit per command),
# so the document store itself must not auto-commit.
_ADR_DOC_TYPE = dataclasses.replace(ADR_TYPE, auto_commit=False)


@dataclass
class Adr:
    id: str
    title: str
    status: str
    created_at: str
    authors: list[str]
    description: str | None = None
    accepted_at: str | None = None
    superseded_at: str | None = None
    superseded_by: str | None = None
    supersedes: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    related_specs: list[str] = field(default_factory=list)
    related_beads: list[str] = field(default_factory=list)
    review_after: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "status": self.status,
            "created_at": self.created_at,
            "authors": self.authors,
            "description": self.description,
            "accepted_at": self.accepted_at,
            "superseded_at": self.superseded_at,
            "superseded_by": self.superseded_by,
            "supersedes": self.supersedes,
            "tags": self.tags,
            "related_specs": self.related_specs,
            "related_beads": self.related_beads,
            "review_after": self.review_after,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Adr":
        # PyYAML parses ISO timestamps as datetime objects; coerce to str explicitly
        raw_created_at = data.get("created_at", "") or ""
        if hasattr(raw_created_at, "isoformat"):
            created_at_str = raw_created_at.isoformat()
        else:
            created_at_str = str(raw_created_at)
        return cls(
            id=data["id"],
            title=data["title"],
            status=data["status"],
            created_at=created_at_str,
            authors=list(data.get("authors") or []),
            description=data.get("description"),
            accepted_at=data.get("accepted_at"),
            superseded_at=data.get("superseded_at"),
            superseded_by=data.get("superseded_by"),
            supersedes=list(data.get("supersedes") or []),
            tags=list(data.get("tags") or []),
            related_specs=list(data.get("related_specs") or []),
            related_beads=list(data.get("related_beads") or []),
            review_after=data.get("review_after"),
        )


@dataclass
class ValidationError:
    adr_id: str
    message: str


def _allocate_id() -> str:
    import uuid

    return f"ADR-{uuid.uuid4().hex[:8]}"


def _slugify(title: str) -> str:
    return DocumentStore._slugify(title)


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Return ``(frontmatter_dict, body)``; ``({}, text)`` without frontmatter."""
    fm, body = read_frontmatter(text)
    if fm is None:
        return {}, text
    return fm, body.lstrip("\n")


def _render_frontmatter(data: dict[str, Any]) -> str:
    """Frontmatter block text (without delimiters) as written by the ADR style."""
    raw, _ = split_frontmatter(ADR_STYLE.render(data, ""))
    return raw or ""


class AdrStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.adr_dir = self.root / "adr"
        self._store = DocumentStore(self.root, _ADR_DOC_TYPE)

    # -- loading ---------------------------------------------------------

    def _docs(self) -> list[Document]:
        return [d for d in self._store.load_all() if not d.legacy]

    @staticmethod
    def _to_adr(doc: Document) -> Adr:
        return Adr.from_dict(doc.frontmatter)

    def _adr_docs(self) -> list[tuple[Adr, Document]]:
        out: list[tuple[Adr, Document]] = []
        for doc in self._docs():
            try:
                out.append((self._to_adr(doc), doc))
            except (KeyError, TypeError):
                pass
        return out

    def _load_file(self, path: Path) -> Adr:
        return self._load_file_with_body(path)[0]

    def _load_file_with_body(self, path: Path) -> tuple[Adr, str]:
        fm, body = _parse_frontmatter(path.read_text(encoding="utf-8"))
        return Adr.from_dict(fm), body

    def read_body(self, path: Path) -> str:
        """Return the markdown body (without frontmatter) of an ADR file."""
        return _parse_frontmatter(path.read_text(encoding="utf-8"))[1]

    def _save_file(self, path: Path, adr: Adr, body: str) -> None:
        path.write_text(ADR_STYLE.render(adr.to_dict(), body), encoding="utf-8")

    def load_all(self) -> list[Adr]:
        return [adr for adr, _ in self._adr_docs()]

    def _find_doc(self, adr_id: str, what: str) -> tuple[Adr, Document]:
        for adr, doc in self._adr_docs():
            if adr.id == adr_id:
                return adr, doc
        raise KeyError(f"{what}: {adr_id}")

    def find_by_id(self, adr_id: str) -> Adr:
        return self._find_doc(adr_id, "ADR not found")[0]

    def find_file_by_id(self, adr_id: str) -> Path:
        """Return the filesystem path for an ADR by full ID."""
        return self._find_doc(adr_id, "ADR file not found")[1].path

    def resolve_prefix(self, prefix: str) -> str:
        """Resolve a partial ADR ID prefix to a full ID.

        Accepts the full ID, "ADR-<hex>", or a bare hex prefix like "a3f1".
        Raises ValueError on zero or multiple matches.
        """
        prefix_lower = prefix.lower()
        if not prefix_lower.startswith("adr-"):
            prefix_lower = f"adr-{prefix_lower}"
        matches = [adr.id for adr in self.load_all() if adr.id.lower().startswith(prefix_lower)]
        if not matches:
            raise ValueError(f"No ADR matches prefix: {prefix!r}")
        if len(matches) > 1:
            joined = ", ".join(sorted(matches))
            raise ValueError(f"Ambiguous ADR prefix {prefix!r}: matches {joined}")
        return matches[0]

    def list_adrs(
        self,
        statuses: list[str] | None = None,
        tags: list[str] | None = None,
    ) -> list[Adr]:
        """Return ADRs filtered by status (OR) and tags (AND)."""
        results = self.load_all()
        if statuses:
            status_set = set(statuses)
            results = [a for a in results if a.status in status_set]
        if tags:
            tag_set = set(tags)
            results = [a for a in results if tag_set.issubset(set(a.tags))]
        return results

    # -- creation --------------------------------------------------------

    def new_adr(
        self,
        title: str,
        *,
        description: str | None = None,
        tags: list[str] | None = None,
        related_specs: list[str] | None = None,
        related_beads: list[str] | None = None,
        supersedes: list[str] | None = None,
    ) -> Adr:
        """Create a new draft ADR in adr/drafts/ via the document core.

        Raises FileNotFoundError if the ADR template does not exist.
        """
        template_path = self.root.joinpath(*TEMPLATE_PATH)
        if not template_path.exists():
            raise FileNotFoundError(
                f"ADR template not found at {template_path}. "
                "Run 'takt init' or 'takt upgrade' to install it."
            )
        doc = self._store.create(
            title,
            description=description,
            tags=list(tags or []),
            related_specs=list(related_specs or []),
            related_beads=list(related_beads or []),
            supersedes=list(supersedes or []),
        )
        return self._to_adr(doc)

    # -- transitions -----------------------------------------------------

    def approve(self, adr_id: str, supersedes: list[str] | None = None) -> Adr:
        """Transition a draft ADR to approved.

        Each ADR in *supersedes* must currently be approved and is transitioned to
        superseded in the same ``DocumentStore.apply`` transaction; on any failure
        no file is modified.
        """
        supersedes_ids = list(dict.fromkeys(supersedes or []))

        adr, _ = self._find_doc(adr_id, "ADR file not found")
        if adr.status != ADR_DRAFT:
            raise ValueError(
                f"ADR {adr_id} cannot be approved: status is {adr.status!r} (must be 'draft')"
            )
        for target_id in supersedes_ids:
            target = self._find_doc(target_id, "ADR file not found")[0]
            if target.status != ADR_APPROVED:
                raise ValueError(
                    f"Cannot supersede {target_id}: status is {target.status!r} (must be 'approved')"
                )

        main_fields: dict[str, Any] | None = None
        if supersedes_ids:
            all_supersedes = list(adr.supersedes)
            all_supersedes += [s for s in supersedes_ids if s not in all_supersedes]
            main_fields = {"supersedes": all_supersedes}
        changes: list[tuple[str, str | None, dict[str, Any] | None]] = [
            (adr_id, ADR_APPROVED, main_fields)
        ]
        changes += [(sid, ADR_SUPERSEDED, {"superseded_by": adr_id}) for sid in supersedes_ids]
        docs = self._store.apply(changes)
        return self._to_adr(docs[0])

    def reject(self, adr_id: str) -> Adr:
        """Transition a draft ADR to rejected."""
        adr, _ = self._find_doc(adr_id, "ADR file not found")
        if adr.status != ADR_DRAFT:
            raise ValueError(
                f"ADR {adr_id} cannot be rejected: status is {adr.status!r} (must be 'draft')"
            )
        docs = self._store.apply([(adr_id, ADR_REJECTED, None)])
        return self._to_adr(docs[0])

    def supersede(self, old_id: str, new_id: str) -> Adr:
        """Standalone transition: move an approved ADR to superseded.

        old_id must be approved; new_id must exist and be approved.
        """
        old_adr, _ = self._find_doc(old_id, "ADR file not found")
        if old_adr.status != ADR_APPROVED:
            raise ValueError(
                f"ADR {old_id} cannot be superseded: status is {old_adr.status!r} (must be 'approved')"
            )
        new_adr, _ = self._find_doc(new_id, "ADR file not found")
        if new_adr.status != ADR_APPROVED:
            raise ValueError(
                f"Replacement ADR {new_id} is not approved: status is {new_adr.status!r}"
            )
        docs = self._store.apply([(old_id, ADR_SUPERSEDED, {"superseded_by": new_id})])
        return self._to_adr(docs[0])

    # -- validation ------------------------------------------------------

    def validate_all(self) -> list[ValidationError]:
        """Validate every ADR via ``DocumentStore.validate`` and return legacy-shaped errors."""
        store = self._store
        results = store.validate()
        out: list[ValidationError] = []
        for r in results:
            msg = r.message
            if "frontmatter is not a YAML mapping" in msg:
                msg = "malformed YAML frontmatter: not a mapping"
            elif msg.startswith("malformed YAML frontmatter"):
                msg = " ".join(msg.split())
                # FrontmatterError text is prefixed with the path; drop it.
                msg = msg[msg.index("malformed YAML frontmatter"):]
            elif msg.startswith(r.path + ": "):
                msg = msg[len(r.path) + 2:]
            out.append(ValidationError(r.path, msg))
        # Files without any frontmatter are reported like ADRs with no fields.
        for doc in store.load_all():
            if doc.legacy and str(doc.path) not in store.errors:
                for r in adr_validate_document(store, dataclasses.replace(doc, frontmatter={})):
                    out.append(ValidationError(r.path, r.message))
        return out
