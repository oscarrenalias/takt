#!/usr/bin/env python3
"""Standalone CLI for creating and managing spec files and their lifecycle transitions."""

import argparse
import os
import sys

SPECS_DIR = "specs"
DRAFTS_DIR = os.path.join(SPECS_DIR, "drafts")
PLANNED_DIR = os.path.join(SPECS_DIR, "planned")
DONE_DIR = os.path.join(SPECS_DIR, "done")

SPECS_NOT_FOUND_MSG = (
    "error: specs/ not found — run 'spec init' to initialise, "
    "or check you are in the project root"
)


def _require_specs_dir() -> None:
    """Exit 1 with a remediation message if specs/ is absent."""
    if not os.path.isdir(SPECS_DIR):
        print(SPECS_NOT_FOUND_MSG, file=sys.stderr)
        sys.exit(1)


def cmd_init(_args: argparse.Namespace) -> None:
    if os.path.exists(SPECS_DIR):
        print(
            "error: specs/ already exists — remove it manually if you want to reinitialise",
            file=sys.stderr,
        )
        sys.exit(1)
    os.makedirs(DRAFTS_DIR)
    os.makedirs(PLANNED_DIR)
    os.makedirs(DONE_DIR)
    print(f"Initialised specs/ in {os.getcwd()}")


def cmd_create(args: argparse.Namespace) -> None:
    _require_specs_dir()
    raise NotImplementedError("spec create is not yet implemented")


def cmd_list(args: argparse.Namespace) -> None:
    _require_specs_dir()
    raise NotImplementedError("spec list is not yet implemented")


def cmd_show(args: argparse.Namespace) -> None:
    _require_specs_dir()
    raise NotImplementedError("spec show is not yet implemented")


def cmd_set(args: argparse.Namespace) -> None:
    _require_specs_dir()
    raise NotImplementedError("spec set is not yet implemented")


def cmd_migrate(args: argparse.Namespace) -> None:
    _require_specs_dir()
    raise NotImplementedError("spec migrate is not yet implemented")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="spec",
        description="Manage spec files and their lifecycle transitions.",
    )
    subparsers = parser.add_subparsers(dest="subcommand", metavar="<subcommand>")
    subparsers.required = True

    # init
    p_init = subparsers.add_parser(
        "init", help="Initialise specs/ folder structure in the current directory."
    )
    p_init.set_defaults(func=cmd_init)

    # create
    p_create = subparsers.add_parser(
        "create", help="Create a new spec file in specs/drafts/."
    )
    p_create.add_argument("title", help="Spec title")
    p_create.set_defaults(func=cmd_create)

    # list
    p_list = subparsers.add_parser(
        "list", help="List all specs across drafts/, planned/, and done/."
    )
    p_list.add_argument("--status", metavar="STATUS", help="Filter by status")
    p_list.add_argument("--tag", metavar="TAG", help="Filter by tag")
    p_list.add_argument("--priority", metavar="PRIORITY", help="Filter by priority")
    p_list.set_defaults(func=cmd_list)

    # show
    p_show = subparsers.add_parser(
        "show", help="Print frontmatter and first section of a spec."
    )
    p_show.add_argument("spec", help="Spec ID or partial filename")
    p_show.set_defaults(func=cmd_show)

    # set
    p_set = subparsers.add_parser("set", help="Update a frontmatter field.")
    set_sub = p_set.add_subparsers(dest="field", metavar="<field>")
    set_sub.required = True

    p_set_status = set_sub.add_parser("status", help="Set status and move file.")
    p_set_status.add_argument("value", choices=["draft", "planned", "done"])
    p_set_status.add_argument("spec", help="Spec ID or partial filename")
    p_set_status.set_defaults(func=cmd_set)

    p_set_feature_root = set_sub.add_parser(
        "feature-root", help="Set feature_root_id field."
    )
    p_set_feature_root.add_argument("value", metavar="bead-id")
    p_set_feature_root.add_argument("spec", help="Spec ID or partial filename")
    p_set_feature_root.set_defaults(func=cmd_set)

    p_set_tags = set_sub.add_parser("tags", help="Replace tags list.")
    p_set_tags.add_argument("value", metavar="tag1,tag2,...")
    p_set_tags.add_argument("spec", help="Spec ID or partial filename")
    p_set_tags.set_defaults(func=cmd_set)

    p_set_priority = set_sub.add_parser("priority", help="Set priority field.")
    p_set_priority.add_argument("value", choices=["high", "medium", "low"])
    p_set_priority.add_argument("spec", help="Spec ID or partial filename")
    p_set_priority.set_defaults(func=cmd_set)

    p_set_description = set_sub.add_parser("description", help="Set description field.")
    p_set_description.add_argument("value", metavar="text")
    p_set_description.add_argument("spec", help="Spec ID or partial filename")
    p_set_description.set_defaults(func=cmd_set)

    # migrate
    p_migrate = subparsers.add_parser(
        "migrate", help="Add frontmatter to a spec that has none."
    )
    p_migrate.add_argument("spec", help="Spec ID or partial filename")
    p_migrate.set_defaults(func=cmd_migrate)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
