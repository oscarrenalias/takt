"""``tool_usage`` analyser: per-tool calls, errors and duration."""
from __future__ import annotations

from typing import Any, Sequence

EXCLUDED_TOOLS = frozenset({"StructuredOutput"})


def analyze_tool_usage(events: Sequence[dict[str, Any]], context: Any = None) -> dict[str, Any]:
    by_tool: dict[str, dict[str, int]] = {}
    tool_calls = errors = api_messages = 0
    for event in events:
        kind = event.get("kind")
        if kind == "usage":
            api_messages += 1
        elif kind == "tool_call":
            tool = event.get("tool")
            if not tool or tool in EXCLUDED_TOOLS:
                continue
            entry = by_tool.setdefault(tool, {"calls": 0, "errors": 0, "duration_ms": 0})
            entry["calls"] += 1
            tool_calls += 1
            if event.get("is_error"):
                entry["errors"] += 1
                errors += 1
            duration = event.get("duration_ms")
            if isinstance(duration, (int, float)) and not isinstance(duration, bool):
                entry["duration_ms"] += int(duration)
    return {
        "tool_calls": tool_calls,
        "by_tool": by_tool,
        "errors": errors,
        "error_rate": (errors / tool_calls) if tool_calls else 0.0,
        "api_messages": api_messages,
    }
