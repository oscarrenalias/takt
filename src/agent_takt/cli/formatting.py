from __future__ import annotations

import json
import re

from ..adr import Adr
from ..models import Bead


LIST_PLAIN_COLUMNS: tuple[tuple[str, str], ...] = (
    ("BEAD_ID", "bead_id"),
    ("STATUS", "status"),
    ("AGENT", "agent_type"),
    ("TYPE", "bead_type"),
    ("PRIORITY", "priority"),
    ("TITLE", "title"),
    ("FEATURE_ROOT", "feature_root_id"),
    ("PARENT", "parent_id"),
)

ADR_LIST_PLAIN_COLUMNS: tuple[tuple[str, str], ...] = (
    ("ID", "id"),
    ("STATUS", "status"),
    ("TITLE", "title"),
    ("CREATED", "created_at"),
)

# Maps lowercase body-section key → (heading text without '#' prefix, heading level)
_BODY_SECTION_MAP: dict[str, tuple[str, int]] = {
    "summary": ("Summary", 2),
    "context": ("Context", 2),
    "decision_drivers": ("Decision Drivers", 2),
    "considered_options": ("Considered Options", 2),
    "decision": ("Decision", 2),
    "consequences": ("Consequences", 2),
    "consequences.positive": ("Positive", 3),
    "consequences.negative": ("Negative", 3),
}

# Keys whose section is a subsection of a parent section
_NESTED_SECTION_PARENT: dict[str, str] = {
    "consequences.positive": "consequences",
    "consequences.negative": "consequences",
}


def _plain_value(value: object) -> str:
    if value is None:
        return "-"
    if isinstance(value, str):
        return value or "-"
    return str(value)


def _column_value(attribute: str, value: object) -> str:
    if attribute == "priority":
        return "" if value is None else str(value)
    return _plain_value(value)


def format_bead_list_plain(beads: list[Bead]) -> str:
    ordered = sorted(
        beads,
        key=lambda bead: (bead.execution_history[0].timestamp if bead.execution_history else "", bead.bead_id),
    )
    if not ordered:
        return "No beads found."

    rows = [
        [_column_value(attribute, getattr(bead, attribute, None)) for _, attribute in LIST_PLAIN_COLUMNS]
        for bead in ordered
    ]
    widths = [
        max(len(header), max((len(row[column_index]) for row in rows), default=0))
        for column_index, (header, _) in enumerate(LIST_PLAIN_COLUMNS)
    ]

    header_line = "  ".join(
        header.ljust(widths[column_index])
        for column_index, (header, _) in enumerate(LIST_PLAIN_COLUMNS)
    )
    row_lines = [
        "  ".join(
            value.ljust(widths[column_index])
            for column_index, value in enumerate(row)
        )
        for row in rows
    ]
    return "\n".join([header_line, *row_lines])


def format_bead_history_plain(
    entries: list[dict[str, object]],
    *,
    plain: bool = False,
    terminal_width: int | None = None,
) -> str:
    if not entries:
        return "No history."

    sorted_entries = sorted(entries, key=lambda e: str(e.get("timestamp", "")))

    def _truncate_ts(ts: object) -> str:
        s = str(ts) if ts is not None else ""
        # Strip fractional seconds: 2026-05-05T07:40:01.234567+00:00 → 2026-05-05T07:40:01
        dot = s.find(".")
        if dot != -1:
            s = s[:dot]
        # Strip timezone that follows the seconds (e.g. +00:00 appended with no dot)
        elif len(s) > 19:
            s = s[:19]
        return s

    event_col_width = max((len(str(e.get("event", ""))) for e in sorted_entries), default=0)

    lines = []
    for entry in sorted_entries:
        ts = _truncate_ts(entry.get("timestamp", ""))
        event = str(entry.get("event", "")).ljust(event_col_width)
        summary = str(entry.get("summary", ""))
        prefix = f"[{ts}] {event}  "
        if not plain and terminal_width is not None:
            available = terminal_width - len(prefix)
            if available > 0 and len(summary) > available:
                summary = summary[:available]
        lines.append(f"{prefix}{summary}")

    return "\n".join(lines)


def format_bead_field(value: object) -> str:
    if value is None or value == "":
        return ""
    # bool must be checked before int since bool is a subclass of int
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value
    if isinstance(value, (list, dict)):
        return json.dumps(value, indent=2)
    return str(value)


