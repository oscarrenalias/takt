"""Tests for console.py SpinnerPool and cli.py multi-worker CliSchedulerReporter."""
from __future__ import annotations

import io
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_takt.console import (
    ConsoleReporter,
    Spinner,
    SpinnerPool,
    GREEN,
    YELLOW,
    RED,
    CYAN,
    RESET,
)
from agent_takt.cli import CliSchedulerReporter
from agent_takt.models import Bead


def _make_bead(bead_id: str = "B0001", agent_type: str = "developer",
               title: str = "Test bead") -> Bead:
    return Bead(bead_id=bead_id, title=title, agent_type=agent_type,
                description="test")


class FakeTTYStream(io.StringIO):
    """StringIO that reports isatty() = True."""
    def isatty(self) -> bool:
        return True


class FakeNonTTYStream(io.StringIO):
    """StringIO that reports isatty() = False."""
    def isatty(self) -> bool:
        return False


# ---------------------------------------------------------------------------
# SpinnerPool tests
# ---------------------------------------------------------------------------

class TestSpinnerPoolNonTTY(unittest.TestCase):
    """SpinnerPool in non-TTY mode should fall back to line output."""

    def setUp(self) -> None:
        self.stream = FakeNonTTYStream()
        self.console = ConsoleReporter(stream=self.stream)

    def test_start_does_not_reserve_lines(self) -> None:
        pool = SpinnerPool(self.console, max_workers=3)
        pool.start()
        # No blank lines should be written, no render thread started
        self.assertFalse(pool._region_started)
        self.assertIsNone(pool._thread)
        pool.stop()

    def test_add_emits_info_line(self) -> None:
        pool = SpinnerPool(self.console, max_workers=2)
        pool.start()
        pool.add("B0001", "developer B0001 · my task")
        output = self.stream.getvalue()
        self.assertIn("developer B0001 · my task", output)
        pool.stop()

    def test_finish_emits_status_line(self) -> None:
        pool = SpinnerPool(self.console, max_workers=2)
        pool.start()
        pool.add("B0001", "developer B0001 · my task")
        pool.finish("B0001", "✓", GREEN, "B0001 completed")
        output = self.stream.getvalue()
        self.assertIn("B0001 completed", output)
        self.assertIn("✓", output)
        pool.stop()

    def test_duplicate_add_is_noop(self) -> None:
        pool = SpinnerPool(self.console, max_workers=2)
        pool.start()
        pool.add("B0001", "first label")
        pool.add("B0001", "second label")
        # Key should still map to original slot
        self.assertEqual(len(pool._key_to_slot), 1)
        pool.stop()

    def test_finish_unknown_key_is_noop(self) -> None:
        pool = SpinnerPool(self.console, max_workers=2)
        pool.start()
        # Should not raise
        pool.finish("UNKNOWN", "✓", GREEN, "message")
        pool.stop()


