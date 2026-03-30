from __future__ import annotations

import io
from argparse import Namespace
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import Callable, Iterable

from rich.text import Text

from .console import ConsoleReporter
from .models import (
    BEAD_BLOCKED,
    BEAD_DONE,
    BEAD_HANDED_OFF,
    BEAD_IN_PROGRESS,
    BEAD_OPEN,
    BEAD_READY,
    Bead,
)
from .storage import RepositoryStorage


def _make_services(root: Path):
    """Lazy import to avoid circular dependency with cli module."""
    from .cli import make_services
    return make_services(root)


def _format_duration_ms(ms: float | int | None) -> str:
    """Format milliseconds as m:ss."""
    if ms is None:
        return "-"
    total_seconds = int(ms / 1000)
    minutes = total_seconds // 60
    seconds = total_seconds % 60
    return f"{minutes}:{seconds:02d}"


_DEFAULT_PANEL_WIDTH = 120


def _truncate_title(title: str, max_width: int) -> str:
    """Truncate *title* to *max_width* characters, adding '...' when trimmed."""
    if len(title) <= max_width:
        return title
    if max_width <= 3:
        return "..."[:max_width]
    return title[: max_width - 3] + "..."


def _telemetry_badge(bead: Bead) -> str:
    """Return compact telemetry badge like '[$0.32, 2:55]' or empty string."""
    telemetry = bead.metadata.get("telemetry")
    if not telemetry:
        return ""
    cost = telemetry.get("cost_usd")
    duration = telemetry.get("duration_ms") or telemetry.get("duration_api_ms")
    parts: list[str] = []
    if cost is not None:
        parts.append(f"${cost:.2f}")
    if duration is not None:
        parts.append(_format_duration_ms(duration))
    if not parts:
        return ""
    return f" [{', '.join(parts)}]"


FILTER_DEFAULT = "default"
FILTER_ALL = "all"
FILTER_ACTIONABLE = "actionable"
FILTER_DEFERRED = "deferred"
FILTER_DONE = "done"
PANEL_LIST = "list"
PANEL_DETAIL = "detail"
STATUS_ACTION_TARGETS = (BEAD_READY, BEAD_BLOCKED, BEAD_DONE)
DETAIL_SECTION_ACCEPTANCE = "acceptance"
DETAIL_SECTION_FILES = "files"
DETAIL_SECTION_HANDOFF = "handoff"
DETAIL_SECTION_TELEMETRY = "telemetry"
DETAIL_SECTION_ORDER = (
    DETAIL_SECTION_ACCEPTANCE,
    DETAIL_SECTION_FILES,
    DETAIL_SECTION_HANDOFF,
    DETAIL_SECTION_TELEMETRY,
)

STATUS_DISPLAY_ORDER = (
    BEAD_OPEN,
    BEAD_READY,
    BEAD_IN_PROGRESS,
    BEAD_BLOCKED,
    BEAD_HANDED_OFF,
    BEAD_DONE,
)

