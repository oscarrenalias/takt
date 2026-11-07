"""Active scheduler-run markers under ``.takt/runs/active/`` and stale-run reaping."""

from __future__ import annotations

import json
import logging
import os
import socket
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _active_dir(root: Path) -> Path:
    return Path(root) / ".takt" / "runs" / "active"


def _marker_path(root: Path, run_id: str) -> Path:
    return _active_dir(root) / f"{run_id}.json"


def _atomic_write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.stem}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def write_active_marker(root: Path, data: dict[str, Any]) -> Path:
    """Atomically write ``data`` (must contain ``run_id``) to the active marker file."""
    path = _marker_path(root, data["run_id"])
    _atomic_write(path, data)
    return path


def update_active_marker(root: Path, run_id: str, **fields: Any) -> dict[str, Any] | None:
    """Read-modify-atomic-write a marker. Returns the new data, or None if missing/unparseable."""
    path = _marker_path(root, run_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    data.update(fields)
    _atomic_write(path, data)
    return data


def remove_active_marker(root: Path, run_id: str) -> None:
    """Delete a marker, ignoring a missing file."""
    try:
        os.remove(_marker_path(root, run_id))
    except FileNotFoundError:
        pass


def is_pid_alive(hostname: str, pid: int) -> bool | None:
    """True/False for a pid on this host; None when the marker is from another host."""
    if hostname != socket.gethostname():
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _read_marker(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise
    except (ValueError, OSError):
        return None
    return data if isinstance(data, dict) else None


def list_active_runs(root: Path) -> list[dict[str, Any]]:
    """Return each parseable marker dict plus an ``alive`` field (bool | None)."""
    directory = _active_dir(root)
    runs: list[dict[str, Any]] = []
    if not directory.is_dir():
        return runs
    for path in sorted(directory.glob("*.json")):
        try:
            data = _read_marker(path)
        except FileNotFoundError:
            continue
        if data is None:
            continue
        try:
            alive = is_pid_alive(str(data.get("hostname", "")), int(data.get("pid", -1)))
        except (TypeError, ValueError):
            continue
        runs.append({**data, "alive": alive})
    return runs


def reap_stale_runs(storage: Any, new_run_id: str) -> list[str]:
    """Reap markers of dead runs on this host, recording a ``crashed`` finish event for each.

    The marker is removed first (claim by delete); only the process whose
    ``os.remove`` succeeds records the event, so concurrent reapers don't duplicate it.
    """
    root = storage.root
    directory = _active_dir(root)
    reaped: list[str] = []
    if not directory.is_dir():
        return reaped
    for path in sorted(directory.glob("*.json")):
        try:
            data = _read_marker(path)
        except FileNotFoundError:
            continue
        if data is None:
            try:
                os.remove(path)
            except FileNotFoundError:
                continue
            logger.warning("Removed unparseable active-run marker %s", path)
            continue
        try:
            pid = int(data.get("pid", -1))
        except (TypeError, ValueError):
            try:
                os.remove(path)
            except FileNotFoundError:
                continue
            logger.warning("Removed active-run marker with invalid pid %s", path)
            continue
        if is_pid_alive(str(data.get("hostname", "")), pid) is not False:
            continue
        try:
            os.remove(path)
        except FileNotFoundError:
            continue
        run_id = data.get("run_id") or path.stem
        storage.record_event(
            "scheduler_run_finished",
            {
                "run_id": run_id,
                "pid": pid,
                "exit_reason": "crashed",
                "detected_by_run_id": new_run_id,
                "cycles": None,
                "started_count": None,
                "completed_count": None,
                "blocked_count": None,
                "correctives_created_count": None,
                "deferred_count": None,
                "final_state": None,
                "duration_seconds": None,
                "cost_usd": None,
                "error": None,
            },
        )
        reaped.append(run_id)
    return reaped
