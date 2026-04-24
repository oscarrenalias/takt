from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ...formatters import format_project_list
from ...models import Project
from ...registry import (
    RegistryError,
    compute_health,
    filter_projects,
    load_registry,
    save_registry,
)


def command_register(args: argparse.Namespace) -> int:
    path = Path(args.path).resolve()

    if not path.exists():
        print(f"error: path does not exist: {path}", file=sys.stderr)
        return 1

    name = args.name or path.name
    tags = tuple(args.tag or [])

    try:
        projects = load_registry()
    except RegistryError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    for p in projects:
        if p.path == path:
            print(
                f"error: path already registered as {p.name!r}: {path}",
                file=sys.stderr,
            )
            return 1

    takt_dir = path / ".takt"
    if not takt_dir.exists():
        print(
            f"warning: {path} does not appear to be a takt project (.takt/ not found)",
            file=sys.stderr,
        )

    projects.append(Project(name=name, path=path, tags=tags))
    save_registry(projects)
    print(f"Registered {name!r} at {path}")
    return 0


def command_unregister(args: argparse.Namespace) -> int:
    identifier = args.path_or_name
    path_arg = Path(identifier).resolve()

    try:
        projects = load_registry()
    except RegistryError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    original_len = len(projects)
    projects = [
        p for p in projects if p.name != identifier and p.path != path_arg
    ]

    if len(projects) == original_len:
        print(f"error: no project found matching {identifier!r}", file=sys.stderr)
        return 1

    save_registry(projects)
    print(f"Unregistered {identifier!r}")
    return 0


def command_list(args: argparse.Namespace) -> int:
    try:
        projects = load_registry()
    except RegistryError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    tags = args.tag or []
    projects = filter_projects(projects, tags=tags)

    plain = getattr(args, "plain", False)

    health_map = {p.name: compute_health(p) for p in projects}
    output = format_project_list(projects, health_map=health_map, plain=plain)
    if output:
        print(output)
    return 0