FILTER_STATUS_SETS: dict[str, frozenset[str]] = {
    FILTER_DEFAULT: frozenset({BEAD_OPEN, BEAD_READY, BEAD_IN_PROGRESS, BEAD_BLOCKED, BEAD_HANDED_OFF}),
    FILTER_ALL: frozenset(STATUS_DISPLAY_ORDER),
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


@dataclass(frozen=True)
class TreeRow:
    bead: Bead
    depth: int
    has_children: bool
    label: str

    @property
    def bead_id(self) -> str:
        return self.bead.bead_id


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
    return sorted(filtered, key=lambda bead: bead.bead_id)


def collect_tree_rows(
    storage: RepositoryStorage,
    *,
    filter_mode: str = FILTER_DEFAULT,
    feature_root_id: str | None = None,
) -> list[TreeRow]:
    return build_tree_rows(load_beads(storage, filter_mode=filter_mode, feature_root_id=feature_root_id))


def build_tree_rows(beads: Iterable[Bead]) -> list[TreeRow]:
    bead_list = sorted(beads, key=lambda bead: bead.bead_id)
    bead_map = {bead.bead_id: bead for bead in bead_list}
    children_by_parent: dict[str | None, list[Bead]] = {}
    for bead in bead_list:
        parent_id = bead.parent_id if bead.parent_id in bead_map else None
        children_by_parent.setdefault(parent_id, []).append(bead)

    rows: list[TreeRow] = []
    for siblings in children_by_parent.values():
        siblings.sort(key=lambda bead: bead.bead_id)

    def visit(parent_id: str | None, depth: int) -> None:
        for bead in children_by_parent.get(parent_id, []):
            has_children = bead.bead_id in children_by_parent
            label = f"{'  ' * depth}{bead.bead_id} · {bead.title}"
            rows.append(TreeRow(bead=bead, depth=depth, has_children=has_children, label=label))
            visit(bead.bead_id, depth + 1)

    visit(None, 0)
    return rows


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


def summarize_status_counts(beads: Iterable[Bead]) -> dict[str, int]:
    counts = {status: 0 for status in STATUS_DISPLAY_ORDER}
    for bead in beads:
        if bead.status in counts:
            counts[bead.status] += 1
    return counts


def format_status_counts(beads: Iterable[Bead]) -> str:
    counts = summarize_status_counts(beads)
    return " | ".join(f"{status}={counts[status]}" for status in STATUS_DISPLAY_ORDER)


def format_footer(
    beads: Iterable[Bead],
    *,
    filter_mode: str,
    selected_index: int | None,
    total_rows: int,
    focused_panel: str = PANEL_LIST,
    timed_refresh_enabled: bool = False,
    continuous_run_enabled: bool,
    refresh_seconds: int = 3,
) -> str:
    cursor = "-" if selected_index is None else str(selected_index + 1)
    run_mode = "continuous" if continuous_run_enabled else "manual"
    return f"filter={filter_mode} | run={run_mode} | rows={total_rows} | selected={cursor} | {format_status_counts(beads)} | ? help"


def _panel_badge(panel_name: str, *, focused: bool) -> str:
    state = "ACTIVE" if focused else "idle"
    return f"{panel_name} [{state}]"


def _format_filter_label(filter_mode: str) -> str:
    return filter_mode.replace("_", " ").title()


def _beads_panel_title(filter_mode: str, *, focused: bool) -> str:
    return _panel_badge(f"Beads [{_format_filter_label(filter_mode)}]", focused=focused)


def _focus_status_hint(focused_panel: str) -> str:
    if focused_panel == PANEL_DETAIL:
        return "detail scroll"
    return "list navigation"


def format_detail_panel(bead: Bead | None) -> str:
    if bead is None:
        return "No bead selected."

    handoff = bead.handoff_summary
    lines = [
        f"Bead: {bead.bead_id}",
        f"Title: {bead.title}",
        f"Status: {bead.status}",
        f"Type: {bead.bead_type}",
        f"Agent: {bead.agent_type}",
        f"Parent: {_value_or_dash(bead.parent_id)}",
        f"Feature Root: {_value_or_dash(bead.feature_root_id)}",
        f"Dependencies: {_format_list(bead.dependencies)}",
        "Acceptance Criteria:",
        *_format_block(bead.acceptance_criteria),
        f"Block Reason: {_value_or_dash(bead.block_reason or handoff.block_reason)}",
        "Files:",
        f"  expected: {_format_list(bead.expected_files)}",
        f"  expected_globs: {_format_list(bead.expected_globs)}",
        f"  touched: {_format_list(bead.touched_files)}",
        f"  changed: {_format_list(bead.changed_files)}",
        f"  updated_docs: {_format_list(bead.updated_docs)}",
        "Handoff:",
        f"  completed: {_value_or_dash(handoff.completed)}",
        f"  remaining: {_value_or_dash(handoff.remaining)}",
        f"  risks: {_value_or_dash(handoff.risks)}",
        f"  next_action: {_value_or_dash(handoff.next_action)}",
        f"  next_agent: {_value_or_dash(handoff.next_agent)}",
        f"  block_reason: {_value_or_dash(handoff.block_reason)}",
        f"  touched_files: {_format_list(handoff.touched_files)}",
        f"  changed_files: {_format_list(handoff.changed_files)}",
        f"  expected_files: {_format_list(handoff.expected_files)}",
        f"  expected_globs: {_format_list(handoff.expected_globs)}",
        f"  updated_docs: {_format_list(handoff.updated_docs)}",
        f"  conflict_risks: {_value_or_dash(handoff.conflict_risks or bead.conflict_risks)}",
    ]
    telemetry = bead.metadata.get("telemetry")
    if telemetry:
        lines.append("Telemetry:")
        lines.append(f"  cost_usd: ${telemetry.get('cost_usd', 0):.2f}")
        lines.append(f"  duration: {_format_duration_ms(telemetry.get('duration_ms') or telemetry.get('duration_api_ms'))}")
        lines.append(f"  num_turns: {_value_or_dash(telemetry.get('num_turns'))}")
        lines.append(f"  input_tokens: {_value_or_dash(telemetry.get('input_tokens'))}")
        lines.append(f"  output_tokens: {_value_or_dash(telemetry.get('output_tokens'))}")
        lines.append(f"  cache_read_tokens: {_value_or_dash(telemetry.get('cache_read_tokens'))}")
        lines.append(f"  prompt_chars: {_value_or_dash(telemetry.get('prompt_chars'))}")
        lines.append(f"  session_id: {_value_or_dash(telemetry.get('session_id'))}")
        history = bead.metadata.get("telemetry_history")
        if history and len(history) > 1:
            total_cost = sum(h.get("cost_usd", 0) or 0 for h in history)
            lines.append(f"  attempts: {len(history)} (total cost: ${total_cost:.2f})")
    return "\n".join(lines)


def _detail_summary_lines(bead: Bead | None) -> list[str]:
    if bead is None:
        return ["No bead selected."]
    handoff = bead.handoff_summary
    return [
        f"Bead: {bead.bead_id}",
        f"Title: {bead.title}",
        f"Status: {bead.status}",
        f"Type: {bead.bead_type}",
        f"Agent: {bead.agent_type}",
        f"Parent: {_value_or_dash(bead.parent_id)}",
        f"Feature Root: {_value_or_dash(bead.feature_root_id)}",
        f"Dependencies: {_format_list(bead.dependencies)}",
        f"Block Reason: {_value_or_dash(bead.block_reason or handoff.block_reason)}",
    ]


def _detail_section_body(bead: Bead | None, section: str) -> str:
    if bead is None:
        return "-"
    handoff = bead.handoff_summary
    if section == DETAIL_SECTION_ACCEPTANCE:
        return "\n".join(_format_block(bead.acceptance_criteria))
    if section == DETAIL_SECTION_FILES:
        return "\n".join(
            [
                f"expected: {_format_list(bead.expected_files)}",
                f"expected_globs: {_format_list(bead.expected_globs)}",
                f"touched: {_format_list(bead.touched_files)}",
                f"changed: {_format_list(bead.changed_files)}",
                f"updated_docs: {_format_list(bead.updated_docs)}",
            ]
        )
    if section == DETAIL_SECTION_HANDOFF:
        return "\n".join(
            [
                f"completed: {_value_or_dash(handoff.completed)}",
                f"remaining: {_value_or_dash(handoff.remaining)}",
                f"risks: {_value_or_dash(handoff.risks)}",
                f"next_action: {_value_or_dash(handoff.next_action)}",
                f"next_agent: {_value_or_dash(handoff.next_agent)}",
                f"block_reason: {_value_or_dash(handoff.block_reason)}",
                f"touched_files: {_format_list(handoff.touched_files)}",
                f"changed_files: {_format_list(handoff.changed_files)}",
                f"expected_files: {_format_list(handoff.expected_files)}",
                f"expected_globs: {_format_list(handoff.expected_globs)}",
                f"updated_docs: {_format_list(handoff.updated_docs)}",
                f"conflict_risks: {_value_or_dash(handoff.conflict_risks or bead.conflict_risks)}",
            ]
        )
    if section == DETAIL_SECTION_TELEMETRY:
        telemetry = bead.metadata.get("telemetry")
        if not telemetry:
            return "No telemetry data."
        lines = [
            f"cost_usd: ${telemetry.get('cost_usd', 0):.2f}",
            f"duration: {_format_duration_ms(telemetry.get('duration_ms') or telemetry.get('duration_api_ms'))}",
            f"num_turns: {_value_or_dash(telemetry.get('num_turns'))}",
            f"input_tokens: {_value_or_dash(telemetry.get('input_tokens'))}",
            f"output_tokens: {_value_or_dash(telemetry.get('output_tokens'))}",
            f"cache_read_tokens: {_value_or_dash(telemetry.get('cache_read_tokens'))}",
            f"prompt_chars: {_value_or_dash(telemetry.get('prompt_chars'))}",
            f"session_id: {_value_or_dash(telemetry.get('session_id'))}",
        ]
        history = bead.metadata.get("telemetry_history")
        if history and len(history) > 1:
            total_cost = sum(h.get("cost_usd", 0) or 0 for h in history)
            lines.append(f"attempts: {len(history)} (total cost: ${total_cost:.2f})")
        return "\n".join(lines)
    raise ValueError(f"Unknown detail section: {section}")


def _detail_section_title(section: str) -> str:
    titles = {
        DETAIL_SECTION_ACCEPTANCE: "Acceptance Criteria",
        DETAIL_SECTION_FILES: "Files",
        DETAIL_SECTION_HANDOFF: "Handoff",
        DETAIL_SECTION_TELEMETRY: "Telemetry",
    }
    return titles[section]


def format_help_overlay() -> str:
    return "\n".join(
        [
            "Shortcuts",
            "",
            "Tab         Focus next panel",
            "Shift+Tab   Focus previous panel",
            "j / Down    Move list or detail down",
            "k / Up      Move list or detail up",
            "PgUp/PgDn   Page list/detail",
            "Home / End  Jump to start/end",
            "g / G       Jump to first/last bead",
            "n / N       Next/prev detail section",
            "f           Next filter",
            "Shift+f     Previous filter",
            "a           Toggle timed refresh",
            "r           Refresh now",
            "s           Run one scheduler cycle",
            "S           Toggle timed scheduler mode",
            "t           Request blocked-bead retry",
            "u           Open status update flow",
            "r / b / d   Choose ready, blocked, done in status flow",
            "y           Confirm retry/status update",
            "c           Cancel pending merge/retry/status",
            "m           Request merge",
            "Enter       Toggle detail section / confirm merge",
            "E           Expand/collapse all tree nodes",
            "q           Quit",
            "",
            "? / Esc     Close help",
        ]
    )


def _status_set(filter_mode: str) -> frozenset[str]:
    try:
        return FILTER_STATUS_SETS[filter_mode]
    except KeyError as exc:
        raise ValueError(f"Unsupported filter mode: {filter_mode}") from exc


def _format_block(values: list[str]) -> list[str]:
    if not values:
        return ["  -"]
    return [f"  - {value}" for value in values]


def _format_list(values: list[str]) -> str:
    return ", ".join(values) if values else "-"


def _value_or_dash(value: str | None) -> str:
    return value if value else "-"


@dataclass
class TuiRuntimeState:
    storage: RepositoryStorage
    feature_root_id: str | None = None
    filter_mode: str = FILTER_DEFAULT
    refresh_seconds: int = 3
    focused_panel: str = PANEL_LIST
    selected_bead_id: str | None = None
    selected_index: int | None = None
    list_scroll_offset: int = 0
    detail_scroll_offset: int = 0
    status_message: str = "Press q to quit."
    activity_message: str = "Waiting for first refresh."
    awaiting_merge_confirmation: bool = False
    pending_merge_bead_id: str | None = None
    awaiting_retry_confirmation: bool = False
    pending_retry_bead_id: str | None = None
    status_flow_active: bool = False
    pending_status_bead_id: str | None = None
    pending_status_target: str | None = None
    help_overlay_visible: bool = False
    timed_refresh_enabled: bool = False
    continuous_run_enabled: bool = False
    scheduler_running: bool = False
    scheduler_log: list[str] = field(default_factory=list)
    max_workers: int = 1
    last_action: str = "-"
    last_result: str = "-"
    last_action_at: str = "-"
    _rows_cache: list[TreeRow] = field(default_factory=list, init=False, repr=False)
    _beads_cache: list[Bead] = field(default_factory=list, init=False, repr=False)
    _detail_cache: dict[str, tuple[Bead, str]] = field(default_factory=dict, init=False, repr=False)
    _rendered_detail_content_height: int | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self.refresh(activity_message="Loaded bead state.")

    @property
    def rows(self) -> list[TreeRow]:
        return list(self._rows_cache)

    @property
    def beads(self) -> list[Bead]:
        return list(self._beads_cache)

    def selected_row(self) -> TreeRow | None:
        if self.selected_index is None:
            return None
        rows = self.rows
        if self.selected_index >= len(rows):
            return None
        return rows[self.selected_index]

    def selected_bead(self) -> Bead | None:
        row = self.selected_row()
        return row.bead if row is not None else None

    def refresh(self, *, activity_message: str | None = None) -> None:
        pending_merge_bead_id = self.pending_merge_bead_id
        pending_status_bead_id = self.pending_status_bead_id
        previous_selected_bead_id = self.selected_bead_id
        try:
            beads = load_beads(
                self.storage,
                filter_mode=self.filter_mode,
                feature_root_id=self.feature_root_id,
            )
            rows = build_tree_rows(beads)
        except Exception as exc:
            self._clear_pending_actions()
            self._record_action_result(
                "refresh",
                f"failed: {exc}",
                status_message=f"Refresh failed: {exc}",
            )
            if activity_message is None:
                activity_message = f"Refresh failed at {datetime.now().strftime('%H:%M:%S')}."
            self.activity_message = activity_message
            return
        self._beads_cache = beads
        self._rows_cache = rows
        previous_index = self.selected_index
        self.selected_index = resolve_selected_index(
            rows,
            selected_bead_id=self.selected_bead_id,
            previous_index=previous_index,
        )
        selected = self.selected_bead()
        self.selected_bead_id = selected.bead_id if selected is not None else None
        if self.selected_bead_id != previous_selected_bead_id:
            self.detail_scroll_offset = 0
        if pending_merge_bead_id is not None:
            pending_bead = next((row.bead for row in rows if row.bead_id == pending_merge_bead_id), None)
            if pending_bead is None or pending_bead.status != BEAD_DONE:
                self.awaiting_merge_confirmation = False
                self.pending_merge_bead_id = None
                self.status_message = "Merge confirmation cleared because the requested bead is no longer mergeable."
        pending_retry_bead_id = self.pending_retry_bead_id
        if pending_retry_bead_id is not None:
            pending_bead = next((row.bead for row in rows if row.bead_id == pending_retry_bead_id), None)
            if pending_bead is None or pending_bead.status != BEAD_BLOCKED:
                self.awaiting_retry_confirmation = False
                self.pending_retry_bead_id = None
                self.status_message = "Retry confirmation cleared because the requested bead is no longer blocked."
        if pending_status_bead_id is not None:
            pending_bead = next((row.bead for row in rows if row.bead_id == pending_status_bead_id), None)
            if pending_bead is None:
                self._clear_pending_status_flow()
                self.status_message = "Status update cleared because the requested bead is no longer visible."
        if activity_message is None:
            activity_message = f"Refreshed at {datetime.now().strftime('%H:%M:%S')}."
        self.activity_message = activity_message

    def move_selection(self, delta: int) -> None:
        rows = self.rows
        if not rows:
            self.selected_index = None
            self.selected_bead_id = None
            self.list_scroll_offset = 0
            self.detail_scroll_offset = 0
            self.status_message = "No beads available for the current filter."
            self._clear_pending_actions()
            return
        current = self.selected_index if self.selected_index is not None else 0
        target_index = max(0, min(current + delta, len(rows) - 1))
        if self.select_index(target_index):
            return
        if target_index <= 0:
            self.status_message = "Selection already at the first bead."
            return
        if target_index >= len(rows) - 1:
            self.status_message = "Selection already at the last bead."
            return
        self.status_message = f"Selected {self.selected_bead_id}."

    def set_focused_panel(self, panel: str, *, announce: bool = True) -> None:
        if panel not in {PANEL_LIST, PANEL_DETAIL}:
            return
        if self.focused_panel == panel:
            return
        self.focused_panel = panel
        if announce:
            self.status_message = f"Focused {panel} panel."

    def cycle_focus(self, step: int = 1) -> None:
        panels = (PANEL_LIST, PANEL_DETAIL)
        index = panels.index(self.focused_panel)
        self.set_focused_panel(panels[(index + step) % len(panels)])

    def select_index(self, index: int) -> bool:
        rows = self.rows
        if not rows or index < 0 or index >= len(rows):
            return False
        if self.selected_index == index and self.selected_bead_id == rows[index].bead_id:
            return False
        self.selected_index = index
        self.selected_bead_id = rows[index].bead_id
        self.detail_scroll_offset = 0
        self._clear_pending_actions()
        self.status_message = f"Selected {self.selected_bead_id}."
        return True

    def move_selection_to_start(self) -> None:
        if self.select_index(0):
            return
        self.status_message = "Selection already at the first bead."

    def move_selection_to_end(self) -> None:
        rows = self.rows
        if self.select_index(len(rows) - 1):
            return
        if not rows:
            self.status_message = "No beads available for the current filter."
            return
        self.status_message = "Selection already at the last bead."

    def visible_list_height(self, viewport_height: int | None) -> int:
        return max(0, (viewport_height or 0) - 2)

    def visible_detail_height(self, viewport_height: int | None) -> int:
        return max(0, (viewport_height or 0) - 2)

    def ensure_selection_visible(self, viewport_height: int | None) -> None:
        if self.selected_index is None:
            self.list_scroll_offset = 0
            return
        visible_height = self.visible_list_height(viewport_height)
        if visible_height <= 0:
            self.list_scroll_offset = max(0, self.selected_index)
            return
        if self.selected_index < self.list_scroll_offset:
            self.list_scroll_offset = self.selected_index
            return
        max_visible_index = self.list_scroll_offset + visible_height - 1
        if self.selected_index > max_visible_index:
            self.list_scroll_offset = self.selected_index - visible_height + 1

    def detail_max_scroll(self, viewport_height: int | None) -> int:
        visible_height = self.visible_detail_height(viewport_height)
        if visible_height <= 0:
            return 0
        total_lines = self._rendered_detail_content_height
        if total_lines is None:
            total_lines = len(self.detail_panel_body().splitlines())
        return max(0, total_lines - visible_height)

    def clamp_detail_scroll(self, viewport_height: int | None) -> None:
        self.detail_scroll_offset = max(0, min(self.detail_scroll_offset, self.detail_max_scroll(viewport_height)))

    def set_rendered_detail_content_height(self, height: int | None) -> None:
        self._rendered_detail_content_height = None if height is None else max(0, int(height))

    def scroll_detail(self, delta: int, viewport_height: int | None) -> bool:
        previous_offset = self.detail_scroll_offset
        self.detail_scroll_offset = max(0, self.detail_scroll_offset + delta)
        self.clamp_detail_scroll(viewport_height)
        return self.detail_scroll_offset != previous_offset

    def page_detail(self, direction: int, viewport_height: int | None) -> bool:
        step = max(1, self.visible_detail_height(viewport_height) - 1)
        return self.scroll_detail(direction * step, viewport_height)

    def jump_detail_to_start(self) -> bool:
        if self.detail_scroll_offset == 0:
            return False
        self.detail_scroll_offset = 0
        return True

    def jump_detail_to_end(self, viewport_height: int | None) -> bool:
        new_offset = self.detail_max_scroll(viewport_height)
        if new_offset == self.detail_scroll_offset:
            return False
        self.detail_scroll_offset = new_offset
        return True

    def cycle_filter(self, step: int = 1) -> None:
        filters = supported_filter_modes()
        index = filters.index(self.filter_mode)
        self.filter_mode = filters[(index + step) % len(filters)]
        self._clear_pending_actions()
        self.refresh(activity_message=f"Switched filter to {self.filter_mode}.")
        self.status_message = f"Filter set to {self.filter_mode}."

    def footer_text(self) -> str:
        return format_footer(
            self.beads,
            filter_mode=self.filter_mode,
            selected_index=self.selected_index,
            total_rows=len(self.rows),
            focused_panel=self.focused_panel,
            timed_refresh_enabled=self.timed_refresh_enabled,
            continuous_run_enabled=self.continuous_run_enabled,
            refresh_seconds=self.refresh_seconds,
        )

    def status_panel_text(self) -> str:
        scheduler_indicator = " [RUNNING]" if self.scheduler_running else ""
        return "\n".join([
            f"{self.mode_summary()}{scheduler_indicator} | {self.status_message}",
            self.footer_text(),
        ])

    def mode_summary(self) -> str:
        if not self.timed_refresh_enabled:
            return f"manual refresh | scheduler=manual | focus={self.focused_panel}"
        if self.continuous_run_enabled:
            return f"timed scheduler every {self.refresh_seconds}s | focus={self.focused_panel}"
        return f"timed refresh every {self.refresh_seconds}s | scheduler=manual | focus={self.focused_panel}"

    def detail_panel_body(self, bead: Bead | None = None) -> str:
        target = bead if bead is not None else self.selected_bead()
        if target is None:
            return "No bead selected."
        cached = self._detail_cache.get(target.bead_id)
        if cached is not None and cached[0] == target:
            return cached[1]
        detail = format_detail_panel(target)
        self._detail_cache[target.bead_id] = (target, detail)
        return detail

    def open_help_overlay(self) -> None:
        self.help_overlay_visible = True
        self.status_message = "Help overlay open. Press ? or Esc to close."

    def close_help_overlay(self) -> bool:
        if not self.help_overlay_visible:
            return False
        self.help_overlay_visible = False
        self.status_message = "Help overlay closed."
        return True

    def toggle_help_overlay(self) -> bool:
        if self.help_overlay_visible:
            self.close_help_overlay()
            return False
        self.open_help_overlay()
        return True

    def request_merge(self) -> None:
        self._clear_pending_retry()
        self._clear_pending_status_flow()
        bead = self.selected_bead()
        if bead is None:
            self.status_message = "No bead selected."
            self.awaiting_merge_confirmation = False
            self.pending_merge_bead_id = None
            return
        if bead.status != BEAD_DONE:
            self.status_message = f"{bead.bead_id} is {bead.status}; only done beads can be merged."
            self.awaiting_merge_confirmation = False
            self.pending_merge_bead_id = None
            return
        self.awaiting_merge_confirmation = True
        self.pending_merge_bead_id = bead.bead_id
        self.status_message = f"Confirm merge for {bead.bead_id} with Enter."

    def confirm_merge(
        self,
        merge_callable: Callable[[Namespace, RepositoryStorage, ConsoleReporter], int] | None = None,
    ) -> bool:
        if not self.awaiting_merge_confirmation:
            self.status_message = "No merge pending confirmation."
            return False
        bead_id = self.pending_merge_bead_id
        if bead_id is None:
            self.status_message = "No merge pending confirmation."
            self.awaiting_merge_confirmation = False
            return False
        bead = next((row.bead for row in self.rows if row.bead_id == bead_id), None)
        if bead is None or bead.status != BEAD_DONE:
            self.status_message = f"Merge cancelled for {bead_id}; press m again."
            self.awaiting_merge_confirmation = False
            self.pending_merge_bead_id = None
            return False
        if merge_callable is None:
            from .cli import command_merge

            merge_callable = command_merge
        console_stream = io.StringIO()
        try:
            exit_code = merge_callable(Namespace(bead_id=bead.bead_id), self.storage, ConsoleReporter(stream=console_stream))
        except SystemExit as exc:
            self._record_action_result(
                f"merge {bead.bead_id}",
                "failed",
                status_message=f"Merge failed for {bead.bead_id}.",
            )
            detail = str(exc.code).strip() if exc.code not in (None, 0) else ""
            self.activity_message = detail or console_stream.getvalue().strip() or "Merge command exited early."
            self.awaiting_merge_confirmation = False
            self.pending_merge_bead_id = None
            return False
        except Exception as exc:
            self._record_action_result(
                f"merge {bead.bead_id}",
                f"failed: {exc}",
                status_message=f"Merge failed for {bead.bead_id}: {exc}",
            )
            self.activity_message = console_stream.getvalue().strip() or "Merge command raised an exception."
            self.awaiting_merge_confirmation = False
            self.pending_merge_bead_id = None
            return False
        self.awaiting_merge_confirmation = False
        self.pending_merge_bead_id = None
        if exit_code != 0:
            self._record_action_result(
                f"merge {bead.bead_id}",
                f"failed ({exit_code})",
                status_message=f"Merge failed for {bead.bead_id}.",
            )
            self.activity_message = console_stream.getvalue().strip() or f"Merge command exited with {exit_code}."
            return False
        self._record_action_result(
            f"merge {bead.bead_id}",
            "success",
            status_message=f"Merged {bead.bead_id}.",
        )
        self.refresh(activity_message=console_stream.getvalue().strip() or f"Merged {bead.bead_id}.")
        return True

    def run_scheduler_cycle(
        self,
        reporter: object | None = None,
    ) -> bool:
        """Run a single scheduler cycle. Called from a worker thread when async."""
        if self.scheduler_running:
            self.status_message = "Scheduler cycle already in progress."
            return False
        self.scheduler_running = True
        self._record_action_result(
            "scheduler run",
            "started",
            status_message="Scheduler cycle running...",
        )
        try:
            _, scheduler, _ = _make_services(self.storage.root)
            result = scheduler.run_once(
                max_workers=self.max_workers,
                feature_root_id=self.feature_root_id,
                reporter=reporter,
            )
        except Exception as exc:
            self.scheduler_running = False
            self._record_action_result(
                "scheduler run",
                f"failed: {exc}",
                status_message=f"Scheduler run failed: {exc}",
            )
            self.refresh(activity_message="Scheduler run raised an exception.")
            return False
        summary_parts = []
        if result.started:
            summary_parts.append(f"started={len(result.started)}")
        if result.completed:
            summary_parts.append(f"completed={len(result.completed)}")
        if result.blocked:
            summary_parts.append(f"blocked={len(result.blocked)}")
        if result.deferred:
            summary_parts.append(f"deferred={len(result.deferred)}")
        result_text = ", ".join(summary_parts) if summary_parts else "no ready beads"
        self.scheduler_running = False
        self._record_action_result(
            "scheduler run",
            "success",
            status_message=f"Cycle done: {result_text}",
        )
        self.refresh(activity_message=f"Cycle: {result_text}")
        return True

    def toggle_timed_refresh(self) -> None:
        if self.timed_refresh_enabled:
            self.timed_refresh_enabled = False
            self.continuous_run_enabled = False
            state = "manual"
            status_message = "Timed refresh disabled; manual mode active."
        else:
            self.timed_refresh_enabled = True
            state = f"refresh/{self.refresh_seconds}s"
            status_message = f"Timed refresh enabled every {self.refresh_seconds}s."
        self._record_action_result(
            "timed refresh",
            state,
            status_message=status_message,
        )

    def toggle_continuous_run(self) -> None:
        if self.continuous_run_enabled:
            self.continuous_run_enabled = False
            state = "disabled"
            status_message = "Timed scheduler disabled; timed refresh remains enabled."
        else:
            self.timed_refresh_enabled = True
            self.continuous_run_enabled = True
            state = "enabled"
            status_message = f"Timed scheduler enabled every {self.refresh_seconds}s."
        self._record_action_result(
            "continuous run",
            state,
            status_message=status_message,
        )

    def request_retry_selected_blocked_bead(self) -> bool:
        self._clear_pending_merge()
        self._clear_pending_status_flow()
        bead = self.selected_bead()
        if bead is None:
            self._record_action_result("retry", "invalid", status_message="No bead selected.")
            self.awaiting_retry_confirmation = False
            self.pending_retry_bead_id = None
            return False
        if bead.status != BEAD_BLOCKED:
            self._record_action_result(
                f"retry {bead.bead_id}",
                "invalid",
                status_message=f"{bead.bead_id} is {bead.status}; only blocked beads can be retried.",
            )
            self.awaiting_retry_confirmation = False
            self.pending_retry_bead_id = None
            return False
        self.awaiting_retry_confirmation = True
        self.pending_retry_bead_id = bead.bead_id
        self.status_message = f"Confirm retry for {bead.bead_id} with y; c cancels."
        return True

    def confirm_retry_selected_blocked_bead(self) -> bool:
        from .cli import command_retry

        if not self.awaiting_retry_confirmation:
            self._record_action_result("retry", "invalid", status_message="No retry pending confirmation.")
            return False
        bead_id = self.pending_retry_bead_id
        if bead_id is None:
            self._record_action_result("retry", "invalid", status_message="No retry pending confirmation.")
            self.awaiting_retry_confirmation = False
            return False
        bead = next((row.bead for row in self.rows if row.bead_id == bead_id), None)
        if bead is None or bead.status != BEAD_BLOCKED:
            self._record_action_result(
                f"retry {bead_id}",
                "invalid",
                status_message=f"Retry cancelled for {bead_id}; press t again.",
            )
            self.awaiting_retry_confirmation = False
            self.pending_retry_bead_id = None
            return False
        self.awaiting_retry_confirmation = False
        self.pending_retry_bead_id = None
        console_stream = io.StringIO()
        try:
            exit_code = command_retry(Namespace(bead_id=bead.bead_id), self.storage, ConsoleReporter(stream=console_stream))
        except SystemExit as exc:
            self._record_action_result(
                f"retry {bead.bead_id}",
                "failed",
                status_message=f"Retry failed for {bead.bead_id}.",
            )
            detail = str(exc.code).strip() if exc.code not in (None, 0) else ""
            self.refresh(activity_message=detail or console_stream.getvalue().strip() or "Retry command exited early.")
            return False
        except Exception as exc:
            self._record_action_result(
                f"retry {bead.bead_id}",
                f"failed: {exc}",
                status_message=f"Retry failed for {bead.bead_id}: {exc}",
            )
            self.refresh(activity_message=console_stream.getvalue().strip() or "Retry raised an exception.")
            return False
        result_text = console_stream.getvalue().strip() or f"Retried {bead.bead_id}."
        if exit_code != 0:
            self._record_action_result(
                f"retry {bead.bead_id}",
                f"failed ({exit_code})",
                status_message=f"Retry failed for {bead.bead_id}.",
            )
            self.refresh(activity_message=result_text)
            return False
        self._record_action_result(
            f"retry {bead.bead_id}",
            "success",
            status_message=f"Retried {bead.bead_id}.",
        )
        self.refresh(activity_message=result_text)
        return True

    def open_status_update_flow(self) -> None:
        self._clear_pending_merge()
        self._clear_pending_retry()
        bead = self.selected_bead()
        if bead is None:
            self._record_action_result("status update", "invalid", status_message="No bead selected.")
            return
        self.status_flow_active = True
        self.pending_status_bead_id = bead.bead_id
        self.pending_status_target = None
        self.status_message = (
            f"Status update for {bead.bead_id}: press r, b, or d, then y to confirm or c to cancel."
        )

    def choose_status_target(self, target_status: str) -> None:
        bead_id = self.pending_status_bead_id
        if not self.status_flow_active or bead_id is None:
            self.status_message = "Press u before choosing a status update."
            return
        if target_status not in STATUS_ACTION_TARGETS:
            self.status_message = f"Unsupported status target: {target_status}."
            return
        self.pending_status_target = target_status
        self.status_message = f"Confirm update for {bead_id} -> {target_status} with y; c cancels."

    def cancel_pending_action(self) -> bool:
        if self.awaiting_merge_confirmation:
            bead_id = self.pending_merge_bead_id or "selected bead"
            self._clear_pending_merge()
            self.status_message = f"Cancelled merge for {bead_id}."
            return True
        if self.awaiting_retry_confirmation:
            bead_id = self.pending_retry_bead_id or "selected bead"
            self._clear_pending_retry()
            self.status_message = f"Cancelled retry for {bead_id}."
            return True
        if self.status_flow_active:
            bead_id = self.pending_status_bead_id or "selected bead"
            self._clear_pending_status_flow()
            self.status_message = f"Cancelled status update for {bead_id}."
            return True
        self.status_message = "No pending action to cancel."
        return False

    def confirm_status_update(self) -> bool:
        from .cli import apply_operator_status_update

        bead_id = self.pending_status_bead_id
        target_status = self.pending_status_target
        if not self.status_flow_active or bead_id is None:
            self._record_action_result(
                "status update",
                "invalid",
                status_message="No status update pending confirmation.",
            )
            return False
        if target_status is None:
            self._record_action_result(
                f"status update {bead_id}",
                "invalid",
                status_message=f"Choose ready, blocked, or done for {bead_id} before confirming.",
            )
            return False
        try:
            apply_operator_status_update(self.storage, bead_id, target_status)
        except ValueError as exc:
            self._record_action_result(
                f"status update {bead_id}",
                "invalid",
                status_message=str(exc),
            )
            self._clear_pending_status_flow()
            self.refresh(activity_message=f"No status change applied to {bead_id}.")
            return False
        except Exception as exc:
            self._record_action_result(
                f"status update {bead_id}",
                f"failed: {exc}",
                status_message=f"Status update failed for {bead_id}: {exc}",
            )
            self._clear_pending_status_flow()
            self.refresh(activity_message="Status update raised an exception.")
            return False
        self._record_action_result(
            f"status update {bead_id}",
            f"success -> {target_status}",
            status_message=f"Updated {bead_id} to {target_status}.",
        )
        self._clear_pending_status_flow()
        self.refresh(activity_message=f"Updated {bead_id} to {target_status}.")
        return True

    def _clear_pending_merge(self) -> None:
        self.awaiting_merge_confirmation = False
        self.pending_merge_bead_id = None

    def _clear_pending_retry(self) -> None:
        self.awaiting_retry_confirmation = False
        self.pending_retry_bead_id = None

    def _clear_pending_status_flow(self) -> None:
        self.status_flow_active = False
        self.pending_status_bead_id = None
        self.pending_status_target = None

    def _clear_pending_actions(self) -> None:
        self._clear_pending_merge()
        self._clear_pending_retry()
        self._clear_pending_status_flow()

    def _record_action_result(self, action: str, result: str, *, status_message: str) -> None:
        self.last_action = action
        self.last_result = result
        self.last_action_at = datetime.now().strftime("%H:%M:%S")
        self.status_message = status_message


class TuiSchedulerReporter:
    """SchedulerReporter that posts events to a Textual app from a worker thread."""

    def __init__(self, app: object, state: TuiRuntimeState) -> None:
        self._app = app
        self._state = state

    def _post(self, text: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {text}"
        self._state.scheduler_log.append(line)
        try:
            self._app.call_from_thread(self._app._append_log_line, line)
        except Exception:
            pass

    def stop(self) -> None:
        pass

    def lease_expired(self, bead_id: str) -> None:
        self._post(f"Lease expired: {bead_id} requeued")

    def bead_started(self, bead: Bead) -> None:
        self._post(f"[{bead.bead_id}] Started {bead.agent_type}: {bead.title}")

    def worktree_ready(self, bead: Bead, branch_name: str, worktree_path: Path) -> None:
        self._post(f"[{bead.bead_id}] Worktree ready: {worktree_path}")

    def bead_completed(self, bead: Bead, summary: str, created: list[Bead]) -> None:
        self._post(f"[{bead.bead_id}] Completed")
        for child in created:
            self._post(f"[{bead.bead_id}] Created followup {child.bead_id} ({child.agent_type})")

    def bead_deferred(self, bead: Bead, summary: str) -> None:
        self._post(f"[{bead.bead_id}] Deferred: {summary}")

    def bead_blocked(self, bead: Bead, summary: str) -> None:
        self._post(f"[{bead.bead_id}] Blocked: {summary}")

    def bead_failed(self, bead: Bead, summary: str) -> None:
        self._post(f"[{bead.bead_id}] Failed: {summary}")


def render_tree_panel(
    rows: list[TreeRow],
    selected_index: int | None,
    *,
    filter_mode: str = FILTER_DEFAULT,
    focused: bool = False,
    scroll_offset: int = 0,
    viewport_height: int | None = None,
    panel_width: int | None = None,
) -> str:
    if not rows:
        return "No beads match the current filter."

    width = panel_width if panel_width is not None else _DEFAULT_PANEL_WIDTH
    visible_rows = rows
    if viewport_height is not None:
        visible_height = max(0, viewport_height)
        visible_rows = rows[scroll_offset:scroll_offset + visible_height]
    selected_marker = ">>" if focused else " >"
    lines: list[str] = []
    for index, row in enumerate(visible_rows, start=scroll_offset):
        marker = selected_marker if selected_index == index else "  "
        badge = _telemetry_badge(row.bead)
        status_tag = f" [{row.bead.status}]"
        indent = "  " * row.depth
        bead_prefix = f"{row.bead.bead_id} · "
        suffix = f"{status_tag}{badge}"
        # Fixed parts: marker + space + indent + bead_prefix + suffix
        fixed_len = len(marker) + 1 + len(indent) + len(bead_prefix) + len(suffix)
        title_budget = width - fixed_len
        title = _truncate_title(row.bead.title, max(0, title_budget))
        lines.append(f"{marker} {indent}{bead_prefix}{title}{suffix}")
    return "\n".join(lines)


def render_detail_panel(
    bead: Bead | None,
    *,
    focused: bool = False,
    scroll_offset: int = 0,
    viewport_height: int | None = None,
) -> str:
    lines = format_detail_panel(bead).splitlines()
    if viewport_height is not None:
        visible_height = max(0, viewport_height - 1)
        lines = lines[scroll_offset:scroll_offset + visible_height]
    focus_hint = "Arrow keys scroll here." if focused else "Press Tab to focus."
    return "\n".join([focus_hint, *lines])


def load_textual_runtime() -> ModuleType:
    try:
        import textual  # type: ignore
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "The `orchestrator tui` command requires the optional `textual` package. "
            "Install dependencies and retry."
        ) from exc
    return textual


def build_tui_app(
    storage: RepositoryStorage,
    *,
    feature_root_id: str | None = None,
    refresh_seconds: int = 3,
    max_workers: int = 1,
):
    load_textual_runtime()
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.css.query import NoMatches
    from textual.containers import Center, Horizontal, Vertical, VerticalScroll
    from textual.screen import ModalScreen
    from textual.widgets import Collapsible, RichLog, Static, Tree

    class FocusableStatic(Static):
        can_focus = True

    class BeadTree(Tree[Bead]):
        """Tree widget for displaying beads with native expand/collapse."""

        show_root = False

        def _node_label(self, bead: Bead, width: int | None = None) -> str:
            badge = _telemetry_badge(bead)
            prefix = f"{bead.bead_id} · "
            suffix = f" [{bead.status}]{badge}"
            avail = (width if width is not None else _DEFAULT_PANEL_WIDTH)
            title_budget = avail - len(prefix) - len(suffix)
            title = _truncate_title(bead.title, max(0, title_budget))
            return f"{prefix}{title}{suffix}"

    class HelpOverlay(ModalScreen[None]):
        CSS = """
        HelpOverlay {
            align: center middle;
            background: $background 60%;
        }

        #help-dialog {
            width: 48;
            height: auto;
            border: round $accent;
            padding: 1 2;
            background: $surface;
        }
        """

        BINDINGS = [
            Binding("escape", "close_overlay", "Close", show=False),
            Binding("question_mark", "close_overlay", "Close Help", show=False),
        ]

        def __init__(self, runtime_state: TuiRuntimeState) -> None:
            super().__init__()
            self.runtime_state = runtime_state

        def compose(self) -> ComposeResult:
            with Center():
                yield Static(format_help_overlay(), id="help-dialog")

        def on_key(self, event: object) -> None:
            key = getattr(event, "key", None)
            if key not in {"escape", "question_mark"} and hasattr(event, "stop"):
                event.stop()

        def action_close_overlay(self) -> None:
            self.runtime_state.close_help_overlay()
            self.dismiss(None)

    class OrchestratorTuiApp(App[None]):
        CSS = """
        Screen {
            layout: vertical;
        }

        #top-row {
            height: 1fr;
        }

        #list-panel, #detail-panel {
            border: round $accent;
            padding: 1;
            width: 1fr;
        }

        #bead-tree, #bead-detail {
            height: 1fr;
        }

        #list-panel, #detail-panel {
            overflow-y: auto;
        }

        #bead-detail {
            height: auto;
        }

        #detail-summary {
            padding-bottom: 1;
        }

        .detail-section {
            margin-top: 1;
        }

        .detail-section.-active {
            background: $accent 10%;
            tint: $accent 5%;
        }

        .focused {
            border: double $success;
            background: $success 12%;
            tint: $success 8%;
        }

        #status-bar {
            height: 3;
            border: round $accent;
            padding: 0 1;
        }

        #scheduler-log {
            height: 8;
            border: round $accent;
            padding: 0 1;
        }
        """

        BINDINGS = [
            Binding("q", "quit", "Quit"),
            Binding("tab", "focus_next_panel", "Next Panel", show=False, priority=True),
            Binding("shift+tab", "focus_previous_panel", "Prev Panel", show=False, priority=True),
            Binding("j", "move_down", "Down", show=False),
            Binding("k", "move_up", "Up", show=False),
            Binding("down", "move_down", "Down", show=False),
            Binding("up", "move_up", "Up", show=False),
            Binding("pageup", "page_up", "Page Up", show=False),
            Binding("pagedown", "page_down", "Page Down", show=False),
            Binding("home", "go_home", "Home", show=False),
            Binding("end", "go_end", "End", show=False),
            Binding("g", "go_home", "Go to Top", show=False),
            Binding("G", "go_end", "Go to Bottom", show=False),
            Binding("n", "next_detail_section", "Next Detail Section", show=False),
            Binding("N", "previous_detail_section", "Prev Detail Section", show=False),
            Binding("f", "filter_next", "Next Filter"),
            Binding("shift+f", "filter_previous", "Prev Filter", show=False),
            Binding("question_mark", "toggle_help", "Help", show=False),
            Binding("a", "toggle_timed_refresh", "Auto Refresh"),
            Binding("r", "manual_refresh", "Refresh"),
            Binding("s", "scheduler_once", "Run Once"),
            Binding("S", "toggle_continuous_run", "Auto Run"),
            Binding("t", "retry_blocked", "Retry"),
            Binding("u", "start_status_update", "Status"),
            Binding("m", "request_merge", "Merge"),
            Binding("enter", "confirm_merge", "Confirm", show=False, priority=True),
            Binding("b", "choose_blocked_status", "Blocked", show=False),
            Binding("d", "choose_done_status", "Done", show=False),
            Binding("y", "confirm_pending_action", "Confirm", show=False),
            Binding("c", "cancel_pending_action", "Cancel", show=False),
            Binding("E", "toggle_all_tree_nodes", "Expand/Collapse All", show=False),
        ]

        def __init__(self) -> None:
            super().__init__()
            self.runtime_state = TuiRuntimeState(
                storage,
                feature_root_id=feature_root_id,
                refresh_seconds=refresh_seconds,
                max_workers=max_workers,
            )
            self._last_list_render = ()
            self._last_detail_render = ""
            self._last_status_render = ""
            self._active_detail_section_index = 0
            self._detail_collapsed = {section: True for section in DETAIL_SECTION_ORDER}
            self._collapsed_bead_ids: set[str] = set()
            self._scheduler_worker_running = False

        def compose(self) -> ComposeResult:
            with Horizontal(id="top-row"):
                with Vertical(id="list-panel"):
                    yield BeadTree("Beads", id="bead-tree")
                with VerticalScroll(id="detail-panel", can_focus=True):
                    with Vertical(id="bead-detail"):
                        yield Static(id="detail-summary")
                        for section in DETAIL_SECTION_ORDER:
                            yield Collapsible(
                                Static(id=f"detail-{section}-body"),
                                id=f"detail-{section}",
                                title=_detail_section_title(section),
                                collapsed=self._detail_collapsed[section],
                                classes="detail-section",
                            )
            yield Static(id="status-bar")
            yield RichLog(id="scheduler-log", auto_scroll=True, wrap=True)

        def on_mount(self) -> None:
            self.title = "Orchestrator TUI"
            self.sub_title = feature_root_id or "all features"
            self.set_interval(refresh_seconds, self._on_interval_tick)
            self._populate_bead_tree()
            self._render_all()
            self._sync_panel_focus()
            try:
                log_widget = self.query_one("#scheduler-log", RichLog)
                log_widget.border_title = Text("Scheduler Log")
                log_widget.write(Text.from_markup("[dim]Press s to run a scheduler cycle, S for continuous mode[/dim]"))
            except NoMatches:
                pass

        def action_focus_next_panel(self) -> None:
            self.runtime_state.cycle_focus(1)
            self._render_focus()
            self._sync_panel_focus()
            self._update_status_panel()

        def action_focus_previous_panel(self) -> None:
            self.runtime_state.cycle_focus(-1)
            self._render_focus()
            self._sync_panel_focus()
            self._update_status_panel()

        def action_move_down(self) -> None:
            if self.runtime_state.focused_panel == PANEL_DETAIL:
                if not self.runtime_state.scroll_detail(1, self._detail_viewport_height()):
                    self.runtime_state.status_message = "Detail view already at the bottom."
                self._sync_detail_scroll()
            else:
                try:
                    bead_tree = self.query_one("#bead-tree", BeadTree)
                    bead_tree.action_cursor_down()
                except NoMatches:
                    pass
            self._update_status_panel()

        def action_move_up(self) -> None:
            if self.runtime_state.focused_panel == PANEL_DETAIL:
                if not self.runtime_state.scroll_detail(-1, self._detail_viewport_height()):
                    self.runtime_state.status_message = "Detail view already at the top."
                self._sync_detail_scroll()
            else:
                try:
                    bead_tree = self.query_one("#bead-tree", BeadTree)
                    bead_tree.action_cursor_up()
                except NoMatches:
                    pass
            self._update_status_panel()

        def action_page_up(self) -> None:
            if self.runtime_state.focused_panel == PANEL_DETAIL:
                if not self.runtime_state.page_detail(-1, self._detail_viewport_height()):
                    self.runtime_state.status_message = "Detail view already at the top."
                self._sync_detail_scroll()
            else:
                try:
                    bead_tree = self.query_one("#bead-tree", BeadTree)
                    bead_tree.action_page_up()
                except NoMatches:
                    pass
            self._update_status_panel()

        def action_page_down(self) -> None:
            if self.runtime_state.focused_panel == PANEL_DETAIL:
                if not self.runtime_state.page_detail(1, self._detail_viewport_height()):
                    self.runtime_state.status_message = "Detail view already at the bottom."
                self._sync_detail_scroll()
            else:
                try:
                    bead_tree = self.query_one("#bead-tree", BeadTree)
                    bead_tree.action_page_down()
                except NoMatches:
                    pass
            self._update_status_panel()

        def action_go_home(self) -> None:
            if self.runtime_state.focused_panel == PANEL_DETAIL:
                if not self.runtime_state.jump_detail_to_start():
                    self.runtime_state.status_message = "Detail view already at the top."
                self._sync_detail_scroll()
            else:
                try:
                    bead_tree = self.query_one("#bead-tree", BeadTree)
                    bead_tree.action_scroll_home()
                except NoMatches:
                    pass
            self._update_status_panel()

        def action_go_end(self) -> None:
            if self.runtime_state.focused_panel == PANEL_DETAIL:
                if not self.runtime_state.jump_detail_to_end(self._detail_viewport_height()):
                    self.runtime_state.status_message = "Detail view already at the bottom."
                self._sync_detail_scroll()
            else:
                try:
                    bead_tree = self.query_one("#bead-tree", BeadTree)
                    bead_tree.action_scroll_end()
                except NoMatches:
                    pass
            self._update_status_panel()

        def action_filter_next(self) -> None:
            self._collapsed_bead_ids.clear()
            self.runtime_state.cycle_filter(1)
            self._render_all(force_detail=True, reset_detail_scroll=True)

        def action_filter_previous(self) -> None:
            self._collapsed_bead_ids.clear()
            self.runtime_state.cycle_filter(-1)
            self._render_all(force_detail=True, reset_detail_scroll=True)

        def action_previous_detail_section(self) -> None:
            self._move_detail_section(-1)

        def action_next_detail_section(self) -> None:
            self._move_detail_section(1)

        def action_toggle_timed_refresh(self) -> None:
            self.runtime_state.toggle_timed_refresh()
            self._update_status_panel()

        def action_manual_refresh(self) -> None:
            if self.runtime_state.status_flow_active:
                self.runtime_state.choose_status_target(BEAD_READY)
                self._update_status_panel()
                return
            self.runtime_state._clear_pending_actions()
            self.runtime_state.refresh(activity_message="Manual refresh completed.")
            self.runtime_state.status_message = "Refreshed bead state."
            self._render_all(force_detail=True)

        def action_scheduler_once(self) -> None:
            self._start_scheduler_worker()

        def action_toggle_continuous_run(self) -> None:
            self.runtime_state.toggle_continuous_run()
            self._update_status_panel()

        def action_retry_blocked(self) -> None:
            self.runtime_state.request_retry_selected_blocked_bead()
            self._update_status_panel()

        def action_start_status_update(self) -> None:
            self.runtime_state.open_status_update_flow()
            self._update_status_panel()

        def action_toggle_help(self) -> None:
            if self.runtime_state.toggle_help_overlay():
                self._update_status_panel()
                self.push_screen(HelpOverlay(self.runtime_state), callback=lambda _: self._update_status_panel())
                return
            self._update_status_panel()

        def action_request_merge(self) -> None:
            self.runtime_state.request_merge()
            self._update_status_panel()

        def action_confirm_merge(self) -> None:
            if self.runtime_state.help_overlay_visible:
                return
            if self.runtime_state.focused_panel == PANEL_DETAIL and not self.runtime_state.awaiting_merge_confirmation:
                if self._toggle_active_detail_section():
                    return
            if not self.runtime_state.awaiting_merge_confirmation and self.runtime_state.focused_panel == PANEL_LIST:
                # Delegate Enter to the Tree for expand/collapse toggle
                try:
                    bead_tree = self.query_one("#bead-tree", BeadTree)
                    bead_tree.action_toggle_node()
                except NoMatches:
                    pass
                return
            self.runtime_state.confirm_merge()
            self._render_all(force_detail=True)

        def action_choose_blocked_status(self) -> None:
            self.runtime_state.choose_status_target(BEAD_BLOCKED)
            self._update_status_panel()

        def action_choose_done_status(self) -> None:
            self.runtime_state.choose_status_target(BEAD_DONE)
            self._update_status_panel()

        def action_confirm_pending_action(self) -> None:
            if self.runtime_state.awaiting_retry_confirmation:
                self.runtime_state.confirm_retry_selected_blocked_bead()
            else:
                self.runtime_state.confirm_status_update()
            self._render_all(force_detail=True)

        def action_cancel_pending_action(self) -> None:
            self.runtime_state.cancel_pending_action()
            self._update_status_panel()

        def action_toggle_all_tree_nodes(self) -> None:
            """Toggle all tree nodes between fully expanded and fully collapsed."""
            try:
                bead_tree = self.query_one("#bead-tree", BeadTree)
            except NoMatches:
                return
            rows = self.runtime_state.rows
            expandable_ids = {row.bead.bead_id for row in rows if row.has_children}
            if not expandable_ids:
                return
            # If any expandable node is collapsed, expand all; otherwise collapse all.
            any_collapsed = bool(self._collapsed_bead_ids & expandable_ids)
            if any_collapsed:
                self._collapsed_bead_ids.clear()
            else:
                self._collapsed_bead_ids = set(expandable_ids)
            self._populate_bead_tree()

        def _on_interval_tick(self) -> None:
            if not self.runtime_state.timed_refresh_enabled:
                return
            if self.runtime_state.continuous_run_enabled:
                self._start_scheduler_worker()
            else:
                self.runtime_state.refresh()
                self._render_all(force_detail=True)

        def _render_panels(self) -> None:
            self._render_all(force_detail=True)

        def _render_all(self, *, force_detail: bool = False, reset_detail_scroll: bool = False) -> None:
            self._render_focus()
            self._update_list_panel()
            self._update_detail_panel(force=force_detail, reset_scroll=reset_detail_scroll)
            self._update_status_panel()

        def _render_focus(self) -> None:
            try:
                list_panel = self.query_one("#list-panel", Vertical)
                detail_panel = self.query_one("#detail-panel", VerticalScroll)
                status_bar = self.query_one("#status-bar", Static)
            except NoMatches:
                # Main panels are not mounted on top-level while modal screens are active.
                return

            list_panel.set_class(self.runtime_state.focused_panel == PANEL_LIST, "focused")
            detail_panel.set_class(self.runtime_state.focused_panel == PANEL_DETAIL, "focused")
            list_panel.border_title = Text(
                _beads_panel_title(
                    self.runtime_state.filter_mode,
                    focused=self.runtime_state.focused_panel == PANEL_LIST,
                )
            )
            list_panel.border_subtitle = "Enter/j/k move selection" if self.runtime_state.focused_panel == PANEL_LIST else "Tab to activate"
            detail_panel.border_title = Text(_panel_badge("Details", focused=self.runtime_state.focused_panel == PANEL_DETAIL))
            detail_panel.border_subtitle = (
                Text("j/k scroll | n/N section | Enter toggle")
                if self.runtime_state.focused_panel == PANEL_DETAIL
                else "Tab to activate"
            )
            status_bar.border_title = Text("Status")

        def _sync_panel_focus(self) -> None:
            try:
                if self.runtime_state.focused_panel == PANEL_DETAIL:
                    self.query_one("#detail-panel", VerticalScroll).focus()
                    self._focus_active_detail_section()
                else:
                    self.query_one("#bead-tree", BeadTree).focus()
            except NoMatches:
                return

        def _populate_bead_tree(self) -> None:
            """Rebuild the Tree widget from current runtime_state rows."""
            try:
                bead_tree = self.query_one("#bead-tree", BeadTree)
            except NoMatches:
                return

            bead_tree.clear()
            rows = self.runtime_state.rows
            if not rows:
                bead_tree.root.set_label("No beads match the current filter.")
                return

            bead_tree.root.set_label("Beads")
            # Build a map from bead_id to tree node for parent lookups
            node_map: dict[str, object] = {}
            for row in rows:
                bead = row.bead
                parent_node = node_map.get(bead.parent_id) if bead.parent_id else None
                target = parent_node if parent_node is not None else bead_tree.root
                tree_width = bead_tree.size.width if bead_tree.size.width > 0 else None
                label = bead_tree._node_label(bead, width=tree_width)
                if row.has_children:
                    node = target.add(label, data=bead)
                else:
                    node = target.add_leaf(label, data=bead)
                node_map[bead.bead_id] = node

            # Restore collapsed state
            for bead_id in self._collapsed_bead_ids:
                if bead_id in node_map:
                    node_map[bead_id].collapse()

            # Expand all non-collapsed nodes (Tree defaults to collapsed)
            for bead_id, node in node_map.items():
                if bead_id not in self._collapsed_bead_ids and hasattr(node, 'expand'):
                    node.expand()

            # Also expand root
            bead_tree.root.expand()

            # Restore selection
            selected_id = self.runtime_state.selected_bead_id
            if selected_id and selected_id in node_map:
                bead_tree.select_node(node_map[selected_id])

        def _update_list_panel(self) -> None:
            # Build a cache key from bead IDs, statuses, and titles to skip redundant rebuilds
            cache_key = tuple(
                (row.bead_id, row.bead.status, row.bead.title, row.depth)
                for row in self.runtime_state.rows
            )
            if cache_key == self._last_list_render:
                return
            self._last_list_render = cache_key
            self._populate_bead_tree()

        def _update_detail_panel(self, *, force: bool = False, reset_scroll: bool = False) -> None:
            try:
                detail_summary = self.query_one("#detail-summary", Static)
            except NoMatches:
                return
            if reset_scroll:
                self._reset_detail_sections()
                self.runtime_state.jump_detail_to_start()
            bead = self.runtime_state.selected_bead()
            detail_render = render_detail_panel(bead, focused=self.runtime_state.focused_panel == PANEL_DETAIL)
            if force or detail_render != self._last_detail_render:
                detail_summary.update("\n".join(_detail_summary_lines(bead)))
                self._refresh_detail_sections(bead)
                self._last_detail_render = detail_render
            self.call_after_refresh(self._sync_detail_scroll)

        def _update_status_panel(self) -> None:
            try:
                status_bar = self.query_one("#status-bar", Static)
            except NoMatches:
                return
            status_render = self.runtime_state.status_panel_text()
            if status_render != self._last_status_render:
                status_bar.update(status_render)
                self._last_status_render = status_render

        def _sync_detail_scroll(self) -> None:
            try:
                detail_panel = self.query_one("#detail-panel", VerticalScroll)
            except NoMatches:
                return
            self.runtime_state.set_rendered_detail_content_height(detail_panel.virtual_size.height)
            self.runtime_state.clamp_detail_scroll(self._detail_viewport_height())
            detail_panel.scroll_to(y=self.runtime_state.detail_scroll_offset, animate=False, force=True)

        def _list_viewport_height(self) -> int | None:
            try:
                return self.query_one("#bead-tree", BeadTree).content_region.height
            except NoMatches:
                return None

        def _detail_viewport_height(self) -> int | None:
            try:
                return self.query_one("#detail-panel", VerticalScroll).content_region.height
            except NoMatches:
                return None

        def _active_detail_section(self) -> str:
            return DETAIL_SECTION_ORDER[self._active_detail_section_index]

        def _reset_detail_sections(self) -> None:
            self._active_detail_section_index = 0
            self._detail_collapsed = {section: True for section in DETAIL_SECTION_ORDER}

        def _refresh_detail_sections(self, bead: Bead | None) -> None:
            for section in DETAIL_SECTION_ORDER:
                body = self.query_one(f"#detail-{section}-body", Static)
                body.update(_detail_section_body(bead, section))
                collapsible = self.query_one(f"#detail-{section}", Collapsible)
                collapsible.collapsed = self._detail_collapsed[section]
                collapsible.set_class(section == DETAIL_SECTION_ORDER[self._active_detail_section_index], "-active")

        def _focus_active_detail_section(self) -> None:
            try:
                collapsible = self.query_one(f"#detail-{self._active_detail_section()}", Collapsible)
            except NoMatches:
                return
            title = next((child for child in collapsible.children if hasattr(child, "focus")), None)
            if title is not None:
                title.focus()

        def _move_detail_section(self, step: int) -> None:
            if self.runtime_state.focused_panel != PANEL_DETAIL:
                return
            next_index = max(0, min(self._active_detail_section_index + step, len(DETAIL_SECTION_ORDER) - 1))
            if next_index == self._active_detail_section_index:
                self.runtime_state.status_message = (
                    "Already at the last detail section." if step > 0 else "Already at the first detail section."
                )
                self._update_status_panel()
                return
            self._active_detail_section_index = next_index
            self.runtime_state.status_message = f"Active detail section: {_detail_section_title(self._active_detail_section())}."
            self._update_detail_panel(force=True)
            self.call_after_refresh(self._focus_active_detail_section)
            self._update_status_panel()

        def _toggle_active_detail_section(self) -> bool:
            if self.runtime_state.selected_bead() is None:
                return False
            section = self._active_detail_section()
            self._detail_collapsed[section] = not self._detail_collapsed[section]
            state = "collapsed" if self._detail_collapsed[section] else "expanded"
            self.runtime_state.status_message = f"{_detail_section_title(section)} {state}."
            self._update_detail_panel(force=True)
            self.call_after_refresh(self._focus_active_detail_section)
            return True

        def on_tree_node_highlighted(self, event: Tree.NodeHighlighted[Bead]) -> None:
            bead = event.node.data
            if bead is None:
                return
            previous_selection = self._selection_marker()
            for index, row in enumerate(self.runtime_state.rows):
                if row.bead_id == bead.bead_id:
                    self.runtime_state.select_index(index)
                    break
            changed = self._selection_changed(previous_selection)
            self._update_detail_panel(force=changed, reset_scroll=changed)
            self._update_status_panel()

        def on_tree_node_collapsed(self, event: Tree.NodeCollapsed[Bead]) -> None:
            bead = event.node.data
            if bead is not None:
                self._collapsed_bead_ids.add(bead.bead_id)

        def on_tree_node_expanded(self, event: Tree.NodeExpanded[Bead]) -> None:
            bead = event.node.data
            if bead is not None:
                self._collapsed_bead_ids.discard(bead.bead_id)

        def on_collapsible_collapsed(self, event: Collapsible.Collapsed) -> None:
            self._sync_detail_state_from_collapsible(event.collapsible, collapsed=True)

        def on_collapsible_expanded(self, event: Collapsible.Expanded) -> None:
            self._sync_detail_state_from_collapsible(event.collapsible, collapsed=False)

        def _selection_marker(self) -> tuple[str | None, int | None]:
            return self.runtime_state.selected_bead_id, self.runtime_state.selected_index

        def _selection_changed(self, previous_selection: tuple[str | None, int | None]) -> bool:
            return previous_selection != self._selection_marker()

        def _widget_matches_panel(self, widget: object, target_ids: set[str]) -> bool:
            current = widget
            while current is not None:
                if getattr(current, "id", None) in target_ids:
                    return True
                current = getattr(current, "parent", None)
            return False

        def on_click(self, event: object) -> None:
            widget = getattr(event, "widget", None)
            if widget is None:
                return
            if self._widget_matches_panel(widget, {"bead-tree", "list-panel"}):
                # Tree widget handles its own click-to-select; just sync focus
                self.runtime_state.set_focused_panel(PANEL_LIST, announce=False)
                self._render_focus()
                self._sync_panel_focus()
                self._update_status_panel()
                return
            if self._widget_matches_panel(widget, {"bead-detail", "detail-panel"}):
                self.runtime_state.set_focused_panel(PANEL_DETAIL, announce=False)
                self._sync_detail_section_from_widget(widget)
                self._render_focus()
                self._sync_panel_focus()
                self._update_status_panel()

        def on_mouse_scroll_down(self, event: object) -> None:
            self._route_mouse_scroll(event, direction=1)

        def on_mouse_scroll_up(self, event: object) -> None:
            self._route_mouse_scroll(event, direction=-1)

        def _route_mouse_scroll(self, event: object, *, direction: int) -> None:
            widget = getattr(event, "widget", None)
            if self._widget_matches_panel(widget, {"bead-detail", "detail-panel"}):
                self.runtime_state.set_focused_panel(PANEL_DETAIL, announce=False)
                changed = self.runtime_state.scroll_detail(direction, self._detail_viewport_height())
                if not changed:
                    self.runtime_state.status_message = (
                        "Detail view already at the bottom." if direction > 0 else "Detail view already at the top."
                    )
                self._render_focus()
                self._sync_panel_focus()
                self._sync_detail_scroll()
                self._update_status_panel()
                if hasattr(event, "stop"):
                    event.stop()
                return
            if self._widget_matches_panel(widget, {"bead-tree", "list-panel"}):
                # Tree widget handles its own scrolling
                self.runtime_state.set_focused_panel(PANEL_LIST, announce=False)
                self._render_focus()
                self._sync_panel_focus()
                self._update_status_panel()
                return
            if self.runtime_state.focused_panel == PANEL_DETAIL:
                self.runtime_state.scroll_detail(direction, self._detail_viewport_height())
                self._sync_detail_scroll()
            else:
                # Fallback: delegate scroll to the tree
                pass
            self._update_status_panel()

        def _sync_detail_section_from_widget(self, widget: object) -> None:
            current = widget
            while current is not None:
                widget_id = getattr(current, "id", None)
                for index, section in enumerate(DETAIL_SECTION_ORDER):
                    if widget_id == f"detail-{section}":
                        self._active_detail_section_index = index
                        return
                current = getattr(current, "parent", None)

        def _sync_detail_state_from_collapsible(self, collapsible: Collapsible, *, collapsed: bool) -> None:
            for index, section in enumerate(DETAIL_SECTION_ORDER):
                if collapsible.id != f"detail-{section}":
                    continue
                self._active_detail_section_index = index
                self._detail_collapsed[section] = collapsed
                collapsible.set_class(True, "-active")
                self.runtime_state.status_message = (
                    f"{_detail_section_title(section)} {'collapsed' if collapsed else 'expanded'}."
                )
                self._refresh_detail_sections(self.runtime_state.selected_bead())
                self.call_after_refresh(self._sync_detail_scroll)
                self._update_status_panel()
                return

        # ── Async scheduler worker ───────────────────────────────

        def _start_scheduler_worker(self) -> None:
            """Launch a scheduler cycle in a background worker thread."""
            if self._scheduler_worker_running:
                self.runtime_state.status_message = "Scheduler cycle already in progress."
                self._update_status_panel()
                return
            self._scheduler_worker_running = True
            self.runtime_state.scheduler_running = True
            self._append_log_line(f"[{datetime.now().strftime('%H:%M:%S')}] Scheduler cycle starting...")
            self._update_status_panel()
            self.run_worker(self._scheduler_worker_task, exclusive=True)

        def _scheduler_worker_task(self) -> bool:
            """Runs in a worker thread. Uses TuiSchedulerReporter for live events."""
            reporter = TuiSchedulerReporter(self, self.runtime_state)
            try:
                return self.runtime_state.run_scheduler_cycle(reporter=reporter)
            finally:
                self.call_from_thread(self._on_scheduler_worker_done)

        def _on_scheduler_worker_done(self) -> None:
            """Called on the main thread when the worker finishes."""
            self._scheduler_worker_running = False
            self.runtime_state.scheduler_running = False
            self._render_all(force_detail=True)

        def _append_log_line(self, line: str) -> None:
            """Append a line to the scheduler log widget. Must be called on the main thread."""
            try:
                log_widget = self.query_one("#scheduler-log", RichLog)
                log_widget.write(Text.from_markup(line))
            except NoMatches:
                pass

    return OrchestratorTuiApp()


def run_tui(
    storage: RepositoryStorage,
    *,
    feature_root_id: str | None = None,
    refresh_seconds: int = 3,
    max_workers: int = 1,
    stream: object | None = None,
) -> int:
    try:
        app = build_tui_app(storage, feature_root_id=feature_root_id, refresh_seconds=refresh_seconds, max_workers=max_workers)
    except RuntimeError as exc:
        target = stream if hasattr(stream, "write") else None
        message = f"{exc}\nHint: install project dependencies so `textual` is available.\n"
        if target is None:
            raise SystemExit(message.rstrip())
        target.write(message)
        if hasattr(target, "flush"):
            target.flush()
        return 1

    app.run()
    return 0
