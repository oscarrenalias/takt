from __future__ import annotations

import os
from pathlib import Path

from ..config import load_config
from ..console import ConsoleReporter, SpinnerPool
from ..gitutils import WorktreeManager
from ..models import Bead
from ..planner import PlanningService
from ..runner import ClaudeCodeAgentRunner, CodexAgentRunner
from ..scheduler import Scheduler, SchedulerReporter
from ..storage import RepositoryStorage


OPERATOR_STATUS_TRANSITIONS: dict[str, frozenset[str]] = {
    "ready": frozenset({"open", "blocked", "handed_off"}),
    "blocked": frozenset({"open", "ready", "in_progress", "handed_off"}),
    "done": frozenset({"ready", "in_progress", "handed_off"}),
}

_RUNNER_CLASSES: dict[str, type] = {
    "codex": CodexAgentRunner,
    "claude": ClaudeCodeAgentRunner,
}


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


def make_services(root: Path, runner_backend: str | None = None) -> tuple[RepositoryStorage, Scheduler, PlanningService]:
    storage = RepositoryStorage(root)
    storage.initialize()
    config = load_config(root)
    backend_name = (
        runner_backend
        or os.environ.get("AGENT_TAKT_RUNNER")
        or os.environ.get("ORCHESTRATOR_RUNNER")  # legacy fallback
        or config.default_runner
    )
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
            from ..console import GREEN
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
            from ..console import YELLOW
            self._pool.finish(bead.bead_id, "!", YELLOW, f"{bead.bead_id} blocked")
        elif self._spinner:
            self._spinner.warn(f"{bead.bead_id} blocked")
            self._spinner = None
        self.console.warn(summary)

    def bead_failed(self, bead: Bead, summary: str) -> None:
        if self._pool is not None:
            from ..console import RED
            self._pool.finish(bead.bead_id, "✗", RED, f"{bead.bead_id} failed")
        elif self._spinner:
            self._spinner.fail(f"{bead.bead_id} failed")
            self._spinner = None
        self.console.error(summary)
