from __future__ import annotations

import argparse
import fnmatch
import json as _json
from dataclasses import asdict
from pathlib import Path

from ...console import ConsoleReporter
from ...models import Bead
from ...planner import PlanningService
from ...storage import RepositoryStorage
from ..commands.bead import _validated_feature_root_id


def command_plan(args: argparse.Namespace, planner: PlanningService, console: ConsoleReporter) -> int:
    spec_path = Path(args.spec_file)
    console.section("Planner")
    with console.spin(f"Reading and decomposing {spec_path.name}") as spinner:
        proposal = planner.propose(spec_path)
        top_title = proposal.feature.title if proposal.feature else "no feature root"
        spinner.success(f"Planned epic '{proposal.epic_title}' with feature root '{top_title}'")
    if args.write:
        with console.spin("Writing bead graph") as spinner:
            created = planner.write_plan(proposal)
            spinner.success(f"Wrote {len(created)} beads")
        created_beads = []
        for bead_id in created:
            bead = planner.storage.load_bead(bead_id)
            created_beads.append({
                "bead_id": bead.bead_id,
                "title": bead.title,
            })
        console.dump_json({"created": created_beads})
    else:
        console.dump_json({
            "epic_title": proposal.epic_title,
            "epic_description": proposal.epic_description,
            "linked_docs": proposal.linked_docs,
            "feature": asdict(proposal.feature) if proposal.feature else None,
        })
    return 0


def command_handoff(args: argparse.Namespace, storage: RepositoryStorage, console: ConsoleReporter) -> int:
    bead = storage.load_bead(storage.resolve_bead_id(args.bead_id))
    child_id = storage.allocate_child_bead_id(bead.bead_id, args.to)
    handoff = storage.create_bead(
        bead_id=child_id,
        title=f"{args.to.title()} handoff for {bead.title}",
        agent_type=args.to,
        description=args.summary,
        parent_id=bead.bead_id,
        dependencies=[bead.bead_id],
        linked_docs=bead.linked_docs,
        expected_files=bead.touched_files or bead.expected_files,
        expected_globs=bead.expected_globs,
        touched_files=bead.touched_files,
        conflict_risks=bead.conflict_risks,
    )
    console.success(f"Created handoff bead {handoff.bead_id}")
    return 0


def command_retry(args: argparse.Namespace, storage: RepositoryStorage, console: ConsoleReporter) -> int:
    bead = storage.load_bead(storage.resolve_bead_id(args.bead_id))
    # Guard: if a recovery bead is already pending for this bead, retrying would
    # race with or duplicate the recovery path. Warn and skip instead.
    recovery_bead_id = bead.metadata.get("auto_recovery_bead_id")
    if recovery_bead_id:
        try:
            recovery_bead = storage.load_bead(recovery_bead_id)
            if recovery_bead.status not in {"done", "blocked"}:
                console.warn(
                    f"Bead {bead.bead_id} already has a pending recovery bead "
                    f"{recovery_bead_id} (status: {recovery_bead.status}). "
                    "Skipping retry — let the recovery bead complete first."
                )
                return 0
        except Exception:
            pass  # Recovery bead missing or unreadable; allow the retry.
    bead.status = "ready"
    bead.block_reason = ""
    bead.lease = None
    storage.update_bead(bead, event="retried", summary="Bead requeued")
    console.success(f"Requeued bead {bead.bead_id}")
    return 0


def command_summary(args: argparse.Namespace, storage: RepositoryStorage, console: ConsoleReporter) -> int:
    feature_root_id = None
    if args.feature_root:
        try:
            feature_root_id = storage.resolve_bead_id(args.feature_root)
        except ValueError as exc:
            console.error(str(exc))
            return 1
    console.dump_json(storage.summary(feature_root_id=feature_root_id))
    return 0


