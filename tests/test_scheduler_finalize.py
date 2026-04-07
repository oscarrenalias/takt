from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_takt.gitutils import WorktreeManager
from agent_takt.models import (
    BEAD_BLOCKED,
    BEAD_DONE,
    BEAD_IN_PROGRESS,
    AgentRunResult,
)
from agent_takt.scheduler import Scheduler
from agent_takt.scheduler.finalize import BeadFinalizer
from agent_takt.storage import RepositoryStorage

# Suppress git commits for the test session (mirrors test_orchestrator.py convention).
RepositoryStorage._auto_commit = False

from helpers import FakeRunner, OrchestratorTests  # noqa: E402


class SchedulerFinalizeTests(OrchestratorTests):
    # ------------------------------------------------------------------
    # Verdict / state transition tests
    # ------------------------------------------------------------------

    def test_review_with_remaining_findings_is_forced_blocked(self) -> None:
        bead = self.storage.create_bead(title="Review work", agent_type="review", description="inspect")
        runner = FakeRunner(
            results={
                bead.bead_id: AgentRunResult(
                    outcome="completed",
                    summary="Review finished",
                    completed="Validated the current implementation state.",
                    remaining="Unresolved defect in prompt template resolution.",
                    risks="Review sign-off cannot complete until the defect is fixed.",
                    next_action="Hand off to developer for the fix, then retry review.",
                    next_agent="developer",
                )
            }
        )
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))
        result = scheduler.run_once()
        self.assertEqual([bead.bead_id], result.blocked)
        self.assertEqual([], result.completed)
        bead = self.storage.load_bead(bead.bead_id)
        self.assertEqual(BEAD_BLOCKED, bead.status)
        self.assertIn("unresolved", bead.block_reason.lower())
        self.assertEqual("Validated the current implementation state.", bead.handoff_summary.completed)
        self.assertEqual("developer", bead.handoff_summary.next_agent)
        self.assertIn("unresolved", bead.handoff_summary.block_reason.lower())
        self.assertEqual("blocked", bead.metadata["last_agent_result"]["outcome"])
        self.assertEqual("developer", bead.metadata["last_agent_result"]["next_agent"])
        self.assertIn("unresolved", bead.metadata["last_agent_result"]["block_reason"].lower())

    def test_tester_with_remaining_findings_is_forced_blocked(self) -> None:
        bead = self.storage.create_bead(title="Test work", agent_type="tester", description="validate")
        runner = FakeRunner(
            results={
                bead.bead_id: AgentRunResult(
                    outcome="completed",
                    summary="Tests run complete",
                    completed="Executed the available regression checks.",
                    remaining="Known failing test remains unresolved.",
                    risks="Test sign-off is blocked until the runtime fix lands.",
                    next_action="Hand off to developer for the runtime fix, then rerun tests.",
                    next_agent="developer",
                )
            }
        )
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))
        result = scheduler.run_once()
        self.assertEqual([bead.bead_id], result.blocked)
        self.assertEqual([], result.completed)
        bead = self.storage.load_bead(bead.bead_id)
        self.assertEqual(BEAD_BLOCKED, bead.status)
        self.assertIn("unresolved", bead.block_reason.lower())
        self.assertEqual("Executed the available regression checks.", bead.handoff_summary.completed)
        self.assertEqual("developer", bead.handoff_summary.next_agent)
        self.assertIn("unresolved", bead.handoff_summary.block_reason.lower())
        self.assertEqual("blocked", bead.metadata["last_agent_result"]["outcome"])
        self.assertEqual("developer", bead.metadata["last_agent_result"]["next_agent"])
        self.assertIn("unresolved", bead.metadata["last_agent_result"]["block_reason"].lower())
        self.assertEqual("compat_fallback_warning", bead.execution_history[-2].event)

    def test_tester_with_approved_verdict_ignores_freeform_remaining(self) -> None:
        bead = self.storage.create_bead(title="Test work", agent_type="tester", description="validate")
        runner = FakeRunner(
            results={
                bead.bead_id: AgentRunResult(
                    outcome="completed",
                    summary="Tests run complete",
                    remaining="Some narrative prose that should not block completion.",
                    verdict="approved",
                    findings_count=0,
                )
            }
        )
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))
        result = scheduler.run_once()
        self.assertEqual([bead.bead_id], result.completed)
        self.assertEqual([], result.blocked)
        bead = self.storage.load_bead(bead.bead_id)
        self.assertEqual(BEAD_DONE, bead.status)
        self.assertEqual("approved", bead.handoff_summary.verdict)
        self.assertEqual(0, bead.handoff_summary.findings_count)
        self.assertFalse(bead.handoff_summary.requires_followup)
        self.assertNotIn("compat_fallback_warning", [record.event for record in bead.execution_history])

    def test_review_with_approved_verdict_and_no_findings_phrase_stays_completed(self) -> None:
        bead = self.storage.create_bead(title="Review work", agent_type="review", description="inspect")
        runner = FakeRunner(
            results={
                bead.bead_id: AgentRunResult(
                    outcome="completed",
                    summary="Review complete",
                    completed="Reviewed the implementation against the requested scope.",
                    remaining="No findings discovered in this review pass.",
                    verdict="approved",
                    findings_count=0,
                )
            }
        )
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))
        result = scheduler.run_once()
        self.assertEqual([bead.bead_id], result.completed)
        self.assertEqual([], result.blocked)
        bead = self.storage.load_bead(bead.bead_id)
        self.assertEqual(BEAD_DONE, bead.status)
        self.assertEqual("approved", bead.handoff_summary.verdict)
        self.assertEqual(0, bead.handoff_summary.findings_count)
        self.assertFalse(bead.handoff_summary.requires_followup)
        self.assertEqual("completed", bead.metadata["last_agent_result"]["outcome"])
        self.assertNotIn("compat_fallback_warning", [record.event for record in bead.execution_history])

    def test_review_with_needs_changes_verdict_blocks_and_requires_followup(self) -> None:
        bead = self.storage.create_bead(title="Review work", agent_type="review", description="inspect")
        runner = FakeRunner(
            results={
                bead.bead_id: AgentRunResult(
                    outcome="completed",
                    summary="Review found required changes",
                    completed="Reviewed current implementation.",
                    remaining="Narrative details about the findings.",
                    verdict="needs_changes",
                    findings_count=2,
                    next_agent="developer",
                )
            }
        )
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))
        result = scheduler.run_once()
        self.assertEqual([bead.bead_id], result.blocked)
        bead = self.storage.load_bead(bead.bead_id)
        self.assertEqual(BEAD_BLOCKED, bead.status)
        self.assertEqual("needs_changes", bead.handoff_summary.verdict)
        self.assertEqual(2, bead.handoff_summary.findings_count)
        self.assertTrue(bead.handoff_summary.requires_followup)
        self.assertIn("requires changes", bead.block_reason.lower())
        self.assertEqual("blocked", bead.metadata["last_agent_result"]["outcome"])

    def test_tester_with_needs_changes_verdict_blocks_and_preserves_findings(self) -> None:
        bead = self.storage.create_bead(title="Test work", agent_type="tester", description="validate")
        runner = FakeRunner(
            results={
                bead.bead_id: AgentRunResult(
                    outcome="completed",
                    summary="Regression run found failures",
                    completed="Executed targeted regression coverage.",
                    remaining="Two failing cases still need a scheduler fix.",
                    verdict="needs_changes",
                    findings_count=2,
                    next_agent="developer",
                )
            }
        )
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))
        result = scheduler.run_once()
        self.assertEqual([bead.bead_id], result.blocked)
        self.assertEqual([], result.completed)
        bead = self.storage.load_bead(bead.bead_id)
        self.assertEqual(BEAD_BLOCKED, bead.status)
        self.assertEqual("needs_changes", bead.handoff_summary.verdict)
        self.assertEqual(2, bead.handoff_summary.findings_count)
        self.assertTrue(bead.handoff_summary.requires_followup)
        self.assertEqual("developer", bead.handoff_summary.next_agent)
        self.assertIn("requires changes", bead.block_reason.lower())
        self.assertEqual("blocked", bead.metadata["last_agent_result"]["outcome"])
        self.assertNotIn("compat_fallback_warning", [record.event for record in bead.execution_history])

    def test_legacy_review_without_verdict_records_compat_warning(self) -> None:
        bead = self.storage.create_bead(title="Review work", agent_type="review", description="inspect")
        runner = FakeRunner(
            results={
                bead.bead_id: AgentRunResult(
                    outcome="completed",
                    summary="Review finished",
                    remaining="No findings discovered in this review pass.",
                )
            }
        )
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))
        result = scheduler.run_once()
        self.assertEqual([bead.bead_id], result.completed)
        bead = self.storage.load_bead(bead.bead_id)
        warning = next(record for record in bead.execution_history if record.event == "compat_fallback_warning")
        self.assertIn("verdict was omitted", warning.summary)

    def test_tester_with_no_additional_work_remaining_stays_completed(self) -> None:
        bead = self.storage.create_bead(title="Test work", agent_type="tester", description="validate")
        runner = FakeRunner(
            results={
                bead.bead_id: AgentRunResult(
                    outcome="completed",
                    summary="Tests run complete",
                    remaining="No additional tester-scope work required for this bead.",
                )
            }
        )
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))
        result = scheduler.run_once()
        self.assertEqual([bead.bead_id], result.completed)
        self.assertEqual([], result.blocked)
        bead = self.storage.load_bead(bead.bead_id)
        self.assertEqual(BEAD_DONE, bead.status)

    def test_tester_with_no_tester_scope_work_remains_stays_completed(self) -> None:
        bead = self.storage.create_bead(title="Test work", agent_type="tester", description="validate")
        runner = FakeRunner(
            results={
                bead.bead_id: AgentRunResult(
                    outcome="completed",
                    summary="Tests run complete",
                    remaining="No tester-scope work remains for this bead.",
                )
            }
        )
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))
        result = scheduler.run_once()
        self.assertEqual([bead.bead_id], result.completed)
        self.assertEqual([], result.blocked)
        bead = self.storage.load_bead(bead.bead_id)
        self.assertEqual(BEAD_DONE, bead.status)

    def test_review_with_none_for_this_bead_remaining_stays_completed(self) -> None:
        bead = self.storage.create_bead(title="Review work", agent_type="review", description="inspect")
        runner = FakeRunner(
            results={
                bead.bead_id: AgentRunResult(
                    outcome="completed",
                    summary="Review finished",
                    remaining="None for this bead.",
                )
            }
        )
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))
        result = scheduler.run_once()
        self.assertEqual([bead.bead_id], result.completed)
        self.assertEqual([], result.blocked)
        bead = self.storage.load_bead(bead.bead_id)
        self.assertEqual(BEAD_DONE, bead.status)

    def test_review_with_no_gaps_identified_remaining_stays_completed(self) -> None:
        bead = self.storage.create_bead(title="Review work", agent_type="review", description="inspect")
        runner = FakeRunner(
            results={
                bead.bead_id: AgentRunResult(
                    outcome="completed",
                    summary="Review finished",
                    remaining="No correctness, coverage, or documentation gaps were identified in the reviewed scope.",
                )
            }
        )
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))
        result = scheduler.run_once()
        self.assertEqual([bead.bead_id], result.completed)
        self.assertEqual([], result.blocked)
        bead = self.storage.load_bead(bead.bead_id)
        self.assertEqual(BEAD_DONE, bead.status)

    def test_review_with_no_findings_discovered_remaining_stays_completed(self) -> None:
        bead = self.storage.create_bead(title="Review work", agent_type="review", description="inspect")
        runner = FakeRunner(
            results={
                bead.bead_id: AgentRunResult(
                    outcome="completed",
                    summary="Review finished",
                    remaining="No findings discovered in this review pass.",
                )
            }
        )
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))
        result = scheduler.run_once()
        self.assertEqual([bead.bead_id], result.completed)
        self.assertEqual([], result.blocked)
        bead = self.storage.load_bead(bead.bead_id)
        self.assertEqual(BEAD_DONE, bead.status)

    # ------------------------------------------------------------------
    # Finalize / corrective interaction
    # ------------------------------------------------------------------

    def test_review_needs_changes_no_duplicate_corrective_on_finalize(self) -> None:
        bead = self.storage.create_bead(title="Review", agent_type="review", description="inspect")
        existing_corrective = self.storage.create_bead(
            title="Existing corrective",
            agent_type="developer",
            description="fix",
            parent_id=bead.bead_id,
            status=BEAD_IN_PROGRESS,
            metadata={"auto_corrective_for": bead.bead_id},
        )
        bead.metadata["auto_corrective_bead_id"] = existing_corrective.bead_id
        self.storage.save_bead(bead)
        runner = FakeRunner(
            results={
                bead.bead_id: AgentRunResult(
                    outcome="completed",
                    summary="Review still finds issues",
                    verdict="needs_changes",
                    findings_count=1,
                    next_agent="developer",
                )
            }
        )
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))
        result = scheduler.run_once()
        self.assertEqual([bead.bead_id], result.blocked)
        self.assertEqual([], result.correctives_created)

    def test_review_approved_does_not_create_corrective(self) -> None:
        bead = self.storage.create_bead(title="Review", agent_type="review", description="inspect")
        runner = FakeRunner(
            results={
                bead.bead_id: AgentRunResult(
                    outcome="completed",
                    summary="All good",
                    verdict="approved",
                    findings_count=0,
                )
            }
        )
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))
        result = scheduler.run_once()
        self.assertEqual([bead.bead_id], result.completed)
        self.assertEqual([], result.correctives_created)

    # ------------------------------------------------------------------
    # Scheduler telemetry integration tests (B0123)
    # ------------------------------------------------------------------

    def _run_bead_with_telemetry(self, outcome="completed", telemetry=None):
        """Helper: create a developer bead, run it through scheduler with given telemetry."""
        bead = self.storage.create_bead(title="Telemetry test", agent_type="developer", description="work")
        runner = FakeRunner(
            results={
                bead.bead_id: AgentRunResult(
                    outcome=outcome,
                    summary="done" if outcome == "completed" else "problem",
                    completed="implemented",
                    remaining="",
                    risks="none",
                    expected_files=["src/app.py"],
                    touched_files=["src/app.py"],
                    changed_files=["src/app.py"],
                    telemetry=telemetry,
                    block_reason="" if outcome != "blocked" else "blocked reason",
                )
            },
            writes={bead.bead_id: {"src/app.py": "print('ok')\n"}},
        )
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))
        result = scheduler.run_once()
        return bead.bead_id, result

    def test_telemetry_populates_bead_metadata(self) -> None:
        """After run, bead.metadata['telemetry'] is populated from AgentRunResult.telemetry."""
        telemetry = {"source": "measured", "duration_ms": 1234, "prompt_chars": 500, "prompt_lines": 10}
        bead_id, _ = self._run_bead_with_telemetry(telemetry=telemetry)
        bead = self.storage.load_bead(bead_id)
        self.assertIn("telemetry", bead.metadata)
        self.assertEqual(bead.metadata["telemetry"]["source"], "measured")
        self.assertEqual(bead.metadata["telemetry"]["duration_ms"], 1234)

    def test_telemetry_history_grows_with_attempts(self) -> None:
        """telemetry_history grows with each attempt."""
        bead = self.storage.create_bead(title="History test", agent_type="developer", description="work")
        telemetry1 = {"source": "measured", "duration_ms": 100}
        telemetry2 = {"source": "measured", "duration_ms": 200}

        # Simulate two runs by manually invoking _store_telemetry on the finalizer
        result1 = AgentRunResult(outcome="failed", summary="fail1", telemetry=telemetry1, block_reason="err")
        result2 = AgentRunResult(outcome="completed", summary="ok", telemetry=telemetry2)

        scheduler = Scheduler(self.storage, FakeRunner(), WorktreeManager(self.root, self.storage.worktrees_dir))
        scheduler._executor._finalizer._store_telemetry(bead, result1)
        scheduler._executor._finalizer._store_telemetry(bead, result2)

        history = bead.metadata.get("telemetry_history", [])
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["attempt"], 1)
        self.assertEqual(history[1]["attempt"], 2)
        self.assertEqual(history[0]["duration_ms"], 100)
        self.assertEqual(history[1]["duration_ms"], 200)

    def test_telemetry_history_capped_at_default_10(self) -> None:
        """telemetry_history is capped at 10 entries by default."""
        bead = self.storage.create_bead(title="Cap test", agent_type="developer", description="work")
        scheduler = Scheduler(self.storage, FakeRunner(), WorktreeManager(self.root, self.storage.worktrees_dir))
        for i in range(15):
            result = AgentRunResult(outcome="completed", summary=f"run {i}", telemetry={"source": "measured", "duration_ms": i})
            scheduler._executor._finalizer._store_telemetry(bead, result)

        history = bead.metadata["telemetry_history"]
        self.assertEqual(len(history), 10)
        # First 10 attempts get sequential numbers; after cap, attempt = len(history)+1
        # which plateaus at cap+1 once history is full
        self.assertEqual(history[0]["attempt"], 6)
        self.assertEqual(history[-1]["attempt"], 11)

    def test_telemetry_max_attempts_env_var_override(self) -> None:
        """ORCHESTRATOR_TELEMETRY_MAX_ATTEMPTS env var overrides default cap."""
        bead = self.storage.create_bead(title="Env cap test", agent_type="developer", description="work")
        scheduler = Scheduler(self.storage, FakeRunner(), WorktreeManager(self.root, self.storage.worktrees_dir))
        with patch.dict(os.environ, {"ORCHESTRATOR_TELEMETRY_MAX_ATTEMPTS": "3"}):
            for i in range(5):
                result = AgentRunResult(outcome="completed", summary=f"run {i}", telemetry={"source": "measured", "duration_ms": i})
                scheduler._executor._finalizer._store_telemetry(bead, result)

        history = bead.metadata["telemetry_history"]
        self.assertEqual(len(history), 3)
        self.assertEqual(history[0]["attempt"], 3)
        self.assertEqual(history[-1]["attempt"], 4)

    def test_telemetry_invalid_env_var_falls_back_to_default(self) -> None:
        """Invalid ORCHESTRATOR_TELEMETRY_MAX_ATTEMPTS values fall back to default 10."""
        for bad_value in ["abc", "0", "-5", ""]:
            with patch.dict(os.environ, {"ORCHESTRATOR_TELEMETRY_MAX_ATTEMPTS": bad_value}):
                self.assertEqual(BeadFinalizer._telemetry_max_attempts(), 10, f"Failed for value: {bad_value!r}")

    def test_telemetry_captured_for_completed_outcome(self) -> None:
        """Telemetry is stored when outcome is completed."""
        telemetry = {"source": "measured", "duration_ms": 500}
        bead_id, result = self._run_bead_with_telemetry(outcome="completed", telemetry=telemetry)
        self.assertIn(bead_id, result.completed)
        bead = self.storage.load_bead(bead_id)
        self.assertIn("telemetry", bead.metadata)

    def test_telemetry_captured_for_blocked_outcome(self) -> None:
        """Telemetry is stored when outcome is blocked."""
        telemetry = {"source": "measured", "duration_ms": 300}
        bead_id, result = self._run_bead_with_telemetry(outcome="blocked", telemetry=telemetry)
        self.assertIn(bead_id, result.blocked)
        bead = self.storage.load_bead(bead_id)
        self.assertIn("telemetry", bead.metadata)

    def test_telemetry_captured_for_failed_outcome(self) -> None:
        """Telemetry is stored when outcome is failed."""
        telemetry = {"source": "measured", "duration_ms": 200}
        bead_id, result = self._run_bead_with_telemetry(outcome="failed", telemetry=telemetry)
        self.assertIn(bead_id, result.blocked)
        bead = self.storage.load_bead(bead_id)
        self.assertIn("telemetry", bead.metadata)

    def test_telemetry_none_gracefully_handled(self) -> None:
        """When telemetry is None, no telemetry metadata is written."""
        bead_id, _ = self._run_bead_with_telemetry(telemetry=None)
        bead = self.storage.load_bead(bead_id)
        self.assertNotIn("telemetry", bead.metadata)
        self.assertNotIn("telemetry_history", bead.metadata)

    def test_telemetry_artifact_file_written(self) -> None:
        """After a run with telemetry, an artifact file exists in telemetry dir."""
        telemetry = {"source": "measured", "duration_ms": 700, "prompt_text": "hello", "response_text": "world"}
        bead_id, _ = self._run_bead_with_telemetry(telemetry=telemetry)
        artifact_dir = self.storage.telemetry_dir / bead_id
        self.assertTrue(artifact_dir.exists(), "Telemetry artifact directory should exist")
        artifacts = list(artifact_dir.glob("*.json"))
        self.assertGreaterEqual(len(artifacts), 1, "At least one artifact file should exist")
        data = json.loads(artifacts[0].read_text())
        self.assertEqual(data["bead_id"], bead_id)
        self.assertEqual(data["telemetry_version"], 1)

    def test_telemetry_write_failure_preserves_bead_outcome(self) -> None:
        """If telemetry artifact write fails, the bead outcome is preserved."""
        bead = self.storage.create_bead(title="Write fail test", agent_type="developer", description="work")
        telemetry = {"source": "measured", "duration_ms": 100}
        result = AgentRunResult(outcome="completed", summary="ok", telemetry=telemetry)
        scheduler = Scheduler(self.storage, FakeRunner(), WorktreeManager(self.root, self.storage.worktrees_dir))

        # Break the telemetry write by making write_telemetry_artifact raise
        original_write = self.storage.write_telemetry_artifact
        def failing_write(**kwargs):
            raise IOError("disk full")
        self.storage.write_telemetry_artifact = failing_write

        try:
            scheduler._executor._finalizer._store_telemetry(bead, result)
        finally:
            self.storage.write_telemetry_artifact = original_write

        # Telemetry metadata should still be set (it's written before the artifact)
        self.assertIn("telemetry", bead.metadata)
        # A warning record should be appended
        warnings = [r for r in bead.execution_history if r.event == "telemetry_write_warning"]
        self.assertEqual(len(warnings), 1)
        self.assertIn("disk full", warnings[0].summary)

    def test_telemetry_lightweight_excludes_prompt_response_text(self) -> None:
        """bead.metadata['telemetry'] excludes heavy prompt_text and response_text fields."""
        telemetry = {"source": "measured", "duration_ms": 42, "prompt_text": "big prompt", "response_text": "big response"}
        bead_id, _ = self._run_bead_with_telemetry(telemetry=telemetry)
        bead = self.storage.load_bead(bead_id)
        self.assertNotIn("prompt_text", bead.metadata["telemetry"])
        self.assertNotIn("response_text", bead.metadata["telemetry"])
        self.assertEqual(bead.metadata["telemetry"]["duration_ms"], 42)

    def test_telemetry_attempt_numbering_sequential(self) -> None:
        """Attempt numbers in telemetry_history are sequential starting from 1."""
        bead = self.storage.create_bead(title="Attempt num test", agent_type="developer", description="work")
        scheduler = Scheduler(self.storage, FakeRunner(), WorktreeManager(self.root, self.storage.worktrees_dir))
        for i in range(3):
            result = AgentRunResult(outcome="completed", summary=f"run {i}", telemetry={"source": "measured", "duration_ms": i * 100})
            scheduler._executor._finalizer._store_telemetry(bead, result)

        history = bead.metadata["telemetry_history"]
        attempts = [entry["attempt"] for entry in history]
        self.assertEqual(attempts, [1, 2, 3])


if __name__ == "__main__":
    unittest.main()
