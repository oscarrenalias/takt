from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_takt.models import (
    BEAD_DONE,
    BEAD_READY,
    Bead,
    ExecutionRecord,
)
from agent_takt.storage import RepositoryStorage
from agent_takt.tui import (
    DETAIL_SECTION_HISTORY,
    DETAIL_SECTION_TELEMETRY,
    EXECUTION_HISTORY_DISPLAY_LIMIT,
    _detail_section_body,
    _detail_section_title,
    _format_duration_ms,
    _telemetry_badge,
    build_tree_rows,
    build_tui_app,
    format_detail_panel,
    render_tree_panel,
)

class TuiTelemetryDisplayTests(unittest.TestCase):
    """Tests for B0132: telemetry summary in bead list and detail panels."""

    # -- _format_duration_ms -------------------------------------------------

    def test_format_duration_ms_none_returns_dash(self) -> None:
        self.assertEqual("-", _format_duration_ms(None))

    def test_format_duration_ms_zero(self) -> None:
        self.assertEqual("0:00", _format_duration_ms(0))

    def test_format_duration_ms_under_one_minute(self) -> None:
        self.assertEqual("0:45", _format_duration_ms(45_000))

    def test_format_duration_ms_exact_minute(self) -> None:
        self.assertEqual("1:00", _format_duration_ms(60_000))

    def test_format_duration_ms_multi_minute(self) -> None:
        self.assertEqual("2:55", _format_duration_ms(175_000))

    def test_format_duration_ms_pads_seconds(self) -> None:
        self.assertEqual("1:05", _format_duration_ms(65_000))

    def test_format_duration_ms_float_input(self) -> None:
        self.assertEqual("0:30", _format_duration_ms(30_500.7))

    # -- _telemetry_badge ----------------------------------------------------

    def test_telemetry_badge_no_metadata_returns_empty(self) -> None:
        bead = Bead(bead_id="B0001", title="T", agent_type="developer", description="d")
        self.assertEqual("", _telemetry_badge(bead))

    def test_telemetry_badge_empty_telemetry_returns_empty(self) -> None:
        bead = Bead(bead_id="B0001", title="T", agent_type="developer", description="d")
        bead.metadata["telemetry"] = {}
        self.assertEqual("", _telemetry_badge(bead))

    def test_telemetry_badge_cost_and_duration(self) -> None:
        bead = Bead(bead_id="B0001", title="T", agent_type="developer", description="d")
        bead.metadata["telemetry"] = {"cost_usd": 0.32, "duration_ms": 175_000}
        self.assertEqual(" [$0.32, 2:55]", _telemetry_badge(bead))

    def test_telemetry_badge_cost_only(self) -> None:
        bead = Bead(bead_id="B0001", title="T", agent_type="developer", description="d")
        bead.metadata["telemetry"] = {"cost_usd": 1.5}
        self.assertEqual(" [$1.50]", _telemetry_badge(bead))

    def test_telemetry_badge_duration_only(self) -> None:
        bead = Bead(bead_id="B0001", title="T", agent_type="developer", description="d")
        bead.metadata["telemetry"] = {"duration_ms": 60_000}
        self.assertEqual(" [1:00]", _telemetry_badge(bead))

    def test_telemetry_badge_falls_back_to_duration_api_ms(self) -> None:
        bead = Bead(bead_id="B0001", title="T", agent_type="developer", description="d")
        bead.metadata["telemetry"] = {"cost_usd": 0.10, "duration_api_ms": 90_000}
        self.assertEqual(" [$0.10, 1:30]", _telemetry_badge(bead))

    def test_telemetry_badge_prefers_duration_ms_over_api_ms(self) -> None:
        bead = Bead(bead_id="B0001", title="T", agent_type="developer", description="d")
        bead.metadata["telemetry"] = {
            "cost_usd": 0.50,
            "duration_ms": 120_000,
            "duration_api_ms": 90_000,
        }
        self.assertEqual(" [$0.50, 2:00]", _telemetry_badge(bead))

    def test_telemetry_badge_zero_cost(self) -> None:
        bead = Bead(bead_id="B0001", title="T", agent_type="developer", description="d")
        bead.metadata["telemetry"] = {"cost_usd": 0.0, "duration_ms": 5_000}
        self.assertEqual(" [$0.00, 0:05]", _telemetry_badge(bead))

    # -- render_tree_panel badge integration ---------------------------------

    def test_render_tree_panel_includes_telemetry_badge(self) -> None:
        bead = Bead(bead_id="B0001", title="Task", agent_type="developer", description="d", status=BEAD_READY)
        bead.metadata["telemetry"] = {"cost_usd": 0.42, "duration_ms": 130_000}
        rows = build_tree_rows([bead])
        output = render_tree_panel(rows, selected_index=0, focused=True)
        self.assertIn("[$0.42, 2:10]", output)

    def test_render_tree_panel_no_badge_without_telemetry(self) -> None:
        bead = Bead(bead_id="B0001", title="Task", agent_type="developer", description="d", status=BEAD_READY)
        rows = build_tree_rows([bead])
        output = render_tree_panel(rows, selected_index=0, focused=True)
        self.assertNotIn("[", output.split("[ready]")[-1].strip())

    # -- format_detail_panel telemetry section --------------------------------

    def test_format_detail_panel_includes_telemetry_section(self) -> None:
        bead = Bead(bead_id="B0001", title="T", agent_type="developer", description="d", status=BEAD_DONE)
        bead.metadata["telemetry"] = {
            "cost_usd": 1.23,
            "duration_ms": 175_000,
            "num_turns": 5,
            "input_tokens": 10_000,
            "output_tokens": 2_000,
            "cache_read_tokens": 500,
            "prompt_chars": 8_000,
            "session_id": "sess-abc123",
        }
        detail = format_detail_panel(bead)
        self.assertIn("Telemetry:", detail)
        self.assertIn("cost_usd: $1.23", detail)
        self.assertIn("duration: 2:55", detail)
        self.assertIn("num_turns: 5", detail)
        self.assertIn("input_tokens: 10000", detail)
        self.assertIn("output_tokens: 2000", detail)
        self.assertIn("cache_read_tokens: 500", detail)
        self.assertIn("prompt_chars: 8000", detail)
        self.assertIn("session_id: sess-abc123", detail)

    def test_format_detail_panel_no_telemetry_omits_section(self) -> None:
        bead = Bead(bead_id="B0001", title="T", agent_type="developer", description="d", status=BEAD_DONE)
        detail = format_detail_panel(bead)
        self.assertNotIn("Telemetry:", detail)

    def test_format_detail_panel_telemetry_missing_optional_fields_shows_dash(self) -> None:
        bead = Bead(bead_id="B0001", title="T", agent_type="developer", description="d", status=BEAD_DONE)
        bead.metadata["telemetry"] = {"cost_usd": 0.50, "duration_ms": 30_000}
        detail = format_detail_panel(bead)
        self.assertIn("Telemetry:", detail)
        self.assertIn("cost_usd: $0.50", detail)
        self.assertIn("duration: 0:30", detail)
        self.assertIn("num_turns: -", detail)
        self.assertIn("session_id: -", detail)

    def test_format_detail_panel_telemetry_history_multi_attempt(self) -> None:
        bead = Bead(bead_id="B0001", title="T", agent_type="developer", description="d", status=BEAD_DONE)
        bead.metadata["telemetry"] = {"cost_usd": 0.50, "duration_ms": 30_000}
        bead.metadata["telemetry_history"] = [
            {"cost_usd": 0.30},
            {"cost_usd": 0.50},
        ]
        detail = format_detail_panel(bead)
        self.assertIn("attempts: 2 (total cost: $0.80)", detail)

    def test_format_detail_panel_telemetry_history_single_attempt_no_summary(self) -> None:
        bead = Bead(bead_id="B0001", title="T", agent_type="developer", description="d", status=BEAD_DONE)
        bead.metadata["telemetry"] = {"cost_usd": 0.50, "duration_ms": 30_000}
        bead.metadata["telemetry_history"] = [{"cost_usd": 0.50}]
        detail = format_detail_panel(bead)
        self.assertNotIn("attempts:", detail)

    def test_format_detail_panel_telemetry_history_with_none_costs(self) -> None:
        bead = Bead(bead_id="B0001", title="T", agent_type="developer", description="d", status=BEAD_DONE)
        bead.metadata["telemetry"] = {"cost_usd": 0.20}
        bead.metadata["telemetry_history"] = [
            {"cost_usd": None},
            {"cost_usd": 0.20},
            {},
        ]
        detail = format_detail_panel(bead)
        self.assertIn("attempts: 3 (total cost: $0.20)", detail)

    # -- _detail_section_body telemetry section ------------------------------

    def test_detail_section_body_telemetry_no_data(self) -> None:
        bead = Bead(bead_id="B0001", title="T", agent_type="developer", description="d")
        body = _detail_section_body(bead, DETAIL_SECTION_TELEMETRY)
        self.assertEqual("No telemetry data.", body)

    def test_detail_section_body_telemetry_with_data(self) -> None:
        bead = Bead(bead_id="B0001", title="T", agent_type="developer", description="d")
        bead.metadata["telemetry"] = {
            "cost_usd": 2.50,
            "duration_api_ms": 200_000,
            "num_turns": 12,
            "input_tokens": 50_000,
            "output_tokens": 8_000,
            "cache_read_tokens": 3_000,
            "prompt_chars": 40_000,
            "session_id": "sess-xyz789",
        }
        body = _detail_section_body(bead, DETAIL_SECTION_TELEMETRY)
        self.assertIn("cost_usd: $2.50", body)
        self.assertIn("duration: 3:20", body)
        self.assertIn("num_turns: 12", body)
        self.assertIn("input_tokens: 50000", body)
        self.assertIn("output_tokens: 8000", body)
        self.assertIn("cache_read_tokens: 3000", body)
        self.assertIn("prompt_chars: 40000", body)
        self.assertIn("session_id: sess-xyz789", body)

    def test_detail_section_body_telemetry_history(self) -> None:
        bead = Bead(bead_id="B0001", title="T", agent_type="developer", description="d")
        bead.metadata["telemetry"] = {"cost_usd": 1.00, "duration_ms": 60_000}
        bead.metadata["telemetry_history"] = [
            {"cost_usd": 0.75},
            {"cost_usd": 1.00},
        ]
        body = _detail_section_body(bead, DETAIL_SECTION_TELEMETRY)
        self.assertIn("attempts: 2 (total cost: $1.75)", body)

    def test_detail_section_body_none_bead(self) -> None:
        body = _detail_section_body(None, DETAIL_SECTION_TELEMETRY)
        self.assertEqual("-", body)

    # -- _detail_section_title -----------------------------------------------

    def test_detail_section_title_telemetry(self) -> None:
        self.assertEqual("Telemetry", _detail_section_title(DETAIL_SECTION_TELEMETRY))

    # -- DETAIL_SECTION_ORDER includes telemetry -----------------------------

    def test_detail_section_order_includes_telemetry(self) -> None:
        from agent_takt.tui import DETAIL_SECTION_ORDER
        self.assertIn(DETAIL_SECTION_TELEMETRY, DETAIL_SECTION_ORDER)
        self.assertIn(DETAIL_SECTION_HISTORY, DETAIL_SECTION_ORDER)
        self.assertEqual(DETAIL_SECTION_HISTORY, DETAIL_SECTION_ORDER[-1])

