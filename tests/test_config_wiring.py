"""Tests for Phase 2: Config wiring into runners and CLI (B0104).

Validates that runner.py and cli.py read binary paths, flags, and
allowed-tools lists from OrchestratorConfig / BackendConfig instead of
hardcoded values.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_takt.config import (
    BackendConfig,
    OrchestratorConfig,
    default_config,
    load_config,
)
from agent_takt.models import AgentRunResult
from agent_takt.runner import (
    AgentRunner,
    ClaudeCodeAgentRunner,
    CodexAgentRunner,
)
from agent_takt.cli import make_services
from agent_takt.storage import RepositoryStorage


# ---------------------------------------------------------------------------
# Runner constructor tests
# ---------------------------------------------------------------------------

class TestCodexRunnerConstructor(unittest.TestCase):
    """CodexAgentRunner accepts config/backend and falls back to defaults."""

    def test_default_construction(self):
        runner = CodexAgentRunner()
        cfg = default_config()
        self.assertEqual(runner.config.default_runner, cfg.default_runner)
        self.assertEqual(runner.backend.binary, "codex")
        self.assertEqual(runner.backend.flags, cfg.backend("codex").flags)

    def test_explicit_config_and_backend(self):
        backend = BackendConfig(
            binary="/custom/codex",
            flags=["--custom-flag"],
        )
        config = OrchestratorConfig(backends={"codex": backend})
        runner = CodexAgentRunner(config=config, backend=backend)
        self.assertEqual(runner.backend.binary, "/custom/codex")
        self.assertEqual(runner.backend.flags, ["--custom-flag"])
        self.assertIs(runner.config, config)

    def test_config_only_resolves_backend(self):
        backend = BackendConfig(binary="/auto/codex", flags=["--auto"])
        config = OrchestratorConfig(backends={"codex": backend})
        runner = CodexAgentRunner(config=config)
        self.assertEqual(runner.backend.binary, "/auto/codex")

    def test_backend_name_property(self):
        runner = CodexAgentRunner()
        self.assertEqual(runner.backend_name, "codex")


class TestClaudeRunnerConstructor(unittest.TestCase):
    """ClaudeCodeAgentRunner accepts config/backend and falls back to defaults."""

    def test_default_construction(self):
        runner = ClaudeCodeAgentRunner()
        cfg = default_config()
        self.assertEqual(runner.config.default_runner, cfg.default_runner)
        self.assertEqual(runner.backend.binary, "claude")
        self.assertEqual(runner.backend.flags, cfg.backend("claude").flags)

    def test_explicit_config_and_backend(self):
        backend = BackendConfig(
            binary="/custom/claude",
            flags=["--custom-perm"],
            allowed_tools_default=["Read", "Write"],
            allowed_tools_by_agent={"developer": ["Agent"]},
        )
        config = OrchestratorConfig(backends={"claude": backend})
        runner = ClaudeCodeAgentRunner(config=config, backend=backend)
        self.assertEqual(runner.backend.binary, "/custom/claude")
        self.assertEqual(runner.backend.flags, ["--custom-perm"])
        self.assertIs(runner.config, config)

    def test_config_only_resolves_backend(self):
        backend = BackendConfig(binary="/auto/claude", flags=["--auto"])
        config = OrchestratorConfig(backends={"claude": backend})
        runner = ClaudeCodeAgentRunner(config=config)
        self.assertEqual(runner.backend.binary, "/auto/claude")

    def test_backend_name_property(self):
        runner = ClaudeCodeAgentRunner()
        self.assertEqual(runner.backend_name, "claude")


# ---------------------------------------------------------------------------
# Codex command building tests
# ---------------------------------------------------------------------------

class TestCodexCommandBuilding(unittest.TestCase):
    """Verify CodexAgentRunner._exec_json builds commands from config."""

    def test_command_uses_config_binary_and_flags(self):
        backend = BackendConfig(
            binary="/opt/codex",
            flags=["--alpha", "--beta"],
        )
        config = OrchestratorConfig(backends={"codex": backend})
        runner = CodexAgentRunner(config=config, backend=backend)

        with patch("agent_takt.runner.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            # Patch output file reading
            with patch("pathlib.Path.read_text", return_value='{"outcome": "completed"}'):
                try:
                    runner._exec_json("test prompt", schema={}, workdir=Path("/tmp"))
                except Exception:
                    pass  # We only care about the command

            if mock_run.called:
                cmd = mock_run.call_args[0][0]
                self.assertEqual(cmd[0], "/opt/codex")
                self.assertEqual(cmd[1], "exec")
                # Config flags should appear after "exec"
                self.assertIn("--alpha", cmd)
                self.assertIn("--beta", cmd)
                # Structural flags must still be present
                self.assertIn("--output-schema", cmd)
                self.assertIn("--output-last-message", cmd)
                self.assertIn("-C", cmd)
                self.assertEqual(cmd[-1], "-")

    def test_structural_flags_not_from_config(self):
        """Structural flags (--output-schema, -C, etc.) are not in config.flags."""
        cfg = default_config()
        codex_flags = cfg.backend("codex").flags
        for structural in ["--output-schema", "--output-last-message", "-C", "-"]:
            self.assertNotIn(structural, codex_flags)


# ---------------------------------------------------------------------------
# Claude command building tests
# ---------------------------------------------------------------------------

class TestClaudeCommandBuilding(unittest.TestCase):
    """Verify ClaudeCodeAgentRunner._exec_json builds commands from config."""

    def test_command_uses_config_binary_and_flags(self):
        backend = BackendConfig(
            binary="/opt/claude",
            flags=["--custom-flag"],
            allowed_tools_default=["Read", "Write"],
            allowed_tools_by_agent={},
        )
        config = OrchestratorConfig(backends={"claude": backend})
        runner = ClaudeCodeAgentRunner(config=config, backend=backend)

        with patch("agent_takt.runner.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout='{"structured_output": {"outcome": "completed"}}',
            )
            try:
                runner._exec_json("test prompt", schema={}, workdir=Path("/tmp"))
            except Exception:
                pass

            if mock_run.called:
                cmd = mock_run.call_args[0][0]
                self.assertEqual(cmd[0], "/opt/claude")
                self.assertIn("-p", cmd)
                self.assertIn("--custom-flag", cmd)
                # Structural flags present
                self.assertIn("--output-format", cmd)
                self.assertIn("--json-schema", cmd)
                self.assertIn("--allowedTools", cmd)

    def test_allowed_tools_resolved_per_agent_type(self):
        backend = BackendConfig(
            binary="claude",
            flags=[],
            allowed_tools_default=["Read", "Write"],
            allowed_tools_by_agent={
                "developer": ["Agent", "NotebookEdit"],
                "tester": ["Agent"],
            },
        )
        config = OrchestratorConfig(backends={"claude": backend})
        runner = ClaudeCodeAgentRunner(config=config, backend=backend)

        with patch("agent_takt.runner.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout='{"structured_output": {"outcome": "completed"}}',
            )

            # Call with developer agent_type
            runner._exec_json("prompt", schema={}, workdir=Path("/tmp"), agent_type="developer")
            cmd_dev = mock_run.call_args[0][0]
            tools_idx = cmd_dev.index("--allowedTools")
            tools_dev = cmd_dev[tools_idx + 1].split(",")
            self.assertIn("Agent", tools_dev)
            self.assertIn("NotebookEdit", tools_dev)
            self.assertIn("Read", tools_dev)

            # Call with tester agent_type
            runner._exec_json("prompt", schema={}, workdir=Path("/tmp"), agent_type="tester")
            cmd_test = mock_run.call_args[0][0]
            tools_idx = cmd_test.index("--allowedTools")
            tools_test = cmd_test[tools_idx + 1].split(",")
            self.assertIn("Agent", tools_test)
            self.assertNotIn("NotebookEdit", tools_test)

    def test_default_agent_type_is_developer(self):
        """When agent_type is None, defaults to 'developer'."""
        backend = BackendConfig(
            binary="claude",
            flags=[],
            allowed_tools_default=["Read"],
            allowed_tools_by_agent={"developer": ["Agent"]},
        )
        config = OrchestratorConfig(backends={"claude": backend})
        runner = ClaudeCodeAgentRunner(config=config, backend=backend)

        with patch("agent_takt.runner.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout='{"structured_output": {"outcome": "completed"}}',
            )
            runner._exec_json("prompt", schema={}, workdir=Path("/tmp"), agent_type=None)
            cmd = mock_run.call_args[0][0]
            tools_idx = cmd.index("--allowedTools")
            tools = cmd[tools_idx + 1].split(",")
            self.assertIn("Agent", tools)

    def test_structural_flags_not_from_config(self):
        """Structural flags (-p, --output-format, --json-schema) are not in config.flags."""
        cfg = default_config()
        claude_flags = cfg.backend("claude").flags
        for structural in ["-p", "--output-format", "--json-schema", "--allowedTools"]:
            self.assertNotIn(structural, claude_flags)


# ---------------------------------------------------------------------------
# Claude agent_type threading tests
# ---------------------------------------------------------------------------

class TestClaudeAgentTypeThreading(unittest.TestCase):
    """Verify agent_type is threaded through run_bead, propose_plan, and retry."""

    def _make_runner(self):
        backend = BackendConfig(
            binary="claude",
            flags=[],
            allowed_tools_default=["Read"],
            allowed_tools_by_agent={
                "developer": ["Agent"],
                "planner": ["WebSearch"],
                "tester": ["Bash"],
            },
        )
        config = OrchestratorConfig(backends={"claude": backend})
        return ClaudeCodeAgentRunner(config=config, backend=backend)

    def test_run_bead_passes_agent_type(self):
        runner = self._make_runner()
        bead = MagicMock()
        bead.agent_type = "tester"

        result_payload = {
            "outcome": "completed", "summary": "", "completed": "",
            "remaining": "", "risks": "", "verdict": "approved",
            "findings_count": 0, "requires_followup": False,
            "expected_files": [], "expected_globs": [],
            "touched_files": [], "changed_files": [],
            "updated_docs": [], "next_action": "", "next_agent": "",
            "block_reason": "", "conflict_risks": "", "new_beads": [],
        }
        mock_response = {"structured_output": result_payload}
        with patch.object(runner, "_exec_json_with_response", return_value=(result_payload, mock_response)) as mock_exec, \
             patch("agent_takt.runner.build_worker_prompt", return_value="mocked prompt"):
            runner.run_bead(bead, workdir=Path("/tmp"), context_paths=[])
            _, kwargs = mock_exec.call_args
            self.assertEqual(kwargs["agent_type"], "tester")

    def test_propose_plan_passes_planner_type(self):
        runner = self._make_runner()

        with patch.object(runner, "_exec_json", return_value={
            "epic_title": "t", "epic_description": "d",
            "linked_docs": [], "feature": {
                "title": "f", "agent_type": "developer",
                "description": "d", "acceptance_criteria": [],
                "dependencies": [], "linked_docs": [],
                "expected_files": [], "expected_globs": [],
                "children": [],
            },
        }) as mock_exec:
            runner.propose_plan("spec text")
            _, kwargs = mock_exec.call_args
            self.assertEqual(kwargs["agent_type"], "planner")

    def test_retry_structured_output_uses_no_tools(self):
        runner = self._make_runner()

        with patch("agent_takt.runner.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout='{"structured_output": {"outcome": "completed"}}',
            )
            runner._retry_structured_output(
                "some text", schema={}, workdir=Path("/tmp"),
                agent_type="tester",
            )
            cmd = mock_run.call_args[0][0]
            tools_idx = cmd.index("--allowedTools")
            # Retry is a pure reformat — no tools should be enabled
            self.assertEqual(cmd[tools_idx + 1], "")


# ---------------------------------------------------------------------------
# CLI make_services tests
# ---------------------------------------------------------------------------

class TestMakeServices(unittest.TestCase):
    """Test cli.make_services config loading and backend resolution."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        subprocess.run(["git", "init"], cwd=self.root, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"],
                       cwd=self.root, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test User"],
                       cwd=self.root, check=True, capture_output=True)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_default_backend_from_config(self):
        """No --runner arg and no env var → uses config.default_runner."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AGENT_TAKT_RUNNER", None)
            os.environ.pop("ORCHESTRATOR_RUNNER", None)
            storage, scheduler, planner = make_services(self.root)
            # Default config has default_runner="codex"
            self.assertIsInstance(scheduler.runner, CodexAgentRunner)

    def test_runner_arg_takes_precedence(self):
        """--runner claude overrides config.default_runner and env."""
        with patch.dict(os.environ, {"AGENT_TAKT_RUNNER": "codex"}, clear=False):
            storage, scheduler, planner = make_services(self.root, runner_backend="claude")
            self.assertIsInstance(scheduler.runner, ClaudeCodeAgentRunner)

    def test_env_var_overrides_config_default(self):
        """$AGENT_TAKT_RUNNER overrides config.default_runner."""
        with patch.dict(os.environ, {"AGENT_TAKT_RUNNER": "claude"}, clear=False):
            storage, scheduler, planner = make_services(self.root)
            self.assertIsInstance(scheduler.runner, ClaudeCodeAgentRunner)

    def test_legacy_orchestrator_runner_env_var(self):
        """$ORCHESTRATOR_RUNNER is honoured as a legacy fallback when AGENT_TAKT_RUNNER is absent."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AGENT_TAKT_RUNNER", None)
            os.environ["ORCHESTRATOR_RUNNER"] = "claude"
            try:
                storage, scheduler, planner = make_services(self.root)
                self.assertIsInstance(scheduler.runner, ClaudeCodeAgentRunner)
            finally:
                os.environ.pop("ORCHESTRATOR_RUNNER", None)

    def test_unknown_backend_exits(self):
        """Unknown backend name produces SystemExit with valid options."""
        with self.assertRaises(SystemExit) as ctx:
            make_services(self.root, runner_backend="nonexistent")
        self.assertIn("nonexistent", str(ctx.exception))
        self.assertIn("codex", str(ctx.exception))
        self.assertIn("claude", str(ctx.exception))

    def test_runner_gets_config_and_backend(self):
        """Runner receives config and backend objects."""
        storage, scheduler, planner = make_services(self.root, runner_backend="codex")
        runner = scheduler.runner
        self.assertIsInstance(runner.config, OrchestratorConfig)
        self.assertIsInstance(runner.backend, BackendConfig)
        self.assertEqual(runner.backend.binary, "codex")

    def test_custom_config_yaml_wired_through(self):
        """A custom config.yaml is loaded and wired into the runner."""
        orch_dir = self.root / ".takt"
        orch_dir.mkdir(parents=True, exist_ok=True)
        (orch_dir / "config.yaml").write_text(textwrap.dedent("""\
            common:
              default_runner: claude
            codex:
              binary: /custom/codex
              skills_dir: .agents
              flags:
                - "--full-auto"
            claude:
              binary: /custom/claude
              skills_dir: .claude
              flags:
                - "--dangerously-skip-permissions"
              allowed_tools_default:
                - Read
              allowed_tools_by_agent:
                developer:
                  - Agent
        """))
        storage, scheduler, planner = make_services(self.root, runner_backend="codex")
        runner = scheduler.runner
        self.assertEqual(runner.backend.binary, "/custom/codex")
        self.assertEqual(runner.backend.flags, ["--full-auto"])

    def test_missing_config_yaml_uses_defaults(self):
        """When no config.yaml exists, make_services still works with defaults."""
        storage, scheduler, planner = make_services(self.root, runner_backend="codex")
        runner = scheduler.runner
        cfg = default_config()
        self.assertEqual(runner.backend.binary, cfg.backend("codex").binary)
        self.assertEqual(runner.backend.flags, cfg.backend("codex").flags)


