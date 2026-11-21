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

from datetime import datetime, timedelta, timezone
from typing import Any

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
