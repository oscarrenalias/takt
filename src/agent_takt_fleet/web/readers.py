"""Tolerant, read-only access to a project's ``.takt/beads/*.json`` files.

Beads are read as raw JSON dicts so unknown fields pass through untouched and
missing optional fields never cause errors (ADR-1897d241). Parsed beads are
cached per path keyed by ``(size, mtime_ns)`` so unchanged files are not
re-parsed on each poll.

This module imports nothing from ``agent_takt`` and spawns no subprocess.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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
