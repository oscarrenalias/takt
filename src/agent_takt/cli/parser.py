from __future__ import annotations

import argparse
from importlib.metadata import version as _pkg_version


def _refresh_seconds(value: str) -> int:
    seconds = int(value)
    if seconds < 1:
        raise argparse.ArgumentTypeError("--refresh-seconds must be at least 1")
    return seconds


_VALID_STATUSES = ["open", "ready", "in_progress", "done", "blocked", "handed_off"]

_DEFAULT_AGENT_TYPES = [
    "planner", "developer", "tester", "documentation", "review", "recovery", "investigator",
]


def build_parser(agent_types: list[str] | None = None) -> argparse.ArgumentParser:
    if agent_types is None:
        agent_types = _DEFAULT_AGENT_TYPES
    parser = argparse.ArgumentParser(prog="takt")
    parser.add_argument("--version", action="version", version=f"takt {_pkg_version('agent-takt')}")
    parser.add_argument("--root", default=".", help="Repository root")
    parser.add_argument(
        "--runner",
        choices=["codex", "claude"],
        default=None,
        help="Agent runner backend (default: $AGENT_TAKT_RUNNER or $ORCHESTRATOR_RUNNER or config.default_runner)",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    plan_parser = subparsers.add_parser("plan", help="Plan a spec file into a bead graph")
    plan_parser.add_argument("--root", dest="root", help=argparse.SUPPRESS)
    plan_parser.add_argument(
        "spec_file",
        nargs="?",
        help="Path to the spec Markdown file to plan (required unless --from-file is used)",
    )
    plan_mode_group = plan_parser.add_mutually_exclusive_group()
    plan_mode_group.add_argument(
        "--write",
        action="store_true",
        help="Persist the bead graph to storage (dry-run if omitted)",
    )
    plan_mode_group.add_argument(
        "--output",
        metavar="FILE",
        help="Write the plan JSON to FILE without persisting any beads",
    )
    plan_mode_group.add_argument(
        "--from-file",
        metavar="FILE",
        dest="from_file",
        help="Read plan JSON from FILE and persist beads without calling the LLM (spec_file not required)",
    )

    run_parser = subparsers.add_parser("run", help="Run the scheduler to quiescence")
    run_parser.add_argument("--root", dest="root", help=argparse.SUPPRESS)
    run_parser.add_argument("--max-workers", type=int, default=1, help="Maximum number of parallel agent workers (default: 1)")
    run_parser.add_argument("--feature-root", help="Run only beads in the specified feature root")
    run_parser.add_argument("--verbose", action="store_true", help="Show per-bead deferral detail lines")

    bead_parser = subparsers.add_parser("bead", help="Manage beads (create, list, show, graph, delete, ...)")
    bead_parser.add_argument("--root", dest="root", help=argparse.SUPPRESS)
    bead_subparsers = bead_parser.add_subparsers(dest="bead_command", required=True)

    create_parser = bead_subparsers.add_parser("create", help="Create a new bead")
    create_parser.add_argument("--title", required=True, help="Short human-readable title for the bead")
    create_parser.add_argument("--agent", required=True, help="Agent type that will execute this bead (e.g. developer, tester, review)")
    create_parser.add_argument("--description", required=True, help="Full description of the work to be done")
    create_parser.add_argument("--parent-id", help="Parent bead ID for child beads in a feature tree")
    create_parser.add_argument("--dependency", action="append", default=[], help="Bead ID that must be done before this one (repeatable)")
    create_parser.add_argument("--criterion", action="append", default=[], help="Acceptance criterion (repeatable)")
    create_parser.add_argument("--linked-doc", action="append", default=[], help="Path to a linked document or spec (repeatable)")
    create_parser.add_argument("--expected-file", action="append", default=[], help="File path expected to be modified by this bead (repeatable)")
    create_parser.add_argument("--expected-glob", action="append", default=[], help="Glob pattern for files expected to be modified (repeatable)")
    create_parser.add_argument("--touched-file", action="append", default=[], help="File path already touched by this bead (repeatable)")
    create_parser.add_argument("--conflict-risks", default="", help="Free-text description of known conflict risks with other beads")
    create_parser.add_argument("--label", action="append", default=[], help="Label to attach to this bead (repeatable)")
    create_parser.add_argument("--priority", choices=["high", "normal"], default=None, help="Bead priority (high or normal; omit for normal)")

    show_parser = bead_subparsers.add_parser("show", help="Show full details for a bead as JSON")
    show_parser.add_argument("bead_id", help="Bead ID or unique prefix to display")
    show_parser.add_argument(
        "--field",
        metavar="PATH",
        help=(
            "Project a single field from the bead JSON using a dotted path "
            "(e.g. status, handoff_summary.completed, execution_history[-1].event)"
        ),
    )

    update_parser = bead_subparsers.add_parser("update", help="Update metadata fields on an existing bead")
    update_parser.add_argument("bead_id", help="Bead ID or unique prefix to update")
    update_parser.add_argument("--status", help="New status (ready, blocked, or done)")
    update_parser.add_argument("--description", help="Replace the bead's description")
    update_parser.add_argument("--block-reason", help="Reason the bead is blocked (used with --status blocked)")
    update_parser.add_argument("--expected-file", action="append", default=[], help="Add an expected file path (repeatable)")
    update_parser.add_argument("--expected-glob", action="append", default=[], help="Add an expected glob pattern (repeatable)")
    update_parser.add_argument("--touched-file", action="append", default=[], help="Add a touched file path (repeatable)")
    update_parser.add_argument("--conflict-risks", help="Update the conflict risks description")
    update_parser.add_argument("--model", help="Set per-bead model override (metadata.model_override)")

    delete_parser = bead_subparsers.add_parser("delete", help="Delete a bead")
    delete_parser.add_argument("bead_id", help="Bead ID or unique prefix to delete")
    delete_parser.add_argument("--force", action="store_true", help="Bypass status check and delete beads in any state")

    list_parser = bead_subparsers.add_parser("list", help="List all beads")
    list_parser.add_argument("--plain", action="store_true", help="Output a plain text table instead of JSON")
    list_parser.add_argument("--label", action="append", default=[], dest="label_filter", help="Filter by label — beads must match ALL provided labels (repeatable)")
    list_parser.add_argument(
        "--status",
        action="append",
        default=[],
        dest="status_filter",
        choices=_VALID_STATUSES,
        metavar="STATUS",
        help=(
            "Filter by bead status (repeatable, OR semantics). "
            f"Valid values: {', '.join(_VALID_STATUSES)}"
        ),
    )
    list_parser.add_argument(
        "--agent",
        action="append",
        default=[],
        dest="agent_filter",
        choices=agent_types,
        metavar="AGENT",
        help=(
            "Filter by agent type (repeatable, OR semantics). "
            f"Valid values: {', '.join(agent_types)}"
        ),
    )
    list_parser.add_argument(
        "--feature-root",
        metavar="ID",
        dest="feature_root",
        help="Restrict list to beads in this feature tree (ID or unique prefix)",
    )

    label_parser = bead_subparsers.add_parser("label", help="Add labels to a bead (idempotent)")
    label_parser.add_argument("bead_id", help="Bead ID or unique prefix")
    label_parser.add_argument("labels", nargs="+", help="One or more labels to add")

    unlabel_parser = bead_subparsers.add_parser("unlabel", help="Remove a label from a bead")
    unlabel_parser.add_argument("bead_id", help="Bead ID or unique prefix")
    unlabel_parser.add_argument("label", help="Label to remove")

    set_priority_parser = bead_subparsers.add_parser("set-priority", help="Set or clear the priority on a bead")
    set_priority_parser.add_argument("bead_id", help="Bead ID or unique prefix")
    set_priority_parser.add_argument("priority", choices=["high", "normal"], help="Priority to set (normal clears it)")

    claims_parser = bead_subparsers.add_parser("claims", help="Show active file-scope claims across in-progress beads")
    claims_parser.add_argument("--plain", action="store_true", help="Output a plain text list instead of JSON")

    history_parser = bead_subparsers.add_parser("history", help="Show formatted execution history for a bead")
    history_parser.add_argument("bead_id", help="Bead ID or unique prefix")
    history_parser.add_argument(
        "--limit",
        type=int,
        metavar="N",
        help="Show only the last N entries (default: all)",
    )
    history_parser.add_argument(
        "--event",
        action="append",
        default=[],
        dest="event_filter",
        metavar="EVENT",
        help="Filter to entries whose event field matches (repeatable, OR semantics)",
    )
    history_format_group = history_parser.add_mutually_exclusive_group()
    history_format_group.add_argument(
        "--json",
        action="store_true",
        dest="output_json",
        help="Emit execution history as a JSON array",
    )
    history_format_group.add_argument(
        "--plain",
        action="store_true",
        help="Pipe-friendly output: identical to default but never truncates the summary column",
    )

    graph_parser = bead_subparsers.add_parser("graph", help="Render a Mermaid diagram of the bead dependency graph")
    graph_parser.add_argument("--feature-root", help="Scope the graph to a single feature root bead ID")
    graph_parser.add_argument("--output", help="Write the diagram to this file instead of printing to stdout")

    handoff_parser = subparsers.add_parser("handoff", help="Show handoff summary for a bead")
    handoff_parser.add_argument("--root", dest="root", help=argparse.SUPPRESS)
    handoff_parser.add_argument("bead_id", help="Bead ID or unique prefix to hand off")
    handoff_parser.add_argument("--to", required=True, help="Target agent type receiving the handoff")
    handoff_parser.add_argument("--summary", required=True, help="Human-readable summary of what was completed and what remains")

    retry_parser = subparsers.add_parser("retry", help="Requeue a blocked bead")
    retry_parser.add_argument("--root", dest="root", help=argparse.SUPPRESS)
    retry_parser.add_argument("bead_id", help="Bead ID or unique prefix to requeue")

    merge_parser = subparsers.add_parser("merge", help="Merge a completed feature branch into main")
    merge_parser.add_argument("--root", dest="root", help=argparse.SUPPRESS)
    merge_parser.add_argument("bead_id", help="Feature root bead ID whose branch will be merged")
    merge_parser.add_argument("--skip-rebase", action="store_true", help="Skip merge-main preflight")
    merge_parser.add_argument("--skip-tests", action="store_true", help="Skip test gate")

    summary_parser = subparsers.add_parser("summary", help="Show bead counts and next actionable beads")
    summary_parser.add_argument("--root", dest="root", help=argparse.SUPPRESS)
    summary_parser.add_argument("--feature-root", help="Scope the summary to a single feature root bead ID")

    tui_parser = subparsers.add_parser("tui", help="Open the interactive terminal UI")
    tui_parser.add_argument("--root", dest="root", help=argparse.SUPPRESS)
    tui_parser.add_argument("--feature-root", help="Scope the TUI view to a single feature root bead ID")
    tui_parser.add_argument("--refresh-seconds", type=_refresh_seconds, default=3, help="How often to refresh the TUI display in seconds (default: 3)")
    tui_parser.add_argument("--max-workers", type=int, default=1, help="Maximum number of parallel agent workers shown in the TUI (default: 1)")

    telemetry_parser = subparsers.add_parser("telemetry", help="Show telemetry data for a bead")
    telemetry_parser.add_argument("--root", dest="root", help=argparse.SUPPRESS)
    telemetry_parser.add_argument("--days", type=int, default=7, help="Number of days to look back (default: 7)")
    telemetry_parser.add_argument("--feature-root", help="Filter by feature root bead ID")
    telemetry_parser.add_argument("--agent-type", help="Filter by agent type")
    telemetry_parser.add_argument("--status", help="Filter by bead status")
    telemetry_parser.add_argument("--json", action="store_true", dest="output_json", help="Output raw JSON")

    init_parser = subparsers.add_parser("init", help="Initialise a new project for orchestration")
    init_parser.add_argument("--root", dest="root", help=argparse.SUPPRESS)
    init_parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing files instead of skipping them",
    )
    init_parser.add_argument(
        "--non-interactive",
        action="store_true",
        dest="non_interactive",
        help="Use all defaults without prompting (useful for scripting)",
    )

    upgrade_parser = subparsers.add_parser("upgrade", help="Upgrade takt-managed assets to the current version")
    upgrade_parser.add_argument("--root", dest="root", help=argparse.SUPPRESS)
    upgrade_parser.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="Print what would change without writing any files",
    )

    asset_parser = subparsers.add_parser("asset", help="Manage asset ownership in the takt manifest")
    asset_parser.add_argument("--root", dest="root", help=argparse.SUPPRESS)
    asset_subparsers = asset_parser.add_subparsers(dest="asset_command", required=True)

    mark_owned_parser = asset_subparsers.add_parser(
        "mark-owned",
        help="Mark assets matching a glob as user-owned (skipped by takt upgrade)",
    )
    mark_owned_parser.add_argument("glob", help="Glob pattern matching asset paths to mark as user-owned")

    unmark_owned_parser = asset_subparsers.add_parser(
        "unmark-owned",
        help="Remove user-owned flag from assets matching a glob",
    )
    unmark_owned_parser.add_argument("glob", help="Glob pattern matching asset paths to unmark")

    asset_subparsers.add_parser("list", help="List all tracked assets with modification status and ownership")

    memory_parser = subparsers.add_parser("memory", help="Manage shared semantic memory")
    memory_parser.add_argument("--root", dest="root", help=argparse.SUPPRESS)
    memory_subparsers = memory_parser.add_subparsers(dest="memory_command", required=True)

    memory_subparsers.add_parser("init", help="Create the memory database and download the ONNX embedding model")

    add_parser = memory_subparsers.add_parser("add", help="Embed and insert a new text entry")
    add_parser.add_argument("text", help="Text to embed and store")
    add_parser.add_argument("--namespace", default="global", help="Namespace to store the entry in (default: global)")
    add_parser.add_argument("--source", default="", help="Source tag for the entry (e.g. developer, tester)")

    search_parser = memory_subparsers.add_parser("search", help="Semantic search over stored entries")
    search_parser.add_argument("query", help="Search query text")
    search_parser.add_argument("--namespace", default=None, help="Restrict search to this namespace (default: search all)")
    search_parser.add_argument("--limit", type=int, default=5, help="Maximum number of results to return (default: 5)")
    search_parser.add_argument("--threshold", type=float, default=None, help="Maximum distance threshold; results beyond this are excluded")

    ingest_parser = memory_subparsers.add_parser("ingest", help="Chunk and ingest a file into memory")
    ingest_parser.add_argument("path", help="Path to the file to ingest")
    ingest_parser.add_argument("--namespace", default="global", help="Namespace to store chunks in (default: global)")
    ingest_parser.add_argument("--source", default="", help="Source tag for ingested chunks")

    delete_parser = memory_subparsers.add_parser("delete", help="Delete a memory entry by UUID")
    delete_parser.add_argument("entry_id", help="UUID of the entry to delete")

    memory_subparsers.add_parser("stats", help="Show memory database statistics")

    namespace_parser = memory_subparsers.add_parser("namespace", help="Manage memory namespaces")
    namespace_subparsers = namespace_parser.add_subparsers(dest="namespace_command", required=True)

    namespace_subparsers.add_parser("list", help="List all namespaces with entry counts")

    namespace_show_parser = namespace_subparsers.add_parser("show", help="Show recent entries for a namespace")
    namespace_show_parser.add_argument("namespace", help="Namespace to show entries for")
    namespace_show_parser.add_argument("--limit", type=int, default=5, help="Maximum number of entries to return (default: 5)")

    return parser
