"""Tests for B0136: Per-bead model override with propagation to followup children.

Validates:
1. ClaudeCodeAgentRunner.run_bead uses bead.metadata["model_override"] over config
2. Scheduler._create_followups propagates model_override to followup beads
3. Scheduler._create_followups propagates model_override to discovered sub-beads
4. No propagation when model_override is absent
5. CLI `bead update --model` sets metadata.model_override
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from codex_orchestrator.config import (
    BackendConfig,
    OrchestratorConfig,
    default_config,
)
from codex_orchestrator.console import ConsoleReporter
from codex_orchestrator.cli import command_bead, build_parser
from codex_orchestrator.models import AgentRunResult, Bead, HandoffSummary
from codex_orchestrator.prompts import BUILT_IN_AGENT_TYPES
from codex_orchestrator.runner import ClaudeCodeAgentRunner
from codex_orchestrator.scheduler import Scheduler
from codex_orchestrator.storage import RepositoryStorage


def _make_git_repo(root: Path) -> None:
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    (root / "README.md").write_text("seed\n")
    source_templates = REPO_ROOT / "templates" / "agents"
    target_templates = root / "templates" / "agents"
    target_templates.mkdir(parents=True, exist_ok=True)
    for t in BUILT_IN_AGENT_TYPES:
        shutil.copy2(source_templates / f"{t}.md", target_templates / f"{t}.md")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=root, check=True, capture_output=True)


class FakeRunner:
    """Minimal fake runner for scheduler tests."""

    def __init__(self, results=None, writes=None):
        self.results = results or {}
        self.writes = writes or {}
        self.last_workdir_by_bead = {}

    def run_bead(self, bead, *, workdir, context_paths, execution_env=None, dep_handoffs: list[HandoffSummary] | None = None):
        self.last_workdir_by_bead[bead.bead_id] = workdir
        for path, content in self.writes.get(bead.bead_id, {}).items():
            target = workdir / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
        return self.results[bead.bead_id]


# ---------------------------------------------------------------------------
# Runner: bead model_override takes precedence over config
# ---------------------------------------------------------------------------

class TestRunnerBeadModelOverride(unittest.TestCase):
    """run_bead uses bead.metadata['model_override'] instead of config model."""

    def _make_runner(self, model_default="claude-sonnet-4-6"):
        backend = BackendConfig(
            binary="claude",
            flags=["--dangerously-skip-permissions"],
            allowed_tools_default=["Read"],
            allowed_tools_by_agent={},
            model_default=model_default,
            model_by_agent={"developer": model_default},
        )
        config = OrchestratorConfig(backends={"claude": backend})
        return ClaudeCodeAgentRunner(config=config, backend=backend)

    def _make_bead(self, model_override=None):
        metadata = {}
        if model_override:
            metadata["model_override"] = model_override
        return Bead(
            bead_id="B9999",
            title="Test bead",
            agent_type="developer",
            description="test",
            status="in_progress",
            metadata=metadata,
        )

    @patch("codex_orchestrator.runner.build_worker_prompt", return_value="test prompt")
    def test_bead_model_override_used_over_config(self, _mock_prompt):
        """When bead has model_override, it overrides the config model."""
        runner = self._make_runner(model_default="claude-sonnet-4-6")
        bead = self._make_bead(model_override="claude-opus-4-6")
        response = {
            "structured_output": {"outcome": "completed", "summary": "done"},
            "usage": {},
        }

        with patch("codex_orchestrator.runner.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=json.dumps(response),
            )
            runner.run_bead(bead, workdir=Path("/tmp"), context_paths=[])
            cmd = mock_run.call_args[0][0]
            self.assertIn("--model", cmd)
            model_idx = cmd.index("--model")
            self.assertEqual(cmd[model_idx + 1], "claude-opus-4-6")

    @patch("codex_orchestrator.runner.build_worker_prompt", return_value="test prompt")
    def test_config_model_used_when_no_override(self, _mock_prompt):
        """When bead has no model_override, config model is used."""
        runner = self._make_runner(model_default="claude-sonnet-4-6")
        bead = self._make_bead(model_override=None)
        response = {
            "structured_output": {"outcome": "completed", "summary": "done"},
            "usage": {},
        }

        with patch("codex_orchestrator.runner.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=json.dumps(response),
            )
            runner.run_bead(bead, workdir=Path("/tmp"), context_paths=[])
            cmd = mock_run.call_args[0][0]
            self.assertIn("--model", cmd)
            model_idx = cmd.index("--model")
            self.assertEqual(cmd[model_idx + 1], "claude-sonnet-4-6")

    @patch("codex_orchestrator.runner.build_worker_prompt", return_value="test prompt")
    def test_bead_with_none_metadata_uses_config(self, _mock_prompt):
        """When bead.metadata is None, config model is used."""
        runner = self._make_runner(model_default="claude-sonnet-4-6")
        bead = Bead(
            bead_id="B9999",
            title="Test bead",
            agent_type="developer",
            description="test",
            status="in_progress",
            metadata=None,
        )
        response = {
            "structured_output": {"outcome": "completed", "summary": "done"},
            "usage": {},
        }

        with patch("codex_orchestrator.runner.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=json.dumps(response),
            )
            runner.run_bead(bead, workdir=Path("/tmp"), context_paths=[])
            cmd = mock_run.call_args[0][0]
            self.assertIn("--model", cmd)
            model_idx = cmd.index("--model")
            self.assertEqual(cmd[model_idx + 1], "claude-sonnet-4-6")


# ---------------------------------------------------------------------------
# Scheduler: model_override propagation to followup beads
# ---------------------------------------------------------------------------

class TestFollowupModelOverridePropagation(unittest.TestCase):
    """_create_followups propagates model_override to followup children."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        _make_git_repo(self.root)
        self.storage = RepositoryStorage(self.root)
        self.storage.initialize()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _make_scheduler(self):
        cfg = default_config()
        return Scheduler(self.storage, FakeRunner(), MagicMock(), config=cfg)

    def test_followup_beads_inherit_model_override(self):
        """Followup beads (test, docs, review) inherit parent's model_override."""
        bead = self.storage.create_bead(
            title="Feature", agent_type="developer", description="d",
            metadata={"model_override": "claude-opus-4-6"},
        )
        agent_result = AgentRunResult(outcome="completed", summary="done", verdict="approved")
        sched = self._make_scheduler()
        created = sched._create_followups(bead, agent_result)

        self.assertTrue(len(created) >= 3, f"Expected at least 3 followups, got {len(created)}")
        for child in created:
            self.assertEqual(
                child.metadata.get("model_override"), "claude-opus-4-6",
                f"Followup {child.bead_id} missing model_override",
            )

    def test_no_model_override_when_parent_has_none(self):
        """Followup beads do not have model_override when parent doesn't."""
        bead = self.storage.create_bead(
            title="Feature", agent_type="developer", description="d",
        )
        agent_result = AgentRunResult(outcome="completed", summary="done", verdict="approved")
        sched = self._make_scheduler()
        created = sched._create_followups(bead, agent_result)

        self.assertTrue(len(created) >= 3)
        for child in created:
            has_override = child.metadata and "model_override" in child.metadata
            self.assertFalse(
                has_override,
                f"Followup {child.bead_id} should not have model_override",
            )

    def test_discovered_sub_beads_inherit_model_override(self):
        """Dynamically discovered sub-beads inherit parent's model_override."""
        bead = self.storage.create_bead(
            title="Feature", agent_type="developer", description="d",
            metadata={"model_override": "claude-opus-4-6"},
        )
        agent_result = AgentRunResult(
            outcome="completed", summary="done", verdict="approved",
            new_beads=[{
                "title": "Sub-task",
                "agent_type": "developer",
                "description": "extra work",
                "acceptance_criteria": [],
                "dependencies": [],
                "linked_docs": [],
                "expected_files": [],
                "expected_globs": [],
            }],
        )
        sched = self._make_scheduler()
        created = sched._create_followups(bead, agent_result)

        # Find the sub-bead (not a standard followup suffix)
        sub_beads = [c for c in created if "-subtask" in c.bead_id]
        self.assertTrue(len(sub_beads) >= 1, f"Expected at least 1 sub-bead, got {sub_beads}")
        for sb in sub_beads:
            self.assertEqual(
                sb.metadata.get("model_override"), "claude-opus-4-6",
                f"Sub-bead {sb.bead_id} missing model_override",
            )

    def test_discovered_sub_beads_no_override_when_parent_has_none(self):
        """Sub-beads do not have model_override when parent doesn't."""
        bead = self.storage.create_bead(
            title="Feature", agent_type="developer", description="d",
        )
        agent_result = AgentRunResult(
            outcome="completed", summary="done", verdict="approved",
            new_beads=[{
                "title": "Sub-task",
                "agent_type": "developer",
                "description": "extra work",
                "acceptance_criteria": [],
                "dependencies": [],
                "linked_docs": [],
                "expected_files": [],
                "expected_globs": [],
            }],
        )
        sched = self._make_scheduler()
        created = sched._create_followups(bead, agent_result)

        sub_beads = [c for c in created if "-subtask" in c.bead_id]
        self.assertTrue(len(sub_beads) >= 1)
        for sb in sub_beads:
            has_override = sb.metadata and "model_override" in sb.metadata
            self.assertFalse(
                has_override,
                f"Sub-bead {sb.bead_id} should not have model_override",
            )

    def test_non_developer_beads_skip_followup_creation(self):
        """Non-developer beads never create followups (model_override irrelevant)."""
        for agent_type in ("tester", "review", "documentation"):
            bead = self.storage.create_bead(
                title="Check", agent_type=agent_type, description="d",
                metadata={"model_override": "claude-opus-4-6"},
            )
            agent_result = AgentRunResult(outcome="completed", summary="done", verdict="approved")
            sched = self._make_scheduler()
            created = sched._create_followups(bead, agent_result)
            self.assertEqual(created, [], f"{agent_type} should not create followups")


