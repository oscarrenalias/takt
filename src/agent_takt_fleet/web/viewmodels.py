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
    EventPosition,
    InvalidCursor,  # noqa: F401  (re-exported for the app layer)
    ProjectReader,
    bead_counts,
    compute_health_from_files,
    decode_cursor,
    encode_cursor,
    read_events_page,
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


DEFAULT_BEAD_LIMIT = 25
MAX_BEAD_LIMIT = 200
ORDERS = ("newest", "oldest")
_BEAD_CURSOR_KEY = "beads"


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if not isinstance(value, (list, tuple, set, frozenset)):
        return []
    return [str(v) for v in value if v]


def build_bead_list(
    beads: Iterable[dict[str, Any]],
    status: Iterable[str] | str | None = None,
    agent: Iterable[str] | str | None = None,
    label: Iterable[str] | str | None = None,
    feature_root: str | None = None,
    q: str | None = None,
    limit: Any = DEFAULT_BEAD_LIMIT,
    cursor: str | None = None,
    order: str = "newest",
) -> dict[str, Any]:
    """Filter, sort and page raw beads into ``{beads, total, limit, next_cursor}``.

    Filters mirror ``takt bead list``: OR within ``status`` and ``agent``, AND
    across ``label``; ``feature_root`` is an exact match and ``q`` a
    case-insensitive substring over bead id and title. Filters apply before
    paging. Sorted newest first (first execution_history timestamp, ties by
    bead id descending) unless ``order="oldest"``. ``limit`` defaults to 25 and
    is clamped to 1..200; ``total`` is the full match count. Raises
    ``ValueError`` for an invalid ``order`` and :class:`InvalidCursor` for a
    malformed ``cursor``.
    """
    if order not in ORDERS:
        raise ValueError(f"invalid order: {order}")
    start = 0
    if cursor:
        position = decode_cursor(cursor).get(_BEAD_CURSOR_KEY)
        if position is None:
            raise InvalidCursor("invalid cursor")
        start = position.offset
    statuses = set(_as_list(status))
    agents = set(_as_list(agent))
    labels = set(_as_list(label))
    needle = q.strip().lower() if isinstance(q, str) else ""
    try:
        count = int(limit)
    except (TypeError, ValueError):
        count = DEFAULT_BEAD_LIMIT
    count = max(1, min(MAX_BEAD_LIMIT, count))

    matched: list[tuple[str, str, dict[str, Any]]] = []
    for bead in beads:
        if not isinstance(bead, dict):
            continue
        if statuses and bead.get("status") not in statuses:
            continue
        if agents and bead.get("agent_type") not in agents:
            continue
        if labels:
            bead_labels = bead.get("labels")
            if not isinstance(bead_labels, list) or not labels.issubset(bead_labels):
                continue
        if feature_root and bead.get("feature_root_id") != feature_root:
            continue
        bead_id = bead.get("bead_id")
        bead_id = bead_id if isinstance(bead_id, str) else ""
        if needle:
            title = bead.get("title")
            title = title if isinstance(title, str) else ""
            if needle not in bead_id.lower() and needle not in title.lower():
                continue
        created_at, _ = _first_last_timestamps(bead)
        matched.append((created_at or "", bead_id, bead))

    matched.sort(key=lambda item: (item[0], item[1]), reverse=(order == "newest"))
    page = matched[start : start + count]
    end = start + len(page)
    next_cursor = (
        encode_cursor({_BEAD_CURSOR_KEY: EventPosition(offset=end)}) if end < len(matched) else None
    )
    return {
        "beads": [build_bead_row(b) for _, _, b in page],
        "total": len(matched),
        "limit": count,
        "next_cursor": next_cursor,
    }


def _assumption_counts(handoff: Any) -> tuple[int, int]:
    """Return (structural, local) assumption counts; unknown scopes count structural."""
    if not isinstance(handoff, dict):
        return 0, 0
    assumptions = handoff.get("assumptions")
    structural = local = 0
    if isinstance(assumptions, list):
        for entry in assumptions:
            if not isinstance(entry, dict):
                continue
            if entry.get("scope") == "local":
                local += 1
            else:
                structural += 1
    return structural, local


