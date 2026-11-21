"""Starlette application factory for the read-only ``takt-fleet serve`` UI.

Only ``GET`` (and implicit ``HEAD``) routes are registered, so every other
method on any route answers 405. Handlers read project files through
``readers``/``viewmodels`` only: they never spawn a subprocess and never call
mutating ``TaktAdapter`` methods (ADR-31a0427e, ADR-1897d241).
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Callable, Iterable

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from agent_takt_fleet.web import viewmodels
from agent_takt_fleet.web.readers import ProjectReader
from agent_takt_fleet.web.stream import FleetStreamHub, format_sse

HEARTBEAT_SECONDS = 15.0

STATIC_DIR = Path(__file__).parent / "static"

ProjectsProvider = Callable[[], Iterable[Any]]


def create_app(
    projects_provider: ProjectsProvider,
    *,
    poll_interval: float = 1.0,
    installed_version: str | None = None,
    heartbeat_interval: float = HEARTBEAT_SECONDS,
) -> Starlette:
    """Build the read-only fleet web app.

    ``projects_provider`` returns the current registry projects (objects with
    ``name`` and ``path``) and is called per request so registry changes show up.
    """
    installed = installed_version or viewmodels.installed_version()
    readers: dict[str, ProjectReader] = {}

    def projects() -> list[Any]:
        return list(projects_provider())

    def reader_for(project: Any) -> ProjectReader:
        existing = readers.get(project.name)
        if existing is None or str(existing.project_root) != str(project.path):
            existing = ProjectReader(project.path)
            readers[project.name] = existing
        return existing

    def index(request: Request) -> FileResponse:
        return FileResponse(
            STATIC_DIR / "index.html",
            media_type="text/html",
            headers={"Cache-Control": "no-store"},
        )

    def fleet(request: Request) -> JSONResponse:
        current = projects()
        return JSONResponse(
            viewmodels.build_fleet(
                current, installed, readers={p.name: reader_for(p) for p in current}
            )
        )

    def project_detail(request: Request) -> JSONResponse:
        name = request.path_params["name"]
        for proj in projects():
            if proj.name == name:
                return JSONResponse(
                    viewmodels.build_project_detail(proj, reader_for(proj), installed)
                )
        return JSONResponse({"error": f"unknown project: {name}"}, status_code=404)

    def events(request: Request) -> JSONResponse:
        project = request.query_params.get("project") or None
        limit = request.query_params.get("limit", viewmodels.DEFAULT_EVENT_LIMIT)
        return JSONResponse(viewmodels.build_events(projects(), project, limit))

    hub = FleetStreamHub(
        projects,
        lambda proj: viewmodels.build_project_card(proj, reader_for(proj), installed),
        lambda current: viewmodels.build_fleet(
            current, installed, readers={p.name: reader_for(p) for p in current}
        ),
        viewmodels.build_event_item,
        poll_interval=poll_interval,
    )

    async def stream(request: Request) -> StreamingResponse:
        sub = hub.subscribe()

        async def body():
            try:
                yield ": connected\n\n"
                while True:
                    try:
                        event, data = await asyncio.wait_for(
                            sub.queue.get(), timeout=heartbeat_interval
                        )
                    except asyncio.TimeoutError:
                        yield ": ping\n\n"
                        continue
                    yield format_sse(event, data)
            finally:
                hub.unsubscribe(sub)

        return StreamingResponse(
            body(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    @asynccontextmanager
    async def lifespan(_app: Starlette):
        await hub.start()
        try:
            yield
        finally:
            await hub.stop()

    routes = [
        Route("/", index, methods=["GET"]),
        Route("/api/fleet", fleet, methods=["GET"]),
        Route("/api/projects/{name}", project_detail, methods=["GET"]),
        Route("/api/events", events, methods=["GET"]),
        Route("/api/stream", stream, methods=["GET"]),
        Mount("/static", app=StaticFiles(directory=STATIC_DIR), name="static"),
    ]
    app = Starlette(routes=routes, lifespan=lifespan)
    app.state.hub = hub
    app.state.poll_interval = poll_interval
    app.state.installed_version = installed
    app.state.projects_provider = projects_provider
    return app
