from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from ..gitutils import GitError, WorktreeManager
from ..models import (
    BEAD_BLOCKED,
    BEAD_DONE,
    ExecutionRecord,
    HandoffSummary,
    MUTATING_AGENTS,
    AgentRunResult,
    Bead,
    SchedulerResult,
    utc_now,
)
from ..config import OrchestratorConfig
from ..prompts import build_recovery_prompt
from ..runner import NO_STRUCTURED_OUTPUT_SENTINEL
from ..storage import RepositoryStorage
from .reporter import SchedulerReporter

if TYPE_CHECKING:
    from .execution import BeadExecutor


REVIEW_TEST_VERDICT_COMPAT_MODE = True


class BeadFinalizer:
    """Handles state-update, telemetry, git-commit, and outcome routing after a bead run."""

    def __init__(
        self,
        storage: RepositoryStorage,
        worktrees: WorktreeManager,
        config: OrchestratorConfig,
        executor: BeadExecutor,
    ) -> None:
        self.storage = storage
        self.worktrees = worktrees
        self.config = config
        self._executor = executor

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def finalize(
        self,
        bead: Bead,
        agent_result: AgentRunResult,
        result: SchedulerResult,
        *,
        reporter: SchedulerReporter | None = None,
    ) -> None:
        bead.lease = None
        existing_touched_files = list(bead.touched_files)
        existing_changed_files = list(bead.changed_files)
        existing_conflict_risks = bead.conflict_risks
        bead.expected_files = list(agent_result.expected_files or bead.expected_files)
        bead.expected_globs = list(agent_result.expected_globs or bead.expected_globs)
        bead.touched_files = list(agent_result.touched_files)
        bead.conflict_risks = agent_result.conflict_risks

        self._apply_review_test_verdict(bead, agent_result)
        bead.block_reason = agent_result.block_reason

        if agent_result.outcome == "blocked":
            if not bead.touched_files:
                bead.touched_files = existing_touched_files
            if not agent_result.changed_files:
                agent_result.changed_files = existing_changed_files
            if not bead.conflict_risks:
                bead.conflict_risks = existing_conflict_risks

        handoff = HandoffSummary(
            completed=agent_result.completed,
            remaining=agent_result.remaining,
            risks=agent_result.risks,
            verdict=agent_result.verdict,
            findings_count=agent_result.findings_count,
            requires_followup=self._resolved_requires_followup(agent_result),
            changed_files=agent_result.changed_files,
            updated_docs=agent_result.updated_docs,
            next_action=agent_result.next_action,
            next_agent=agent_result.next_agent,
            block_reason=agent_result.block_reason,
            expected_files=bead.expected_files,
            expected_globs=bead.expected_globs,
            touched_files=bead.touched_files,
            conflict_risks=bead.conflict_risks,
            design_decisions=agent_result.design_decisions,
            test_coverage_notes=agent_result.test_coverage_notes,
            known_limitations=agent_result.known_limitations,
        )
        bead.handoff_summary = handoff
        bead.changed_files = list(agent_result.changed_files)
        bead.updated_docs = list(agent_result.updated_docs)
        bead.metadata["last_agent_result"] = {
            "outcome": agent_result.outcome,
            "summary": agent_result.summary,
            "verdict": agent_result.verdict,
            "findings_count": agent_result.findings_count,
            "requires_followup": self._resolved_requires_followup(agent_result),
            "next_agent": agent_result.next_agent,
            "block_reason": agent_result.block_reason,
        }

        self._store_telemetry(bead, agent_result)

        if agent_result.outcome == "blocked":
            bead.status = BEAD_BLOCKED
            self.storage.update_bead(bead, event="blocked", summary=agent_result.summary)
            result.blocked.append(bead.bead_id)
            if reporter:
                reporter.bead_blocked(bead, agent_result.summary)
            # Immediately create corrective for review/tester needs_changes verdicts
            if (
                bead.agent_type in {"review", "tester"}
                and agent_result.verdict == "needs_changes"
                and self._executor._followups._can_plan_corrective(bead)
                and not self._executor._followups._corrective_children(bead)
            ):
                corrective = self._executor._followups._create_corrective_bead(bead, reporter=reporter)
                result.correctives_created.append(corrective.bead_id)
            return

        if agent_result.outcome == "failed":
            bead.status = BEAD_BLOCKED
            bead.retries += 1
            # Auto-create a recovery bead when no structured output was produced.
            # This does NOT consume a corrective attempt slot.
            # Recovery-of-recovery is explicitly prevented (bead_type guard).
            if (
                NO_STRUCTURED_OUTPUT_SENTINEL.lower() in (agent_result.block_reason or "").lower()
                and not bead.metadata.get("auto_recovery_bead_id")
                and bead.bead_type != "recovery"
            ):
                self._create_recovery_bead(bead, agent_result, reporter=reporter)
            self.storage.update_bead(bead, event="failed", summary=agent_result.summary)
            result.blocked.append(bead.bead_id)
            if reporter:
                reporter.bead_failed(bead, agent_result.summary)
            return

        # Dedicated recovery-completion path: when a recovery bead produces valid
        # structured output, apply it to the original bead and resume normal flow.
        if bead.recovery_for:
            self._handle_recovery_completion(bead, agent_result, result, reporter=reporter)
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
                    f"[takt] {bead.bead_id}: {bead.title}",
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
        # Requeue blocked verification parents before creating new followups so
        # tester/review beads resume instead of spawning duplicate downstream work.
        self._executor._followups._requeue_parent_after_corrective_completion(bead, reporter=reporter)
        created = self._executor._followups._create_followups(bead, agent_result)
        if reporter:
            reporter.bead_completed(bead, agent_result.summary, created)
        result.completed.append(bead.bead_id)

    # ------------------------------------------------------------------
    # Telemetry
    # ------------------------------------------------------------------

    @staticmethod
    def _telemetry_max_attempts() -> int:
        default = 10
        raw = os.environ.get("ORCHESTRATOR_TELEMETRY_MAX_ATTEMPTS", "")
        if not raw:
            return default
        try:
            value = int(raw)
        except (ValueError, TypeError):
            return default
        if value <= 0:
            return default
        return value

    def _store_telemetry(self, bead: Bead, agent_result: AgentRunResult) -> None:
        if agent_result.telemetry is None:
            return
        try:
            metrics = dict(agent_result.telemetry)
            # Remove heavy text fields from lightweight bead metadata copy
            lightweight = {k: v for k, v in metrics.items() if k not in ("prompt_text", "response_text")}

            # Tier 1: bead metadata
            bead.metadata["telemetry"] = lightweight

            history: list[dict] = list(bead.metadata.get("telemetry_history", []))
            attempt = len(history) + 1
            lightweight["attempt"] = attempt
            history.append(lightweight)

            cap = self._telemetry_max_attempts()
            if len(history) > cap:
                history = history[-cap:]
            bead.metadata["telemetry_history"] = history

            # Tier 2: full artifact file
            started_at = ""
            finished_at = utc_now()
            for record in reversed(bead.execution_history):
                if record.event == "started":
                    started_at = record.timestamp
                    break

            error = None
            if agent_result.outcome == "failed":
                error = {
                    "stage": "agent_execution",
                    "message": agent_result.summary or agent_result.block_reason or "Unknown failure",
                }

            parsed_result = bead.metadata.get("last_agent_result")

            self.storage.write_telemetry_artifact(
                bead_id=bead.bead_id,
                agent_type=bead.agent_type,
                attempt=attempt,
                started_at=started_at,
                finished_at=finished_at,
                outcome=agent_result.outcome,
                prompt_text=metrics.get("prompt_text"),
                response_text=metrics.get("response_text"),
                parsed_result=parsed_result,
                metrics=lightweight,
                error=error,
            )
        except Exception as exc:
            bead.execution_history.append(
                ExecutionRecord(
                    timestamp=utc_now(),
                    event="telemetry_write_warning",
                    agent_type="scheduler",
                    summary=f"Telemetry write failed (bead outcome preserved): {exc}",
                )
            )

    # ------------------------------------------------------------------
    # Recovery bead completion
    # ------------------------------------------------------------------

    def _handle_recovery_completion(
        self,
        recovery_bead: Bead,
        agent_result: AgentRunResult,
        result: SchedulerResult,
        *,
        reporter: SchedulerReporter | None = None,
    ) -> None:
        """Apply a successful recovery result to the original bead.

        Marks the recovery bead done, commits any uncommitted worktree changes
        left by the original bead, applies the synthesised handoff to the
        original bead, marks it done, and triggers normal follow-up creation.
        """
        # Step 1: Finish the recovery bead.
        recovery_bead.status = BEAD_DONE
        self.storage.update_bead(
            recovery_bead, event="completed", summary=agent_result.summary
        )
        self.storage.record_event(
            "bead_completed",
            {"bead_id": recovery_bead.bead_id, "agent_type": recovery_bead.agent_type},
        )
        result.completed.append(recovery_bead.bead_id)
        if reporter:
            reporter.bead_completed(recovery_bead, agent_result.summary, [])

        # Step 2: Load the original bead.
        try:
            original = self.storage.load_bead(recovery_bead.recovery_for)
        except Exception as exc:
            if reporter:
                reporter.bead_blocked(
                    recovery_bead,
                    f"Could not load original bead {recovery_bead.recovery_for}: {exc}",
                )
            return

        # Step 3: Commit the original bead's uncommitted changes (if any).
        # The recovery agent only emits JSON; actual code changes were made by
        # the original mutating agent before it failed without structured output.
        worktree = original.worktree_path or recovery_bead.execution_worktree_path
        if original.agent_type in MUTATING_AGENTS and worktree:
            try:
                commit_hash = self.worktrees.commit_all(
                    Path(worktree),
                    f"[takt] {original.bead_id}: {original.title}",
                )
                if commit_hash:
                    original.metadata["last_commit"] = commit_hash
            except GitError:
                pass  # Non-fatal: original agent may not have staged any changes.

        # Step 4: Apply the synthesised handoff to the original bead.
        original.lease = None
        original.block_reason = ""
        original.touched_files = list(agent_result.touched_files or original.touched_files)
        original.changed_files = list(agent_result.changed_files or original.changed_files)
        if agent_result.expected_files:
            original.expected_files = list(agent_result.expected_files)
        if agent_result.expected_globs:
            original.expected_globs = list(agent_result.expected_globs)
        if agent_result.conflict_risks:
            original.conflict_risks = agent_result.conflict_risks
        original.updated_docs = list(agent_result.updated_docs)

        handoff = HandoffSummary(
            completed=agent_result.completed,
            remaining=agent_result.remaining,
            risks=agent_result.risks,
            verdict=agent_result.verdict,
            findings_count=agent_result.findings_count,
            requires_followup=self._resolved_requires_followup(agent_result),
            changed_files=agent_result.changed_files,
            updated_docs=agent_result.updated_docs,
            next_action=agent_result.next_action,
            next_agent=agent_result.next_agent,
            block_reason="",
            expected_files=original.expected_files,
            expected_globs=original.expected_globs,
            touched_files=original.touched_files,
            conflict_risks=original.conflict_risks,
            design_decisions=agent_result.design_decisions,
            test_coverage_notes=agent_result.test_coverage_notes,
            known_limitations=agent_result.known_limitations,
        )
        original.handoff_summary = handoff
        original.metadata["last_agent_result"] = {
            "outcome": "completed",
            "summary": agent_result.summary,
            "verdict": agent_result.verdict,
            "findings_count": agent_result.findings_count,
            "requires_followup": self._resolved_requires_followup(agent_result),
            "next_agent": agent_result.next_agent,
            "block_reason": "",
        }
        original.metadata["recovered_by"] = recovery_bead.bead_id

        # Step 5: Mark the original bead done.
        original.status = BEAD_DONE
        recovery_summary = f"Completed via recovery bead {recovery_bead.bead_id}"
        self.storage.update_bead(original, event="completed", summary=recovery_summary)
        self.storage.record_event(
            "bead_completed",
            {"bead_id": original.bead_id, "agent_type": original.agent_type},
        )
        result.completed.append(original.bead_id)

        # Step 6: Resume normal follow-up creation for the original bead.
        self._executor._followups._requeue_parent_after_corrective_completion(
            original, reporter=reporter
        )
        created = self._executor._followups._create_followups(original, agent_result)
        if reporter:
            reporter.bead_completed(original, recovery_summary, created)

    # ------------------------------------------------------------------
    # Recovery bead creation
    # ------------------------------------------------------------------

    def _create_recovery_bead(
        self,
        bead: Bead,
        agent_result: AgentRunResult,
        *,
        reporter: SchedulerReporter | None = None,
    ) -> Bead:
        """Create a recovery bead for a no-structured-output failure.

        Prefers full prose from .takt/agent-runs/{bead_id}/stdout.txt;
        falls back to block_reason when that file is absent.
        Does NOT consume a corrective attempt slot.
        """
        stdout_path = self.storage.state_dir / "agent-runs" / bead.bead_id / "stdout.txt"
        if stdout_path.is_file():
            prose_output = stdout_path.read_text(encoding="utf-8")
        else:
            prose_output = agent_result.block_reason or agent_result.summary or ""

        git_diff = ""
        worktree_path = bead.worktree_path or bead.execution_worktree_path
        if worktree_path:
            try:
                proc = subprocess.run(
                    ["git", "diff", "HEAD"],
                    cwd=worktree_path,
                    text=True,
                    capture_output=True,
                    check=False,
                )
                if proc.returncode == 0:
                    git_diff = proc.stdout
            except Exception:
                pass

        recovery_prompt = build_recovery_prompt(bead, prose_output, git_diff)
        recovery_id = self.storage.allocate_child_bead_id(bead.bead_id, "recovery")
        recovery_bead = self.storage.create_bead(
            bead_id=recovery_id,
            title=f"Recover structured output for {bead.bead_id}: {bead.title}",
            agent_type="recovery",
            bead_type="recovery",
            description=recovery_prompt,
            parent_id=bead.bead_id,
            dependencies=[],
            acceptance_criteria=[f"Emit valid structured JSON for original bead {bead.bead_id}."],
            linked_docs=list(bead.linked_docs),
            feature_root_id=bead.feature_root_id,
            execution_branch_name=bead.execution_branch_name,
            execution_worktree_path=bead.execution_worktree_path,
            expected_files=list(bead.expected_files),
            expected_globs=list(bead.expected_globs),
            recovery_for=bead.bead_id,
        )
        bead.metadata["auto_recovery_bead_id"] = recovery_bead.bead_id
        if reporter:
            reporter.bead_deferred(
                bead,
                f"Created recovery bead {recovery_bead.bead_id} for no-structured-output failure",
            )
        return recovery_bead

    # ------------------------------------------------------------------
    # Verdict / outcome helpers
    # ------------------------------------------------------------------

    def _apply_review_test_verdict(self, bead: Bead, agent_result: AgentRunResult) -> None:
        if bead.agent_type not in {"review", "tester"}:
            return
        verdict = agent_result.verdict.strip()
        if verdict:
            agent_result.verdict = verdict
            if verdict == "approved":
                if agent_result.outcome != "failed":
                    agent_result.outcome = "completed"
                if agent_result.requires_followup is None:
                    agent_result.requires_followup = False
                return
            if verdict == "needs_changes":
                agent_result.outcome = "blocked"
                if not agent_result.block_reason:
                    agent_result.block_reason = (
                        f"{bead.agent_type.title()} verdict requires changes."
                    )
                if not agent_result.summary:
                    agent_result.summary = agent_result.block_reason
                if agent_result.requires_followup is None:
                    agent_result.requires_followup = True
                return
            raise ValueError(f"Unsupported {bead.agent_type} verdict: {verdict}")

        if not REVIEW_TEST_VERDICT_COMPAT_MODE:
            agent_result.outcome = "blocked"
            if not agent_result.block_reason:
                agent_result.block_reason = (
                    f"{bead.agent_type.title()} output omitted required verdict."
                )
            agent_result.summary = (
                f"{agent_result.summary} Missing structured verdict."
            ).strip()
            if agent_result.requires_followup is None:
                agent_result.requires_followup = True
            return

        bead.execution_history.append(
            ExecutionRecord(
                timestamp=utc_now(),
                event="compat_fallback_warning",
                agent_type="scheduler",
                summary=(
                    f"Used legacy remaining-text fallback for {bead.agent_type} bead because verdict was omitted."
                ),
            )
        )
        if agent_result.outcome == "completed" and self._remaining_requires_followup(agent_result.remaining):
            agent_result.outcome = "blocked"
            if not agent_result.block_reason:
                agent_result.block_reason = (
                    f"{bead.agent_type.title()} reported unresolved findings in remaining."
                )
            agent_result.summary = (
                f"{agent_result.summary} "
                f"{bead.agent_type.title()} reported unresolved findings and requires follow-up."
            ).strip()
        if agent_result.requires_followup is None:
            agent_result.requires_followup = agent_result.outcome == "blocked"

    def _resolved_requires_followup(self, agent_result: AgentRunResult) -> bool:
        if agent_result.requires_followup is not None:
            return agent_result.requires_followup
        if agent_result.verdict == "needs_changes":
            return True
        return False

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
