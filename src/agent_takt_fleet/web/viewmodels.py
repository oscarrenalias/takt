"""Server-side view models for the fleet web UI (ADR-73d258d5).

Pure functions that shape raw, tolerantly-read project data into JSON-ready
dicts. Reads only go through ``agent_takt_fleet.web.readers``; nothing here
imports ``agent_takt`` or spawns a subprocess.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from typing import Any, Iterable

from agent_takt_fleet.event_summary import event_severity, summarize_event
from agent_takt_fleet.scheduler_runs import collect_scheduler_runs
from agent_takt_fleet.web.readers import (
    BEAD_STATUSES,
    ProjectReader,
    bead_counts,
    compute_health_from_files,
    read_recent_events,
    read_scheduler_state,
    read_takt_version,
)


def installed_version() -> str:
    """Installed ``agent-takt`` version, or ``"unknown"`` if not resolvable."""
    try:
        return metadata.version("agent-takt")
    except Exception:
        return "unknown"


_NUM = re.compile(r"\d+")


def _version_key(version: str) -> tuple[tuple[int, ...], int] | None:
    """Comparable key: numeric release parts plus a final-vs-prerelease flag."""
    text = version.strip().lstrip("vV")
    match = re.match(r"\d+(?:\.\d+)*", text)
    if not match:
        return None
    release = [int(n) for n in _NUM.findall(match.group(0))]
    while release and release[-1] == 0:
        release.pop()
    rest = text[match.end():]
    # A pre/dev release sorts before the same final release.
    is_final = 0 if re.match(r"[-._]?(a|b|c|rc|alpha|beta|pre|preview|dev)", rest, re.I) else 1
    return tuple(release), is_final


def version_drift(project_version: str | None, installed: str) -> str:
    """``behind`` if older than installed, ``unknown`` if absent, else ``none``."""
    if not project_version:
        return "unknown"
    mine = _version_key(project_version)
    theirs = _version_key(installed)
    if mine is None or theirs is None:
        return "none" if project_version == installed else "unknown"
    return "behind" if mine < theirs else "none"


def _last_event_at(events: list[dict[str, Any]]) -> str | None:
    for record in reversed(events):
        ts = record.get("timestamp")
        if isinstance(ts, str) and ts:
            return ts
    return None


def build_project_card(project: Any, reader: ProjectReader | None, installed: str) -> dict[str, Any]:
    """Build the overview card for one project; never raises for a bad project."""
    path = project.path
    health = compute_health_from_files(path)
    takt_version: str | None = None
    scheduler: dict[str, Any] = {"state": "idle", "run_id": None, "started_at": None}
    counts: dict[str, int] = {status: 0 for status in BEAD_STATUSES}
    unreadable = 0
    last_event: str | None = None

    if health != "missing":
        takt_version = read_takt_version(path)
        try:
            scheduler = read_scheduler_state(path, project.name)
        except Exception:
            pass
        try:
            events = read_recent_events(path, limit=50)
            last_event = _last_event_at(events)
        except Exception:
            pass
        if health == "ok":
            try:
                result = (reader or ProjectReader(path)).read_beads()
                counts = bead_counts(result.beads)
                for status in BEAD_STATUSES:
                    counts.setdefault(status, 0)
                unreadable = result.unreadable_beads
            except Exception:
                pass

    return {
        "name": project.name,
        "path": str(path),
        "tags": list(project.tags),
        "health": health,
        "takt_version": takt_version,
        "version_drift": version_drift(takt_version, installed),
        "scheduler": scheduler,
        "bead_counts": counts,
        "unreadable_beads": unreadable,
        "last_event_at": last_event,
    }


DEFAULT_EVENT_LIMIT = 200
MAX_EVENT_LIMIT = 1000
DETAIL_RUN_LIMIT = 20


def build_event_item(project_name: str, raw_event: dict[str, Any]) -> dict[str, Any]:
    """Shape one raw events.jsonl record; unknown types get severity ``info``."""
    event_type = raw_event.get("event_type")
    event_type = event_type if isinstance(event_type, str) else str(event_type or "")
    payload = raw_event.get("payload")
    if not isinstance(payload, dict):
        payload = {}
    timestamp = raw_event.get("timestamp")
    return {
        "project": project_name,
        "timestamp": timestamp if isinstance(timestamp, str) else "",
        "event_type": event_type,
        "payload": payload,
        "summary": summarize_event(event_type, payload),
        "severity": event_severity(event_type, payload),
    }


def _clamp_limit(limit: Any) -> int:
    try:
        value = int(limit)
    except (TypeError, ValueError):
        value = DEFAULT_EVENT_LIMIT
    return max(1, min(MAX_EVENT_LIMIT, value))


def build_events(
    projects: Iterable[Any],
    project: str | None = None,
    limit: int = DEFAULT_EVENT_LIMIT,
) -> dict[str, Any]:
    """Merge events across projects newest first: ``{events, limit}``."""
    count = _clamp_limit(limit)
    items: list[dict[str, Any]] = []
    for proj in projects:
        if project and proj.name != project:
            continue
        try:
            raw = read_recent_events(proj.path, limit=count)
        except Exception:
            continue
        items.extend(build_event_item(proj.name, r) for r in raw)
    # Stable sort on ISO timestamps; reversing first keeps later-appended
    # events ahead of earlier ones that share a timestamp.
    items.reverse()
    items.sort(key=lambda e: e["timestamp"], reverse=True)
    return {"events": items[:count], "limit": count}


def build_project_detail(
    project: Any,
    reader: ProjectReader | None,
    installed: str,
) -> dict[str, Any]:
    """Project card plus up to 20 scheduler runs, newest first."""
    detail = build_project_card(project, reader, installed)
    try:
        runs = collect_scheduler_runs(
            [(project.name, Path(project.path))], limit=DETAIL_RUN_LIMIT
        )
    except Exception:
        runs = []
    detail["scheduler_runs"] = runs
    return detail


def build_fleet(
    projects: Iterable[Any],
    installed: str,
    now: datetime | None = None,
    readers: dict[str, ProjectReader] | None = None,
) -> dict[str, Any]:
    """Fleet overview body: ``{installed_version, projects, generated_at}``."""
    stamp = now or datetime.now(timezone.utc)
    cards = [
        build_project_card(p, (readers or {}).get(p.name), installed) for p in projects
    ]
    return {
        "installed_version": installed,
        "projects": cards,
        "generated_at": stamp.isoformat(),
    }