def build_feature_list(beads: Iterable[dict[str, Any]], reader: ProjectReader | None = None) -> dict[str, Any]:
    """One rollup entry per feature root, in-flight first then newest activity.

    Only beads whose ``bead_id`` equals their ``feature_root_id`` produce
    entries (epic planner beads without a feature root are excluded). Counts,
    criteria, assumptions, cost and wall clock are summed across the tree.
    ``in_flight`` is true when any bead in the tree is ``in_progress``.
    """
    all_beads = [b for b in beads if isinstance(b, dict)]
    trees: dict[str, list[dict[str, Any]]] = {}
    for bead in all_beads:
        root = bead.get("feature_root_id")
        if isinstance(root, str) and root:
            trees.setdefault(root, []).append(bead)

    entries: list[dict[str, Any]] = []
    for bead in all_beads:
        bead_id = bead.get("bead_id")
        if not isinstance(bead_id, str) or not bead_id or bead.get("feature_root_id") != bead_id:
            continue
        tree = trees.get(bead_id, [bead])
        counts: dict[str, int] = {status: 0 for status in BEAD_STATUSES}
        criteria = {"met": 0, "unmet": 0, "descoped": 0}
        assumptions = {"structural": 0, "local": 0}
        cost_total: float | None = None
        wall_total: float | None = None
        last_activity = ""
        for member in tree:
            status = member.get("status")
            if isinstance(status, str):
                counts[status] = counts.get(status, 0) + 1
            handoff = member.get("handoff_summary")
            entries_cs = handoff.get("criteria_status") if isinstance(handoff, dict) else None
            if isinstance(entries_cs, list):
                for item in entries_cs:
                    if isinstance(item, dict) and item.get("status") in criteria:
                        criteria[item["status"]] += 1
            structural, local = _assumption_counts(handoff)
            assumptions["structural"] += structural
            assumptions["local"] += local
            metrics = bead_cost(member)
            if metrics["cost_usd"] is not None:
                cost_total = (cost_total or 0.0) + metrics["cost_usd"]
            if metrics["wall_clock_seconds"] is not None:
                wall_total = (wall_total or 0.0) + metrics["wall_clock_seconds"]
            _, last = _first_last_timestamps(member)
            if last and last > last_activity:
                last_activity = last
        metadata = bead.get("metadata")
        records = metadata.get("merge_records") if isinstance(metadata, dict) else None
        last_merge = records[-1] if isinstance(records, list) and records else None
        audits_count = 0
        if reader is not None:
            try:
                audits_count = len(reader.list_audits(bead_id))
            except Exception:
                audits_count = 0
        entries.append(
            {
                "bead_id": bead_id,
                "title": bead.get("title"),
                "status": bead.get("status"),
                "counts": counts,
                "in_flight": counts.get("in_progress", 0) > 0,
                "last_activity_at": last_activity or None,
                "criteria": criteria,
                "assumptions": assumptions,
                "cost_usd": cost_total,
                "wall_clock_seconds": wall_total,
                "last_merge": last_merge,
                "audits_count": audits_count,
            }
        )

    entries.sort(key=lambda e: e["last_activity_at"] or "", reverse=True)
    entries.sort(key=lambda e: not e["in_flight"])
    return {"features": entries, "total": len(entries)}


def build_feature_detail(
    root_id: str,
    beads: Iterable[dict[str, Any]],
    reader: ProjectReader | None = None,
) -> dict[str, Any] | None:
    """Feature entry, nested bead tree, merge records and audit list.

    Returns ``None`` when ``root_id`` is not a feature root. Children nest under
    their parent ordered by creation time (then id); tree beads whose parent is
    absent (or that are unreachable from the root, e.g. via a parent cycle) hang
    directly under the root. Each node carries ``dependencies`` as
    ``{bead_id, status}`` (status ``None`` when the dependency is unknown).
    """
    all_beads = [b for b in beads if isinstance(b, dict)]
    by_id: dict[str, dict[str, Any]] = {}
    for bead in all_beads:
        bead_id = bead.get("bead_id")
        if isinstance(bead_id, str) and bead_id:
            by_id[bead_id] = bead
    root = by_id.get(root_id)
    if root is None or root.get("feature_root_id") != root_id:
        return None

    tree = [b for b in by_id.values() if b.get("feature_root_id") == root_id]
    tree_ids = {b["bead_id"] for b in tree}

    def order_key(bead: dict[str, Any]) -> tuple[str, str]:
        created, _ = _first_last_timestamps(bead)
        return (created or "", bead["bead_id"])

    def make_node(bead: dict[str, Any]) -> dict[str, Any]:
        node = build_bead_row(bead)
        deps = []
        for dep_id in _as_list(bead.get("dependencies")):
            dep = by_id.get(dep_id)
            status = dep.get("status") if dep is not None else None
            deps.append({"bead_id": dep_id, "status": status})
        node["dependencies"] = deps
        node["children"] = []
        return node

    nodes = {b["bead_id"]: make_node(b) for b in tree}
    children_of: dict[str, list[dict[str, Any]]] = {}
    for bead in tree:
        bead_id = bead["bead_id"]
        if bead_id == root_id:
            continue
        parent = bead.get("parent_id")
        if not (isinstance(parent, str) and parent in tree_ids and parent != bead_id):
            parent = root_id
        children_of.setdefault(parent, []).append(bead)

    def attach(parent_id: str, seen: set[str]) -> None:
        for child in sorted(children_of.get(parent_id, []), key=order_key):
            child_id = child["bead_id"]
            if child_id in seen:
                continue
            seen.add(child_id)
            nodes[parent_id]["children"].append(nodes[child_id])
            attach(child_id, seen)

    reached: set[str] = {root_id}
    attach(root_id, reached)
    # Beads in a parent cycle are unreachable from the root; hang them under it.
    for bead in sorted(tree, key=order_key):
        bead_id = bead["bead_id"]
        if bead_id not in reached:
            reached.add(bead_id)
            nodes[root_id]["children"].append(nodes[bead_id])
            attach(bead_id, reached)

    feature = next(
        (e for e in build_feature_list(tree, reader)["features"] if e["bead_id"] == root_id),
        None,
    )
    metadata = root.get("metadata")
    records = metadata.get("merge_records") if isinstance(metadata, dict) else None
    audits: list[dict[str, Any]] = []
    if reader is not None:
        try:
            audits = list(reader.list_audits(root_id))
        except Exception:
            audits = []
    return {
        "feature": feature,
        "tree": nodes[root_id],
        "merge_records": list(records) if isinstance(records, list) else [],
        "audits": audits,
    }


