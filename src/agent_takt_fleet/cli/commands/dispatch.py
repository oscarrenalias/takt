"""takt-fleet dispatch command: fan out an ad-hoc bead to each target project."""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from ...adapter import AdapterError, TaktAdapter
from ...executor import fan_out
from ...formatters import format_dispatch_summary
from ...models import FleetRun, Project, ProjectResult, RunInputs
from ...registry import RegistryError, filter_projects, load_registry
from ...runlog import new_run_id, write_run


def _dispatch_one(
    project: Project,
    title: str,
    description: str,
    agent_type: str,
    labels: list[str],
) -> ProjectResult:
    """Create a bead in a single project; always returns a ProjectResult."""
    started_at = datetime.now(tz=timezone.utc)
    try:
        adapter = TaktAdapter(project.path)
        bead_id = adapter.create_bead(
            title=title,
            description=description,
            agent_type=agent_type,
            labels=labels,
        )
        return ProjectResult(
            name=project.name,
            path=project.path,
            status="success",
            started_at=started_at,
            finished_at=datetime.now(tz=timezone.utc),
            error=None,
            outputs={"created_beads": [bead_id], "run_summary": None},
        )
    except AdapterError as exc:
        return ProjectResult(
            name=project.name,
            path=project.path,
            status="error",
            started_at=started_at,
            finished_at=datetime.now(tz=timezone.utc),
            error=str(exc),
            outputs={"created_beads": None, "run_summary": None},
        )


def command_dispatch(args: argparse.Namespace) -> int:
    try:
        projects = load_registry()
    except RegistryError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    tags = args.tag or []
    names = args.project or []
    title = args.title
    description = args.description
    agent_type = args.agent
    labels = list(args.label or [])

    inputs = RunInputs(
        bead={"title": title, "agent_type": agent_type, "labels": labels},
        tag_filter=tuple(tags),
        project_filter=tuple(names),
        max_parallel=args.max_parallel or 0,
        runner=None,
        project_max_workers=None,
    )
    run = FleetRun(
        run_id=new_run_id(),
        command="dispatch",
        started_at=datetime.now(tz=timezone.utc),
        finished_at=None,
        inputs=inputs,
        projects=[],
        crashed=False,
    )
    write_run(run)

    projects = filter_projects(projects, tags=tags, names=names)

    if not projects:
        if tags or names:
            print("No projects match the given filters.", file=sys.stderr)
        else:
            print("No projects registered. Run `takt-fleet register <path>` first.", file=sys.stderr)
        return 0

    max_parallel = args.max_parallel if args.max_parallel is not None else min(len(projects), 4)

    def _worker(project: Project) -> ProjectResult:
        return _dispatch_one(project, title, description, agent_type, labels)

    try:
        fan_results = fan_out(projects, _worker, max_parallel=max_parallel)
    except KeyboardInterrupt:
        run.finished_at = datetime.now(tz=timezone.utc)
        run.crashed = True
        write_run(run)
        print("\nInterrupted — partial results saved.", file=sys.stderr)
        return 130

    now = datetime.now(tz=timezone.utc)
    project_results: list[ProjectResult] = []
    for _project, result, exc in fan_results:
        if exc is not None:
            pr = ProjectResult(
                name=_project.name,
                path=_project.path,
                status="error",
                started_at=now,
                finished_at=datetime.now(tz=timezone.utc),
                error=f"unexpected error: {exc}",
                outputs={"created_beads": None, "run_summary": None},
            )
        else:
            pr = result  # type: ignore[assignment]
        project_results.append(pr)

    run.projects = project_results
    run.finished_at = datetime.now(tz=timezone.utc)
    write_run(run)

    print(format_dispatch_summary(run))
    return 0
