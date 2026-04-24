"""Fleet run log: write, read, list, and query run records.

Run logs live at $XDG_DATA_HOME/agent-takt/fleet/runs/<run_id>.json.
Each write is atomic (temp-file + os.replace) so readers always see a
complete JSON document — never a partial write.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

from .models import FleetRun, ProjectResult, RunInputs
from .paths import runs_dir

_CURRENT_VERSION = 1
_LOG = logging.getLogger(__name__)


class RunLogError(Exception):
    pass


# ── Run ID ─────────────────────────────────────────────────────────────────────


def new_run_id() -> str:
    """Generate a new fleet run ID in the format FR-<8 hex chars>."""
    return f"FR-{secrets.token_hex(4)}"


# ── Serialisation ──────────────────────────────────────────────────────────────


def _dt_to_str(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.isoformat()


def _str_to_dt(s: str | None) -> datetime | None:
    if s is None:
        return None
    return datetime.fromisoformat(s)


def _run_to_dict(run: FleetRun) -> dict:
    """Serialize a FleetRun to a version-tagged dict ready for JSON output."""
    return {
        "version": _CURRENT_VERSION,
        "run_id": run.run_id,
        "command": run.command,
        "started_at": _dt_to_str(run.started_at),
        "finished_at": _dt_to_str(run.finished_at),
        "inputs": {
            "bead": run.inputs.bead,
            "tag_filter": list(run.inputs.tag_filter),
            "project_filter": list(run.inputs.project_filter),
            "max_parallel": run.inputs.max_parallel,
            "runner": run.inputs.runner,
            "project_max_workers": run.inputs.project_max_workers,
        },
        "projects": [
            {
                "name": p.name,
                "path": str(p.path),
                "status": p.status,
                "started_at": _dt_to_str(p.started_at),
                "finished_at": _dt_to_str(p.finished_at),
                "error": p.error,
                "outputs": p.outputs,
            }
            for p in run.projects
        ],
        "crashed": run.crashed,
        "aggregate": run.aggregate,
    }


def _run_from_dict(data: dict) -> FleetRun:
    """Deserialize a FleetRun from a dict loaded from JSON."""
    inputs_raw = data.get("inputs") or {}
    inputs = RunInputs(
        bead=inputs_raw.get("bead"),
        tag_filter=tuple(inputs_raw.get("tag_filter") or []),
        project_filter=tuple(inputs_raw.get("project_filter") or []),
        max_parallel=inputs_raw.get("max_parallel", 1),
        runner=inputs_raw.get("runner"),
        project_max_workers=inputs_raw.get("project_max_workers"),
    )

    projects = []
    for p_raw in data.get("projects") or []:
        started_at = _str_to_dt(p_raw.get("started_at"))
        projects.append(
            ProjectResult(
                name=p_raw["name"],
                path=Path(p_raw["path"]),
                status=p_raw["status"],
                started_at=started_at or datetime.now(tz=timezone.utc),
                finished_at=_str_to_dt(p_raw.get("finished_at")),
                error=p_raw.get("error"),
                outputs=p_raw.get("outputs") or {},
            )
        )

    return FleetRun(
        run_id=data["run_id"],
        command=data["command"],
        started_at=_str_to_dt(data.get("started_at")),
        finished_at=_str_to_dt(data.get("finished_at")),
        inputs=inputs,
        projects=projects,
        crashed=data.get("crashed", False),
    )


# ── Disk I/O ───────────────────────────────────────────────────────────────────


def write_run(run: FleetRun) -> None:
    """Atomically write (or rewrite) a run record to disk.

    Creates parent directories as needed.  Uses a sibling temp file and
    os.replace() so readers always see a complete JSON document.
    """
    path = runs_dir() / f"{run.run_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)

    data = _run_to_dict(run)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _load_run_file(path: Path) -> FleetRun | None:
    """Load a run from a JSON file path.

    Returns None (and logs a warning) for unreadable, missing-version, or
    future-version files so callers can skip bad files gracefully.
    """
    try:
        with path.open() as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        _LOG.warning("runs: skipping unreadable run log %s: %s", path.name, exc)
        return None

    version = data.get("version")
    if version is None or not isinstance(version, int):
        _LOG.warning(
            "runs: skipping run log %s: missing or non-integer 'version' field",
            path.name,
        )
        return None

    if version > _CURRENT_VERSION:
        _LOG.warning(
            "runs: skipping run log %s: written by newer takt-fleet (version %d)",
            path.name,
            version,
        )
        return None

    try:
        return _run_from_dict(data)
    except (KeyError, ValueError, TypeError) as exc:
        _LOG.warning("runs: skipping malformed run log %s: %s", path.name, exc)
        return None


def load_run(run_id: str) -> FleetRun:
    """Load a single run by exact run ID.  Raises RunLogError if not found."""
    path = runs_dir() / f"{run_id}.json"
    if not path.exists():
        raise RunLogError(f"Run not found: {run_id}")
    run = _load_run_file(path)
    if run is None:
        raise RunLogError(
            f"Could not load run log for {run_id}: unrecognised format or corrupt file"
        )
    return run


# ── Prefix resolution ──────────────────────────────────────────────────────────


def resolve_run_id(prefix: str) -> str:
    """Resolve an unambiguous run ID prefix to the full run ID.

    Raises RunLogError on zero or multiple matches.
    """
    d = runs_dir()
    if not d.exists():
        raise RunLogError(f"No runs found matching {prefix!r}")

    matches = [p.stem for p in sorted(d.glob("FR-*.json")) if p.stem.startswith(prefix)]

    if not matches:
        raise RunLogError(f"No run found matching prefix {prefix!r}")
    if len(matches) > 1:
        raise RunLogError(
            f"Prefix {prefix!r} is ambiguous — matches: {', '.join(sorted(matches))}"
        )
    return matches[0]


# ── Status computation ─────────────────────────────────────────────────────────


def compute_run_status(run: FleetRun) -> str:
    """Return the aggregate status string for a run.

    Values: 'in_progress' | 'success' | 'error' | 'partial'.
    """
    if run.finished_at is None:
        return "in_progress"
    agg = run.aggregate
    if agg["failed"] == 0:
        return "success"
    if agg["succeeded"] == 0:
        return "error"
    return "partial"


# ── Duration parsing ───────────────────────────────────────────────────────────


def _parse_duration(s: str) -> timedelta:
    """Parse a duration string like '24h', '7d', '5m', '30s'."""
    if not s:
        raise ValueError("Empty duration string")
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    unit = s[-1].lower()
    if unit not in units:
        raise ValueError(
            f"Unknown duration unit {unit!r} in {s!r}; use s, m, h, or d"
        )
    try:
        value = float(s[:-1])
    except ValueError:
        raise ValueError(f"Invalid duration {s!r}")
    return timedelta(seconds=value * units[unit])


# ── Query ──────────────────────────────────────────────────────────────────────


def list_runs(
    limit: int = 20,
    since: str | None = None,
    status: str | None = None,
    command: str | None = None,
) -> list[FleetRun]:
    """List run records, most recent first, with optional filters.

    Files with unrecognised versions are silently skipped with a warning.
    """
    d = runs_dir()
    if not d.exists():
        return []

    runs: list[FleetRun] = []
    for path in d.glob("FR-*.json"):
        run = _load_run_file(path)
        if run is not None:
            runs.append(run)

    # Most recent first
    runs.sort(
        key=lambda r: r.started_at or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )

    if since is not None:
        try:
            delta = _parse_duration(since)
        except ValueError as exc:
            _LOG.warning("runs: ignoring invalid --since value %r: %s", since, exc)
        else:
            cutoff = datetime.now(tz=timezone.utc) - delta
            runs = [
                r
                for r in runs
                if r.started_at is not None and r.started_at.astimezone(timezone.utc) >= cutoff
            ]

    if status is not None:
        runs = [r for r in runs if compute_run_status(r) == status]

    if command is not None:
        runs = [r for r in runs if r.command == command]

    return runs[:limit]


# ── Live tailing ───────────────────────────────────────────────────────────────


def tail_run(
    run_id: str,
    interval: float = 1.0,
) -> Iterator[tuple[FleetRun, list[ProjectResult]]]:
    """Poll a run record, yielding (run, new_projects) tuples as state changes.

    new_projects contains ProjectResult entries not seen in the previous
    iteration.  Stops when finished_at is set on the run.
    """
    path = runs_dir() / f"{run_id}.json"
    seen_count = 0

    while True:
        if path.exists():
            run = _load_run_file(path)
            if run is not None:
                new_projects = run.projects[seen_count:]
                seen_count = len(run.projects)
                yield run, new_projects
                if run.finished_at is not None:
                    break
        time.sleep(interval)
