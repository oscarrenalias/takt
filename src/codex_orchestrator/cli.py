from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import load_config
from .console import ConsoleReporter, SpinnerPool
from .graph import render_bead_graph
from .gitutils import GitError, WorktreeManager
from .models import Bead
from .planner import PlanningService
from .runner import ClaudeCodeAgentRunner, CodexAgentRunner
from .scheduler import Scheduler, SchedulerReporter
from .storage import RepositoryStorage


LIST_PLAIN_COLUMNS: tuple[tuple[str, str], ...] = (
    ("BEAD_ID", "bead_id"),
    ("STATUS", "status"),
    ("AGENT", "agent_type"),
    ("TYPE", "bead_type"),
    ("TITLE", "title"),
    ("FEATURE_ROOT", "feature_root_id"),
    ("PARENT", "parent_id"),
)

OPERATOR_STATUS_TRANSITIONS: dict[str, frozenset[str]] = {
    "ready": frozenset({"open", "blocked", "handed_off"}),
    "blocked": frozenset({"open", "ready", "in_progress", "handed_off"}),
    "done": frozenset({"ready", "in_progress", "handed_off"}),
}


def _plain_value(value: object) -> str:
    if value is None:
        return "-"
    if isinstance(value, str):
        return value or "-"
    return str(value)


def format_bead_list_plain(beads: list[Bead]) -> str:
    ordered = sorted(
        beads,
        key=lambda bead: (bead.execution_history[0].timestamp if bead.execution_history else "", bead.bead_id),
    )
    if not ordered:
        return "No beads found."

    rows = [
        [_plain_value(getattr(bead, attribute, None)) for _, attribute in LIST_PLAIN_COLUMNS]
        for bead in ordered
    ]
    widths = [
        max(len(header), max((len(row[column_index]) for row in rows), default=0))
        for column_index, (header, _) in enumerate(LIST_PLAIN_COLUMNS)
    ]

    header_line = "  ".join(
        header.ljust(widths[column_index])
        for column_index, (header, _) in enumerate(LIST_PLAIN_COLUMNS)
    )
    row_lines = [
        "  ".join(
            value.ljust(widths[column_index])
            for column_index, value in enumerate(row)
        )
        for row in rows
    ]
    return "\n".join([header_line, *row_lines])


def format_claims_plain(claims: list[dict[str, object]]) -> str:
    if not claims:
        return "No active claims."

    lines: list[str] = []
    for claim in claims:
        lease_owner = "-"
        lease = claim.get("lease")
        if isinstance(lease, dict):
            lease_owner = _plain_value(lease.get("owner"))
        lines.append(
            f"{_plain_value(claim.get('bead_id'))} | "
            f"{_plain_value(claim.get('agent_type'))} | "
            f"feature={_plain_value(claim.get('feature_root_id'))} | "
            f"lease={lease_owner}"
        )
    return "\n".join(lines)


def validate_operator_status_update(bead: Bead, target_status: str) -> str | None:
    allowed_sources = OPERATOR_STATUS_TRANSITIONS.get(target_status)
    if allowed_sources is None:
        return f"Unsupported operator status update: {target_status}."
    if target_status == "done" and bead.agent_type == "developer":
        return (
            f"{bead.bead_id} is a developer bead; mark it done through scheduler execution "
            "so follow-up beads are created."
        )
    if bead.status == target_status:
        return f"{bead.bead_id} is already {target_status}."
    if bead.status not in allowed_sources:
        return f"{bead.bead_id} is {bead.status}; cannot mark it {target_status}."
    return None


