"""The single frontmatter reader/writer for specs and ADRs.

Two write styles are supported:

* ``ADR_STYLE`` – re-serialises the whole mapping with ``yaml.dump`` and lays
  the file out as ``---\\n{frontmatter}---\\n\\n{body}`` (identical to the
  historical ``adr.py`` behaviour).
* ``SPEC_STYLE`` – line-preserving: only the lines of a changed key are
  rewritten, new keys are appended, the body follows the closing ``---``
  immediately and is never modified.
"""
from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Mapping

import yaml

__all__ = [
    "FrontmatterError",
    "FrontmatterStyle",
    "AdrStyle",
    "SpecStyle",
    "ADR_STYLE",
    "SPEC_STYLE",
    "split_frontmatter",
    "read_frontmatter",
    "read_frontmatter_file",
    "render_document",
    "update_frontmatter",
    "write_document",
    "update_frontmatter_file",
]


class FrontmatterError(Exception):
    """Raised when frontmatter cannot be parsed. Carries the file path."""

    def __init__(self, path: Any, message: str) -> None:
        self.path = None if path is None else str(path)
        self.message = message
        super().__init__(f"{self.path}: {message}" if self.path else message)


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

_OPEN_RE = re.compile(r"---[ \t]*\r?\n")
_CLOSE_RE = re.compile(r"^---[ \t]*(?:\r?\n|$)", re.MULTILINE)


def split_frontmatter(text: str) -> tuple[str | None, str]:
    """Split *text* into (raw frontmatter text, body).

    The raw frontmatter excludes the ``---`` delimiter lines. Returns
    ``(None, text)`` when there is no complete frontmatter block. The body is
    everything after the closing delimiter line, untouched.
    """
    m = _OPEN_RE.match(text)
    if not m:
        return None, text
    start = m.end()
    close = _CLOSE_RE.search(text, start)
    if not close:
        return None, text
    return text[start:close.start()], text[close.end():]


def read_frontmatter(text: str, path: Any = None) -> tuple[dict[str, Any] | None, str]:
    """Parse frontmatter from *text*; return ``(mapping | None, body)``.

    Text without frontmatter yields ``(None, text)``. Malformed YAML, or YAML
    that is not a mapping, raises :class:`FrontmatterError`.
    """
    raw, body = split_frontmatter(text)
    if raw is None:
        return None, text
    return _parse_mapping(raw, path), body


