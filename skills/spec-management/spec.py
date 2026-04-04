#!/usr/bin/env python3
"""Standalone CLI for creating and managing spec files and their lifecycle transitions."""

import argparse
import os
import re
import secrets
import shutil
import sys
from typing import Any, Dict, List, Optional, Tuple

try:
    import yaml
except ImportError:
    print("error: PyYAML is required — install with: pip install pyyaml", file=sys.stderr)
    sys.exit(1)

SPECS_DIR = "specs"
DRAFTS_DIR = os.path.join(SPECS_DIR, "drafts")
PLANNED_DIR = os.path.join(SPECS_DIR, "planned")
DONE_DIR = os.path.join(SPECS_DIR, "done")

LIFECYCLE_DIRS = [DRAFTS_DIR, PLANNED_DIR, DONE_DIR]

STATUS_TO_DIR: Dict[str, str] = {
    "draft": DRAFTS_DIR,
    "planned": PLANNED_DIR,
    "done": DONE_DIR,
}

SPECS_NOT_FOUND_MSG = (
    "error: specs/ not found — run 'spec init' to initialise, "
    "or check you are in the project root"
)

_LIFECYCLE_DIRS_NOT_FOUND_MSG = (
    "error: specs directory not found — run from the project root"
)


# ---------------------------------------------------------------------------
# Frontmatter error types
# ---------------------------------------------------------------------------


class FrontmatterError(Exception):
    """Raised when frontmatter YAML cannot be parsed."""


class LegacySpecError(Exception):
    """Raised when a spec file has no frontmatter block at all."""


# ---------------------------------------------------------------------------
# Low-level frontmatter splitting/joining
# ---------------------------------------------------------------------------


def _split_frontmatter(content: str) -> Tuple[Optional[str], str]:
    """Split file content into (raw_yaml_str, body).

    Returns (None, content) when the file has no ``---`` frontmatter block,
    which indicates a legacy spec.  A file that opens with ``---`` but has no
    closing delimiter is also treated as legacy (no frontmatter).
    """
    if not content.startswith("---"):
        return None, content

    lines = content.split("\n")
    close_idx: Optional[int] = None
    for i, line in enumerate(lines[1:], start=1):
        if line.rstrip() == "---":
            close_idx = i
            break

    if close_idx is None:
        # Opening marker present but no closing marker — treat as legacy.
        return None, content

    frontmatter_str = "\n".join(lines[1:close_idx])
    body = "\n".join(lines[close_idx + 1:])
    return frontmatter_str, body


# ---------------------------------------------------------------------------
# Public frontmatter utilities
# ---------------------------------------------------------------------------


def parse_frontmatter(path: str) -> Tuple[Optional[Dict[str, Any]], str]:
    """Parse a spec file and return ``(frontmatter_dict, body)``.

    * Returns ``(None, full_content)`` for legacy specs that have no
      frontmatter block — callers can distinguish these from malformed specs.
    * Raises :class:`FrontmatterError` when the ``---`` block is present but
      contains invalid YAML.
    """
    with open(path, encoding="utf-8") as fh:
        content = fh.read()

    fm_str, body = _split_frontmatter(content)

    if fm_str is None:
        return None, content

    try:
        data = yaml.safe_load(fm_str)
    except yaml.YAMLError as exc:
        raise FrontmatterError(
            f"could not parse frontmatter in {path}: {exc}"
        ) from exc

    if not isinstance(data, dict):
        data = {}

    return data, body


def is_legacy_spec(path: str) -> bool:
    """Return ``True`` if *path* contains no frontmatter block.

    A file with a ``---`` opening marker but no matching closing marker is
    also considered legacy — it could be markdown with a thematic break.
    """
    with open(path, encoding="utf-8") as fh:
        content = fh.read()
    fm_str, _ = _split_frontmatter(content)
    return fm_str is None


def infer_display_name(path: str, frontmatter: Optional[Dict[str, Any]]) -> str:
    """Infer a human-readable display name for a spec.

    Priority order:
    1. ``name`` field in frontmatter
    2. First ``# Heading`` found in the file body
    3. Filename stem with hyphens/underscores replaced by spaces, title-cased
    """
    if frontmatter and frontmatter.get("name"):
        return str(frontmatter["name"])

    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                stripped = line.strip()
                if stripped.startswith("# "):
                    return stripped[2:].strip()
    except OSError:
        pass

    stem = os.path.splitext(os.path.basename(path))[0]
    return stem.replace("-", " ").replace("_", " ").title()


def write_frontmatter(path: str, new_data: Dict[str, Any]) -> None:
    """Rewrite the frontmatter of *path* to *new_data*, preserving the body.

    If the file has no existing frontmatter the new block is prepended before
    the existing content.
    """
    with open(path, encoding="utf-8") as fh:
        content = fh.read()

    _, body = _split_frontmatter(content)

    fm_yaml = yaml.dump(
        new_data,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
    )
    new_content = f"---\n{fm_yaml}---\n{body}"

    with open(path, "w", encoding="utf-8") as fh:
        fh.write(new_content)