class TestSpinnerPoolTTY(unittest.TestCase):
    """SpinnerPool in TTY mode should use ANSI cursor positioning."""

    def setUp(self) -> None:
        self.stream = FakeTTYStream()
        self.console = ConsoleReporter(stream=self.stream)

    def test_start_reserves_lines_and_starts_thread(self) -> None:
        pool = SpinnerPool(self.console, max_workers=3)
        pool.start()
        self.assertTrue(pool._region_started)
        self.assertIsNotNone(pool._thread)
        self.assertTrue(pool._thread.is_alive())
        pool.stop()
        # Thread should be stopped
        self.assertIsNone(pool._thread)

    def test_start_writes_blank_lines(self) -> None:
        pool = SpinnerPool(self.console, max_workers=3)
        pool.start()
        output = self.stream.getvalue()
        # Should have 3 newlines from reserving blank lines
        self.assertEqual(output.count("\n"), 3)
        pool.stop()

    def test_add_assigns_slots_sequentially(self) -> None:
        pool = SpinnerPool(self.console, max_workers=3)
        pool.start()
        pool.add("A", "label A")
        pool.add("B", "label B")
        pool.add("C", "label C")
        self.assertEqual(pool._key_to_slot["A"], 0)
        self.assertEqual(pool._key_to_slot["B"], 1)
        self.assertEqual(pool._key_to_slot["C"], 2)
        pool.stop()

    def test_finish_frees_slot_for_reuse(self) -> None:
        pool = SpinnerPool(self.console, max_workers=2)
        pool.start()
        pool.add("A", "label A")
        pool.add("B", "label B")
        pool.finish("A", "✓", GREEN, "A done")
        # Slot 0 should now be free
        self.assertNotIn(0, pool._slots)
        self.assertNotIn("A", pool._key_to_slot)
        # New item should get slot 0
        pool.add("C", "label C")
        self.assertEqual(pool._key_to_slot["C"], 0)
        pool.stop()

    def test_add_overflows_to_slot_zero(self) -> None:
        pool = SpinnerPool(self.console, max_workers=2)
        pool.start()
        pool.add("A", "label A")
        pool.add("B", "label B")
        # All slots full, should overwrite slot 0
        pool.add("C", "label C")
        self.assertEqual(pool._key_to_slot["C"], 0)
        pool.stop()

    def test_render_loop_writes_ansi(self) -> None:
        pool = SpinnerPool(self.console, max_workers=2)
        pool.start()
        pool.add("A", "label A")
        # Let the render loop run at least one cycle
        time.sleep(0.25)
        pool.stop()
        output = self.stream.getvalue()
        # Should contain ANSI cursor up/down sequences
        self.assertIn("\033[", output)

    def test_finish_writes_ansi_to_slot(self) -> None:
        pool = SpinnerPool(self.console, max_workers=2)
        pool.start()
        pool.add("A", "label A")
        pool.finish("A", "✓", GREEN, "A done")
        output = self.stream.getvalue()
        # Should contain the final status text
        self.assertIn("A done", output)
        self.assertIn("✓", output)
        pool.stop()

    def test_stop_is_idempotent(self) -> None:
        pool = SpinnerPool(self.console, max_workers=2)
        pool.start()
        pool.stop()
        # Second stop should not raise
        pool.stop()

    def test_write_slot_positions_cursor(self) -> None:
        """_write_slot moves up, clears, writes text, moves back down."""
        pool = SpinnerPool(self.console, max_workers=3)
        pool._region_started = True
        # Directly call _write_slot for slot 1 in a 3-worker pool
        pool._write_slot(1, "hello")
        output = self.stream.getvalue()
        # lines_up = 3 - 1 = 2
        self.assertIn("\033[2A", output)  # move up 2
        self.assertIn("\r\033[2K", output)  # clear line
        self.assertIn("hello", output)
        self.assertIn("\033[2B", output)  # move down 2
        self.assertIn("\r", output)


class TestSpinnerPoolThreadSafety(unittest.TestCase):
    """Concurrent add/finish calls should not raise."""

    def test_concurrent_add_finish(self) -> None:
        stream = FakeNonTTYStream()
        console = ConsoleReporter(stream=stream)
        # Use enough slots so no overflow occurs during concurrent access
        pool = SpinnerPool(console, max_workers=8)
        pool.start()

        errors = []

        def worker(i: int) -> None:
            try:
                key = f"B{i:04d}"
                pool.add(key, f"task {i}")
                time.sleep(0.01)
                pool.finish(key, "✓", GREEN, f"{key} done")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        pool.stop()
        self.assertEqual(errors, [])


# ---------------------------------------------------------------------------
# Spinner thread-safety tests
# ---------------------------------------------------------------------------

