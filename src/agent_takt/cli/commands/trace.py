"""``takt trace`` command group: inspect captured agent session traces."""
from __future__ import annotations

import argparse
import json
from typing import Any, Callable

from ...console import ConsoleReporter
from ...storage import RepositoryStorage
from ...traces import ROLE_MAIN, TRACE_STATUSES, TraceRecordMeta
from ...traces.store import PLANNER_DIR, TraceStore

_INPUT_EXCERPT_CHARS = 120
_TEXT_EXCERPT_CHARS = 160
_LIST_COLUMNS = ("BEAD", "SEQ", "AGENT", "ROLE", "STATUS", "TOOLS", "STARTED")


def _squash(text: str) -> str:
    return " ".join(str(text).split())


def _clip(text: str, limit: int | None) -> str:
    text = _squash(text)
    if limit is not None and len(text) > limit:
        return text[: limit - 1] + "…"
    return text


# ------------------------------------------------------------------- list
def _collect_records(
    storage: RepositoryStorage,
    store: TraceStore,
    *,
    bead_id: str | None,
    feature_root_id: str | None,
    status: str | None,
) -> list[TraceRecordMeta]:
    tree_ids: set[str] | None = None
    if feature_root_id is not None:
        tree_ids = {
            bead.bead_id
            for bead in storage.list_beads()
            if (storage.feature_root_id_for(bead) or bead.bead_id) == feature_root_id
        }
    if bead_id is not None:
        bead_dirs: list[str] = [bead_id]
    elif store.traces_dir.is_dir():
        bead_dirs = sorted(p.name for p in store.traces_dir.iterdir() if p.is_dir())
    else:
        bead_dirs = []
    records: list[TraceRecordMeta] = []
    for name in bead_dirs:
        key = None if name == PLANNER_DIR else name
        for record in store.list_records(key):
            if tree_ids is not None:
                in_tree = (record.bead_id in tree_ids) or record.feature_root_id == feature_root_id
                if not in_tree:
                    continue
            if status is not None and record.status != status:
                continue
            records.append(record)
    return records


def _record_row(record: TraceRecordMeta) -> list[str]:
    return [
        record.bead_id or PLANNER_DIR,
        str(record.seq),
        record.agent_type or "-",
        record.role or "-",
        record.status or "-",
        "-" if record.tool_calls is None else str(record.tool_calls),
        record.started_at or "-",
    ]


