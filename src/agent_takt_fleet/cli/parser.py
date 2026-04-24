from __future__ import annotations

import argparse
from importlib.metadata import version as _pkg_version

_ALLOWED_AGENT_TYPES = ["developer", "tester", "documentation", "review"]


def _add_fleet_filters(parser: argparse.ArgumentParser) -> None:
    """Add shared --tag and --project filter flags to a subcommand parser."""
    parser.add_argument(
        "--tag",
        action="append",
        default=[],
        metavar="TAG",
        help="Filter to projects carrying this tag (repeatable; AND semantics — all tags must match)",
    )
    parser.add_argument(
        "--project",
        action="append",
        default=[],
        metavar="NAME",
        help="Filter to this project name (repeatable; ANY of the listed names match)",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="takt-fleet",
        description="Manage a fleet of takt projects: register projects, fan out work, aggregate status.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"takt-fleet {_pkg_version('agent-takt')}",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # ── register ──────────────────────────────────────────────────────────────
    register_parser = subparsers.add_parser(
        "register",
        help="Add a project to the fleet registry",
    )
    register_parser.add_argument(
        "path",
        help="Path to the project root directory",
    )
    register_parser.add_argument(
        "--name",
        default=None,
        help="Human-readable name for this project (default: basename of path)",
    )
    register_parser.add_argument(
        "--tag",
        action="append",
        default=[],
        metavar="TAG",
        help="Tag to attach to this project (repeatable)",
    )

    # ── unregister ────────────────────────────────────────────────────────────
    unregister_parser = subparsers.add_parser(
        "unregister",
        help="Remove a project from the fleet registry",
    )
    unregister_parser.add_argument(
        "path_or_name",
        help="Project path or name to remove",
    )

    # ── list ──────────────────────────────────────────────────────────────────
    list_parser = subparsers.add_parser(
        "list",
        help="List registered projects with health status",
    )
    list_parser.add_argument(
        "--tag",
        action="append",
        default=[],
        metavar="TAG",
        help="Filter to projects carrying this tag (repeatable; AND semantics)",
    )
    list_parser.add_argument(
        "--plain",
        action="store_true",
        help="Output a pipe-friendly plain text table instead of the default formatted view",
    )

    # ── dispatch ──────────────────────────────────────────────────────────────
    dispatch_parser = subparsers.add_parser(
        "dispatch",
        help="Fan out an ad-hoc bead to each target project (does not trigger execution)",
    )
    dispatch_parser.add_argument(
        "--title",
        required=True,
        help="Short title for the bead created in each project",
    )
    dispatch_parser.add_argument(
        "--description",
        required=True,
        help="Full description of the work to be done",
    )
    dispatch_parser.add_argument(
        "--agent",
        default="developer",
        choices=_ALLOWED_AGENT_TYPES,
        help="Agent type for the created beads (default: developer)",
    )
    dispatch_parser.add_argument(
        "--label",
        action="append",
        default=[],
        metavar="LABEL",
        help="Label to attach to each created bead (repeatable)",
    )
    dispatch_parser.add_argument(
        "--max-parallel",
        type=int,
        default=None,
        dest="max_parallel",
        help="Maximum number of projects to create beads in concurrently (default: min(projects, 4))",
    )
    _add_fleet_filters(dispatch_parser)

    # ── run ───────────────────────────────────────────────────────────────────
    run_parser = subparsers.add_parser(
        "run",
        help="Trigger takt run across target projects concurrently",
    )
    run_parser.add_argument(
        "--max-parallel",
        type=int,
        default=None,
        dest="max_parallel",
        help="Maximum number of project-level takt run calls to run concurrently (default: min(projects, 4))",
    )
    run_parser.add_argument(
        "--runner",
        choices=["codex", "claude"],
        default=None,
        help="Agent runner backend to forward to each takt run invocation",
    )
    run_parser.add_argument(
        "--project-max-workers",
        type=int,
        default=None,
        dest="project_max_workers",
        help="Forwarded as --max-workers to each takt run subprocess",
    )
    _add_fleet_filters(run_parser)

    # ── summary ───────────────────────────────────────────────────────────────
    summary_parser = subparsers.add_parser(
        "summary",
        help="Print an aggregated bead-count table across target projects",
    )
    summary_parser.add_argument(
        "--json",
        action="store_true",
        dest="output_json",
        help="Emit the aggregate as JSON for scripting",
    )
    summary_parser.add_argument(
        "--plain",
        action="store_true",
        help="Output a plain text table without ANSI formatting",
    )
    _add_fleet_filters(summary_parser)

    # ── watch ─────────────────────────────────────────────────────────────────
    watch_parser = subparsers.add_parser(
        "watch",
        help="Tail events.jsonl in each target project and print a merged live stream",
    )
    watch_parser.add_argument(
        "--since",
        default=None,
        metavar="DURATION",
        help=(
            "Replay events from this far back before streaming live (e.g. 5m, 1h). "
            "Default: live only (start from EOF)."
        ),
    )
    _add_fleet_filters(watch_parser)

    # ── runs ──────────────────────────────────────────────────────────────────
    runs_parser = subparsers.add_parser(
        "runs",
        help="Query the fleet run log",
    )
    runs_subparsers = runs_parser.add_subparsers(dest="runs_command", required=True)

    runs_list_parser = runs_subparsers.add_parser(
        "list",
        help="List recent fleet runs (most recent first)",
    )
    runs_list_parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum number of runs to show (default: 20)",
    )
    runs_list_parser.add_argument(
        "--since",
        default=None,
        metavar="DURATION",
        help="Show only runs started within this duration (e.g. 24h, 7d)",
    )
    runs_list_parser.add_argument(
        "--status",
        choices=["success", "error", "partial", "in_progress"],
        default=None,
        help="Filter by run aggregate status",
    )
    runs_list_parser.add_argument(
        "--command",
        choices=["dispatch", "run"],
        default=None,
        help="Filter by fleet command type",
    )
    runs_list_parser.add_argument(
        "--plain",
        action="store_true",
        help="Output a pipe-friendly plain text table",
    )

    runs_show_parser = runs_subparsers.add_parser(
        "show",
        help=(
            "Show details for a fleet run. "
            "Auto-detects state: tails an in-progress run live, prints a breakdown for completed runs."
        ),
    )
    runs_show_parser.add_argument(
        "run_id",
        help="Fleet run ID or unambiguous prefix (e.g. FR-a1b2c3d4 or FR-a1b2)",
    )
    runs_show_parser.add_argument(
        "--json",
        action="store_true",
        dest="output_json",
        help="Dump the raw run log record as JSON without live-tailing",
    )

    return parser