# ---------------------------------------------------------------------------
# No hardcoded values tests
# ---------------------------------------------------------------------------

class TestNoHardcodedValues(unittest.TestCase):
    """Verify hardcoded binary paths, flags, and tool lists are removed from
    runner.py and cli.py source code."""

    def _read_source(self, filename: str) -> str:
        src_dir = REPO_ROOT / "src" / "agent_takt"
        return (src_dir / filename).read_text(encoding="utf-8")

    def test_runner_no_hardcoded_codex_binary(self):
        """runner.py should not hardcode 'codex' as a binary in command construction."""
        source = self._read_source("runner.py")
        # The string "codex" can appear in backend_name property and default_config() call,
        # but should not appear in cmd = [...] construction
        lines = source.split("\n")
        for line in lines:
            stripped = line.strip()
            # Skip comments, property returns, and default_config fallback
            if stripped.startswith("#") or stripped.startswith("return"):
                continue
            if "config.backend(" in stripped:
                continue
            if "default_config" in stripped:
                continue
            # In cmd construction, "codex" should not be a string literal
            if "cmd" in stripped and '= [' in stripped:
                self.assertNotIn('"codex"', stripped,
                                 "Found hardcoded 'codex' binary in command construction")

    def test_runner_no_hardcoded_claude_binary(self):
        source = self._read_source("runner.py")
        lines = source.split("\n")
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith("return"):
                continue
            if "config.backend(" in stripped:
                continue
            if "default_config" in stripped:
                continue
            if "cmd" in stripped and '= [' in stripped:
                self.assertNotIn('"claude"', stripped,
                                 "Found hardcoded 'claude' binary in command construction")

    def test_runner_no_hardcoded_full_auto_flag(self):
        """--full-auto should come from config, not be hardcoded."""
        source = self._read_source("runner.py")
        # Exclude comments
        code_lines = [l for l in source.split("\n") if not l.strip().startswith("#")]
        code = "\n".join(code_lines)
        self.assertNotIn('"--full-auto"', code)
        self.assertNotIn("'--full-auto'", code)

    def test_runner_no_hardcoded_skip_git_check(self):
        source = self._read_source("runner.py")
        code_lines = [l for l in source.split("\n") if not l.strip().startswith("#")]
        code = "\n".join(code_lines)
        self.assertNotIn('"--skip-git-repo-check"', code)
        self.assertNotIn("'--skip-git-repo-check'", code)

    def test_runner_no_hardcoded_dangerously_skip(self):
        source = self._read_source("runner.py")
        code_lines = [l for l in source.split("\n") if not l.strip().startswith("#")]
        code = "\n".join(code_lines)
        self.assertNotIn('"--dangerously-skip-permissions"', code)
        self.assertNotIn("'--dangerously-skip-permissions'", code)

    def test_runner_no_hardcoded_color_never(self):
        source = self._read_source("runner.py")
        code_lines = [l for l in source.split("\n") if not l.strip().startswith("#")]
        code = "\n".join(code_lines)
        self.assertNotIn('"--color"', code)
        self.assertNotIn('"never"', code)

    def test_runner_no_hardcoded_allowed_tools_list(self):
        """Allowed tools list should be resolved from config, not hardcoded."""
        source = self._read_source("runner.py")
        code_lines = [l for l in source.split("\n") if not l.strip().startswith("#")]
        code = "\n".join(code_lines)
        # The actual tool names shouldn't appear in runner.py
        for tool in ["Edit", "Write", "Bash", "Glob", "Grep"]:
            # Only check in non-schema, non-import contexts
            self.assertNotIn(f'"{tool}"', code,
                             f"Found hardcoded tool name '{tool}' in runner.py")