class TuiTitleTruncationTests(unittest.TestCase):
    """Tests for B0133: Truncate bead titles to single line in list panel."""

    # -- _truncate_title unit tests -------------------------------------------

    def test_truncate_title_short_title_unchanged(self) -> None:
        from agent_takt.tui import _truncate_title
        self.assertEqual("Hello", _truncate_title("Hello", 10))

    def test_truncate_title_exact_fit_unchanged(self) -> None:
        from agent_takt.tui import _truncate_title
        self.assertEqual("Hello", _truncate_title("Hello", 5))

    def test_truncate_title_long_title_gets_ellipsis(self) -> None:
        from agent_takt.tui import _truncate_title
        result = _truncate_title("Hello World", 8)
        self.assertEqual("Hello...", result)
        self.assertEqual(8, len(result))

    def test_truncate_title_max_width_3_returns_ellipsis(self) -> None:
        from agent_takt.tui import _truncate_title
        self.assertEqual("...", _truncate_title("Hello World", 3))

    def test_truncate_title_max_width_2_returns_partial_ellipsis(self) -> None:
        from agent_takt.tui import _truncate_title
        self.assertEqual("..", _truncate_title("Hello World", 2))

    def test_truncate_title_max_width_1_returns_single_dot(self) -> None:
        from agent_takt.tui import _truncate_title
        self.assertEqual(".", _truncate_title("Hello World", 1))

    def test_truncate_title_max_width_0_returns_empty(self) -> None:
        from agent_takt.tui import _truncate_title
        self.assertEqual("", _truncate_title("Hello World", 0))

    def test_truncate_title_max_width_4_keeps_one_char_plus_ellipsis(self) -> None:
        from agent_takt.tui import _truncate_title
        self.assertEqual("H...", _truncate_title("Hello World", 4))

    def test_truncate_title_empty_title(self) -> None:
        from agent_takt.tui import _truncate_title
        self.assertEqual("", _truncate_title("", 10))

    # -- render_tree_panel truncation integration -----------------------------

    def test_render_tree_panel_truncates_long_title(self) -> None:
        long_title = "A" * 200
        bead = Bead(bead_id="B0001", title=long_title, agent_type="developer", description="d", status=BEAD_READY)
        rows = build_tree_rows([bead])
        output = render_tree_panel(rows, selected_index=0, focused=True, panel_width=60)
        lines = output.splitlines()
        self.assertEqual(1, len(lines))
        self.assertIn("...", lines[0])
        # Line should not exceed panel_width
        self.assertLessEqual(len(lines[0]), 60)

    def test_render_tree_panel_short_title_not_truncated(self) -> None:
        bead = Bead(bead_id="B0001", title="Short", agent_type="developer", description="d", status=BEAD_READY)
        rows = build_tree_rows([bead])
        output = render_tree_panel(rows, selected_index=0, focused=True, panel_width=120)
        self.assertIn("Short", output)
        self.assertNotIn("...", output)

    def test_render_tree_panel_respects_panel_width_parameter(self) -> None:
        title = "Medium length title for testing"
        bead = Bead(bead_id="B0001", title=title, agent_type="developer", description="d", status=BEAD_READY)
        rows = build_tree_rows([bead])
        # With a wide panel, title should fit
        wide_output = render_tree_panel(rows, selected_index=0, focused=True, panel_width=200)
        self.assertIn(title, wide_output)
        # With a narrow panel, title should be truncated
        narrow_output = render_tree_panel(rows, selected_index=0, focused=True, panel_width=40)
        self.assertNotIn(title, narrow_output)
        self.assertIn("...", narrow_output)

    def test_render_tree_panel_default_width_used_when_none(self) -> None:
        from agent_takt.tui import _DEFAULT_PANEL_WIDTH
        long_title = "X" * 200
        bead = Bead(bead_id="B0001", title=long_title, agent_type="developer", description="d", status=BEAD_READY)
        rows = build_tree_rows([bead])
        output = render_tree_panel(rows, selected_index=0, focused=True)
        lines = output.splitlines()
        self.assertLessEqual(len(lines[0]), _DEFAULT_PANEL_WIDTH)

    def test_render_tree_panel_nested_bead_title_truncation(self) -> None:
        parent = Bead(bead_id="B0001", title="Parent", agent_type="planner", description="p", status=BEAD_READY)
        child = Bead(
            bead_id="B0001-dev",
            title="C" * 200,
            agent_type="developer",
            description="d",
            status=BEAD_READY,
            parent_id="B0001",
        )
        rows = build_tree_rows([parent, child])
        output = render_tree_panel(rows, selected_index=0, focused=True, panel_width=60)
        for line in output.splitlines():
            self.assertLessEqual(len(line), 60, f"Line exceeds panel_width: {line!r}")

    def test_render_tree_panel_truncation_with_telemetry_badge(self) -> None:
        long_title = "T" * 200
        bead = Bead(bead_id="B0001", title=long_title, agent_type="developer", description="d", status=BEAD_READY)
        bead.metadata["telemetry"] = {"cost_usd": 0.42, "duration_ms": 130_000}
        rows = build_tree_rows([bead])
        output = render_tree_panel(rows, selected_index=0, focused=True, panel_width=80)
        lines = output.splitlines()
        self.assertEqual(1, len(lines))
        # Badge should still be present
        self.assertIn("[$0.42, 2:10]", lines[0])
        # Title should be truncated
        self.assertIn("...", lines[0])
        # Line should respect width
        self.assertLessEqual(len(lines[0]), 80)

