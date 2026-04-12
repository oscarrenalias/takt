from __future__ import annotations

from datetime import datetime, timezone

from ..models import BEAD_IN_PROGRESS, Bead
from .constants import (
    DETAIL_SECTION_ACCEPTANCE,
    DETAIL_SECTION_FILES,
    DETAIL_SECTION_HANDOFF,
    DETAIL_SECTION_HISTORY,
    DETAIL_SECTION_TELEMETRY,
    EXECUTION_HISTORY_DISPLAY_LIMIT,
    _format_block,
    _format_duration_ms,
    _format_list,
    _value_or_dash,
)
from .tree import FILTER_DEFAULT, TreeRow

_DEFAULT_PANEL_WIDTH = 120


def _panel_badge(panel_name: str, *, focused: bool) -> str:
    state = "ACTIVE" if focused else "idle"
    return f"{panel_name} [{state}]"


def _format_filter_label(filter_mode: str) -> str:
    return filter_mode.replace("_", " ").title()


def _beads_panel_title(filter_mode: str, *, focused: bool) -> str:
    return _panel_badge(f"Beads [{_format_filter_label(filter_mode)}]", focused=focused)


def _detail_summary_lines(bead: "Bead | None") -> list[str]:
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


def _detail_section_body(bead: "Bead | None", section: str, subtree_telemetry: dict | None = None) -> str:
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
        if subtree_telemetry is not None:
            sub_cost = subtree_telemetry.get("cost_usd", 0)
            sub_duration = subtree_telemetry.get("duration_ms", 0)
            sub_count = subtree_telemetry.get("bead_count", 0)
            lines.append(f"Subtree: ${sub_cost:.2f} total, {_format_duration_ms(sub_duration)} duration, {sub_count} beads")
        return "\n".join(lines)
    if section == DETAIL_SECTION_HISTORY:
        exec_history = bead.execution_history
        if not exec_history:
            return "No execution history."
        lines = []
        omitted = len(exec_history) - EXECUTION_HISTORY_DISPLAY_LIMIT
        if omitted > 0:
            lines.append(f"... {omitted} earlier entries omitted")
        for record in exec_history[-EXECUTION_HISTORY_DISPLAY_LIMIT:]:
            lines.append(f"[{record.timestamp}] {record.event} ({record.agent_type}): {record.summary}")
        return "\n".join(lines)
    raise ValueError(f"Unknown detail section: {section}")


def _detail_section_title(section: str) -> str:
    titles = {
        DETAIL_SECTION_ACCEPTANCE: "Acceptance Criteria",
        DETAIL_SECTION_FILES: "Files",
        DETAIL_SECTION_HANDOFF: "Handoff",
        DETAIL_SECTION_TELEMETRY: "Telemetry",
        DETAIL_SECTION_HISTORY: "Execution History",
    }
    return titles[section]


def _truncate_title(title: str, max_width: int) -> str:
    """Truncate *title* to *max_width* characters, adding '...' when trimmed."""
    if len(title) <= max_width:
        return title
    if max_width <= 3:
        return "..."[:max_width]
    return title[: max_width - 3] + "..."


def _telemetry_badge(bead: Bead, subtree_telemetry: dict | None = None) -> str:
    """Return compact telemetry badge.

    Without subtree: '[$0.32, 2:55]' (own cost + duration).
    With subtree (parent bead): '[$0.32 / $1.85]' (own cost / subtree total cost).
    """
    telemetry = bead.metadata.get("telemetry")
    own_cost = (telemetry or {}).get("cost_usd")

    if subtree_telemetry is not None:
        subtree_cost = subtree_telemetry.get("cost_usd")
        own_str = f"${own_cost:.2f}" if own_cost is not None else "-"
        sub_str = f"${subtree_cost:.2f}" if subtree_cost is not None else "-"
        if own_cost is not None or subtree_cost is not None:
            return f" [{own_str} / {sub_str}]"
        return ""

    if not telemetry:
        return ""
    duration = telemetry.get("duration_ms") or telemetry.get("duration_api_ms")
    parts: list[str] = []
    if own_cost is not None:
        parts.append(f"${own_cost:.2f}")
    if duration is not None:
        parts.append(_format_duration_ms(duration))
    if not parts:
        return ""
    return f" [{', '.join(parts)}]"


