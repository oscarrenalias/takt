"""Read scheduler run lifecycle events directly from takt project directories.

Deliberately does not import ``agent_takt``: it reads ``.takt/logs/events.jsonl``
and ``.takt/runs/active/*.json`` as plain files.
"""
from __future__ import annotations

import json
import os
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .runlog import _parse_duration


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _pid_alive(hostname: str, pid: int) -> bool:
    if hostname != socket.gethostname():
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _alive_markers(project_path: Path) -> set[str]:
    """Return run_ids whose active marker exists and whose process is alive."""
    directory = Path(project_path) / ".takt" / "runs" / "active"
    alive: set[str] = set()
    if not directory.is_dir():
        return alive
    for path in directory.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                continue
            run_id = data.get("run_id") or path.stem
            if _pid_alive(str(data.get("hostname", "")), int(data.get("pid", -1))):
                alive.add(str(run_id))
        except (OSError, ValueError, TypeError):
            continue
    return alive


def read_project_runs(project_name: str, project_path: Path) -> list[dict[str, Any]]:
    """Return scheduler run rows for one project (unordered)."""
    events_path = Path(project_path) / ".takt" / "logs" / "events.jsonl"
    started: dict[str, dict[str, Any]] = {}
    finished: dict[str, dict[str, Any]] = {}
    try:
        handle = events_path.open("r", encoding="utf-8", errors="replace")
    except OSError:
        return []
    with handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if not isinstance(record, dict):
                continue
            event_type = record.get("event_type")
            if event_type not in ("scheduler_run_started", "scheduler_run_finished"):
                continue
            payload = record.get("payload")
            if not isinstance(payload, dict):
                continue
            run_id = payload.get("run_id")
            if not run_id or not isinstance(run_id, str):
                continue
            entry = {"timestamp": record.get("timestamp") or "", "payload": payload}
            if event_type == "scheduler_run_started":
                started[run_id] = entry
            else:
                finished[run_id] = entry

    alive: set[str] | None = None
    rows: list[dict[str, Any]] = []
    for run_id in list(started) + [r for r in finished if r not in started]:
        start = started.get(run_id)
        fin = finished.get(run_id)
        row: dict[str, Any] = {}
        if start:
            row.update(start["payload"])
        if fin:
            row.update(fin["payload"])
        row["project"] = project_name
        row["run_id"] = run_id
        row["started_at"] = start["timestamp"] if start else ""
        row["finished_at"] = fin["timestamp"] if fin else ""
        if fin:
            row["status"] = fin["payload"].get("exit_reason") or "unknown"
        else:
            if alive is None:
                alive = _alive_markers(project_path)
            row["status"] = "running" if run_id in alive else "unknown"
        rows.append(row)
    return rows


def _sort_time(row: dict[str, Any]) -> datetime | None:
    return _parse_ts(row.get("started_at")) or _parse_ts(row.get("finished_at"))


def collect_scheduler_runs(
    projects: Iterable[tuple[str, Path]],
    since: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Merge rows across ``(name, path)`` projects; filter by since, sort newest first, truncate."""
    rows: list[dict[str, Any]] = []
    for name, path in projects:
        rows.extend(read_project_runs(name, Path(path)))

    if since:
        cutoff = datetime.now(tz=timezone.utc) - _parse_duration(since)
        rows = [r for r in rows if (t := _sort_time(r)) is not None and t >= cutoff]

    epoch = datetime.fromtimestamp(0, tz=timezone.utc)
    rows.sort(key=lambda r: _sort_time(r) or epoch, reverse=True)
    if limit is not None and limit >= 0:
        rows = rows[:limit]
    return rows