class TuiExecutionHistoryTruncationTests(unittest.TestCase):
    """Tests for B-d793bd9f: Truncate execution_history display in TUI detail panel."""

    def _make_record(self, i: int) -> ExecutionRecord:
        return ExecutionRecord(
            timestamp=f"2026-01-01T00:00:{i:02d}+00:00",
            event="started",
            agent_type="developer",
            summary=f"Event {i}",
        )

    def _make_bead(self, num_records: int) -> Bead:
        bead = Bead(bead_id="B0001", title="T", agent_type="developer", description="d")
        bead.execution_history = [self._make_record(i) for i in range(num_records)]
        return bead

    # -- EXECUTION_HISTORY_DISPLAY_LIMIT constant -----------------------------

    def test_display_limit_is_five(self) -> None:
        self.assertEqual(5, EXECUTION_HISTORY_DISPLAY_LIMIT)

    # -- _detail_section_body DETAIL_SECTION_HISTORY --------------------------

    def test_detail_section_history_no_records(self) -> None:
        bead = self._make_bead(0)
        result = _detail_section_body(bead, DETAIL_SECTION_HISTORY)
        self.assertEqual("No execution history.", result)

    def test_detail_section_history_fewer_than_limit(self) -> None:
        bead = self._make_bead(3)
        result = _detail_section_body(bead, DETAIL_SECTION_HISTORY)
        self.assertNotIn("omitted", result)
        self.assertIn("Event 0", result)
        self.assertIn("Event 2", result)

    def test_detail_section_history_exactly_at_limit(self) -> None:
        bead = self._make_bead(EXECUTION_HISTORY_DISPLAY_LIMIT)
        result = _detail_section_body(bead, DETAIL_SECTION_HISTORY)
        self.assertNotIn("omitted", result)
        self.assertIn("Event 0", result)
        self.assertIn(f"Event {EXECUTION_HISTORY_DISPLAY_LIMIT - 1}", result)

    def test_detail_section_history_above_limit_shows_omitted_count(self) -> None:
        total = EXECUTION_HISTORY_DISPLAY_LIMIT + 3
        bead = self._make_bead(total)
        result = _detail_section_body(bead, DETAIL_SECTION_HISTORY)
        self.assertIn("3 earlier entries omitted", result)

    def test_detail_section_history_above_limit_shows_only_last_entries(self) -> None:
        total = EXECUTION_HISTORY_DISPLAY_LIMIT + 2
        bead = self._make_bead(total)
        result = _detail_section_body(bead, DETAIL_SECTION_HISTORY)
        # First two events should be omitted
        self.assertNotIn("Event 0", result)
        self.assertNotIn("Event 1", result)
        # Last EXECUTION_HISTORY_DISPLAY_LIMIT events should appear
        for i in range(2, total):
            self.assertIn(f"Event {i}", result)

    def test_detail_section_history_none_bead_returns_dash(self) -> None:
        result = _detail_section_body(None, DETAIL_SECTION_HISTORY)
        self.assertEqual("-", result)

    # -- format_detail_panel execution history --------------------------------

    def test_format_detail_panel_no_history_omits_section(self) -> None:
        bead = self._make_bead(0)
        result = format_detail_panel(bead)
        self.assertNotIn("Execution History:", result)

    def test_format_detail_panel_with_history_includes_header(self) -> None:
        bead = self._make_bead(2)
        result = format_detail_panel(bead)
        self.assertIn("Execution History:", result)

    def test_format_detail_panel_truncates_long_history(self) -> None:
        total = EXECUTION_HISTORY_DISPLAY_LIMIT + 4
        bead = self._make_bead(total)
        result = format_detail_panel(bead)
        self.assertIn("4 earlier entries omitted", result)
        # Early events should not appear
        self.assertNotIn("Event 0", result)
        # Last events should appear
        self.assertIn(f"Event {total - 1}", result)

    def test_format_detail_panel_exactly_at_limit_no_omission(self) -> None:
        bead = self._make_bead(EXECUTION_HISTORY_DISPLAY_LIMIT)
        result = format_detail_panel(bead)
        self.assertNotIn("omitted", result)
        self.assertIn("Event 0", result)