# ---------------------------------------------------------------------------
# Spec discovery
# ---------------------------------------------------------------------------


def find_all_specs() -> List[str]:
    """Return sorted paths to every ``.md`` file across all lifecycle folders."""
    paths: List[str] = []
    for folder in LIFECYCLE_DIRS:
        if os.path.isdir(folder):
            for entry in sorted(os.listdir(folder)):
                if entry.endswith(".md"):
                    paths.append(os.path.join(folder, entry))
    return paths


def _spec_id(path: str) -> Optional[str]:
    """Return the ``id`` field from a spec's frontmatter, or ``None``."""
    try:
        fm, _ = parse_frontmatter(path)
        if fm and fm.get("id"):
            return str(fm["id"])
    except FrontmatterError:
        pass
    return None


# ---------------------------------------------------------------------------
# ID / filename resolution
# ---------------------------------------------------------------------------


def resolve_spec(query: str) -> str:
    """Locate a spec by full ``id`` or partial filename (case-insensitive).

    Returns the path to the single matching spec.  Prints an error and exits
    with code 1 if the query matches zero or more than one spec.

    Error messages match the format documented in the spec:
    * No match  → ``error: no spec matching "<query>"``
    * Ambiguous → ``error: "<query>" matches multiple specs: <list of ids>``
    """
    query_lower = query.lower()
    all_paths = find_all_specs()

    # --- Exact ID match (highest priority, returns immediately) ---
    for path in all_paths:
        spec_id = _spec_id(path)
        if spec_id and spec_id.lower() == query_lower:
            return path

    # --- Partial filename match ---
    candidates: List[str] = []
    for path in all_paths:
        stem = os.path.splitext(os.path.basename(path))[0].lower()
        if query_lower in stem:
            candidates.append(path)

    if not candidates:
        print(f'error: no spec matching "{query}"', file=sys.stderr)
        sys.exit(1)

    if len(candidates) > 1:
        ids = [_spec_id(p) or os.path.basename(p) for p in candidates]
        print(
            f'error: "{query}" matches multiple specs: {", ".join(ids)}',
            file=sys.stderr,
        )
        sys.exit(1)

    return candidates[0]


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


def _require_specs_dir() -> None:
    """Exit 1 with a remediation message if specs/ is absent."""
    if not os.path.isdir(SPECS_DIR):
        print(SPECS_NOT_FOUND_MSG, file=sys.stderr)
        sys.exit(1)


def _require_lifecycle_dirs() -> None:
    """Exit 1 if any lifecycle subfolder is missing."""
    missing = [d for d in LIFECYCLE_DIRS if not os.path.isdir(d)]
    if missing:
        print(_LIFECYCLE_DIRS_NOT_FOUND_MSG, file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _title_to_filename(title: str) -> str:
    """Derive a slug filename from a spec title."""
    slug = title.lower()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"[\s]+", "-", slug.strip())
    slug = re.sub(r"-+", "-", slug)
    return slug + ".md"


def _normalize_tags(raw: Any) -> List[str]:
    """Normalise a tags value that may be a list, comma-string, or None."""
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(t).strip() for t in raw]
    return [t.strip() for t in str(raw).split(",") if t.strip()]


SPEC_TEMPLATE = """\
---
name: {name}
id: spec-{spec_id}
description:
dependencies:
priority:
complexity:
status: draft
tags: []
scope:
  in:
  out:
feature_root_id:
---

# {name}

## Objective

## Problems to Fix

## Changes

## Files to Modify

| File | Change |
|---|---|

## Acceptance Criteria

## Pending Decisions
"""


# ---------------------------------------------------------------------------
# Command implementations
# ---------------------------------------------------------------------------


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
    _require_lifecycle_dirs()

    title = args.title
    filename = _title_to_filename(title)
    dest = os.path.join(DRAFTS_DIR, filename)

    if os.path.exists(dest):
        print(f"error: spec file already exists: {dest}", file=sys.stderr)
        sys.exit(1)

    spec_id = secrets.token_hex(4)
    content = SPEC_TEMPLATE.format(name=title, spec_id=spec_id)

    with open(dest, "w", encoding="utf-8") as fh:
        fh.write(content)

    print(f"Created {dest}")
    print(f"ID: spec-{spec_id}")


