from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

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
        return _cmd_approve(args, adr_store, console)
    if args.adr_command == "reject":
        return _cmd_reject(args, adr_store, console)
    if args.adr_command == "supersede":
        return _cmd_supersede(args, adr_store, console)
    if args.adr_command == "validate":
        return _cmd_validate(args, adr_store, console)
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


def _git_commit_adr_lifecycle(root: Path, message: str) -> None:
    """Stage adr/ changes and commit. Non-fatal on failure."""
    adr_dir = root / "adr"
    try:
        subprocess.run(
            ["git", "add", str(adr_dir)],
            cwd=root,
            capture_output=True,
            check=True,
            timeout=30,
        )
        subprocess.run(
            ["git", "commit", "-m", f"ADR lifecycle: {message}"],
            cwd=root,
            capture_output=True,
            check=True,
            timeout=30,
        )
    except (subprocess.CalledProcessError, OSError):
        pass


def _cmd_approve(
    args: argparse.Namespace,
    adr_store: AdrStore,
    console: ConsoleReporter,
) -> int:
    try:
        adr_id = adr_store.resolve_prefix(args.adr_id)
    except ValueError as exc:
        console.error(str(exc))
        return 1

    supersedes_ids: list[str] = []
    for ref in list(args.supersedes or []):
        try:
            sid = adr_store.resolve_prefix(ref)
        except ValueError as exc:
            console.error(str(exc))
            return 1
        supersedes_ids.append(sid)

    try:
        main_old_file = adr_store.find_file_by_id(adr_id)
    except KeyError as exc:
        console.error(str(exc))
        return 1

    superseded_old_files: dict[str, Path] = {}
    for sid in supersedes_ids:
        try:
            superseded_old_files[sid] = adr_store.find_file_by_id(sid)
        except KeyError as exc:
            console.error(str(exc))
            return 1

    try:
        adr = adr_store.approve(adr_id, supersedes=supersedes_ids)
    except ValueError as exc:
        console.error(str(exc))
        return 1

    approved_dir = adr_store.adr_dir / "approved"
    new_file = approved_dir / main_old_file.name
    try:
        old_rel = main_old_file.relative_to(adr_store.root)
        new_rel = new_file.relative_to(adr_store.root)
    except ValueError:
        old_rel, new_rel = main_old_file, new_file

    console.success(f"Transitioned {adr_id} to approved")
    console.detail(f"Moved {old_rel} → {new_rel.parent}/")
    console.detail(f"Set accepted_at: {adr.accepted_at}")

    for sid in supersedes_ids:
        old_sup_file = superseded_old_files[sid]
        superseded_dir = adr_store.adr_dir / "superseded"
        try:
            old_sup_rel = old_sup_file.relative_to(adr_store.root)
            sup_dir_rel = superseded_dir.relative_to(adr_store.root)
        except ValueError:
            old_sup_rel = old_sup_file
            sup_dir_rel = superseded_dir
        console.success(f"Transitioned {sid} to superseded (replaced by {adr_id})")
        console.detail(f"Moved {old_sup_rel} → {sup_dir_rel}/")
        console.detail(f"Set {sid} superseded_at: {adr.accepted_at}")
        console.detail(f"Set {sid} superseded_by: {adr_id}")

    supersedes_part = f" (supersedes {', '.join(supersedes_ids)})" if supersedes_ids else ""
    _git_commit_adr_lifecycle(adr_store.root, f"approved {adr_id}{supersedes_part}")
    return 0


def _cmd_reject(
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
        old_file = adr_store.find_file_by_id(adr_id)
    except KeyError as exc:
        console.error(str(exc))
        return 1

    try:
        adr_store.reject(adr_id)
    except ValueError as exc:
        console.error(str(exc))
        return 1

    rejected_dir = adr_store.adr_dir / "rejected"
    new_file = rejected_dir / old_file.name
    try:
        old_rel = old_file.relative_to(adr_store.root)
        new_rel = new_file.relative_to(adr_store.root)
    except ValueError:
        old_rel, new_rel = old_file, new_file

    console.success(f"Transitioned {adr_id} to rejected")
    console.detail(f"Moved {old_rel} → {new_rel.parent}/")

    _git_commit_adr_lifecycle(adr_store.root, f"rejected {adr_id}")
    return 0


def _cmd_supersede(
    args: argparse.Namespace,
    adr_store: AdrStore,
    console: ConsoleReporter,
) -> int:
    try:
        old_id = adr_store.resolve_prefix(args.adr_id)
    except ValueError as exc:
        console.error(str(exc))
        return 1

    try:
        new_id = adr_store.resolve_prefix(args.by_adr_id)
    except ValueError as exc:
        console.error(str(exc))
        return 1

    try:
        old_file = adr_store.find_file_by_id(old_id)
    except KeyError as exc:
        console.error(str(exc))
        return 1

    try:
        adr = adr_store.supersede(old_id, new_id)
    except ValueError as exc:
        console.error(str(exc))
        return 1

    superseded_dir = adr_store.adr_dir / "superseded"
    new_file = superseded_dir / old_file.name
    try:
        old_rel = old_file.relative_to(adr_store.root)
        new_rel = new_file.relative_to(adr_store.root)
    except ValueError:
        old_rel, new_rel = old_file, new_file

    console.success(f"Transitioned {old_id} to superseded (replaced by {new_id})")
    console.detail(f"Moved {old_rel} → {new_rel.parent}/")
    console.detail(f"Set superseded_at: {adr.superseded_at}")
    console.detail(f"Set superseded_by: {new_id}")

    _git_commit_adr_lifecycle(adr_store.root, f"superseded {old_id} by {new_id}")
    return 0


def _cmd_validate(
    _args: argparse.Namespace,
    adr_store: AdrStore,
    console: ConsoleReporter,
) -> int:
    errors = adr_store.validate_all()
    if not errors:
        console.success("All ADRs valid.")
        return 0
    for err in errors:
        console.emit(f"{err.adr_id}: {err.message}")
    return 1
