from __future__ import annotations

import io
from argparse import Namespace
from dataclasses import dataclass
from datetime import datetime
from types import ModuleType
from typing import Callable, Iterable

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


FILTER_DEFAULT = "default"
FILTER_ALL = "all"
FILTER_ACTIONABLE = "actionable"
FILTER_DEFERRED = "deferred"
FILTER_DONE = "done"

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
) -> str:
    cursor = "-" if selected_index is None else str(selected_index + 1)
    return f"filter={filter_mode} | rows={total_rows} | selected={cursor} | {format_status_counts(beads)} | ? help"


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
    return "\n".join(lines)


def format_help_overlay() -> str:
    return "\n".join(
        [
            "Shortcuts",
            "",
            "j / Down    Move selection down",
            "k / Up      Move selection up",
            "f           Next filter",
            "r           Refresh now",
            "m           Request merge",
            "Enter       Confirm merge",
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
    selected_bead_id: str | None = None
    selected_index: int | None = None
    status_message: str = "Press q to quit."
    activity_message: str = "Waiting for first refresh."
    awaiting_merge_confirmation: bool = False
    pending_merge_bead_id: str | None = None
    help_overlay_visible: bool = False

    def __post_init__(self) -> None:
        self.refresh(activity_message="Loaded bead state.")

    @property
    def rows(self) -> list[TreeRow]:
        return collect_tree_rows(
            self.storage,
            filter_mode=self.filter_mode,
            feature_root_id=self.feature_root_id,
        )

    @property
    def beads(self) -> list[Bead]:
        return load_beads(
            self.storage,
            filter_mode=self.filter_mode,
            feature_root_id=self.feature_root_id,
        )

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
        try:
            rows = self.rows
        except Exception as exc:
            self.awaiting_merge_confirmation = False
            self.pending_merge_bead_id = None
            self.status_message = f"Refresh failed: {exc}"
            if activity_message is None:
                activity_message = f"Refresh failed at {datetime.now().strftime('%H:%M:%S')}."
            self.activity_message = activity_message
            return
        previous_index = self.selected_index
        self.selected_index = resolve_selected_index(
            rows,
            selected_bead_id=self.selected_bead_id,
            previous_index=previous_index,
        )
        selected = self.selected_bead()
        self.selected_bead_id = selected.bead_id if selected is not None else None
        if pending_merge_bead_id is not None:
            pending_bead = next((row.bead for row in rows if row.bead_id == pending_merge_bead_id), None)
            if pending_bead is None or pending_bead.status != BEAD_DONE:
                self.awaiting_merge_confirmation = False
                self.pending_merge_bead_id = None
                self.status_message = "Merge confirmation cleared because the requested bead is no longer mergeable."
        if activity_message is None:
            activity_message = f"Refreshed at {datetime.now().strftime('%H:%M:%S')}."
        self.activity_message = activity_message

    def move_selection(self, delta: int) -> None:
        rows = self.rows
        if not rows:
            self.selected_index = None
            self.selected_bead_id = None
            self.status_message = "No beads available for the current filter."
            self.awaiting_merge_confirmation = False
            self.pending_merge_bead_id = None
            return
        current = self.selected_index if self.selected_index is not None else 0
        self.selected_index = max(0, min(current + delta, len(rows) - 1))
        self.selected_bead_id = rows[self.selected_index].bead_id
        self.awaiting_merge_confirmation = False
        self.pending_merge_bead_id = None
        self.status_message = f"Selected {self.selected_bead_id}."

    def cycle_filter(self, step: int = 1) -> None:
        filters = supported_filter_modes()
        index = filters.index(self.filter_mode)
        self.filter_mode = filters[(index + step) % len(filters)]
        self.awaiting_merge_confirmation = False
        self.pending_merge_bead_id = None
        self.refresh(activity_message=f"Switched filter to {self.filter_mode}.")
        self.status_message = f"Filter set to {self.filter_mode}."

    def footer_text(self) -> str:
        return format_footer(
            self.beads,
            filter_mode=self.filter_mode,
            selected_index=self.selected_index,
            total_rows=len(self.rows),
        )

    def status_panel_text(self) -> str:
        return "\n".join([
            f"Status: {self.status_message}",
            f"Activity: {self.activity_message}",
            self.footer_text(),
        ])

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
            self.status_message = f"Merge failed for {bead.bead_id}."
            detail = str(exc.code).strip() if exc.code not in (None, 0) else ""
            self.activity_message = detail or console_stream.getvalue().strip() or "Merge command exited early."
            self.awaiting_merge_confirmation = False
            self.pending_merge_bead_id = None
            return False
        except Exception as exc:
            self.status_message = f"Merge failed for {bead.bead_id}: {exc}"
            self.activity_message = console_stream.getvalue().strip() or "Merge command raised an exception."
            self.awaiting_merge_confirmation = False
            self.pending_merge_bead_id = None
            return False
        self.awaiting_merge_confirmation = False
        self.pending_merge_bead_id = None
        if exit_code != 0:
            self.status_message = f"Merge failed for {bead.bead_id}."
            self.activity_message = console_stream.getvalue().strip() or f"Merge command exited with {exit_code}."
            return False
        self.status_message = f"Merged {bead.bead_id}."
        self.refresh(activity_message=console_stream.getvalue().strip() or f"Merged {bead.bead_id}.")
        return True


def render_tree_panel(rows: list[TreeRow], selected_index: int | None) -> str:
    if not rows:
        return "Beads\n\nNo beads match the current filter."

    lines = ["Beads", ""]
    for index, row in enumerate(rows):
        marker = ">" if selected_index == index else " "
        lines.append(f"{marker} {row.label} [{row.bead.status}]")
    return "\n".join(lines)


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
):
    load_textual_runtime()
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Center, Horizontal, Vertical
    from textual.screen import ModalScreen
    from textual.widgets import Static

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

        #list-panel, #detail-panel, #status-panel {
            border: round $accent;
            padding: 1;
        }

        #list-panel, #detail-panel {
            width: 1fr;
        }

        #status-panel {
            height: 7;
        }
        """

        BINDINGS = [
            Binding("q", "quit", "Quit"),
            Binding("j", "move_down", "Down", show=False),
            Binding("k", "move_up", "Up", show=False),
            Binding("down", "move_down", "Down", show=False),
            Binding("up", "move_up", "Up", show=False),
            Binding("f", "filter_next", "Next Filter"),
            Binding("shift+f", "filter_previous", "Prev Filter", show=False),
            Binding("question_mark", "toggle_help", "Help", show=False),
            Binding("r", "manual_refresh", "Refresh"),
            Binding("m", "request_merge", "Merge"),
            Binding("enter", "confirm_merge", "Confirm", show=False),
        ]

        def __init__(self) -> None:
            super().__init__()
            self.runtime_state = TuiRuntimeState(storage, feature_root_id=feature_root_id)

        def compose(self) -> ComposeResult:
            with Horizontal(id="top-row"):
                with Vertical(id="list-panel"):
                    yield Static(id="bead-list")
                with Vertical(id="detail-panel"):
                    yield Static(id="bead-detail")
            yield Static(id="status-panel")

        def on_mount(self) -> None:
            self.title = "Orchestrator TUI"
            self.sub_title = feature_root_id or "all features"
            self.set_interval(refresh_seconds, self._refresh_from_storage)
            self._render_panels()

        def action_move_down(self) -> None:
            self.runtime_state.move_selection(1)
            self._render_panels()

        def action_move_up(self) -> None:
            self.runtime_state.move_selection(-1)
            self._render_panels()

        def action_filter_next(self) -> None:
            self.runtime_state.cycle_filter(1)
            self._render_panels()

        def action_filter_previous(self) -> None:
            self.runtime_state.cycle_filter(-1)
            self._render_panels()

        def action_manual_refresh(self) -> None:
            self.runtime_state.awaiting_merge_confirmation = False
            self.runtime_state.pending_merge_bead_id = None
            self.runtime_state.refresh(activity_message="Manual refresh completed.")
            self.runtime_state.status_message = "Refreshed bead state."
            self._render_panels()

        def action_toggle_help(self) -> None:
            if self.runtime_state.toggle_help_overlay():
                self._render_panels()
                self.push_screen(HelpOverlay(self.runtime_state), callback=lambda _: self._render_panels())
                return
            self._render_panels()

        def action_request_merge(self) -> None:
            self.runtime_state.request_merge()
            self._render_panels()

        def action_confirm_merge(self) -> None:
            self.runtime_state.confirm_merge()
            self._render_panels()

        def _refresh_from_storage(self) -> None:
            self.runtime_state.refresh()
            self._render_panels()

        def _render_panels(self) -> None:
            self.query_one("#bead-list", Static).update(
                render_tree_panel(self.runtime_state.rows, self.runtime_state.selected_index)
            )
            self.query_one("#bead-detail", Static).update(
                format_detail_panel(self.runtime_state.selected_bead())
            )
            self.query_one("#status-panel", Static).update(self.runtime_state.status_panel_text())

    return OrchestratorTuiApp()


def run_tui(
    storage: RepositoryStorage,
    *,
    feature_root_id: str | None = None,
    refresh_seconds: int = 3,
    stream: object | None = None,
) -> int:
    try:
        app = build_tui_app(storage, feature_root_id=feature_root_id, refresh_seconds=refresh_seconds)
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
