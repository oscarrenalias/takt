from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from .config import OrchestratorConfig, SchedulerConfig
from .models import Bead

MAX_TITLE_LENGTH = 40
STATUS_ICONS = {
    "done": "✓",
    "in_progress": "...",
    "blocked": "!",
    "ready": "○",
    "open": "◌",
    "handed_off": "↪",
}


class _HasScheduler(Protocol):
    scheduler: SchedulerConfig


def _mermaid_node_id(bead_id: str) -> str:
    return bead_id.replace("-", "_")


def _truncate_title(title: str, limit: int = MAX_TITLE_LENGTH) -> str:
    if len(title) <= limit:
        return title
    return f"{title[:limit - 3]}..."


def _status_icon(status: str) -> str:
    return STATUS_ICONS.get(status, "?")


def _format_node_label(bead: Bead) -> str:
    title = _truncate_title(bead.title)
    return f"{bead.bead_id}: {title} [{bead.agent_type}] {_status_icon(bead.status)}"


def _escape_label(label: str) -> str:
    return label.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def _corrective_suffix(config: OrchestratorConfig | SchedulerConfig | _HasScheduler) -> str:
    if isinstance(config, SchedulerConfig):
        return config.corrective_suffix
    if isinstance(config, OrchestratorConfig):
        return config.scheduler.corrective_suffix
    return config.scheduler.corrective_suffix


def _is_corrective_bead(bead: Bead, corrective_suffix: str) -> bool:
    return f"-{corrective_suffix}" in bead.bead_id


def render_bead_graph(
    beads: Iterable[Bead],
    config: OrchestratorConfig | SchedulerConfig | _HasScheduler,
) -> str:
    bead_list = list(beads)
    bead_ids = {bead.bead_id for bead in bead_list}
    lines = ["graph TD"]

    for bead in bead_list:
        node_id = _mermaid_node_id(bead.bead_id)
        label = _escape_label(_format_node_label(bead))
        lines.append(f'    {node_id}["{label}"]')

    for bead in bead_list:
        bead_node_id = _mermaid_node_id(bead.bead_id)
        for dependency_id in bead.dependencies:
            if dependency_id not in bead_ids:
                continue
            dependency_node_id = _mermaid_node_id(dependency_id)
            lines.append(f"    {dependency_node_id} --> {bead_node_id}")

    corrective_suffix = _corrective_suffix(config)
    for bead in bead_list:
        if bead.parent_id is None or bead.parent_id not in bead_ids:
            continue
        if not _is_corrective_bead(bead, corrective_suffix):
            continue
        lines.append(
            f"    {_mermaid_node_id(bead.bead_id)} -.-> {_mermaid_node_id(bead.parent_id)}"
        )

    return "\n".join(lines)


__all__ = [
    "MAX_TITLE_LENGTH",
    "STATUS_ICONS",
    "render_bead_graph",
]