def _parse_mapping(raw: str, path: Any) -> dict[str, Any]:
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise FrontmatterError(path, f"malformed YAML frontmatter: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise FrontmatterError(path, "frontmatter is not a YAML mapping")
    return data


def read_frontmatter_file(path: str | Path) -> tuple[dict[str, Any] | None, str]:
    """Read *path* and parse its frontmatter (see :func:`read_frontmatter`)."""
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise FrontmatterError(p, f"cannot read file: {exc}") from exc
    return read_frontmatter(text, p)


# ---------------------------------------------------------------------------
# Spec-style value rendering
# ---------------------------------------------------------------------------

_INDICATOR_START = set("-?:,[]{}#&*!|>'\"%@`")


def _quote_if_needed(s: str) -> str:
    """Return *s* as a YAML scalar, double-quoting only when required."""
    if s and _plain_ok(s):
        return s
    return json.dumps(s, ensure_ascii=False)


def _plain_ok(s: str) -> bool:
    if s != s.strip() or s[0] in _INDICATOR_START:
        return False
    if any(c in s for c in "\n\r\t\x00") or ": " in s or " #" in s or s.endswith(":"):
        return False
    if any(ord(c) < 0x20 or ord(c) == 0x7F or c in "  \x85﻿" for c in s):
        return False
    try:
        return (
            yaml.safe_load(f"k: {s}") == {"k": s}
            and yaml.safe_load(f"- {s}") == [s]
        )
    except yaml.YAMLError:
        return False


def _scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return _quote_if_needed(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return _quote_if_needed(str(value))


def _flow(value: Any) -> str:
    """JSON flow form (valid YAML) for containers nested inside lists."""
    return json.dumps(value, ensure_ascii=False, default=str)


def _render_key(key: str, value: Any, indent: int = 0) -> list[str]:
    pad = " " * indent
    k = _quote_if_needed(str(key))
    if isinstance(value, dict):
        if not value:
            return [f"{pad}{k}: {{}}"]
        lines = [f"{pad}{k}:"]
        for sk, sv in value.items():
            lines.extend(_render_key(sk, sv, indent + 2))
        return lines
    if isinstance(value, (list, tuple)):
        if not value:
            return [f"{pad}{k}: []"]
        lines = [f"{pad}{k}:"]
        for item in value:
            if item is None:
                lines.append(f"{pad}-")
            elif isinstance(item, (dict, list, tuple)):
                lines.append(f"{pad}- {_flow(item)}")
            else:
                lines.append(f"{pad}- {_scalar(item)}")
        return lines
    return [f"{pad}{k}: {_scalar(value)}"]


# ---------------------------------------------------------------------------
# Styles
# ---------------------------------------------------------------------------


class FrontmatterStyle(ABC):
    """Strategy for writing frontmatter documents."""

    name: str = ""

    @abstractmethod
    def render(self, frontmatter: Mapping[str, Any], body: str) -> str:
        """Render a complete document from a frontmatter mapping and body."""

    @abstractmethod
    def update(self, text: str, updates: Mapping[str, Any], path: Any = None) -> str:
        """Return *text* with *updates* applied to its frontmatter."""


class AdrStyle(FrontmatterStyle):
    """Full re-serialisation via ``yaml.dump`` (matches legacy ``adr.py``)."""

    name = "adr"

    def render(self, frontmatter: Mapping[str, Any], body: str) -> str:
        fm_text = yaml.dump(
            dict(frontmatter), default_flow_style=False, allow_unicode=True, sort_keys=False
        )
        return f"---\n{fm_text}---\n\n{body}"

    def update(self, text: str, updates: Mapping[str, Any], path: Any = None) -> str:
        fm, body = read_frontmatter(text, path)
        merged = dict(fm or {})
        merged.update(updates)
        # Legacy ADR reader strips leading newlines from the body; the writer
        # re-adds exactly one blank line.
        return self.render(merged, body.lstrip("\n") if fm is not None else body)


_TOP_KEY_RE = re.compile(r"^([^\s#\-][^:]*|-[^\s:][^:]*|\"[^\"]*\"|'[^']*'):(?:[ \t]|$)")


class SpecStyle(FrontmatterStyle):
    """Line-preserving writer: touches only the lines of changed keys."""

    name = "spec"

    def render(self, frontmatter: Mapping[str, Any], body: str) -> str:
        lines: list[str] = []
        for k, v in frontmatter.items():
            lines.extend(_render_key(k, v))
        return "---\n" + "".join(l + "\n" for l in lines) + "---\n" + body

    def update(self, text: str, updates: Mapping[str, Any], path: Any = None) -> str:
        raw, body = split_frontmatter(text)
        if raw is None:
            return self.render(dict(updates), text)
        current = _parse_mapping(raw, path)
        lines = raw.splitlines(keepends=True)
        if lines and not lines[-1].endswith(("\n", "\r")):
            lines[-1] += "\n"
        nl = "\r\n" if lines and lines[0].endswith("\r\n") else "\n"

        for key, value in updates.items():
            if key in current and current[key] == value and type(current[key]) is type(value):
                continue
            new_lines = [l + nl for l in _render_key(key, value)]
            span = _find_key_span(lines, key)
            if span is None:
                lines.extend(new_lines)
            else:
                lines[span[0]:span[1]] = new_lines
        if lines and not lines[-1].endswith(("\n", "\r")):
            lines[-1] += nl
        rebuilt = "---" + nl + "".join(lines) + "---" + nl
        # Preserve the original closing delimiter line ending/presence.
        head_len = len(text) - len(body)
        closing = text[:head_len]
        closing_line = closing[closing.rfind("---", 0, len(closing)):]
        if closing_line != "---" + nl:
            rebuilt = rebuilt[: -len("---" + nl)] + closing_line
        return rebuilt + body


def _find_key_span(lines: list[str], key: str) -> tuple[int, int] | None:
    """Return [start, end) line span of top-level *key*, or None."""
    start = None
    for i, line in enumerate(lines):
        m = _TOP_KEY_RE.match(line)
        if not m:
            continue
        name = m.group(1).strip()
        if len(name) >= 2 and name[0] == name[-1] and name[0] in "\"'":
            name = name[1:-1]
        if name == key:
            start = i
            break
    if start is None:
        return None
    end = start + 1
    last_content = start + 1
    while end < len(lines):
        line = lines[end]
        stripped = line.strip()
        if stripped == "":
            end += 1
            continue
        if line[0] in " \t" or line.startswith("-") and not _TOP_KEY_RE.match(line):
            end += 1
            last_content = end
            continue
        break
    return start, last_content


# ---------------------------------------------------------------------------
# Public write helpers
# ---------------------------------------------------------------------------

ADR_STYLE: FrontmatterStyle = AdrStyle()
SPEC_STYLE: FrontmatterStyle = SpecStyle()


def render_document(frontmatter: Mapping[str, Any], body: str, style: FrontmatterStyle) -> str:
    """Render a whole document with *style*."""
    return style.render(frontmatter, body)


def update_frontmatter(
    text: str, updates: Mapping[str, Any], style: FrontmatterStyle, path: Any = None
) -> str:
    """Apply *updates* to the frontmatter in *text* using *style*."""
    return style.update(text, updates, path)


def write_document(
    path: str | Path, frontmatter: Mapping[str, Any], body: str, style: FrontmatterStyle
) -> None:
    """Write a complete document to *path*."""
    Path(path).write_text(style.render(frontmatter, body), encoding="utf-8")


def update_frontmatter_file(
    path: str | Path, updates: Mapping[str, Any], style: FrontmatterStyle
) -> None:
    """Update frontmatter keys of the file at *path* in place."""
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise FrontmatterError(p, f"cannot read file: {exc}") from exc
    p.write_text(style.update(text, updates, p), encoding="utf-8")
