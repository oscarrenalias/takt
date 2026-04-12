from __future__ import annotations

from pathlib import Path

from .reporter import TuiSchedulerReporter
from .render import (
    _detail_section_body,
    _detail_section_title,
)
from .app import (
    build_tui_app,
    run_tui,
)
from .render import (
    _DEFAULT_PANEL_WIDTH,
    _telemetry_badge,
    _truncate_title,
    format_detail_panel,
    format_help_overlay,
    render_detail_panel,
    render_tree_panel,
)
from .state import (
    DETAIL_SECTION_ACCEPTANCE,
    DETAIL_SECTION_FILES,
    DETAIL_SECTION_HANDOFF,
    DETAIL_SECTION_HISTORY,
    DETAIL_SECTION_ORDER,
    DETAIL_SECTION_TELEMETRY,
    EXECUTION_HISTORY_DISPLAY_LIMIT,
    FILTER_ALL,
    FILTER_ACTIONABLE,
    FILTER_DEFAULT,
    FILTER_DEFERRED,
    FILTER_DONE,
    FILTER_STATUS_SETS,
    PANEL_DETAIL,
    PANEL_LIST,
    PANEL_SCHEDULER_LOG,
    STATUS_ACTION_TARGETS,
    STATUS_DISPLAY_ORDER,
    TreeRow,
    TuiRuntimeState,
    _compute_subtree_telemetry,
    _format_block,
    _format_duration_ms,
    _format_list,
    _status_set,
    _value_or_dash,
    bead_matches_filter,
    build_tree_rows,
    collect_tree_rows,
    format_footer,
    format_status_counts,
    load_beads,
    resolve_selected_bead,
    resolve_selected_index,
    summarize_status_counts,
    supported_filter_modes,
)


def _make_services(root: Path):
    """Lazy import to avoid circular dependency with cli module.

    Kept in tui.__init__ namespace so that actions.py can patch it in tests via
    ``patch("agent_takt.tui._make_services", ...)``.
    """
    from ..cli import make_services
    return make_services(root)