def format_claims_plain(claims: list[dict[str, object]]) -> str:
    if not claims:
        return "No active claims."

    lines: list[str] = []
    for claim in claims:
        lease_owner = "-"
        lease = claim.get("lease")
        if isinstance(lease, dict):
            lease_owner = _plain_value(lease.get("owner"))
        lines.append(
            f"{_plain_value(claim.get('bead_id'))} | "
            f"{_plain_value(claim.get('agent_type'))} | "
            f"feature={_plain_value(claim.get('feature_root_id'))} | "
            f"lease={lease_owner}"
        )
    return "\n".join(lines)


def _resolve_path(data: object, path: str) -> object:
    """Resolve a dotted path with bracket-style array indexing against a dict."""
    segments: list[str | int] = []
    for part in path.split("."):
        m = re.match(r"^([^\[]+)(\[(-?\d+)\])?$", part)
        if not m:
            raise KeyError(f"invalid path segment: {part!r}")
        key = m.group(1)
        idx_str = m.group(3)
        segments.append(key)
        if idx_str is not None:
            segments.append(int(idx_str))

    current = data
    for seg in segments:
        if isinstance(seg, int):
            if not isinstance(current, list):
                raise TypeError(f"expected list for index {seg}")
            current = current[seg]
        elif isinstance(current, dict):
            if seg not in current:
                raise KeyError(f"key not found: {seg!r}")
            current = current[seg]
        else:
            raise TypeError(f"cannot traverse into {type(current).__name__}")
    return current


def _extract_markdown_section(text: str, heading: str, level: int) -> str | None:
    """Extract the body of a markdown section by heading text and level.

    Heading lookup is case-sensitive. Stops at the next heading of equal or
    higher level. Returns None when the heading is not found.
    """
    prefix = "#" * level
    in_section = False
    section_lines: list[str] = []

    for line in text.split("\n"):
        if re.match(r"^" + re.escape(prefix) + r"\s+" + re.escape(heading) + r"\s*$", line):
            in_section = True
            continue
        if in_section:
            if re.match(r"^#{1," + str(level) + r"}\s", line):
                break
            section_lines.append(line)

    if not in_section:
        return None
    return "\n".join(section_lines).strip()


def format_adr_list_plain(adrs: list[Adr]) -> str:
    ordered = sorted(adrs, key=lambda a: (a.created_at or "", a.id))
    if not ordered:
        return "No ADRs found."

    def _get_col(attribute: str, adr: Adr) -> str:
        if attribute == "created_at":
            s = adr.created_at or ""
            return s[:10] if len(s) >= 10 else (s or "-")
        return _plain_value(getattr(adr, attribute, None))

    rows = [
        [_get_col(attr, adr) for _, attr in ADR_LIST_PLAIN_COLUMNS]
        for adr in ordered
    ]
    widths = [
        max(len(header), max((len(row[i]) for row in rows), default=0))
        for i, (header, _) in enumerate(ADR_LIST_PLAIN_COLUMNS)
    ]
    header_line = "  ".join(
        header.ljust(widths[i]) for i, (header, _) in enumerate(ADR_LIST_PLAIN_COLUMNS)
    )
    row_lines = [
        "  ".join(value.ljust(widths[i]) for i, value in enumerate(row))
        for row in rows
    ]
    return "\n".join([header_line, *row_lines])


def format_adr_field(adr: Adr, field_path: str, body: str = "") -> str:
    """Resolve field_path against an ADR and return a formatted string.

    Resolution order:
    1. Frontmatter dotted-path with bracket-style array indexing
       (e.g. 'status', 'authors[0]', 'tags[1]')
    2. Reserved body-section key, case-insensitive
       (e.g. 'decision', 'consequences.positive')

    Scalar values are returned as bare strings; bools as lowercase 'true'/'false';
    lists/dicts as pretty JSON (indent=2). Raises ValueError('field not found: <path>')
    when neither the frontmatter path nor the body-section key resolves.
    """
    # Step 1: frontmatter dotted-path
    try:
        value = _resolve_path(adr.to_dict(), field_path)
        return format_bead_field(value)
    except (KeyError, IndexError, TypeError):
        pass

    # Step 2: reserved body-section key (case-insensitive)
    key = field_path.lower()
    if key in _BODY_SECTION_MAP:
        heading, level = _BODY_SECTION_MAP[key]
        search_text = body
        if key in _NESTED_SECTION_PARENT:
            parent_key = _NESTED_SECTION_PARENT[key]
            parent_heading, parent_level = _BODY_SECTION_MAP[parent_key]
            parent_text = _extract_markdown_section(body, parent_heading, parent_level)
            search_text = parent_text if parent_text is not None else ""
        result = _extract_markdown_section(search_text, heading, level)
        if result is not None:
            return result

    raise ValueError(f"field not found: {field_path}")


