from __future__ import annotations

import json
import unittest

from agent_takt.runner import _claude_failure_message

WARN = "Warning: claude.ai MCP servers blocked by enterprise policy: claude.ai Claude Docs"


class ClaudeFailureMessageTests(unittest.TestCase):
    def test_benign_warning_dropped_uses_json_result(self) -> None:
        stdout = json.dumps({"is_error": True, "result": "Credit balance too low"})
        msg = _claude_failure_message(1, WARN + "\n", stdout)
        self.assertIn("Credit balance too low", msg)
        self.assertNotIn("MCP servers blocked", msg)
        self.assertIn("exit code 1", msg)

    def test_non_json_stdout_used(self) -> None:
        msg = _claude_failure_message(2, WARN, "boom happened\n")
        self.assertEqual(msg, "claude -p failed (exit code 2): boom happened")

    def test_generic_fallback(self) -> None:
        self.assertEqual(_claude_failure_message(3, WARN, ""), "claude -p failed (exit code 3)")

    def test_real_stderr_kept_after_warning(self) -> None:
        msg = _claude_failure_message(1, WARN + "\nreal error\n", "{}")
        self.assertIn("real error", msg)
        self.assertNotIn("MCP servers blocked", msg)


if __name__ == "__main__":
    unittest.main()
