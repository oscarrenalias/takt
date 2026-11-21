"""Tolerant, read-only access to a project's ``.takt/beads/*.json`` files.

Beads are read as raw JSON dicts so unknown fields pass through untouched and
missing optional fields never cause errors (ADR-1897d241). Parsed beads are
cached per path keyed by ``(size, mtime_ns)`` so unchanged files are not
re-parsed on each poll.

It also reads recent events, scheduler state, ``version.json`` and file-based
health. This module imports nothing from ``agent_takt`` and spawns no subprocess.
"""

from __future__ import annotations

import base64
import json
import os
import re
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


def _audit_stem_to_iso(stem: str) -> str | None:
    """Convert ``2026-05-05T08-02-51.123456Z`` to ISO form; None if unparseable."""
    date, sep, rest = stem.partition("T")
    if not sep:
        return None
    clock, dot, frac = rest.partition(".")
    parts = clock.split("-")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        return None
    return f"{date}T{':'.join(parts)}{dot}{frac}"


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

    def _audits_dir_for(self, bead_id: str) -> Path | None:
        """Audit directory for the feature root of the matched bead, or None."""
        bead = self.get_bead(bead_id)
        if bead is None:
            return None
        root_id = bead.get("feature_root_id") or bead.get("bead_id")
        if (
            not isinstance(root_id, str)
            or not root_id
            or root_id in (".", "..")
            or "/" in root_id
            or "\\" in root_id
            or "\x00" in root_id
        ):
            return None
        return self.project_root / ".takt" / "audits" / root_id

    def _audit_files(self, bead_id: str) -> dict[str, Path]:
        """Map of stem -> path for ``<timestamp>.json`` files (never caller-built)."""
        directory = self._audits_dir_for(bead_id)
        if directory is None:
            return {}
        try:
            paths = [p for p in directory.glob("*.json") if p.is_file()]
        except OSError:
            return {}
        return {p.name[: -len(".json")]: p for p in paths}

    def list_audits(self, bead_id: str) -> list[dict[str, Any]]:
        """List stored audit snapshots of the bead's feature tree, newest first.

        Each entry is ``{audit_id, generated_at, size}``. ``audit_id`` is the
        file stem (names sort chronologically). ``generated_at`` is
        ``feature.generated_at`` from the stored report when readable, else
        derived from the stem, else None. Snapshots are never rebuilt.
        """
        entries: list[dict[str, Any]] = []
        for stem, path in self._audit_files(bead_id).items():
            try:
                size = path.stat().st_size
            except OSError:
                continue
            generated_at: Any = None
            data = self._parse(path)
            if data is not None:
                feature = data.get("feature")
                if isinstance(feature, dict) and isinstance(feature.get("generated_at"), str):
                    generated_at = feature["generated_at"]
            if generated_at is None:
                generated_at = _audit_stem_to_iso(stem)
            entries.append({"audit_id": stem, "generated_at": generated_at, "size": size})
        entries.sort(key=lambda e: e["audit_id"], reverse=True)
        return entries

    def read_audit(self, bead_id: str, audit_id: Any) -> dict[str, Any] | None:
        """Return a stored audit snapshot as written, or None.

        ``audit_id`` must equal a stem present in the directory listing; it is
        never joined into a path, so traversal-style input matches nothing.
        """
        if not isinstance(audit_id, str) or not audit_id:
            return None
        path = self._audit_files(bead_id).get(audit_id)
        if path is None:
            return None
        return self._parse(path)

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


EVENT_BLOCK_BYTES = 64 * 1024
CURSOR_VERSION = 1


class InvalidCursor(ValueError):
    """Raised when a paging cursor cannot be decoded."""


@dataclass(frozen=True)
class EventPosition:
    """Where to continue strictly older: byte offset of a line start + file identity.

    The next page covers only bytes ``[0, offset)``, so events that share a
    timestamp are never duplicated or skipped. ``identity`` is the file's inode;
    if it no longer matches (log replaced) the reader restarts from the newest.
    """

    offset: int
    identity: int = 0


@dataclass
class EventPage:
    events: list[dict[str, Any]] = field(default_factory=list)  # newest first
    next_position: EventPosition | None = None  # None when no older events remain
    bytes_read: int = 0
    reset: bool = False  # the supplied position no longer matched the file


def encode_cursor(positions: dict[str, EventPosition]) -> str:
    """Encode ``{project: position}`` as an opaque base64url string."""
    body = {
        "v": CURSOR_VERSION,
        "p": {name: [pos.offset, pos.identity] for name, pos in positions.items()},
    }
    raw = json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> dict[str, EventPosition]:
    """Inverse of :func:`encode_cursor`; raises :class:`InvalidCursor` on bad input."""
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        body = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
        if not isinstance(body, dict) or body.get("v") != CURSOR_VERSION:
            raise ValueError("bad version")
        raw_positions = body.get("p")
        if not isinstance(raw_positions, dict):
            raise ValueError("bad positions")
        result: dict[str, EventPosition] = {}
        for name, value in raw_positions.items():
            if (
                not isinstance(value, list)
                or len(value) != 2
                or any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in value)
            ):
                raise ValueError("bad position")
            result[str(name)] = EventPosition(offset=value[0], identity=value[1])
        return result
    except (ValueError, TypeError, UnicodeError) as exc:  # binascii.Error is a ValueError
        raise InvalidCursor("invalid cursor") from exc


