from __future__ import annotations

import argparse
import sys

from ...adr import AdrStore, _parse_frontmatter
from ...console import ConsoleReporter
from ...storage import RepositoryStorage
from ..formatting import format_adr_field, format_adr_list_plain
from ..services import make_adr_store


def command_adr(
    args: argparse.Namespace,
    storage: RepositoryStorage,
    console: ConsoleReporter,
) -> int:
    adr_store = make_adr_store(storage.root)
    if args.adr_command == "new":
        return _cmd_new(args, adr_store, console)
    if args.adr_command == "list":
        return _cmd_list(args, adr_store, console)
    if args.adr_command == "show":
        return _cmd_show(args, adr_store, console)
    if args.adr_command == "approve":
        raise NotImplementedError("takt adr approve is not yet implemented")
    if args.adr_command == "reject":
        raise NotImplementedError("takt adr reject is not yet implemented")
    if args.adr_command == "supersede":
        raise NotImplementedError("takt adr supersede is not yet implemented")
    if args.adr_command == "validate":
        raise NotImplementedError("takt adr validate is not yet implemented")
    return 1


def _cmd_new(
    args: argparse.Namespace,
    adr_store: AdrStore,
    console: ConsoleReporter,
) -> int:
    # Resolve and validate --supersedes references (must exist, status not required to be approved)
    supersedes: list[str] = []
    for ref in list(args.supersedes or []):
        try:
            full_id = adr_store.resolve_prefix(ref)
        except ValueError:
            console.error(f"Referenced ADR not found: {ref!r}")
            return 1
        try:
            target = adr_store.find_by_id(full_id)
        except KeyError:
            console.error(f"Referenced ADR not found: {ref!r}")
            return 1
        supersedes.append(full_id)
        console.detail(f"Declares supersession of: {full_id} (validated to exist, status={target.status})")

    try:
        adr = adr_store.new_adr(
            args.title,
            description=getattr(args, "description", None),
            tags=list(args.tag or []),
            related_specs=list(getattr(args, "related_spec", None) or []),
            related_beads=list(getattr(args, "related_bead", None) or []),
            supersedes=supersedes,
        )
    except FileNotFoundError as exc:
        console.error(str(exc))
        return 1

    try:
        path = adr_store.find_file_by_id(adr.id)
        rel_path = path.relative_to(adr_store.root)
    except (KeyError, ValueError):
        rel_path = None

    console.success(f"Created {rel_path} ({adr.id})" if rel_path else f"Created ADR {adr.id}")
    return 0


def _cmd_list(
    args: argparse.Namespace,
    adr_store: AdrStore,
    console: ConsoleReporter,
) -> int:
    statuses = list(args.status_filter or []) or None
    tags = list(args.tag_filter or []) or None
    adrs = adr_store.list_adrs(statuses=statuses, tags=tags)

    if getattr(args, "output_json", False):
        console.dump_json([a.to_dict() for a in adrs])
        return 0

    # --plain and default both render the table
    console.emit(format_adr_list_plain(adrs))
    return 0


def _cmd_show(
    args: argparse.Namespace,
    adr_store: AdrStore,
    console: ConsoleReporter,
) -> int:
    try:
        adr_id = adr_store.resolve_prefix(args.adr_id)
    except ValueError as exc:
        console.error(str(exc))
        return 1

    try:
        path = adr_store.find_file_by_id(adr_id)
    except KeyError as exc:
        console.error(str(exc))
        return 1

    adr = adr_store.find_by_id(adr_id)
    text = path.read_text(encoding="utf-8")
    _, body = _parse_frontmatter(text)

    field = getattr(args, "field", None)
    if field:
        try:
            value = format_adr_field(adr, field, body=body)
            console.emit(value)
            return 0
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1

    console.dump_json(adr.to_dict())
    return 0
