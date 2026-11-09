"""``commands`` analyser: failed and repeated Bash commands."""
from __future__ import annotations

import re
from typing import Any, Sequence

EXCERPT_CHARS = 200
REPEAT_THRESHOLD = 3
_CD_PREFIX = re.compile(r"^cd\s+(?:\"[^\"]*\"|'[^']*'|\S+)\s*&&\s*")


def normalize_command(command: str) -> str:
    text = " ".join(command.split())
    return _CD_PREFIX.sub("", text, count=1)


def _excerpt(text: str) -> str:
    return text[:EXCERPT_CHARS]


def analyze_commands(events: Sequence[dict[str, Any]], context: Any = None) -> dict[str, Any]:
    total = 0
    failed: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for event in events:
        if event.get("kind") != "tool_call" or event.get("tool") != "Bash":
            continue
        payload = event.get("input")
        command = payload.get("command") if isinstance(payload, dict) else None
        if not isinstance(command, str) or not command.strip():
            continue
        total += 1
        norm = normalize_command(command)
        counts[norm] = counts.get(norm, 0) + 1
        if event.get("is_error"):
            failed.append({"call_id": event.get("call_id"), "command_excerpt": _excerpt(norm)})
    repeated = [
        {"command_excerpt": _excerpt(cmd), "count": n}
        for cmd, n in counts.items()
        if n >= REPEAT_THRESHOLD
    ]
    return {"commands": total, "failed": failed, "repeated": repeated}