def apply_operator_status_update(storage: RepositoryStorage, bead_id: str, target_status: str) -> Bead:
    bead = storage.load_bead(bead_id)
    validation_error = validate_operator_status_update(bead, target_status)
    if validation_error is not None:
        raise ValueError(validation_error)
    bead.status = target_status
    if target_status != "blocked":
        bead.block_reason = ""
        bead.handoff_summary.block_reason = ""
    if target_status in {"ready", "done"}:
        bead.lease = None
    storage.update_bead(
        bead,
        event="updated",
        summary=f"Bead marked {target_status} via operator action",
    )
    return bead


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="orchestrator")
    parser.add_argument("--root", default=".", help="Repository root")
    parser.add_argument(
        "--runner",
        choices=["codex", "claude"],
        default=None,
        help="Agent runner backend (default: $ORCHESTRATOR_RUNNER or config.default_runner)",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    plan_parser = subparsers.add_parser("plan")
    plan_parser.add_argument("--root", dest="root", help=argparse.SUPPRESS)
    plan_parser.add_argument("spec_file")
    plan_parser.add_argument("--write", action="store_true")

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--root", dest="root", help=argparse.SUPPRESS)
    run_parser.add_argument("--once", action="store_true")
    run_parser.add_argument("--max-workers", type=int, default=1)
    run_parser.add_argument("--feature-root", help="Run only beads in the specified feature root")

    bead_parser = subparsers.add_parser("bead")
    bead_parser.add_argument("--root", dest="root", help=argparse.SUPPRESS)
    bead_subparsers = bead_parser.add_subparsers(dest="bead_command", required=True)

    create_parser = bead_subparsers.add_parser("create")
    create_parser.add_argument("--title", required=True)
    create_parser.add_argument("--agent", required=True)
    create_parser.add_argument("--description", required=True)
    create_parser.add_argument("--parent-id")
    create_parser.add_argument("--dependency", action="append", default=[])
    create_parser.add_argument("--criterion", action="append", default=[])
    create_parser.add_argument("--linked-doc", action="append", default=[])
    create_parser.add_argument("--expected-file", action="append", default=[])
    create_parser.add_argument("--expected-glob", action="append", default=[])
    create_parser.add_argument("--touched-file", action="append", default=[])
    create_parser.add_argument("--conflict-risks", default="")

    show_parser = bead_subparsers.add_parser("show")
    show_parser.add_argument("bead_id")

    update_parser = bead_subparsers.add_parser("update")
    update_parser.add_argument("bead_id")
    update_parser.add_argument("--status")
    update_parser.add_argument("--description")
    update_parser.add_argument("--block-reason")
    update_parser.add_argument("--expected-file", action="append", default=[])
    update_parser.add_argument("--expected-glob", action="append", default=[])
    update_parser.add_argument("--touched-file", action="append", default=[])
    update_parser.add_argument("--conflict-risks")
    update_parser.add_argument("--model", help="Set per-bead model override (metadata.model_override)")

    delete_parser = bead_subparsers.add_parser("delete")
    delete_parser.add_argument("bead_id")
    delete_parser.add_argument("--force", action="store_true", help="Bypass status check")

    list_parser = bead_subparsers.add_parser("list")
    list_parser.add_argument("--plain", action="store_true")
    claims_parser = bead_subparsers.add_parser("claims")
    claims_parser.add_argument("--plain", action="store_true")
    graph_parser = bead_subparsers.add_parser("graph")
    graph_parser.add_argument("--feature-root")
    graph_parser.add_argument("--output")

    handoff_parser = subparsers.add_parser("handoff")
    handoff_parser.add_argument("--root", dest="root", help=argparse.SUPPRESS)
    handoff_parser.add_argument("bead_id")
    handoff_parser.add_argument("--to", required=True)
    handoff_parser.add_argument("--summary", required=True)

    retry_parser = subparsers.add_parser("retry")
    retry_parser.add_argument("--root", dest="root", help=argparse.SUPPRESS)
    retry_parser.add_argument("bead_id")

    merge_parser = subparsers.add_parser("merge")
    merge_parser.add_argument("--root", dest="root", help=argparse.SUPPRESS)
    merge_parser.add_argument("bead_id")
    merge_parser.add_argument("--skip-rebase", action="store_true", help="Skip merge-main preflight")
    merge_parser.add_argument("--skip-tests", action="store_true", help="Skip test gate")

    summary_parser = subparsers.add_parser("summary")
    summary_parser.add_argument("--root", dest="root", help=argparse.SUPPRESS)
    summary_parser.add_argument("--feature-root")

    tui_parser = subparsers.add_parser("tui")
    tui_parser.add_argument("--root", dest="root", help=argparse.SUPPRESS)
    tui_parser.add_argument("--feature-root")
    tui_parser.add_argument("--refresh-seconds", type=_refresh_seconds, default=3)
    tui_parser.add_argument("--max-workers", type=int, default=1)

    telemetry_parser = subparsers.add_parser("telemetry")
    telemetry_parser.add_argument("--root", dest="root", help=argparse.SUPPRESS)
    telemetry_parser.add_argument("--days", type=int, default=7, help="Number of days to look back (default: 7)")
    telemetry_parser.add_argument("--feature-root", help="Filter by feature root bead ID")
    telemetry_parser.add_argument("--agent-type", help="Filter by agent type")
    telemetry_parser.add_argument("--status", help="Filter by bead status")
    telemetry_parser.add_argument("--json", action="store_true", dest="output_json", help="Output raw JSON")

    return parser


def _refresh_seconds(value: str) -> int:
    seconds = int(value)
    if seconds < 1:
        raise argparse.ArgumentTypeError("--refresh-seconds must be at least 1")
    return seconds


_RUNNER_CLASSES: dict[str, type] = {
    "codex": CodexAgentRunner,
    "claude": ClaudeCodeAgentRunner,
}


def make_services(root: Path, runner_backend: str | None = None) -> tuple[RepositoryStorage, Scheduler, PlanningService]:
    storage = RepositoryStorage(root)
    storage.initialize()
    config = load_config(root)
    backend_name = runner_backend or os.environ.get("ORCHESTRATOR_RUNNER") or config.default_runner
    runner_cls = _RUNNER_CLASSES.get(backend_name)
    if runner_cls is None:
        valid = ", ".join(sorted(config.backends.keys()))
        raise SystemExit(f"Unknown runner backend '{backend_name}'. Valid options: {valid}")
    backend_cfg = config.backend(backend_name)
    runner = runner_cls(config=config, backend=backend_cfg)
    worktrees = WorktreeManager(root, storage.worktrees_dir)
    scheduler = Scheduler(storage, runner, worktrees, config=config)
    planner = PlanningService(storage, runner)
    return storage, scheduler, planner


class CliSchedulerReporter(SchedulerReporter):
    def __init__(self, console: ConsoleReporter, max_workers: int = 1) -> None:
        self.console = console
        self.max_workers = max_workers
        self._spinner = None
        self._pool: SpinnerPool | None = None
        if max_workers > 1:
            self._pool = SpinnerPool(console, max_workers)
            self._pool.start()

    def stop(self) -> None:
        if self._pool is not None:
            self._pool.stop()

    def lease_expired(self, bead_id: str) -> None:
        self.console.warn(f"Lease expired for {bead_id}; requeued")

    def bead_started(self, bead: Bead) -> None:
        label = f"{bead.agent_type} {bead.bead_id} · {bead.title}"
        if self._pool is not None:
            self._pool.add(bead.bead_id, label)
        else:
            self._spinner = self.console.spin(label)
            self._spinner.__enter__()

    def worktree_ready(self, bead: Bead, branch_name: str, worktree_path: Path) -> None:
        self.console.detail(f"worktree {worktree_path} on {branch_name}")

    def bead_completed(self, bead: Bead, summary: str, created: list[Bead]) -> None:
        if self._pool is not None:
            from .console import GREEN
            self._pool.finish(bead.bead_id, "✓", GREEN, f"{bead.bead_id} completed")
        elif self._spinner:
            self._spinner.success(f"{bead.bead_id} completed")
            self._spinner = None
        self.console.detail(summary)
        for child in created:
            self.console.detail(f"created handoff bead {child.bead_id} ({child.agent_type})")

    def bead_deferred(self, bead: Bead, summary: str) -> None:
        self.console.warn(f"{bead.bead_id} deferred: {summary}")

    def bead_blocked(self, bead: Bead, summary: str) -> None:
        if self._pool is not None:
            from .console import YELLOW
            self._pool.finish(bead.bead_id, "!", YELLOW, f"{bead.bead_id} blocked")
        elif self._spinner:
            self._spinner.warn(f"{bead.bead_id} blocked")
            self._spinner = None
        self.console.warn(summary)

    def bead_failed(self, bead: Bead, summary: str) -> None:
        if self._pool is not None:
            from .console import RED
            self._pool.finish(bead.bead_id, "✗", RED, f"{bead.bead_id} failed")
        elif self._spinner:
            self._spinner.fail(f"{bead.bead_id} failed")
            self._spinner = None
        self.console.error(summary)


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
        )
        console.success(f"Created bead {bead.bead_id}")
        return 0

    if args.bead_command == "show":
        bead = storage.load_bead(storage.resolve_bead_id(args.bead_id))
        console.dump_json(bead.to_dict())
        return 0

    if args.bead_command == "list":
        beads = storage.list_beads()
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
    return 1


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
    bead.status = "ready"
    bead.block_reason = ""
    bead.lease = None
    storage.update_bead(bead, event="retried", summary="Bead requeued")
    console.success(f"Requeued bead {bead.bead_id}")
    return 0


