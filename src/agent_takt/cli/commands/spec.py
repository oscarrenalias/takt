"""``takt spec`` command handlers (init, create, list, show, set, migrate, remove).

Dispatched before ``make_services()`` so it never creates ``.takt/``.
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

from ...console import ConsoleReporter
from ...documents.frontmatter import SPEC_STYLE
from ...documents.registry import get_store
from ...documents.spec_type import (
    SPEC_BODY_TEMPLATE,
    TEMPLATE_FILE,
    extract_requirements_section,
    parse_requirement_entries,
)
from ...documents.store import AmbiguousDocument, DocumentNotFound, DocumentStore
from ...documents.types import Document
from ..formatting import _resolve_path, format_bead_field

_SHOW_BODY_LINES = 20


def command_spec(args: argparse.Namespace, console: ConsoleReporter) -> int:
    root = Path(getattr(args, "root", None) or ".").resolve()
    cmd = args.spec_command
    if cmd == "init":
        return _cmd_init(root, console)
    if cmd in ("search", "reindex"):
        from ..doc_search import run_reindex, run_search

        return (run_search if cmd == "search" else run_reindex)(args, console, "spec")
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
    if cmd == "set":
        return _cmd_set(args, store, console)
    if cmd == "migrate":
        return _cmd_migrate(args, store, console)
    if cmd == "remove":
        return _cmd_remove(args, store, console)
    if cmd == "validate":
        return _cmd_validate(args, store, console)
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


_PRIORITIES = ("high", "medium", "low")
_SET_FIELD_KEYS = {"feature-root": "feature_root_id", "tags": "tags", "priority": "priority", "description": "description"}


def _resolve_or_error(store: DocumentStore, query: str) -> Document | None:
    try:
        return store.resolve(query)
    except DocumentNotFound as exc:
        print(f'error: no spec matching "{exc.query}"', file=sys.stderr)
    except AmbiguousDocument as exc:
        print(f'error: "{exc.query}" matches multiple specs: {", ".join(exc.matches)}', file=sys.stderr)
    return None


def _cmd_set(args: argparse.Namespace, store: DocumentStore, console: ConsoleReporter) -> int:
    field = args.spec_set_field
    doc = _resolve_or_error(store, args.spec)
    if doc is None:
        return 1
    if doc.legacy:
        print(f"error: {_rel(store.root, doc.path)} is a legacy spec — run 'takt spec migrate' first", file=sys.stderr)
        return 1
    value: object = args.value
    if field == "status":
        try:
            updated = store.set_status(doc.id, args.value)
        except (ValueError, FileExistsError, OSError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        console.emit(f"{updated.id} -> {updated.status}: {_rel(store.root, updated.path)}")
        for op, capability, requirement in getattr(store, "last_merged", None) or []:
            console.emit(f"merged: {op} {capability} / {requirement}")
        return 0
    if field == "priority" and args.value not in _PRIORITIES:
        print(f"error: invalid priority {args.value!r}; allowed: {', '.join(_PRIORITIES)}", file=sys.stderr)
        return 1
    if field == "tags":
        value = [t.strip() for t in args.value.split(",") if t.strip()]
    try:
        updated = store.set_field(doc.id, _SET_FIELD_KEYS[field], value)
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    console.emit(f"Updated {field} of {updated.id}")
    return 0


def _cmd_migrate(args: argparse.Namespace, store: DocumentStore, console: ConsoleReporter) -> int:
    doc = _resolve_or_error(store, args.spec)
    if doc is None:
        return 1
    rel = _rel(store.root, doc.path)
    err = store.errors.get(str(doc.path))
    if not doc.legacy or err is not None:
        print(f"error: {rel} already has frontmatter" if err is None else f"error: {rel} has malformed frontmatter ({err}); fix it manually", file=sys.stderr)
        return 1
    try:
        text = doc.path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print(f"error: cannot read {rel}: {exc}", file=sys.stderr)
        return 1
    dt = store.doc_type
    status = dt.status_for_folder(doc.path.parent.name) or "draft"
    fm = dict(dt.default_frontmatter)
    fm["id"] = f"{dt.id_prefix}{uuid.uuid4().hex[:8]}"
    fm["name"] = _display_name(doc)
    fm["status"] = status
    doc.path.write_text(dt.frontmatter_style.render(fm, text), encoding="utf-8")
    console.emit(f"Migrated {rel}")
    console.emit(f"ID: {fm['id']}")
    console.emit(f"Status: {status}")
    return 0


def _cmd_remove(args: argparse.Namespace, store: DocumentStore, console: ConsoleReporter) -> int:
    doc = _resolve_or_error(store, args.spec)
    if doc is None:
        return 1
    status = doc.status
    if doc.legacy:
        status = store.doc_type.status_for_folder(doc.path.parent.name) or doc.status
    rel = _rel(store.root, doc.path)
    if status != "draft" and not args.force:
        if not sys.stdin.isatty():
            print(f"refusing to delete a {status} spec without --force", file=sys.stderr)
            return 1
        try:
            answer = input(f"Delete {status} spec {rel}? [y/N] ")
        except EOFError:
            answer = ""
        if answer.strip().lower() not in ("y", "yes"):
            console.emit("Aborted.")
            return 1
    try:
        store.remove(doc.id)
    except (OSError, LookupError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    console.emit(f"Removed {rel}")
    return 0


def _cmd_validate(args: argparse.Namespace, store: DocumentStore, console: ConsoleReporter) -> int:
    query = getattr(args, "spec", None)
    docs = store.load_all()
    if query:
        candidate = Path(query)
        target: Document | None = None
        if candidate.suffix == ".md" or candidate.exists():
            try:
                wanted = candidate.resolve()
            except OSError:
                wanted = candidate
            target = next((d for d in docs if d.path.resolve() == wanted), None)
            if target is None:
                print(f"error: {query} is not a spec under {_rel(store.root, store.root_path)}", file=sys.stderr)
                return 1
        else:
            try:
                target = store.resolve(query, docs)
            except DocumentNotFound as exc:
                print(f'error: no spec matching "{exc.query}"', file=sys.stderr)
                return 1
            except AmbiguousDocument as exc:
                print(f'error: "{exc.query}" matches multiple specs: {", ".join(exc.matches)}', file=sys.stderr)
                return 1
        docs = [target]

    findings: dict[str, list] = {}
    for item in store.validate():
        findings.setdefault(str(item.path), []).append(item)

    failed = False
    for doc in docs:
        items = findings.get(str(doc.path), [])
        rel = _rel(store.root, doc.path)
        has_error = False
        for item in items:
            if item.severity == "error":
                has_error = True
                print(f"error: {rel}: {item.message}", file=sys.stderr)
            else:
                print(f"warning: {rel}: {item.message}", file=sys.stderr)
        if has_error:
            failed = True
            continue
        section = None if doc.legacy else extract_requirements_section(doc.body)
        count = len([e for e in parse_requirement_entries(section) if e.well_formed]) if section else 0
        console.emit(f"OK: {rel} ({count} requirement deltas)")
    return 1 if failed else 0
