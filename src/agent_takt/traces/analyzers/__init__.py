"""Trace analysers: pure functions over normalised trace events.

An analyser is ``Analyzer(name, func)`` where ``func(events, context) -> dict``.
Adding an analyser means adding an entry to ``REGISTRY``; capture and storage
are unaffected. Analysers must ignore event kinds they do not know.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

Event = dict[str, Any]


@dataclass
class AnalyzerContext:
    bead_id: str | None = None
    agent_type: str | None = None
    linked_docs: list[str] = field(default_factory=list)
    feature_root_id: str | None = None
    repo_root: Path | None = None
    main_checkout_root: Path | None = None


@dataclass(frozen=True)
class Analyzer:
    name: str
    func: Callable[[Sequence[Event], AnalyzerContext], dict[str, Any]]

    def __call__(self, events: Sequence[Event], context: AnalyzerContext) -> dict[str, Any]:
        return self.func(events, context)


from .commands import analyze_commands  # noqa: E402
from .document_access import analyze_document_access  # noqa: E402
from .tool_usage import analyze_tool_usage  # noqa: E402

# Ordered built-in analysers.
REGISTRY: list[Analyzer] = [
    Analyzer("tool_usage", analyze_tool_usage),
    Analyzer("commands", analyze_commands),
    Analyzer("document_access", analyze_document_access),
]


def analyzer_names() -> list[str]:
    return [a.name for a in REGISTRY]


def run_analyzers(
    events: Iterable[Event],
    context: AnalyzerContext,
    names: Iterable[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Run analysers (all, or those in ``names``) in registry order."""
    events = list(events)
    selected = None if names is None else set(names)
    if selected is not None:
        unknown = selected - set(analyzer_names())
        if unknown:
            raise ValueError(f"unknown analyzer(s): {', '.join(sorted(unknown))}")
    return {
        a.name: a.func(events, context)
        for a in REGISTRY
        if selected is None or a.name in selected
    }


def _merge_values(a: Any, b: Any) -> Any:
    if isinstance(a, bool) or isinstance(b, bool):
        return b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a + b
    if isinstance(a, list) and isinstance(b, list):
        return a + b
    if isinstance(a, dict) and isinstance(b, dict):
        out = dict(a)
        for key, value in b.items():
            out[key] = _merge_values(out[key], value) if key in out else value
        return out
    return b


def merge_totals(name: str, results: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Combine per-trace results of one analyser: sum counts, concatenate lists.

    ``error_rate`` is recomputed from the merged ``errors`` / ``tool_calls``.
    """
    merged: dict[str, Any] = {}
    for result in results:
        for key, value in result.items():
            if key == "error_rate":
                continue
            merged[key] = _merge_values(merged[key], value) if key in merged else value
    if "errors" in merged and "tool_calls" in merged:
        calls = merged["tool_calls"]
        merged["error_rate"] = (merged["errors"] / calls) if calls else 0.0
    return merged
