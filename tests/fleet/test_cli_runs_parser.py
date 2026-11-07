"""Regression tests: `runs list --command` must not clobber the top-level command dest."""
from __future__ import annotations

import argparse
from unittest.mock import patch

import pytest

from agent_takt_fleet.cli import main
from agent_takt_fleet.cli.parser import build_parser


def test_runs_list_parses_as_runs_command() -> None:
    args = build_parser().parse_args(["runs", "list"])
    assert args.command == "runs"
    assert args.runs_command == "list"
    assert args.command_filter is None


def test_runs_list_command_filter_does_not_overwrite_command() -> None:
    args = build_parser().parse_args(["runs", "list", "--command", "run"])
    assert args.command == "runs"
    assert args.runs_command == "list"
    assert args.command_filter == "run"


def test_main_runs_list_invokes_list_handler(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setattr("sys.argv", ["takt-fleet", "runs", "list"])
    with patch("agent_takt_fleet.cli.commands.runs.command_runs_list", return_value=0) as m:
        assert main() == 0
    m.assert_called_once()
    assert "usage:" not in capsys.readouterr().out


def test_main_runs_list_command_filter_never_runs_fleet_command(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setattr("sys.argv", ["takt-fleet", "runs", "list", "--command", "run"])
    with patch("agent_takt_fleet.cli.commands.runs.command_runs_list", return_value=0) as m, \
         patch("agent_takt_fleet.cli.commands.run.command_run") as run, \
         patch("agent_takt_fleet.cli.commands.dispatch.command_dispatch") as disp:
        assert main() == 0
    m.assert_called_once()
    assert m.call_args[0][0].command_filter == "run"
    run.assert_not_called()
    disp.assert_not_called()


def test_main_runs_show_dispatches_to_show(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setattr("sys.argv", ["takt-fleet", "runs", "show", "FR-a1b2c3d4"])
    with patch("agent_takt_fleet.cli.commands.runs.command_runs_show", return_value=0) as m:
        assert main() == 0
    m.assert_called_once()


def _all_parsers(parser: argparse.ArgumentParser):
    yield parser
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for sub in action.choices.values():
                yield from _all_parsers(sub)


def test_no_subcommand_option_uses_reserved_command_dest() -> None:
    for p in _all_parsers(build_parser()):
        for action in p._actions:
            if isinstance(action, argparse._SubParsersAction):
                continue
            assert action.dest != "command", f"{p.prog}: option {action.option_strings} uses dest 'command'"
