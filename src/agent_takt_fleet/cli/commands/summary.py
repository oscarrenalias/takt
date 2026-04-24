from __future__ import annotations

import argparse
import json
import sys

from ...adapter import AdapterError, TaktAdapter
from ...executor import fan_out
from ...formatters import format_fleet_summary
from ...models import Project
from ...registry import RegistryError, compute_health, filter_projects, load_registry


def _fetch_project_row(project: Project) -> dict:
    """Return a summary row dict for one project.

    Computes health first; skips the takt summary call if the project is
    unhealthy.  Adapter errors on otherwise-healthy projects fall back to
    a ``takt-error`` health value.
    """
    health = compute_health(project)
    if health != "ok":
        return {"name": project.name, "health": health, "counts": None}

    try:
        adapter = TaktAdapter(project.path)
        result = adapter.summary()
        return {"name": project.name, "health": "ok", "counts": result.get("counts", {})}
    except AdapterError:
        return {"name": project.name, "health": "takt-error", "counts": None}


def command_summary(args: argparse.Namespace) -> int:
    try:
        projects = load_registry()
    except RegistryError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    tags = args.tag or []
    names = args.project or []
    projects = filter_projects(projects, tags=tags, names=names)

    if not projects:
        if tags or names:
            print("No projects match the given filters.", file=sys.stderr)
        else:
            print("No projects registered. Run `takt-fleet register <path>` first.", file=sys.stderr)
        return 0

    max_parallel = min(len(projects), 4)
    results = fan_out(projects, _fetch_project_row, max_parallel=max_parallel)

    rows: list[dict] = []
    for _project, row, exc in results:
        if exc is not None:
            rows.append({"name": _project.name, "health": "error", "counts": None})
        else:
            rows.append(row)  # type: ignore[arg-type]

    if args.output_json:
        print(json.dumps(rows, indent=2))
        return 0

    plain = getattr(args, "plain", False)
    table = format_fleet_summary(rows, plain=plain)
    if table:
        print(table)

    return 0
