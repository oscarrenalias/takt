"""Tests for --version flag and prog name in takt CLI.

Covers:
- build_parser() uses prog="takt"
- --version exits with code 0
- --version output matches "takt <version>"
- --help output references "takt" not "orchestrator"
- Existing subcommands still parse correctly after version flag added
"""
from __future__ import annotations

import sys
import unittest
from importlib.metadata import version as _pkg_version
from io import StringIO
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_takt.cli import build_parser


class TestVersionFlag(unittest.TestCase):
    def test_version_exits_zero(self):
        parser = build_parser()
        with self.assertRaises(SystemExit) as ctx:
            parser.parse_args(["--version"])
        self.assertEqual(0, ctx.exception.code)

    def test_version_output_format(self):
        """--version should print 'takt <version>' matching the installed package."""
        import io
        parser = build_parser()
        with self.assertRaises(SystemExit):
            # argparse writes version to stdout
            old_stdout = sys.stdout
            sys.stdout = buf = io.StringIO()
            try:
                parser.parse_args(["--version"])
            finally:
                sys.stdout = old_stdout
        expected_version = _pkg_version("agent-takt")
        output = buf.getvalue().strip()
        self.assertEqual(f"takt {expected_version}", output)

    def test_prog_name_is_takt(self):
        parser = build_parser()
        self.assertEqual("takt", parser.prog)

    def test_help_usage_line_uses_takt_prog(self):
        """The usage line must reference 'takt', not 'orchestrator' or '__main__'."""
        parser = build_parser()
        help_text = parser.format_help()
        # usage line is the first non-blank line
        usage_line = next(line for line in help_text.splitlines() if line.strip())
        self.assertIn("takt", usage_line)
        self.assertNotIn("orchestrator", usage_line)
        self.assertNotIn("__main__", usage_line)


class TestExistingSubcommands(unittest.TestCase):
    """Verify that existing subcommands still parse after adding --version."""

    def test_run_subcommand_parses(self):
        parser = build_parser()
        # run requires at least the subcommand
        args = parser.parse_args(["run"])
        self.assertEqual("run", args.command)

    def test_run_once_flag(self):
        parser = build_parser()
        args = parser.parse_args(["run", "--once"])
        self.assertTrue(args.once)

    def test_summary_subcommand_parses(self):
        parser = build_parser()
        args = parser.parse_args(["summary"])
        self.assertEqual("summary", args.command)

    def test_bead_list_subcommand_parses(self):
        parser = build_parser()
        args = parser.parse_args(["bead", "list"])
        self.assertEqual("bead", args.command)


if __name__ == "__main__":
    unittest.main()
