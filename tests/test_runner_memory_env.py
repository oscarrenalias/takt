"""Tests for memory env injection in CodexAgentRunner and ClaudeCodeAgentRunner.

Covers:
- _find_project_root: nested inside .takt, and no .takt ancestor
- _resolve_takt_cmd: self-hosting pyproject.toml, PATH binary, fallback to uv run
- CodexAgentRunner.run_bead: env contains TAKT_CMD, AGENT_MEMORY_DB, AGENT_TAKT_FEATURE_ROOT_ID
- ClaudeCodeAgentRunner.run_bead: same env vars
- Standalone bead (feature_root_id=None): AGENT_TAKT_FEATURE_ROOT_ID='global'
- caller-supplied execution_env overrides memory_env defaults
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_takt.runner import (
    CodexAgentRunner,
    ClaudeCodeAgentRunner,
    _find_project_root,
    _resolve_takt_cmd,
)
from agent_takt.models import Bead
from agent_takt.config import default_config


# ---------------------------------------------------------------------------
# _find_project_root
# ---------------------------------------------------------------------------


class TestFindProjectRoot(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_returns_dir_above_takt(self):
        """A path nested inside .takt should return the directory one level above .takt."""
        takt_dir = self.root / ".takt"
        takt_dir.mkdir()
        nested = takt_dir / "worktrees" / "B-abc" / "src"
        nested.mkdir(parents=True)
        result = _find_project_root(nested)
        self.assertEqual(self.root.resolve(), result)

    def test_finds_takt_in_ancestor(self):
        """Walking up several levels should find the .takt directory."""
        takt_dir = self.root / ".takt"
        takt_dir.mkdir()
        deep = self.root / "a" / "b" / "c"
        deep.mkdir(parents=True)
        result = _find_project_root(deep)
        self.assertEqual(self.root.resolve(), result)

    def test_returns_start_when_no_takt(self):
        """When no .takt is found, return the (resolved) start path."""
        no_takt = self.root / "no_takt_here"
        no_takt.mkdir()
        result = _find_project_root(no_takt)
        self.assertEqual(no_takt.resolve(), result)

    def test_works_with_takt_at_root(self):
        """Direct parent of .takt is returned."""
        takt_dir = self.root / ".takt"
        takt_dir.mkdir()
        result = _find_project_root(self.root)
        self.assertEqual(self.root.resolve(), result)


# ---------------------------------------------------------------------------
# _resolve_takt_cmd
# ---------------------------------------------------------------------------


class TestResolveTaktCmd(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_self_hosting_project_returns_uv_run_form(self):
        """pyproject.toml containing 'agent-takt' → uv run --directory ... takt"""
        pyproject = self.root / "pyproject.toml"
        pyproject.write_text('[project]\nname = "agent-takt"\n')
        cmd = _resolve_takt_cmd(self.root)
        self.assertIn("uv run", cmd)
        self.assertIn("takt", cmd)
        self.assertIn(str(self.root), cmd)

    def test_non_self_hosting_with_takt_on_path(self):
        """Without 'agent-takt' in pyproject.toml, prefer the PATH binary."""
        pyproject = self.root / "pyproject.toml"
        pyproject.write_text('[project]\nname = "other-project"\n')
        fake_takt = self.root / "takt"
        fake_takt.write_text("#!/bin/sh\necho takt")
        with patch("agent_takt.runner.shutil.which", return_value=str(fake_takt)):
            cmd = _resolve_takt_cmd(self.root)
        self.assertEqual(str(fake_takt), cmd)

    def test_no_pyproject_no_takt_binary_falls_back_to_uv_run(self):
        """No pyproject.toml and takt not on PATH → uv run form."""
        with patch("agent_takt.runner.shutil.which", return_value=None):
            cmd = _resolve_takt_cmd(self.root)
        self.assertIn("uv run", cmd)
        self.assertIn("takt", cmd)

    def test_no_pyproject_with_takt_on_path(self):
        """No pyproject.toml but takt on PATH → return PATH binary."""
        with patch("agent_takt.runner.shutil.which", return_value="/usr/local/bin/takt"):
            cmd = _resolve_takt_cmd(self.root)
        self.assertEqual("/usr/local/bin/takt", cmd)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bead(feature_root_id: str | None = "B-feature123") -> Bead:
    return Bead(
        bead_id="B-testbead1",
        title="Test Bead",
        agent_type="developer",
        description="Test",
        feature_root_id=feature_root_id,
    )


def _fake_exec_json_payload():
    return {
        "outcome": "completed",
        "summary": "done",
        "completed": "",
        "remaining": "",
        "risks": "",
        "verdict": "",
        "findings_count": 0,
        "requires_followup": False,
        "expected_files": [],
        "expected_globs": [],
        "touched_files": [],
        "changed_files": [],
        "updated_docs": [],
        "next_action": "",
        "next_agent": "",
        "block_reason": "",
        "conflict_risks": "",
        "design_decisions": "",
        "test_coverage_notes": "",
        "known_limitations": "",
        "new_beads": [],
    }


# ---------------------------------------------------------------------------
# CodexAgentRunner env injection
# ---------------------------------------------------------------------------


def _patch_build_worker_prompt():
    """Patch build_worker_prompt so run_bead doesn't need templates on disk."""
    return patch("agent_takt.runner.build_worker_prompt", return_value="dummy prompt")


