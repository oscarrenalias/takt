"""Normalise Claude Code JSONL transcripts into backend-neutral trace events."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable

from .models import (
    EVENT_TOOL_CALL,
    assistant_text_event,
    session_event,
    tool_call_event,
    usage_event,
)

STRUCTURED_OUTPUT_TOOL = "StructuredOutput"


@dataclass
class NormalizeResult:
    events: list[dict[str, Any]] = field(default_factory=list)
    malformed_lines: int = 0


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _duration_ms(start: Any, end: Any) -> int | None:
    a, b = _parse_ts(start), _parse_ts(end)
    if a is None or b is None:
        return None
    try:
        return int(round((b - a).total_seconds() * 1000))
    except TypeError:  # naive vs aware
        return None


def _result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    return ""


def _int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def normalize_claude_transcript(lines: Iterable[str | bytes]) -> NormalizeResult:
    """Convert transcript lines into trace events (single streaming pass)."""
    result = NormalizeResult()
    events = result.events
    calls: dict[str, dict[str, Any]] = {}
    seen_calls: set[str] = set()
    seen_messages: set[str] = set()
    session: dict[str, Any] | None = None

    for raw in lines:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        raw = raw.strip()
        if not raw:
            continue
        try:
            entry = json.loads(raw)
        except (ValueError, TypeError):
            result.malformed_lines += 1
            continue
        if not isinstance(entry, dict):
            result.malformed_lines += 1
            continue

        message = entry.get("message")
        if not isinstance(message, dict):
            message = {}

        if session is None and (entry.get("sessionId") or entry.get("cwd")):
            model = message.get("model")
            session = session_event(
                entry.get("sessionId"),
                entry.get("cwd"),
                model if isinstance(model, str) and model else None,
                entry.get("timestamp"),
            )
            events.append(session)
        elif (
            session is not None
            and "model" not in session
            and entry.get("type") == "assistant"
            and isinstance(message.get("model"), str)
            and message["model"]
        ):
            session["model"] = message["model"]

        etype = entry.get("type")
        if etype not in ("assistant", "user"):
            continue
        ts = entry.get("timestamp")
        content = message.get("content")

        if etype == "assistant":
            mid = message.get("id")
            usage = message.get("usage")
            if isinstance(mid, str) and mid and mid not in seen_messages:
                seen_messages.add(mid)
                if isinstance(usage, dict):
                    events.append(
                        usage_event(
                            ts,
                            mid,
                            _int(usage.get("input_tokens")),
                            _int(usage.get("output_tokens")),
                            _int(usage.get("cache_read_input_tokens")),
                            _int(usage.get("cache_creation_input_tokens")),
                        )
                    )
            if isinstance(content, str):
                if content:
                    events.append(assistant_text_event(ts, content))
            elif isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    btype = block.get("type")
                    if btype == "text":
                        text = block.get("text")
                        if isinstance(text, str) and text:
                            events.append(assistant_text_event(ts, text))
                    elif btype == "tool_use":
                        cid = block.get("id")
                        if not isinstance(cid, str) or not cid or cid in seen_calls:
                            continue
                        seen_calls.add(cid)
                        ev = tool_call_event(
                            ts, cid, str(block.get("name") or ""), block.get("input")
                        )
                        calls[cid] = ev
                        events.append(ev)
        else:
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                ev = calls.get(block.get("tool_use_id"))
                if ev is None:
                    continue
                text = _result_text(block.get("content"))
                ev["result_ts"] = ts
                ev["duration_ms"] = _duration_ms(ev.get("ts"), ts)
                ev["is_error"] = bool(block.get("is_error", False))
                ev["result_excerpt"] = text[:1000]
                ev["result_chars"] = len(text)

    return result


def count_tool_calls(events: Iterable[dict[str, Any]]) -> int:
    """Count tool_call events, excluding StructuredOutput."""
    return sum(
        1
        for e in events
        if e.get("kind") == EVENT_TOOL_CALL and e.get("tool") != STRUCTURED_OUTPUT_TOOL
    )
