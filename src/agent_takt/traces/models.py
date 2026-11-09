"""Backend-neutral trace data models.

This module must not import from ``agent_takt.models`` (which imports it).
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any, TypeVar

TRACE_VERSION = 1

ROLE_MAIN = "main"
ROLE_STRUCTURED_OUTPUT_RETRY = "structured_output_retry"
ROLE_PLANNER = "planner"
TRACE_ROLES = (ROLE_MAIN, ROLE_STRUCTURED_OUTPUT_RETRY, ROLE_PLANNER)

STATUS_CAPTURED = "captured"
STATUS_MISSING = "missing"
STATUS_UNSUPPORTED = "unsupported"
STATUS_DISABLED = "disabled"
STATUS_TOO_LARGE = "too_large"
STATUS_ERROR = "error"
TRACE_STATUSES = (
    STATUS_CAPTURED,
    STATUS_MISSING,
    STATUS_UNSUPPORTED,
    STATUS_DISABLED,
    STATUS_TOO_LARGE,
    STATUS_ERROR,
)

EVENT_SESSION = "session"
EVENT_ASSISTANT_TEXT = "assistant_text"
EVENT_TOOL_CALL = "tool_call"
EVENT_USAGE = "usage"

_T = TypeVar("_T")


def _from_dict(cls: type[_T], data: dict[str, Any]) -> _T:
    names = {f.name for f in fields(cls)}  # type: ignore[arg-type]
    return cls(**{k: v for k, v in data.items() if k in names})


@dataclass
class TraceSession:
    backend: str
    session_id: str | None = None
    role: str = ROLE_MAIN
    started_at: str | None = None
    finished_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TraceSession":
        return _from_dict(cls, data)


@dataclass
class TraceRecordMeta:
    bead_id: str | None = None
    agent_type: str | None = None
    feature_root_id: str | None = None
    spec_id: str | None = None
    seq: int = 0
    backend: str = ""
    session_id: str | None = None
    role: str = ROLE_MAIN
    status: str = STATUS_CAPTURED
    started_at: str | None = None
    finished_at: str | None = None
    outcome: str | None = None
    source_path: str | None = None
    raw_bytes: int | None = None
    tool_calls: int | None = None
    malformed_lines: int | None = None
    telemetry_attempt: int | None = None
    error: str | None = None
    trace_version: int = TRACE_VERSION

    def to_dict(self) -> dict[str, Any]:
        data = {f.name: getattr(self, f.name) for f in fields(self)}
        # trace_version first for readability
        return {"trace_version": data.pop("trace_version"), **data}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TraceRecordMeta":
        return _from_dict(cls, data)


def session_event(
    session_id: str | None,
    cwd: str | None = None,
    model: str | None = None,
    started_at: str | None = None,
) -> dict[str, Any]:
    event: dict[str, Any] = {"kind": EVENT_SESSION, "session_id": session_id, "cwd": cwd}
    if model is not None:
        event["model"] = model
    event["started_at"] = started_at
    return event


def assistant_text_event(ts: str | None, text: str) -> dict[str, Any]:
    return {
        "kind": EVENT_ASSISTANT_TEXT,
        "ts": ts,
        "text_excerpt": text[:500],
        "text_chars": len(text),
    }


def tool_call_event(
    ts: str | None,
    call_id: str,
    tool: str,
    input: Any = None,
    result_ts: str | None = None,
    duration_ms: int | None = None,
    is_error: bool = False,
    result_text: str = "",
) -> dict[str, Any]:
    return {
        "kind": EVENT_TOOL_CALL,
        "ts": ts,
        "call_id": call_id,
        "tool": tool,
        "input": input,
        "result_ts": result_ts,
        "duration_ms": duration_ms,
        "is_error": bool(is_error),
        "result_excerpt": result_text[:1000],
        "result_chars": len(result_text),
    }


def usage_event(
    ts: str | None,
    message_id: str | None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
) -> dict[str, Any]:
    return {
        "kind": EVENT_USAGE,
        "ts": ts,
        "message_id": message_id,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read_tokens,
        "cache_creation_tokens": cache_creation_tokens,
    }


_E = TypeVar("_E", bound=BaseException)


def attach_trace_sessions(exc: _E, sessions: list[TraceSession]) -> _E:
    """Set ``exc.trace_sessions`` and return the same exception unchanged otherwise."""
    exc.trace_sessions = list(sessions)  # type: ignore[attr-defined]
    return exc
