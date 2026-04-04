"""Tests for B0129: Capture telemetry from main run, not retry.

Validates _add_numeric helper and the telemetry merge behaviour when
_retry_structured_output succeeds — cost and duration from the retry
are folded into the main response envelope.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from codex_orchestrator.config import BackendConfig, OrchestratorConfig
from codex_orchestrator.runner import ClaudeCodeAgentRunner, _add_numeric, _extract_json_from_text


# ---------------------------------------------------------------------------
# _extract_json_from_text unit tests
# ---------------------------------------------------------------------------

class TestExtractJsonFromText(unittest.TestCase):
    """Unit tests for _extract_json_from_text extraction strategies."""

    def test_direct_json(self):
        """Direct JSON parse succeeds."""
        result = _extract_json_from_text('{"outcome": "completed"}')
        self.assertEqual(result, {"outcome": "completed"})

    def test_outer_code_fence(self):
        """Entire text is a code fence block."""
        text = '```json\n{"outcome": "completed"}\n```'
        result = _extract_json_from_text(text)
        self.assertEqual(result, {"outcome": "completed"})

    def test_embedded_code_fence(self):
        """JSON is embedded in a code fence within surrounding text."""
        text = (
            "The tests passed successfully.\n\n"
            "```json\n"
            '{"outcome": "completed", "verdict": "approved"}\n'
            "```\n\n"
            "All checks green."
        )
        result = _extract_json_from_text(text)
        self.assertEqual(result["outcome"], "completed")
        self.assertEqual(result["verdict"], "approved")

    def test_embedded_json_object_in_conversational_text(self):
        """JSON object is embedded directly in conversational text without code fence."""
        text = (
            'The background task completed successfully. The structured output verdict stands: '
            '{"outcome": "completed", "verdict": "approved", "summary": "tests passed", '
            '"findings_count": 0}'
        )
        result = _extract_json_from_text(text)
        self.assertIsNotNone(result)
        self.assertEqual(result["outcome"], "completed")

    def test_non_json_returns_none(self):
        """Purely conversational text with no JSON returns None."""
        result = _extract_json_from_text("Tests passed. Everything looks good.")
        self.assertIsNone(result)

    def test_empty_string_returns_none(self):
        result = _extract_json_from_text("")
        self.assertIsNone(result)

    def test_non_dict_json_returns_none(self):
        """JSON that is not a dict (e.g., array) returns None."""
        result = _extract_json_from_text("[1, 2, 3]")
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# _add_numeric unit tests
# ---------------------------------------------------------------------------

class TestAddNumeric(unittest.TestCase):
    """Unit tests for the _add_numeric helper."""

    def test_adds_values(self):
        target = {"x": 10}
        _add_numeric(target, {"x": 5}, "x")
        self.assertEqual(target["x"], 15)

    def test_source_missing_key_is_noop(self):
        target = {"x": 10}
        _add_numeric(target, {}, "x")
        self.assertEqual(target["x"], 10)

    def test_source_none_value_is_noop(self):
        target = {"x": 10}
        _add_numeric(target, {"x": None}, "x")
        self.assertEqual(target["x"], 10)

    def test_target_missing_key_uses_zero(self):
        target = {}
        _add_numeric(target, {"x": 7}, "x")
        self.assertEqual(target["x"], 7)

    def test_target_none_value_uses_zero(self):
        target = {"x": None}
        _add_numeric(target, {"x": 3}, "x")
        self.assertEqual(target["x"], 3)

    def test_both_zero(self):
        target = {"x": 0}
        _add_numeric(target, {"x": 0}, "x")
        self.assertEqual(target["x"], 0)

    def test_float_values(self):
        target = {"cost": 0.05}
        _add_numeric(target, {"cost": 0.02}, "cost")
        self.assertAlmostEqual(target["cost"], 0.07)


# ---------------------------------------------------------------------------
# _retry_structured_output return-type tests
# ---------------------------------------------------------------------------

class TestRetryStructuredOutputTuple(unittest.TestCase):
    """Verify _retry_structured_output returns (payload, response) tuples."""

    def _make_runner(self):
        backend = BackendConfig(
            binary="claude",
            flags=[],
            allowed_tools_default=["Read"],
            allowed_tools_by_agent={},
        )
        config = OrchestratorConfig(backends={"claude": backend})
        return ClaudeCodeAgentRunner(config=config, backend=backend)

    def test_success_returns_payload_and_response(self):
        runner = self._make_runner()
        retry_response = {"structured_output": {"outcome": "completed"}, "total_cost_usd": 0.01}
        with patch("codex_orchestrator.runner.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=json.dumps(retry_response),
            )
            payload, resp = runner._retry_structured_output(
                "some text", schema={}, workdir=Path("/tmp"),
            )
        self.assertEqual(payload, {"outcome": "completed"})
        self.assertEqual(resp, retry_response)

    def test_failure_returns_none_none(self):
        runner = self._make_runner()
        with patch("codex_orchestrator.runner.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="")
            payload, resp = runner._retry_structured_output(
                "text", schema={}, workdir=Path("/tmp"),
            )
        self.assertIsNone(payload)
        self.assertIsNone(resp)

    def test_bad_json_returns_none_none(self):
        runner = self._make_runner()
        with patch("codex_orchestrator.runner.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="not json")
            payload, resp = runner._retry_structured_output(
                "text", schema={}, workdir=Path("/tmp"),
            )
        self.assertIsNone(payload)
        self.assertIsNone(resp)

    def test_result_text_fallback_returns_tuple(self):
        """When structured_output is absent but result parses as JSON."""
        runner = self._make_runner()
        retry_response = {"result": '{"outcome": "done"}', "total_cost_usd": 0.03}
        with patch("codex_orchestrator.runner.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=json.dumps(retry_response),
            )
            payload, resp = runner._retry_structured_output(
                "text", schema={}, workdir=Path("/tmp"),
            )
        self.assertEqual(payload, {"outcome": "done"})
        self.assertEqual(resp, retry_response)


# ---------------------------------------------------------------------------
# Telemetry merge in _exec_json_with_response
# ---------------------------------------------------------------------------

class TestTelemetryMerge(unittest.TestCase):
    """Verify retry cost/duration is merged into the main response."""

    def _make_runner(self):
        backend = BackendConfig(
            binary="claude",
            flags=[],
            allowed_tools_default=["Read"],
            allowed_tools_by_agent={},
        )
        config = OrchestratorConfig(backends={"claude": backend})
        return ClaudeCodeAgentRunner(config=config, backend=backend)

    def test_retry_merges_cost_and_duration(self):
        """When main run needs a retry, cost and duration are summed."""
        runner = self._make_runner()

        # Main response has no structured_output, only a conversational result
        main_response = {
            "result": "Here is the summary ...",
            "total_cost_usd": 0.10,
            "duration_api_ms": 5000,
            "is_error": False,
            "num_turns": 5,
            "session_id": "main-session",
        }
        retry_response = {
            "structured_output": {"outcome": "completed"},
            "total_cost_usd": 0.02,
            "duration_api_ms": 1000,
        }

        with patch("codex_orchestrator.runner.subprocess.run") as mock_run:
            # First call: main run returns conversational result
            # Second call: retry returns structured output
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout=json.dumps(main_response)),
                MagicMock(returncode=0, stdout=json.dumps(retry_response)),
            ]
            payload, response = runner._exec_json_with_response(
                "test prompt", schema={}, workdir=Path("/tmp"),
            )

        self.assertEqual(payload, {"outcome": "completed"})
        # Cost and duration should be summed
        self.assertAlmostEqual(response["total_cost_usd"], 0.12)
        self.assertEqual(response["duration_api_ms"], 6000)
        # Other fields should remain from the main run
        self.assertEqual(response["num_turns"], 5)
        self.assertEqual(response["session_id"], "main-session")

    def test_no_retry_preserves_response(self):
        """When structured_output is present, no retry and no merge."""
        runner = self._make_runner()

        main_response = {
            "structured_output": {"outcome": "completed"},
            "total_cost_usd": 0.10,
            "duration_api_ms": 5000,
        }

        with patch("codex_orchestrator.runner.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout=json.dumps(main_response),
            )
            payload, response = runner._exec_json_with_response(
                "test prompt", schema={}, workdir=Path("/tmp"),
            )

        self.assertEqual(payload, {"outcome": "completed"})
        self.assertAlmostEqual(response["total_cost_usd"], 0.10)
        self.assertEqual(response["duration_api_ms"], 5000)

    def test_retry_with_missing_cost_in_main(self):
        """When main response has no cost field, retry cost becomes the total."""
        runner = self._make_runner()

        main_response = {
            "result": "summary text",
            "is_error": False,
        }
        retry_response = {
            "structured_output": {"outcome": "completed"},
            "total_cost_usd": 0.02,
            "duration_api_ms": 800,
        }

        with patch("codex_orchestrator.runner.subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout=json.dumps(main_response)),
                MagicMock(returncode=0, stdout=json.dumps(retry_response)),
            ]
            payload, response = runner._exec_json_with_response(
                "test prompt", schema={}, workdir=Path("/tmp"),
            )

        self.assertEqual(payload, {"outcome": "completed"})
        self.assertAlmostEqual(response["total_cost_usd"], 0.02)
        self.assertEqual(response["duration_api_ms"], 800)


if __name__ == "__main__":
    unittest.main()
