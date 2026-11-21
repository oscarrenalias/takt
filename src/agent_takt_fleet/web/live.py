"""Live agent session detection from parsed ``events.jsonl`` events.

Pure functions, stdlib only. Events are the dicts produced by the readers in
:mod:`agent_takt_fleet.web.readers` (``{timestamp, event_type, payload}``),
passed oldest first. "Later" means later in the list.

A session is *live* when an ``agent_session_started`` event has no later
``trace_captured`` with the same ``session_id``, no later ``bead_completed``,
``bead_blocked`` or ``bead_failed`` for the same ``bead_id``, and its
``started_at`` is less than :data:`LIVE_MAX_AGE` before ``now``.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from agent_takt.traces.normalize import normalize_claude_transcript

LIVE_MAX_AGE = timedelta(hours=6)
BEAD_END_EVENTS = frozenset({"bead_completed", "bead_blocked", "bead_failed"})


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _payload(event: Any) -> dict[str, Any]:
    if not isinstance(event, dict):
        return {}
    payload = event.get("payload")
    return payload if isinstance(payload, dict) else {}


def _started_sessions(events: list[dict[str, Any]]) -> list[tuple[int, dict[str, Any]]]:
    """Return ``(index, session)`` for every well-formed start event."""
    sessions: list[tuple[int, dict[str, Any]]] = []
    for idx, event in enumerate(events):
        if not isinstance(event, dict) or event.get("event_type") != "agent_session_started":
            continue
        payload = _payload(event)
        session_id = payload.get("session_id")
        bead_id = payload.get("bead_id")
        if not session_id or not isinstance(session_id, str):
            continue
        started_at = payload.get("started_at") or event.get("timestamp")
        sessions.append(
            (
                idx,
                {
                    "session_id": session_id,
                    "bead_id": bead_id if isinstance(bead_id, str) else None,
                    "role": payload.get("role"),
                    "agent_type": payload.get("agent_type"),
                    "feature_root_id": payload.get("feature_root_id"),
                    "backend": payload.get("backend"),
                    "started_at": started_at,
                },
            )
        )
    return sessions


def _find_end(events: list[dict[str, Any]], start_idx: int, session: dict[str, Any]) -> dict[str, Any] | None:
    """Return the first event after ``start_idx`` that ends ``session``.

    Result is ``{"ended_at", "reason", "trace"}``; ``trace`` is the capturing
    ``trace_captured`` reference (``bead_id``, ``seq``, ``session_id``) or None.
    """
    for event in events[start_idx + 1 :]:
        if not isinstance(event, dict):
            continue
        etype = event.get("event_type")
        payload = _payload(event)
        if etype == "trace_captured" and payload.get("session_id") == session["session_id"]:
            return {
                "ended_at": event.get("timestamp"),
                "reason": "trace_captured",
                "trace": {
                    "bead_id": payload.get("bead_id"),
                    "seq": payload.get("seq"),
                    "session_id": payload.get("session_id"),
                },
            }
        if (
            etype in BEAD_END_EVENTS
            and session["bead_id"] is not None
            and payload.get("bead_id") == session["bead_id"]
        ):
            return {"ended_at": event.get("timestamp"), "reason": etype, "trace": None}
    return None


def live_sessions(events: list[dict[str, Any]], now: datetime | None = None) -> list[dict[str, Any]]:
    """Return the live sessions in ``events`` (oldest start first).

    ``now`` is injectable for tests; it defaults to the current UTC time.
    """
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    live: list[dict[str, Any]] = []
    for idx, session in _started_sessions(events):
        started = _parse_ts(session["started_at"])
        if started is None or current - started >= LIVE_MAX_AGE:
            continue
        if _find_end(events, idx, session) is not None:
            continue
        live.append(dict(session))
    return live


def last_ended_sessions(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Return the most recent ended session per bead, keyed by ``bead_id``.

    Each value is the session dict plus ``ended_at``, ``end_reason`` and
    ``trace`` (``{bead_id, seq, session_id}`` of the ``trace_captured`` event
    that ended it, or ``None`` when it ended by a bead event). Sessions that
    never ended are excluded; the latest start wins per bead.
    """
    result: dict[str, dict[str, Any]] = {}
    for idx, session in _started_sessions(events):
        bead_id = session["bead_id"]
        if bead_id is None:
            continue
        end = _find_end(events, idx, session)
        if end is None:
            continue
        # Later starts overwrite earlier ones (events are oldest first).
        result[bead_id] = {
            **session,
            "ended_at": end["ended_at"],
            "end_reason": end["reason"],
            "trace": end["trace"],
        }
    return result


