from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..gitutils import GitError, WorktreeManager
from ..models import (
    BEAD_BLOCKED,
    BEAD_DONE,
    BEAD_IN_PROGRESS,
    ExecutionRecord,
    HandoffSummary,
    Lease,
    MUTATING_AGENTS,
    AgentRunResult,
    Bead,
    SchedulerResult,
    utc_now,
)
from ..config import OrchestratorConfig
from ..prompts import load_guardrail_template
from ..runner import AgentRunner
from ..skills import prepare_isolated_execution_root
from ..storage import RepositoryStorage
from .finalize import BeadFinalizer
from .followups import FollowupManager
from .reporter import SchedulerReporter


class BeadExecutor:
    """Handles all execution-path logic for a single bead run."""

    def __init__(
        self,
        storage: RepositoryStorage,
        runner: AgentRunner,
        worktrees: WorktreeManager,
        config: OrchestratorConfig,
    ) -> None:
        self.storage = storage
        self.runner = runner
        self.worktrees = worktrees
        self.config = config

        self.lease_timeout_minutes = config.scheduler.lease_timeout_minutes
        self._finalizer = BeadFinalizer(storage, worktrees, config, self)
        self._followups = FollowupManager(storage, config)

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def process(
        self,
        bead: Bead,
        result: SchedulerResult,
        *,
        reporter: SchedulerReporter | None = None,
    ) -> None:
        workdir = self.storage.root
        runner_workdir = Path(workdir)
        execution_env: dict[str, str] | None = None
        feature_root_id = self.storage.feature_root_id_for(bead)
        self._followups._populate_shared_followup_touched_files(bead)
        bead.status = BEAD_IN_PROGRESS
        bead.lease = Lease(
            owner=f"{bead.agent_type}:{bead.bead_id}",
            expires_at=(
                datetime.now(timezone.utc) + timedelta(minutes=self.lease_timeout_minutes)
            ).isoformat(),
        )
        if feature_root_id:
            bead.feature_root_id = feature_root_id
            bead.execution_branch_name = (
                bead.execution_branch_name
                or self.storage.default_execution_branch_name(feature_root_id)
            )
            bead.execution_worktree_path = bead.execution_worktree_path or str(
                self.storage.worktrees_dir / feature_root_id
            )
        if reporter:
            reporter.bead_started(bead)
        if feature_root_id:
            branch_name = (
                bead.execution_branch_name
                or self.storage.default_execution_branch_name(feature_root_id)
            )
            try:
                worktree_path = self.worktrees.ensure_worktree(feature_root_id, branch_name)
            except GitError as exc:
                bead.status = BEAD_BLOCKED
                bead.lease = None
                bead.block_reason = str(exc)
                self.storage.update_bead(bead, event="blocked", summary=str(exc))
                result.blocked.append(bead.bead_id)
                if reporter:
                    reporter.bead_blocked(bead, str(exc))
                return
            bead.branch_name = branch_name
            bead.execution_branch_name = branch_name
            bead.execution_worktree_path = str(worktree_path)
            if bead.agent_type in MUTATING_AGENTS:
                bead.worktree_path = str(worktree_path)
            feature_root = self.storage.feature_root_bead_for(bead)
            if feature_root is not None and feature_root.bead_id != bead.bead_id:
                feature_root.execution_branch_name = branch_name
                feature_root.execution_worktree_path = str(worktree_path)
                self.storage.save_bead(feature_root)
            workdir = worktree_path
            runner_workdir = Path(worktree_path)
            if reporter:
                reporter.worktree_ready(bead, branch_name, worktree_path)
        elif bead.agent_type in MUTATING_AGENTS:
            bead.status = BEAD_BLOCKED
            bead.lease = None
            bead.block_reason = "Mutating bead has no feature_root_id"
            self.storage.update_bead(bead, event="blocked", summary=bead.block_reason)
            result.blocked.append(bead.bead_id)
            if reporter:
                reporter.bead_blocked(bead, bead.block_reason)
            return
        try:
            exec_root, skill_metadata = prepare_isolated_execution_root(
                orchestrator_state_dir=self.storage.state_dir,
                catalog_repo_root=self.storage.root,
                workspace_repo_root=runner_workdir,
                bead=bead,
                config=self.config,
                runner_backend=self.runner.backend_name,
            )
            bead.metadata.update(skill_metadata)
            bead.execution_history.append(
                ExecutionRecord(
                    timestamp=utc_now(),
                    event="skills_loaded",
                    agent_type=bead.agent_type,
                    summary=f"Loaded {len(skill_metadata.get('loaded_skills', []))} skill(s) for isolated execution",
                    details={"loaded_skills": list(skill_metadata.get("loaded_skills", []))},
                )
            )
            runner_workdir = exec_root / "repo"
            execution_env = None
        except Exception as exc:
            bead.metadata["skills_warning"] = f"Skill isolation unavailable: {exc}"
            bead.execution_history.append(
                ExecutionRecord(
                    timestamp=utc_now(),
                    event="skills_isolation_unavailable",
                    agent_type="scheduler",
                    summary=str(exc),
                )
            )
        self.storage.update_bead(bead, event="started", summary="Worker started")
        context_paths = self.storage.linked_context_paths(bead)
        dep_handoffs = self._load_dep_handoffs(bead)
        try:
            guardrail_path, guardrail_text = load_guardrail_template(
                bead.agent_type,
                root=runner_workdir,
                templates_dir=self.config.templates_dir,
                agent_types=self.config.agent_types,
            )
            self.storage.record_guardrail_context(
                bead,
                template_path=guardrail_path,
                template_text=guardrail_text,
                prompt_context=self._worker_prompt_context(bead),
            )
            agent_result = self.runner.run_bead(
                bead,
                workdir=runner_workdir,
                context_paths=context_paths,
                execution_env=execution_env,
                dep_handoffs=dep_handoffs,
            )
        except Exception as exc:
            agent_result = AgentRunResult(
                outcome="failed",
                summary=f"Worker execution failed: {exc}",
                block_reason=str(exc),
            )
        if bead.worktree_path:
            try:
                touched = self.worktrees.changed_files(Path(bead.worktree_path))
            except GitError as exc:
                touched = []
                if not agent_result.block_reason:
                    agent_result.block_reason = str(exc)
            agent_result.touched_files = sorted(
                dict.fromkeys([*agent_result.touched_files, *touched])
            )
            agent_result.changed_files = sorted(
                dict.fromkeys([*agent_result.changed_files, *agent_result.touched_files])
            )
        self._finalize(bead, agent_result, result, reporter=reporter)

    # ------------------------------------------------------------------
    # Finalization (delegated to BeadFinalizer)
    # ------------------------------------------------------------------

    def _finalize(
        self,
        bead: Bead,
        agent_result: AgentRunResult,
        result: SchedulerResult,
        *,
        reporter: SchedulerReporter | None = None,
    ) -> None:
        self._finalizer.finalize(bead, agent_result, result, reporter=reporter)

    # ------------------------------------------------------------------
    # Prompt / context helpers
    # ------------------------------------------------------------------

    def _worker_prompt_context(self, bead: Bead) -> dict[str, object]:
        return {
            "bead_id": bead.bead_id,
            "feature_root_id": bead.feature_root_id,
            "title": bead.title,
            "agent_type": bead.agent_type,
            "description": bead.description,
            "status": bead.status,
            "acceptance_criteria": list(bead.acceptance_criteria),
            "dependencies": list(bead.dependencies),
            "linked_docs": list(bead.linked_docs),
            "execution_branch_name": bead.execution_branch_name,
            "execution_worktree_path": bead.execution_worktree_path,
            "expected_files": list(bead.expected_files),
            "expected_globs": list(bead.expected_globs),
            "touched_files": list(bead.touched_files),
            "conflict_risks": bead.conflict_risks,
            "handoff_summary": bead.handoff_summary.__dict__,
        }

    def _load_dep_handoffs(self, bead: Bead) -> list[HandoffSummary]:
        """Load handoff summaries from done dependency beads for tester/review prompts."""
        if bead.agent_type not in {"review", "tester"}:
            return []
        handoffs: list[HandoffSummary] = []
        for dep_id in bead.dependencies:
            try:
                dep_bead = self.storage.load_bead(dep_id)
            except Exception:
                continue
            if dep_bead.status == BEAD_DONE:
                handoffs.append(dep_bead.handoff_summary)
        return handoffs

