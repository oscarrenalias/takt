"""Tests for B0134: Per-agent-type model selection for Claude Code.

Validates:
1. config.model_for() resolution logic (per-agent, fallback, None)
2. default_config() includes model_default and model_by_agent for claude
3. load_config() reads model_default and model_by_agent from YAML
4. ClaudeCodeAgentRunner passes --model to CLI commands
5. --model is omitted when model_for() returns None
6. Codex backend has no model fields (no --model passed)
"""
from __future__ import annotations

import json
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
from agent_takt.runner import ClaudeCodeAgentRunner


# ---------------------------------------------------------------------------
# config.model_for() unit tests
# ---------------------------------------------------------------------------

class TestModelFor(unittest.TestCase):
    """Test OrchestratorConfig.model_for() resolution logic."""

    def setUp(self):
        self.cfg = default_config()

    def test_returns_per_agent_model_when_set(self):
        """model_for returns the agent-specific model from model_by_agent."""
        expected = {
            "developer": "claude-sonnet-4-6",
            "tester": "claude-sonnet-4-6",
            "planner": "claude-sonnet-4-6",
            "review": "claude-haiku-4-5-20251001",
            "documentation": "claude-haiku-4-5-20251001",
        }
        for agent, expected_model in expected.items():
            model = self.cfg.model_for("claude", agent)
            self.assertEqual(model, expected_model, f"agent={agent}")

    def test_falls_back_to_model_default(self):
        """When agent_type is not in model_by_agent, falls back to model_default."""
        backend = BackendConfig(
            binary="claude",
            model_default="claude-opus-4-6",
            model_by_agent={},  # no per-agent overrides
        )
        cfg = OrchestratorConfig(backends={"claude": backend})
        self.assertEqual(cfg.model_for("claude", "developer"), "claude-opus-4-6")
        self.assertEqual(cfg.model_for("claude", "unknown_agent"), "claude-opus-4-6")

    def test_returns_none_when_no_model_configured(self):
        """When neither model_by_agent nor model_default is set, returns None."""
        backend = BackendConfig(
            binary="claude",
            model_default=None,
            model_by_agent={},
        )
        cfg = OrchestratorConfig(backends={"claude": backend})
        self.assertIsNone(cfg.model_for("claude", "developer"))

    def test_per_agent_overrides_default(self):
        """Per-agent model takes precedence over model_default."""
        backend = BackendConfig(
            binary="claude",
            model_default="claude-sonnet-4-6",
            model_by_agent={"developer": "claude-opus-4-6"},
        )
        cfg = OrchestratorConfig(backends={"claude": backend})
        self.assertEqual(cfg.model_for("claude", "developer"), "claude-opus-4-6")
        # Other agents fall back to default
        self.assertEqual(cfg.model_for("claude", "tester"), "claude-sonnet-4-6")

    def test_nonexistent_backend_raises(self):
        """model_for on unknown backend raises KeyError."""
        with self.assertRaises(KeyError):
            self.cfg.model_for("nonexistent", "developer")

    def test_codex_backend_returns_none(self):
        """Codex backend has no model fields, so model_for returns None."""
        self.assertIsNone(self.cfg.model_for("codex", "developer"))


# ---------------------------------------------------------------------------
# default_config() model fields
# ---------------------------------------------------------------------------

class TestDefaultConfigModelFields(unittest.TestCase):
    """Verify default_config() has correct model fields for claude backend."""

    def setUp(self):
        self.claude = default_config().backend("claude")

    def test_model_default(self):
        self.assertEqual(self.claude.model_default, "claude-sonnet-4-6")

    def test_model_by_agent_all_types(self):
        expected = {
            "developer": "claude-sonnet-4-6",
            "tester": "claude-sonnet-4-6",
            "planner": "claude-sonnet-4-6",
            "review": "claude-haiku-4-5-20251001",
            "documentation": "claude-haiku-4-5-20251001",
        }
        for agent, expected_model in expected.items():
            self.assertIn(agent, self.claude.model_by_agent)
            self.assertEqual(self.claude.model_by_agent[agent], expected_model)

    def test_codex_has_no_model(self):
        codex = default_config().backend("codex")
        self.assertIsNone(codex.model_default)
        self.assertEqual(codex.model_by_agent, {})


# ---------------------------------------------------------------------------
# load_config() reads model fields from YAML
# ---------------------------------------------------------------------------

