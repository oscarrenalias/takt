from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from ...config import load_config
from ...console import ConsoleReporter
from ...graph import render_bead_graph
from ...storage import RepositoryStorage
from ..formatting import format_bead_list_plain, format_claims_plain


def _validated_feature_root_id(storage: RepositoryStorage, feature_root_id: str | None) -> str | None:
    if not feature_root_id:
        return None
    target_path = storage.bead_path(feature_root_id)
    if not target_path.exists():
        return None
    target = storage.load_bead(feature_root_id)
    if storage.feature_root_id_for(target) != feature_root_id:
        return None
    return feature_root_id


def _resolve_feature_root_id(storage: RepositoryStorage, prefix: str) -> str | None:
    validated = _validated_feature_root_id(storage, prefix)
    if validated is not None:
        return validated

    matches = [
        bead.bead_id
        for bead in storage.list_beads()
        if bead.bead_id.startswith(prefix) and storage.feature_root_id_for(bead) == bead.bead_id
    ]
    if not matches:
        try:
            resolved_bead_id = storage.resolve_bead_id(prefix)
        except ValueError:
            raise
        return _validated_feature_root_id(storage, resolved_bead_id)
    if len(matches) == 1:
        return matches[0]

    matches.sort()
    match_list = ", ".join(matches)
    raise ValueError(
        f"Ambiguous feature root prefix '{prefix}' matches {len(matches)} beads: {match_list}"
    )


