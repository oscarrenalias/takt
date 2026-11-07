"""takt-fleet scheduler-runs list command handler."""
from __future__ import annotations

import argparse
import json
import sys

from ...formatters import format_scheduler_runs_list
from ...registry import RegistryError, filter_projects, load_registry
from ...runlog import RunLogError
from ...scheduler_runs import collect_scheduler_runs


def command_scheduler_runs_list(args: argparse.Namespace) -> int:
    try:
        projects = load_registry()
    except RegistryError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    projects = filter_projects(
        projects, tags=getattr(args, "tag", None) or [], names=getattr(args, "project", None) or []
    )

    try:
        rows = collect_scheduler_runs(
            [(p.name, p.path) for p in projects],
            since=getattr(args, "since", None),
            limit=getattr(args, "limit", 20),
        )
    except (RunLogError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if getattr(args, "output_json", False):
        print(json.dumps(rows, indent=2))
        return 0

    if not rows:
        print("No scheduler runs found.")
        return 0

    print(format_scheduler_runs_list(rows, plain=getattr(args, "plain", False)))
    return 0
