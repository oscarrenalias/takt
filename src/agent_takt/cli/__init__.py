from __future__ import annotations

import argparse
from pathlib import Path

from ..console import ConsoleReporter
from .formatting import LIST_PLAIN_COLUMNS
from .parser import build_parser
from .services import apply_operator_status_update, make_services
from .commands import command_bead
from .commands.run import CliSchedulerReporter, command_run
from .commands.merge import command_merge
from .commands.telemetry import (
    _bead_cost_usd,
    _bead_wall_clock_seconds,
    _filter_beads_by_days,
    _format_telemetry_table,
    aggregate_telemetry,
    command_telemetry,
)
from .commands.init import command_init, command_upgrade
from .commands.misc import (
    command_plan,
    command_handoff,
    command_retry,
    command_summary,
    command_tui,
    command_asset,
)


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    root = Path(args.root or ".").resolve()
    console = ConsoleReporter()

    # Commands that do not need an existing .takt/ storage directory
    if args.command == "init":
        return command_init(args, console)
    if args.command == "upgrade":
        return command_upgrade(args, console)
    if args.command == "asset":
        return command_asset(args, console)

    storage, scheduler, planner = make_services(root, runner_backend=args.runner)

    if args.command == "plan":
        return command_plan(args, planner, console)
    if args.command == "run":
        return command_run(args, scheduler, console)
    if args.command == "bead":
        return command_bead(args, storage, console)
    if args.command == "handoff":
        return command_handoff(args, storage, console)
    if args.command == "retry":
        return command_retry(args, storage, console)
    if args.command == "merge":
        return command_merge(args, storage, console)
    if args.command == "summary":
        return command_summary(args, storage, console)
    if args.command == "tui":
        return command_tui(args, storage, console)
    if args.command == "telemetry":
        return command_telemetry(args, storage, console)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