def _get_diff_context(worktree_path: Path) -> str:
    proc = subprocess.run(
        ["git", "diff"],
        cwd=worktree_path,
        text=True,
        capture_output=True,
        check=False,
    )
    output = proc.stdout
    if len(output) > 4000:
        output = output[:4000] + "\n... (truncated)"
    return output


def _merge_conflict_attempt_cap_exceeded(
    storage: RepositoryStorage,
    feature_root_id: str,
    max_attempts: int,
) -> bool:
    all_conflict_beads = [
        b for b in storage.list_beads()
        if b.bead_type == "merge-conflict"
        and storage.feature_root_id_for(b) == feature_root_id
    ]
    return len(all_conflict_beads) >= max_attempts


def _emit_merge_conflict_bead(
    storage: RepositoryStorage,
    console: ConsoleReporter,
    feature_root: "Bead",
    feature_root_id: str,
    max_attempts: int,
    description: str,
    conflicted_files: list[str],
    retry_bead_id: str,
) -> None:
    if _merge_conflict_attempt_cap_exceeded(storage, feature_root_id, max_attempts):
        console.error(
            f"Corrective attempt cap ({max_attempts}) exceeded for feature {feature_root_id}. "
            "Manual operator intervention required."
        )
        return
    conflict_bead = storage.create_bead(
        title=f"Resolve merge conflicts for {feature_root.title or feature_root_id}",
        agent_type="developer",
        description=description,
        bead_type="merge-conflict",
        parent_id=feature_root_id,
        feature_root_id=feature_root_id,
        expected_files=conflicted_files,
        conflict_risks=f"Conflicted files: {', '.join(conflicted_files)}" if conflicted_files else "Test/merge failure",
    )
    console.error(
        f"Created merge-conflict bead {conflict_bead.bead_id}. "
        f"Resolve it then retry: orchestrator merge {retry_bead_id}"
    )


