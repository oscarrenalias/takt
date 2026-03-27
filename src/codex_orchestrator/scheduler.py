from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from fnmatch import fnmatch
from pathlib import Path
from typing import Protocol

from .gitutils import GitError, WorktreeManager
from .models import (
    BEAD_BLOCKED,
    BEAD_DONE,
    BEAD_IN_PROGRESS,
    BEAD_READY,
    ExecutionRecord,
    HandoffSummary,
    Lease,
    MUTATING_AGENTS,
    AgentRunResult,
    Bead,
    SchedulerResult,
    utc_now,
)
from .prompts import BUILT_IN_AGENT_TYPES, load_guardrail_template
from .runner import AgentRunner
from .skills import prepare_isolated_execution_root
from .storage import RepositoryStorage


FOLLOWUP_SUFFIXES = {
    "tester": "test",
    "documentation": "docs",
    "review": "review",
}
CORRECTIVE_SUFFIX = "corrective"
MAX_CORRECTIVE_ATTEMPTS = 2
TRANSIENT_BLOCK_PATTERNS = (
    "high demand",
    "internal server error",
    "timeout",
    "timed out",
    "connection reset",
    "connection refused",
    "temporarily unavailable",
    "service unavailable",
    "missing bearer",
    "unauthorized",
)
RUNNABLE_REASSIGN_AGENTS = set(BUILT_IN_AGENT_TYPES)
FOLLOWUP_AGENT_BY_SUFFIX = {
    f"-{FOLLOWUP_SUFFIXES['tester']}": "tester",
    f"-{FOLLOWUP_SUFFIXES['documentation']}": "documentation",
    f"-{FOLLOWUP_SUFFIXES['review']}": "review",
}


