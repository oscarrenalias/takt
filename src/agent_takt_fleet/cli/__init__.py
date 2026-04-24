from __future__ import annotations

import argparse
import sys

from .parser import build_parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "register":
        from .commands.register import command_register
        return command_register(args)

    if args.command == "unregister":
        from .commands.register import command_unregister
        return command_unregister(args)

    if args.command == "list":
        from .commands.register import command_list
        return command_list(args)

    if args.command == "dispatch":
        from .commands.dispatch import command_dispatch
        return command_dispatch(args)

    if args.command == "run":
        from .commands.run import command_run
        return command_run(args)

    if args.command == "summary":
        from .commands.summary import command_summary
        return command_summary(args)

    if args.command == "watch":
        from .commands.watch import command_watch
        return command_watch(args)

    if args.command == "runs":
        if args.runs_command == "list":
            from .commands.runs import command_runs_list
            return command_runs_list(args)
        if args.runs_command == "show":
            from .commands.runs import command_runs_show
            return command_runs_show(args)

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
