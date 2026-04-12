from __future__ import annotations

import argparse
from pathlib import Path

from ...console import ConsoleReporter
from ...memory import add_entry, delete_entry, ingest_file, init_db, search, stats
from ...storage import RepositoryStorage

_MIGRATE_GLOB = "docs/memory/*.md"


def command_memory(
    args: argparse.Namespace,
    storage: RepositoryStorage,
    console: ConsoleReporter,
) -> int:
    """Dispatch to `takt memory` sub-subcommands."""
    db_path = storage.root / ".takt" / "memory" / "memory.db"

    if args.memory_command == "init":
        return _cmd_init(db_path, console)
    if args.memory_command == "add":
        return _cmd_add(args, db_path, console)
    if args.memory_command == "search":
        return _cmd_search(args, db_path, console)
    if args.memory_command == "ingest":
        return _cmd_ingest(args, db_path, storage.root, console)
    if args.memory_command == "delete":
        return _cmd_delete(args, db_path, console)
    if args.memory_command == "stats":
        return _cmd_stats(db_path, console)
    return 1


# ---------------------------------------------------------------------------
# Sub-command handlers
# ---------------------------------------------------------------------------


def _cmd_init(db_path: Path, console: ConsoleReporter) -> int:
    with console.spin("Initialising memory database") as spinner:
        init_db(db_path)
        spinner.success(f"Memory database ready at {db_path}")
    return 0


def _cmd_add(
    args: argparse.Namespace,
    db_path: Path,
    console: ConsoleReporter,
) -> int:
    if not db_path.exists():
        console.error(f"Memory database not found at {db_path}. Run `takt memory init` first.")
        return 1
    entry_id = add_entry(
        db_path,
        args.text,
        namespace=args.namespace,
        source=args.source,
    )
    console.dump_json({"entry_id": entry_id, "namespace": args.namespace})
    return 0


def _cmd_search(
    args: argparse.Namespace,
    db_path: Path,
    console: ConsoleReporter,
) -> int:
    if not db_path.exists():
        console.error(f"Memory database not found at {db_path}. Run `takt memory init` first.")
        return 1
    results = search(
        db_path,
        args.query,
        namespace=args.namespace,
        limit=args.limit,
        threshold=args.threshold,
    )
    console.dump_json(results)
    return 0


def _cmd_ingest(
    args: argparse.Namespace,
    db_path: Path,
    project_root: Path,
    console: ConsoleReporter,
) -> int:
    if not db_path.exists():
        console.error(f"Memory database not found at {db_path}. Run `takt memory init` first.")
        return 1

    if args.migrate:
        # Migrate docs/memory/*.md → global namespace
        memory_docs_dir = project_root / "docs" / "memory"
        if not memory_docs_dir.is_dir():
            console.warn(f"No docs/memory/ directory found at {memory_docs_dir}; nothing to migrate.")
            return 0
        md_files = sorted(memory_docs_dir.glob("*.md"))
        if not md_files:
            console.warn(f"No .md files found in {memory_docs_dir}; nothing to migrate.")
            return 0
        total = 0
        for md_file in md_files:
            with console.spin(f"Ingesting {md_file.name}") as spinner:
                count = ingest_file(
                    db_path,
                    md_file,
                    namespace="global",
                    source="migrate",
                )
                total += count
                spinner.success(f"{md_file.name}: {count} chunk(s) added")
        console.dump_json({"migrated_files": len(md_files), "entries_added": total})
        return 0

    # Single-file ingest
    if not args.path:
        console.error("Provide a file path or use --migrate to migrate docs/memory/*.md.")
        return 1

    path = Path(args.path)
    if not path.exists():
        console.error(f"File not found: {path}")
        return 1

    with console.spin(f"Ingesting {path.name}") as spinner:
        count = ingest_file(
            db_path,
            path,
            namespace=args.namespace,
            source=args.source,
        )
        spinner.success(f"{count} chunk(s) added from {path.name}")
    console.dump_json({"path": str(path), "entries_added": count})
    return 0


def _cmd_delete(
    args: argparse.Namespace,
    db_path: Path,
    console: ConsoleReporter,
) -> int:
    if not db_path.exists():
        console.error(f"Memory database not found at {db_path}. Run `takt memory init` first.")
        return 1
    try:
        delete_entry(db_path, args.entry_id)
    except ValueError as exc:
        console.error(str(exc))
        return 1
    console.success(f"Deleted entry {args.entry_id}")
    return 0


def _cmd_stats(db_path: Path, console: ConsoleReporter) -> int:
    if not db_path.exists():
        console.error(f"Memory database not found at {db_path}. Run `takt memory init` first.")
        return 1
    result = stats(db_path)
    console.dump_json(result)
    return 0
