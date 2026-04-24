"""takt-fleet watch command handler."""
from __future__ import annotations

import argparse
import queue
import sys

from ...formatters import format_watch_event_line
from ...registry import RegistryError, filter_projects, load_registry
from ...tailer import start_tailing


def command_watch(args: argparse.Namespace) -> int:
    tag_filter: list[str] = getattr(args, "tag", [])
    project_filter: list[str] = getattr(args, "project", [])
    since: str | None = getattr(args, "since", None)

    try:
        all_projects = load_registry()
    except RegistryError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    projects = filter_projects(all_projects, tags=tag_filter, names=project_filter)
    if not projects:
        print("No projects match the given filters.", file=sys.stderr)
        return 1

    project_pairs = [(p.name, p.path) for p in projects]
    merged_queue, stop_event, threads = start_tailing(project_pairs, since=since)

    n_workers = len(threads)
    n_done = 0

    try:
        while n_done < n_workers:
            try:
                event = merged_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if event is None:
                n_done += 1
                continue
            print(format_watch_event_line(event), flush=True)
    except KeyboardInterrupt:
        stop_event.set()
        print("\n(interrupted)", file=sys.stderr)
        return 0

    stop_event.set()
    return 0
