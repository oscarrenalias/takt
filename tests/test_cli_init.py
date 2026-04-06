"""Tests for command_init in cli.py and the `init` subparser wiring.

Covers:
- Parser wiring: `init` subcommand registered, --overwrite and --non-interactive flags
- command_init: non-git directory returns exit code 1
- command_init: non-interactive mode uses defaults without prompting
- command_init: missing runner binary returns exit code 1
- command_init: calls scaffold_project on success, returns 0
- command_init: overwrite flag is forwarded to scaffold_project
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from argparse import Namespace
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, call, patch

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from codex_orchestrator.cli import build_parser, command_init
from codex_orchestrator.console import ConsoleReporter


# ---------------------------------------------------------------------------
# Parser wiring
# ---------------------------------------------------------------------------


class TestInitParserWiring(unittest.TestCase):
    def test_init_command_registered(self):
        parser = build_parser()
        args = parser.parse_args(["init"])
        self.assertEqual("init", args.command)

    def test_overwrite_flag_default_false(self):
        parser = build_parser()
        args = parser.parse_args(["init"])
        self.assertFalse(args.overwrite)

    def test_overwrite_flag_set(self):
        parser = build_parser()
        args = parser.parse_args(["init", "--overwrite"])
        self.assertTrue(args.overwrite)

    def test_non_interactive_default_false(self):
        parser = build_parser()
        args = parser.parse_args(["init"])
        self.assertFalse(args.non_interactive)

    def test_non_interactive_flag_set(self):
        parser = build_parser()
        args = parser.parse_args(["init", "--non-interactive"])
        self.assertTrue(args.non_interactive)

    def test_root_flag_accepted(self):
        parser = build_parser()
        args = parser.parse_args(["init", "--root", "/tmp/myproject"])
        self.assertEqual("/tmp/myproject", args.root)


# ---------------------------------------------------------------------------
# command_init behaviour
# ---------------------------------------------------------------------------


class TestCommandInit(unittest.TestCase):
    def _console(self):
        return ConsoleReporter(stream=StringIO())

    def _args(self, root=None, non_interactive=True, overwrite=False):
        ns = Namespace()
        ns.root = root
        ns.non_interactive = non_interactive
        ns.overwrite = overwrite
        return ns

    # -- non-git directory ---------------------------------------------------

    def test_non_git_dir_returns_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            args = self._args(root=tmpdir)
            console = self._console()
            rc = command_init(args, console)
        self.assertEqual(1, rc)

    def test_non_git_dir_prints_error_message(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            stream = StringIO()
            console = ConsoleReporter(stream=stream)
            args = self._args(root=tmpdir)
            command_init(args, console)
        self.assertIn("not a git repository", stream.getvalue().lower())

    # -- missing runner binary -----------------------------------------------

    def test_missing_runner_binary_returns_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / ".git").mkdir()
            args = self._args(root=str(root), non_interactive=True)
            console = self._console()
            with patch("shutil.which", return_value=None):
                rc = command_init(args, console)
        self.assertEqual(1, rc)

    def test_missing_runner_binary_prints_hint(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / ".git").mkdir()
            stream = StringIO()
            console = ConsoleReporter(stream=stream)
            args = self._args(root=str(root), non_interactive=True)
            with patch("shutil.which", return_value=None):
                command_init(args, console)
        self.assertIn("not found in PATH", stream.getvalue())

    # -- successful init (non-interactive) -----------------------------------

    def test_successful_init_returns_zero(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / ".git").mkdir()
            args = self._args(root=str(root), non_interactive=True, overwrite=False)
            console = self._console()
            with (
                patch("shutil.which", return_value="/usr/local/bin/claude"),
                patch("codex_orchestrator.onboarding.scaffold_project") as mock_scaffold,
            ):
                rc = command_init(args, console)
        self.assertEqual(0, rc)
        mock_scaffold.assert_called_once()

    def test_non_interactive_uses_default_answers(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / ".git").mkdir()
            args = self._args(root=str(root), non_interactive=True)
            console = self._console()
            captured_answers = {}

            def capture_scaffold(project_root, answers, **kwargs):
                captured_answers["answers"] = answers

            with (
                patch("shutil.which", return_value="/usr/local/bin/claude"),
                patch("codex_orchestrator.onboarding.scaffold_project", side_effect=capture_scaffold),
            ):
                command_init(args, console)

        a = captured_answers["answers"]
        self.assertEqual("claude", a.runner)
        self.assertEqual(1, a.max_workers)
        self.assertEqual("Python", a.language)
        self.assertEqual("pytest", a.test_command)

    def test_overwrite_flag_forwarded_to_scaffold(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / ".git").mkdir()
            args = self._args(root=str(root), non_interactive=True, overwrite=True)
            console = self._console()
            with (
                patch("shutil.which", return_value="/usr/local/bin/claude"),
                patch("codex_orchestrator.onboarding.scaffold_project") as mock_scaffold,
            ):
                command_init(args, console)

        _, kwargs = mock_scaffold.call_args
        self.assertTrue(kwargs.get("overwrite", False))

    def test_no_overwrite_forwarded_to_scaffold(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / ".git").mkdir()
            args = self._args(root=str(root), non_interactive=True, overwrite=False)
            console = self._console()
            with (
                patch("shutil.which", return_value="/usr/local/bin/claude"),
                patch("codex_orchestrator.onboarding.scaffold_project") as mock_scaffold,
            ):
                command_init(args, console)

        _, kwargs = mock_scaffold.call_args
        self.assertFalse(kwargs.get("overwrite", False))

    # -- interactive mode falls back to collect_init_answers -----------------

    def test_interactive_mode_calls_collect_init_answers(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / ".git").mkdir()
            args = self._args(root=str(root), non_interactive=False)
            console = self._console()

            from codex_orchestrator.onboarding import InitAnswers
            fake_answers = InitAnswers(
                runner="claude",
                max_workers=1,
                language="Python",
                test_command="pytest",
                build_check_command="python -m py_compile",
            )

            with (
                patch("codex_orchestrator.onboarding.collect_init_answers", return_value=fake_answers) as mock_collect,
                patch("shutil.which", return_value="/usr/local/bin/claude"),
                patch("codex_orchestrator.onboarding.scaffold_project"),
            ):
                rc = command_init(args, console)

        mock_collect.assert_called_once()
        self.assertEqual(0, rc)

    # -- runner hint messages ------------------------------------------------

    def test_claude_runner_hint_mentions_npm_install(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / ".git").mkdir()
            stream = StringIO()
            console = ConsoleReporter(stream=stream)
            # non-interactive defaults to claude runner
            args = self._args(root=str(root), non_interactive=True)
            with patch("shutil.which", return_value=None):
                command_init(args, console)
        self.assertIn("npm install", stream.getvalue())


if __name__ == "__main__":
    unittest.main()
