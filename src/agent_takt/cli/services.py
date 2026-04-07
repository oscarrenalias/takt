from __future__ import annotations

import os
from pathlib import Path

from ..config import load_config
from ..console import ConsoleReporter
from ..gitutils import WorktreeManager
from ..models import Bead
from ..planner import PlanningService
from ..runner import ClaudeCodeAgentRunner, CodexAgentRunner
from ..scheduler import Scheduler
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


