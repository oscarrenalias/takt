"""Tolerant, read-only access to a project's ``.takt/beads/*.json`` files.

Beads are read as raw JSON dicts so unknown fields pass through untouched and
missing optional fields never cause errors (ADR-1897d241). Parsed beads are
cached per path keyed by ``(size, mtime_ns)`` so unchanged files are not
re-parsed on each poll.

It also reads recent events, scheduler state, ``version.json`` and file-based
health. This module imports nothing from ``agent_takt`` and spawns no subprocess.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_takt_fleet.scheduler_runs import _alive_markers, read_project_runs

BEAD_STATUSES: tuple[str, ...] = (
    "open",
    "ready",
    "in_progress",
    "done",
    "blocked",
    "handed_off",
)


@dataclass
class BeadReadResult:
    beads: list[dict[str, Any]] = field(default_factory=list)
    unreadable_beads: int = 0


def bead_counts(beads: list[dict[str, Any]]) -> dict[str, int]:
    """Count beads per status; always contains all six known statuses.

    Beads with a missing or unknown status are not counted under the six keys
    (unknown statuses get their own key so they stay visible).
    """
    counts = {status: 0 for status in BEAD_STATUSES}
    for bead in beads:
        status = bead.get("status") if isinstance(bead, dict) else None
        if not isinstance(status, str) or not status:
            continue
        counts[status] = counts.get(status, 0) + 1
    return counts


class ProjectReader:
    """Reads bead files of one project root with a (size, mtime_ns) cache."""

    def __init__(self, project_root: str | Path) -> None:
        self.project_root = Path(project_root)
        self._cache: dict[Path, tuple[int, int, dict[str, Any] | None]] = {}
        self._lock = threading.Lock()
        self.parse_count = 0  # number of actual JSON parses (observability/tests)

    @property
    def beads_dir(self) -> Path:
        return self.project_root / ".takt" / "beads"

    def read_beads(self) -> BeadReadResult:
        result = BeadReadResult()
        try:
            paths = sorted(self.beads_dir.glob("*.json"))
        except OSError:
            return result
        seen: set[Path] = set()
        with self._lock:
            for path in paths:
                seen.add(path)
                try:
                    st = path.stat()
                except OSError:
                    result.unreadable_beads += 1
                    self._cache.pop(path, None)
                    continue
                key = (st.st_size, st.st_mtime_ns)
                cached = self._cache.get(path)
                if cached is not None and cached[:2] == key:
                    data = cached[2]
                else:
                    data = self._parse(path)
                    self._cache[path] = (key[0], key[1], data)
                if data is None:
                    result.unreadable_beads += 1
                else:
                    result.beads.append(data)
            for stale in set(self._cache) - seen:
                del self._cache[stale]
        return result

    def get_bead(self, bead_id: str) -> dict[str, Any] | None:
        """Return the raw bead dict whose ``bead_id`` equals ``bead_id``, or None.

        The ID is only compared against beads already read from the directory
        listing; it is never joined into a filesystem path, so traversal-style
        input (``../x``, ``..%2F..%2Fetc``, slashes) simply matches nothing.
        """
        if not isinstance(bead_id, str) or not bead_id:
            return None
        for bead in self.read_beads().beads:
            if bead.get("bead_id") == bead_id:
                return bead
        return None

    def _parse(self, path: Path) -> dict[str, Any] | None:
        self.parse_count += 1
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None


def read_beads(project_root: str | Path) -> BeadReadResult:
    """One-shot uncached read of a project's beads."""
    return ProjectReader(project_root).read_beads()


# --- events, scheduler state, version, health -------------------------------

_EVENT_TAIL_BYTES = 256 * 1024


def read_recent_events(project_root: str | Path, limit: int = 100) -> list[dict[str, Any]]:
    """Return up to ``limit`` most recent events (oldest first).

    Only the tail of ``events.jsonl`` is read; malformed lines and non-dict
    records are skipped. A missing or unreadable file yields ``[]``.
    """
    if limit <= 0:
        return []
    path = Path(project_root) / ".takt" / "logs" / "events.jsonl"
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            window = _EVENT_TAIL_BYTES
            while True:
                start = max(0, size - window)
                handle.seek(start)
                data = handle.read(size - start)
                if start == 0 or data.count(b"\n") > limit:
                    break
                window *= 4
    except OSError:
        return []
    lines = data.split(b"\n")
    if start > 0:
        lines = lines[1:]  # first line may be cut off mid-record
    events: list[dict[str, Any]] = []
    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            record = json.loads(raw.decode("utf-8", errors="replace"))
        except ValueError:
            continue
        if isinstance(record, dict):
            events.append(record)
    return events[-limit:]


def read_scheduler_runs(project_name: str, project_root: str | Path) -> list[dict[str, Any]]:
    """Scheduler run rows for a project, via the shared fleet helper."""
    try:
        return read_project_runs(project_name, Path(project_root))
    except Exception:
        return []


def read_scheduler_state(project_root: str | Path, project_name: str = "") -> dict[str, Any]:
    """Return ``{"state": running|stale|idle, "run_id", "started_at"}``.

    ``running``: at least one active marker whose process is alive.
    ``stale``: marker files exist but none is alive. ``idle``: no markers.
    """
    root = Path(project_root)
    state: dict[str, Any] = {"state": "idle", "run_id": None, "started_at": None}
    try:
        alive = _alive_markers(root)
    except Exception:
        alive = set()
    if alive:
        state["state"] = "running"
        rows = [r for r in read_scheduler_runs(project_name, root) if r.get("run_id") in alive]
        rows.sort(key=lambda r: r.get("started_at") or "")
        if rows:
            state["run_id"] = rows[-1].get("run_id")
            state["started_at"] = rows[-1].get("started_at") or None
        else:
            state["run_id"] = sorted(alive)[-1]
        return state
    try:
        has_markers = any((root / ".takt" / "runs" / "active").glob("*.json"))
    except OSError:
        has_markers = False
    if has_markers:
        state["state"] = "stale"
    return state


def read_takt_version(project_root: str | Path) -> str | None:
    """``takt_version`` from ``.takt/version.json``; None if absent or invalid."""
    try:
        data = json.loads((Path(project_root) / ".takt" / "version.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    version = data.get("takt_version")
    return version if isinstance(version, str) and version else None


def compute_health_from_files(path: str | Path) -> str:
    """``missing`` (path gone), ``no-takt`` (no .takt/config.yaml) or ``ok``."""
    root = Path(path)
    if not root.exists():
        return "missing"
    if not (root / ".takt" / "config.yaml").is_file():
        return "no-takt"
    return "ok"
