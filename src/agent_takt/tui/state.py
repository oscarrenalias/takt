from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
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
from .constants import (
    DETAIL_SECTION_ACCEPTANCE,
    DETAIL_SECTION_FILES,
    DETAIL_SECTION_HANDOFF,
    DETAIL_SECTION_HISTORY,
    DETAIL_SECTION_ORDER,
    DETAIL_SECTION_TELEMETRY,
    EXECUTION_HISTORY_DISPLAY_LIMIT,
    _format_block,
    _format_duration_ms,
    _format_list,
    _value_or_dash,
)
from .tree import (
    FILTER_ALL,
    FILTER_ACTIONABLE,
    FILTER_DEFAULT,
    FILTER_DEFERRED,
    FILTER_DONE,
    FILTER_STATUS_SETS,
    TreeRow,
    _status_set,
    bead_matches_filter,
    build_tree_rows,
    collect_tree_rows,
    load_beads,
    resolve_selected_bead,
    resolve_selected_index,
    supported_filter_modes,
)
from .render import format_detail_panel


def _compute_subtree_telemetry(bead_id: str, all_beads: list[Bead]) -> dict | None:
    """Aggregate telemetry for all descendants of bead_id (children, grandchildren, etc.).

    Returns None if the bead has no children. The aggregated dict contains:
    cost_usd, duration_ms, input_tokens, output_tokens, bead_count.
    """
    children_by_parent: dict[str, list[str]] = {}
    bead_map: dict[str, Bead] = {}
    for b in all_beads:
        bead_map[b.bead_id] = b
        if b.parent_id:
            children_by_parent.setdefault(b.parent_id, []).append(b.bead_id)

    if bead_id not in children_by_parent:
        return None

    total_cost = 0.0
    total_duration = 0
    total_input_tokens = 0
    total_output_tokens = 0
    bead_count = 0

    def _collect(bid: str) -> None:
        nonlocal total_cost, total_duration, total_input_tokens, total_output_tokens, bead_count
        for child_id in children_by_parent.get(bid, []):
            child = bead_map.get(child_id)
            if child is None:
                continue
            bead_count += 1
            tel = child.metadata.get("telemetry")
            if tel:
                total_cost += tel.get("cost_usd") or 0
                total_duration += tel.get("duration_ms") or tel.get("duration_api_ms") or 0
                total_input_tokens += tel.get("input_tokens") or 0
                total_output_tokens += tel.get("output_tokens") or 0
            _collect(child_id)

    _collect(bead_id)

    return {
        "cost_usd": total_cost,
        "duration_ms": total_duration,
        "input_tokens": total_input_tokens,
        "output_tokens": total_output_tokens,
        "bead_count": bead_count,
    }


PANEL_LIST = "list"
PANEL_DETAIL = "detail"
PANEL_SCHEDULER_LOG = "scheduler-log"
STATUS_ACTION_TARGETS = (BEAD_READY, BEAD_BLOCKED, BEAD_DONE)

STATUS_DISPLAY_ORDER = (
    BEAD_OPEN,
    BEAD_READY,
    BEAD_IN_PROGRESS,
    BEAD_BLOCKED,
    BEAD_HANDED_OFF,
    BEAD_DONE,
)


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
    maximized_panel: str | None = None
    scheduler_running: bool = False
    scheduler_log: list[str] = field(default_factory=list)
    deferred_this_cycle: set[str] = field(default_factory=set)
    max_workers: int = 1
    last_action: str = "-"
    last_result: str = "-"
    last_action_at: str = "-"
    _rows_cache: list[TreeRow] = field(default_factory=list, init=False, repr=False)
    _beads_cache: list[Bead] = field(default_factory=list, init=False, repr=False)
    _detail_cache: dict[str, tuple[Bead, dict | None, str]] = field(default_factory=dict, init=False, repr=False)
    _subtree_cache: dict[str, dict | None] = field(default_factory=dict, init=False, repr=False)
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
        parent_ids = {b.parent_id for b in beads if b.parent_id}
        self._subtree_cache = {
            b.bead_id: _compute_subtree_telemetry(b.bead_id, beads) if b.bead_id in parent_ids else None
            for b in beads
        }
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
        if panel not in {PANEL_LIST, PANEL_DETAIL, PANEL_SCHEDULER_LOG}:
            return
        if self.focused_panel == panel:
            return
        self.focused_panel = panel
        if announce:
            self.status_message = f"Focused {panel} panel."

    def cycle_focus(self, step: int = 1) -> None:
        panels = (PANEL_LIST, PANEL_DETAIL, PANEL_SCHEDULER_LOG)
        index = panels.index(self.focused_panel) if self.focused_panel in panels else 0
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

    def subtree_telemetry_for(self, bead_id: str) -> dict | None:
        """Return precomputed subtree telemetry for bead_id, or None if no children."""
        return self._subtree_cache.get(bead_id)

    def detail_panel_body(self, bead: Bead | None = None) -> str:
        target = bead if bead is not None else self.selected_bead()
        if target is None:
            return "No bead selected."
        subtree_tel = self.subtree_telemetry_for(target.bead_id)
        cached = self._detail_cache.get(target.bead_id)
        if cached is not None and cached[0] == target and cached[1] == subtree_tel:
            return cached[2]
        detail = format_detail_panel(target, subtree_telemetry=subtree_tel)
        self._detail_cache[target.bead_id] = (target, subtree_tel, detail)
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
        from .actions import request_merge as _request_merge
        _request_merge(self)

    def confirm_merge(self, merge_callable=None) -> bool:
        from .actions import confirm_merge as _confirm_merge
        return _confirm_merge(self, merge_callable)

    def run_scheduler_cycle(self, reporter: object | None = None) -> bool:
        """Run a single scheduler cycle. Called from a worker thread when async."""
        from .actions import run_scheduler_cycle as _run_scheduler_cycle
        return _run_scheduler_cycle(self, reporter)

    def toggle_timed_refresh(self) -> None:
        from .actions import toggle_timed_refresh as _toggle_timed_refresh
        _toggle_timed_refresh(self)

    def toggle_continuous_run(self) -> None:
        from .actions import toggle_continuous_run as _toggle_continuous_run
        _toggle_continuous_run(self)

    def request_retry_selected_blocked_bead(self) -> bool:
        from .actions import request_retry_selected_blocked_bead as _request_retry
        return _request_retry(self)

    def confirm_retry_selected_blocked_bead(self) -> bool:
        from .actions import confirm_retry_selected_blocked_bead as _confirm_retry
        return _confirm_retry(self)

    def open_status_update_flow(self) -> None:
        from .actions import open_status_update_flow as _open_status_update_flow
        _open_status_update_flow(self)

    def choose_status_target(self, target_status: str) -> None:
        from .actions import choose_status_target as _choose_status_target
        _choose_status_target(self, target_status)

    def cancel_pending_action(self) -> bool:
        from .actions import cancel_pending_action as _cancel_pending_action
        return _cancel_pending_action(self)

    def confirm_status_update(self) -> bool:
        from .actions import confirm_status_update as _confirm_status_update
        return _confirm_status_update(self)

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
