"""Shared test harness for scheduler-oriented tests.

Exports:
    FakeRunner       — minimal AgentRunner stub for unit tests
    OrchestratorTests — base TestCase with a temp git repo + RepositoryStorage
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_takt.models import AgentRunResult, Bead, HandoffSummary, PlanProposal
from agent_takt.prompts import BUILT_IN_AGENT_TYPES
from agent_takt.storage import RepositoryStorage


class FakeRunner:
    def __init__(
        self,
        results: dict[str, AgentRunResult] | None = None,
        proposal: PlanProposal | None = None,
        writes: dict[str, dict[str, str]] | None = None,
    ) -> None:
        self.results = results or {}
        self.proposal_value = proposal
        self.writes = writes or {}
        self.last_workdir_by_bead: dict[str, Path] = {}

    def run_bead(
        self,
        bead: Bead,
        *,
        workdir: Path,
        context_paths: list[Path],
        execution_env: dict[str, str] | None = None,
        dep_handoffs: list[HandoffSummary] | None = None,
    ) -> AgentRunResult:
        self.last_workdir_by_bead[bead.bead_id] = workdir
        for relative_path, content in self.writes.get(bead.bead_id, {}).items():
            target = workdir / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        return self.results[bead.bead_id]

    def propose_plan(self, spec_text: str) -> PlanProposal:
        if self.proposal_value is None:
            raise AssertionError("No plan proposal configured")
        return self.proposal_value


class OrchestratorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        subprocess.run(["git", "init"], cwd=self.root, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=self.root, check=True)
        (self.root / "README.md").write_text("seed\n", encoding="utf-8")
        source_templates = REPO_ROOT / "templates" / "agents"
        target_templates = self.root / "templates" / "agents"
        target_templates.mkdir(parents=True, exist_ok=True)
        for template in BUILT_IN_AGENT_TYPES:
            shutil.copy2(source_templates / f"{template}.md", target_templates / f"{template}.md")
        subprocess.run(["git", "add", "README.md"], cwd=self.root, check=True)
        subprocess.run(["git", "add", "templates/agents"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=self.root, check=True, capture_output=True)
        self.storage = RepositoryStorage(self.root)
        self.storage.initialize()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()