class TestLoadConfigModelFields(unittest.TestCase):
    """Verify load_config() correctly parses model_default and model_by_agent."""

    def _write_config(self, tmp: Path, yaml_text: str):
        orch_dir = tmp / ".takt"
        orch_dir.mkdir(parents=True, exist_ok=True)
        (orch_dir / "config.yaml").write_text(textwrap.dedent(yaml_text))

    def test_custom_model_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_config(Path(tmp), """\
                claude:
                  binary: claude
                  model_default: claude-opus-4-6
            """)
            cfg = load_config(Path(tmp))
            self.assertEqual(cfg.backend("claude").model_default, "claude-opus-4-6")

    def test_custom_model_by_agent(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_config(Path(tmp), """\
                claude:
                  binary: claude
                  model_default: claude-sonnet-4-6
                  model_by_agent:
                    developer: claude-opus-4-6
                    tester: claude-haiku-4-5-20251001
            """)
            cfg = load_config(Path(tmp))
            claude = cfg.backend("claude")
            self.assertEqual(claude.model_by_agent["developer"], "claude-opus-4-6")
            self.assertEqual(claude.model_by_agent["tester"], "claude-haiku-4-5-20251001")

    def test_no_model_fields_in_yaml(self):
        """When model fields are absent in YAML, backend has None/empty."""
        with tempfile.TemporaryDirectory() as tmp:
            self._write_config(Path(tmp), """\
                claude:
                  binary: claude
                  flags:
                    - "--dangerously-skip-permissions"
            """)
            cfg = load_config(Path(tmp))
            claude = cfg.backend("claude")
            self.assertIsNone(claude.model_default)
            self.assertEqual(claude.model_by_agent, {})



# ---------------------------------------------------------------------------
# Runner --model flag integration
# ---------------------------------------------------------------------------

class TestClaudeRunnerModelFlag(unittest.TestCase):
    """Verify ClaudeCodeAgentRunner passes --model to subprocess commands."""

    def _make_runner(self, model_default=None, model_by_agent=None):
        backend = BackendConfig(
            binary="claude",
            flags=["--dangerously-skip-permissions"],
            allowed_tools_default=["Read"],
            allowed_tools_by_agent={},
            model_default=model_default,
            model_by_agent=model_by_agent or {},
        )
        config = OrchestratorConfig(backends={"claude": backend})
        return ClaudeCodeAgentRunner(config=config, backend=backend)

    def test_model_flag_present_when_model_set(self):
        """--model is included in the command when model_for returns a value."""
        runner = self._make_runner(model_default="claude-sonnet-4-6")
        response = {"structured_output": {"outcome": "completed"}}

        with patch("agent_takt.runner.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=json.dumps(response),
            )
            runner._exec_json(
                "test prompt", schema={}, workdir=Path("/tmp"),
                agent_type="developer",
            )
            cmd = mock_run.call_args[0][0]
            self.assertIn("--model", cmd)
            model_idx = cmd.index("--model")
            self.assertEqual(cmd[model_idx + 1], "claude-sonnet-4-6")

    def test_model_flag_absent_when_no_model(self):
        """--model is NOT included when model_for returns None."""
        runner = self._make_runner(model_default=None, model_by_agent={})
        response = {"structured_output": {"outcome": "completed"}}

        with patch("agent_takt.runner.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=json.dumps(response),
            )
            runner._exec_json(
                "test prompt", schema={}, workdir=Path("/tmp"),
                agent_type="developer",
            )
            cmd = mock_run.call_args[0][0]
            self.assertNotIn("--model", cmd)

    def test_per_agent_model_used_in_command(self):
        """Per-agent model override is passed to the CLI."""
        runner = self._make_runner(
            model_default="claude-sonnet-4-6",
            model_by_agent={"planner": "claude-opus-4-6"},
        )
        response = {"structured_output": {"outcome": "completed"}}

        with patch("agent_takt.runner.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=json.dumps(response),
            )
            runner._exec_json(
                "test prompt", schema={}, workdir=Path("/tmp"),
                agent_type="planner",
            )
            cmd = mock_run.call_args[0][0]
            model_idx = cmd.index("--model")
            self.assertEqual(cmd[model_idx + 1], "claude-opus-4-6")

    def test_retry_also_uses_model_flag(self):
        """_retry_structured_output also passes --model."""
        runner = self._make_runner(model_default="claude-sonnet-4-6")
        retry_response = {"structured_output": {"outcome": "completed"}}

        with patch("agent_takt.runner.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=json.dumps(retry_response),
            )
            runner._retry_structured_output(
                "some text", schema={}, workdir=Path("/tmp"),
                agent_type="tester",
            )
            cmd = mock_run.call_args[0][0]
            self.assertIn("--model", cmd)
            model_idx = cmd.index("--model")
            self.assertEqual(cmd[model_idx + 1], "claude-sonnet-4-6")

    def test_retry_omits_model_when_none(self):
        """_retry_structured_output omits --model when model is None."""
        runner = self._make_runner(model_default=None)
        retry_response = {"structured_output": {"outcome": "completed"}}

        with patch("agent_takt.runner.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=json.dumps(retry_response),
            )
            runner._retry_structured_output(
                "some text", schema={}, workdir=Path("/tmp"),
                agent_type="tester",
            )
            cmd = mock_run.call_args[0][0]
            self.assertNotIn("--model", cmd)

    def test_default_runner_uses_model_from_config(self):
        """Default-constructed ClaudeCodeAgentRunner uses model from default_config."""
        runner = ClaudeCodeAgentRunner()
        response = {"structured_output": {"outcome": "completed"}}

        with patch("agent_takt.runner.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=json.dumps(response),
            )
            runner._exec_json(
                "test prompt", schema={}, workdir=Path("/tmp"),
                agent_type="developer",
            )
            cmd = mock_run.call_args[0][0]
            self.assertIn("--model", cmd)
            model_idx = cmd.index("--model")
            self.assertEqual(cmd[model_idx + 1], "claude-sonnet-4-6")


if __name__ == "__main__":
    unittest.main()
