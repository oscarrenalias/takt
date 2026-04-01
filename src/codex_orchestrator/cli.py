from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from dataclasses import asdict
from pathlib import Path

from .config import load_config
from .console import ConsoleReporter, SpinnerPool
from .gitutils import WorktreeManager
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

    summary_parser = subparsers.add_parser("summary")
    summary_parser.add_argument("--root", dest="root", help=argparse.SUPPRESS)
    summary_parser.add_argument("--feature-root")

    tui_parser = subparsers.add_parser("tui")
    tui_parser.add_argument("--root", dest="root", help=argparse.SUPPRESS)
    tui_parser.add_argument("--feature-root")
    tui_parser.add_argument("--refresh-seconds", type=_refresh_seconds, default=3)
    tui_parser.add_argument("--max-workers", type=int, default=1)

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


def command_merge(args: argparse.Namespace, storage: RepositoryStorage, console: ConsoleReporter) -> int:
    bead = storage.load_bead(storage.resolve_bead_id(args.bead_id))
    feature_root = storage.feature_root_bead_for(bead) or bead
    branch_name = (
        feature_root.execution_branch_name
        or bead.execution_branch_name
        or feature_root.branch_name
        or bead.branch_name
    )
    if not branch_name:
        raise SystemExit(f"{bead.bead_id} has no feature branch to merge")
    worktrees = WorktreeManager(storage.root, storage.worktrees_dir)
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
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