class TestSpinnerThreadSafety(unittest.TestCase):
    """Spinner uses ConsoleReporter._lock for thread-safe output."""

    def test_spinner_acquires_lock_during_render(self) -> None:
        stream = FakeTTYStream()
        console = ConsoleReporter(stream=stream)
        spinner = Spinner(console, "test label")
        spinner.__enter__()
        # Let it animate briefly
        time.sleep(0.15)
        # The lock should exist and be unlocked (not held permanently)
        self.assertFalse(console._lock.locked())
        spinner.success("done")

    def test_spinner_finish_emits_final_message(self) -> None:
        stream = FakeTTYStream()
        console = ConsoleReporter(stream=stream)
        spinner = Spinner(console, "test label")
        spinner.__enter__()
        time.sleep(0.15)
        # _finish writes clear-line to sys.stdout (not console.stream),
        # then emits final message via console.emit -> stream
        spinner.success("finished")
        output = stream.getvalue()
        self.assertIn("finished", output)


# ---------------------------------------------------------------------------
# CliSchedulerReporter tests
# ---------------------------------------------------------------------------

class TestCliSchedulerReporterSingleWorker(unittest.TestCase):
    """Default single-worker mode uses Spinner, not SpinnerPool."""

    def test_no_pool_created(self) -> None:
        stream = FakeNonTTYStream()
        console = ConsoleReporter(stream=stream)
        reporter = CliSchedulerReporter(console, max_workers=1)
        self.assertIsNone(reporter._pool)
        reporter.stop()

    def test_bead_started_creates_spinner(self) -> None:
        stream = FakeNonTTYStream()
        console = ConsoleReporter(stream=stream)
        reporter = CliSchedulerReporter(console, max_workers=1)
        bead = _make_bead()
        reporter.bead_started(bead)
        # In non-TTY, spinner emits info line
        output = stream.getvalue()
        self.assertIn("developer B0001", output)
        reporter.stop()

    def test_bead_completed_uses_spinner_success(self) -> None:
        stream = FakeNonTTYStream()
        console = ConsoleReporter(stream=stream)
        reporter = CliSchedulerReporter(console, max_workers=1)
        bead = _make_bead()
        reporter.bead_started(bead)
        reporter.bead_completed(bead, "summary text", [])
        output = stream.getvalue()
        self.assertIn("B0001 completed", output)
        reporter.stop()

    def test_bead_blocked_uses_spinner_warn(self) -> None:
        stream = FakeNonTTYStream()
        console = ConsoleReporter(stream=stream)
        reporter = CliSchedulerReporter(console, max_workers=1)
        bead = _make_bead()
        reporter.bead_started(bead)
        reporter.bead_blocked(bead, "blocked reason")
        output = stream.getvalue()
        self.assertIn("B0001 blocked", output)
        reporter.stop()

    def test_bead_failed_uses_spinner_fail(self) -> None:
        stream = FakeNonTTYStream()
        console = ConsoleReporter(stream=stream)
        reporter = CliSchedulerReporter(console, max_workers=1)
        bead = _make_bead()
        reporter.bead_started(bead)
        reporter.bead_failed(bead, "failed reason")
        output = stream.getvalue()
        self.assertIn("B0001 failed", output)
        reporter.stop()


