"""``document_access`` analyser: which specs/ADRs an agent looked at, and how.

Takes the raw candidates from ``document_access_detect``, resolves each to a
document id and classifies it against the bead's ``linked_docs``:

* ``linked``     - resolved document is one of the bead's linked documents
* ``proactive``  - resolved document that was not linked (including ADRs that
  were only in the pushed index)
* ``search``     - ``spec|adr search|list`` calls (query kept; result count when parseable)
* ``unresolved`` - target could not be resolved (``doc_id`` is ``None``, raw target kept)
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Sequence

from ...doc_refs import normalize_linked_docs, parse_ref, resolve_entry
from ...documents.registry import get_store
from .document_access_detect import (
    METHOD_CLI_LIST,
    METHOD_CLI_SEARCH,
    detect_document_accesses,
)

CLASS_LINKED = "linked"
CLASS_PROACTIVE = "proactive"
CLASS_SEARCH = "search"
CLASS_UNRESOLVED = "unresolved"

_ADR_FILENAME = re.compile(r"^adr-([0-9a-f]{8})(?:-|\.md$)", re.IGNORECASE)
_SEARCH_METHODS = {METHOD_CLI_SEARCH, METHOD_CLI_LIST}


def _roots(context: Any) -> tuple[Path | None, Path | None]:
    repo = getattr(context, "repo_root", None)
    main = getattr(context, "main_checkout_root", None) or repo
    return (Path(repo) if repo else None, Path(main) if main else None)


def _resolve_id(doc_type: str, target: str, roots: list[Path]) -> str | None:
    for root in roots:
        try:
            store = get_store(root, doc_type)
        except (LookupError, OSError, ValueError):
            continue
        # Bare hex prefixes (``bbbb22``) also match ``<id_prefix>bbbb22``.
        for query in (target, store.doc_type.id_prefix + target):
            try:
                return store.resolve(query).id
            except (LookupError, OSError, ValueError):
                continue
    return None


def _resolve_path(doc_type: str, rel: str, cwd: str | None, roots: list[Path]) -> str | None:
    if doc_type == "adr":
        match = _ADR_FILENAME.match(Path(rel).name)
        return f"ADR-{match.group(1).lower()}" if match else None
    workspace = Path(cwd) if cwd else (roots[0] if roots else None)
    main = roots[-1] if roots else None
    if workspace is None or main is None:
        return None
    try:
        resolved = resolve_entry(rel, workspace, main)
    except (OSError, ValueError):
        return None
    if resolved is None or resolved.source == "missing" or resolved.ref.kind != "spec":
        return None
    return resolved.id


def _linked_ids(context: Any, roots: list[Path]) -> list[str]:
    entries = list(getattr(context, "linked_docs", None) or [])
    if not entries:
        return []
    ids: list[str] = []
    workspace = roots[0] if roots else None
    main = roots[-1] if roots else None
    if workspace is not None:
        try:
            entries = normalize_linked_docs(entries, workspace)
        except (OSError, ValueError):
            pass
    for entry in entries:
        doc_id: str | None = None
        if workspace is not None and main is not None:
            try:
                resolved = resolve_entry(entry, workspace, main)
            except (OSError, ValueError):
                resolved = None
            if resolved is not None and resolved.source != "missing":
                doc_id = resolved.id
        if doc_id is None:
            ref = parse_ref(entry)
            doc_id = ref.id if ref else None
        if doc_id and doc_id not in ids:
            ids.append(doc_id)
    return ids


def _result_counts(events: Sequence[dict[str, Any]]) -> dict[Any, int]:
    """Best-effort result counts for search calls, keyed by call_id."""
    counts: dict[Any, int] = {}
    for event in events:
        if event.get("kind") != "tool_call" or event.get("is_error"):
            continue
        text = event.get("result_excerpt")
        if not isinstance(text, str) or event.get("result_chars", len(text)) > len(text):
            continue  # truncated: the count would be wrong
        try:
            data = json.loads(text)
        except ValueError:
            continue
        if isinstance(data, dict):
            data = data.get("results", data.get("documents"))
        if isinstance(data, list):
            counts[event.get("call_id")] = len(data)
    return counts


def _session_cwd(events: Sequence[dict[str, Any]]) -> str | None:
    for event in events:
        if event.get("kind") == "session" and event.get("cwd"):
            return str(event["cwd"])
    return None


def analyze_document_access(events: Sequence[dict[str, Any]], context: Any = None) -> dict[str, Any]:
    events = list(events)
    repo, main = _roots(context)
    # Main checkout first for id lookups; worktree as a fallback.
    id_roots = [r for r in (main, repo) if r is not None]
    path_roots = [r for r in (repo, main) if r is not None]
    cwd = _session_cwd(events)
    linked = _linked_ids(context, path_roots)
    linked_lower = {i.lower() for i in linked}
    search_counts = _result_counts(events)

    accesses: list[dict[str, Any]] = []
    counts = {CLASS_LINKED: 0, CLASS_PROACTIVE: 0, CLASS_SEARCH: 0, CLASS_UNRESOLVED: 0}
    accessed: set[str] = set()
    for cand in detect_document_accesses(events, main):
        method, doc_type, target = cand["method"], cand["doc_type"], cand["target"]
        entry: dict[str, Any] = {
            "call_id": cand["call_id"], "method": method, "doc_type": doc_type,
            "doc_id": None, "target": target,
        }
        if method in _SEARCH_METHODS:
            entry["class"] = CLASS_SEARCH
            entry["query"] = cand.get("query")
            count = search_counts.get(cand["call_id"])
            if count is not None:
                entry["result_count"] = count
        else:
            if not target:
                doc_id = None
            elif method == "cli_show":
                doc_id = _resolve_id(doc_type, target, id_roots)
            else:
                doc_id = _resolve_path(doc_type, target, cwd, path_roots)
            entry["doc_id"] = doc_id
            if doc_id is None:
                entry["class"] = CLASS_UNRESOLVED
            elif doc_id.lower() in linked_lower:
                entry["class"] = CLASS_LINKED
                accessed.add(doc_id.lower())
            else:
                entry["class"] = CLASS_PROACTIVE
        counts[entry["class"]] += 1
        accesses.append(entry)
    return {
        "accesses": accesses,
        "counts": counts,
        "linked_not_accessed": [i for i in linked if i.lower() not in accessed],
    }
