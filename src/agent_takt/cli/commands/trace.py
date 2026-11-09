"""``takt trace`` command group: inspect captured agent session traces."""
from __future__ import annotations

import argparse
import json
from typing import Any, Callable

from ...console import ConsoleReporter
from ...storage import RepositoryStorage
from ...paths import main_checkout_root
from ...traces import (
    ROLE_MAIN,
    STATUS_CAPTURED,
    STATUS_TOO_LARGE,
    TRACE_STATUSES,
    TraceRecordMeta,
)
from ...traces.analyzers import AnalyzerContext, analyzer_names, merge_totals, run_analyzers
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


# ---------------------------------------------------------------- analyze
_ANALYZABLE_STATUSES = (STATUS_CAPTURED, STATUS_TOO_LARGE)


def _analysis_context(storage: RepositoryStorage, bead_id: str | None) -> AnalyzerContext:
    main_root = main_checkout_root(storage.root)
    if bead_id is None:
        return AnalyzerContext(agent_type="planner", repo_root=storage.root, main_checkout_root=main_root)
    bead = storage.load_bead(bead_id)
    return AnalyzerContext(
        bead_id=bead.bead_id,
        agent_type=bead.agent_type,
        linked_docs=list(bead.linked_docs or []),
        feature_root_id=storage.feature_root_id_for(bead) or bead.bead_id,
        repo_root=storage.root,
        main_checkout_root=main_root,
    )


def _analyze_bead(
    store: TraceStore,
    context: AnalyzerContext,
    records: list[TraceRecordMeta],
    names: list[str] | None,
) -> dict[str, Any] | None:
    """Run analysers over each usable record of one bead and merge the results."""
    per_record: list[dict[str, dict[str, Any]]] = []
    for record in records:
        if record.status not in _ANALYZABLE_STATUSES:
            continue
        if not store.events_path(record.bead_id, record.seq).exists():
            continue
        events = store.load_events(record.bead_id, record.seq)
        per_record.append(run_analyzers(events, context, names))
    if not per_record:
        return None
    selected = list(per_record[0])
    return {name: merge_totals(name, [r[name] for r in per_record]) for name in selected}


def _command_analyze(args: argparse.Namespace, storage: RepositoryStorage, console: ConsoleReporter) -> int:
    names = list(args.analyzer) if args.analyzer else None
    if names:
        unknown = sorted(set(names) - set(analyzer_names()))
        if unknown:
            console.error(
                f"Unknown analyzer(s): {', '.join(unknown)}. Allowed: {', '.join(analyzer_names())}"
            )
            return 1
    if bool(args.bead_id) == bool(args.feature_root):
        console.error("Specify exactly one of <bead_id> or --feature-root.")
        return 1
    store = TraceStore(storage.root)
    root_id: str | None = None
    targets: list[str | None]
    if args.bead_id:
        try:
            targets = [storage.resolve_bead_id(args.bead_id)]
        except ValueError as exc:
            console.error(str(exc))
            return 1
    else:
        from .bead import _resolve_feature_root_id

        try:
            root_id = _resolve_feature_root_id(storage, args.feature_root)
        except ValueError as exc:
            console.error(str(exc))
            return 1
        if root_id is None:
            console.error(f"No feature root found matching '{args.feature_root}'")
            return 1
        targets = sorted(
            bead.bead_id
            for bead in storage.list_beads()
            if (storage.feature_root_id_for(bead) or bead.bead_id) == root_id
        )
        if any(r.feature_root_id == root_id for r in store.list_planner_records()):
            targets.append(None)

    beads: dict[str, dict[str, Any]] = {}
    agents: dict[str, str] = {}
    trace_counts: dict[str, int] = {}
    no_traces: list[str] = []
    for target in targets:
        if target is None:
            records = [r for r in store.list_planner_records() if r.feature_root_id == root_id]
            key = PLANNER_DIR
        else:
            records = store.list_records(target)
            key = target
        context = _analysis_context(storage, target)
        result = _analyze_bead(store, context, records, names)
        if result is None:
            no_traces.append(key)
            continue
        beads[key] = result
        agents[key] = context.agent_type or "unknown"
        trace_counts[key] = sum(1 for r in records if r.status in _ANALYZABLE_STATUSES)

    selected = [n for n in analyzer_names() if names is None or n in names]
    totals = {n: merge_totals(n, [b[n] for b in beads.values() if n in b]) for n in selected}
    by_agent: dict[str, dict[str, Any]] = {}
    if args.by_agent:
        for agent in sorted(set(agents.values())):
            members = [beads[k] for k in beads if agents[k] == agent]
            by_agent[agent] = {n: merge_totals(n, [m[n] for m in members if n in m]) for n in selected}

    if args.json:
        payload: dict[str, Any] = {"beads": beads, "totals": totals, "no_traces": no_traces}
        if args.by_agent:
            payload["by_agent"] = by_agent
        print(json.dumps(payload, indent=2))
        return 0

    if not beads:
        console.info("No traces found to analyze.")
    else:
        print("TOTALS")
        for name, result in totals.items():
            print(f"  {name}: {json.dumps(result, ensure_ascii=False, sort_keys=True)}")
        if args.by_agent:
            for agent, results in by_agent.items():
                print(f"AGENT {agent}")
                for name, result in results.items():
                    print(f"  {name}: {json.dumps(result, ensure_ascii=False, sort_keys=True)}")
        header = ("BEAD", "AGENT", "TRACES", "TOOL_CALLS", "ERRORS")
        rows = []
        for key, result in beads.items():
            usage = result.get("tool_usage", {})
            rows.append([
                key, agents[key], str(trace_counts[key]),
                str(usage.get("tool_calls", "-")), str(usage.get("errors", "-")),
            ])
        widths = [max(len(header[i]), *(len(r[i]) for r in rows)) for i in range(len(header))]
        print("  ".join(h.ljust(widths[i]) for i, h in enumerate(header)).rstrip())
        for row in rows:
            print("  ".join(c.ljust(widths[i]) for i, c in enumerate(row)).rstrip())
    if no_traces:
        console.info(f"No traces recorded for: {', '.join(no_traces)}")
    return 0


# --------------------------------------------------------------- dispatch
# Later beads register the ``backfill`` handler here.
_SUBCOMMAND_HANDLERS: dict[str, Callable[[argparse.Namespace, RepositoryStorage, ConsoleReporter], int]] = {
    "list": _command_list,
    "show": _command_show,
    "analyze": _command_analyze,
}


def command_trace(args: argparse.Namespace, storage: RepositoryStorage, console: ConsoleReporter) -> int:
    handler = _SUBCOMMAND_HANDLERS.get(getattr(args, "trace_command", None) or "")
    if handler is None:
        console.error("Usage: takt trace {list,show,analyze} ...")
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

    analyze_parser = trace_sub.add_parser("analyze", help="Run analysers over a bead's traces or a feature tree")
    analyze_parser.add_argument("bead_id", nargs="?", help="Bead ID or unique prefix")
    analyze_parser.add_argument("--feature-root", help="Analyse every bead of this feature tree (plus planner records)")
    analyze_parser.add_argument(
        "--analyzer", action="append", metavar="NAME",
        help="Run only this analyser (repeatable; default: all)",
    )
    analyze_parser.add_argument("--by-agent", action="store_true", help="Group results by agent type")
    analyze_parser.add_argument("--json", action="store_true", help="Emit results as JSON")
