"""takt-fleet runs {list,show} command handlers."""
from __future__ import annotations

import argparse
import json
import sys

from ...formatters import (
    format_project_result_line,
    format_run_aggregate_line,
    format_run_show,
    format_run_show_header,
    format_runs_list,
)
from ...runlog import (
    RunLogError,
    list_runs,
    load_run,
    resolve_run_id,
    tail_run,
)


def command_runs_list(args: argparse.Namespace) -> int:
    limit = getattr(args, "limit", 20)
    since = getattr(args, "since", None)
    status = getattr(args, "status", None)
    command = getattr(args, "command", None)
    plain = getattr(args, "plain", False)

    runs = list_runs(limit=limit, since=since, status=status, command=command)

    if not runs:
        print("No fleet runs found.", file=sys.stderr)
        return 0

    table = format_runs_list(runs, plain=plain)
    if table:
        print(table)
    return 0


def command_runs_show(args: argparse.Namespace) -> int:
    try:
        run_id = resolve_run_id(args.run_id)
    except RunLogError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    try:
        run = load_run(run_id)
    except RunLogError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    # --json: always dump raw record without tailing
    if getattr(args, "output_json", False):
        from ...runlog import _run_to_dict
        print(json.dumps(_run_to_dict(run), indent=2))
        return 0

    # Completed run: print detailed breakdown
    if run.finished_at is not None:
        print(format_run_show(run))
        return 0

    # In-progress run: tail the record live
    print(format_run_show_header(run), end="", flush=True)

    try:
        for current_run, new_projects in tail_run(run_id):
            for project in new_projects:
                print(format_project_result_line(current_run, project), flush=True)
            if current_run.finished_at is not None:
                print(format_run_aggregate_line(current_run), flush=True)
                break
    except KeyboardInterrupt:
        print("\n(interrupted)", file=sys.stderr)
        return 1

    return 0
