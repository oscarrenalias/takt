"""Regression tests: global --root must survive subcommand parsing."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_takt.cli import build_parser, main

# Each entry: subcommand argv (including required positional args / nested subcommands).
SUBCOMMANDS = [
    ["plan", "spec.md"],
    ["run"],
    ["bead", "create", "--agent", "developer", "--title", "t", "--description", "d"],
    ["bead", "list"],
    ["bead", "show", "B-1"],
    ["bead", "graph"],
    ["handoff", "B-1", "--to", "tester", "--summary", "s"],
    ["retry", "B-1"],
    ["merge", "B-1"],
    ["audit", "B-1"],
    ["summary"],
    ["tui"],
    ["telemetry"],
    ["init"],
    ["upgrade"],
    ["asset", "list"],
    ["adr", "list"],
    ["adr", "validate"],
    ["memory", "stats"],
    ["memory", "namespace", "list"],
]


class RootOptionParsingTests(unittest.TestCase):
    def test_root_before_subcommand(self):
        for cmd in SUBCOMMANDS:
            with self.subTest(cmd=cmd):
                args = build_parser().parse_args(["--root", "/x", *cmd])
                self.assertEqual("/x", args.root)

    def test_root_after_subcommand(self):
        for cmd in SUBCOMMANDS:
            with self.subTest(cmd=cmd):
                # insert after the top-level subcommand name
                args = build_parser().parse_args([cmd[0], "--root", "/x", *cmd[1:]])
                self.assertEqual("/x", args.root)

    def test_default_when_omitted(self):
        for cmd in SUBCOMMANDS:
            with self.subTest(cmd=cmd):
                args = build_parser().parse_args(cmd)
                self.assertEqual(".", args.root)

    def test_subcommand_level_wins_when_both_given(self):
        for cmd in SUBCOMMANDS:
            with self.subTest(cmd=cmd):
                args = build_parser().parse_args(["--root", "/a", cmd[0], "--root", "/b", *cmd[1:]])
                self.assertEqual("/b", args.root)


class RootOptionEndToEndTests(unittest.TestCase):
    def test_init_with_global_root_scaffolds_into_root_not_cwd(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as other:
            target = Path(tmp).resolve()
            (target / ".git").mkdir()
            with (
                patch.object(sys, "argv", ["takt", "--root", str(target), "init", "--non-interactive"]),
                patch("shutil.which", return_value="/usr/local/bin/claude"),
                patch("agent_takt.onboarding.scaffold_project") as mock_scaffold,
                patch("os.getcwd", return_value=other),
            ):
                rc = main()
            self.assertEqual(0, rc)
            mock_scaffold.assert_called_once()
            self.assertEqual(target, Path(mock_scaffold.call_args.args[0]))


if __name__ == "__main__":
    unittest.main()
