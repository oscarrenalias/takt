from __future__ import annotations

import logging
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait as cf_wait
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

logger = logging.getLogger(__name__)


class Scheduler:
    """Orchestrates bead dispatch across one or more worker threads.

    A single ``run_once()`` call constitutes one scheduler *cycle*.  Within a
    cycle the scheduler continuously fills available worker slots as running
    beads complete — it does not stop after the first batch.  This reactive
    loop means that a bead unlocked by a dependency completing mid-cycle can be
    started in the same cycle, without waiting for the next ``run_once()`` call.

    Slot-filling and deferral logic live in ``_select_beads_for_dispatch()``.
    Bead execution is delegated to ``BeadExecutor`` (``scheduler/execution.py``).
    Followup creation, corrective retries, and recovery bead creation are
    handled by ``BeadFinalizer`` (``scheduler/finalize.py``) and
    ``FollowupManager`` (``scheduler/followups.py``).

    Progress events are reported through ``SchedulerReporter``; pass ``None``
    to suppress all reporting.
    """

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
        self.serialize_within_feature_tree = self.config.scheduler.serialize_within_feature_tree
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
        """Execute one scheduler cycle and return a summary of what happened.

        The cycle proceeds as follows:

        1. **Expire stale leases** — any bead whose lease has passed its
           ``expires_at`` timestamp is reset to READY and reported via
           ``reporter.lease_expired()``.
        2. **Re-evaluate blocked beads** — blocked beads are inspected for
           transient errors and corrective/recovery state changes.
        3. **Guard: skip dispatch when max_workers < 1** — if *max_workers* is
           less than 1 (the legacy ``max_workers=0`` sentinel), the function
           returns after steps 1–2 without entering the slot-fill loop.
           ``ThreadPoolExecutor`` requires at least one worker thread, so
           passing 0 is a supported way to run only lease expiry and blocked
           bead re-evaluation without dispatching any new work.
        4. **Continuous slot-fill loop** — a ``ThreadPoolExecutor`` with
           ``max_workers`` threads is used.  Each iteration of the inner loop:

           a. Fills all free worker slots by calling
              ``_select_beads_for_dispatch()``, which skips beads with
              unresolved dependencies or file-scope conflicts and calls
              ``reporter.bead_deferred()`` for each skipped bead (at most once
              per bead per cycle).
           b. Waits (``FIRST_COMPLETED``) for at least one running bead to
              finish.
           c. After each completion, re-evaluates blocked beads so that
              correctives or newly satisfied dependencies can be picked up in
              the same cycle.
           d. Loops back to fill newly freed slots immediately.

           The loop exits only when no bead is running *and* no bead can be
           dispatched.

        Returns a :class:`~agent_takt.models.SchedulerResult` with lists of
        ``started``, ``completed``, ``blocked``, ``correctives_created``, and
        ``deferred`` bead IDs plus ``final_state`` counts.
        """
        result = SchedulerResult()
        expired = self.expire_stale_leases()
        if reporter:
            for bead_id in expired:
                reporter.lease_expired(bead_id)
        self._reevaluate_blocked(feature_root_id=feature_root_id, reporter=reporter)

        # max_workers=0 is a legacy sentinel: run _reevaluate_blocked only, dispatch nothing.
        # ThreadPoolExecutor requires max_workers >= 1.
        if max_workers < 1:
            return result

        # Track beads deferred in this cycle to avoid duplicate history entries
        # and duplicate reporter events when the same bead is reconsidered across
        # fill-loop iterations.
        deferred_this_cycle: set[str] = set()
        # Track beads already dispatched this cycle so the slot-fill loop cannot
        # re-select a bead whose on-disk status is still READY (e.g. because
        # _process was mocked in tests or failed before updating storage).
        started_this_cycle: set[str] = set()

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures: dict[Future, Bead] = {}
            reevaluate_after_completion = False

            while True:
                # Re-evaluate blocked beads after each completion pass to pick up
                # corrective beads and dependencies that became satisfied mid-cycle.
                if reevaluate_after_completion:
                    self._reevaluate_blocked(feature_root_id=feature_root_id, reporter=reporter)
                    reevaluate_after_completion = False

                # Fill any free worker slots.
                free_slots = max_workers - len(futures)
                if free_slots > 0:
                    selected = self._select_beads_for_dispatch(
                        free_slots,
                        in_flight=list(futures.values()),
                        feature_root_id=feature_root_id,
                        result=result,
                        reporter=reporter,
                        deferred_this_cycle=deferred_this_cycle,
                        started_this_cycle=started_this_cycle,
                    )
                    result.started.extend(bead.bead_id for bead in selected)
                    started_this_cycle.update(bead.bead_id for bead in selected)
                    for bead in selected:
                        futures[executor.submit(self._process, bead, result, reporter=reporter)] = bead

                if not futures:
                    break  # nothing running and nothing left to dispatch

                # Wait for at least one in-flight bead to finish, then loop to fill
                # the freed slot immediately.
                done_set, _ = cf_wait(list(futures.keys()), return_when=FIRST_COMPLETED)
                for future in done_set:
                    futures.pop(future)
                    future.result()  # re-raise any uncaught exception from the worker
                reevaluate_after_completion = True

        return result

    def _select_beads_for_dispatch(
        self,
        max_count: int,
        *,
        in_flight: list[Bead],
        feature_root_id: str | None,
        result: SchedulerResult,
        reporter: SchedulerReporter | None = None,
        deferred_this_cycle: set[str],
        started_this_cycle: set[str] | None = None,
    ) -> list[Bead]:
        """Return up to *max_count* ready beads with no file-scope conflicts.

        Beads already being processed (*in_flight*) or already dispatched earlier
        in the current cycle (*started_this_cycle*) are excluded from candidates.
        Conflict checks include in-flight beads to guard the brief window between
        executor.submit() and the worker thread marking the bead in_progress.
        A bead is recorded as deferred at most once per run_once() call to avoid
        duplicate execution-history entries.
        """
        ready = self.storage.ready_beads()
        if feature_root_id:
            ready = [
                bead for bead in ready
                if self.storage.feature_root_id_for(bead) == feature_root_id
            ]
        in_flight_ids = {bead.bead_id for bead in in_flight}
        already_dispatched = in_flight_ids | (started_this_cycle or set())
        ready = [bead for bead in ready if bead.bead_id not in already_dispatched]
        ready.sort(key=lambda b: 0 if b.priority == "high" else 1)

        # Emit deferral events for READY beads whose dependencies are not yet done.
        # These are excluded from the ready list but still warrant a structured reason.
        if reporter:
            self._report_dependency_deferrals(
                in_flight_ids=in_flight_ids,
                feature_root_id=feature_root_id,
                reporter=reporter,
                deferred_this_cycle=deferred_this_cycle,
            )

        # Merge storage-active beads with in-flight beads for conflict detection.
        # In-flight beads may not yet be marked in_progress in storage.
        active = self.storage.active_beads()
        active_ids = {b.bead_id for b in active}
        for b in in_flight:
            if b.bead_id not in active_ids:
                active.append(b)

        selected: list[Bead] = []
        for bead in ready:
            conflict_reason = self._find_conflict_reason(bead, active + selected)
            if conflict_reason:
                if bead.bead_id not in deferred_this_cycle:
                    bead.block_reason = conflict_reason
                    self.storage.update_bead(bead, event="deferred", summary=conflict_reason)
                    result.deferred.append(bead.bead_id)
                    deferred_this_cycle.add(bead.bead_id)
                    if reporter:
                        reporter.bead_deferred(bead, conflict_reason)
                continue
            if len(selected) >= max_count:
                continue
            selected.append(bead)
        return selected

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
            self._executor.reevaluate_corrective_state(bead, reporter=reporter)

    # ------------------------------------------------------------------
    # Delegation shims — preserve the original Scheduler attribute surface
    # Tests and callers that access these via `scheduler.*` still work.
    # ------------------------------------------------------------------

    @property
    def lease_timeout_minutes(self) -> int:
        return self._executor.lease_timeout_minutes

    def _create_followups(self, bead: Bead, agent_result) -> list:
        return self._executor._followups._create_followups(bead, agent_result)

    def _create_corrective_bead(self, bead: Bead, *, reporter=None) -> Bead:
        return self._executor._followups._create_corrective_bead(bead, reporter=reporter)

    def _populate_shared_followup_touched_files(self, bead: Bead) -> None:
        return self._executor._followups._populate_shared_followup_touched_files(bead)

    def _planner_owned_followup(self, bead: Bead, agent_type: str):
        return self._executor._followups._planner_owned_followup(bead, agent_type)

    def _existing_followups_for(self, bead: Bead, *, include_planner_owned: bool = True) -> dict:
        return self._executor._followups._existing_followups_for(bead, include_planner_owned=include_planner_owned)

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

    def _find_conflict_reason(self, bead: Bead, active_beads: list[Bead]) -> str:
        for active in active_beads:
            if active.bead_id == bead.bead_id:
                continue
            if not self._beads_conflict(bead, active):
                continue
            same_feature_tree = (
                self.storage.feature_root_id_for(bead) == self.storage.feature_root_id_for(active)
            )
            if (
                self.serialize_within_feature_tree
                and same_feature_tree
                and bead.agent_type in MUTATING_AGENTS
                and active.agent_type in MUTATING_AGENTS
            ):
                return f"worktree serialization enabled — waiting on in-progress {active.bead_id}"
            if same_feature_tree and (not bead.has_scope() or not active.has_scope()):
                return f"worktree in use by in-progress {active.bead_id} (no file scope defined)"
            return f"file-scope conflict with in-progress {active.bead_id}"
        return ""

    def _report_dependency_deferrals(
        self,
        *,
        in_flight_ids: set[str],
        feature_root_id: str | None,
        reporter: SchedulerReporter,
        deferred_this_cycle: set[str],
    ) -> None:
        """Emit bead_deferred events for READY beads blocked on unsatisfied dependencies."""
        for bead in self.storage.list_beads():
            if bead.status != BEAD_READY or bead.lease is not None:
                continue
            if feature_root_id and self.storage.feature_root_id_for(bead) != feature_root_id:
                continue
            if bead.bead_id in in_flight_ids:
                continue
            if bead.bead_id in deferred_this_cycle:
                continue
            if self.storage.dependency_satisfied(bead):
                continue
            unsatisfied = [dep_id for dep_id in bead.dependencies if not self._dep_is_done(dep_id, bead)]
            if not unsatisfied:
                continue
            reason = "dependency not done: " + ", ".join(unsatisfied)
            deferred_this_cycle.add(bead.bead_id)
            reporter.bead_deferred(bead, reason)

    def _dep_is_done(self, dep_id: str, depending_bead: Bead | None = None) -> bool:
        try:
            return self.storage.load_bead(dep_id).status == BEAD_DONE
        except Exception as exc:
            logger.warning(
                "dependency lookup failed: dep_id=%s, exc=%s",
                dep_id,
                exc,
                exc_info=True,
            )
            if depending_bead is not None:
                depending_bead.execution_history.append(
                    ExecutionRecord(
                        timestamp=utc_now(),
                        event="dependency_resolution_error",
                        agent_type="scheduler",
                        summary=f"Failed to load dependency {dep_id}: {exc}",
                        details={"dep_id": dep_id, "error": str(exc)},
                    )
                )
                self.storage.update_bead(depending_bead)
            return False

    def _beads_conflict(self, bead: Bead, active: Bead) -> bool:
        same_feature_tree = self.storage.feature_root_id_for(bead) == self.storage.feature_root_id_for(active)
        if same_feature_tree and bead.agent_type in MUTATING_AGENTS and active.agent_type in MUTATING_AGENTS:
            if self.serialize_within_feature_tree:
                return True
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
