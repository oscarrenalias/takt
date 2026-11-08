"""Discovered-issue bead creation: finalize, dedup, recovery, regression tests."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_takt.gitutils import WorktreeManager
from agent_takt.models import BEAD_BLOCKED, BEAD_DONE, BEAD_OPEN, AgentRunResult
from agent_takt.runner import NO_STRUCTURED_OUTPUT_SENTINEL
from agent_takt.scheduler import Scheduler
from agent_takt.storage import RepositoryStorage

RepositoryStorage._auto_commit = False

from helpers import FakeRunner, OrchestratorTests  # noqa: E402


class _Results(dict):
    """Results dict that returns a blocked result for beads not configured."""

    def __missing__(self, key):
        return AgentRunResult(outcome="blocked", summary="n/a", block_reason="unconfigured")


def _done(issues, **kw) -> AgentRunResult:
    return AgentRunResult(
        outcome="completed", summary="ok", completed="done", verdict="approved",
        requires_followup=False, discovered_issues=issues, **kw,
    )


class DiscoveredIssueTests(OrchestratorTests):
    def _run(self, results):
        runner = FakeRunner(results=_Results(results))
        scheduler = Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))
        return scheduler, scheduler.run_once()

    def _discovered(self):
        return [b for b in self.storage.list_beads() if "discovered" in b.labels]

    def test_corrective_bead_reports_unrelated_issue(self) -> None:
        """Scenario: agent-context / Discovered issues become untriaged beads / Corrective bead reports an unrelated issue"""
        parent = self.storage.create_bead(title="Parent", agent_type="developer", description="p")
        corrective = self.storage.create_bead(
            title="Fix parent", agent_type="developer", description="fix",
            parent_id=parent.bead_id, metadata={"auto_corrective_for": parent.bead_id},
        )
        issue = {"title": "Flaky cache", "description": "cache flakes", "agent_type": "developer",
                 "expected_files": ["src/cache.py"]}
        self._run({corrective.bead_id: _done([issue])})
        found = self._discovered()
        self.assertEqual(1, len(found))
        b = found[0]
        self.assertEqual(BEAD_OPEN, b.status)
        self.assertEqual(b.bead_id, b.feature_root_id)
        self.assertIsNone(b.parent_id)
        self.assertEqual(["discovered"], b.labels)
        self.assertEqual(corrective.bead_id, b.metadata["discovered_issue_of"])
        self.assertEqual(["src/cache.py"], b.expected_files)
        events = (self.root / ".takt" / "logs" / "events.jsonl").read_text()
        self.assertIn("issue_discovered", events)
        reporter = self.storage.load_bead(corrective.bead_id)
        self.assertEqual(1, sum(1 for e in reporter.execution_history if e.event == "discovered_issue_created"))

    def test_defect_issue_gets_defect_type(self) -> None:
        """Scenario: agent-context / Discovered issues become untriaged beads / Defect issue gets defect type"""
        bead = self.storage.create_bead(title="Dev", agent_type="developer", description="d")
        self._run({bead.bead_id: _done([{"title": "Broken thing", "description": "x", "agent_type": "defect"}])})
        (b,) = self._discovered()
        self.assertEqual("defect", b.agent_type)
        self.assertEqual("defect", b.bead_type)

    def test_discovered_beads_are_not_dispatched(self) -> None:
        """Scenario: agent-context / Discovered issues become untriaged beads / Discovered beads are not dispatched"""
        bead = self.storage.create_bead(title="Dev", agent_type="developer", description="d")
        scheduler, _ = self._run({bead.bead_id: _done([{"title": "Later", "description": "x"}])})
        (b,) = self._discovered()
        result = scheduler.run_once()
        self.assertNotIn(b.bead_id, result.started)
        self.assertEqual(BEAD_OPEN, self.storage.load_bead(b.bead_id).status)

    def test_blocked_review_still_reports_issues(self) -> None:
        """Scenario: agent-context / Discovered issues become untriaged beads / Blocked review still reports issues"""
        bead = self.storage.create_bead(title="Review", agent_type="review", description="r")
        res = AgentRunResult(
            outcome="blocked", summary="needs work", verdict="needs_changes", findings_count=1,
            requires_followup=True, block_reason="Unresolved defect", next_agent="developer",
            discovered_issues=[{"title": "Side problem", "description": "seen", "agent_type": "developer"}],
        )
        _, result = self._run({bead.bead_id: res})
        self.assertIn(bead.bead_id, result.blocked)
        self.assertEqual(["Side problem"], [b.title for b in self._discovered()])

    def test_retried_bead_does_not_duplicate(self) -> None:
        """Scenario: agent-context / Discovered issues become untriaged beads / Retried bead does not duplicate"""
        bead = self.storage.create_bead(title="Dev", agent_type="developer", description="d")
        scheduler, _ = self._run({bead.bead_id: _done([{"title": "Dup issue", "description": "x"}])})
        self.assertEqual(1, len(self._discovered()))
        reloaded = self.storage.load_bead(bead.bead_id)
        # Repeat creation directly with case/whitespace variants.
        from agent_takt.scheduler.finalize import BeadFinalizer

        f = BeadFinalizer.__new__(BeadFinalizer)
        f.storage = self.storage
        created = f._create_discovered_issue_beads(reloaded, [{"title": "  DUP ISSUE "}, {"title": "dup issue"}])
        self.assertEqual([], created)
        self.assertEqual(1, len(self._discovered()))

    def test_same_payload_variants_create_one_bead(self) -> None:
        bead = self.storage.create_bead(title="Dev", agent_type="developer", description="d")
        issues = [{"title": "Same"}, {"title": " same  "}, {"title": "SAME"}]
        self._run({bead.bead_id: _done(issues)})
        self.assertEqual(1, len(self._discovered()))

    def test_invalid_entries_create_nothing(self) -> None:
        bead = self.storage.create_bead(title="Dev", agent_type="developer", description="d")
        self._run({bead.bead_id: _done(["junk", {"title": ""}, {"title": "   "}, {"description": "x"}, None])})
        self.assertEqual([], self._discovered())

    def test_older_handoffs_without_the_field(self) -> None:
        """Scenario: agent-context / Discovered issues become untriaged beads / Older handoffs without the field"""
        from agent_takt.runner import _payload_to_run_result

        r = _payload_to_run_result({"outcome": "completed", "summary": "s"}, "developer")
        self.assertEqual([], r.discovered_issues)
        bead = self.storage.create_bead(title="Dev", agent_type="developer", description="d")
        _, result = self._run({bead.bead_id: _done([])})
        self.assertIn(bead.bead_id, result.completed)
        self.assertEqual([], self._discovered())

    def test_recovery_creates_discovered_beads_once(self) -> None:
        """Scenario: agent-context / Discovered issues become untriaged beads / Recovery creates discovered beads once"""
        original = self.storage.create_bead(title="Impl Z", agent_type="developer", description="z")
        original.status = BEAD_BLOCKED
        original.block_reason = NO_STRUCTURED_OUTPUT_SENTINEL
        self.storage.save_bead(original)
        rid = self.storage.allocate_child_bead_id(original.bead_id, "recovery")
        recovery = self.storage.create_bead(
            bead_id=rid, title="Recover", agent_type="recovery", bead_type="recovery",
            description="r", parent_id=original.bead_id, feature_root_id=original.feature_root_id,
            recovery_for=original.bead_id,
        )
        original.metadata["auto_recovery_bead_id"] = recovery.bead_id
        self.storage.save_bead(original)
        issue = {"title": "Found during recovery", "description": "x"}
        scheduler, _ = self._run({recovery.bead_id: _done([issue])})
        found = self._discovered()
        self.assertEqual(1, len(found))
        self.assertEqual(original.bead_id, found[0].metadata["discovered_issue_of"])
        self.assertEqual([], [b for b in found if b.metadata["discovered_issue_of"] == recovery.bead_id])
        self.assertEqual(BEAD_DONE, self.storage.load_bead(original.bead_id).status)
        scheduler.run_once()
        self.assertEqual(1, len(self._discovered()))


if __name__ == "__main__":
    unittest.main()
