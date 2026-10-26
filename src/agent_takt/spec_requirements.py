"""Parse spec requirement scenarios and match acceptance criteria against them."""

from __future__ import annotations

import re

_REQ_HEADING_RE = re.compile(r"^### (ADDED|MODIFIED|REMOVED):\s+(.+?)\s+/\s+(.+)$")
_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_SCENARIO_PREFIX = "#### Scenario:"
_EM_DASH = "—"
_RESERVED_DELIMITERS = (f" {_EM_DASH} ", " -- ", " - ")


def _fenced_flags(lines: list[str]) -> list[bool]:
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


def parse_requirement_scenarios(spec_text: str) -> list[tuple[str, str, str]]:
    """Return (capability, requirement, scenario) for ADDED/MODIFIED scenarios."""
    lines = spec_text.splitlines()
    fenced = _fenced_flags(lines)
    in_section = False
    current: tuple[str, str] | None = None
    results: list[tuple[str, str, str]] = []
    for line, in_fence in zip(lines, fenced):
        if in_fence:
            continue
        stripped = line.strip()
        if not in_section:
            if stripped == "## Requirements":
                in_section = True
            continue
        if stripped.startswith("## "):
            break
        if line.startswith("###") and not line.startswith("####"):
            m = _REQ_HEADING_RE.match(line.rstrip())
            if m and m.group(1) != "REMOVED":
                current = (m.group(2).strip(), m.group(3).strip())
            else:
                current = None
        elif current is not None and line.startswith(_SCENARIO_PREFIX):
            name = line[len(_SCENARIO_PREFIX):].strip()
            if name:
                results.append((current[0], current[1], name))
    return results


def scenario_prefix(cap: str, req: str, scen: str) -> str:
    return f"Scenario: {cap} / {req} / {scen}"


def normalize_delimiters(text: str) -> str:
    return text.replace(" -- ", f" {_EM_DASH} ").replace(" - ", f" {_EM_DASH} ")


def criterion_matches_prefix(criterion: str, prefix: str) -> bool:
    crit = normalize_delimiters(criterion.strip())
    pref = normalize_delimiters(prefix.strip())
    return crit == pref or crit.startswith(pref + f" {_EM_DASH} ")


def reserved_delimiter_names(scenarios: list[tuple[str, str, str]]) -> list[str]:
    """Return prefixes whose capability, requirement or scenario name contains a reserved delimiter."""
    return [
        scenario_prefix(cap, req, scen)
        for cap, req, scen in scenarios
        if any(d in part for part in (cap, req, scen) for d in _RESERVED_DELIMITERS)
    ]