class TuiMarkupRenderingTests(unittest.TestCase):
    """Tests for B0138: Fix Rich markup rendering in scheduler log widget."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        source_templates = REPO_ROOT / "templates" / "agents"
        target_templates = self.root / "templates" / "agents"
        target_templates.mkdir(parents=True, exist_ok=True)
        for template_path in source_templates.glob("*.md"):
            shutil.copy2(template_path, target_templates / template_path.name)
        self.storage = RepositoryStorage(self.root)
        self.storage.initialize()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_on_mount_writes_text_object_to_scheduler_log(self) -> None:
        """on_mount should write a Text object (not raw markup string) to the scheduler log."""
        from rich.text import Text as RichText
        self.storage.create_bead(
            bead_id="B0001", title="Dev", agent_type="developer",
            description="d", status=BEAD_READY,
        )
        app = build_tui_app(self.storage, refresh_seconds=60)
        captured_writes: list = []

        async def exercise_app() -> None:
            async with app.run_test() as pilot:
                await pilot.resize_terminal(120, 30)
                await pilot.pause()
                from textual.widgets import RichLog
                log_widget = app.query_one("#scheduler-log", RichLog)
                original_write = log_widget.write
                # Inspect the rendered lines - Strip objects contain Segments
                for strip in log_widget.lines:
                    captured_writes.append(strip)

        asyncio.run(exercise_app())
        # on_mount should have written at least the initial hint message
        self.assertGreaterEqual(len(captured_writes), 1)
        # The Strip should contain segments with proper dim styling, not raw markup
        first_strip = captured_writes[0]
        plain_text = "".join(seg.text for seg in first_strip)
        self.assertIn("Press s to run a scheduler cycle", plain_text)
        # The raw markup tags should NOT appear in the rendered text
        self.assertNotIn("[dim]", plain_text)
        self.assertNotIn("[/dim]", plain_text)
        # Verify dim style was applied to at least one segment
        has_dim = any(seg.style and seg.style.dim for seg in first_strip if seg.style)
        self.assertTrue(has_dim, "Expected dim styling to be applied to the initial log message")

    def test_append_log_line_converts_markup_to_text_object(self) -> None:
        """_append_log_line should pass a Text object (not raw string) to RichLog.write."""
        from rich.text import Text as RichText
        self.storage.create_bead(
            bead_id="B0001", title="Dev", agent_type="developer",
            description="d", status=BEAD_READY,
        )
        app = build_tui_app(self.storage, refresh_seconds=60)
        captured_args: list = []

        async def exercise_app() -> None:
            async with app.run_test() as pilot:
                await pilot.resize_terminal(120, 30)
                await pilot.pause()
                from textual.widgets import RichLog
                log_widget = app.query_one("#scheduler-log", RichLog)
                original_write = log_widget.write
                def capturing_write(data, *args, **kwargs):
                    captured_args.append(data)
                    return original_write(data, *args, **kwargs)
                log_widget.write = capturing_write
                app._append_log_line("[bold]Test message[/bold]")
                await pilot.pause()

        asyncio.run(exercise_app())
        # _append_log_line should have written exactly one item
        self.assertEqual(1, len(captured_args), f"Expected 1 write call, got {len(captured_args)}")
        written = captured_args[0]
        # It should be a Text object, not a raw string
        self.assertIsInstance(written, RichText, f"Expected Text object, got {type(written)}: {written!r}")
        self.assertNotIn("[bold]", written.plain)
        self.assertIn("Test message", written.plain)

    def test_append_log_line_preserves_markup_styling(self) -> None:
        """_append_log_line should preserve Rich styling when converting markup."""
        from rich.text import Text as RichText
        self.storage.create_bead(
            bead_id="B0001", title="Dev", agent_type="developer",
            description="d", status=BEAD_READY,
        )
        app = build_tui_app(self.storage, refresh_seconds=60)
        captured_args: list = []

        async def exercise_app() -> None:
            async with app.run_test() as pilot:
                await pilot.resize_terminal(120, 30)
                await pilot.pause()
                from textual.widgets import RichLog
                log_widget = app.query_one("#scheduler-log", RichLog)
                original_write = log_widget.write
                def capturing_write(data, *args, **kwargs):
                    captured_args.append(data)
                    return original_write(data, *args, **kwargs)
                log_widget.write = capturing_write
                app._append_log_line("[dim]dimmed text[/dim]")
                await pilot.pause()

        asyncio.run(exercise_app())
        self.assertEqual(1, len(captured_args))
        written = captured_args[0]
        self.assertIsInstance(written, RichText)
        # Raw markup tags should not appear in plain text
        self.assertNotIn("[dim]", written.plain)
        self.assertIn("dimmed text", written.plain)
        # Verify the Text object carries dim style spans
        has_dim_style = any(
            span.style and "dim" in str(span.style)
            for span in written._spans
        )
        self.assertTrue(has_dim_style, "Expected dim style span in the Text object")

class TuiSubtreeTelemetryTests(unittest.TestCase):
    """Tests for B0152: Show aggregated telemetry for parent beads including children."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        source_templates = REPO_ROOT / "templates" / "agents"
        target_templates = self.root / "templates" / "agents"
        target_templates.mkdir(parents=True, exist_ok=True)
        for template_path in source_templates.glob("*.md"):
            shutil.copy2(template_path, target_templates / template_path.name)
        self.storage = RepositoryStorage(self.root)
        self.storage.initialize()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    # -- _compute_subtree_telemetry unit tests ---------------------------------

    def test_compute_subtree_telemetry_no_children_returns_none(self) -> None:
        from agent_takt.tui import _compute_subtree_telemetry
        bead = Bead(bead_id="B0001", title="Root", agent_type="developer", description="d")
        result = _compute_subtree_telemetry("B0001", [bead])
        self.assertIsNone(result)

    def test_compute_subtree_telemetry_single_child_aggregates(self) -> None:
        from agent_takt.tui import _compute_subtree_telemetry
        parent = Bead(bead_id="B0001", title="Root", agent_type="developer", description="d")
        child = Bead(bead_id="B0001-test", title="Test", agent_type="tester", description="t", parent_id="B0001")
        child.metadata["telemetry"] = {"cost_usd": 0.50, "duration_ms": 60_000, "input_tokens": 1000, "output_tokens": 200}
        result = _compute_subtree_telemetry("B0001", [parent, child])
        self.assertIsNotNone(result)
        self.assertAlmostEqual(0.50, result["cost_usd"])
        self.assertEqual(60_000, result["duration_ms"])
        self.assertEqual(1000, result["input_tokens"])
        self.assertEqual(200, result["output_tokens"])
        self.assertEqual(1, result["bead_count"])

    def test_compute_subtree_telemetry_multiple_children_sums_costs(self) -> None:
        from agent_takt.tui import _compute_subtree_telemetry
        parent = Bead(bead_id="B0001", title="Root", agent_type="developer", description="d")
        child1 = Bead(bead_id="B0001-test", title="Test", agent_type="tester", description="t", parent_id="B0001")
        child1.metadata["telemetry"] = {"cost_usd": 0.30, "duration_ms": 30_000}
        child2 = Bead(bead_id="B0001-docs", title="Docs", agent_type="documentation", description="d", parent_id="B0001")
        child2.metadata["telemetry"] = {"cost_usd": 0.20, "duration_ms": 20_000}
        result = _compute_subtree_telemetry("B0001", [parent, child1, child2])
        self.assertIsNotNone(result)
        self.assertAlmostEqual(0.50, result["cost_usd"])
        self.assertEqual(50_000, result["duration_ms"])
        self.assertEqual(2, result["bead_count"])

    def test_compute_subtree_telemetry_child_without_telemetry_counted_in_bead_count(self) -> None:
        from agent_takt.tui import _compute_subtree_telemetry
        parent = Bead(bead_id="B0001", title="Root", agent_type="developer", description="d")
        child = Bead(bead_id="B0001-test", title="Test", agent_type="tester", description="t", parent_id="B0001")
        # child has no telemetry
        result = _compute_subtree_telemetry("B0001", [parent, child])
        self.assertIsNotNone(result)
        self.assertAlmostEqual(0.0, result["cost_usd"])
        self.assertEqual(1, result["bead_count"])

    def test_compute_subtree_telemetry_grandchildren_included(self) -> None:
        from agent_takt.tui import _compute_subtree_telemetry
        grandparent = Bead(bead_id="B0001", title="Root", agent_type="developer", description="d")
        parent = Bead(bead_id="B0001-test", title="Test", agent_type="tester", description="t", parent_id="B0001")
        parent.metadata["telemetry"] = {"cost_usd": 0.30, "duration_ms": 30_000}
        grandchild = Bead(bead_id="B0001-test-fix", title="Fix", agent_type="developer", description="f", parent_id="B0001-test")
        grandchild.metadata["telemetry"] = {"cost_usd": 0.10, "duration_ms": 10_000}
        result = _compute_subtree_telemetry("B0001", [grandparent, parent, grandchild])
        self.assertIsNotNone(result)
        self.assertAlmostEqual(0.40, result["cost_usd"])
        self.assertEqual(2, result["bead_count"])

    def test_compute_subtree_telemetry_uses_duration_api_ms_fallback(self) -> None:
        from agent_takt.tui import _compute_subtree_telemetry
        parent = Bead(bead_id="B0001", title="Root", agent_type="developer", description="d")
        child = Bead(bead_id="B0001-test", title="Test", agent_type="tester", description="t", parent_id="B0001")
        child.metadata["telemetry"] = {"cost_usd": 0.10, "duration_api_ms": 45_000}
        result = _compute_subtree_telemetry("B0001", [parent, child])
        self.assertIsNotNone(result)
        self.assertEqual(45_000, result["duration_ms"])

    def test_compute_subtree_telemetry_none_cost_treated_as_zero(self) -> None:
        from agent_takt.tui import _compute_subtree_telemetry
        parent = Bead(bead_id="B0001", title="Root", agent_type="developer", description="d")
        child = Bead(bead_id="B0001-test", title="Test", agent_type="tester", description="t", parent_id="B0001")
        child.metadata["telemetry"] = {"cost_usd": None, "duration_ms": 10_000}
        result = _compute_subtree_telemetry("B0001", [parent, child])
        self.assertIsNotNone(result)
        self.assertAlmostEqual(0.0, result["cost_usd"])

    # -- _telemetry_badge with subtree_telemetry ------------------------------

    def test_telemetry_badge_subtree_shows_own_and_subtree_cost(self) -> None:
        bead = Bead(bead_id="B0001", title="T", agent_type="developer", description="d")
        bead.metadata["telemetry"] = {"cost_usd": 0.32}
        subtree = {"cost_usd": 1.85, "duration_ms": 60_000, "bead_count": 3}
        result = _telemetry_badge(bead, subtree_telemetry=subtree)
        self.assertEqual(" [$0.32 / $1.85]", result)

    def test_telemetry_badge_subtree_no_own_cost_shows_dash(self) -> None:
        bead = Bead(bead_id="B0001", title="T", agent_type="developer", description="d")
        subtree = {"cost_usd": 1.00, "duration_ms": 0, "bead_count": 2}
        result = _telemetry_badge(bead, subtree_telemetry=subtree)
        self.assertEqual(" [- / $1.00]", result)

    def test_telemetry_badge_subtree_no_costs_returns_empty(self) -> None:
        bead = Bead(bead_id="B0001", title="T", agent_type="developer", description="d")
        subtree = {"cost_usd": None, "duration_ms": 0, "bead_count": 1}
        result = _telemetry_badge(bead, subtree_telemetry=subtree)
        self.assertEqual("", result)

    def test_telemetry_badge_subtree_zero_cost_shows_formatted(self) -> None:
        bead = Bead(bead_id="B0001", title="T", agent_type="developer", description="d")
        bead.metadata["telemetry"] = {"cost_usd": 0.0}
        subtree = {"cost_usd": 0.0, "duration_ms": 0, "bead_count": 1}
        result = _telemetry_badge(bead, subtree_telemetry=subtree)
        self.assertEqual(" [$0.00 / $0.00]", result)

    # -- format_detail_panel with subtree_telemetry ---------------------------

    def test_format_detail_panel_subtree_telemetry_shown_in_telemetry_section(self) -> None:
        bead = Bead(bead_id="B0001", title="T", agent_type="developer", description="d", status=BEAD_DONE)
        bead.metadata["telemetry"] = {"cost_usd": 0.50, "duration_ms": 30_000}
        subtree = {"cost_usd": 1.20, "duration_ms": 90_000, "bead_count": 2}
        detail = format_detail_panel(bead, subtree_telemetry=subtree)
        self.assertIn("Subtree:", detail)
        self.assertIn("$1.20 total", detail)
        self.assertIn("2 beads", detail)

    def test_format_detail_panel_no_subtree_telemetry_omits_subtree_line(self) -> None:
        bead = Bead(bead_id="B0001", title="T", agent_type="developer", description="d", status=BEAD_DONE)
        bead.metadata["telemetry"] = {"cost_usd": 0.50, "duration_ms": 30_000}
        detail = format_detail_panel(bead)
        self.assertNotIn("Subtree:", detail)

    # -- _detail_section_body with subtree_telemetry --------------------------

    def test_detail_section_body_subtree_telemetry_shown(self) -> None:
        bead = Bead(bead_id="B0001", title="T", agent_type="developer", description="d")
        bead.metadata["telemetry"] = {"cost_usd": 0.50, "duration_ms": 30_000}
        subtree = {"cost_usd": 1.00, "duration_ms": 60_000, "bead_count": 3}
        body = _detail_section_body(bead, DETAIL_SECTION_TELEMETRY, subtree_telemetry=subtree)
        self.assertIn("Subtree:", body)
        self.assertIn("$1.00 total", body)
        self.assertIn("3 beads", body)

    def test_detail_section_body_no_subtree_telemetry_omits_subtree_line(self) -> None:
        bead = Bead(bead_id="B0001", title="T", agent_type="developer", description="d")
        bead.metadata["telemetry"] = {"cost_usd": 0.50, "duration_ms": 30_000}
        body = _detail_section_body(bead, DETAIL_SECTION_TELEMETRY)
        self.assertNotIn("Subtree:", body)

    # -- TuiRuntimeState subtree_telemetry_for and _subtree_cache -------------


if __name__ == '__main__':
    unittest.main()