def _dict_list(value: Any) -> list[dict[str, Any]]:
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _detail_criteria(bead: dict[str, Any], handoff: dict[str, Any]) -> list[dict[str, Any]]:
    criteria = bead.get("acceptance_criteria")
    reported: dict[int, dict[str, Any]] = {}
    for entry in _dict_list(handoff.get("criteria_status")):
        index = entry.get("index")
        if isinstance(index, int) and not isinstance(index, bool) and index not in reported:
            reported[index] = entry
    out: list[dict[str, Any]] = []
    for index, text in enumerate(criteria if isinstance(criteria, list) else []):
        entry = reported.get(index)
        out.append(
            {
                "index": index,
                "criterion": text if isinstance(text, str) else str(text),
                "status": (entry.get("status") if entry else None) or "unreported",
                "evidence": (entry.get("evidence") if entry else None) or "",
            }
        )
    return out


def _detail_telemetry(bead: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    tokens_total = 0
    duration_total = 0.0
    cost_total: float | None = None
    have_duration = have_tokens = False
    for entry in _dict_list(metadata.get("telemetry_history")):
        cost = _num(entry.get("cost_usd"))
        duration = _num(entry.get("duration_ms"))
        tokens = {
            key: _num(entry.get(key))
            for key in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens")
        }
        rows.append(
            {
                "attempt": entry.get("attempt"),
                "outcome": entry.get("outcome"),
                "cost_usd": cost,
                "duration_ms": duration,
                "model": entry.get("model"),
                **tokens,
            }
        )
        if cost is not None:
            cost_total = (cost_total or 0.0) + cost
        if duration is not None:
            duration_total += duration
            have_duration = True
        for value in tokens.values():
            if value is not None:
                tokens_total += int(value)
                have_tokens = True
    if cost_total is None:
        cost_total = bead_cost(bead)["cost_usd"]
    return {
        "rows": rows,
        "totals": {
            "attempts": len(rows),
            "cost_usd": cost_total,
            "duration_ms": duration_total if have_duration else None,
            "tokens": tokens_total if have_tokens else None,
        },
    }


def build_bead_detail(
    bead: dict[str, Any],
    beads: Iterable[dict[str, Any]],
    reader: ProjectReader | None = None,
) -> dict[str, Any]:
    """Full detail view model for one raw bead dict; malformed fields never raise."""
    all_beads = [b for b in beads if isinstance(b, dict)]
    by_id = {b["bead_id"]: b for b in all_beads if isinstance(b.get("bead_id"), str)}
    bead_id = bead.get("bead_id")
    handoff = bead.get("handoff_summary")
    if not isinstance(handoff, dict):
        handoff = {}
    metadata = bead.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}

    def ref(other: dict[str, Any] | None, other_id: str) -> dict[str, Any]:
        return {
            "bead_id": other_id,
            "status": other.get("status") if other else None,
            "title": other.get("title") if other else None,
        }

    assumptions = _dict_list(handoff.get("assumptions"))
    assumptions.sort(key=lambda a: 1 if a.get("scope") == "local" else 0)

    children = [b for b in all_beads if bead_id and b.get("parent_id") == bead_id]
    children.sort(key=lambda b: (_first_last_timestamps(b)[0] or "", str(b.get("bead_id"))))
    history = _dict_list(bead.get("execution_history"))
    history.sort(key=lambda r: str(r.get("timestamp") or ""))

    traces: list[dict[str, Any]] = []
    if reader is not None and isinstance(bead_id, str):
        try:
            traces = list(reader.list_trace_records(bead_id).records)
        except Exception:
            traces = []

    discovered = [
        build_bead_row(b)
        for b in all_beads
        if bead_id and _dict_get(b.get("metadata"), "discovered_issue_of") == bead_id
    ]
    handoff_text = {
        key: handoff.get(key)
        for key in (
            "completed", "remaining", "risks", "next_action", "block_reason",
            "design_decisions", "test_coverage_notes", "known_limitations", "conflict_risks",
        )
    }
    return {
        "row": build_bead_row(bead),
        "description": bead.get("description"),
        "block_reason": bead.get("block_reason"),
        "acceptance_criteria": _detail_criteria(bead, handoff),
        "handoff": {**handoff_text, "verdict": handoff.get("verdict") or None},
        "assumptions": assumptions,
        "files": {
            "expected_files": _as_list(bead.get("expected_files")),
            "expected_globs": _as_list(bead.get("expected_globs")),
            "touched_files": _as_list(bead.get("touched_files")),
            "changed_files": _as_list(bead.get("changed_files")),
            "updated_docs": _as_list(handoff.get("updated_docs")),
        },
        "dependencies": [ref(by_id.get(d), d) for d in _as_list(bead.get("dependencies"))],
        "children": [ref(c, str(c.get("bead_id"))) for c in children],
        "execution_history": history,
        "telemetry": _detail_telemetry(bead, metadata),
        "traces": traces,
        "merge_records": _dict_list(metadata.get("merge_records")),
        "discovered_issues": discovered,
        "raw": bead,
    }


