from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from fnmatch import fnmatch
from pathlib import Path
from typing import Protocol

from .gitutils import GitError, WorktreeManager
from .models import (
    AGENT_TYPES,
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
from .runner import AgentRunner
from .storage import RepositoryStorage


FOLLOWUP_SUFFIXES = {
    "tester": "test",
    "documentation": "docs",
    "review": "review",
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

    def run_once(self, *, max_workers: int = 1, reporter: "SchedulerReporter | None" = None) -> SchedulerResult:
        result = SchedulerResult()
        expired = self.expire_stale_leases()
        if reporter:
            for bead_id in expired:
                reporter.lease_expired(bead_id)
        ready = self.storage.ready_beads()
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

    def _process(self, bead: Bead, result: SchedulerResult, *, reporter: "SchedulerReporter | None" = None) -> None:
        workdir = self.storage.root
        feature_root_id = self.storage.feature_root_id_for(bead)
        bead.status = BEAD_IN_PROGRESS
        bead.lease = Lease(owner=f"{bead.agent_type}:{bead.bead_id}", expires_at=(datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat())
        if feature_root_id:
            bead.feature_root_id = feature_root_id
            bead.execution_branch_name = bead.execution_branch_name or self.storage.default_execution_branch_name(feature_root_id)
            bead.execution_worktree_path = bead.execution_worktree_path or str(self.storage.worktrees_dir / feature_root_id)
        if reporter:
            reporter.bead_started(bead)
        if bead.agent_type in MUTATING_AGENTS:
            if not feature_root_id:
                bead.status = BEAD_BLOCKED
                bead.lease = None
                bead.block_reason = "Mutating bead has no feature_root_id"
                self.storage.update_bead(bead, event="blocked", summary=bead.block_reason)
                result.blocked.append(bead.bead_id)
                if reporter:
                    reporter.bead_blocked(bead, bead.block_reason)
                return
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
            bead.worktree_path = str(worktree_path)
            bead.execution_branch_name = branch_name
            bead.execution_worktree_path = str(worktree_path)
            feature_root = self.storage.feature_root_bead_for(bead)
            if feature_root is not None and feature_root.bead_id != bead.bead_id:
                feature_root.execution_branch_name = branch_name
                feature_root.execution_worktree_path = str(worktree_path)
                self.storage.save_bead(feature_root)
            workdir = worktree_path
            if reporter:
                reporter.worktree_ready(bead, branch_name, worktree_path)
        self.storage.update_bead(bead, event="started", summary="Worker started")
        context_paths = self.storage.linked_context_paths(bead)
        try:
            agent_result = self.runner.run_bead(bead, workdir=Path(workdir), context_paths=context_paths)
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
        handoff = HandoffSummary(
            completed=agent_result.completed,
            remaining=agent_result.remaining,
            risks=agent_result.risks,
            changed_files=agent_result.changed_files,
            updated_docs=agent_result.updated_docs,
            next_action=agent_result.next_action,
            expected_files=bead.expected_files,
            expected_globs=bead.expected_globs,
            touched_files=bead.touched_files,
            conflict_risks=bead.conflict_risks,
        )
        bead.handoff_summary = handoff
        bead.changed_files = list(agent_result.changed_files)
        bead.updated_docs = list(agent_result.updated_docs)

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

        bead.status = BEAD_DONE
        self.storage.update_bead(bead, event="completed", summary=agent_result.summary)
        self.storage.record_event("bead_completed", {"bead_id": bead.bead_id, "agent_type": bead.agent_type})
        created = self._create_followups(bead, agent_result)
        if reporter:
            reporter.bead_completed(bead, agent_result.summary, created)
        result.completed.append(bead.bead_id)

    def _create_followups(self, bead: Bead, agent_result: AgentRunResult) -> list[Bead]:
        created: list[Bead] = []
        if bead.agent_type != "developer":
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
