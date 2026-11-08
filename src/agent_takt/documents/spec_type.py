"""Spec document type: the single ``## Requirements`` parser and delta validation.

Unified rules:

* Only the first ``## Requirements`` heading starts the section; a repeated one
  is treated as ordinary section content.
* The section ends at the next ``## `` heading outside a fenced code block.
* Headings inside fenced code blocks are ignored.
* Heading lines are ``rstrip()``ped before matching.
* A ``#### Scenario:`` with an empty name is a validation error; the
  scenario parser skips such scenarios (see :func:`find_empty_scenarios`).

Syntax checks (:func:`validate_delta_syntax`) and consistency checks against
``specs/current/`` (:func:`validate_delta_consistency`) are separate so callers
can skip consistency for specs that are already done.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .types import ValidationError

REQUIREMENTS_HEADING = "## Requirements"
SCENARIO_PREFIX = "#### Scenario:"
DEFAULT_CURRENT_DIR = "specs/current"

_REQ_HEADING_RE = re.compile(r"^### (ADDED|MODIFIED|REMOVED):\s+(.+?)\s+/\s+(.+)$")
_CAPABILITY_SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_SHALL_MUST_RE = re.compile(r"\bSHALL\b|\bMUST\b")
_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_CURRENT_REQ_RE = re.compile(r"^## Requirement:\s+(.+)$", re.MULTILINE)


@dataclass
class RequirementEntry:
    """One ``### OP: capability / requirement`` entry of a Requirements section."""

    heading: str
    body_lines: list[str] = field(default_factory=list)
    op: str | None = None
    capability: str = ""
    requirement: str = ""

    @property
    def well_formed(self) -> bool:
        return self.op is not None


def fenced_flags(lines: list[str]) -> list[bool]:
    """Per-line flags: True for lines inside a fenced code block (fences included)."""
    flags: list[bool] = []
    fence: str | None = None
    for line in lines:
        m = _FENCE_RE.match(line)
        if fence is None:
            if m and not (m.group(1)[0] == "`" and "`" in m.group(2)):
                fence = m.group(1)
                flags.append(True)
            else:
                flags.append(False)
        else:
            flags.append(True)
            if (
                m
                and m.group(1)[0] == fence[0]
                and len(m.group(1)) >= len(fence)
                and not m.group(2).strip()
            ):
                fence = None
    return flags


def extract_requirements_section(body: str) -> str | None:
    """Return the text of the ``## Requirements`` section, or None if absent."""
    lines = body.splitlines()
    fenced = fenced_flags(lines)
    start: int | None = None
    for i, line in enumerate(lines):
        if not fenced[i] and line.rstrip().strip() == REQUIREMENTS_HEADING:
            start = i + 1
            break
    if start is None:
        return None
    for i in range(start, len(lines)):
        stripped = lines[i].strip()
        if not fenced[i] and stripped.startswith("## ") and stripped != REQUIREMENTS_HEADING:
            return "\n".join(lines[start:i])
    return "\n".join(lines[start:])


def parse_requirement_entries(section: str) -> list[RequirementEntry]:
    """Split a Requirements section into ``###`` entries (fence-aware)."""
    lines = section.splitlines()
    fenced = fenced_flags(lines)
    entries: list[RequirementEntry] = []
    current: RequirementEntry | None = None
    for line, in_fence in zip(lines, fenced):
        if not in_fence and line.startswith("###") and not line.startswith("####"):
            heading = line.rstrip()
            current = RequirementEntry(heading=heading)
            m = _REQ_HEADING_RE.match(heading)
            if m:
                current.op = m.group(1)
                current.capability = m.group(2).strip()
                current.requirement = m.group(3).strip()
            entries.append(current)
        elif current is not None:
            current.body_lines.append(line)
    return entries


def _split_scenarios(body_lines: list[str]) -> list[tuple[str, list[str]]]:
    """Return (scenario heading line, following lines) pairs, ignoring fenced headings."""
    scenarios: list[tuple[str, list[str]]] = []
    fenced = fenced_flags(body_lines)
    cur: str | None = None
    cur_lines: list[str] = []
    for line, in_fence in zip(body_lines, fenced):
        if not in_fence and line.startswith(SCENARIO_PREFIX):
            if cur is not None:
                scenarios.append((cur, cur_lines))
            cur = line.rstrip()
            cur_lines = []
        elif cur is not None:
            cur_lines.append(line)
    if cur is not None:
        scenarios.append((cur, cur_lines))
    return scenarios


def _scenario_name(heading: str) -> str:
    return heading[len(SCENARIO_PREFIX):].strip()


