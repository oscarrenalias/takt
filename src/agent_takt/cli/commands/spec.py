"""``takt spec`` command handlers (init, create, list, show).

Dispatched before ``make_services()`` so it never creates ``.takt/``.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ...console import ConsoleReporter
from ...documents.frontmatter import SPEC_STYLE
from ...documents.registry import get_store
from ...documents.spec_type import SPEC_BODY_TEMPLATE, TEMPLATE_FILE
from ...documents.store import AmbiguousDocument, DocumentNotFound, DocumentStore
from ...documents.types import Document
from ..formatting import _resolve_path, format_bead_field

_SHOW_BODY_LINES = 20


def command_spec(args: argparse.Namespace, console: ConsoleReporter) -> int:
    root = Path(getattr(args, "root", None) or ".").resolve()
    cmd = args.spec_command
    if cmd == "init":
        return _cmd_init(root, console)
    store = get_store(root, "spec")
    if not store.root_path.is_dir():
        print("error: specs/ not found — run 'takt spec init' first", file=sys.stderr)
        return 1
    if cmd == "create":
        return _cmd_create(args, store, console)
    if cmd == "list":
        return _cmd_list(args, store, console)
    if cmd == "show":
        return _cmd_show(args, store, console)
    print(f"error: unknown spec command {cmd!r}", file=sys.stderr)
    return 1


def _rel(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _cmd_init(root: Path, console: ConsoleReporter) -> int:
    specs = root / "specs"
    if specs.exists():
        print("error: specs/ already exists — remove it manually if you want to reinitialise", file=sys.stderr)
        return 1
    for folder in ("drafts", "planned", "done"):
        (specs / folder).mkdir(parents=True)
    (specs / TEMPLATE_FILE).write_text(SPEC_BODY_TEMPLATE, encoding="utf-8")
    console.emit(f"Initialised specs/ in {root}")
    console.emit(f"Default template: {_rel(root, specs / TEMPLATE_FILE)}")
    return 0


def _cmd_create(args: argparse.Namespace, store: DocumentStore, console: ConsoleReporter) -> int:
    try:
        doc = store.create(args.title)
    except FileExistsError as exc:
        print(f"error: spec {exc}", file=sys.stderr)
        return 1
    console.emit(f"Created {_rel(store.root, doc.path)}")
    console.emit(f"ID: {doc.id}")
    return 0


def _display_name(doc: Document) -> str:
    name = doc.frontmatter.get("name")
    if name:
        return str(name)
    try:
        text = doc.path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        text = doc.body
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return doc.path.stem.replace("-", " ").replace("_", " ").title()


def _tags(doc: Document) -> list[str]:
    raw = doc.frontmatter.get("tags") or []
    if isinstance(raw, (list, tuple)):
        return [str(t) for t in raw]
    return [str(raw)]


def _cmd_list(args: argparse.Namespace, store: DocumentStore, console: ConsoleReporter) -> int:
    docs = store.list(
        status=getattr(args, "status", None),
        tags=list(getattr(args, "tag", None) or []),
        priority=getattr(args, "priority", None),
    )
    for err in store.errors.values():
        print(f"warning: {err}", file=sys.stderr)

    rows = [
        {
            "id": d.id,
            "name": _display_name(d),
            "status": d.status,
            "priority": d.frontmatter.get("priority") or None,
            "tags": _tags(d),
            "path": _rel(store.root, d.path),
        }
        for d in docs
    ]
    if getattr(args, "output_json", False):
        console.dump_json(rows)
        return 0
    if not rows:
        console.emit("No specs found.")
        return 0
    table = [
        (r["id"], r["status"], str(r["priority"] or "—"), r["name"]) for r in rows
    ]
    headers = ("id", "status", "priority", "name")
    widths = [max(len(headers[i]), *(len(t[i]) for t in table)) for i in range(3)]
    fmt = f"{{:<{widths[0]}}}  {{:<{widths[1]}}}  {{:<{widths[2]}}}  {{}}"
    console.emit(fmt.format(*headers))
    for row in table:
        console.emit(fmt.format(*row))
    return 0


def _cmd_show(args: argparse.Namespace, store: DocumentStore, console: ConsoleReporter) -> int:
    try:
        doc = store.resolve(args.spec)
    except DocumentNotFound as exc:
        print(f'error: no spec matching "{exc.query}"', file=sys.stderr)
        return 1
    except AmbiguousDocument as exc:
        print(f'error: "{exc.query}" matches multiple specs: {", ".join(exc.matches)}', file=sys.stderr)
        return 1

    rel = _rel(store.root, doc.path)
    if doc.legacy:
        try:
            text = doc.path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            text = doc.body
        err = store.errors.get(str(doc.path))
        reason = f"malformed frontmatter ({err})" if err else "no frontmatter"
        print(f"warning: {rel} is a legacy spec — {reason}", file=sys.stderr)
        if getattr(args, "output_json", False):
            console.dump_json(
                {"id": doc.id, "status": doc.status, "path": rel, "frontmatter": {}, "body": text}
            )
            return 0
        if getattr(args, "field", None):
            print(f"field not found: {args.field}", file=sys.stderr)
            return 1
        sys.stdout.write(text)
        return 0

    field = getattr(args, "field", None)
    if field:
        try:
            console.emit(format_bead_field(_resolve_path(doc.frontmatter, field)))
        except (KeyError, IndexError, TypeError):
            print(f"field not found: {field}", file=sys.stderr)
            return 1
        return 0

    if getattr(args, "output_json", False):
        console.dump_json(
            {
                "id": doc.id,
                "status": doc.status,
                "path": rel,
                "frontmatter": doc.frontmatter,
                "body": doc.body,
            }
        )
        return 0

    if getattr(args, "body_only", False):
        sys.stdout.write(doc.body)
        return 0

    sys.stdout.write(SPEC_STYLE.render(doc.frontmatter, "").rstrip("\n") + "\n")
    shown = 0
    for line in doc.body.splitlines():
        if line.rstrip() == "---":
            break
        if not args.full and shown >= _SHOW_BODY_LINES:
            break
        console.emit(line)
        shown += 1
    return 0