class TestCodexAgentRunnerEnvInjection(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        takt_dir = self.root / ".takt"
        takt_dir.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def _make_runner(self) -> CodexAgentRunner:
        return CodexAgentRunner()

    def test_env_contains_takt_cmd(self):
        runner = self._make_runner()
        bead = _make_bead()
        captured_env: dict = {}

        def fake_exec_json(prompt, *, schema, workdir, execution_env=None):
            if execution_env:
                captured_env.update(execution_env)
            return _fake_exec_json_payload()

        with _patch_build_worker_prompt(), patch.object(runner, "_exec_json", side_effect=fake_exec_json):
            runner.run_bead(
                bead,
                workdir=self.root / ".takt" / "worktrees" / "B-feature123",
                context_paths=[],
            )
        self.assertIn("TAKT_CMD", captured_env)

    def test_env_contains_agent_memory_db(self):
        runner = self._make_runner()
        bead = _make_bead()
        captured_env: dict = {}

        def fake_exec_json(prompt, *, schema, workdir, execution_env=None):
            if execution_env:
                captured_env.update(execution_env)
            return _fake_exec_json_payload()

        with _patch_build_worker_prompt(), patch.object(runner, "_exec_json", side_effect=fake_exec_json):
            runner.run_bead(bead, workdir=self.root / ".takt" / "worktrees" / "B-feature123", context_paths=[])
        self.assertIn("AGENT_MEMORY_DB", captured_env)
        self.assertIn("memory.db", captured_env["AGENT_MEMORY_DB"])

    def test_env_contains_feature_root_id(self):
        runner = self._make_runner()
        bead = _make_bead(feature_root_id="B-feature123")
        captured_env: dict = {}

        def fake_exec_json(prompt, *, schema, workdir, execution_env=None):
            if execution_env:
                captured_env.update(execution_env)
            return _fake_exec_json_payload()

        with _patch_build_worker_prompt(), patch.object(runner, "_exec_json", side_effect=fake_exec_json):
            runner.run_bead(bead, workdir=self.root / ".takt" / "worktrees" / "B-feature123", context_paths=[])
        self.assertEqual("B-feature123", captured_env.get("AGENT_TAKT_FEATURE_ROOT_ID"))

    def test_standalone_bead_feature_root_id_is_global(self):
        """A bead with feature_root_id=None should inject 'global'."""
        runner = self._make_runner()
        bead = _make_bead(feature_root_id=None)
        captured_env: dict = {}

        def fake_exec_json(prompt, *, schema, workdir, execution_env=None):
            if execution_env:
                captured_env.update(execution_env)
            return _fake_exec_json_payload()

        with _patch_build_worker_prompt(), patch.object(runner, "_exec_json", side_effect=fake_exec_json):
            runner.run_bead(bead, workdir=self.root / ".takt" / "worktrees" / "global", context_paths=[])
        self.assertEqual("global", captured_env.get("AGENT_TAKT_FEATURE_ROOT_ID"))

    def test_caller_execution_env_overrides_memory_env(self):
        """caller-supplied execution_env should override the default memory vars."""
        runner = self._make_runner()
        bead = _make_bead()
        captured_env: dict = {}

        def fake_exec_json(prompt, *, schema, workdir, execution_env=None):
            if execution_env:
                captured_env.update(execution_env)
            return _fake_exec_json_payload()

        custom_env = {"TAKT_CMD": "my_custom_takt", "EXTRA_VAR": "extra_value"}
        with _patch_build_worker_prompt(), patch.object(runner, "_exec_json", side_effect=fake_exec_json):
            runner.run_bead(
                bead,
                workdir=self.root / ".takt" / "worktrees" / "B-feature123",
                context_paths=[],
                execution_env=custom_env,
            )
        self.assertEqual("my_custom_takt", captured_env.get("TAKT_CMD"))
        self.assertEqual("extra_value", captured_env.get("EXTRA_VAR"))


# ---------------------------------------------------------------------------
# ClaudeCodeAgentRunner env injection
# ---------------------------------------------------------------------------


class TestClaudeCodeAgentRunnerEnvInjection(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        takt_dir = self.root / ".takt"
        takt_dir.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def _make_runner(self) -> ClaudeCodeAgentRunner:
        return ClaudeCodeAgentRunner()

    def test_env_contains_takt_cmd(self):
        runner = self._make_runner()
        bead = _make_bead()
        captured_env: dict = {}

        def fake_exec_json_with_response(prompt, *, schema, workdir, execution_env=None, agent_type=None, **kwargs):
            if execution_env:
                captured_env.update(execution_env)
            return _fake_exec_json_payload(), {"usage": {}, "total_cost_usd": 0.0}

        with _patch_build_worker_prompt(), patch.object(runner, "_exec_json_with_response", side_effect=fake_exec_json_with_response):
            runner.run_bead(
                bead,
                workdir=self.root / ".takt" / "worktrees" / "B-feature123",
                context_paths=[],
            )
        self.assertIn("TAKT_CMD", captured_env)

    def test_env_contains_agent_memory_db(self):
        runner = self._make_runner()
        bead = _make_bead()
        captured_env: dict = {}

        def fake_exec_json_with_response(prompt, *, schema, workdir, execution_env=None, agent_type=None, **kwargs):
            if execution_env:
                captured_env.update(execution_env)
            return _fake_exec_json_payload(), {"usage": {}, "total_cost_usd": 0.0}

        with _patch_build_worker_prompt(), patch.object(runner, "_exec_json_with_response", side_effect=fake_exec_json_with_response):
            runner.run_bead(bead, workdir=self.root / ".takt" / "worktrees" / "B-feature123", context_paths=[])
        self.assertIn("AGENT_MEMORY_DB", captured_env)
        self.assertIn("memory.db", captured_env["AGENT_MEMORY_DB"])

    def test_env_contains_feature_root_id(self):
        runner = self._make_runner()
        bead = _make_bead(feature_root_id="B-feature123")
        captured_env: dict = {}

        def fake_exec_json_with_response(prompt, *, schema, workdir, execution_env=None, agent_type=None, **kwargs):
            if execution_env:
                captured_env.update(execution_env)
            return _fake_exec_json_payload(), {"usage": {}, "total_cost_usd": 0.0}

        with _patch_build_worker_prompt(), patch.object(runner, "_exec_json_with_response", side_effect=fake_exec_json_with_response):
            runner.run_bead(bead, workdir=self.root / ".takt" / "worktrees" / "B-feature123", context_paths=[])
        self.assertEqual("B-feature123", captured_env.get("AGENT_TAKT_FEATURE_ROOT_ID"))

    def test_standalone_bead_feature_root_id_is_global(self):
        runner = self._make_runner()
        bead = _make_bead(feature_root_id=None)
        captured_env: dict = {}

        def fake_exec_json_with_response(prompt, *, schema, workdir, execution_env=None, agent_type=None, **kwargs):
            if execution_env:
                captured_env.update(execution_env)
            return _fake_exec_json_payload(), {"usage": {}, "total_cost_usd": 0.0}

        with _patch_build_worker_prompt(), patch.object(runner, "_exec_json_with_response", side_effect=fake_exec_json_with_response):
            runner.run_bead(bead, workdir=self.root / ".takt" / "worktrees" / "global", context_paths=[])
        self.assertEqual("global", captured_env.get("AGENT_TAKT_FEATURE_ROOT_ID"))

    def test_caller_execution_env_overrides_memory_env(self):
        runner = self._make_runner()
        bead = _make_bead()
        captured_env: dict = {}

        def fake_exec_json_with_response(prompt, *, schema, workdir, execution_env=None, agent_type=None, **kwargs):
            if execution_env:
                captured_env.update(execution_env)
            return _fake_exec_json_payload(), {"usage": {}, "total_cost_usd": 0.0}

        custom_env = {"TAKT_CMD": "custom_override", "MY_VAR": "my_value"}
        with _patch_build_worker_prompt(), patch.object(runner, "_exec_json_with_response", side_effect=fake_exec_json_with_response):
            runner.run_bead(
                bead,
                workdir=self.root / ".takt" / "worktrees" / "B-feature123",
                context_paths=[],
                execution_env=custom_env,
            )
        self.assertEqual("custom_override", captured_env.get("TAKT_CMD"))
        self.assertEqual("my_value", captured_env.get("MY_VAR"))


if __name__ == "__main__":
    unittest.main()
