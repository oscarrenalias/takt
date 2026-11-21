"""Shared one-line summary and severity for scheduler events.

Used by the watch CLI formatter and the web UI view models so both describe
events identically. Operates only on the documented ``event_type`` + payload
shape from ``.takt/logs/events.jsonl`` and tolerates unknown types.
"""
from __future__ import annotations

from typing import Any

_MAX_DETAIL = 120

_SUCCESS_TYPES = frozenset({
    "bead_completed",
    "feature_merged",
})
_ERROR_TYPES = frozenset({
    "bead_failed",
    "merge_gate_failed",
    "transient_retries_exhausted",
})
_WARNING_TYPES = frozenset({
    "bead_blocked",
    "lease_expired",
    "verdict_overridden",
    "structural_assumption_recorded",
    "criteria_gap_recorded",
})
_KNOWN_TYPES = frozenset({
    "bead_started", "bead_deferred", "bead_deleted", "bead_blocked",
    "bead_completed", "bead_failed", "feature_merged", "merge_gate_failed",
    "transient_retries_exhausted", "lease_expired", "verdict_overridden",
    "structural_assumption_recorded", "criteria_gap_recorded",
    "issue_discovered", "trace_captured", "worktree_ready", "retried",
    "scheduler_run_started", "scheduler_run_finished",
    "scheduler_cycle_started", "scheduler_cycle_completed",
})


def event_detail(payload: Any) -> str:
    """Return the first non-empty of summary/title/reason, whitespace-collapsed
    and truncated to 120 characters; empty string when none is present."""
    if not isinstance(payload, dict):
        return ""
    detail = ""
    for key in ("summary", "title", "reason"):
        value = payload.get(key)
        if value is not None and str(value).strip():
            detail = " ".join(str(value).split())
            break
    if len(detail) > _MAX_DETAIL:
        detail = detail[: _MAX_DETAIL - 1] + "…"
    return detail


def summarize_event(event_type: str, payload: Any) -> str:
    """One-line summary: the payload detail if any, else the event type.

    Unknown event types always include the type: ``"<type>: <detail>"``."""
    detail = event_detail(payload)
    et = str(event_type or "")
    if detail:
        if et and et not in _KNOWN_TYPES:
            return f"{et}: {detail}"
        return detail
    return et or "unknown event"


def event_severity(event_type: str, payload: Any) -> str:
    """Return 'info', 'success', 'warning' or 'error' for an event."""
    et = str(event_type or "")
    if et in _ERROR_TYPES:
        return "error"
    if et in _WARNING_TYPES:
        return "warning"
    if et in _SUCCESS_TYPES:
        return "success"
    if et == "scheduler_run_finished":
        reason = payload.get("exit_reason") if isinstance(payload, dict) else None
        if reason in ("error", "crashed"):
            return "error"
        if reason in ("interrupted", "terminated", "max_cycles"):
            return "warning"
        return "info"
    return "info"