class TestCliSchedulerReporterMultiWorker(unittest.TestCase):
    """Multi-worker mode uses SpinnerPool."""

    def test_pool_created_for_multi_worker(self) -> None:
        stream = FakeNonTTYStream()
        console = ConsoleReporter(stream=stream)
        reporter = CliSchedulerReporter(console, max_workers=3)
        self.assertIsNotNone(reporter._pool)
        self.assertEqual(reporter._pool.max_workers, 3)
        reporter.stop()

    def test_bead_started_adds_to_pool(self) -> None:
        stream = FakeNonTTYStream()
        console = ConsoleReporter(stream=stream)
        reporter = CliSchedulerReporter(console, max_workers=2)
        bead = _make_bead()
        reporter.bead_started(bead)
        self.assertIn("B0001", reporter._pool._key_to_slot)
        reporter.stop()

    def test_bead_completed_finishes_in_pool(self) -> None:
        stream = FakeNonTTYStream()
        console = ConsoleReporter(stream=stream)
        reporter = CliSchedulerReporter(console, max_workers=2)
        bead = _make_bead()
        reporter.bead_started(bead)
        reporter.bead_completed(bead, "done", [])
        output = stream.getvalue()
        self.assertIn("B0001 completed", output)
        self.assertNotIn("B0001", reporter._pool._key_to_slot)
        reporter.stop()

    def test_bead_blocked_finishes_in_pool(self) -> None:
        stream = FakeNonTTYStream()
        console = ConsoleReporter(stream=stream)
        reporter = CliSchedulerReporter(console, max_workers=2)
        bead = _make_bead()
        reporter.bead_started(bead)
        reporter.bead_blocked(bead, "blocked")
        self.assertNotIn("B0001", reporter._pool._key_to_slot)
        reporter.stop()

    def test_bead_failed_finishes_in_pool(self) -> None:
        stream = FakeNonTTYStream()
        console = ConsoleReporter(stream=stream)
        reporter = CliSchedulerReporter(console, max_workers=2)
        bead = _make_bead()
        reporter.bead_started(bead)
        reporter.bead_failed(bead, "failed")
        self.assertNotIn("B0001", reporter._pool._key_to_slot)
        reporter.stop()

    def test_multiple_beads_tracked_concurrently(self) -> None:
        stream = FakeNonTTYStream()
        console = ConsoleReporter(stream=stream)
        reporter = CliSchedulerReporter(console, max_workers=3)
        beads = [_make_bead(f"B000{i}", title=f"Task {i}") for i in range(1, 4)]
        for b in beads:
            reporter.bead_started(b)
        self.assertEqual(len(reporter._pool._key_to_slot), 3)
        reporter.bead_completed(beads[1], "done", [])
        self.assertEqual(len(reporter._pool._key_to_slot), 2)
        reporter.stop()

    def test_bead_completed_reports_created_children(self) -> None:
        stream = FakeNonTTYStream()
        console = ConsoleReporter(stream=stream)
        reporter = CliSchedulerReporter(console, max_workers=2)
        bead = _make_bead()
        child = _make_bead("B0001-test", agent_type="tester", title="Test B0001")
        reporter.bead_started(bead)
        reporter.bead_completed(bead, "done", [child])
        output = stream.getvalue()
        self.assertIn("created handoff bead B0001-test (tester)", output)
        reporter.stop()

    def test_stop_cleans_up_pool(self) -> None:
        stream = FakeNonTTYStream()
        console = ConsoleReporter(stream=stream)
        reporter = CliSchedulerReporter(console, max_workers=2)
        reporter.stop()
        # Should be safe to call stop again
        reporter.stop()


class TestCliSchedulerReporterLeaseExpired(unittest.TestCase):
    """lease_expired works in both modes."""

    def test_lease_expired_emits_warning(self) -> None:
        stream = FakeNonTTYStream()
        console = ConsoleReporter(stream=stream)
        reporter = CliSchedulerReporter(console, max_workers=1)
        reporter.lease_expired("B0001")
        output = stream.getvalue()
        self.assertIn("Lease expired for B0001", output)
        reporter.stop()


# ---------------------------------------------------------------------------
# ConsoleReporter TTY / non-TTY color behaviour
# ---------------------------------------------------------------------------

