"""Generic document store: read API plus create/set_field/set_status/remove/validate.

Hook signatures (all optional on the ``DocumentType``):

* ``on_create(store, doc)`` – called before the new file is written; may mutate
  ``doc.frontmatter`` / ``doc.body``.
* ``before_transition(store, doc, new_status)`` – called before a status move;
  raise ``ValueError`` to reject the transition.
* ``after_transition(store, doc, old_status)`` – called after the move is written.
* ``validate_document(store, doc)`` – returns ``list[ValidationError]``.
"""
from __future__ import annotations

import re
import subprocess
import uuid
from pathlib import Path
from typing import Any, Iterable

from .frontmatter import (
    FrontmatterError,
    read_frontmatter_file,
    render_document,
)
from .types import Document, DocumentType, ValidationError

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


    # ------------------------------------------------------------------
    # Write side
    # ------------------------------------------------------------------

    @staticmethod
    def _slugify(title: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")
        return slug or "untitled"

    def _notify(self, doc_id: str, path: Path) -> None:
        self.doc_type.on_document_changed(self.doc_type, doc_id, path)

    def _commit(self, message: str) -> None:
        """One git commit covering the type's root dir. Non-fatal on failure."""
        if not self.doc_type.auto_commit:
            return
        try:
            subprocess.run(
                ["git", "add", "-A", "--", str(self.root_path)],
                cwd=self.root, capture_output=True, check=True, timeout=30,
            )
            subprocess.run(
                ["git", "commit", "-m", f"{self.doc_type.name.upper()} lifecycle: {message}"],
                cwd=self.root, capture_output=True, check=True, timeout=30,
            )
        except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired):
            pass

    def _resolve_one(self, doc_id: str) -> Document:
        return self.resolve(doc_id)

    def create(self, title: str, **fields: Any) -> Document:
        """Create a new document in its initial status folder and return it."""
        dt = self.doc_type
        hex_part = uuid.uuid4().hex[:8]
        doc_id = f"{dt.id_prefix}{hex_part}"
        slug = self._slugify(title)
        fm: dict[str, Any] = dict(dt.default_frontmatter)
        if "id" not in fm:
            fm = {"id": doc_id, **fm}
        fm["id"] = doc_id
        if "name" in fm and "name" not in fields:
            fm["name"] = title
        elif "title" in fm and "title" not in fields:
            fm["title"] = title
        for key, value in fields.items():
            fm[key] = value
        status = fm.get("status")
        if not status:
            status = next(iter(dt.statuses), None)
            if status is None:
                raise ValueError(f"{dt.name} has no statuses; cannot create documents")
            if "status" in dt.default_frontmatter or "status" in fields:
                fm["status"] = status
        folder = dt.folder_for_status(str(status))

        if dt.filename_builder is not None:
            filename = dt.filename_builder(id=doc_id, hex=hex_part, slug=slug, title=title)
        else:
            filename = dt.filename_pattern.format(id=doc_id, hex=hex_part, slug=slug, title=title)
        body = dt.template
        for key, val in (("{name}", title), ("{title}", title), ("{id}", doc_id)):
            body = body.replace(key, val)

        path = self.root_path / folder / filename
        doc = Document(
            id=doc_id, type=dt, status=str(status), path=path,
            frontmatter=fm, body=body, legacy=False,
        )
        if dt.on_create is not None:
            dt.on_create(self, doc)
        if path.exists():
            raise FileExistsError(f"file already exists: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        content = dt.render_new(self, doc) if dt.render_new is not None else None
        if content is None:
            content = render_document(doc.frontmatter, doc.body, dt.frontmatter_style)
        path.write_text(content, encoding="utf-8")
        self._notify(doc.id, path)
        self._commit(f"created {doc.id}")
        return doc

    def set_field(self, doc_id: str, field: str, value: Any) -> Document:
        """Set a frontmatter field; the file is rewritten only if the value changes."""
        if field == "status":
            raise ValueError("use set_status to change the status field")
        doc = self._resolve_one(doc_id)
        if doc.legacy:
            raise ValueError(f"{doc.path}: document has no valid frontmatter")
        cur = doc.frontmatter.get(field)
        if field in doc.frontmatter and cur == value and type(cur) is type(value):
            return doc
        style = self.doc_type.frontmatter_style
        text = doc.path.read_text(encoding="utf-8")
        new_text = style.update(text, {field: value}, doc.path)
        if new_text == text:
            return doc
        doc.path.write_text(new_text, encoding="utf-8")
        doc.frontmatter[field] = value
        self._notify(doc.id, doc.path)
        self._commit(f"updated {doc.id} {field}")
        return doc

    def set_status(self, doc_id: str, status: str) -> Document:
        """Update the status field and move the file to the mapped folder."""
        dt = self.doc_type
        doc = self._resolve_one(doc_id)
        if doc.legacy:
            raise ValueError(f"{doc.path}: document has no valid frontmatter")
        folder = dt.folder_for_status(status)  # raises on reserved/invalid
        old_status = doc.status
        if status == old_status:
            return doc
        if not dt.is_transition_allowed(old_status, status):
            raise ValueError(
                f"transition from {old_status!r} to {status!r} is not allowed for {dt.name}"
            )
        if dt.before_transition is not None:
            dt.before_transition(self, doc, status)

        style = dt.frontmatter_style
        text = doc.path.read_text(encoding="utf-8")
        new_text = style.update(text, {"status": status}, doc.path)
        new_path = self.root_path / folder / doc.path.name
        if new_path != doc.path and new_path.exists():
            raise FileExistsError(f"file already exists: {new_path}")
        old_path = doc.path
        new_path.parent.mkdir(parents=True, exist_ok=True)
        new_path.write_text(new_text, encoding="utf-8")
        if new_path != old_path:
            try:
                old_path.unlink()
            except OSError:
                new_path.unlink(missing_ok=True)
                raise
        doc.path = new_path
        doc.status = status
        doc.frontmatter["status"] = status
        if dt.after_transition is not None:
            dt.after_transition(self, doc, old_status)
        self._notify(doc.id, new_path)
        self._commit(f"{doc.id} {old_status} -> {status}")
        return doc

    def apply(
        self,
        changes: Iterable[tuple[str, str | None, dict[str, Any] | None]],
    ) -> list[Document]:
        """All-or-nothing multi-document transition.

        Each change is ``(id, new_status_or_None, field_updates_or_None)``. Every
        change is validated first (status, transition rules, ``before_transition``
        hooks, path collisions) before anything is written. New files are then
        written, originals removed, and ``after_transition`` hooks run; any error
        restores every original byte-for-byte and deletes newly created files and
        directories. ``on_document_changed`` fires once per affected document, and
        a single commit is made, only after the whole transaction succeeds.
        Documents whose change is a no-op are returned but not notified.
        """
        dt = self.doc_type
        docs = self.load_all()
        plans: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        new_paths: set[Path] = set()
        old_paths: set[Path] = set()

        # ---- Phase 1: validate everything, write nothing ----
        for change in changes:
            doc_id, new_status, fields = change
            fields = dict(fields or {})
            if "status" in fields:
                raise ValueError("use the status element of the change to change status")
            doc = self.resolve(doc_id, docs)
            if doc.id in seen_ids:
                raise ValueError(f"duplicate change for document {doc.id}")
            seen_ids.add(doc.id)
            if doc.legacy:
                raise ValueError(f"{doc.path}: document has no valid frontmatter")
            old_status = doc.status
            moving = new_status is not None and new_status != old_status
            new_path = doc.path
            if moving and new_status is not None:
                folder = dt.folder_for_status(new_status)  # raises on reserved/invalid
                if not dt.is_transition_allowed(old_status, new_status):
                    raise ValueError(
                        f"transition from {old_status!r} to {new_status!r} "
                        f"is not allowed for {dt.name}"
                    )
                if dt.before_transition is not None:
                    dt.before_transition(self, doc, new_status)
                new_path = self.root_path / folder / doc.path.name
            updates = dict(fields)
            if moving:
                updates["status"] = new_status
            text = doc.path.read_text(encoding="utf-8")
            new_text = dt.frontmatter_style.update(text, updates, doc.path) if updates else text
            if not moving and new_text == text:
                plans.append({"doc": doc, "noop": True})
                continue
            plans.append({
                "doc": doc, "noop": False, "moving": moving, "old_status": old_status,
                "new_status": new_status, "fields": fields, "old_path": doc.path,
                "new_path": new_path, "text": text, "new_text": new_text,
            })
            old_paths.add(doc.path)
            if moving:
                if new_path in new_paths:
                    raise FileExistsError(f"file already exists: {new_path}")
                new_paths.add(new_path)
        for plan in plans:
            if plan["noop"] or not plan["moving"]:
                continue
            np_ = plan["new_path"]
            if np_.exists() and np_ not in old_paths:
                raise FileExistsError(f"file already exists: {np_}")
            if np_ in old_paths and np_ != plan["old_path"]:
                raise FileExistsError(f"file already exists: {np_}")

        active = [p for p in plans if not p["noop"]]
        if not active:
            return [p["doc"] for p in plans]

        # ---- Phase 2: write with rollback ----
        backups: dict[Path, bytes] = {p["old_path"]: p["old_path"].read_bytes() for p in active}
        created_files: list[Path] = []
        created_dirs: list[Path] = []
        try:
            for plan in active:
                target = plan["new_path"]
                if plan["moving"]:
                    missing: list[Path] = []
                    parent = target.parent
                    while not parent.exists():
                        missing.append(parent)
                        parent = parent.parent
                    target.parent.mkdir(parents=True, exist_ok=True)
                    created_dirs.extend(reversed(missing))
                    created_files.append(target)
                target.write_text(plan["new_text"], encoding="utf-8")
            for plan in active:
                if plan["moving"]:
                    plan["old_path"].unlink()
            for plan in active:
                doc = plan["doc"]
                if plan["moving"]:
                    doc.path = plan["new_path"]
                    doc.status = plan["new_status"]
                    doc.frontmatter["status"] = plan["new_status"]
                doc.frontmatter.update(plan["fields"])
                if plan["moving"] and dt.after_transition is not None:
                    dt.after_transition(self, doc, plan["old_status"])
        except BaseException:
            for path in created_files:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
            for path, data in backups.items():
                try:
                    if not path.exists() or path.read_bytes() != data:
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(data)
                except OSError:
                    pass
            for d in reversed(created_dirs):
                try:
                    d.rmdir()
                except OSError:
                    pass
            for plan in active:  # undo in-memory mutation
                doc = plan["doc"]
                doc.path = plan["old_path"]
                doc.status = plan["old_status"]
                if plan["moving"]:
                    doc.frontmatter["status"] = plan["old_status"]
            raise

        # ---- Phase 3: committed; notify once per affected document ----
        for plan in active:
            self._notify(plan["doc"].id, plan["doc"].path)
        self._commit("applied " + ", ".join(p["doc"].id for p in active))
        return [p["doc"] for p in plans]

    def remove(self, doc_id: str) -> Document:
        """Delete the document file."""
        doc = self._resolve_one(doc_id)
        doc.path.unlink()
        self._notify(doc.id, doc.path)
        self._commit(f"removed {doc.id}")
        return doc

    def validate(self, doc_id: str | None = None) -> list[ValidationError]:
        """Validate one document (or all); malformed files become errors."""
        docs = self.load_all()
        if doc_id is not None:
            docs = [self.resolve(doc_id, docs)]
        results: list[ValidationError] = []
        for doc in docs:
            err = self.errors.get(str(doc.path))
            if err is not None:
                results.append(ValidationError(path=str(doc.path), message=err.message))
                continue
            if doc.legacy:
                continue
            hook = self.doc_type.validate_document
            if hook is None:
                continue
            try:
                found = hook(self, doc) or []
            except Exception as exc:  # a broken hook must not stop other files
                found = [ValidationError(path=str(doc.path), message=f"validation failed: {exc}")]
            results.extend(found)
        return results
