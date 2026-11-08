"""``on_document_changed`` hook that keeps the document index fresh.

The hook is deliberately cheap: it only marks a document stale (or removes it)
in an *existing* index database on the main checkout. It never embeds, loads the
embedding model, or creates ``.takt/`` or the database; ``takt spec|adr reindex``
does the actual indexing. Any failure is reported as a single stderr warning and
never affects the document write.
"""

from __future__ import annotations

import sys
from pathlib import Path

from .frontmatter import FrontmatterError, read_frontmatter_file
from .types import Document

_LEGACY_STATUS = "legacy"


def document_index_hook(doc_type, doc_id: str, path: Path) -> None:
    """Mark *doc_id* stale in the index, or drop it when its file is gone."""
    try:
        from . import index  # lazy: avoids import cycles and startup cost
        from ..paths import main_checkout_root

        path = Path(path)
        root = path.parent.parent.parent
        main = main_checkout_root(root)
        if root.resolve() != main.resolve():
            return
        db_path = index.document_index_db(root)
        if not db_path.exists():
            return

        if not path.exists():
            index.remove_document(db_path, doc_type, doc_id)
            return

        try:
            status = doc_type.status_for_folder(path.parent.name)
        except ValueError:
            status = None
        try:
            fm, body = read_frontmatter_file(path)
            fm = dict(fm or {})
        except (FrontmatterError, OSError, UnicodeDecodeError):
            fm, body = {}, ""
        doc = Document(
            id=doc_id, type=doc_type, status=status or _LEGACY_STATUS, path=path,
            frontmatter=fm, body=body, legacy=not fm,
        )
        title = index.resolve_title(doc)
        tags = fm.get("tags") or []
        if not isinstance(tags, (list, tuple)):
            tags = [tags]
        index.mark_stale(db_path, doc_type, doc_id, path, doc.status, title, tags)
    except Exception as exc:  # noqa: BLE001 - hook must never break the write
        print(f"warning: document index not updated for {doc_id}: {exc}", file=sys.stderr)