class TestConsoleReporterColorBehavior(unittest.TestCase):
    """_c() and high-level methods emit ANSI only when is_tty is True."""

    def test_c_returns_code_on_tty(self) -> None:
        console = ConsoleReporter(stream=FakeTTYStream())
        self.assertEqual(GREEN, console._c(GREEN))

    def test_c_returns_empty_on_non_tty(self) -> None:
        console = ConsoleReporter(stream=FakeNonTTYStream())
        self.assertEqual("", console._c(GREEN))

    def test_section_emits_ansi_on_tty(self) -> None:
        stream = FakeTTYStream()
        console = ConsoleReporter(stream=stream)
        console.section("Hello")
        output = stream.getvalue()
        self.assertIn("\033[", output)
        self.assertIn("Hello", output)

    def test_section_no_ansi_on_non_tty(self) -> None:
        stream = FakeNonTTYStream()
        console = ConsoleReporter(stream=stream)
        console.section("Hello")
        output = stream.getvalue()
        self.assertNotIn("\033[", output)
        self.assertIn("Hello", output)

    def test_info_emits_ansi_on_tty(self) -> None:
        stream = FakeTTYStream()
        console = ConsoleReporter(stream=stream)
        console.info("some info")
        self.assertIn("\033[", stream.getvalue())

    def test_info_no_ansi_on_non_tty(self) -> None:
        stream = FakeNonTTYStream()
        console = ConsoleReporter(stream=stream)
        console.info("some info")
        self.assertNotIn("\033[", stream.getvalue())

    def test_success_emits_ansi_on_tty(self) -> None:
        stream = FakeTTYStream()
        console = ConsoleReporter(stream=stream)
        console.success("ok")
        self.assertIn("\033[", stream.getvalue())

    def test_success_no_ansi_on_non_tty(self) -> None:
        stream = FakeNonTTYStream()
        console = ConsoleReporter(stream=stream)
        console.success("ok")
        self.assertNotIn("\033[", stream.getvalue())

    def test_warn_emits_ansi_on_tty(self) -> None:
        stream = FakeTTYStream()
        console = ConsoleReporter(stream=stream)
        console.warn("careful")
        self.assertIn("\033[", stream.getvalue())

    def test_warn_no_ansi_on_non_tty(self) -> None:
        stream = FakeNonTTYStream()
        console = ConsoleReporter(stream=stream)
        console.warn("careful")
        self.assertNotIn("\033[", stream.getvalue())

    def test_error_emits_ansi_on_tty(self) -> None:
        stream = FakeTTYStream()
        console = ConsoleReporter(stream=stream)
        console.error("bad")
        self.assertIn("\033[", stream.getvalue())

    def test_error_no_ansi_on_non_tty(self) -> None:
        stream = FakeNonTTYStream()
        console = ConsoleReporter(stream=stream)
        console.error("bad")
        self.assertNotIn("\033[", stream.getvalue())

    def test_detail_emits_ansi_on_tty(self) -> None:
        stream = FakeTTYStream()
        console = ConsoleReporter(stream=stream)
        console.detail("extra info")
        self.assertIn("\033[", stream.getvalue())

    def test_detail_no_ansi_on_non_tty(self) -> None:
        stream = FakeNonTTYStream()
        console = ConsoleReporter(stream=stream)
        console.detail("extra info")
        self.assertNotIn("\033[", stream.getvalue())

    def test_scaffold_done_line_no_ansi_on_non_tty(self) -> None:
        """scaffold_project Done. line must contain no ANSI when stream is non-TTY."""
        import io
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from agent_takt.onboarding import scaffold_project, InitAnswers

        answers = InitAnswers(
            runner="claude",
            max_workers=1,
            language="Python",
            test_command="pytest",
            build_check_command="python -m py_compile",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            fake_templates = root / "_tpl"
            fake_templates.mkdir()
            (fake_templates / "developer.md").write_text("x")
            fake_agents = root / "_agents"
            fake_agents.mkdir()
            (fake_agents / "skill.md").write_text("s")
            fake_claude = root / "_claude"
            fake_claude.mkdir()
            (fake_claude / "skill.md").write_text("s")
            fake_config = root / "_cfg.yaml"
            fake_config.write_text("fake: true")

            stream = FakeNonTTYStream()
            with (
                patch("agent_takt.onboarding.config.packaged_templates_dir", return_value=fake_templates),
                patch("agent_takt.onboarding.assets.packaged_agents_skills_dir", return_value=fake_agents),
                patch("agent_takt.onboarding.assets.packaged_claude_skills_dir", return_value=fake_claude),
                patch("agent_takt.onboarding.config.packaged_default_config", return_value=fake_config),
            ):
                scaffold_project(root, answers, stream_out=stream)

        output = stream.getvalue()
        done_line = next(l for l in output.splitlines() if "Done" in l)
        self.assertNotIn("\033[", done_line)


# ---------------------------------------------------------------------------
# command_init section header test
# ---------------------------------------------------------------------------

class TestCommandInitSectionHeader(unittest.TestCase):
    """command_init calls console.section() to write the init header."""

    def test_section_header_written_before_scaffold(self) -> None:
        import tempfile
        from argparse import Namespace
        from pathlib import Path
        from unittest.mock import patch

        output_order: list[str] = []

        class OrderTrackingStream(FakeNonTTYStream):
            def write(self, s: str) -> int:
                output_order.append(s)
                return super().write(s)

        stream = OrderTrackingStream()
        console = ConsoleReporter(stream=stream)

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / ".git").mkdir()
            args = Namespace(root=str(root), non_interactive=True, overwrite=False)

            def fake_scaffold(project_root, answers, **kwargs):
                output_order.append("__scaffold__")

            with (
                patch("shutil.which", return_value="/usr/local/bin/claude"),
                patch("agent_takt.onboarding.scaffold_project", side_effect=fake_scaffold),
            ):
                from agent_takt.cli import command_init
                command_init(args, console)

        full_output = "".join(output_order)
        self.assertIn("takt init", full_output)
        # section header must appear before the scaffold sentinel
        section_pos = full_output.index("takt init")
        scaffold_pos = full_output.index("__scaffold__")
        self.assertLess(section_pos, scaffold_pos)