def last_ended_session(events: list[dict[str, Any]], bead_id: str) -> dict[str, Any] | None:
    """Return the most recent ended session for ``bead_id``, if any."""
    return last_ended_sessions(events).get(bead_id)


# --- Transcript location -------------------------------------------------

@dataclass(frozen=True)
class TranscriptLookup:
    """Result of :func:`locate_transcript`.

    ``status`` is ``"found"`` (``path`` set), ``"waiting"`` (no transcript yet,
    not an error) or ``"rejected"`` (unsafe id or path; ``reason`` explains).
    """

    status: str
    path: Path | None = None
    reason: str = ""

    @property
    def found(self) -> bool:
        return self.status == "found"


_TRANSCRIPT_CACHE: dict[tuple[str, str], Path] = {}


def resolve_config_dir(override: str | os.PathLike[str] | None = None) -> Path:
    """Claude config dir: explicit override, else $CLAUDE_CONFIG_DIR, else ~/.claude."""
    if override:
        return Path(override).expanduser()
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".claude"


def _valid_session_id(session_id: Any) -> bool:
    if not isinstance(session_id, str) or len(session_id) != 36:
        return False
    try:
        return str(uuid.UUID(session_id)) == session_id.lower()
    except ValueError:
        return False


def locate_transcript(
    session_id: Any, config_dir: str | os.PathLike[str] | None = None
) -> TranscriptLookup:
    """Find ``<config>/projects/*/<session_id>.jsonl`` safely (read-only)."""
    if not _valid_session_id(session_id):
        return TranscriptLookup("rejected", reason="invalid session id")
    sid = session_id.lower()
    base = resolve_config_dir(config_dir)
    key = (str(base), sid)
    cached = _TRANSCRIPT_CACHE.get(key)
    if cached is not None:
        if cached.is_file():
            return TranscriptLookup("found", cached)
        _TRANSCRIPT_CACHE.pop(key, None)
    projects = base / "projects"
    try:
        root = projects.resolve(strict=True)
        candidates = sorted(projects.glob(f"*/{sid}.jsonl"))
    except OSError:
        return TranscriptLookup("waiting")
    for candidate in candidates:
        try:
            real = candidate.resolve(strict=True)
        except OSError:
            continue
        if not real.is_relative_to(root):
            return TranscriptLookup("rejected", reason="path escapes projects directory")
        if real.is_file():
            _TRANSCRIPT_CACHE[key] = real
            return TranscriptLookup("found", real)
    return TranscriptLookup("waiting")


def clear_transcript_cache() -> None:
    _TRANSCRIPT_CACHE.clear()


# --- Incremental transcript following -------------------------------------

TRANSCRIPT_MAX_BYTES = 50 * 1024 * 1024  # default of common.trace_max_bytes
_USAGE_KEYS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens")