# ---------------------------------------------------------------------------
# Integration: config changes affect runner behavior
# ---------------------------------------------------------------------------

class TestConfigDrivenBehavior(unittest.TestCase):
    """Verify that modifying config values actually changes runner behavior."""

    def test_custom_binary_appears_in_codex_command(self):
        backend = BackendConfig(binary="/usr/local/bin/my-codex", flags=["--verbose"])
        config = OrchestratorConfig(backends={"codex": backend})
        runner = CodexAgentRunner(config=config, backend=backend)

        with patch("agent_takt.runner.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            with patch("pathlib.Path.read_text", return_value='{"outcome": "ok"}'):
                try:
                    runner._exec_json("prompt", schema={}, workdir=Path("/tmp"))
                except Exception:
                    pass

            if mock_run.called:
                cmd = mock_run.call_args[0][0]
                self.assertEqual(cmd[0], "/usr/local/bin/my-codex")
                self.assertIn("--verbose", cmd)

    def test_custom_tools_appear_in_claude_command(self):
        backend = BackendConfig(
            binary="claude",
            flags=[],
            allowed_tools_default=["CustomTool1", "CustomTool2"],
            allowed_tools_by_agent={"developer": ["CustomTool3"]},
        )
        config = OrchestratorConfig(backends={"claude": backend})
        runner = ClaudeCodeAgentRunner(config=config, backend=backend)

        with patch("agent_takt.runner.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout='{"structured_output": {"outcome": "completed"}}',
            )
            runner._exec_json("prompt", schema={}, workdir=Path("/tmp"), agent_type="developer")
            cmd = mock_run.call_args[0][0]
            tools_idx = cmd.index("--allowedTools")
            tools_str = cmd[tools_idx + 1]
            self.assertIn("CustomTool1", tools_str)
            self.assertIn("CustomTool2", tools_str)
            self.assertIn("CustomTool3", tools_str)

    def test_empty_flags_produces_minimal_command(self):
        backend = BackendConfig(binary="codex", flags=[])
        config = OrchestratorConfig(backends={"codex": backend})
        runner = CodexAgentRunner(config=config, backend=backend)

        with patch("agent_takt.runner.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            with patch("pathlib.Path.read_text", return_value='{"outcome": "ok"}'):
                try:
                    runner._exec_json("prompt", schema={}, workdir=Path("/tmp"))
                except Exception:
                    pass

            if mock_run.called:
                cmd = mock_run.call_args[0][0]
                # Should have binary, "exec", structural flags, but no config flags
                self.assertEqual(cmd[0], "codex")
                self.assertEqual(cmd[1], "exec")
                # No --full-auto, --skip-git-repo-check, etc.
                self.assertNotIn("--full-auto", cmd)
                self.assertNotIn("--skip-git-repo-check", cmd)


# ---------------------------------------------------------------------------
# Backend resolution priority tests
# ---------------------------------------------------------------------------

class TestBackendResolutionPriority(unittest.TestCase):
    """Test the arg > env > config.default_runner resolution order in make_services."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        subprocess.run(["git", "init"], cwd=self.root, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"],
                       cwd=self.root, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test User"],
                       cwd=self.root, check=True, capture_output=True)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_arg_beats_env_and_config(self):
        """runner_backend arg overrides both env var and config."""
        # Write config with default_runner=codex
        orch_dir = self.root / ".takt"
        orch_dir.mkdir(parents=True, exist_ok=True)
        (orch_dir / "config.yaml").write_text(textwrap.dedent("""\
            common:
              default_runner: codex
            codex:
              binary: codex
              skills_dir: .agents
              flags: []
            claude:
              binary: claude
              skills_dir: .claude
              flags: []
              allowed_tools_default:
                - Read
        """))
        with patch.dict(os.environ, {"AGENT_TAKT_RUNNER": "codex"}, clear=False):
            _, scheduler, _ = make_services(self.root, runner_backend="claude")
            self.assertIsInstance(scheduler.runner, ClaudeCodeAgentRunner)

    def test_env_beats_config(self):
        """$AGENT_TAKT_RUNNER overrides config.default_runner when no arg given."""
        orch_dir = self.root / ".takt"
        orch_dir.mkdir(parents=True, exist_ok=True)
        (orch_dir / "config.yaml").write_text(textwrap.dedent("""\
            common:
              default_runner: codex
            codex:
              binary: codex
              skills_dir: .agents
              flags: []
            claude:
              binary: claude
              skills_dir: .claude
              flags: []
              allowed_tools_default:
                - Read
        """))
        with patch.dict(os.environ, {"AGENT_TAKT_RUNNER": "claude"}, clear=False):
            _, scheduler, _ = make_services(self.root)
            self.assertIsInstance(scheduler.runner, ClaudeCodeAgentRunner)

    def test_config_default_used_when_no_arg_or_env(self):
        """config.default_runner is used when no arg and no env var."""
        orch_dir = self.root / ".takt"
        orch_dir.mkdir(parents=True, exist_ok=True)
        (orch_dir / "config.yaml").write_text(textwrap.dedent("""\
            common:
              default_runner: claude
            codex:
              binary: codex
              skills_dir: .agents
              flags: []
            claude:
              binary: claude
              skills_dir: .claude
              flags: []
              allowed_tools_default:
                - Read
        """))
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AGENT_TAKT_RUNNER", None)
            os.environ.pop("ORCHESTRATOR_RUNNER", None)
            _, scheduler, _ = make_services(self.root)
            self.assertIsInstance(scheduler.runner, ClaudeCodeAgentRunner)


# ---------------------------------------------------------------------------
# AgentRunResult telemetry field tests
# ---------------------------------------------------------------------------

class TestAgentRunResultTelemetry(unittest.TestCase):
    """Verify AgentRunResult has an optional telemetry field defaulting to None."""

    def test_telemetry_defaults_to_none(self):
        result = AgentRunResult(outcome="completed", summary="ok")
        self.assertIsNone(result.telemetry)

    def test_telemetry_accepts_dict(self):
        metrics = {"duration_ms": 1234, "source": "measured"}
        result = AgentRunResult(outcome="completed", summary="ok", telemetry=metrics)
        self.assertEqual(result.telemetry, metrics)
        self.assertEqual(result.telemetry["duration_ms"], 1234)

    def test_telemetry_mutable_after_construction(self):
        result = AgentRunResult(outcome="completed", summary="ok")
        result.telemetry = {"cost_usd": 0.05}
        self.assertEqual(result.telemetry["cost_usd"], 0.05)


# ---------------------------------------------------------------------------
# Codex runner telemetry capture tests
# ---------------------------------------------------------------------------

class TestCodexRunnerTelemetry(unittest.TestCase):
    """Verify CodexAgentRunner.run_bead populates telemetry with measured metrics."""

    def _make_runner(self):
        backend = BackendConfig(binary="codex", flags=["--full-auto"])
        config = OrchestratorConfig(backends={"codex": backend})
        return CodexAgentRunner(config=config, backend=backend)

    def _result_payload(self):
        return {
            "outcome": "completed", "summary": "done", "completed": "",
            "remaining": "", "risks": "", "verdict": "approved",
            "findings_count": 0, "requires_followup": False,
            "expected_files": [], "expected_globs": [],
            "touched_files": [], "changed_files": [],
            "updated_docs": [], "next_action": "", "next_agent": "",
            "block_reason": "", "conflict_risks": "", "new_beads": [],
        }

    def test_run_bead_populates_telemetry(self):
        runner = self._make_runner()
        bead = MagicMock()
        bead.agent_type = "developer"

        payload = self._result_payload()
        with patch.object(runner, "_exec_json", return_value=payload), \
             patch("agent_takt.runner.build_worker_prompt", return_value="test prompt"):
            result = runner.run_bead(bead, workdir=Path("/tmp"), context_paths=[])

        self.assertIsNotNone(result.telemetry)
        self.assertEqual(result.telemetry["source"], "measured")
        self.assertIn("duration_ms", result.telemetry)
        self.assertIsInstance(result.telemetry["duration_ms"], int)
        self.assertGreaterEqual(result.telemetry["duration_ms"], 0)

    def test_codex_telemetry_has_prompt_size(self):
        runner = self._make_runner()
        bead = MagicMock()
        bead.agent_type = "developer"

        payload = self._result_payload()
        prompt_text = "line one\nline two\nline three"
        with patch.object(runner, "_exec_json", return_value=payload), \
             patch("agent_takt.runner.build_worker_prompt", return_value=prompt_text):
            result = runner.run_bead(bead, workdir=Path("/tmp"), context_paths=[])

        self.assertEqual(result.telemetry["prompt_chars"], len(prompt_text))
        self.assertEqual(result.telemetry["prompt_lines"], 3)

    def test_codex_telemetry_has_prompt_and_response_text(self):
        runner = self._make_runner()
        bead = MagicMock()
        bead.agent_type = "developer"

        payload = self._result_payload()
        prompt_text = "my prompt"
        with patch.object(runner, "_exec_json", return_value=payload), \
             patch("agent_takt.runner.build_worker_prompt", return_value=prompt_text):
            result = runner.run_bead(bead, workdir=Path("/tmp"), context_paths=[])

        self.assertEqual(result.telemetry["prompt_text"], prompt_text)
        self.assertIn("response_text", result.telemetry)

    def test_codex_telemetry_required_fields(self):
        """Codex telemetry must contain exactly the spec-defined fields."""
        runner = self._make_runner()
        bead = MagicMock()
        bead.agent_type = "developer"

        payload = self._result_payload()
        with patch.object(runner, "_exec_json", return_value=payload), \
             patch("agent_takt.runner.build_worker_prompt", return_value="p"):
            result = runner.run_bead(bead, workdir=Path("/tmp"), context_paths=[])

        expected_keys = {
            "duration_ms", "prompt_chars", "prompt_lines",
            "source", "prompt_text", "response_text",
        }
        self.assertEqual(set(result.telemetry.keys()), expected_keys)


# ---------------------------------------------------------------------------
# Claude runner telemetry capture tests
# ---------------------------------------------------------------------------

class TestClaudeRunnerTelemetry(unittest.TestCase):
    """Verify ClaudeCodeAgentRunner.run_bead populates telemetry with provider metrics."""

    def _make_runner(self):
        backend = BackendConfig(
            binary="claude", flags=[],
            allowed_tools_default=["Read"],
            allowed_tools_by_agent={},
        )
        config = OrchestratorConfig(backends={"claude": backend})
        return ClaudeCodeAgentRunner(config=config, backend=backend)

    def _result_payload(self):
        return {
            "outcome": "completed", "summary": "done", "completed": "",
            "remaining": "", "risks": "", "verdict": "approved",
            "findings_count": 0, "requires_followup": False,
            "expected_files": [], "expected_globs": [],
            "touched_files": [], "changed_files": [],
            "updated_docs": [], "next_action": "", "next_agent": "",
            "block_reason": "", "conflict_risks": "", "new_beads": [],
        }

    def _mock_response(self, payload):
        return {
            "structured_output": payload,
            "total_cost_usd": 0.42,
            "duration_api_ms": 12000,
            "num_turns": 5,
            "usage": {
                "input_tokens": 18000,
                "output_tokens": 800,
                "cache_creation_input_tokens": 5500,
                "cache_read_input_tokens": 12500,
            },
            "stop_reason": "end_turn",
            "session_id": "abc-123",
            "permission_denials": [],
        }

    def test_run_bead_populates_telemetry(self):
        runner = self._make_runner()
        bead = MagicMock()
        bead.agent_type = "developer"

        payload = self._result_payload()
        response = self._mock_response(payload)
        with patch.object(runner, "_exec_json_with_response", return_value=(payload, response)), \
             patch("agent_takt.runner.build_worker_prompt", return_value="test prompt"):
            result = runner.run_bead(bead, workdir=Path("/tmp"), context_paths=[])

        self.assertIsNotNone(result.telemetry)
        self.assertEqual(result.telemetry["source"], "provider")

    def test_claude_telemetry_captures_cost(self):
        runner = self._make_runner()
        bead = MagicMock()
        bead.agent_type = "developer"

        payload = self._result_payload()
        response = self._mock_response(payload)
        with patch.object(runner, "_exec_json_with_response", return_value=(payload, response)), \
             patch("agent_takt.runner.build_worker_prompt", return_value="p"):
            result = runner.run_bead(bead, workdir=Path("/tmp"), context_paths=[])

        self.assertEqual(result.telemetry["cost_usd"], 0.42)

    def test_claude_telemetry_captures_usage_tokens(self):
        runner = self._make_runner()
        bead = MagicMock()
        bead.agent_type = "developer"

        payload = self._result_payload()
        response = self._mock_response(payload)
        with patch.object(runner, "_exec_json_with_response", return_value=(payload, response)), \
             patch("agent_takt.runner.build_worker_prompt", return_value="p"):
            result = runner.run_bead(bead, workdir=Path("/tmp"), context_paths=[])

        self.assertEqual(result.telemetry["input_tokens"], 18000)
        self.assertEqual(result.telemetry["output_tokens"], 800)
        self.assertEqual(result.telemetry["cache_creation_tokens"], 5500)
        self.assertEqual(result.telemetry["cache_read_tokens"], 12500)

    def test_claude_telemetry_captures_session_and_turns(self):
        runner = self._make_runner()
        bead = MagicMock()
        bead.agent_type = "developer"

        payload = self._result_payload()
        response = self._mock_response(payload)
        with patch.object(runner, "_exec_json_with_response", return_value=(payload, response)), \
             patch("agent_takt.runner.build_worker_prompt", return_value="p"):
            result = runner.run_bead(bead, workdir=Path("/tmp"), context_paths=[])

        self.assertEqual(result.telemetry["num_turns"], 5)
        self.assertEqual(result.telemetry["session_id"], "abc-123")
        self.assertEqual(result.telemetry["stop_reason"], "end_turn")
        self.assertEqual(result.telemetry["duration_api_ms"], 12000)
        self.assertEqual(result.telemetry["permission_denials"], [])

    def test_claude_telemetry_has_prompt_size(self):
        runner = self._make_runner()
        bead = MagicMock()
        bead.agent_type = "developer"

        payload = self._result_payload()
        response = self._mock_response(payload)
        prompt_text = "line one\nline two"
        with patch.object(runner, "_exec_json_with_response", return_value=(payload, response)), \
             patch("agent_takt.runner.build_worker_prompt", return_value=prompt_text):
            result = runner.run_bead(bead, workdir=Path("/tmp"), context_paths=[])

        self.assertEqual(result.telemetry["prompt_chars"], len(prompt_text))
        self.assertEqual(result.telemetry["prompt_lines"], 2)

    def test_claude_telemetry_has_wall_clock_duration(self):
        runner = self._make_runner()
        bead = MagicMock()
        bead.agent_type = "developer"

        payload = self._result_payload()
        response = self._mock_response(payload)
        with patch.object(runner, "_exec_json_with_response", return_value=(payload, response)), \
             patch("agent_takt.runner.build_worker_prompt", return_value="p"):
            result = runner.run_bead(bead, workdir=Path("/tmp"), context_paths=[])

        self.assertIn("duration_ms", result.telemetry)
        self.assertIsInstance(result.telemetry["duration_ms"], int)
        self.assertGreaterEqual(result.telemetry["duration_ms"], 0)

    def test_claude_telemetry_has_prompt_and_response_text(self):
        runner = self._make_runner()
        bead = MagicMock()
        bead.agent_type = "developer"

        payload = self._result_payload()
        response = self._mock_response(payload)
        with patch.object(runner, "_exec_json_with_response", return_value=(payload, response)), \
             patch("agent_takt.runner.build_worker_prompt", return_value="my prompt"):
            result = runner.run_bead(bead, workdir=Path("/tmp"), context_paths=[])

        self.assertEqual(result.telemetry["prompt_text"], "my prompt")
        self.assertIn("response_text", result.telemetry)

    def test_claude_telemetry_required_fields(self):
        """Claude telemetry must contain all spec-defined fields."""
        runner = self._make_runner()
        bead = MagicMock()
        bead.agent_type = "developer"

        payload = self._result_payload()
        response = self._mock_response(payload)
        with patch.object(runner, "_exec_json_with_response", return_value=(payload, response)), \
             patch("agent_takt.runner.build_worker_prompt", return_value="p"):
            result = runner.run_bead(bead, workdir=Path("/tmp"), context_paths=[])

        expected_keys = {
            "cost_usd", "duration_ms", "duration_api_ms", "num_turns",
            "input_tokens", "output_tokens", "cache_creation_tokens",
            "cache_read_tokens", "stop_reason", "session_id",
            "permission_denials", "prompt_chars", "prompt_lines",
            "source", "prompt_text", "response_text",
        }
        self.assertEqual(set(result.telemetry.keys()), expected_keys)

    def test_claude_telemetry_handles_missing_usage(self):
        """When response has no usage block, token fields should be None."""
        runner = self._make_runner()
        bead = MagicMock()
        bead.agent_type = "developer"

        payload = self._result_payload()
        response = {
            "structured_output": payload,
            # No usage, no other optional fields
        }
        with patch.object(runner, "_exec_json_with_response", return_value=(payload, response)), \
             patch("agent_takt.runner.build_worker_prompt", return_value="p"):
            result = runner.run_bead(bead, workdir=Path("/tmp"), context_paths=[])

        self.assertIsNone(result.telemetry["input_tokens"])
        self.assertIsNone(result.telemetry["output_tokens"])
        self.assertIsNone(result.telemetry["cache_creation_tokens"])
        self.assertIsNone(result.telemetry["cache_read_tokens"])
        self.assertIsNone(result.telemetry["cost_usd"])
        self.assertIsNone(result.telemetry["num_turns"])


if __name__ == "__main__":
    unittest.main()