def command_merge(args: argparse.Namespace, storage: RepositoryStorage, console: ConsoleReporter) -> int:
    config = load_config(storage.root)
    bead = storage.load_bead(storage.resolve_bead_id(args.bead_id))
    feature_root = storage.feature_root_bead_for(bead) or bead
    feature_root_id = storage.feature_root_id_for(bead) or bead.bead_id
    branch_name = (
        feature_root.execution_branch_name
        or bead.execution_branch_name
        or feature_root.branch_name
        or bead.branch_name
    )
    if not branch_name:
        raise SystemExit(f"{bead.bead_id} has no feature branch to merge")

    # Block if an unresolved merge-conflict bead already exists for this feature root
    existing_conflict = next(
        (
            b for b in storage.list_beads()
            if b.bead_type == "merge-conflict"
            and storage.feature_root_id_for(b) == feature_root_id
            and b.status != "done"
        ),
        None,
    )
    if existing_conflict:
        console.error(
            f"Unresolved merge-conflict bead {existing_conflict.bead_id} exists for this feature. "
            f"Resolve it first, then retry: orchestrator merge {args.bead_id}"
        )
        return 1

    worktrees = WorktreeManager(storage.root, storage.worktrees_dir)
    worktree_path = Path(
        feature_root.execution_worktree_path or bead.execution_worktree_path or ""
    )

    # Preflight: merge main into the feature branch to detect conflicts early
    if not args.skip_rebase:
        if worktree_path and worktree_path.exists():
            with console.spin("Preflight: merging main into feature branch") as spinner:
                try:
                    worktrees.merge_main_into_branch(worktree_path)
                    spinner.success("Preflight passed")
                except GitError as exc:
                    spinner.fail("Preflight conflict detected")
                    conflicted = worktrees.conflicted_files(worktree_path)
                    diff_context = _get_diff_context(worktree_path)
                    try:
                        worktrees.abort_merge(worktree_path)
                    except GitError:
                        pass
                    conflict_desc = (
                        f"Merge conflict detected during preflight merge of main into {branch_name}.\n"
                        f"Conflicted files: {', '.join(conflicted) if conflicted else 'unknown'}\n\n"
                        f"Git error: {exc}\n\n"
                        f"Diff context:\n{diff_context}"
                    )
                    _emit_merge_conflict_bead(
                        storage, console, feature_root, feature_root_id,
                        config.scheduler.max_corrective_attempts,
                        conflict_desc, conflicted, args.bead_id,
                    )
                    return 1

    # Test gate
    if not args.skip_tests:
        test_command = config.common.test_command
        if not test_command:
            console.warn("No test_command configured; skipping test gate")
        else:
            console.info(f"Running test gate: {test_command}")
            cwd = worktree_path if worktree_path and worktree_path.exists() else storage.root
            output_lines: list[str] = []

            try:
                test_proc = subprocess.Popen(
                    test_command,
                    shell=True,
                    cwd=cwd,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                )

                def _stream_output() -> None:
                    assert test_proc.stdout is not None
                    for line in test_proc.stdout:
                        output_lines.append(line)
                        with console._lock:
                            console.stream.write(line)
                            console.stream.flush()

                reader = threading.Thread(target=_stream_output, daemon=True)
                reader.start()
                try:
                    test_proc.wait(timeout=config.common.test_timeout_seconds)
                except subprocess.TimeoutExpired:
                    test_proc.kill()
                    test_proc.wait()
                    reader.join(timeout=5)
                    console.error(f"Test gate timed out after {config.common.test_timeout_seconds}s")
                    _emit_merge_conflict_bead(
                        storage, console, feature_root, feature_root_id,
                        config.scheduler.max_corrective_attempts,
                        (
                            f"Test gate timed out for {branch_name}.\n\n"
                            f"Command: {test_command}\n"
                            f"Timeout: {config.common.test_timeout_seconds}s"
                        ),
                        [], args.bead_id,
                    )
                    return 1
                reader.join(timeout=5)

                if test_proc.returncode != 0:
                    console.error("Test gate failed")
                    failure_output = "".join(output_lines).strip()
                    if len(failure_output) > 4000:
                        failure_output = failure_output[:4000] + "\n... (truncated)"
                    _emit_merge_conflict_bead(
                        storage, console, feature_root, feature_root_id,
                        config.scheduler.max_corrective_attempts,
                        (
                            f"Test gate failed for {branch_name}.\n\n"
                            f"Command: {test_command}\n\n"
                            f"Output:\n{failure_output}"
                        ),
                        [], args.bead_id,
                    )
                    return 1
                console.success("Test gate passed")
            except OSError as exc:
                console.error(f"Test gate failed to start: {exc}")
                _emit_merge_conflict_bead(
                    storage, console, feature_root, feature_root_id,
                    config.scheduler.max_corrective_attempts,
                    (
                        f"Test gate failed to start for {branch_name}.\n\n"
                        f"Command: {test_command}\n\n"
                        f"Error: {exc}"
                    ),
                    [], args.bead_id,
                )
                return 1

    with console.spin(f"Merging {branch_name}") as spinner:
        worktrees.merge_branch(branch_name)
        spinner.success(f"Merged {branch_name}")
    return 0


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
    from .tui import run_tui

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