def summarize_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Summary of normalised events, shaped like the P2 trace summary.

    Adds ``event_count`` and ``last_event_at`` (latest ``ts``/``result_ts``).
    """
    tool_counts: dict[str, int] = {}
    errors = 0
    duration = 0
    messages = 0
    usage = {key: 0 for key in _USAGE_KEYS}
    last_at: str | None = None
    last_parsed: datetime | None = None

    def note(ts: Any) -> None:
        nonlocal last_at, last_parsed
        parsed = _parse_ts(ts)
        if parsed is not None and (last_parsed is None or parsed >= last_parsed):
            last_at, last_parsed = ts, parsed

    for ev in events:
        if not isinstance(ev, dict):
            continue
        kind = ev.get("kind")
        note(ev.get("ts"))
        if kind == "tool_call":
            tool = str(ev.get("tool") or "")
            tool_counts[tool] = tool_counts.get(tool, 0) + 1
            if ev.get("is_error"):
                errors += 1
            dur = ev.get("duration_ms")
            if isinstance(dur, int) and not isinstance(dur, bool):
                duration += dur
            note(ev.get("result_ts"))
        elif kind == "assistant_text":
            messages += 1
        elif kind == "usage":
            for key in _USAGE_KEYS:
                val = ev.get(key)
                if isinstance(val, int) and not isinstance(val, bool):
                    usage[key] += val
    return {
        "tool_counts": tool_counts,
        "error_count": errors,
        "total_duration_ms": duration,
        "usage": usage,
        "assistant_messages": messages,
        "event_count": len(events),
        "last_event_at": last_at,
    }


class TranscriptFollower:
    """Follows one growing transcript file, reading only appended bytes.

    ``status`` is ``"ok"``, ``"waiting"`` (file missing), ``"too_large"``
    (size over ``max_bytes``; following stops for good) or ``"error"``
    (unreadable file). ``error`` carries a note when normalisation or reading
    failed. A trailing line without a newline is held back until completed.
    """

    def __init__(self, path: str | os.PathLike[str], max_bytes: int = TRANSCRIPT_MAX_BYTES) -> None:
        self.path = Path(path)
        self.max_bytes = max_bytes
        self.offset = 0
        self.status = "waiting"
        self.error: str | None = None
        self.events: list[dict[str, Any]] = []
        self.summary: dict[str, Any] = summarize_events([])
        self._lines: list[bytes] = []
        self._partial = b""
        self.bytes_read = 0  # total bytes read from disk (for observability/tests)

    def _reset(self) -> None:
        self.offset = 0
        self._lines = []
        self._partial = b""
        self.events = []
        self.summary = summarize_events([])

    def refresh(self) -> dict[str, Any]:
        """Read appended bytes, re-normalise if new lines arrived, return state."""
        if self.status != "too_large":
            self._advance()
        return self.state()

    def _advance(self) -> None:
        try:
            size = self.path.stat().st_size
        except FileNotFoundError:
            self.status = "waiting"
            return
        except OSError as exc:
            self.status, self.error = "error", f"cannot stat transcript: {exc}"
            return
        if size > self.max_bytes:
            self.status = "too_large"
            return
        if size < self.offset:  # truncated or replaced: start over
            self._reset()
        new_lines = False
        if size > self.offset:
            try:
                with open(self.path, "rb") as fh:
                    fh.seek(self.offset)
                    chunk = fh.read(size - self.offset)
            except OSError as exc:
                self.status, self.error = "error", f"cannot read transcript: {exc}"
                return
            self.offset += len(chunk)
            self.bytes_read += len(chunk)
            data = self._partial + chunk
            parts = data.split(b"\n")
            self._partial = parts.pop()
            complete = [part for part in parts if part.strip()]
            if complete:
                self._lines.extend(complete)
                new_lines = True
        self.status = "ok"
        if new_lines:
            try:
                self.events = normalize_claude_transcript(self._lines).events
                self.error = None
            except Exception as exc:  # normaliser must never break the page
                self.events = []
                self.error = f"normalisation failed: {exc}"
            self.summary = summarize_events(self.events)

    def state(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "error": self.error,
            "events": self.events,
            "summary": self.summary,
            "offset": self.offset,
        }