# ---------------------------------------------------------------------------
# CLI: bead update --model sets metadata.model_override
# ---------------------------------------------------------------------------

class TestCliModelOverride(unittest.TestCase):
    """CLI `bead update --model` sets metadata.model_override."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        _make_git_repo(self.root)
        self.storage = RepositoryStorage(self.root)
        self.storage.initialize()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_update_sets_model_override(self):
        """bead update --model sets metadata.model_override."""
        bead = self.storage.create_bead(
            title="Feature", agent_type="developer", description="d",
        )
        parser = build_parser()
        args = parser.parse_args(["bead", "update", bead.bead_id, "--model", "claude-opus-4-6"])
        console = ConsoleReporter(stream=MagicMock())
        result = command_bead(args, self.storage, console)
        self.assertEqual(result, 0)

        updated = self.storage.load_bead(bead.bead_id)
        self.assertEqual(updated.metadata["model_override"], "claude-opus-4-6")

    def test_update_overwrites_existing_model_override(self):
        """bead update --model overwrites an existing model_override."""
        bead = self.storage.create_bead(
            title="Feature", agent_type="developer", description="d",
            metadata={"model_override": "claude-sonnet-4-6"},
        )
        parser = build_parser()
        args = parser.parse_args(["bead", "update", bead.bead_id, "--model", "claude-opus-4-6"])
        console = ConsoleReporter(stream=MagicMock())
        result = command_bead(args, self.storage, console)
        self.assertEqual(result, 0)

        updated = self.storage.load_bead(bead.bead_id)
        self.assertEqual(updated.metadata["model_override"], "claude-opus-4-6")

    def test_update_model_preserves_other_metadata(self):
        """Setting model_override does not clobber other metadata fields."""
        bead = self.storage.create_bead(
            title="Feature", agent_type="developer", description="d",
            metadata={"custom_key": "value"},
        )
        parser = build_parser()
        args = parser.parse_args(["bead", "update", bead.bead_id, "--model", "claude-opus-4-6"])
        console = ConsoleReporter(stream=MagicMock())
        command_bead(args, self.storage, console)

        updated = self.storage.load_bead(bead.bead_id)
        self.assertEqual(updated.metadata["model_override"], "claude-opus-4-6")
        self.assertEqual(updated.metadata["custom_key"], "value")

    def test_update_model_on_bead_with_none_metadata(self):
        """Setting model_override on a bead with None metadata initializes dict."""
        bead = self.storage.create_bead(
            title="Feature", agent_type="developer", description="d",
            metadata=None,
        )
        parser = build_parser()
        args = parser.parse_args(["bead", "update", bead.bead_id, "--model", "claude-opus-4-6"])
        console = ConsoleReporter(stream=MagicMock())
        result = command_bead(args, self.storage, console)
        self.assertEqual(result, 0)

        updated = self.storage.load_bead(bead.bead_id)
        self.assertIsNotNone(updated.metadata)
        self.assertEqual(updated.metadata["model_override"], "claude-opus-4-6")


# ---------------------------------------------------------------------------
# End-to-end: scheduler run propagates model_override through full cycle
# ---------------------------------------------------------------------------

class TestEndToEndModelOverridePropagation(unittest.TestCase):
    """Full scheduler cycle propagates model_override from developer to followups."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        _make_git_repo(self.root)
        self.storage = RepositoryStorage(self.root)
        self.storage.initialize()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_full_cycle_propagates_model_override(self):
        """A developer bead with model_override produces followups that inherit it."""
        from codex_orchestrator.gitutils import WorktreeManager

        bead = self.storage.create_bead(
            title="Implement feature",
            agent_type="developer",
            description="do work",
            metadata={"model_override": "claude-opus-4-6"},
        )
        runner = FakeRunner(
            results={
                bead.bead_id: AgentRunResult(
                    outcome="completed",
                    summary="done",
                    expected_files=["src/app.py"],
                    touched_files=["src/app.py"],
                    changed_files=["src/app.py"],
                )
            },
            writes={bead.bead_id: {"src/app.py": "print('hello')\n"}},
        )
        scheduler = Scheduler(
            self.storage, runner,
            WorktreeManager(self.root, self.storage.worktrees_dir),
        )
        scheduler.run_once()

        # Verify all followup children have the model_override
        children = [b for b in self.storage.list_beads() if b.parent_id == bead.bead_id]
        self.assertTrue(len(children) >= 3, f"Expected followup children, got {len(children)}")
        for child in children:
            self.assertEqual(
                child.metadata.get("model_override"), "claude-opus-4-6",
                f"Child {child.bead_id} should inherit model_override",
            )


if __name__ == "__main__":
    unittest.main()
