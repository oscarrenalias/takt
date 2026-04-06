"""Tests for subprocess timeout handling in CodexAgentRunner and ClaudeCodeAgentRunner."""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_takt.config import BackendConfig, OrchestratorConfig, default_config
from agent_takt.runner import CodexAgentRunner, ClaudeCodeAgentRunner


class TestCodexRunnerTimeout(unittest.TestCase):
    """Verify CodexAgentRunner._exec_json passes timeout and handles TimeoutExpired."""

    def _make_runner(self, timeout: int = 600) -> CodexAgentRunner:
        cfg = default_config()
        backend = BackendConfig(
            binary="codex",
            skills_dir=".agents",
            flags=["--full-auto"],
            timeout_seconds=timeout,
        )
        return CodexAgentRunner(config=cfg, backend=backend)

    @patch("agent_takt.runner.subprocess.run")
    def test_timeout_passed_to_subprocess(self, mock_run):
        """subprocess.run receives the configured timeout_seconds."""
        mock_run.return_value = MagicMock(returncode=0)
        runner = self._make_runner(timeout=120)
        # _exec_json reads from a temp file; mock that too
        with patch("pathlib.Path.read_text", return_value='{"key": "value"}'):
            result = runner._exec_json("test prompt", schema={}, workdir=Path("/tmp"))
        # Verify timeout was passed
        _, kwargs = mock_run.call_args
        self.assertEqual(kwargs["timeout"], 120)

    @patch("agent_takt.runner.subprocess.run")
    def test_default_timeout_value(self, mock_run):
        """Default runner uses 600s timeout."""
        mock_run.return_value = MagicMock(returncode=0)
        runner = self._make_runner()
        with patch("pathlib.Path.read_text", return_value='{"key": "value"}'):
            runner._exec_json("test prompt", schema={}, workdir=Path("/tmp"))
        _, kwargs = mock_run.call_args
        self.assertEqual(kwargs["timeout"], 600)

    @patch("agent_takt.runner.subprocess.run")
    def test_timeout_expired_raises_runtime_error(self, mock_run):
        """TimeoutExpired is caught and re-raised as RuntimeError."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="codex", timeout=120)
        runner = self._make_runner(timeout=120)
        with self.assertRaises(RuntimeError) as ctx:
            runner._exec_json("test prompt", schema={}, workdir=Path("/tmp"))
        self.assertIn("timed out", str(ctx.exception))
        self.assertIn("120", str(ctx.exception))


class TestClaudeRunnerTimeout(unittest.TestCase):
    """Verify ClaudeCodeAgentRunner passes timeout and handles TimeoutExpired."""

    def _make_runner(self, timeout: int = 600, retry_timeout: int = 300) -> ClaudeCodeAgentRunner:
        cfg = default_config()
        backend = BackendConfig(
            binary="claude",
            skills_dir=".claude",
            flags=["--dangerously-skip-permissions"],
            allowed_tools_default=["Read", "Write"],
            timeout_seconds=timeout,
            retry_timeout_seconds=retry_timeout,
        )
        # Replace the claude backend in the config
        return ClaudeCodeAgentRunner(config=cfg, backend=backend)

    @patch("agent_takt.runner.subprocess.run")
    def test_timeout_passed_to_main_subprocess(self, mock_run):
        """_exec_json_with_response passes timeout_seconds to subprocess.run."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({"structured_output": {"key": "val"}}),
        )
        runner = self._make_runner(timeout=180)
        result, _ = runner._exec_json_with_response(
            "test prompt", schema={}, workdir=Path("/tmp"),
        )
        _, kwargs = mock_run.call_args
        self.assertEqual(kwargs["timeout"], 180)

    @patch("agent_takt.runner.subprocess.run")
    def test_default_timeout_value(self, mock_run):
        """Default runner uses 600s timeout."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({"structured_output": {"key": "val"}}),
        )
        runner = self._make_runner()
        runner._exec_json_with_response(
            "test prompt", schema={}, workdir=Path("/tmp"),
        )
        _, kwargs = mock_run.call_args
        self.assertEqual(kwargs["timeout"], 600)

    @patch("agent_takt.runner.subprocess.run")
    def test_timeout_expired_raises_runtime_error(self, mock_run):
        """TimeoutExpired on main call raises RuntimeError."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="claude", timeout=180)
        runner = self._make_runner(timeout=180)
        with self.assertRaises(RuntimeError) as ctx:
            runner._exec_json_with_response(
                "test prompt", schema={}, workdir=Path("/tmp"),
            )
        self.assertIn("timed out", str(ctx.exception))
        self.assertIn("180", str(ctx.exception))

    @patch("agent_takt.runner.subprocess.run")
    def test_retry_timeout_passed_to_subprocess(self, mock_run):
        """_retry_structured_output uses retry_timeout_seconds."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({"structured_output": {"key": "val"}}),
        )
        runner = self._make_runner(retry_timeout=60)
        result = runner._retry_structured_output(
            "some agent result", schema={}, workdir=Path("/tmp"),
        )
        _, kwargs = mock_run.call_args
        self.assertEqual(kwargs["timeout"], 60)

    @patch("agent_takt.runner.subprocess.run")
    def test_retry_default_timeout(self, mock_run):
        """Default retry timeout is 300s."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({"structured_output": {"key": "val"}}),
        )
        runner = self._make_runner()
        runner._retry_structured_output(
            "some agent result", schema={}, workdir=Path("/tmp"),
        )
        _, kwargs = mock_run.call_args
        self.assertEqual(kwargs["timeout"], 300)

    @patch("agent_takt.runner.subprocess.run")
    def test_retry_timeout_expired_raises_runtime_error(self, mock_run):
        """TimeoutExpired on retry raises RuntimeError with retry-specific message."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="claude", timeout=60)
        runner = self._make_runner(retry_timeout=60)
        with self.assertRaises(RuntimeError) as ctx:
            runner._retry_structured_output(
                "some agent result", schema={}, workdir=Path("/tmp"),
            )
        self.assertIn("retry timed out", str(ctx.exception))
        self.assertIn("60", str(ctx.exception))


class TestRetryStructuredOutputCommand(unittest.TestCase):
    """Verify _retry_structured_output builds the correct subprocess command."""

    def _make_runner(self, retry_timeout: int = 300) -> ClaudeCodeAgentRunner:
        cfg = default_config()
        backend = BackendConfig(
            binary="claude",
            skills_dir=".claude",
            flags=["--dangerously-skip-permissions"],
            allowed_tools_default=["Read", "Write"],
            timeout_seconds=600,
            retry_timeout_seconds=retry_timeout,
        )
        return ClaudeCodeAgentRunner(config=cfg, backend=backend)

    @patch("agent_takt.runner.subprocess.run")
    def test_retry_includes_allowed_tools_flag(self, mock_run):
        """--allowedTools with empty string is present in the retry command."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({"structured_output": {"key": "val"}}),
        )
        runner = self._make_runner()
        runner._retry_structured_output("some result", schema={}, workdir=Path("/tmp"))
        args, _ = mock_run.call_args
        cmd = args[0]
        self.assertIn("--allowedTools", cmd)
        idx = cmd.index("--allowedTools")
        self.assertEqual(cmd[idx + 1], "", "--allowedTools must be followed by an empty string")

    @patch("agent_takt.runner.subprocess.run")
    def test_retry_excludes_backend_flags(self, mock_run):
        """Backend flags (e.g. --dangerously-skip-permissions) are not included in retry command."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({"structured_output": {"key": "val"}}),
        )
        runner = self._make_runner()
        runner._retry_structured_output("some result", schema={}, workdir=Path("/tmp"))
        args, _ = mock_run.call_args
        cmd = args[0]
        self.assertNotIn("--dangerously-skip-permissions", cmd)

    @patch("agent_takt.runner.subprocess.run")
    def test_retry_includes_max_turns_one(self, mock_run):
        """--max-turns 1 is present in retry command to enforce single-turn."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({"structured_output": {"key": "val"}}),
        )
        runner = self._make_runner()
        runner._retry_structured_output("some result", schema={}, workdir=Path("/tmp"))
        args, _ = mock_run.call_args
        cmd = args[0]
        self.assertIn("--max-turns", cmd)
        idx = cmd.index("--max-turns")
        self.assertEqual(cmd[idx + 1], "1")


class TestTimeoutConfigIntegration(unittest.TestCase):
    """Verify runners pick up timeout from their BackendConfig."""

    def test_codex_runner_uses_backend_timeout(self):
        """CodexAgentRunner stores the backend's timeout_seconds."""
        runner = CodexAgentRunner()
        self.assertEqual(runner.backend.timeout_seconds, 600)

    def test_claude_runner_uses_backend_timeout(self):
        """ClaudeCodeAgentRunner stores the backend's timeout_seconds."""
        runner = ClaudeCodeAgentRunner()
        self.assertEqual(runner.backend.timeout_seconds, 600)
        self.assertEqual(runner.backend.retry_timeout_seconds, 300)

    def test_custom_timeout_propagates(self):
        """Custom BackendConfig timeout propagates to runner."""
        backend = BackendConfig(timeout_seconds=999, retry_timeout_seconds=111)
        runner = ClaudeCodeAgentRunner(backend=backend)
        self.assertEqual(runner.backend.timeout_seconds, 999)
        self.assertEqual(runner.backend.retry_timeout_seconds, 111)


if __name__ == "__main__":
    unittest.main()