def command_bead(args: argparse.Namespace, storage: RepositoryStorage, console: ConsoleReporter) -> int:
    if args.bead_command == "create":
        bead = storage.create_bead(
            title=args.title,
            agent_type=args.agent,
            description=args.description,
            parent_id=args.parent_id,
            dependencies=args.dependency,
            acceptance_criteria=args.criterion,
            linked_docs=args.linked_doc,
            expected_files=args.expected_file,
            expected_globs=args.expected_glob,
            touched_files=args.touched_file,
            conflict_risks=args.conflict_risks,
            labels=args.label,
        )
        console.success(f"Created bead {bead.bead_id}")
        return 0

    if args.bead_command == "show":
        bead = storage.load_bead(storage.resolve_bead_id(args.bead_id))
        console.dump_json(bead.to_dict())
        return 0

    if args.bead_command == "list":
        beads = storage.list_beads()
        label_filter = getattr(args, "label_filter", [])
        if label_filter:
            beads = [b for b in beads if all(lbl in b.labels for lbl in label_filter)]
        if getattr(args, "plain", False):
            console.emit(format_bead_list_plain(beads))
        else:
            console.dump_json([bead.to_dict() for bead in beads])
        return 0

    if args.bead_command == "claims":
        claims = storage.active_claims()
        if getattr(args, "plain", False):
            console.emit(format_claims_plain(claims))
        else:
            console.dump_json(claims)
        return 0

    if args.bead_command == "graph":
        beads = storage.list_beads()
        if args.feature_root:
            try:
                resolved_feature_root_id = _resolve_feature_root_id(storage, args.feature_root)
            except ValueError as exc:
                console.error(str(exc))
                return 1

            feature_root_id = _validated_feature_root_id(storage, resolved_feature_root_id)
            if feature_root_id is None:
                console.error(f"{args.feature_root} is not a valid feature root")
                return 1

            feature_root = storage.load_bead(feature_root_id)
            beads_by_id = {bead.bead_id: bead for bead in beads}
            beads = [
                bead for bead in beads
                if bead.bead_id == feature_root_id or storage.feature_root_id_for(bead) == feature_root_id
            ]
            if feature_root.parent_id:
                parent = beads_by_id.get(feature_root.parent_id) or storage.load_bead(feature_root.parent_id)
                if parent.bead_type == "epic" and parent.bead_id not in {bead.bead_id for bead in beads}:
                    beads = [parent, *beads]

        graph = render_bead_graph(beads, load_config(storage.root))
        if args.output:
            output_path = Path(args.output)
            output_path.write_text(f"```mermaid\n{graph}\n```\n", encoding="utf-8")
            print(f"Wrote Mermaid graph to {output_path}", file=sys.stderr)
        else:
            console.emit(graph)
        return 0

    if args.bead_command == "delete":
        try:
            bead_id = storage.resolve_bead_id(args.bead_id)
            bead = storage.delete_bead(bead_id, force=args.force)
        except ValueError as exc:
            console.error(str(exc))
            return 1
        storage.record_event("bead_deleted", {"bead_id": bead.bead_id, "title": bead.title})
        console.success(f"Deleted bead {bead.bead_id}")
        for artifact_dir in (
            storage.state_dir / "agent-runs" / bead.bead_id,
            storage.telemetry_dir / bead.bead_id,
        ):
            if artifact_dir.exists():
                shutil.rmtree(artifact_dir)
                console.detail(f"Removed {artifact_dir}")
            else:
                console.detail(f"No artifact directory at {artifact_dir}")
        if bead.feature_root_id == bead.bead_id:
            worktree_path = storage.worktrees_dir / bead.bead_id
            if worktree_path.exists():
                status_proc = subprocess.run(
                    ["git", "status", "--porcelain", "--untracked-files=all"],
                    cwd=worktree_path,
                    text=True,
                    capture_output=True,
                    check=False,
                )
                if status_proc.returncode == 0 and status_proc.stdout.strip():
                    console.warn(f"Worktree at {worktree_path} has uncommitted changes; removing anyway")
                remove_proc = subprocess.run(
                    ["git", "worktree", "remove", "--force", str(worktree_path)],
                    cwd=storage.root,
                    text=True,
                    capture_output=True,
                    check=False,
                )
                if remove_proc.returncode != 0:
                    console.warn(f"Failed to remove worktree: {remove_proc.stderr.strip() or remove_proc.stdout.strip()}")
                else:
                    console.detail(f"Removed worktree {worktree_path}")
                branch_name = f"feature/{bead.bead_id.lower()}"
                branch_proc = subprocess.run(
                    ["git", "branch", "-D", branch_name],
                    cwd=storage.root,
                    text=True,
                    capture_output=True,
                    check=False,
                )
                if branch_proc.returncode != 0:
                    console.warn(f"Failed to delete branch {branch_name}: {branch_proc.stderr.strip() or branch_proc.stdout.strip()}")
                else:
                    console.detail(f"Deleted branch {branch_name}")
        return 0

    if args.bead_command == "update":
        bead = storage.load_bead(storage.resolve_bead_id(args.bead_id))
        if args.status:
            bead.status = args.status
        if args.description:
            bead.description = args.description
        if args.block_reason is not None:
            bead.block_reason = args.block_reason
        if args.expected_file:
            bead.expected_files = list(args.expected_file)
        if args.expected_glob:
            bead.expected_globs = list(args.expected_glob)
        if args.touched_file:
            bead.touched_files = list(args.touched_file)
        if args.conflict_risks is not None:
            bead.conflict_risks = args.conflict_risks
        if args.model is not None:
            if bead.metadata is None:
                bead.metadata = {}
            bead.metadata["model_override"] = args.model
        storage.update_bead(bead, event="updated", summary="Bead updated via CLI")
        console.success(f"Updated bead {bead.bead_id}")
        return 0

    if args.bead_command == "label":
        bead = storage.load_bead(storage.resolve_bead_id(args.bead_id))
        added = []
        for lbl in args.labels:
            if lbl not in bead.labels:
                bead.labels.append(lbl)
                added.append(lbl)
        storage.update_bead(bead, event="updated", summary=f"Added labels: {', '.join(args.labels)}")
        if added:
            console.success(f"Added label(s) {', '.join(added)} to {bead.bead_id}")
        else:
            console.detail(f"No new labels added to {bead.bead_id} (already present)")
        return 0

    if args.bead_command == "unlabel":
        bead = storage.load_bead(storage.resolve_bead_id(args.bead_id))
        if args.label in bead.labels:
            bead.labels.remove(args.label)
            storage.update_bead(bead, event="updated", summary=f"Removed label: {args.label}")
            console.success(f"Removed label '{args.label}' from {bead.bead_id}")
        else:
            console.detail(f"Label '{args.label}' not present on {bead.bead_id}")
        return 0

    return 1