def _dict_get(value: Any, key: str) -> Any:
    return value.get(key) if isinstance(value, dict) else None


DEFAULT_EVENT_LIMIT = 25
MAX_EVENT_LIMIT = 200
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
    limit: Any = DEFAULT_EVENT_LIMIT,
    cursor: str | None = None,
) -> dict[str, Any]:
    """Merge events across projects newest first: ``{events, limit, next_cursor}``.

    Raises :class:`InvalidCursor` for a malformed ``cursor``.
    """
    count = _clamp_limit(limit)
    positions = decode_cursor(cursor) if cursor else None  # may raise InvalidCursor
    streams: dict[str, list[dict[str, Any]]] = {}
    starts: dict[str, EventPosition | None] = {}
    pages: dict[str, Any] = {}
    roots: dict[str, Any] = {}
    for proj in projects:
        if project and proj.name != project:
            continue
        before: EventPosition | None = None
        if positions is not None:
            # A project absent from the cursor has no older events left.
            if proj.name not in positions:
                continue
            before = positions[proj.name]
        try:
            page = read_events_page(proj.path, count, before)
        except Exception:
            continue
        roots[proj.name] = proj.path
        starts[proj.name] = before
        pages[proj.name] = page
        streams[proj.name] = [build_event_item(proj.name, r) for r in page.events]

    # k-way merge: per-project file order is preserved (so the cursor is a
    # simple per-project position); ties on timestamp break by project name.
    consumed = {name: 0 for name in streams}
    items: list[dict[str, Any]] = []
    while len(items) < count:
        best: str | None = None
        for name, stream in streams.items():
            if consumed[name] >= len(stream):
                continue
            key = (stream[consumed[name]]["timestamp"], name)
            if best is None or key > (streams[best][consumed[best]]["timestamp"], best):
                best = name
        if best is None:
            break
        items.append(streams[best][consumed[best]])
        consumed[best] += 1

    next_positions: dict[str, EventPosition] = {}
    for name, page in pages.items():
        taken = consumed[name]
        total = len(streams[name])
        if taken == 0:
            # Nothing taken: continue from where this project started (if it has events).
            if total:
                start = starts[name]
                if start is not None:
                    next_positions[name] = start
                else:
                    # First page: resume from the end of the file.
                    try:
                        next_positions[name] = _end_position(roots[name])
                    except OSError:
                        pass
            continue
        if taken == total:
            if page.next_position is not None:
                next_positions[name] = page.next_position
            continue
        try:
            partial = read_events_page(roots[name], taken, starts[name])
        except Exception:
            continue
        if partial.next_position is not None:
            next_positions[name] = partial.next_position
    cursor_out = encode_cursor(next_positions) if next_positions else None
    return {"events": items, "limit": count, "next_cursor": cursor_out}


def _end_position(root: Any) -> EventPosition:
    """Position at end-of-file for a project none of whose first-page events were taken."""
    path = Path(root) / ".takt" / "logs" / "events.jsonl"
    stat = path.stat()
    return EventPosition(offset=stat.st_size, identity=stat.st_ino)


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