def command_run(args: argparse.Namespace, scheduler: Scheduler, console: ConsoleReporter) -> int:
    reporter = CliSchedulerReporter(console, max_workers=args.max_workers)
    # Use dicts keyed by bead ID so each bead appears at most once (last event wins).
    started: dict[str, str] = {}
    completed: dict[str, str] = {}
    blocked: dict[str, str] = {}
    correctives_created: dict[str, str] = {}
    deferred_count = 0
    console.section("Scheduler")
    feature_root_id = None
    if args.feature_root:
        try:
            feature_root_id = scheduler.storage.resolve_bead_id(args.feature_root)
        except ValueError as exc:
            console.error(str(exc))
            return 1
    scope = f", feature_root={feature_root_id}" if feature_root_id else ""
    console.info(f"Starting scheduler loop with max_workers={args.max_workers}{scope}")
    try:
        while True:
            result = scheduler.run_once(
                max_workers=args.max_workers,
                feature_root_id=feature_root_id,
                reporter=reporter,
            )
            for bead_id in result.started:
                started[bead_id] = bead_id
            for bead_id in result.completed:
                completed[bead_id] = bead_id
            for bead_id in result.blocked:
                blocked[bead_id] = bead_id
            for bead_id in result.correctives_created:
                correctives_created[bead_id] = bead_id
            deferred_count += len(result.deferred)
            if args.once or (not result.started and not result.correctives_created):
                break
    finally:
        reporter.stop()

    # Build final-state counts from storage.
    all_beads = scheduler.storage.list_beads()
    if feature_root_id:
        all_beads = [b for b in all_beads if b.feature_root_id == feature_root_id]
    final_counts: dict[str, int] = {}
    for bead in all_beads:
        final_counts[bead.status] = final_counts.get(bead.status, 0) + 1

    if not started:
        console.warn("No ready beads to run")
    else:
        console.success(
            f"Cycle summary: started {len(started)}, completed {len(completed)}, "
            f"blocked {len(blocked)}, deferred {deferred_count} (total cycles)"
        )

    done_count = final_counts.get("done", 0)
    blocked_count = final_counts.get("blocked", 0)
    ready_count = final_counts.get("ready", 0)
    console.info(f"Final state: {done_count} done, {blocked_count} blocked, {ready_count} ready")

    summary = {
        "started": sorted(started.keys()),
        "completed": sorted(completed.keys()),
        "blocked": sorted(blocked.keys()),
        "correctives_created": sorted(correctives_created.keys()),
        "deferred_count": deferred_count,
        "final_state": final_counts,
    }
    console.dump_json(summary)
    return 0