def read_events_page(
    project_root: str | Path,
    limit: int = 25,
    before: EventPosition | None = None,
    block_size: int = EVENT_BLOCK_BYTES,
) -> EventPage:
    """Read up to ``limit`` events older than ``before`` (newest first).

    Reads fixed-size blocks backwards from ``before.offset`` (or EOF), so a
    first page costs a few blocks regardless of file size. Malformed, blank and
    non-dict lines are skipped. A missing or unreadable file yields an empty page.
    """
    page = EventPage()
    if limit <= 0:
        return page
    block_size = max(1, block_size)
    path = Path(project_root) / ".takt" / "logs" / "events.jsonl"
    try:
        with path.open("rb") as handle:
            stat = os.fstat(handle.fileno())
            size = stat.st_size
            identity = stat.st_ino
            end = size
            if before is not None:
                if before.identity != identity or before.offset > size:
                    page.reset = True
                else:
                    end = before.offset
            pos = end
            buf = b""
            while True:
                start = max(0, pos - block_size)
                handle.seek(start)
                block = handle.read(pos - start)
                page.bytes_read += len(block)
                buf = block + buf
                pos = start
                if pos == 0 or buf.count(b"\n") > limit * 2 + 1:
                    parsed = _parse_backwards(buf, pos)
                    if pos == 0 or len(parsed) > limit:
                        break
    except OSError:
        return EventPage()
    selected = parsed[:limit]
    page.events = [record for _, record in selected]
    if len(parsed) > limit and selected:
        page.next_position = EventPosition(offset=selected[-1][0], identity=identity)
    return page


def _parse_backwards(buf: bytes, base: int) -> list[tuple[int, dict[str, Any]]]:
    """Valid records in ``buf`` as ``(line_start_offset, record)``, newest first.

    When ``base > 0`` the first segment may be a cut-off line and is dropped.
    """
    out: list[tuple[int, dict[str, Any]]] = []
    offset = base
    segments = buf.split(b"\n")
    for index, segment in enumerate(segments):
        line_start = offset
        offset += len(segment) + 1
        if index == 0 and base > 0:
            continue
        raw = segment.strip()
        if not raw:
            continue
        try:
            record = json.loads(raw.decode("utf-8", errors="replace"))
        except ValueError:
            continue
        if isinstance(record, dict):
            out.append((line_start, record))
    out.reverse()
    return out


def read_recent_events(project_root: str | Path, limit: int = 100) -> list[dict[str, Any]]:
    """Return up to ``limit`` most recent events (oldest first).

    Only the tail of ``events.jsonl`` is read; malformed lines and non-dict
    records are skipped. A missing or unreadable file yields ``[]``.
    """
    page = read_events_page(project_root, limit)
    return list(reversed(page.events))


def read_scheduler_runs(project_name: str, project_root: str | Path) -> list[dict[str, Any]]:
    """Scheduler run rows for a project, via the shared fleet helper."""
    try:
        return read_project_runs(project_name, Path(project_root))
    except Exception:
        return []


_RUN_ID_RE = re.compile(r"^SR-[0-9a-f]{8}$")


def is_valid_run_id(run_id: Any) -> bool:
    """True for a well-formed scheduler run id (``SR-<8 lowercase hex>``)."""
    return isinstance(run_id, str) and _RUN_ID_RE.fullmatch(run_id) is not None


def list_project_runs(project_name: str, project_root: str | Path) -> list[dict[str, Any]]:
    """Scheduler runs of a project, newest first.

    Each row has ``run_id``, ``status`` (``running``; ``stale`` for a run with
    no finish event and no live marker; otherwise the ``exit_reason``),
    ``started_at``, ``finished_at``, ``cycles``, ``started_count``,
    ``max_workers``, ``runner`` and ``feature_root_id``. Missing values are None
    (timestamps: None when absent).
    """
    rows: list[dict[str, Any]] = []
    for raw in read_scheduler_runs(project_name, project_root):
        status = raw.get("status")
        if status in (None, "", "unknown"):
            status = "stale"
        rows.append(
            {
                "run_id": raw.get("run_id"),
                "status": status,
                "started_at": raw.get("started_at") or None,
                "finished_at": raw.get("finished_at") or None,
                "cycles": raw.get("cycles"),
                "started_count": raw.get("started_count"),
                "max_workers": raw.get("max_workers"),
                "runner": raw.get("runner"),
                "feature_root_id": raw.get("feature_root_id"),
            }
        )
    rows.sort(key=lambda r: r["started_at"] or r["finished_at"] or "", reverse=True)
    return rows


def read_run_events(project_root: str | Path, run_id: str) -> list[dict[str, Any]] | None:
    """All events whose payload ``run_id`` equals ``run_id``, in file order.

    Returns None for a malformed ``run_id`` (the log is not read). A missing or
    unreadable log yields ``[]``; malformed lines are skipped.
    """
    if not is_valid_run_id(run_id):
        return None
    path = Path(project_root) / ".takt" / "logs" / "events.jsonl"
    events: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if run_id not in line:  # cheap prefilter before parsing
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(record, dict):
                    continue
                payload = record.get("payload")
                if isinstance(payload, dict) and payload.get("run_id") == run_id:
                    events.append(record)
    except OSError:
        return []
    return events


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
