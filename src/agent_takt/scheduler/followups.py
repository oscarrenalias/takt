from __future__ import annotations

from ..models import (
    BEAD_BLOCKED,
    BEAD_DONE,
    BEAD_READY,
    MUTATING_AGENTS,
    AgentRunResult,
    Bead,
    SchedulerResult,
)
from ..config import OrchestratorConfig
from ..storage import RepositoryStorage
from .reporter import SchedulerReporter


FOLLOWUP_AGENT_TYPES = ("tester", "documentation", "review")


class FollowupManager:
    """Manages followup bead creation, scope syncing, and corrective bead logic."""

    def __init__(
        self,
        storage: RepositoryStorage,
        config: OrchestratorConfig,
    ) -> None:
        self.storage = storage
        self.config = config

        self.followup_suffixes = dict(config.scheduler.followup_suffixes)
        self.corrective_suffix = config.scheduler.corrective_suffix
        self.max_corrective_attempts = config.scheduler.max_corrective_attempts

    # ------------------------------------------------------------------
    # Corrective bead helpers
    # ------------------------------------------------------------------

    def _is_corrective_bead(self, bead: Bead) -> bool:
        if bead.metadata.get("auto_corrective_for"):
            return True
        return f"-{self.corrective_suffix}" in bead.bead_id

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
            if child.parent_id == bead.bead_id
            and child.metadata.get("auto_corrective_for") == bead.bead_id
        ]
        return sorted(children, key=lambda item: item.bead_id)

    def _can_plan_corrective(self, bead: Bead) -> bool:
        if self._is_corrective_bead(bead):
            return False
        # A recovery bead was already created for this no-structured-output failure;
        # do not also consume a corrective attempt slot.
        if bead.metadata.get("auto_recovery_bead_id"):
            return False
        current = bead
        while current.parent_id:
            parent = self.storage.load_bead(current.parent_id)
            if self._is_corrective_bead(parent):
                return False
            current = parent
        return True

    def _create_corrective_bead(
        self, bead: Bead, *, reporter: SchedulerReporter | None = None
    ) -> Bead:
        next_agent = bead.handoff_summary.next_agent.strip()
        corrective_agent = next_agent if next_agent in MUTATING_AGENTS else "developer"
        touched_files = list(bead.touched_files or bead.changed_files or bead.expected_files)
        changed_files = list(bead.changed_files or touched_files)
        description_parts = []
        if bead.block_reason:
            description_parts.append(f"Blocked reason: {bead.block_reason}")
        if bead.handoff_summary.remaining:
            description_parts.append(f"Remaining work: {bead.handoff_summary.remaining}")
        if not description_parts:
            description_parts.append("Investigate blocked bead and implement corrective fix to unblock parent bead.")
        corrective_id = self.storage.allocate_child_bead_id(bead.bead_id, self.corrective_suffix)
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
            touched_files=touched_files,
            changed_files=changed_files,
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

    def _escalate_blocked_bead(
        self, bead: Bead, *, reporter: SchedulerReporter | None = None
    ) -> None:
        if bead.metadata.get("needs_human_intervention"):
            return
        bead.metadata["needs_human_intervention"] = True
        bead.metadata["escalation_reason"] = (
            f"Exceeded corrective attempt budget ({self.max_corrective_attempts}) for blocked bead."
        )
        self.storage.update_bead(
            bead,
            event="escalated",
            summary=bead.metadata["escalation_reason"],
        )
        if reporter:
            reporter.bead_deferred(bead, "Escalated to human after repeated blocked retries")

    def _requeue_parent_after_corrective_completion(
        self,
        bead: Bead,
        *,
        reporter: SchedulerReporter | None = None,
    ) -> None:
        # A corrective developer bead can unblock its blocked tester/review parent
        # so the original verification pass reruns against the corrective commit.
        if not self._is_corrective_bead(bead) or bead.agent_type != "developer" or not bead.parent_id:
            return
        parent = self.storage.load_bead(bead.parent_id)
        if parent.status != BEAD_BLOCKED or parent.agent_type not in {"tester", "review"}:
            return
        if self._already_retried_after_corrective(parent, bead):
            return
        parent.status = BEAD_READY
        parent.block_reason = ""
        parent.metadata["last_corrective_retry_source"] = bead.bead_id
        parent.metadata["last_corrective_retry_commit"] = str(bead.metadata.get("last_commit", ""))
        self.storage.update_bead(
            parent,
            event="retried",
            summary=f"Requeued blocked bead after corrective bead {bead.bead_id} completed",
        )
        if reporter:
            reporter.bead_deferred(
                parent,
                f"Requeued after corrective bead {bead.bead_id} completed",
            )

    # ------------------------------------------------------------------
    # Followup bead helpers
    # ------------------------------------------------------------------

    def _create_followups(self, bead: Bead, agent_result: AgentRunResult) -> list[Bead]:
        created: list[Bead] = []
        if bead.agent_type != "developer":
            return created
        if self._is_corrective_bead(bead):
            return created

        # Propagate model_override from parent to all followup children
        parent_model_override = bead.metadata.get("model_override") if bead.metadata else None

        for new_bead in agent_result.new_beads:
            child_id = self.storage.allocate_child_bead_id(bead.bead_id, "subtask")
            child_metadata: dict = {"discovered_by": bead.bead_id}
            if parent_model_override:
                child_metadata["model_override"] = parent_model_override
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
                metadata=child_metadata,
            ))

        # Planner/feature flows may pre-create shared tester/documentation/review beads
        # that depend on multiple developer beads in the same feature tree. Reuse those
        # followups first so the scheduler does not create duplicate auto-followups,
        # while standalone/manual developer flows still fall back to the legacy
        # per-developer child-bead creation path below.
        uses_planner_owned = self._uses_planner_owned_followups(bead)
        planner_owned_followups = (
            self._planner_owned_followups_for(bead)
            if uses_planner_owned
            else {}
        )
        # Refine: suppress only when planner-owned shared followup beads actually exist.
        # A developer bead with a planner parent but no pre-created shared followups
        # should fall back to the legacy per-developer creation path.
        uses_planner_owned = uses_planner_owned and any(planner_owned_followups.values())
        legacy_followups = self._existing_followups_for(bead, include_planner_owned=False)
        # Reuse planner-owned followups per agent type, but still backfill any
        # missing followups through the legacy child-bead path.
        existing_followups = {
            agent_type: planner_owned_followups.get(agent_type) or legacy_followups[agent_type]
            for agent_type in FOLLOWUP_AGENT_TYPES
        }
        test_bead = existing_followups["tester"]
        doc_bead = existing_followups["documentation"]
        review_bead = existing_followups["review"]
        test_id = test_bead.bead_id if test_bead else self._existing_or_new_child_id(
            bead.bead_id,
            self.followup_suffixes["tester"],
        )
        doc_id = doc_bead.bead_id if doc_bead else self._existing_or_new_child_id(
            bead.bead_id,
            self.followup_suffixes["documentation"],
        )
        review_id = review_bead.bead_id if review_bead else self._existing_or_new_child_id(
            bead.bead_id,
            self.followup_suffixes["review"],
        )

        followup_metadata: dict = {}
        if parent_model_override:
            followup_metadata["model_override"] = parent_model_override

        if test_bead is None and not uses_planner_owned:
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
                changed_files=bead.changed_files,
                conflict_risks=bead.conflict_risks,
                metadata=dict(followup_metadata) if followup_metadata else None,
            ))
        elif test_bead is not None:
            self._sync_followup_scope(test_bead, bead)
        if doc_bead is None and not uses_planner_owned:
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
                changed_files=bead.changed_files,
                conflict_risks=bead.conflict_risks,
                metadata=dict(followup_metadata) if followup_metadata else None,
            ))
        elif doc_bead is not None:
            self._sync_followup_scope(doc_bead, bead)
        if review_bead is None and not uses_planner_owned:
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
                changed_files=bead.changed_files,
                conflict_risks=bead.conflict_risks,
                metadata=dict(followup_metadata) if followup_metadata else None,
            ))
        elif review_bead is not None:
            self._sync_followup_scope(review_bead, bead)
            self._sync_followup_dependencies(review_bead, [bead.bead_id, test_id, doc_id])
        return created

    @staticmethod
    def _merge_unique_items(existing: list[str], incoming: list[str]) -> list[str]:
        return sorted(dict.fromkeys([*existing, *incoming]))

    @staticmethod
    def _merge_conflict_risks(existing: str, incoming: str) -> str:
        if not existing:
            return incoming
        if not incoming or incoming == existing:
            return existing
        return "\n".join(dict.fromkeys([existing, incoming]))

    def _sync_followup_scope(self, followup: Bead, source: Bead) -> None:
        expected_files = self._merge_unique_items(
            followup.expected_files,
            source.touched_files or source.expected_files,
        )
        expected_globs = self._merge_unique_items(followup.expected_globs, source.expected_globs)
        touched_files = self._merge_unique_items(followup.touched_files, source.touched_files)
        changed_files = self._merge_unique_items(followup.changed_files, source.changed_files)
        conflict_risks = self._merge_conflict_risks(followup.conflict_risks, source.conflict_risks)

        if (
            expected_files == followup.expected_files
            and expected_globs == followup.expected_globs
            and touched_files == followup.touched_files
            and changed_files == followup.changed_files
            and conflict_risks == followup.conflict_risks
        ):
            return

        followup.expected_files = expected_files
        followup.expected_globs = expected_globs
        followup.touched_files = touched_files
        followup.changed_files = changed_files
        followup.conflict_risks = conflict_risks
        self.storage.save_bead(followup)

    def _sync_followup_dependencies(self, followup: Bead, dependencies: list[str]) -> None:
        merged_dependencies = self._merge_unique_items(followup.dependencies, dependencies)
        if merged_dependencies == followup.dependencies:
            return
        followup.dependencies = merged_dependencies
        self.storage.save_bead(followup)

    def _populate_shared_followup_touched_files(self, bead: Bead) -> None:
        if bead.agent_type not in FOLLOWUP_AGENT_TYPES:
            return

        done_dependencies = [
            self.storage.load_bead(dependency_id)
            for dependency_id in bead.dependencies
        ]
        done_dependencies = [
            dependency for dependency in done_dependencies
            if dependency.status == BEAD_DONE
        ]
        if not any(dependency.handoff_summary.touched_files for dependency in done_dependencies):
            return

        aggregated_touched_files = sorted(
            {
                file_path
                for dependency in done_dependencies
                for file_path in (
                    dependency.handoff_summary.touched_files
                    + dependency.handoff_summary.changed_files
                )
                if file_path
            }
        )
        if not aggregated_touched_files:
            return

        merged_touched_files = self._merge_unique_items(
            bead.touched_files,
            aggregated_touched_files,
        )
        merged_changed_files = self._merge_unique_items(
            bead.changed_files,
            aggregated_touched_files,
        )
        if (
            merged_touched_files == bead.touched_files
            and merged_changed_files == bead.changed_files
        ):
            return

        bead.touched_files = merged_touched_files
        bead.changed_files = merged_changed_files
        self.storage.save_bead(bead)

    def _existing_followups_for(
        self,
        bead: Bead,
        *,
        include_planner_owned: bool = True,
    ) -> dict[str, Bead | None]:
        return {
            agent_type: self._existing_followup_for(
                bead,
                agent_type,
                include_planner_owned=include_planner_owned,
            )
            for agent_type in FOLLOWUP_AGENT_TYPES
        }

    def _planner_owned_followups_for(self, bead: Bead) -> dict[str, Bead | None]:
        return {
            agent_type: self._planner_owned_followup(bead, agent_type)
            for agent_type in FOLLOWUP_AGENT_TYPES
        }

    def _existing_followup_for(
        self,
        bead: Bead,
        agent_type: str,
        *,
        include_planner_owned: bool = True,
    ) -> Bead | None:
        if include_planner_owned:
            explicit = self._planner_owned_followup(bead, agent_type)
            if explicit is not None:
                return explicit
        return self._legacy_followup_child(bead, agent_type)

    def _uses_planner_owned_followups(self, bead: Bead) -> bool:
        if bead.agent_type != "developer" or not bead.parent_id:
            return False
        parent = self.storage.load_bead(bead.parent_id)
        # Only planner/feature-owned developer subtasks opt into shared followups.
        # That includes children of an explicit feature bead and children that sit
        # directly under the feature root in an epic-created feature tree, even if
        # the root bead was materialized as a normal developer bead.
        if parent.agent_type == "planner" or parent.bead_type == "feature":
            return True
        return self.storage.feature_root_id_for(bead) == parent.bead_id and parent.parent_id is not None

    def _planner_owned_followup(self, bead: Bead, agent_type: str) -> Bead | None:
        feature_root_id = self.storage.feature_root_id_for(bead)
        if not feature_root_id:
            return None
        legacy_id = f"{bead.bead_id}-{self.followup_suffixes[agent_type]}"
        # Reuse only feature-root-owned shared followups that already depend on this
        # developer bead. That keeps scheduler reuse aligned with planner guidance and
        # avoids treating unrelated nested followups as planner-owned candidates.
        matches = [
            candidate for candidate in self.storage.list_beads()
            if candidate.bead_id != bead.bead_id
            and candidate.agent_type == agent_type
            and self.storage.feature_root_id_for(candidate) == feature_root_id
            and candidate.parent_id == feature_root_id
            and bead.bead_id in candidate.dependencies
        ]
        if not matches:
            return None
        matches.sort(key=lambda candidate: (candidate.bead_id == legacy_id, candidate.bead_id))
        return matches[0]

    def _legacy_followup_child(self, bead: Bead, agent_type: str) -> Bead | None:
        suffix = self.followup_suffixes[agent_type]
        expected_id = f"{bead.bead_id}-{suffix}"
        for candidate in self.storage.list_beads():
            if candidate.parent_id != bead.bead_id:
                continue
            if candidate.bead_id == expected_id and candidate.agent_type == agent_type:
                return candidate
        return None

    def _existing_or_new_child_id(self, parent_id: str, suffix: str) -> str:
        base = f"{parent_id}-{suffix}"
        for bead in self.storage.list_beads():
            if bead.parent_id == parent_id and bead.bead_id == base:
                return bead.bead_id
        return self.storage.allocate_child_bead_id(parent_id, suffix)