def _command_list(args: argparse.Namespace, storage: RepositoryStorage, console: ConsoleReporter) -> int:
    store = TraceStore(storage.root)
    bead_id: str | None = None
    feature_root_id: str | None = None
    if args.bead:
        try:
            bead_id = storage.resolve_bead_id(args.bead)
        except ValueError as exc:
            console.error(str(exc))
            return 1
    if args.feature_root:
        from .bead import _resolve_feature_root_id

        try:
            feature_root_id = _resolve_feature_root_id(storage, args.feature_root)
        except ValueError as exc:
            console.error(str(exc))
            return 1
        if feature_root_id is None:
            console.error(f"No feature root found matching '{args.feature_root}'")
            return 1
    records = _collect_records(
        storage, store, bead_id=bead_id, feature_root_id=feature_root_id, status=args.status
    )
    if args.json:
        print(json.dumps([r.to_dict() for r in records], indent=2))
        return 0
    if not records:
        console.info("No trace records found.")
        return 0
    rows = [_record_row(r) for r in records]
    if args.plain:
        for row in rows:
            print("\t".join(row))
        return 0
    widths = [max(len(_LIST_COLUMNS[i]), *(len(row[i]) for row in rows)) for i in range(len(_LIST_COLUMNS))]
    print("  ".join(col.ljust(widths[i]) for i, col in enumerate(_LIST_COLUMNS)).rstrip())
    for row in rows:
        print("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip())
    return 0


# ------------------------------------------------------------------- show
def _input_summary(tool: str, tool_input: Any) -> str:
    if isinstance(tool_input, dict):
        for key in ("command", "file_path", "path", "pattern", "url", "query", "description"):
            value = tool_input.get(key)
            if isinstance(value, str) and value:
                return value
        return json.dumps(tool_input, ensure_ascii=False)
    if tool_input is None:
        return ""
    return str(tool_input)


def _format_event(event: dict[str, Any], *, full: bool, truncate: bool) -> list[str]:
    kind = event.get("kind", "?")
    ts = event.get("ts") or event.get("started_at") or "-"
    prefix = f"[{ts}] {kind}"
    in_limit = _INPUT_EXCERPT_CHARS if truncate else None
    if kind == "tool_call":
        tool = event.get("tool", "?")
        mark = "✗" if event.get("is_error") else "✓"
        if full:
            raw_input = event.get("input")
            body = json.dumps(raw_input, ensure_ascii=False) if raw_input is not None else ""
        else:
            body = _clip(_input_summary(tool, event.get("input")), in_limit)
        lines = [f"{prefix} {tool}  {mark}  {body}".rstrip()]
        if full:
            result = event.get("result_excerpt") or ""
            chars = event.get("result_chars")
            duration = event.get("duration_ms")
            meta = f"result ({chars} chars" if chars is not None else "result ("
            meta += f", {duration} ms)" if duration is not None else ")"
            lines.append(f"    {meta}: {result}" if result else f"    {meta}")
        return lines
    if kind == "assistant_text":
        text = event.get("text_excerpt") or ""
        return [f"{prefix}  {text if full else _clip(text, _TEXT_EXCERPT_CHARS if truncate else None)}".rstrip()]
    if kind == "usage":
        return [
            f"{prefix} in={event.get('input_tokens', 0)} out={event.get('output_tokens', 0)} "
            f"cache_read={event.get('cache_read_tokens', 0)} cache_create={event.get('cache_creation_tokens', 0)}"
        ]
    if kind == "session":
        extra = " ".join(str(event[k]) for k in ("session_id", "model", "cwd") if event.get(k))
        return [f"{prefix} {extra}".rstrip()]
    return [f"{prefix} {_clip(json.dumps(event, ensure_ascii=False), in_limit)}"]


def _command_show(args: argparse.Namespace, storage: RepositoryStorage, console: ConsoleReporter) -> int:
    try:
        bead_id = storage.resolve_bead_id(args.bead_id)
    except ValueError as exc:
        console.error(str(exc))
        return 1
    store = TraceStore(storage.root)
    records = store.list_records(bead_id)
    if not records:
        console.info(f"No traces recorded for {bead_id}.")
        return 0
    if args.seq is not None:
        matching = [r for r in records if r.seq == args.seq]
        if not matching:
            console.error(f"No trace seq {args.seq} for {bead_id} (available: {', '.join(str(r.seq) for r in records)})")
            return 1
        record = matching[0]
    else:
        mains = [r for r in records if r.role == ROLE_MAIN]
        record = (mains or records)[-1]
    events = store.load_events(bead_id, record.seq)
    if args.kind:
        events = [e for e in events if e.get("kind") == args.kind]
    if args.errors:
        events = [e for e in events if e.get("kind") == "tool_call" and e.get("is_error")]
    if args.json:
        print(json.dumps(events, indent=2))
        return 0
    if not store.events_path(bead_id, record.seq).exists():
        note = f"Trace {bead_id} seq {record.seq} has no events (status: {record.status}"
        note += f", error: {record.error})" if record.error else ")"
        console.info(note + ".")
        return 0
    if not events:
        console.info(f"No matching events in {bead_id} seq {record.seq}.")
        return 0
    for event in events:
        for line in _format_event(event, full=args.full, truncate=not (args.plain or args.full)):
            print(line)
    return 0


# --------------------------------------------------------------- dispatch
# Later beads register ``analyze`` and ``backfill`` handlers here.
_SUBCOMMAND_HANDLERS: dict[str, Callable[[argparse.Namespace, RepositoryStorage, ConsoleReporter], int]] = {
    "list": _command_list,
    "show": _command_show,
}


def command_trace(args: argparse.Namespace, storage: RepositoryStorage, console: ConsoleReporter) -> int:
    handler = _SUBCOMMAND_HANDLERS.get(getattr(args, "trace_command", None) or "")
    if handler is None:
        console.error("Usage: takt trace {list,show} ...")
        return 1
    return handler(args, storage, console)


def add_trace_parser(subparsers: Any) -> None:
    """Build the ``trace`` parser group. Add analyze/backfill subparsers here later."""
    trace_parser = subparsers.add_parser("trace", help="Inspect captured agent session traces")
    trace_parser.add_argument("--root", dest="root", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    trace_sub = trace_parser.add_subparsers(dest="trace_command")

    list_parser = trace_sub.add_parser("list", help="List trace records")
    list_parser.add_argument("--feature-root", help="Only records of this feature tree (incl. planner records)")
    list_parser.add_argument("--bead", help="Only records of this bead (ID or unique prefix)")
    list_parser.add_argument("--status", choices=list(TRACE_STATUSES), help="Only records with this status")
    fmt = list_parser.add_mutually_exclusive_group()
    fmt.add_argument("--plain", action="store_true", help="Tab-separated rows without header")
    fmt.add_argument("--json", action="store_true", help="Emit record metadata as a JSON array")

    show_parser = trace_sub.add_parser("show", help="Show the normalised events of a trace")
    show_parser.add_argument("bead_id", help="Bead ID or unique prefix")
    show_parser.add_argument("--seq", type=int, help="Trace sequence number (default: latest main trace)")
    show_parser.add_argument("--kind", help="Only events of this kind (e.g. tool_call)")
    show_parser.add_argument("--errors", action="store_true", help="Only tool calls that errored")
    show_parser.add_argument("--full", action="store_true", help="Show full inputs and result excerpts")
    fmt = show_parser.add_mutually_exclusive_group()
    fmt.add_argument("--plain", action="store_true", help="No truncation of excerpts")
    fmt.add_argument("--json", action="store_true", help="Emit the events as a JSON array")