def cmd_list(args: argparse.Namespace) -> None:
    _require_specs_dir()

    specs = find_all_specs()
    rows: List[Tuple[str, str, str, str, str]] = []  # id, status, priority, complexity, name

    for path in specs:
        try:
            fm, _ = parse_frontmatter(path)
        except FrontmatterError as exc:
            print(f"warning: {exc}", file=sys.stderr)
            fm = None

        if fm is None:
            # Legacy spec — derive what we can from the file
            spec_id = "—"
            status = "legacy"
            priority = "—"
            complexity = "—"
        else:
            spec_id = str(fm.get("id") or "—")
            status = str(fm.get("status") or "—")
            priority = str(fm.get("priority") or "—")
            complexity = str(fm.get("complexity") or "—")

        name = infer_display_name(path, fm)

        # Apply filters
        if args.status and status != args.status:
            continue
        if args.tag:
            tags = _normalize_tags(fm.get("tags") if fm else None)
            if args.tag not in tags:
                continue
        if args.priority and priority != args.priority:
            continue

        rows.append((spec_id, status, priority, complexity, name))

    if not rows:
        print("No specs found.")
        return

    # Column widths
    col_id = max(len("id"), max(len(r[0]) for r in rows))
    col_st = max(len("status"), max(len(r[1]) for r in rows))
    col_pr = max(len("priority"), max(len(r[2]) for r in rows))
    col_cx = max(len("complexity"), max(len(r[3]) for r in rows))

    fmt = f"{{:<{col_id}}}  {{:<{col_st}}}  {{:<{col_pr}}}  {{:<{col_cx}}}  {{}}"
    print(fmt.format("id", "status", "priority", "complexity", "name"))
    for row in rows:
        print(fmt.format(*row))


def cmd_show(args: argparse.Namespace) -> None:
    _require_specs_dir()

    path = resolve_spec(args.spec)

    try:
        fm, body = parse_frontmatter(path)
    except FrontmatterError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)

    if fm is None:
        print(f"warning: {path} is a legacy spec — no frontmatter")
        print()
        sys.stdout.write(body)
        return

    print("---")
    print(
        yaml.dump(fm, default_flow_style=False, allow_unicode=True, sort_keys=False),
        end="",
    )
    print("---")

    # Print first section body (up to first --- or 20 lines)
    body_lines = body.splitlines()
    shown = 0
    for line in body_lines:
        if line.rstrip() == "---":
            break
        if shown >= 20:
            break
        print(line)
        shown += 1


def cmd_set(args: argparse.Namespace) -> None:
    _require_specs_dir()

    path = resolve_spec(args.spec)

    try:
        fm, _ = parse_frontmatter(path)
    except FrontmatterError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)

    if fm is None:
        print(
            f"error: {path} has no frontmatter — run 'spec migrate {args.spec}' first",
            file=sys.stderr,
        )
        sys.exit(1)

    field = args.field
    value = args.value

    if field == "status":
        fm["status"] = value
        write_frontmatter(path, fm)

        target_dir = STATUS_TO_DIR[value]
        new_path = os.path.join(target_dir, os.path.basename(path))
        if os.path.abspath(path) != os.path.abspath(new_path):
            try:
                shutil.move(path, new_path)
            except OSError as exc:
                print(
                    f"error: could not move {path} to {new_path}: {exc}",
                    file=sys.stderr,
                )
                sys.exit(1)
            # Rewrite frontmatter in new location (already written above at old loc,
            # but move carried the file so it's fine — we need to write at new path).
            # Re-parse and re-write at destination to be safe.
            try:
                fm2, _ = parse_frontmatter(new_path)
            except FrontmatterError:
                fm2 = fm
            if fm2:
                fm2["status"] = value
                write_frontmatter(new_path, fm2)
            print(new_path)
        else:
            print(path)

    elif field == "feature-root":
        fm["feature_root_id"] = value
        write_frontmatter(path, fm)
        print(f"Set feature_root_id to {value!r} in {path}")

    elif field == "tags":
        fm["tags"] = [t.strip() for t in value.split(",") if t.strip()]
        write_frontmatter(path, fm)
        print(f"Set tags to {fm['tags']!r} in {path}")

    elif field == "priority":
        fm["priority"] = value
        write_frontmatter(path, fm)
        print(f"Set priority to {value!r} in {path}")

    elif field == "description":
        fm["description"] = value
        write_frontmatter(path, fm)
        print(f"Set description in {path}")

    else:
        print(f"error: unknown field {field!r}", file=sys.stderr)
        sys.exit(1)


def _status_from_path(path: str) -> str:
    """Infer lifecycle status from a spec file's parent folder.

    Falls back to ``"draft"`` for files that do not live under a recognised
    lifecycle directory (e.g. directly in ``specs/``).
    """
    abs_parent = os.path.abspath(os.path.dirname(path))
    for status, rel_dir in STATUS_TO_DIR.items():
        if abs_parent == os.path.abspath(rel_dir):
            return status
    return "draft"


def cmd_migrate(args: argparse.Namespace) -> None:
    _require_specs_dir()

    path = resolve_spec(args.spec)

    if not is_legacy_spec(path):
        print(f"error: {path} already has frontmatter", file=sys.stderr)
        sys.exit(1)

    display_name = infer_display_name(path, None)
    spec_id = secrets.token_hex(4)
    status = _status_from_path(path)

    fm: Dict[str, Any] = {
        "name": display_name,
        "id": f"spec-{spec_id}",
        "description": None,
        "dependencies": None,
        "priority": None,
        "complexity": None,
        "status": status,
        "tags": [],
        "scope": {"in": None, "out": None},
        "feature_root_id": None,
    }
    write_frontmatter(path, fm)
    print(f"Migrated {path}")
    print(f"ID: spec-{spec_id}")


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


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