def parse_requirement_scenarios(spec_text: str) -> list[tuple[str, str, str]]:
    """Return (capability, requirement, scenario) for ADDED/MODIFIED scenarios.

    Scenarios with an empty name are skipped (they are validation errors).
    """
    section = extract_requirements_section(spec_text)
    if section is None:
        return []
    results: list[tuple[str, str, str]] = []
    for entry in parse_requirement_entries(section):
        if not entry.well_formed or entry.op == "REMOVED":
            continue
        for heading, _ in _split_scenarios(entry.body_lines):
            name = _scenario_name(heading)
            if name:
                results.append((entry.capability, entry.requirement, name))
    return results


def find_empty_scenarios(spec_text: str) -> list[tuple[str, str]]:
    """Return (capability, requirement) for each ADDED/MODIFIED empty-name scenario."""
    section = extract_requirements_section(spec_text)
    if section is None:
        return []
    found: list[tuple[str, str]] = []
    for entry in parse_requirement_entries(section):
        if not entry.well_formed or entry.op == "REMOVED":
            continue
        for heading, _ in _split_scenarios(entry.body_lines):
            if not _scenario_name(heading):
                found.append((entry.capability, entry.requirement))
    return found


def validate_delta_syntax(path: str, section: str) -> list[ValidationError]:
    """Syntax checks for the deltas of a Requirements section (no filesystem access)."""
    errors: list[ValidationError] = []

    def err(heading: str, message: str) -> None:
        errors.append(ValidationError(path=path, message=f"{heading}: {message}"))

    seen: set[tuple[str, str]] = set()
    for entry in parse_requirement_entries(section):
        heading = entry.heading
        if not entry.well_formed:
            err(
                heading,
                "malformed heading (expected "
                "'### ADDED|MODIFIED|REMOVED: <capability> / <requirement name>')",
            )
            continue
        if not _CAPABILITY_SLUG_RE.match(entry.capability):
            err(
                heading,
                f"invalid capability slug '{entry.capability}' "
                "(must match ^[a-z0-9]+(-[a-z0-9]+)*$)",
            )
        key = (entry.capability.lower(), entry.requirement.lower())
        if key in seen:
            err(heading, f"duplicate capability/requirement pair "
                         f"'{entry.capability} / {entry.requirement}'")
        else:
            seen.add(key)

        if entry.op in ("ADDED", "MODIFIED"):
            if not _SHALL_MUST_RE.search("\n".join(entry.body_lines)):
                err(heading, "no SHALL or MUST sentence found")
            scenarios = _split_scenarios(entry.body_lines)
            if not scenarios:
                err(heading, "no '#### Scenario:' sub-heading found")
            for sc_heading, sc_lines in scenarios:
                if not _scenario_name(sc_heading):
                    err(sc_heading, "scenario has an empty name")
                stripped = [bl.strip() for bl in sc_lines]
                if not any(s.startswith("- **WHEN**") for s in stripped):
                    err(sc_heading, "scenario missing '- **WHEN**' line")
                if not any(s.startswith("- **THEN**") for s in stripped):
                    err(sc_heading, "scenario missing '- **THEN**' line")
        else:  # REMOVED
            if not any(bl.strip().startswith("Reason:") for bl in entry.body_lines):
                err(heading, "REMOVED entry missing 'Reason:' line")
            if any(bl.startswith(SCENARIO_PREFIX) for bl in entry.body_lines):
                err(heading, "REMOVED entry must not contain scenarios")
    return errors


def load_current_requirements(
    capability: str, current_dir: str | Path = DEFAULT_CURRENT_DIR
) -> set[str] | None:
    """Lowercase requirement names in ``<current_dir>/<capability>.md``, or None if missing."""
    cap_path = Path(current_dir) / f"{capability}.md"
    if not cap_path.is_file():
        return None
    try:
        content = cap_path.read_text(encoding="utf-8")
    except OSError:
        return None
    return {m.group(1).strip().lower() for m in _CURRENT_REQ_RE.finditer(content)}


def validate_delta_consistency(
    path: str, section: str, current_dir: str | Path = DEFAULT_CURRENT_DIR
) -> list[ValidationError]:
    """Check deltas against ``specs/current/``: ADDED must be new; MODIFIED/REMOVED must exist.

    Malformed entries are skipped (syntax checks report them).
    """
    errors: list[ValidationError] = []
    cache: dict[str, set[str] | None] = {}
    for entry in parse_requirement_entries(section):
        if not entry.well_formed:
            continue
        cap = entry.capability
        if cap not in cache:
            cache[cap] = load_current_requirements(cap, current_dir)
        current = cache[cap]
        cap_file = str(Path(current_dir) / f"{cap}.md")
        req = entry.requirement.lower()

        def err(message: str) -> None:
            errors.append(ValidationError(path=path, message=f"{entry.heading}: {message}"))

        if entry.op == "ADDED":
            if current is not None and req in current:
                err(f"requirement already exists in {cap_file}")
        elif current is None:
            err(f"capability file not found: {cap_file}")
        elif req not in current:
            err(f"requirement not found in {cap_file}")
    return errors