# ---------------------------------------------------------------------------
# ConsoleReporter lock initialization tests
# ---------------------------------------------------------------------------

class TestConsoleReporterLock(unittest.TestCase):
    def test_lock_initialized_on_creation(self) -> None:
        console = ConsoleReporter(stream=io.StringIO())
        self.assertIsInstance(console._lock, type(threading.Lock()))

    def test_emit_is_threadsafe(self) -> None:
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)
        errors = []

        def emitter(i: int) -> None:
            try:
                for _ in range(20):
                    console.emit(f"msg-{i}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=emitter, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        self.assertEqual(errors, [])
        # All messages should be present
        output = stream.getvalue()
        for i in range(4):
            self.assertIn(f"msg-{i}", output)


# ---------------------------------------------------------------------------
# command_run try/finally reporter.stop() test
# ---------------------------------------------------------------------------

class TestCommandRunCleanup(unittest.TestCase):
    """command_run wraps the scheduler loop in try/finally to call reporter.stop()."""

    def test_reporter_stop_called_on_normal_exit(self) -> None:
        """Verify reporter.stop() is called even during normal flow."""
        from agent_takt.cli import command_run
        from unittest.mock import MagicMock
        from argparse import Namespace

        stream = FakeNonTTYStream()
        console = ConsoleReporter(stream=stream)
        scheduler = MagicMock()
        # Simulate run_once returning empty result
        run_result = MagicMock()
        run_result.started = []
        run_result.completed = []
        run_result.blocked = []
        run_result.deferred = []
        scheduler.run_once.return_value = run_result

        args = Namespace(
            max_workers=2,
            feature_root=None,
            once=True,
        )

        with patch("agent_takt.cli.CliSchedulerReporter") as MockReporter:
            mock_reporter = MagicMock()
            MockReporter.return_value = mock_reporter
            command_run(args, scheduler, console)
            mock_reporter.stop.assert_called_once()

    def test_reporter_stop_called_on_exception(self) -> None:
        """Verify reporter.stop() is called even when scheduler raises."""
        from agent_takt.cli import command_run
        from unittest.mock import MagicMock
        from argparse import Namespace

        stream = FakeNonTTYStream()
        console = ConsoleReporter(stream=stream)
        scheduler = MagicMock()
        scheduler.run_once.side_effect = RuntimeError("boom")

        args = Namespace(
            max_workers=2,
            feature_root=None,
            once=True,
        )

        with patch("agent_takt.cli.CliSchedulerReporter") as MockReporter:
            mock_reporter = MagicMock()
            MockReporter.return_value = mock_reporter
            with self.assertRaises(RuntimeError):
                command_run(args, scheduler, console)
            mock_reporter.stop.assert_called_once()


# ---------------------------------------------------------------------------
# B0149: correctives_created tracking and loop continuation
# ---------------------------------------------------------------------------

class TestCommandRunCorrectivesCreated(unittest.TestCase):
    """Tests for correctives_created aggregate tracking and loop exit logic."""

    def _make_cycle_result(self, started=None, completed=None, blocked=None,
                           deferred=None, correctives_created=None):
        from unittest.mock import MagicMock
        r = MagicMock()
        r.started = started or []
        r.completed = completed or []
        r.blocked = blocked or []
        r.deferred = deferred or []
        r.correctives_created = correctives_created or []
        return r

    def test_correctives_created_accumulated_in_aggregate(self) -> None:
        """correctives_created from each cycle should appear in the final aggregate."""
        from agent_takt.cli import command_run
        from unittest.mock import MagicMock
        from argparse import Namespace

        stream = FakeNonTTYStream()
        console = ConsoleReporter(stream=stream)
        scheduler = MagicMock()
        scheduler.run_once.return_value = self._make_cycle_result(
            started=["B0010"], correctives_created=["B0010-corrective"]
        )

        args = Namespace(max_workers=1, feature_root=None, once=True)

        with patch("agent_takt.cli.CliSchedulerReporter") as MockReporter:
            MockReporter.return_value = MagicMock()
            command_run(args, scheduler, console)

        output = stream.getvalue()
        self.assertIn("B0010-corrective", output)

    def test_loop_continues_when_correctives_created(self) -> None:
        """Loop should continue when correctives were created even if no beads started."""
        from agent_takt.cli import command_run
        from unittest.mock import MagicMock, call
        from argparse import Namespace

        stream = FakeNonTTYStream()
        console = ConsoleReporter(stream=stream)
        scheduler = MagicMock()

        # First cycle: nothing started but a corrective was created -> loop continues
        # Second cycle: nothing started, no correctives -> loop breaks
        cycle1 = self._make_cycle_result(correctives_created=["B0010-corrective"])
        cycle2 = self._make_cycle_result()
        scheduler.run_once.side_effect = [cycle1, cycle2]

        args = Namespace(max_workers=1, feature_root=None, once=False)

        with patch("agent_takt.cli.CliSchedulerReporter") as MockReporter:
            MockReporter.return_value = MagicMock()
            command_run(args, scheduler, console)

        self.assertEqual(scheduler.run_once.call_count, 2)

    def test_loop_breaks_when_no_started_and_no_correctives(self) -> None:
        """Loop should break immediately when nothing started and no correctives created."""
        from agent_takt.cli import command_run
        from unittest.mock import MagicMock
        from argparse import Namespace

        stream = FakeNonTTYStream()
        console = ConsoleReporter(stream=stream)
        scheduler = MagicMock()
        scheduler.run_once.return_value = self._make_cycle_result()

        args = Namespace(max_workers=1, feature_root=None, once=False)

        with patch("agent_takt.cli.CliSchedulerReporter") as MockReporter:
            MockReporter.return_value = MagicMock()
            command_run(args, scheduler, console)

        scheduler.run_once.assert_called_once()

    def test_once_flag_breaks_even_with_correctives(self) -> None:
        """With --once, loop breaks after first cycle regardless of correctives."""
        from agent_takt.cli import command_run
        from unittest.mock import MagicMock
        from argparse import Namespace

        stream = FakeNonTTYStream()
        console = ConsoleReporter(stream=stream)
        scheduler = MagicMock()
        scheduler.run_once.return_value = self._make_cycle_result(
            started=["B0010"], correctives_created=["B0010-corrective"]
        )

        args = Namespace(max_workers=1, feature_root=None, once=True)

        with patch("agent_takt.cli.CliSchedulerReporter") as MockReporter:
            MockReporter.return_value = MagicMock()
            command_run(args, scheduler, console)

        scheduler.run_once.assert_called_once()


if __name__ == "__main__":
    unittest.main()
