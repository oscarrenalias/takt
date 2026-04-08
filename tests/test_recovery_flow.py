"""Tests for the structured-output recovery flow.

Covers:
1. build_recovery_prompt content (title, description, prose, git diff, schema, JSON instruction)
2. AGENT_OUTPUT_SCHEMA structural identity between runner.py and prompts.py
3. Type registration: AGENT_TYPES, BEAD_TYPES, allowed_skill_ids, supported_agent_types
4. ClaudeCodeAgentRunner passes empty tools for recovery agent_type
5. load_guardrail_template resolves recovery.md
6. Recovery bead creation: one bead created for NO_STRUCTURED_OUTPUT failure
7. Idempotency: second scheduler cycle does NOT create a second recovery bead
8. _can_plan_corrective returns False when auto_recovery_bead_id is set
9. Non-matching block_reason → normal corrective path (no recovery bead)
10. Recovery completion: original bead marked done, handoff applied, followups created
11. Recovery failure containment: recovery bead (bead_type=recovery) fail → no second recovery
12. Recovery bead blocked → original stays blocked, no corrective created
13. command_retry: pending recovery bead → warns and returns 0
14. command_retry: done recovery bead → retry proceeds normally
15. command_retry: missing recovery bead → retry proceeds normally (exception swallowed)
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_takt.cli.commands.misc import command_retry
from agent_takt.console import ConsoleReporter
from agent_takt.gitutils import WorktreeManager
from agent_takt.models import (
    AGENT_TYPES,
    BEAD_BLOCKED,
    BEAD_DONE,
    BEAD_READY,
    BEAD_TYPES,
    AgentRunResult,
)
from agent_takt.prompts import (
    AGENT_OUTPUT_SCHEMA as PROMPTS_SCHEMA,
    BUILT_IN_AGENT_TYPES,
    build_recovery_prompt,
    load_guardrail_template,
    supported_agent_types,
)
from agent_takt.runner import (
    AGENT_OUTPUT_SCHEMA as RUNNER_SCHEMA,
    NO_STRUCTURED_OUTPUT_SENTINEL,
    ClaudeCodeAgentRunner,
)
from agent_takt.scheduler import Scheduler
from agent_takt.skills import allowed_skill_ids
from agent_takt.storage import RepositoryStorage

RepositoryStorage._auto_commit = False

from helpers import FakeRunner, OrchestratorTests  # noqa: E402


# ---------------------------------------------------------------------------
# 1. build_recovery_prompt content
# ---------------------------------------------------------------------------

class BuildRecoveryPromptTests(unittest.TestCase):

    def _minimal_bead(self):
        from agent_takt.models import Bead
        return Bead(
            bead_id="B-origtest",
            title="My feature bead",
            agent_type="developer",
            description="Implement a feature",
        )

    def test_prompt_contains_bead_title(self) -> None:
        bead = self._minimal_bead()
        prompt = build_recovery_prompt(bead, "Some prose output", "diff --git ...")
        self.assertIn("My feature bead", prompt)

    def test_prompt_contains_bead_description(self) -> None:
        bead = self._minimal_bead()
        prompt = build_recovery_prompt(bead, "Some prose output", "diff --git ...")
        self.assertIn("Implement a feature", prompt)

    def test_prompt_contains_prose_output(self) -> None:
        bead = self._minimal_bead()
        prompt = build_recovery_prompt(bead, "Agent wrote some prose here", "")
        self.assertIn("Agent wrote some prose here", prompt)

    def test_prompt_contains_git_diff(self) -> None:
        bead = self._minimal_bead()
        prompt = build_recovery_prompt(bead, "", "diff --git a/src/x.py b/src/x.py")
        self.assertIn("diff --git a/src/x.py b/src/x.py", prompt)

    def test_prompt_contains_output_schema(self) -> None:
        bead = self._minimal_bead()
        prompt = build_recovery_prompt(bead, "", "")
        # Schema is embedded as JSON; check for a distinctive key
        self.assertIn('"additionalProperties"', prompt)
        self.assertIn('"required"', prompt)

    def test_prompt_contains_json_only_instruction(self) -> None:
        bead = self._minimal_bead()
        prompt = build_recovery_prompt(bead, "", "")
        self.assertIn("CRITICAL", prompt)
        # Must instruct agent to emit only JSON
        self.assertIn("JSON", prompt)
        self.assertIn("parseable as JSON", prompt)

    def test_prompt_not_empty_for_minimal_bead(self) -> None:
        bead = self._minimal_bead()
        prompt = build_recovery_prompt(bead, "", "")
        self.assertGreater(len(prompt), 200)

    def test_prompt_handles_empty_prose_output_without_error(self) -> None:
        bead = self._minimal_bead()
        # Should not raise
        prompt = build_recovery_prompt(bead, "", "")
        self.assertIsInstance(prompt, str)

    def test_prompt_handles_empty_git_diff_without_error(self) -> None:
        bead = self._minimal_bead()
        prompt = build_recovery_prompt(bead, "prose here", "")
        self.assertIsInstance(prompt, str)


# ---------------------------------------------------------------------------
# 2. AGENT_OUTPUT_SCHEMA structural identity between runner.py and prompts.py
# ---------------------------------------------------------------------------

class SchemaConsistencyTests(unittest.TestCase):

    def test_runner_and_prompts_schema_required_fields_match(self) -> None:
        self.assertEqual(
            sorted(RUNNER_SCHEMA["required"]),
            sorted(PROMPTS_SCHEMA["required"]),
        )

    def test_runner_and_prompts_schema_property_keys_match(self) -> None:
        self.assertEqual(
            sorted(RUNNER_SCHEMA["properties"].keys()),
            sorted(PROMPTS_SCHEMA["properties"].keys()),
        )

    def test_runner_and_prompts_schema_new_beads_agent_type_enum_match(self) -> None:
        runner_enum = RUNNER_SCHEMA["properties"]["new_beads"]["items"]["properties"]["agent_type"]["enum"]
        prompts_enum = PROMPTS_SCHEMA["properties"]["new_beads"]["items"]["properties"]["agent_type"]["enum"]
        self.assertEqual(sorted(runner_enum), sorted(prompts_enum))

    def test_runner_and_prompts_schema_outcome_enum_match(self) -> None:
        runner_enum = RUNNER_SCHEMA["properties"]["outcome"]["enum"]
        prompts_enum = PROMPTS_SCHEMA["properties"]["outcome"]["enum"]
        self.assertEqual(sorted(runner_enum), sorted(prompts_enum))


# ---------------------------------------------------------------------------
# 3. Type registration
# ---------------------------------------------------------------------------

class TypeRegistrationTests(unittest.TestCase):

    def test_agent_types_contains_recovery(self) -> None:
        self.assertIn("recovery", AGENT_TYPES)

    def test_bead_types_contains_recovery(self) -> None:
        self.assertIn("recovery", BEAD_TYPES)

    def test_allowed_skill_ids_for_recovery_is_base_orchestrator_only(self) -> None:
        self.assertEqual(["core/base-orchestrator"], allowed_skill_ids("recovery"))

    def test_supported_agent_types_includes_recovery(self) -> None:
        self.assertIn("recovery", supported_agent_types())

    def test_built_in_agent_types_includes_recovery(self) -> None:
        self.assertIn("recovery", BUILT_IN_AGENT_TYPES)


# ---------------------------------------------------------------------------
# 4. ClaudeCodeAgentRunner: empty tools for recovery
# ---------------------------------------------------------------------------

class ClaudeCodeAgentRunnerRecoveryToolsTests(unittest.TestCase):

    def test_exec_json_with_response_uses_empty_tools_for_recovery(self) -> None:
        """_exec_json_with_response should build the command with an empty --allowedTools value for recovery."""
        import subprocess
        runner = ClaudeCodeAgentRunner()
        captured_cmd: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            captured_cmd.append(cmd)
            # Simulate a TimeoutExpired to avoid actually running claude
            raise subprocess.TimeoutExpired(cmd, 0)

        orig = subprocess.run
        subprocess.run = fake_run  # type: ignore[assignment]
        try:
            with self.assertRaises((subprocess.TimeoutExpired, RuntimeError)):
                runner._exec_json_with_response(
                    "prompt text",
                    schema={},
                    workdir=Path("/tmp"),
                    agent_type="recovery",
                )
        finally:
            subprocess.run = orig

        self.assertTrue(len(captured_cmd) >= 1, "Expected subprocess.run to be called")
        cmd = captured_cmd[0]
        # --allowedTools should be present with an empty string value
        self.assertIn("--allowedTools", cmd)
        idx = cmd.index("--allowedTools")
        self.assertEqual("", cmd[idx + 1])

    def test_exec_json_with_response_uses_nonempty_tools_for_developer(self) -> None:
        """Non-recovery agents should receive non-empty tool lists."""
        import subprocess
        runner = ClaudeCodeAgentRunner()
        captured_cmd: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            captured_cmd.append(cmd)
            raise subprocess.TimeoutExpired(cmd, 0)

        orig = subprocess.run
        subprocess.run = fake_run  # type: ignore[assignment]
        try:
            with self.assertRaises((subprocess.TimeoutExpired, RuntimeError)):
                runner._exec_json_with_response(
                    "prompt text",
                    schema={},
                    workdir=Path("/tmp"),
                    agent_type="developer",
                )
        finally:
            subprocess.run = orig

        cmd = captured_cmd[0]
        idx = cmd.index("--allowedTools")
        self.assertNotEqual("", cmd[idx + 1])


# ---------------------------------------------------------------------------
# 5. load_guardrail_template resolves recovery.md
# ---------------------------------------------------------------------------

class LoadGuardrailTemplateRecoveryTests(OrchestratorTests):

    def test_load_guardrail_template_finds_recovery_template(self) -> None:
        path, text = load_guardrail_template("recovery", root=self.root)
        self.assertTrue(path.is_file())
        self.assertIn("recovery", path.name.lower())
        self.assertGreater(len(text), 20)

    def test_load_guardrail_template_recovery_text_contains_guardrail_content(self) -> None:
        _, text = load_guardrail_template("recovery", root=self.root)
        # Template should mention that no tools may be called
        self.assertIn("no tools", text.lower())


# ---------------------------------------------------------------------------
# 6 & 7. Recovery bead creation + idempotency
# ---------------------------------------------------------------------------

class RecoveryBeadCreationTests(OrchestratorTests):

    def _no_structured_output_result(self) -> AgentRunResult:
        return AgentRunResult(
            outcome="failed",
            summary=f"Failed: {NO_STRUCTURED_OUTPUT_SENTINEL}",
            block_reason=NO_STRUCTURED_OUTPUT_SENTINEL,
        )

    def test_failed_bead_with_sentinel_creates_recovery_child(self) -> None:
        bead = self.storage.create_bead(
            title="Implement X", agent_type="developer", description="do work"
        )
        runner = FakeRunner(results={bead.bead_id: self._no_structured_output_result()})
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))
        result = scheduler.run_once()

        self.assertIn(bead.bead_id, result.blocked)

        bead = self.storage.load_bead(bead.bead_id)
        self.assertEqual(BEAD_BLOCKED, bead.status)

        recovery_id = bead.metadata.get("auto_recovery_bead_id")
        self.assertIsNotNone(recovery_id, "auto_recovery_bead_id must be set on original bead")

        recovery = self.storage.load_bead(recovery_id)
        self.assertEqual("recovery", recovery.bead_type)
        self.assertEqual("recovery", recovery.agent_type)
        self.assertEqual(bead.bead_id, recovery.recovery_for)

    def test_recovery_bead_is_child_of_original(self) -> None:
        bead = self.storage.create_bead(
            title="Implement X", agent_type="developer", description="do work"
        )
        runner = FakeRunner(results={bead.bead_id: self._no_structured_output_result()})
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))
        scheduler.run_once()

        bead = self.storage.load_bead(bead.bead_id)
        recovery_id = bead.metadata["auto_recovery_bead_id"]
        recovery = self.storage.load_bead(recovery_id)
        self.assertEqual(bead.bead_id, recovery.parent_id)

    def test_second_cycle_does_not_create_second_recovery_bead(self) -> None:
        """Idempotency guard: auto_recovery_bead_id prevents duplicate recovery beads."""
        bead = self.storage.create_bead(
            title="Implement X", agent_type="developer", description="do work"
        )
        runner = FakeRunner(results={bead.bead_id: self._no_structured_output_result()})
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))

        # First cycle — creates recovery bead.
        scheduler.run_once()
        bead_after_first = self.storage.load_bead(bead.bead_id)
        recovery_id = bead_after_first.metadata["auto_recovery_bead_id"]

        all_beads_after_first = {b.bead_id for b in self.storage.list_beads()}

        # Put the original bead back to ready so the scheduler picks it up again.
        bead_after_first.status = BEAD_READY
        self.storage.save_bead(bead_after_first)

        # Second cycle — recovery bead already exists; no new recovery bead.
        scheduler.run_once()

        all_beads_after_second = {b.bead_id for b in self.storage.list_beads()}
        # No new beads should have been added beyond the recovery bead created in cycle 1.
        new_beads = all_beads_after_second - all_beads_after_first
        recovery_beads = [
            bid for bid in new_beads
            if self.storage.load_bead(bid).bead_type == "recovery"
        ]
        self.assertEqual([], recovery_beads, "No second recovery bead should be created")

    def test_can_plan_corrective_false_when_auto_recovery_bead_id_set(self) -> None:
        """_can_plan_corrective must return False when a recovery bead is already pending."""
        bead = self.storage.create_bead(
            title="Implement X", agent_type="developer", description="do work"
        )
        runner = FakeRunner(results={bead.bead_id: self._no_structured_output_result()})
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))
        scheduler.run_once()

        bead = self.storage.load_bead(bead.bead_id)
        self.assertIn("auto_recovery_bead_id", bead.metadata)

        followup_manager = scheduler._executor._followups
        self.assertFalse(followup_manager._can_plan_corrective(bead))

    def test_unrelated_failure_uses_corrective_not_recovery(self) -> None:
        """A failure whose block_reason does NOT contain the sentinel uses the corrective path."""
        bead = self.storage.create_bead(
            title="Implement Y", agent_type="developer", description="do work"
        )
        runner = FakeRunner(
            results={
                bead.bead_id: AgentRunResult(
                    outcome="failed",
                    summary="Some other error",
                    block_reason="Subprocess timed out after 300 seconds",
                )
            }
        )
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))
        result = scheduler.run_once()

        self.assertIn(bead.bead_id, result.blocked)
        bead = self.storage.load_bead(bead.bead_id)
        # No recovery bead.
        self.assertNotIn("auto_recovery_bead_id", bead.metadata)


# ---------------------------------------------------------------------------
# 8. Recovery completion — happy path
# ---------------------------------------------------------------------------

class RecoveryCompletionTests(OrchestratorTests):

    def _setup_recovery_scenario(self):
        """Create an original developer bead already blocked with a pending recovery bead."""
        original = self.storage.create_bead(
            title="Implement feature Z",
            agent_type="developer",
            description="implement Z",
        )
        original.status = BEAD_BLOCKED
        original.block_reason = NO_STRUCTURED_OUTPUT_SENTINEL
        original.retries = 1
        self.storage.save_bead(original)

        # Manually create the recovery bead (mirrors what BeadFinalizer._create_recovery_bead does).
        recovery_id = self.storage.allocate_child_bead_id(original.bead_id, "recovery")
        recovery = self.storage.create_bead(
            bead_id=recovery_id,
            title=f"Recover structured output for {original.bead_id}",
            agent_type="recovery",
            bead_type="recovery",
            description="Synthesise JSON handoff",
            parent_id=original.bead_id,
            dependencies=[],
            acceptance_criteria=[],
            linked_docs=[],
            feature_root_id=original.feature_root_id,
            recovery_for=original.bead_id,
        )
        original.metadata["auto_recovery_bead_id"] = recovery.bead_id
        self.storage.save_bead(original)

        return original, recovery

    def test_recovery_completion_marks_original_done(self) -> None:
        original, recovery = self._setup_recovery_scenario()

        runner = FakeRunner(
            results={
                recovery.bead_id: AgentRunResult(
                    outcome="completed",
                    summary="Synthesised handoff from prior agent prose",
                    completed="Extracted structured output from prose",
                    remaining="",
                    risks="",
                    verdict="approved",
                    findings_count=0,
                    requires_followup=False,
                    touched_files=["src/feature_z.py"],
                    changed_files=["src/feature_z.py"],
                )
            }
        )
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))
        result = scheduler.run_once()

        self.assertIn(recovery.bead_id, result.completed)
        self.assertIn(original.bead_id, result.completed)

        original_reloaded = self.storage.load_bead(original.bead_id)
        self.assertEqual(BEAD_DONE, original_reloaded.status)
        self.assertEqual("", original_reloaded.block_reason)

    def test_recovery_completion_sets_recovered_by_metadata(self) -> None:
        original, recovery = self._setup_recovery_scenario()

        runner = FakeRunner(
            results={
                recovery.bead_id: AgentRunResult(
                    outcome="completed",
                    summary="Handoff synthesised",
                    completed="done",
                    remaining="",
                    risks="",
                    verdict="approved",
                    findings_count=0,
                    requires_followup=False,
                    touched_files=[],
                    changed_files=[],
                )
            }
        )
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))
        scheduler.run_once()

        original_reloaded = self.storage.load_bead(original.bead_id)
        self.assertEqual(recovery.bead_id, original_reloaded.metadata.get("recovered_by"))

    def test_recovery_completion_applies_handoff_to_original(self) -> None:
        original, recovery = self._setup_recovery_scenario()

        runner = FakeRunner(
            results={
                recovery.bead_id: AgentRunResult(
                    outcome="completed",
                    summary="Handoff synthesised",
                    completed="All work done via recovery",
                    remaining="",
                    risks="minimal",
                    verdict="approved",
                    findings_count=0,
                    requires_followup=False,
                    touched_files=["src/z.py"],
                    changed_files=["src/z.py"],
                )
            }
        )
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))
        scheduler.run_once()

        original_reloaded = self.storage.load_bead(original.bead_id)
        self.assertEqual("All work done via recovery", original_reloaded.handoff_summary.completed)
        self.assertIn("src/z.py", original_reloaded.touched_files)

    def test_recovery_completion_creates_followup_beads_for_original(self) -> None:
        original, recovery = self._setup_recovery_scenario()

        runner = FakeRunner(
            results={
                recovery.bead_id: AgentRunResult(
                    outcome="completed",
                    summary="Handoff synthesised",
                    completed="done",
                    remaining="",
                    risks="",
                    verdict="approved",
                    findings_count=0,
                    requires_followup=False,
                    touched_files=["src/z.py"],
                    changed_files=["src/z.py"],
                )
            },
            writes={recovery.bead_id: {}},
        )
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))
        scheduler.run_once()

        child_ids = {b.bead_id for b in self.storage.list_beads() if b.parent_id == original.bead_id}
        # Recovery bead itself is a child; followups should also be created.
        followup_ids = child_ids - {recovery.bead_id}
        followup_agent_types = {self.storage.load_bead(bid).agent_type for bid in followup_ids}
        self.assertIn("tester", followup_agent_types)
        self.assertIn("review", followup_agent_types)


# ---------------------------------------------------------------------------
# 9. Recovery failure containment
# ---------------------------------------------------------------------------

class RecoveryFailureContainmentTests(OrchestratorTests):

    def _setup_recovery_bead(self):
        """Create a recovery-type bead directly in the scheduler."""
        recovery = self.storage.create_bead(
            title="Recover structured output for B-origXXXX",
            agent_type="recovery",
            bead_type="recovery",
            description="Synthesise JSON",
        )
        return recovery

    def test_recovery_bead_failure_does_not_create_second_recovery_bead(self) -> None:
        """A recovery bead that fails with the sentinel must NOT spawn another recovery bead."""
        recovery = self._setup_recovery_bead()
        runner = FakeRunner(
            results={
                recovery.bead_id: AgentRunResult(
                    outcome="failed",
                    summary=f"Failed: {NO_STRUCTURED_OUTPUT_SENTINEL}",
                    block_reason=NO_STRUCTURED_OUTPUT_SENTINEL,
                )
            }
        )
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))
        result = scheduler.run_once()

        self.assertIn(recovery.bead_id, result.blocked)
        recovery_reloaded = self.storage.load_bead(recovery.bead_id)
        # No auto_recovery_bead_id must be set on the recovery bead itself.
        self.assertNotIn("auto_recovery_bead_id", recovery_reloaded.metadata)

        all_beads = self.storage.list_beads()
        nested_recoveries = [
            b for b in all_beads
            if b.recovery_for == recovery.bead_id
        ]
        self.assertEqual([], nested_recoveries)

    def test_recovery_bead_blocked_leaves_original_blocked(self) -> None:
        """A recovery bead with outcome=blocked should NOT complete the original bead."""
        original = self.storage.create_bead(
            title="Implement feature W", agent_type="developer", description="do W"
        )
        original.status = BEAD_BLOCKED
        original.block_reason = NO_STRUCTURED_OUTPUT_SENTINEL
        self.storage.save_bead(original)

        recovery_id = self.storage.allocate_child_bead_id(original.bead_id, "recovery")
        recovery = self.storage.create_bead(
            bead_id=recovery_id,
            title=f"Recover structured output for {original.bead_id}",
            agent_type="recovery",
            bead_type="recovery",
            description="Synthesise JSON handoff",
            parent_id=original.bead_id,
            dependencies=[],
            acceptance_criteria=[],
            linked_docs=[],
            recovery_for=original.bead_id,
        )
        original.metadata["auto_recovery_bead_id"] = recovery.bead_id
        self.storage.save_bead(original)

        runner = FakeRunner(
            results={
                recovery.bead_id: AgentRunResult(
                    outcome="blocked",
                    summary="Could not synthesise handoff",
                    block_reason="Prose was unintelligible",
                    verdict="needs_changes",
                    findings_count=1,
                    requires_followup=True,
                )
            }
        )
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))
        scheduler.run_once()

        original_reloaded = self.storage.load_bead(original.bead_id)
        # Original bead must remain blocked.
        self.assertEqual(BEAD_BLOCKED, original_reloaded.status)


# ---------------------------------------------------------------------------
# 10. command_retry guard
# ---------------------------------------------------------------------------

class CommandRetryGuardTests(OrchestratorTests):

    def _make_console(self) -> tuple[ConsoleReporter, io.StringIO]:
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)
        return console, stream

    def _make_args(self, bead_id: str) -> argparse.Namespace:
        return argparse.Namespace(bead_id=bead_id)

    def test_retry_warns_and_returns_0_when_recovery_bead_is_pending(self) -> None:
        """Retry must be skipped (return 0) when the recovery bead status is 'ready'."""
        original = self.storage.create_bead(
            title="Blocked bead", agent_type="developer", description="work"
        )
        original.status = BEAD_BLOCKED
        self.storage.save_bead(original)

        recovery_id = self.storage.allocate_child_bead_id(original.bead_id, "recovery")
        recovery = self.storage.create_bead(
            bead_id=recovery_id,
            title="Recovery bead",
            agent_type="recovery",
            bead_type="recovery",
            description="Synthesise",
            parent_id=original.bead_id,
            dependencies=[],
            acceptance_criteria=[],
            linked_docs=[],
            recovery_for=original.bead_id,
        )
        # recovery is in 'open'/'ready' state — considered pending.
        recovery.status = BEAD_READY
        self.storage.save_bead(recovery)
        original.metadata["auto_recovery_bead_id"] = recovery.bead_id
        self.storage.save_bead(original)

        console, stream = self._make_console()
        ret = command_retry(self._make_args(original.bead_id), self.storage, console)

        self.assertEqual(0, ret)
        output = stream.getvalue()
        self.assertIn("recovery bead", output.lower())
        # Original bead must still be blocked.
        original_reloaded = self.storage.load_bead(original.bead_id)
        self.assertEqual(BEAD_BLOCKED, original_reloaded.status)

    def test_retry_proceeds_normally_when_recovery_bead_is_done(self) -> None:
        """Retry must proceed when the recovery bead is already done."""
        original = self.storage.create_bead(
            title="Blocked bead", agent_type="developer", description="work"
        )
        original.status = BEAD_BLOCKED
        self.storage.save_bead(original)

        recovery_id = self.storage.allocate_child_bead_id(original.bead_id, "recovery")
        recovery = self.storage.create_bead(
            bead_id=recovery_id,
            title="Recovery bead",
            agent_type="recovery",
            bead_type="recovery",
            description="Synthesise",
            parent_id=original.bead_id,
            dependencies=[],
            acceptance_criteria=[],
            linked_docs=[],
            recovery_for=original.bead_id,
        )
        recovery.status = BEAD_DONE
        self.storage.save_bead(recovery)
        original.metadata["auto_recovery_bead_id"] = recovery.bead_id
        self.storage.save_bead(original)

        console, stream = self._make_console()
        ret = command_retry(self._make_args(original.bead_id), self.storage, console)

        self.assertEqual(0, ret)
        # Bead should be requeued.
        original_reloaded = self.storage.load_bead(original.bead_id)
        self.assertEqual(BEAD_READY, original_reloaded.status)

    def test_retry_proceeds_normally_when_recovery_bead_id_not_found(self) -> None:
        """Retry must proceed when auto_recovery_bead_id points to a non-existent bead."""
        original = self.storage.create_bead(
            title="Blocked bead", agent_type="developer", description="work"
        )
        original.status = BEAD_BLOCKED
        original.metadata["auto_recovery_bead_id"] = "B-nonexistent"
        self.storage.save_bead(original)

        console, _stream = self._make_console()
        ret = command_retry(self._make_args(original.bead_id), self.storage, console)

        self.assertEqual(0, ret)
        original_reloaded = self.storage.load_bead(original.bead_id)
        self.assertEqual(BEAD_READY, original_reloaded.status)

    def test_retry_proceeds_normally_when_no_recovery_bead_metadata(self) -> None:
        """Retry proceeds as usual when auto_recovery_bead_id is absent."""
        original = self.storage.create_bead(
            title="Plain blocked bead", agent_type="developer", description="work"
        )
        original.status = BEAD_BLOCKED
        self.storage.save_bead(original)

        console, _stream = self._make_console()
        ret = command_retry(self._make_args(original.bead_id), self.storage, console)

        self.assertEqual(0, ret)
        original_reloaded = self.storage.load_bead(original.bead_id)
        self.assertEqual(BEAD_READY, original_reloaded.status)


if __name__ == "__main__":
    unittest.main()
