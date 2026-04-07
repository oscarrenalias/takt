from __future__ import annotations

import io
from argparse import Namespace
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Iterable

from ..console import ConsoleReporter
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


def _format_duration_ms(ms: float | int | None) -> str:
    """Format milliseconds as m:ss."""
    if ms is None:
        return "-"
    total_seconds = int(ms / 1000)
    minutes = total_seconds // 60
    seconds = total_seconds % 60
    return f"{minutes}:{seconds:02d}"


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
DETAIL_SECTION_ACCEPTANCE = "acceptance"
DETAIL_SECTION_FILES = "files"
DETAIL_SECTION_HANDOFF = "handoff"
DETAIL_SECTION_TELEMETRY = "telemetry"
DETAIL_SECTION_HISTORY = "history"
DETAIL_SECTION_ORDER = (
    DETAIL_SECTION_ACCEPTANCE,
    DETAIL_SECTION_FILES,
    DETAIL_SECTION_HANDOFF,
    DETAIL_SECTION_TELEMETRY,
    DETAIL_SECTION_HISTORY,
)
EXECUTION_HISTORY_DISPLAY_LIMIT = 5

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
    maximized_panel: str | None = None
    scheduler_running: bool = False
    scheduler_log: list[str] = field(default_factory=list)
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
        from .render import format_detail_panel  # lazy import to avoid circular dependency
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
        self._clear_pending_retry()
        self._clear_pending_status_flow()
        self._clear_pending_merge()
        bead = self.selected_bead()
        bead_id = bead.bead_id if bead is not None else "<id>"
        self.status_message = f"Use CLI to merge: takt merge {bead_id}"

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
            from ..cli import command_merge

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
            from . import _make_services  # lazy import: keeps _make_services in tui.__init__ namespace for test patches
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
        from ..cli import command_retry

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
        from ..cli import apply_operator_status_update

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
