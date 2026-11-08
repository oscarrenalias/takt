"""Parse spec requirement scenarios and match acceptance criteria against them."""

from __future__ import annotations

from .documents.spec_type import parse_requirement_scenarios

_EM_DASH = "—"
_RESERVED_DELIMITERS = (f" {_EM_DASH} ", " -- ", " - ")


__all__ = [
    "parse_requirement_scenarios",
    "scenario_prefix",
    "normalize_delimiters",
    "criterion_matches_prefix",
    "reserved_delimiter_names",
]


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