def command_tui(args: argparse.Namespace, storage: RepositoryStorage, console: ConsoleReporter) -> int:
    from ...tui import run_tui

    feature_root_id = _validated_feature_root_id(storage, args.feature_root)
    if args.feature_root and feature_root_id is None:
        console.error(f"{args.feature_root} is not a valid feature root")
        return 1

    return run_tui(
        storage,
        feature_root_id=feature_root_id,
        refresh_seconds=args.refresh_seconds,
        max_workers=args.max_workers,
        stream=console.stream,
    )


def command_asset(args: argparse.Namespace, console: ConsoleReporter) -> int:
    """Manage asset ownership entries in ``.takt/assets-manifest.json``.

    Dispatches to ``mark-owned``, ``unmark-owned``, and ``list``
    sub-subcommands.
    """
    from ...onboarding import (
        _sha256_file,
        evaluate_upgrade_actions,
        read_assets_manifest,
    )
    from ...console import BOLD, CYAN, DIM, GREEN, RED, RESET, YELLOW

    root = Path(args.root or ".").resolve()
    manifest = read_assets_manifest(root)
    assets: dict[str, dict] = manifest.get("assets", {})
    subcommand = args.asset_command

    if subcommand in ("mark-owned", "unmark-owned"):
        glob_pattern: str = args.glob
        target_value = subcommand == "mark-owned"
        matched = [
            rp for rp in assets
            if fnmatch.fnmatch(rp, glob_pattern)
        ]
        if not matched:
            console.warn(f"No manifest entries matched pattern: {glob_pattern!r}")
            return 0
        updated = 0
        for rp in matched:
            entry = assets[rp]
            # User-added files (source: user) must remain user_owned: true;
            # unmark-owned must not clear that flag or they would be disabled
            # on the next upgrade run.
            if not target_value and entry.get("source") == "user":
                console.warn(f"  {rp}  →  skipped (user-added files always remain user-owned)")
                continue
            entry["user_owned"] = target_value
            verb = "marked as user-owned" if target_value else "unmarked (upgrade-managed)"
            console.emit(f"  {rp}  →  {verb}")
            updated += 1

        manifest["assets"] = assets
        manifest_path = root / ".takt" / "assets-manifest.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(_json.dumps(manifest, indent=2), encoding="utf-8")
        console.success(f"{updated} asset(s) updated in manifest.")
        return 0

    if subcommand == "list":
        decisions = evaluate_upgrade_actions(root, manifest)
        if not decisions:
            console.emit("No assets tracked in manifest.")
            return 0

        # Column widths.
        path_w = max((len(d.rel_path) for d in decisions), default=40)
        header = f"{'PATH':<{path_w}}  {'STATUS':<18}  {'SOURCE':<10}  OWNED"
        console.emit(console._c(BOLD) + header + console._c(RESET))
        console.emit("-" * len(header))

        _action_labels: dict[str, str] = {
            "update": "update available",
            "unchanged": "up-to-date",
            "new": "new",
            "restored": "missing (will restore)",
            "skipped_user_owned": "user-owned",
            "skipped_modified": "locally modified",
            "disabled": "removed from bundle",
            "user_added": "user-added",
        }
        _action_colors: dict[str, str] = {
            "update": CYAN,
            "unchanged": DIM,
            "new": GREEN,
            "restored": YELLOW,
            "skipped_user_owned": DIM,
            "skipped_modified": YELLOW,
            "disabled": DIM,
            "user_added": GREEN,
        }

        for decision in sorted(decisions, key=lambda d: d.rel_path):
            label = _action_labels.get(decision.action, decision.action)
            color = _action_colors.get(decision.action, RESET)
            source = assets.get(decision.rel_path, {}).get("source", "—")
            owned = "yes" if decision.user_owned else "no"
            colored_label = console._c(color) + f"{label:<18}" + console._c(RESET)
            console.emit(
                f"{decision.rel_path:<{path_w}}  {colored_label}  {source:<10}  {owned}"
            )
        return 0

    console.error(f"Unknown asset subcommand: {subcommand!r}")
    return 1