class Scheduler:
    def __init__(self, storage: RepositoryStorage, runner: AgentRunner, worktrees: WorktreeManager) -> None:
        self.storage = storage
        self.runner = runner
        self.worktrees = worktrees

    def expire_stale_leases(self, *, now: datetime | None = None) -> list[str]:
        now = now or datetime.now(timezone.utc)
        expired: list[str] = []
        for bead in self.storage.list_beads():
            if bead.lease is None:
                continue
            if datetime.fromisoformat(bead.lease.expires_at) <= now:
                bead.lease = None
                if bead.status == BEAD_IN_PROGRESS:
                    bead.status = BEAD_READY
                bead.execution_history.append(
                    ExecutionRecord(
                        timestamp=utc_now(),
                        event="lease_expired",
                        agent_type="scheduler",
                        summary="Lease expired and bead was requeued",
                    )
                )
                self.storage.save_bead(bead)
                expired.append(bead.bead_id)
        return expired

    def run_once(
        self,
        *,
        max_workers: int = 1,
        feature_root_id: str | None = None,
        reporter: "SchedulerReporter | None" = None,
    ) -> SchedulerResult:
        result = SchedulerResult()
        expired = self.expire_stale_leases()
        if reporter:
            for bead_id in expired:
                reporter.lease_expired(bead_id)
        self._reevaluate_blocked(feature_root_id=feature_root_id, reporter=reporter)
        ready = self.storage.ready_beads()
        if feature_root_id:
            ready = [
                bead for bead in ready
                if self.storage.feature_root_id_for(bead) == feature_root_id
            ]
        selected: list[Bead] = []
        active = self.storage.active_beads()
        for bead in ready:
            conflict_reason = self._find_conflict_reason(bead, active + selected)
            if conflict_reason:
                bead.block_reason = conflict_reason
                self.storage.update_bead(bead, event="deferred", summary=conflict_reason)
                result.deferred.append(bead.bead_id)
                if reporter:
                    reporter.bead_deferred(bead, conflict_reason)
                continue
            if len(selected) >= max_workers:
                continue
            selected.append(bead)
        result.started.extend(bead.bead_id for bead in selected)
        if len(selected) <= 1:
            for bead in selected:
                self._process(bead, result, reporter=reporter)
            return result
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(self._process, bead, result, reporter=reporter) for bead in selected]
            for future in futures:
                future.result()
        return result

    def _reevaluate_blocked(
        self,
        *,
        feature_root_id: str | None,
        reporter: "SchedulerReporter | None" = None,
    ) -> None:
        for bead in self.storage.list_beads():
            if bead.status != BEAD_BLOCKED or bead.lease is not None:
                continue
            if feature_root_id and self.storage.feature_root_id_for(bead) != feature_root_id:
                continue
            repaired = self._repair_invalid_worker_agent_type(bead)
            if repaired:
                self.storage.update_bead(
                    bead,
                    event="agent_type_repaired",
                    summary=f"Repaired unrunnable agent type to {bead.agent_type}",
                )
                if reporter:
                    reporter.bead_deferred(bead, f"Repaired agent type to {bead.agent_type}")
            reason = bead.block_reason.lower()
            if reason and any(pattern in reason for pattern in TRANSIENT_BLOCK_PATTERNS):
                bead.status = BEAD_READY
                bead.block_reason = ""
                self.storage.update_bead(
                    bead,
                    event="retried",
                    summary="Requeued blocked bead after transient infrastructure/auth error",
                )
                if reporter:
                    reporter.bead_deferred(bead, "Requeued blocked bead after transient failure")
                continue
            corrective_children = self._corrective_children(bead)
            open_corrective = next(
                (child for child in corrective_children if child.status in {BEAD_READY, BEAD_IN_PROGRESS}),
                None,
            )
            if open_corrective is not None:
                continue
            latest_done = next((child for child in reversed(corrective_children) if child.status == BEAD_DONE), None)
            if latest_done is not None:
                if not self._already_retried_after_corrective(bead, latest_done):
                    bead.status = BEAD_READY
                    bead.block_reason = ""
                    bead.metadata["last_corrective_retry_source"] = latest_done.bead_id
                    bead.metadata["last_corrective_retry_commit"] = str(latest_done.metadata.get("last_commit", ""))
                    self.storage.update_bead(
                        bead,
                        event="retried",
                        summary=f"Requeued blocked bead after corrective bead {latest_done.bead_id} completed",
                    )
                    if reporter:
                        reporter.bead_deferred(
                            bead,
                            f"Requeued after corrective bead {latest_done.bead_id} completed",
                        )
                    continue
                if len(corrective_children) < MAX_CORRECTIVE_ATTEMPTS and self._can_plan_corrective(bead):
                    self._create_corrective_bead(bead, reporter=reporter)
                else:
                    self._escalate_blocked_bead(bead, reporter=reporter)
                continue
            if not corrective_children and self._can_plan_corrective(bead):
                self._create_corrective_bead(bead, reporter=reporter)
                continue
            if len(corrective_children) >= MAX_CORRECTIVE_ATTEMPTS:
                self._escalate_blocked_bead(bead, reporter=reporter)

    def _already_retried_after_corrective(self, bead: Bead, corrective: Bead) -> bool:
        retry_source = str(bead.metadata.get("last_corrective_retry_source", "")).strip()
        retry_commit = str(bead.metadata.get("last_corrective_retry_commit", "")).strip()
        corrective_commit = str(corrective.metadata.get("last_commit", "")).strip()
        if retry_source == corrective.bead_id and retry_commit == corrective_commit:
            return True
        for record in reversed(bead.execution_history):
            if record.event != "retried":
                continue
            if corrective.bead_id in record.summary:
                return True
        return False

    def _corrective_children(self, bead: Bead) -> list[Bead]:
        children = [
            child for child in self.storage.list_beads()
            if child.parent_id == bead.bead_id and child.metadata.get("auto_corrective_for") == bead.bead_id
        ]
        return sorted(children, key=lambda item: item.bead_id)

    def _can_plan_corrective(self, bead: Bead) -> bool:
        if bead.metadata.get("auto_corrective_for"):
            return False
        if "-corrective" in bead.bead_id:
            return False
        current = bead
        while current.parent_id:
            parent = self.storage.load_bead(current.parent_id)
            if parent.metadata.get("auto_corrective_for"):
                return False
            if "-corrective" in parent.bead_id:
                return False
            current = parent
        return True

    def _repair_invalid_worker_agent_type(self, bead: Bead) -> bool:
        if bead.agent_type in RUNNABLE_REASSIGN_AGENTS:
            return False
        candidates: list[str] = []
        next_agent = bead.handoff_summary.next_agent.strip()
        if next_agent in RUNNABLE_REASSIGN_AGENTS:
            candidates.append(next_agent)
        previous = str(bead.metadata.get("reassigned_from_agent_type", "")).strip()
        if previous in RUNNABLE_REASSIGN_AGENTS:
            candidates.append(previous)
        for suffix, agent in FOLLOWUP_AGENT_BY_SUFFIX.items():
            if bead.bead_id.endswith(suffix):
                candidates.append(agent)
                break
        if bead.parent_id:
            parent = self.storage.load_bead(bead.parent_id)
            if parent.agent_type in RUNNABLE_REASSIGN_AGENTS:
                candidates.append(parent.agent_type)
        candidates.append("developer")
        for candidate in candidates:
            if candidate in RUNNABLE_REASSIGN_AGENTS:
                bead.agent_type = candidate
                return True
        return False

    def _find_corrective_child(self, bead: Bead) -> Bead | None:
        recorded = bead.metadata.get("auto_corrective_bead_id", "")
        if recorded:
            path = self.storage.bead_path(recorded)
            if path.exists():
                return self.storage.load_bead(recorded)
        expected = f"{bead.bead_id}-{CORRECTIVE_SUFFIX}"
        path = self.storage.bead_path(expected)
        if path.exists():
            return self.storage.load_bead(expected)
        for candidate in self.storage.list_beads():
            if candidate.parent_id != bead.bead_id:
                continue
            if candidate.metadata.get("auto_corrective_for") == bead.bead_id:
                return candidate
        return None

    def _create_corrective_bead(self, bead: Bead, *, reporter: "SchedulerReporter | None" = None) -> Bead:
        next_agent = bead.handoff_summary.next_agent.strip()
        corrective_agent = next_agent if next_agent in MUTATING_AGENTS else "developer"
        description_parts = []
        if bead.block_reason:
            description_parts.append(f"Blocked reason: {bead.block_reason}")
        if bead.handoff_summary.remaining:
            description_parts.append(f"Remaining work: {bead.handoff_summary.remaining}")
        if not description_parts:
            description_parts.append("Investigate blocked bead and implement corrective fix to unblock parent bead.")
        corrective_id = self.storage.allocate_child_bead_id(bead.bead_id, CORRECTIVE_SUFFIX)
        corrective = self.storage.create_bead(
            bead_id=corrective_id,
            title=f"Corrective fix for {bead.bead_id}: {bead.title}",
            agent_type=corrective_agent,
            description="\n\n".join(description_parts),
            parent_id=bead.bead_id,
            dependencies=[],
            acceptance_criteria=[
                f"Implement the minimum fix required to unblock {bead.bead_id}.",
                "Update tests/docs as needed for the corrective change.",
                "Leave a handoff summary that states how the parent bead can be retried.",
            ],
            linked_docs=bead.linked_docs,
            feature_root_id=bead.feature_root_id,
            execution_branch_name=bead.execution_branch_name,
            execution_worktree_path=bead.execution_worktree_path,
            expected_files=bead.expected_files,
            expected_globs=bead.expected_globs,
            touched_files=bead.touched_files,
            conflict_risks=bead.conflict_risks,
            metadata={"auto_corrective_for": bead.bead_id},
        )
        bead.metadata["auto_corrective_bead_id"] = corrective.bead_id
        self.storage.update_bead(
            bead,
            event="corrective_planned",
            summary=f"Created corrective bead {corrective.bead_id} for blocked issue",
        )
        if reporter:
            reporter.bead_deferred(
                bead,
                f"Created corrective bead {corrective.bead_id} ({corrective.agent_type})",
            )
        return corrective

    def _escalate_blocked_bead(self, bead: Bead, *, reporter: "SchedulerReporter | None" = None) -> None:
        if bead.metadata.get("needs_human_intervention"):
            return
        bead.metadata["needs_human_intervention"] = True
        bead.metadata["escalation_reason"] = (
            f"Exceeded corrective attempt budget ({MAX_CORRECTIVE_ATTEMPTS}) for blocked bead."
        )
        self.storage.update_bead(
            bead,
            event="escalated",
            summary=bead.metadata["escalation_reason"],
        )
        if reporter:
            reporter.bead_deferred(bead, "Escalated to human after repeated blocked retries")

    def _process(self, bead: Bead, result: SchedulerResult, *, reporter: "SchedulerReporter | None" = None) -> None:
        workdir = self.storage.root
        runner_workdir = Path(workdir)
        execution_env: dict[str, str] | None = None
        feature_root_id = self.storage.feature_root_id_for(bead)
        bead.status = BEAD_IN_PROGRESS
        bead.lease = Lease(owner=f"{bead.agent_type}:{bead.bead_id}", expires_at=(datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat())
        if feature_root_id:
            bead.feature_root_id = feature_root_id
            bead.execution_branch_name = bead.execution_branch_name or self.storage.default_execution_branch_name(feature_root_id)
            bead.execution_worktree_path = bead.execution_worktree_path or str(self.storage.worktrees_dir / feature_root_id)
        if reporter:
            reporter.bead_started(bead)
        if feature_root_id:
            branch_name = bead.execution_branch_name or self.storage.default_execution_branch_name(feature_root_id)
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
        try:
            guardrail_path, guardrail_text = load_guardrail_template(bead.agent_type, root=runner_workdir)
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
            agent_result.touched_files = sorted(dict.fromkeys([*agent_result.touched_files, *touched]))
            agent_result.changed_files = sorted(dict.fromkeys([*agent_result.changed_files, *agent_result.touched_files]))
        self._finalize(bead, agent_result, result, reporter=reporter)

    def _finalize(self, bead: Bead, agent_result: AgentRunResult, result: SchedulerResult, *, reporter: "SchedulerReporter | None" = None) -> None:
        bead.lease = None
        bead.block_reason = agent_result.block_reason
        bead.expected_files = list(agent_result.expected_files or bead.expected_files)
        bead.expected_globs = list(agent_result.expected_globs or bead.expected_globs)
        bead.touched_files = list(agent_result.touched_files)
        bead.conflict_risks = agent_result.conflict_risks

        # Review/test signoff is strict: unresolved remaining work cannot be marked completed.
        if (
            bead.agent_type in {"review", "tester"}
            and agent_result.outcome == "completed"
            and self._remaining_requires_followup(agent_result.remaining)
        ):
            agent_result.outcome = "blocked"
            if not agent_result.block_reason:
                agent_result.block_reason = (
                    f"{bead.agent_type.title()} reported unresolved findings in remaining."
                )
            agent_result.summary = (
                f"{agent_result.summary} "
                f"{bead.agent_type.title()} reported unresolved findings and requires follow-up."
            ).strip()
            bead.block_reason = agent_result.block_reason

        handoff = HandoffSummary(
            completed=agent_result.completed,
            remaining=agent_result.remaining,
            risks=agent_result.risks,
            changed_files=agent_result.changed_files,
            updated_docs=agent_result.updated_docs,
            next_action=agent_result.next_action,
            next_agent=agent_result.next_agent,
            block_reason=agent_result.block_reason,
            expected_files=bead.expected_files,
            expected_globs=bead.expected_globs,
            touched_files=bead.touched_files,
            conflict_risks=bead.conflict_risks,
        )
        bead.handoff_summary = handoff
        bead.changed_files = list(agent_result.changed_files)
        bead.updated_docs = list(agent_result.updated_docs)
        bead.metadata["last_agent_result"] = {
            "outcome": agent_result.outcome,
            "summary": agent_result.summary,
            "next_agent": agent_result.next_agent,
            "block_reason": agent_result.block_reason,
        }

        if agent_result.outcome == "blocked":
            bead.status = BEAD_BLOCKED
            self.storage.update_bead(bead, event="blocked", summary=agent_result.summary)
            result.blocked.append(bead.bead_id)
            if reporter:
                reporter.bead_blocked(bead, agent_result.summary)
            return

        if agent_result.outcome == "failed":
            bead.status = BEAD_BLOCKED
            bead.retries += 1
            self.storage.update_bead(bead, event="failed", summary=agent_result.summary)
            result.blocked.append(bead.bead_id)
            if reporter:
                reporter.bead_failed(bead, agent_result.summary)
            return

        if bead.agent_type in MUTATING_AGENTS:
            if not bead.worktree_path:
                bead.status = BEAD_BLOCKED
                bead.block_reason = "Mutating bead completed without a worktree path."
                self.storage.update_bead(bead, event="blocked", summary=bead.block_reason)
                result.blocked.append(bead.bead_id)
                if reporter:
                    reporter.bead_blocked(bead, bead.block_reason)
                return
            try:
                commit_hash = self.worktrees.commit_all(
                    Path(bead.worktree_path),
                    f"[orchestrator] {bead.bead_id}: {bead.title}",
                )
            except GitError as exc:
                bead.status = BEAD_BLOCKED
                bead.block_reason = f"Auto-commit failed: {exc}"
                self.storage.update_bead(bead, event="blocked", summary=bead.block_reason)
                result.blocked.append(bead.bead_id)
                if reporter:
                    reporter.bead_blocked(bead, bead.block_reason)
                return
            if commit_hash:
                bead.metadata["last_commit"] = commit_hash

        bead.status = BEAD_DONE
        self.storage.update_bead(bead, event="completed", summary=agent_result.summary)
        self.storage.record_event("bead_completed", {"bead_id": bead.bead_id, "agent_type": bead.agent_type})
        created = self._create_followups(bead, agent_result)
        if reporter:
            reporter.bead_completed(bead, agent_result.summary, created)
        result.completed.append(bead.bead_id)

    def _remaining_requires_followup(self, remaining: str) -> bool:
        text = " ".join(remaining.strip().lower().split())
        if not text:
            return False
        if text in {"none", "n/a", "na", "none.", "n/a.", "na."}:
            return False
        benign_phrases = (
            "none for this bead",
            "no additional",
            "no findings discovered",
            "no correctness",
            "no coverage",
            "no documentation gaps",
            "no gaps were identified",
            "no further",
            "no remaining",
            "nothing remaining",
            "nothing further",
            "no unresolved",
            "no action required",
            "no follow-up required",
            "no followup required",
            "no tester-scope work required",
            "no tester-scope work remains",
            "no review-scope work required",
            "no review-scope work remains",
        )
        return not any(phrase in text for phrase in benign_phrases)

    def _create_followups(self, bead: Bead, agent_result: AgentRunResult) -> list[Bead]:
        created: list[Bead] = []
        if bead.agent_type != "developer":
            return created
        if bead.metadata.get("auto_corrective_for"):
            return created

        for new_bead in agent_result.new_beads:
            child_id = self.storage.allocate_child_bead_id(bead.bead_id, "subtask")
            created.append(self.storage.create_bead(
                bead_id=child_id,
                title=new_bead["title"],
                agent_type=new_bead["agent_type"],
                description=new_bead["description"],
                parent_id=bead.bead_id,
                dependencies=list(new_bead.get("dependencies", [])),
                acceptance_criteria=list(new_bead.get("acceptance_criteria", [])),
                linked_docs=list(new_bead.get("linked_docs", [])),
                feature_root_id=bead.feature_root_id,
                execution_branch_name=bead.execution_branch_name,
                execution_worktree_path=bead.execution_worktree_path,
                expected_files=list(new_bead.get("expected_files", [])),
                expected_globs=list(new_bead.get("expected_globs", [])),
                metadata={"discovered_by": bead.bead_id},
            ))

        test_id = self._existing_or_new_child_id(bead.bead_id, FOLLOWUP_SUFFIXES["tester"])
        doc_id = self._existing_or_new_child_id(bead.bead_id, FOLLOWUP_SUFFIXES["documentation"])
        review_id = self._existing_or_new_child_id(bead.bead_id, FOLLOWUP_SUFFIXES["review"])

        if not self.storage.bead_path(test_id).exists():
            created.append(self.storage.create_bead(
                bead_id=test_id,
                title=f"Test {bead.title}",
                agent_type="tester",
                description=f"Validate implementation for {bead.bead_id}",
                parent_id=bead.bead_id,
                dependencies=[bead.bead_id],
                linked_docs=bead.linked_docs,
                feature_root_id=bead.feature_root_id,
                execution_branch_name=bead.execution_branch_name,
                execution_worktree_path=bead.execution_worktree_path,
                expected_files=bead.touched_files or bead.expected_files,
                expected_globs=bead.expected_globs,
                touched_files=bead.touched_files,
                conflict_risks=bead.conflict_risks,
            ))
        if not self.storage.bead_path(doc_id).exists():
            created.append(self.storage.create_bead(
                bead_id=doc_id,
                title=f"Document {bead.title}",
                agent_type="documentation",
                description=f"Update docs for {bead.bead_id}",
                parent_id=bead.bead_id,
                dependencies=[bead.bead_id],
                linked_docs=bead.linked_docs,
                feature_root_id=bead.feature_root_id,
                execution_branch_name=bead.execution_branch_name,
                execution_worktree_path=bead.execution_worktree_path,
                expected_files=bead.touched_files or bead.expected_files,
                expected_globs=bead.expected_globs,
                touched_files=bead.touched_files,
                conflict_risks=bead.conflict_risks,
            ))
        if not self.storage.bead_path(review_id).exists():
            created.append(self.storage.create_bead(
                bead_id=review_id,
                title=f"Review {bead.title}",
                agent_type="review",
                description=f"Review implementation for {bead.bead_id}",
                parent_id=bead.bead_id,
                dependencies=[bead.bead_id, test_id, doc_id],
                linked_docs=bead.linked_docs,
                feature_root_id=bead.feature_root_id,
                execution_branch_name=bead.execution_branch_name,
                execution_worktree_path=bead.execution_worktree_path,
                expected_files=bead.touched_files or bead.expected_files,
                expected_globs=bead.expected_globs,
                touched_files=bead.touched_files,
                conflict_risks=bead.conflict_risks,
            ))
        return created

    def _existing_or_new_child_id(self, parent_id: str, suffix: str) -> str:
        base = f"{parent_id}-{suffix}"
        for bead in self.storage.list_beads():
            if bead.parent_id == parent_id and bead.bead_id == base:
                return bead.bead_id
        return self.storage.allocate_child_bead_id(parent_id, suffix)

    def _find_conflict_reason(self, bead: Bead, active_beads: list[Bead]) -> str:
        for active in active_beads:
            if active.bead_id == bead.bead_id:
                continue
            if self._beads_conflict(bead, active):
                return f"Deferred due to file-scope conflict with active bead {active.bead_id}"
        return ""

    def _beads_conflict(self, bead: Bead, active: Bead) -> bool:
        same_feature_tree = self.storage.feature_root_id_for(bead) == self.storage.feature_root_id_for(active)
        if same_feature_tree and bead.agent_type in MUTATING_AGENTS and active.agent_type in MUTATING_AGENTS:
            if not bead.has_scope() or not active.has_scope():
                return True
        if bead.agent_type not in MUTATING_AGENTS or active.agent_type not in MUTATING_AGENTS:
            return False
        if not bead.has_scope() or not active.has_scope():
            return (
                bead.agent_type == "developer"
                and active.agent_type == "developer"
                and same_feature_tree
            )
        return self._scopes_overlap(bead, active)

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

    def _scopes_overlap(self, first: Bead, second: Bead) -> bool:
        first_source = first.scope_source()
        second_source = second.scope_source()
        first_entries = first.scope_entries()
        second_entries = second.scope_entries()

        if first_source in {"touched_files", "expected_files"} and second_source in {"touched_files", "expected_files"}:
            return bool(set(first_entries) & set(second_entries))
        if first_source == "expected_globs" and second_source == "expected_globs":
            return self._globs_overlap(first_entries, second_entries)
        if first_source == "expected_globs":
            return self._files_match_globs(second_entries, first_entries)
        if second_source == "expected_globs":
            return self._files_match_globs(first_entries, second_entries)
        return False

    def _files_match_globs(self, files: list[str], globs: list[str]) -> bool:
        for file_path in files:
            for pattern in globs:
                if fnmatch(file_path, pattern):
                    return True
        return False

    def _globs_overlap(self, first_globs: list[str], second_globs: list[str]) -> bool:
        for first in first_globs:
            first_prefix = self._glob_prefix(first)
            for second in second_globs:
                second_prefix = self._glob_prefix(second)
                if first == second:
                    return True
                if first_prefix.startswith(second_prefix) or second_prefix.startswith(first_prefix):
                    return True
        return False

    def _glob_prefix(self, pattern: str) -> str:
        wildcard_positions = [index for index in (pattern.find("*"), pattern.find("?"), pattern.find("[")) if index != -1]
        if not wildcard_positions:
            return pattern
        return pattern[:min(wildcard_positions)]


class SchedulerReporter(Protocol):
    def lease_expired(self, bead_id: str) -> None: ...

    def bead_started(self, bead: Bead) -> None: ...

    def worktree_ready(self, bead: Bead, branch_name: str, worktree_path: Path) -> None: ...

    def bead_completed(self, bead: Bead, summary: str, created: list[Bead]) -> None: ...

    def bead_deferred(self, bead: Bead, summary: str) -> None: ...

    def bead_blocked(self, bead: Bead, summary: str) -> None: ...

    def bead_failed(self, bead: Bead, summary: str) -> None: ...
