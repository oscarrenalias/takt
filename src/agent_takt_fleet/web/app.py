"""Starlette application factory for the read-only ``takt-fleet serve`` UI.

Only ``GET`` (and implicit ``HEAD``) routes are registered, so every other
method on any route answers 405. Handlers read project files through
``readers``/``viewmodels`` only: they never spawn a subprocess and never call
mutating ``TaktAdapter`` methods (ADR-31a0427e, ADR-1897d241).
"""

from __future__ import annotations

import asyncio
import re
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

# Path params must look like plain identifiers: no dots-only segments, slashes
# or separators ever reach a lookup.
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\- ]*$")

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

    def find_project(name: str) -> Any | None:
        if not _SAFE_ID.match(name or ""):
            return None
        for proj in projects():
            if proj.name == name:
                return proj
        return None

    def bead_list(request: Request) -> JSONResponse:
        name = request.path_params["name"]
        proj = find_project(name)
        if proj is None:
            return JSONResponse({"error": f"unknown project: {name}"}, status_code=404)
        qp = request.query_params
        order = qp.get("order") or "newest"
        if order not in viewmodels.ORDERS:
            return JSONResponse({"error": f"invalid order: {order}"}, status_code=400)
        try:
            beads = reader_for(proj).read_beads().beads
            body = viewmodels.build_bead_list(
                beads,
                status=qp.getlist("status"),
                agent=qp.getlist("agent"),
                label=qp.getlist("label"),
                feature_root=qp.get("feature_root") or None,
                q=qp.get("q") or None,
                limit=qp.get("limit", viewmodels.DEFAULT_BEAD_LIMIT),
                cursor=qp.get("cursor") or None,
                order=order,
            )
        except viewmodels.InvalidCursor:
            return JSONResponse({"error": "invalid cursor"}, status_code=400)
        except Exception:
            body = {"beads": [], "total": 0, "limit": viewmodels.DEFAULT_BEAD_LIMIT, "next_cursor": None}
        return JSONResponse(body)

    def bead_detail(request: Request) -> JSONResponse:
        name = request.path_params["name"]
        bead_id = request.path_params["bead_id"]
        proj = find_project(name)
        if proj is None:
            return JSONResponse({"error": f"unknown project: {name}"}, status_code=404)
        if not _SAFE_ID.match(bead_id):
            return JSONResponse({"error": f"unknown bead: {bead_id}"}, status_code=404)
        reader = reader_for(proj)
        try:
            beads = reader.read_beads().beads
            bead = next((b for b in beads if b.get("bead_id") == bead_id), None)
            if bead is None:
                return JSONResponse({"error": f"unknown bead: {bead_id}"}, status_code=404)
            return JSONResponse(viewmodels.build_bead_detail(bead, beads, reader))
        except Exception:
            return JSONResponse({"error": f"bead unreadable: {bead_id}"}, status_code=404)

    def feature_list(request: Request) -> JSONResponse:
        name = request.path_params["name"]
        proj = find_project(name)
        if proj is None:
            return JSONResponse({"error": f"unknown project: {name}"}, status_code=404)
        reader = reader_for(proj)
        try:
            body = viewmodels.build_feature_list(reader.read_beads().beads, reader)
        except Exception:
            body = {"features": [], "total": 0}
        return JSONResponse(body)

    def feature_detail(request: Request) -> JSONResponse:
        name = request.path_params["name"]
        root_id = request.path_params["root_id"]
        proj = find_project(name)
        if proj is None:
            return JSONResponse({"error": f"unknown project: {name}"}, status_code=404)
        missing = JSONResponse({"error": f"unknown feature: {root_id}"}, status_code=404)
        if not _SAFE_ID.match(root_id):
            return missing
        reader = reader_for(proj)
        try:
            body = viewmodels.build_feature_detail(root_id, reader.read_beads().beads, reader)
        except Exception:
            return JSONResponse({"error": f"feature unreadable: {root_id}"}, status_code=404)
        return missing if body is None else JSONResponse(body)

    def feature_audit(request: Request) -> JSONResponse:
        name = request.path_params["name"]
        root_id = request.path_params["root_id"]
        audit_id = request.path_params["audit_id"]
        proj = find_project(name)
        if proj is None:
            return JSONResponse({"error": f"unknown project: {name}"}, status_code=404)
        if not _SAFE_ID.match(root_id):
            return JSONResponse({"error": f"unknown feature: {root_id}"}, status_code=404)
        missing = JSONResponse({"error": f"unknown audit: {audit_id}"}, status_code=404)
        if not _SAFE_ID.match(audit_id):
            return missing
        reader = reader_for(proj)
        try:
            beads = reader.read_beads().beads
            root = next((b for b in beads if b.get("bead_id") == root_id), None)
            if root is None or root.get("feature_root_id") != root_id:
                return JSONResponse({"error": f"unknown feature: {root_id}"}, status_code=404)
            entry = next((a for a in reader.list_audits(root_id) if a["audit_id"] == audit_id), None)
            data = reader.read_audit(root_id, audit_id) if entry is not None else None
        except Exception:
            return missing
        if data is None or entry is None:
            return missing
        return JSONResponse({**data, "audit_id": audit_id, "generated_at": entry["generated_at"]})

    def _int_param(value: str | None, default: int) -> int:
        try:
            return int(value) if value is not None else default
        except ValueError:
            return default

    def _bead_reader(name: str, bead_id: str) -> tuple[ProjectReader | None, JSONResponse | None]:
        proj = find_project(name)
        if proj is None:
            return None, JSONResponse({"error": f"unknown project: {name}"}, status_code=404)
        missing = JSONResponse({"error": f"unknown bead: {bead_id}"}, status_code=404)
        if not _SAFE_ID.match(bead_id):
            return None, missing
        reader = reader_for(proj)
        try:
            if reader.get_bead(bead_id) is None:
                return None, missing
        except Exception:
            return None, missing
        return reader, None

    def trace_detail(request: Request) -> JSONResponse:
        bead_id = request.path_params["bead_id"]
        seq = request.path_params["seq"]
        reader, err = _bead_reader(request.path_params["name"], bead_id)
        if err is not None:
            return err
        missing = JSONResponse({"error": f"unknown trace: {bead_id}/{seq}"}, status_code=404)
        qp = request.query_params
        offset = max(0, _int_param(qp.get("offset"), 0))
        limit = min(1000, max(0, _int_param(qp.get("limit"), 200)))
        try:
            resolved = reader.resolve_trace_seq(bead_id, seq)
            if resolved is None:
                return missing
            listing = reader.list_trace_records(bead_id)
            meta = listing.records[listing.seqs.index(resolved)]
            result = reader.read_trace_events(
                bead_id,
                resolved,
                offset=offset,
                limit=limit,
                kind=qp.get("kind") or None,
                errors_only=qp.get("errors_only", "").lower() in ("1", "true", "yes", "on"),
                tool=qp.get("tool") or None,
            )
        except Exception:
            return missing
        return JSONResponse(
            {
                "meta": meta,
                "summary": result.summary,
                "events": result.events,
                "total": result.total,
                "offset": offset,
                "limit": limit,
            }
        )

    def telemetry_detail(request: Request) -> JSONResponse:
        bead_id = request.path_params["bead_id"]
        attempt = request.path_params["attempt"]
        reader, err = _bead_reader(request.path_params["name"], bead_id)
        if err is not None:
            return err
        try:
            data = reader.read_telemetry_attempt(bead_id, attempt)
        except Exception:
            data = None
        if data is None:
            return JSONResponse({"error": f"unknown attempt: {attempt}"}, status_code=404)
        return JSONResponse(data)

    def events(request: Request) -> JSONResponse:
        project = request.query_params.get("project") or None
        limit = request.query_params.get("limit", viewmodels.DEFAULT_EVENT_LIMIT)
        cursor = request.query_params.get("cursor") or None
        try:
            body = viewmodels.build_events(projects(), project, limit, cursor)
        except viewmodels.InvalidCursor:
            return JSONResponse({"error": "invalid cursor"}, status_code=400)
        return JSONResponse(body)

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
        Route("/api/projects/{name}/beads", bead_list, methods=["GET"]),
        Route("/api/projects/{name}/beads/{bead_id}", bead_detail, methods=["GET"]),
        Route(
            "/api/projects/{name}/beads/{bead_id}/traces/{seq}",
            trace_detail,
            methods=["GET"],
        ),
        Route(
            "/api/projects/{name}/beads/{bead_id}/telemetry/{attempt}",
            telemetry_detail,
            methods=["GET"],
        ),
        Route("/api/projects/{name}/features", feature_list, methods=["GET"]),
        Route("/api/projects/{name}/features/{root_id}", feature_detail, methods=["GET"]),
        Route(
            "/api/projects/{name}/features/{root_id}/audits/{audit_id}",
            feature_audit,
            methods=["GET"],
        ),
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
