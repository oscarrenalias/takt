"""Server-side view models for the fleet web UI (ADR-73d258d5).

Pure functions that shape raw, tolerantly-read project data into JSON-ready
dicts. Reads only go through ``agent_takt_fleet.web.readers``; nothing here
spawns a subprocess. Only ``agent_takt.bead_metrics``/``agent_takt.models`` are
imported (lazily, for cost) per ADR-1897d241.
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


def _first_last_timestamps(bead: dict[str, Any]) -> tuple[str | None, str | None]:
    history = bead.get("execution_history")
    stamps: list[str] = []
    if isinstance(history, list):
        for record in history:
            ts = record.get("timestamp") if isinstance(record, dict) else None
            if isinstance(ts, str) and ts:
                stamps.append(ts)
    if not stamps:
        return None, None
    return stamps[0], stamps[-1]


def _criteria_counts(handoff: Any) -> tuple[int, int, int]:
    """Return (structural assumptions, unmet criteria, descoped criteria)."""
    if not isinstance(handoff, dict):
        return 0, 0, 0
    assumptions = handoff.get("assumptions")
    criteria = handoff.get("criteria_status")
    structural = 0
    if isinstance(assumptions, list):
        structural = sum(
            1 for a in assumptions if isinstance(a, dict) and a.get("scope") == "structural"
        )
    unmet = descoped = 0
    if isinstance(criteria, list):
        for entry in criteria:
            if not isinstance(entry, dict):
                continue
            status = entry.get("status")
            if status == "unmet":
                unmet += 1
            elif status == "descoped":
                descoped += 1
    return structural, unmet, descoped


def bead_badges(bead: dict[str, Any]) -> list[str]:
    """Governance/context badges for a raw bead dict (mirrors the TUI badges)."""
    badges: list[str] = []
    structural, unmet, descoped = _criteria_counts(bead.get("handoff_summary"))
    if structural:
        badges.append("structural_assumptions")
    if unmet:
        badges.append("unmet_criteria")
    if descoped:
        badges.append("descoped_criteria")
    metadata = bead.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    bead_id = bead.get("bead_id")
    if bead_id and bead_id == bead.get("feature_root_id") and metadata.get("merge_records"):
        badges.append("merged")
    labels = bead.get("labels")
    if isinstance(labels, list) and "discovered" in labels:
        badges.append("discovered")
    if metadata.get("auto_corrective_for"):
        badges.append("corrective")
    if bead.get("recovery_for"):
        badges.append("recovery")
    if bead.get("priority") == "high":
        badges.append("high_priority")
    return badges


def _history_cost(bead: dict[str, Any]) -> float | None:
    metadata = bead.get("metadata")
    history = metadata.get("telemetry_history") if isinstance(metadata, dict) else None
    if not isinstance(history, list):
        return None
    total = 0.0
    found = False
    for entry in history:
        if not isinstance(entry, dict):
            continue
        cost = entry.get("cost_usd")
        if isinstance(cost, bool) or not isinstance(cost, (int, float)):
            continue
        total += float(cost)
        found = True
    return total if found else None


def bead_cost(bead: dict[str, Any]) -> dict[str, float | None]:
    """``{cost_usd, wall_clock_seconds}`` for a raw bead dict; never raises.

    Uses ``agent_takt.bead_metrics`` when the dict converts to a ``Bead``;
    otherwise (or when it yields no cost) sums ``metadata.telemetry_history``
    cost fields, else ``None``.
    """
    cost: float | None = None
    seconds: float | None = None
    try:
        from agent_takt.bead_metrics import bead_cost_usd, bead_wall_clock_seconds
        from agent_takt.models import Bead

        converted = Bead.from_dict(bead)
        cost = bead_cost_usd(converted)
        seconds = bead_wall_clock_seconds(converted)
    except Exception:
        cost = None
        seconds = None
    if cost is None:
        try:
            cost = _history_cost(bead)
        except Exception:
            cost = None
    return {"cost_usd": cost, "wall_clock_seconds": seconds}


def build_bead_row(bead: dict[str, Any]) -> dict[str, Any]:
    """Shape one raw bead dict into a list row; tolerant of missing fields."""
    created_at, updated_at = _first_last_timestamps(bead)
    handoff = bead.get("handoff_summary")
    verdict = handoff.get("verdict") if isinstance(handoff, dict) else None
    labels = bead.get("labels")
    return {
        "bead_id": bead.get("bead_id"),
        "title": bead.get("title"),
        "status": bead.get("status"),
        "agent_type": bead.get("agent_type"),
        "bead_type": bead.get("bead_type"),
        "parent_id": bead.get("parent_id"),
        "feature_root_id": bead.get("feature_root_id"),
        "labels": list(labels) if isinstance(labels, list) else [],
        "created_at": created_at,
        "updated_at": updated_at,
        "verdict": verdict or None,
        "badges": bead_badges(bead),
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
