"""Shared ``search`` / ``reindex`` command helpers for document types (specs, ADRs)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..console import ConsoleReporter
from ..paths import main_checkout_root


def _rel_to_main(main_root: Path, path: str) -> str:
    try:
        return str(Path(path).relative_to(main_root))
    except ValueError:
        return str(path)


def _prepare(root: Path, create_db: bool):
    """Return ``(main_root, db_path)`` or ``None`` after printing the missing-project error."""
    from ..documents import index

    main_root = main_checkout_root(root)
    if not (main_root / ".takt").is_dir():
        print(
            f"error: no takt project at {root}; document search needs .takt/",
            file=sys.stderr,
        )
        return None
    db_path = index.document_index_db(root)
    if create_db:
        index.configure_embedding_cache(root)
        if not db_path.exists():
            from .. import memory

            memory.init_db(db_path)
        index.ensure_index_schema(db_path)
    return main_root, db_path


def run_search(args: argparse.Namespace, console: ConsoleReporter, type_name: str) -> int:
    from ..documents import index

    root = Path(getattr(args, "root", None) or ".").resolve()
    prepared = _prepare(root, create_db=True)
    if prepared is None:
        return 1
    main_root, db_path = prepared
    index.sync_index(root, db_path, doc_types=(type_name,))
    hits = index.search(
        db_path,
        args.query,
        type_name,
        statuses=list(args.status or []),
        tags=list(args.tag or []),
        limit=args.limit,
        per_document=not args.chunks,
    )
    if getattr(args, "output_json", False):
        console.dump_json(
            [
                {
                    "id": h.id,
                    "type": h.type,
                    "status": h.status,
                    "title": h.title,
                    "path": _rel_to_main(main_root, h.path),
                    "section": h.section,
                    "snippet": h.snippet,
                    "score": h.score,
                }
                for h in hits
            ]
        )
        return 0
    if not hits:
        console.emit("No matches.")
        return 0
    for h in hits:
        if getattr(args, "plain", False):
            console.emit(
                "\t".join(
                    [h.id, h.status or "", h.section or "", f"{h.score:.4f}", h.title or ""]
                )
            )
        else:
            console.emit(f"{h.id}  {h.status or ''}  {h.title or ''}")
            console.emit(f"  § {h.section or ''} · {h.score:.4f}")
            console.emit(f"  {h.snippet}")
    return 0


def run_reindex(args: argparse.Namespace, console: ConsoleReporter, type_name: str) -> int:
    from .. import memory
    from ..documents import index

    root = Path(getattr(args, "root", None) or ".").resolve()
    check = getattr(args, "check", False)
    prepared = _prepare(root, create_db=False)
    if prepared is None:
        return 1
    _main_root, db_path = prepared
    if check:
        r = index.sync_index(root, db_path, doc_types=(type_name,), dry_run=True)
        console.emit(
            f"would add {r.added}, update {r.updated}, remove {r.removed} ({r.unchanged} unchanged)"
        )
        return 0

    index.configure_embedding_cache(root)
    if not db_path.exists():
        memory.init_db(db_path)
    index.ensure_index_schema(db_path)
    for doc_id in list(index._existing_rows(db_path, type_name)):
        index.remove_document(db_path, type_name, doc_id)
    r = index.sync_index(root, db_path, doc_types=(type_name,))
    n = r.added + r.updated
    conn = memory._open_conn(db_path)
    try:
        m = conn.execute(
            "SELECT COUNT(*) FROM doc_index_chunks WHERE doc_type = ?", (type_name,)
        ).fetchone()[0]
    finally:
        conn.close()
    console.emit(f"indexed {n} {type_name} documents ({m} chunks)")
    return 0
