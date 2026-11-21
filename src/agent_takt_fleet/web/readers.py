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


@dataclass
class TraceListResult:
    records: list[dict[str, Any]] = field(default_factory=list)  # meta dicts, seq order
    seqs: list[int] = field(default_factory=list)  # integer seq of each record
    unreadable_traces: int = 0


_TELEMETRY_TEXT_CAP = 256 * 1024
_USAGE_KEYS =("input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens")


def _zero_trace_summary() -> dict[str, Any]:
    return {
        "tool_counts": {},
        "error_count": 0,
        "total_duration_ms": 0,
        "usage": {key: 0 for key in _USAGE_KEYS},
        "assistant_messages": 0,
    }


@dataclass
class TraceEventsResult:
    events: list[dict[str, Any]] = field(default_factory=list)
    total: int = 0  # number of events matching the filters
    summary: dict[str, Any] = field(default_factory=_zero_trace_summary)


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
        self._trace_cache: dict[Path, tuple[int, int, dict[str, Any] | None]] = {}
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

    def traces_dir(self, bead_id: str) -> Path:
        """Trace directory for an already-validated bead ID (internal use)."""
        return self.project_root / ".takt" / "traces" / bead_id

    def list_trace_records(self, bead_id: str) -> TraceListResult:
        """List ``<seq>.meta.json`` records for a bead, sorted by integer seq.

        The bead must be found by :meth:`get_bead`; the directory name is taken
        from the matched bead dict, never from the caller's string. Unknown
        beads and missing directories yield an empty result. Unparseable or
        non-integer-named meta files are skipped and counted.
        """
        result = TraceListResult()
        bead = self.get_bead(bead_id)
        if bead is None:
            return result
        matched_id = bead.get("bead_id")
        if not isinstance(matched_id, str) or not matched_id:
            return result
        try:
            paths = list(self.traces_dir(matched_id).glob("*.meta.json"))
        except OSError:
            return result
        entries: list[tuple[int, dict[str, Any]]] = []
        seen: set[Path] = set()
        with self._lock:
            for path in paths:
                seen.add(path)
                stem = path.name[: -len(".meta.json")]
                try:
                    st = path.stat()
                except OSError:
                    result.unreadable_traces += 1
                    self._trace_cache.pop(path, None)
                    continue
                key = (st.st_size, st.st_mtime_ns)
                cached = self._trace_cache.get(path)
                if cached is not None and cached[:2] == key:
                    data = cached[2]
                else:
                    data = self._parse(path)
                    self._trace_cache[path] = (key[0], key[1], data)
                if data is None or not (stem.isascii() and stem.isdigit()):
                    result.unreadable_traces += 1
                    continue
                entries.append((int(stem), data))
            prefix = self.traces_dir(matched_id)
            for stale in [p for p in self._trace_cache if p.parent == prefix and p not in seen]:
                del self._trace_cache[stale]
        entries.sort(key=lambda e: e[0])
        result.records = [data for _, data in entries]
        result.seqs = [seq for seq, _ in entries]
        return result

    def resolve_trace_seq(self, bead_id: str, seq: Any) -> int | None:
        """Return ``seq`` as an int only if it is an integer present in the listing.

        Accepts an int or a string of ASCII digits; anything else (negative,
        float, bool, traversal-style text) resolves to None.
        """
        if isinstance(seq, bool):
            return None
        if isinstance(seq, str):
            if not (seq.isascii() and seq.isdigit()):
                return None
            value = int(seq)
        elif isinstance(seq, int):
            value = seq
        else:
            return None
        return value if value in self.list_trace_records(bead_id).seqs else None

    def read_trace_events(
        self,
        bead_id: str,
        seq: Any,
        offset: int = 0,
        limit: int = 100,
        kind: str | None = None,
        errors_only: bool = False,
        tool: str | None = None,
    ) -> TraceEventsResult:
        """Page and filter one record's ``<seq>.events.jsonl``.

        ``total`` is the filtered count; ``summary`` always covers every event.
        Unknown beads/seqs, ``missing`` records and absent files yield an empty
        result with a zero summary. Malformed lines are skipped.
        """
        result = TraceEventsResult()
        listing = self.list_trace_records(bead_id)
        resolved = self.resolve_trace_seq(bead_id, seq)
        if resolved is None or resolved not in listing.seqs:
            return result
        meta = listing.records[listing.seqs.index(resolved)]
        if meta.get("status") == "missing":
            return result
        bead = self.get_bead(bead_id)
        matched_id = bead.get("bead_id") if bead else None
        if not isinstance(matched_id, str) or not matched_id:
            return result
        path = self.traces_dir(matched_id) / f"{resolved}.events.jsonl"
        offset = max(0, int(offset))
        limit = max(0, int(limit))
        summary = result.summary
        per_tool: dict[str, int] = summary["tool_counts"]
        usage: dict[str, int] = summary["usage"]
        matched = 0
        try:
            handle = path.open("rb")
        except OSError:
            return result
        with handle:
            for raw in handle:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    event = json.loads(raw.decode("utf-8", errors="replace"))
                except ValueError:
                    continue
                if not isinstance(event, dict):
                    continue
                ev_kind = event.get("kind")
                if ev_kind == "tool_call":
                    name = event.get("tool")
                    name = name if isinstance(name, str) else ""
                    per_tool[name] = per_tool.get(name, 0) + 1
                    if event.get("is_error") is True:
                        summary["error_count"] += 1
                    dur = event.get("duration_ms")
                    if isinstance(dur, (int, float)) and not isinstance(dur, bool):
                        summary["total_duration_ms"] += dur
                elif ev_kind == "usage":
                    for key in _USAGE_KEYS:
                        val = event.get(key)
                        if isinstance(val, (int, float)) and not isinstance(val, bool):
                            usage[key] += int(val)
                elif ev_kind == "assistant_text":
                    summary["assistant_messages"] += 1
                if kind and ev_kind != kind:
                    continue
                if errors_only and not (ev_kind == "tool_call" and event.get("is_error") is True):
                    continue
                if tool and not (ev_kind == "tool_call" and event.get("tool") == tool):
                    continue
                if offset <= matched < offset + limit:
                    result.events.append(event)
                matched += 1
        result.total = matched
        return result

    def telemetry_dir(self, bead_id: str) -> Path:
        """Telemetry directory for an already-validated bead ID (internal use)."""
        return self.project_root / ".takt" / "telemetry" / bead_id

    def read_telemetry_attempt(self, bead_id: str, attempt: Any) -> dict[str, Any] | None:
        """Return one telemetry attempt with prompt/response capped, or None.

        The bead must exist and ``attempt`` must be an integer (or ASCII-digit
        string) matching a ``<n>.json`` file in the telemetry directory listing;
        the file name is built from the listing, never from caller text.
        ``prompt_text``/``response_text`` are cut to 256 KiB of UTF-8 on a
        character boundary and ``prompt_truncated``/``response_truncated`` are
        added. All other fields pass through unchanged.
        """
        if isinstance(attempt, bool):
            return None
        if isinstance(attempt, str):
            if not (attempt.isascii() and attempt.isdigit()):
                return None
            value = int(attempt)
        elif isinstance(attempt, int):
            value = attempt
        else:
            return None
        bead = self.get_bead(bead_id)
        matched_id = bead.get("bead_id") if bead else None
        if not isinstance(matched_id, str) or not matched_id:
            return None
        try:
            names = {p.name for p in self.telemetry_dir(matched_id).glob("*.json")}
        except OSError:
            return None
        target = next(
            (n for n in names if n[:-5].isascii() and n[:-5].isdigit() and int(n[:-5]) == value),
            None,
        )
        if target is None:
            return None
        data = self._parse(self.telemetry_dir(matched_id) / target)
        if data is None:
            return None
        for field_name, flag in (
            ("prompt_text", "prompt_truncated"),
            ("response_text", "response_truncated"),
        ):
            text = data.get(field_name)
            truncated = False
            if isinstance(text, str):
                raw = text.encode("utf-8", errors="replace")
                if len(raw) > _TELEMETRY_TEXT_CAP:
                    text = raw[:_TELEMETRY_TEXT_CAP].decode("utf-8", errors="ignore")
                    truncated = True
                data[field_name] = text
            data[flag] = truncated
        return data

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
