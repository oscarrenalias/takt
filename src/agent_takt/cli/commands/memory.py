from __future__ import annotations

import argparse
from pathlib import Path

from ...config import load_config
from ...console import ConsoleReporter
from ...memory import add_entry, configure_model_cache_dir, delete_entry, ingest_file, init_db, search, stats
from ...storage import RepositoryStorage


def command_memory(
    args: argparse.Namespace,
    storage: RepositoryStorage,
    console: ConsoleReporter,
) -> int:
    """Dispatch to `takt memory` sub-subcommands."""
    db_path = storage.root / ".takt" / "memory" / "memory.db"

    # Apply the configured model cache directory before any embed operations so
    # that all subcommands (not just `init`) resolve the model from the right path.
    _config = load_config(storage.root)
    configure_model_cache_dir(_config.common.memory_cache_dir)

    if args.memory_command == "init":
        return _cmd_init(db_path, _config.common.memory_cache_dir, console)
    if args.memory_command == "add":
        return _cmd_add(args, db_path, console)
    if args.memory_command == "search":
        return _cmd_search(args, db_path, console)
    if args.memory_command == "ingest":
        return _cmd_ingest(args, db_path, console)
    if args.memory_command == "delete":
        return _cmd_delete(args, db_path, console)
    if args.memory_command == "stats":
        return _cmd_stats(db_path, console)
    return 1


# ---------------------------------------------------------------------------
# Sub-command handlers
# ---------------------------------------------------------------------------


def _cmd_init(db_path: Path, model_cache_dir: Path | None, console: ConsoleReporter) -> int:
    with console.spin("Initialising memory database") as spinner:
        init_db(db_path, model_cache_dir=model_cache_dir)
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
    console: ConsoleReporter,
) -> int:
    if not db_path.exists():
        console.error(f"Memory database not found at {db_path}. Run `takt memory init` first.")
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
