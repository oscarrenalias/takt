from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from fnmatch import fnmatch
from pathlib import Path

from ..gitutils import WorktreeManager
from ..models import (
    BEAD_BLOCKED,
    BEAD_DONE,
    BEAD_IN_PROGRESS,
    BEAD_READY,
    ExecutionRecord,
    MUTATING_AGENTS,
    Bead,
    SchedulerResult,
    utc_now,
)
from ..config import OrchestratorConfig, default_config
from ..runner import AgentRunner
from ..storage import RepositoryStorage
from .execution import BeadExecutor
from .reporter import SchedulerReporter


class Scheduler:
    def __init__(
        self,
        storage: RepositoryStorage,
        runner: AgentRunner,
        worktrees: WorktreeManager,
        config: OrchestratorConfig | None = None,
    ) -> None:
        self.storage = storage
        self.runner = runner
        self.worktrees = worktrees
        self.config = config or default_config()

        self.followup_suffixes = dict(self.config.scheduler.followup_suffixes)
        self.corrective_suffix = self.config.scheduler.corrective_suffix
        self.max_corrective_attempts = self.config.scheduler.max_corrective_attempts
        self.transient_block_patterns = self.config.scheduler.transient_block_patterns
        self.runnable_reassign_agents = set(self.config.agent_types)
        self.followup_agent_by_suffix = {
            f"-{suffix}": agent for agent, suffix in self.followup_suffixes.items()
        }
        self._executor = BeadExecutor(storage, runner, worktrees, self.config)

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
        reporter: SchedulerReporter | None = None,
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

    def _process(
        self,
        bead: Bead,
        result: SchedulerResult,
        *,
        reporter: SchedulerReporter | None = None,
    ) -> None:
        self._executor.process(bead, result, reporter=reporter)

    def _reevaluate_blocked(
        self,
        *,
        feature_root_id: str | None,
        reporter: SchedulerReporter | None = None,
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
            if reason and any(pattern in reason for pattern in self.transient_block_patterns):
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
            corrective_children = self._executor._followups._corrective_children(bead)
            open_corrective = next(
                (child for child in corrective_children if child.status in {BEAD_READY, BEAD_IN_PROGRESS}),
                None,
            )
            if open_corrective is not None:
                continue
            latest_done = next((child for child in reversed(corrective_children) if child.status == BEAD_DONE), None)
            if latest_done is not None:
                if not self._executor._followups._already_retried_after_corrective(bead, latest_done):
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
                if len(corrective_children) < self.max_corrective_attempts and self._executor._followups._can_plan_corrective(bead):
                    self._executor._followups._create_corrective_bead(bead, reporter=reporter)
                else:
                    self._executor._followups._escalate_blocked_bead(bead, reporter=reporter)
                continue
            if not corrective_children and self._executor._followups._can_plan_corrective(bead):
                self._executor._followups._create_corrective_bead(bead, reporter=reporter)
                continue
            if len(corrective_children) >= self.max_corrective_attempts:
                self._executor._followups._escalate_blocked_bead(bead, reporter=reporter)

    def _repair_invalid_worker_agent_type(self, bead: Bead) -> bool:
        if bead.agent_type in self.runnable_reassign_agents:
            return False
        candidates: list[str] = []
        next_agent = bead.handoff_summary.next_agent.strip()
        if next_agent in self.runnable_reassign_agents:
            candidates.append(next_agent)
        previous = str(bead.metadata.get("reassigned_from_agent_type", "")).strip()
        if previous in self.runnable_reassign_agents:
            candidates.append(previous)
        for suffix, agent in self.followup_agent_by_suffix.items():
            if bead.bead_id.endswith(suffix):
                candidates.append(agent)
                break
        if bead.parent_id:
            parent = self.storage.load_bead(bead.parent_id)
            if parent.agent_type in self.runnable_reassign_agents:
                candidates.append(parent.agent_type)
        candidates.append("developer")
        for candidate in candidates:
            if candidate in self.runnable_reassign_agents:
                bead.agent_type = candidate
                return True
        return False

    def _find_corrective_child(self, bead: Bead) -> Bead | None:
        recorded = bead.metadata.get("auto_corrective_bead_id", "")
        if recorded:
            path = self.storage.bead_path(recorded)
            if path.exists():
                return self.storage.load_bead(recorded)
        expected = f"{bead.bead_id}-{self.corrective_suffix}"
        path = self.storage.bead_path(expected)
        if path.exists():
            return self.storage.load_bead(expected)
        for candidate in self.storage.list_beads():
            if candidate.parent_id != bead.bead_id:
                continue
            if candidate.metadata.get("auto_corrective_for") == bead.bead_id:
                return candidate
        return None

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
