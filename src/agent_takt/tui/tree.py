from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from ..models import (
    BEAD_BLOCKED,
    BEAD_DONE,
    BEAD_HANDED_OFF,
    BEAD_IN_PROGRESS,
    BEAD_OPEN,
    BEAD_READY,
    Bead,
)
from ..storage import RepositoryStorage


FILTER_DEFAULT = "default"
FILTER_ALL = "all"
FILTER_ACTIONABLE = "actionable"
FILTER_DEFERRED = "deferred"
FILTER_DONE = "done"

FILTER_STATUS_SETS: dict[str, frozenset[str]] = {
    FILTER_DEFAULT: frozenset({BEAD_OPEN, BEAD_READY, BEAD_IN_PROGRESS, BEAD_BLOCKED, BEAD_HANDED_OFF}),
    FILTER_ALL: frozenset({BEAD_OPEN, BEAD_READY, BEAD_IN_PROGRESS, BEAD_BLOCKED, BEAD_HANDED_OFF, BEAD_DONE}),
    FILTER_ACTIONABLE: frozenset({BEAD_OPEN, BEAD_READY}),
    FILTER_DEFERRED: frozenset({BEAD_HANDED_OFF}),
    FILTER_DONE: frozenset({BEAD_DONE}),
    BEAD_OPEN: frozenset({BEAD_OPEN}),
    BEAD_READY: frozenset({BEAD_READY}),
    BEAD_IN_PROGRESS: frozenset({BEAD_IN_PROGRESS}),
    BEAD_BLOCKED: frozenset({BEAD_BLOCKED}),
    BEAD_HANDED_OFF: frozenset({BEAD_HANDED_OFF}),
    BEAD_DONE: frozenset({BEAD_DONE}),
}


def _status_set(filter_mode: str) -> frozenset[str]:
    try:
        return FILTER_STATUS_SETS[filter_mode]
    except KeyError as exc:
        raise ValueError(f"Unsupported filter mode: {filter_mode}") from exc


def supported_filter_modes() -> tuple[str, ...]:
    return tuple(FILTER_STATUS_SETS.keys())


def bead_matches_filter(bead: Bead, filter_mode: str = FILTER_DEFAULT) -> bool:
    return bead.status in _status_set(filter_mode)


def load_beads(
    storage: RepositoryStorage,
    *,
    filter_mode: str = FILTER_DEFAULT,
    feature_root_id: str | None = None,
) -> list[Bead]:
    """Load and filter beads, sorted by creation timestamp.

    Beads are sorted by the timestamp of their first execution_history entry,
    falling back to bead_id on tie. This ensures consistent ordering independent
    of ID generation strategy (e.g., UUID-based IDs that don't sort chronologically).
    """
    beads = storage.list_beads()
    feature_root_bead: Bead | None = None
    if feature_root_id:
        beads = [
            bead for bead in beads
            if bead.bead_id == feature_root_id or storage.feature_root_id_for(bead) == feature_root_id
        ]
        feature_root_bead = next((bead for bead in beads if bead.bead_id == feature_root_id), None)
    filtered = [
        bead for bead in beads
        if bead_matches_filter(bead, filter_mode) or (feature_root_bead is not None and bead.bead_id == feature_root_id)
    ]
    return sorted(filtered, key=lambda bead: (bead.execution_history[0].timestamp if bead.execution_history else "", bead.bead_id))


@dataclass(frozen=True)
class TreeRow:
    bead: Bead
    depth: int
    has_children: bool
    label: str

    @property
    def bead_id(self) -> str:
        return self.bead.bead_id


def build_tree_rows(beads: Iterable[Bead]) -> list[TreeRow]:
    """Build tree rows from beads, sorted by creation timestamp within each level.

    Beads are sorted by the timestamp of their first execution_history entry,
    falling back to bead_id on tie. Parent-child relationships are preserved,
    and siblings within each level are also sorted by creation timestamp.
    """
    bead_list = sorted(beads, key=lambda bead: (bead.execution_history[0].timestamp if bead.execution_history else "", bead.bead_id))
    bead_map = {bead.bead_id: bead for bead in bead_list}
    children_by_parent: dict[str | None, list[Bead]] = {}
    for bead in bead_list:
        parent_id = bead.parent_id if bead.parent_id in bead_map else None
        children_by_parent.setdefault(parent_id, []).append(bead)

    rows: list[TreeRow] = []
    for siblings in children_by_parent.values():
        siblings.sort(key=lambda bead: (bead.execution_history[0].timestamp if bead.execution_history else "", bead.bead_id))

    def visit(parent_id: str | None, depth: int) -> None:
        for bead in children_by_parent.get(parent_id, []):
            has_children = bead.bead_id in children_by_parent
            label = f"{'  ' * depth}{bead.bead_id} · {bead.title}"
            rows.append(TreeRow(bead=bead, depth=depth, has_children=has_children, label=label))
            visit(bead.bead_id, depth + 1)

    visit(None, 0)
    return rows


def collect_tree_rows(
    storage: RepositoryStorage,
    *,
    filter_mode: str = FILTER_DEFAULT,
    feature_root_id: str | None = None,
) -> list[TreeRow]:
    return build_tree_rows(load_beads(storage, filter_mode=filter_mode, feature_root_id=feature_root_id))


def resolve_selected_index(
    rows: list[TreeRow],
    *,
    selected_bead_id: str | None = None,
    previous_index: int | None = None,
) -> int | None:
    if not rows:
        return None
    if selected_bead_id:
        for index, row in enumerate(rows):
            if row.bead_id == selected_bead_id:
                return index
    if previous_index is None:
        return 0
    return max(0, min(previous_index, len(rows) - 1))


def resolve_selected_bead(
    rows: list[TreeRow],
    *,
    selected_bead_id: str | None = None,
    previous_index: int | None = None,
) -> Bead | None:
    selected_index = resolve_selected_index(rows, selected_bead_id=selected_bead_id, previous_index=previous_index)
    if selected_index is None:
        return None
    return rows[selected_index].bead
