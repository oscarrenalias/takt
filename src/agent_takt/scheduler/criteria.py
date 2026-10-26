from __future__ import annotations

import logging

from ..models import BEAD_DONE, Bead
from ..storage import RepositoryStorage

logger = logging.getLogger(__name__)


def resolve_criterion_text(bead_id: str, index: int, criterion: str) -> str:
    """Exact criterion wording used by auto-corrective beads."""
    return f"Resolve unmet criterion #{index} from {bead_id}: {criterion}"


def _done_dependencies(storage: RepositoryStorage, review_bead: Bead) -> list[Bead]:
    deps: list[Bead] = []
    for dep_id in review_bead.dependencies:
        try:
            dep = storage.load_bead(dep_id)
        except Exception:
            logger.warning(
                "Skipping missing dependency %s of %s", dep_id, review_bead.bead_id, exc_info=True
            )
            continue
        if dep.status == BEAD_DONE:
            deps.append(dep)
    return deps


def _entries_with_status(dep: Bead, status: str) -> list[dict]:
    entries: list[dict] = []
    for entry in dep.handoff_summary.criteria_status or []:
        if not isinstance(entry, dict) or entry.get("status") != status:
            continue
        entries.append(
            {
                "bead_id": dep.bead_id,
                "index": entry.get("index"),
                "criterion": entry.get("criterion", ""),
                "evidence": entry.get("evidence", ""),
            }
        )
    return entries


def dependency_criteria_state(
    storage: RepositoryStorage, review_bead: Bead
) -> tuple[list[dict], list[dict]]:
    """Return (unresolved, resolved) unmet criteria across done dependencies.

    An unmet entry is resolved when a done corrective of ``review_bead``
    reports the matching ``Resolve unmet criterion ...`` criterion as met.
    """
    unmet: list[dict] = []
    for dep in _done_dependencies(storage, review_bead):
        unmet.extend(_entries_with_status(dep, "unmet"))
    if not unmet:
        return [], []

    # corrective criterion text -> (corrective bead id, evidence)
    met_by_corrective: dict[str, tuple[str, str]] = {}
    for bead in storage.list_beads():
        if bead.status != BEAD_DONE:
            continue
        if bead.metadata.get("auto_corrective_for") != review_bead.bead_id:
            continue
        for entry in bead.handoff_summary.criteria_status or []:
            if isinstance(entry, dict) and entry.get("status") == "met":
                met_by_corrective.setdefault(
                    entry.get("criterion", ""), (bead.bead_id, entry.get("evidence", ""))
                )

    unresolved: list[dict] = []
    resolved: list[dict] = []
    for item in unmet:
        text = resolve_criterion_text(item["bead_id"], item["index"], item["criterion"])
        match = met_by_corrective.get(text)
        if match is None:
            unresolved.append(item)
        else:
            resolved.append({**item, "resolved_by": match[0], "evidence": match[1]})
    return unresolved, resolved


def dependency_descoped_entries(
    storage: RepositoryStorage, review_bead: Bead
) -> list[dict]:
    """Return descoped criteria entries from done dependencies."""
    entries: list[dict] = []
    for dep in _done_dependencies(storage, review_bead):
        entries.extend(_entries_with_status(dep, "descoped"))
    return entries
