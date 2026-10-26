"""UI-neutral feature audit report builders (no dependency on ``agent_takt.cli``)."""
from __future__ import annotations

from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version as _pkg_version
from typing import Any

from .bead_metrics import bead_cost_usd, bead_wall_clock_seconds
from .models import BEAD_DONE, Bead
from .storage import RepositoryStorage

AUDIT_SCHEMA_VERSION = 1


def _takt_version() -> str | None:
    try:
        return _pkg_version("agent-takt")
    except PackageNotFoundError:
        return None


def _feature_tree(storage: RepositoryStorage, feature_root_id: str) -> list[Bead]:
    beads = [
        bead
        for bead in storage.list_beads()
        if bead.bead_id == feature_root_id or storage.feature_root_id_for(bead) == feature_root_id
    ]
    return sorted(beads, key=storage._bead_sort_key)


def _section_feature(root: Bead, feature_root_id: str) -> dict[str, Any]:
    return {
        "bead_id": feature_root_id,
        "title": root.title,
        "linked_docs": list(root.linked_docs),
        "branch": f"feature/{feature_root_id.lower()}",
        "worktree_path": f".takt/worktrees/{feature_root_id}",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "takt_version": _takt_version(),
    }


def _section_status(beads: list[Bead]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for bead in beads:
        counts[bead.status] = counts.get(bead.status, 0) + 1
    return {
        "counts": counts,
        "not_done": [
            {"bead_id": bead.bead_id, "title": bead.title}
            for bead in beads
            if bead.status != BEAD_DONE
        ],
    }


def _section_beads(beads: list[Bead]) -> list[dict[str, Any]]:
    corrective: dict[str, int] = {}
    recovery: dict[str, int] = {}
    for bead in beads:
        target = bead.metadata.get("auto_corrective_for")
        if target:
            corrective[target] = corrective.get(target, 0) + 1
        if bead.recovery_for:
            recovery[bead.recovery_for] = recovery.get(bead.recovery_for, 0) + 1
    return [
        {
            "bead_id": bead.bead_id,
            "agent_type": bead.agent_type,
            "bead_type": bead.bead_type,
            "status": bead.status,
            "verdict": bead.handoff_summary.verdict or None,
            "findings_count": bead.handoff_summary.findings_count,
            "corrective_children": corrective.get(bead.bead_id, 0),
            "recovery_children": recovery.get(bead.bead_id, 0),
            "wall_clock_seconds": bead_wall_clock_seconds(bead),
            "cost_usd": bead_cost_usd(bead),
        }
        for bead in beads
    ]


def _section_changed_files(beads: list[Bead]) -> list[str]:
    files: set[str] = set()
    for bead in beads:
        files.update(bead.changed_files)
    return sorted(files)


def _section_cost(beads: list[Bead]) -> dict[str, Any]:
    costs = [c for c in (bead_cost_usd(b) for b in beads) if c is not None]
    walls = [w for w in (bead_wall_clock_seconds(b) for b in beads) if w is not None]
    with_telemetry = sum(1 for b in beads if b.metadata and b.metadata.get("telemetry") is not None)
    return {
        "total_cost_usd": sum(costs) if costs else None,
        "total_wall_clock_seconds": sum(walls) if walls else None,
        "beads_with_telemetry": with_telemetry,
    }


def _section_merges(root: Bead) -> list[dict[str, Any]]:
    records = root.metadata.get("merge_records") if root.metadata else None
    return [dict(r) for r in records] if isinstance(records, list) else []


def build_feature_audit(storage: RepositoryStorage, feature_root_id: str) -> dict[str, Any]:
    """Build the audit report for a feature tree. Read-only: writes no beads or events."""
    root = storage.load_bead(feature_root_id)
    beads = _feature_tree(storage, feature_root_id)
    return {
        "audit_schema_version": AUDIT_SCHEMA_VERSION,
        "feature": _section_feature(root, feature_root_id),
        "status": _section_status(beads),
        "beads": _section_beads(beads),
        # Placeholders, filled in by a later bead.
        "gates": [],
        "assumptions": None,
        "criteria": None,
        "changed_files": _section_changed_files(beads),
        "cost": _section_cost(beads),
        "merges": _section_merges(root),
    }