def _elapsed_badge(bead: Bead) -> str:
    """Return elapsed runtime badge for in-progress beads, e.g. ' (8m 23s)'.

    Computed from the most recent 'started' execution history entry to now.
    Returns empty string for non-running beads or if no started entry is found.
    """
    if bead.status != BEAD_IN_PROGRESS:
        return ""
    started_at: str | None = None
    for record in reversed(bead.execution_history):
        if record.event == "started":
            started_at = record.timestamp
            break
    if started_at is None:
        return ""
    try:
        start_dt = datetime.fromisoformat(started_at)
        elapsed = datetime.now(timezone.utc) - start_dt
        total_seconds = int(elapsed.total_seconds())
        if total_seconds < 0:
            return ""
        minutes = total_seconds // 60
        seconds = total_seconds % 60
        return f" ({minutes}m {seconds:02d}s)"
    except (ValueError, TypeError):
        return ""


def _stale_badge(bead: Bead) -> str:
    """Return stale lease indicator for in-progress beads with expired leases.

    Returns ' stale?' if the bead is in_progress and its lease expires_at is in the past.
    Returns empty string otherwise.
    """
    if bead.status != BEAD_IN_PROGRESS:
        return ""
    if bead.lease is None:
        return ""
    try:
        expires_dt = datetime.fromisoformat(bead.lease.expires_at)
        if datetime.now(timezone.utc) > expires_dt:
            return " stale?"
    except (ValueError, TypeError):
        pass
    return ""


def format_detail_panel(bead: Bead | None, subtree_telemetry: dict | None = None) -> str:
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
        if subtree_telemetry is not None:
            sub_cost = subtree_telemetry.get("cost_usd", 0)
            sub_duration = subtree_telemetry.get("duration_ms", 0)
            sub_count = subtree_telemetry.get("bead_count", 0)
            lines.append(f"  Subtree: ${sub_cost:.2f} total, {_format_duration_ms(sub_duration)} duration, {sub_count} beads")
    exec_history = bead.execution_history
    if exec_history:
        lines.append("Execution History:")
        omitted = len(exec_history) - EXECUTION_HISTORY_DISPLAY_LIMIT
        if omitted > 0:
            lines.append(f"  ... {omitted} earlier entries omitted")
        for record in exec_history[-EXECUTION_HISTORY_DISPLAY_LIMIT:]:
            lines.append(f"  [{record.timestamp}] {record.event} ({record.agent_type}): {record.summary}")
    return "\n".join(lines)


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
            "c           Cancel pending retry/status",
            "M           Merge: use 'takt merge <id>' from CLI",
            "m           Toggle maximize panel",
            "Enter       Toggle detail section",
            "E           Expand/collapse all tree nodes",
            "q           Quit",
            "",
            "? / Esc     Close help",
        ]
    )


def render_tree_panel(
    rows: list[TreeRow],
    selected_index: int | None,
    *,
    filter_mode: str = FILTER_DEFAULT,
    focused: bool = False,
    scroll_offset: int = 0,
    viewport_height: int | None = None,
    panel_width: int | None = None,
    deferred_bead_ids: set[str] | None = None,
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
        elapsed = _elapsed_badge(row.bead)
        stale = _stale_badge(row.bead)
        deferred = " \u2298 deferred" if (deferred_bead_ids and row.bead.bead_id in deferred_bead_ids) else ""
        status_tag = f" [{row.bead.status}]"
        indent = "  " * row.depth
        bead_prefix = f"{row.bead.bead_id} · "
        suffix = f"{status_tag}{elapsed}{stale}{deferred}{badge}"
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
    subtree_telemetry: dict | None = None,
) -> str:
    lines = format_detail_panel(bead, subtree_telemetry=subtree_telemetry).splitlines()
    if viewport_height is not None:
        visible_height = max(0, viewport_height - 1)
        lines = lines[scroll_offset:scroll_offset + visible_height]
    focus_hint = "Arrow keys scroll here." if focused else "Press Tab to focus."
    return "\n".join([focus_hint, *lines])