def _filter_beads_by_days(beads: list[Bead], days: int) -> list[Bead]:
    """Return beads whose first execution_history entry falls within the last `days` days."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    result = []
    for bead in beads:
        if not bead.execution_history:
            continue
        ts = bead.execution_history[0].timestamp
        try:
            dt = datetime.fromisoformat(ts)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt >= cutoff:
                result.append(bead)
        except ValueError:
            pass
    return result


def _bead_wall_clock_seconds(bead: Bead) -> float | None:
    """Compute total wall-clock seconds from started->completed/blocked/failed pairs.

    Skips incomplete entries (no terminal event after a started event).
    """
    TERMINAL_EVENTS = {"completed", "blocked", "failed"}
    started_ts: str | None = None
    total: float = 0.0
    found_any = False
    for record in bead.execution_history:
        if record.event == "started":
            started_ts = record.timestamp
        elif record.event in TERMINAL_EVENTS and started_ts is not None:
            try:
                start = datetime.fromisoformat(started_ts)
                end = datetime.fromisoformat(record.timestamp)
                if start.tzinfo is None:
                    start = start.replace(tzinfo=timezone.utc)
                if end.tzinfo is None:
                    end = end.replace(tzinfo=timezone.utc)
                total += (end - start).total_seconds()
                found_any = True
            except ValueError:
                pass
            started_ts = None
    return total if found_any else None


def _bead_turns(storage: RepositoryStorage, bead_id: str) -> int | None:
    """Load the total num_turns from all telemetry artifact files for a bead."""
    bead_telemetry_dir = storage.telemetry_dir / bead_id
    if not bead_telemetry_dir.exists():
        return None
    total = 0
    found_any = False
    for artifact_path in sorted(bead_telemetry_dir.glob("*.json")):
        try:
            data = json.loads(artifact_path.read_text(encoding="utf-8"))
            turns = data.get("metrics", {}).get("num_turns")
            if turns is not None:
                total += int(turns)
                found_any = True
        except (json.JSONDecodeError, OSError, ValueError):
            pass
    return total if found_any else None


def _percentile(values: list[float], p: float) -> float | None:
    """Compute the p-th percentile of a sorted list of values (linear interpolation)."""
    if not values:
        return None
    sorted_vals = sorted(values)
    k = (len(sorted_vals) - 1) * p / 100.0
    lo = int(k)
    hi = lo + 1
    if hi >= len(sorted_vals):
        return sorted_vals[lo]
    frac = k - lo
    return sorted_vals[lo] + frac * (sorted_vals[hi] - sorted_vals[lo])


def aggregate_telemetry(
    beads: list[Bead],
    storage: RepositoryStorage,
    transient_patterns: tuple[str, ...] = (),
) -> dict:
    """Compute aggregate telemetry metrics from the given list of beads.

    Returns a structured dict suitable for both table and JSON output modes.
    """
    by_status: Counter[str] = Counter()
    by_agent_type: Counter[str] = Counter()
    wall_clock_values: list[float] = []
    turns_values: list[int] = []
    retry_count = 0
    corrective_count = 0
    merge_conflict_count = 0
    timeout_block_count = 0
    transient_block_count = 0

    for bead in beads:
        by_status[bead.status] += 1
        by_agent_type[bead.agent_type] += 1

        wc = _bead_wall_clock_seconds(bead)
        if wc is not None:
            wall_clock_values.append(wc)

        turns = _bead_turns(storage, bead.bead_id)
        if turns is not None:
            turns_values.append(turns)

        if bead.retries > 0:
            retry_count += 1

        if bead.bead_id.endswith("-corrective"):
            corrective_count += 1

        if bead.bead_type == "merge-conflict":
            merge_conflict_count += 1

        if bead.status == "blocked":
            reason_lower = (bead.block_reason or "").lower()
            if "timeout" in reason_lower or "timed out" in reason_lower:
                timeout_block_count += 1
            if transient_patterns and any(p in reason_lower for p in transient_patterns):
                transient_block_count += 1

    total = len(beads)
    avg_wc = sum(wall_clock_values) / len(wall_clock_values) if wall_clock_values else None
    p95_wc = _percentile(wall_clock_values, 95)
    avg_turns = sum(turns_values) / len(turns_values) if turns_values else None

    return {
        "total_beads": total,
        "by_status": dict(by_status),
        "by_agent_type": dict(by_agent_type),
        "avg_wall_clock_seconds": round(avg_wc, 1) if avg_wc is not None else None,
        "p95_wall_clock_seconds": round(p95_wc, 1) if p95_wc is not None else None,
        "avg_turns": round(avg_turns, 1) if avg_turns is not None else None,
        "retry_rate": round(retry_count / total, 3) if total > 0 else None,
        "corrective_bead_count": corrective_count,
        "merge_conflict_bead_count": merge_conflict_count,
        "timeout_block_count": timeout_block_count,
        "transient_block_count": transient_block_count,
    }


def _format_telemetry_table(data: dict, console: ConsoleReporter) -> None:
    """Render aggregated telemetry as a human-readable plain-text report."""
    filters = data["filters"]
    agg = data["aggregates"]
    lines: list[str] = []

    header = f"Telemetry report  (last {filters['days']} days)"
    if filters.get("feature_root"):
        header += f"  |  feature_root={filters['feature_root']}"
    if filters.get("agent_type"):
        header += f"  |  agent_type={filters['agent_type']}"
    if filters.get("status"):
        header += f"  |  status={filters['status']}"
    lines.append(header)

    if agg["total_beads"] == 0:
        lines.append("No beads found.")
        console.emit("\n".join(lines))
        return

    lines.append(f"Total beads: {agg['total_beads']}")
    lines.append("")

    if agg["by_status"]:
        lines.append("By status:")
        for status, count in sorted(agg["by_status"].items()):
            lines.append(f"  {status:<20} {count}")
        lines.append("")

    if agg["by_agent_type"]:
        lines.append("By agent type:")
        for agent_type, count in sorted(agg["by_agent_type"].items()):
            lines.append(f"  {agent_type:<20} {count}")
        lines.append("")

    feature_roots = data.get("feature_roots") or []
    if feature_roots and not filters.get("feature_root"):
        lines.append("By feature root:")
        for fr in feature_roots:
            frid = fr["feature_root_id"]
            title = fr.get("title") or ""
            truncated = (title[:37] + "...") if len(title) > 40 else title
            count = fr["bead_count"]
            lines.append(f"  {frid}  {truncated:<40}  {count}")
        lines.append("")

    wc_avg = agg["avg_wall_clock_seconds"]
    wc_p95 = agg["p95_wall_clock_seconds"]
    avg_turns = agg["avg_turns"]
    retry_rate = agg["retry_rate"]

    lines.append(f"Avg wall-clock    : {f'{wc_avg}s' if wc_avg is not None else 'N/A'}")
    lines.append(f"P95 wall-clock    : {f'{wc_p95}s' if wc_p95 is not None else 'N/A'}")
    lines.append(f"Avg turns         : {avg_turns if avg_turns is not None else 'N/A'}")
    lines.append(f"Retry rate        : {f'{retry_rate:.1%}' if retry_rate is not None else 'N/A'}")
    lines.append(f"Corrective beads  : {agg['corrective_bead_count']}")
    lines.append(f"Merge-conflict    : {agg['merge_conflict_bead_count']}")
    lines.append(f"Timeout blocks    : {agg['timeout_block_count']}")
    lines.append(f"Transient blocks  : {agg['transient_block_count']}")

    console.emit("\n".join(lines))


def command_telemetry(args: argparse.Namespace, storage: RepositoryStorage, console: ConsoleReporter) -> int:
    beads = storage.list_beads()

    beads = _filter_beads_by_days(beads, args.days)

    if args.feature_root:
        try:
            feature_root_id = storage.resolve_bead_id(args.feature_root)
        except ValueError as exc:
            console.error(str(exc))
            return 1
        beads = [b for b in beads if storage.feature_root_id_for(b) == feature_root_id]

    if args.agent_type:
        beads = [b for b in beads if b.agent_type == args.agent_type]

    if args.status:
        beads = [b for b in beads if b.status == args.status]

    config = load_config(storage.root)
    agg = aggregate_telemetry(beads, storage, config.scheduler.transient_block_patterns)

    feature_root_counts: Counter[str] = Counter()
    feature_root_titles: dict[str, str] = {}
    for b in beads:
        frid = b.feature_root_id or b.bead_id
        feature_root_counts[frid] += 1
        if frid not in feature_root_titles:
            try:
                fr_bead = storage.load_bead(frid)
                feature_root_titles[frid] = fr_bead.title or ""
            except Exception:
                feature_root_titles[frid] = ""

    result = {
        "filters": {
            "days": args.days,
            "feature_root": args.feature_root or None,
            "agent_type": args.agent_type or None,
            "status": args.status or None,
        },
        "bead_count": len(beads),
        "aggregates": agg,
        "feature_roots": [
            {
                "feature_root_id": frid,
                "title": feature_root_titles.get(frid, ""),
                "bead_count": count,
            }
            for frid, count in sorted(feature_root_counts.items())
        ],
        "beads": [
            {
                "bead_id": b.bead_id,
                "title": b.title,
                "agent_type": b.agent_type,
                "status": b.status,
                "feature_root_id": b.feature_root_id,
                "wall_clock_seconds": _bead_wall_clock_seconds(b),
            }
            for b in beads
        ],
    }

    if getattr(args, "output_json", False):
        console.dump_json(result)
    else:
        _format_telemetry_table(result, console)

    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    root = Path(args.root or ".").resolve()
    storage, scheduler, planner = make_services(root, runner_backend=args.runner)
    console = ConsoleReporter()

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
