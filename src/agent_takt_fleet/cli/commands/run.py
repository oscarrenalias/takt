"""takt-fleet run command: fan out takt run across target projects concurrently."""
from __future__ import annotations

import argparse
import concurrent.futures
import sys
from datetime import datetime, timezone

from ...adapter import AdapterError, TaktAdapter
from ...formatters import format_run_summary
from ...models import FleetRun, Project, ProjectResult, RunInputs
from ...registry import RegistryError, filter_projects, load_registry
from ...runlog import new_run_id, write_run


def _run_one(
    project: Project,
    runner: str | None,
    max_workers: int | None,
) -> ProjectResult:
    """Invoke takt run in a single project; always returns a ProjectResult."""
    started_at = datetime.now(tz=timezone.utc)
    try:
        adapter = TaktAdapter(project.path)
        run_summary = adapter.run(runner=runner, max_workers=max_workers)
        return ProjectResult(
            name=project.name,
            path=project.path,
            status="success",
            started_at=started_at,
            finished_at=datetime.now(tz=timezone.utc),
            error=None,
            outputs={"created_beads": None, "run_summary": run_summary},
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


def command_run(args: argparse.Namespace) -> int:
    try:
        projects = load_registry()
    except RegistryError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    tags = args.tag or []
    names = args.project or []
    runner: str | None = getattr(args, "runner", None)
    max_workers: int | None = getattr(args, "project_max_workers", None)

    inputs = RunInputs(
        bead=None,
        tag_filter=tuple(tags),
        project_filter=tuple(names),
        max_parallel=args.max_parallel or 0,
        runner=runner,
        project_max_workers=max_workers,
    )
    run = FleetRun(
        run_id=new_run_id(),
        command="run",
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

    crashed = False
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_parallel) as pool:
        future_to_project: dict[concurrent.futures.Future[ProjectResult], Project] = {
            pool.submit(_run_one, project, runner, max_workers): project
            for project in projects
        }
        try:
            for future in concurrent.futures.as_completed(future_to_project):
                project = future_to_project[future]
                exc = future.exception()
                if exc is not None:
                    pr = ProjectResult(
                        name=project.name,
                        path=project.path,
                        status="error",
                        started_at=datetime.now(tz=timezone.utc),
                        finished_at=datetime.now(tz=timezone.utc),
                        error=f"unexpected error: {exc}",
                        outputs={"created_beads": None, "run_summary": None},
                    )
                else:
                    pr = future.result()
                run.projects.append(pr)
                write_run(run)
        except KeyboardInterrupt:
            for f in future_to_project:
                f.cancel()
            crashed = True

    run.crashed = crashed
    run.finished_at = datetime.now(tz=timezone.utc)
    write_run(run)

    if crashed:
        print("\nInterrupted — partial results saved.", file=sys.stderr)
        return 130

    print(format_run_summary(run))
    return 0
