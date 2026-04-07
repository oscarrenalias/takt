"""Tests for telemetry: runner telemetry population and storage artifact writing.

Covers:
- AgentRunResult.telemetry defaults to None
- CodexAgentRunner.run_bead attaches measured telemetry (source, duration_ms, prompt_chars/lines)
- ClaudeCodeAgentRunner.run_bead extracts provider telemetry fields from response envelope
- prompt_chars and prompt_lines match actual prompt content
- RepositoryStorage.initialize() creates .takt/telemetry/
- RepositoryStorage.telemetry_dir attribute
- write_telemetry_artifact: creates file, correct content, atomic write, multiple attempts,
  returns Path, failed attempt fields, auto-creates subdirectory
- .gitignore includes .takt/telemetry/ entry
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_takt.models import AgentRunResult, BEAD_IN_PROGRESS
from agent_takt.storage import RepositoryStorage

RepositoryStorage._auto_commit = False

from helpers import OrchestratorTests as _OrchestratorBase  # noqa: E402


class TestRunnerTelemetry(_OrchestratorBase):
    """Tests for telemetry fields populated by agent runners."""

    def test_agent_run_result_telemetry_defaults_to_none(self) -> None:
        result = AgentRunResult(outcome="completed", summary="done")
        self.assertIsNone(result.telemetry)

    def test_codex_runner_populates_minimal_telemetry(self) -> None:
        """CodexAgentRunner.run_bead attaches measured telemetry fields."""
        from agent_takt.runner import CodexAgentRunner

        bead = self.storage.create_bead(title="Telemetry codex", agent_type="developer", description="test")
        bead.status = BEAD_IN_PROGRESS

        fake_payload = {
            "outcome": "completed",
            "summary": "done",
            "completed": "",
            "remaining": "",
            "risks": "",
            "verdict": "approved",
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
            "new_beads": [],
        }

        runner = CodexAgentRunner()
        with patch.object(runner, "_exec_json", return_value=fake_payload):
            result = runner.run_bead(bead, workdir=self.root, context_paths=[])

        self.assertIsNotNone(result.telemetry)
        self.assertEqual(result.telemetry["source"], "measured")
        self.assertIn("duration_ms", result.telemetry)
        self.assertIsInstance(result.telemetry["duration_ms"], int)
        self.assertGreaterEqual(result.telemetry["duration_ms"], 0)
        self.assertIn("prompt_chars", result.telemetry)
        self.assertIsInstance(result.telemetry["prompt_chars"], int)
        self.assertGreater(result.telemetry["prompt_chars"], 0)
        self.assertIn("prompt_lines", result.telemetry)
        self.assertIsInstance(result.telemetry["prompt_lines"], int)
        self.assertGreater(result.telemetry["prompt_lines"], 0)
        self.assertIn("prompt_text", result.telemetry)
        self.assertIn("response_text", result.telemetry)

    def test_claude_runner_populates_provider_telemetry(self) -> None:
        """ClaudeCodeAgentRunner.run_bead extracts all provider fields from response envelope."""
        from agent_takt.runner import ClaudeCodeAgentRunner

        bead = self.storage.create_bead(title="Telemetry claude", agent_type="developer", description="test")
        bead.status = BEAD_IN_PROGRESS

        fake_payload = {
            "outcome": "completed",
            "summary": "done",
            "completed": "",
            "remaining": "",
            "risks": "",
            "verdict": "approved",
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
            "new_beads": [],
        }
        fake_response = {
            "structured_output": fake_payload,
            "total_cost_usd": 0.42,
            "duration_api_ms": 12345,
            "num_turns": 3,
            "usage": {
                "input_tokens": 1000,
                "output_tokens": 500,
                "cache_creation_input_tokens": 200,
                "cache_read_input_tokens": 100,
            },
            "stop_reason": "end_turn",
            "session_id": "sess-abc123",
            "permission_denials": 0,
        }

        runner = ClaudeCodeAgentRunner()
        with patch.object(
            runner, "_exec_json_with_response",
            return_value=(fake_payload, fake_response),
        ):
            result = runner.run_bead(bead, workdir=self.root, context_paths=[])

        self.assertIsNotNone(result.telemetry)
        t = result.telemetry
        self.assertEqual(t["source"], "provider")
        self.assertEqual(t["cost_usd"], 0.42)
        self.assertEqual(t["duration_api_ms"], 12345)
        self.assertEqual(t["num_turns"], 3)
        self.assertEqual(t["input_tokens"], 1000)
        self.assertEqual(t["output_tokens"], 500)
        self.assertEqual(t["cache_creation_tokens"], 200)
        self.assertEqual(t["cache_read_tokens"], 100)
        self.assertEqual(t["stop_reason"], "end_turn")
        self.assertEqual(t["session_id"], "sess-abc123")
        self.assertEqual(t["permission_denials"], 0)
        # Also has measured fields
        self.assertIn("duration_ms", t)
        self.assertIsInstance(t["duration_ms"], int)
        self.assertGreaterEqual(t["duration_ms"], 0)
        self.assertIn("prompt_chars", t)
        self.assertIn("prompt_lines", t)
        self.assertIn("prompt_text", t)
        self.assertIn("response_text", t)

    def test_codex_telemetry_prompt_chars_and_lines_match_actual_prompt(self) -> None:
        """Verify prompt_chars and prompt_lines reflect the actual prompt content."""
        from agent_takt.runner import CodexAgentRunner

        bead = self.storage.create_bead(title="Telemetry prompt", agent_type="developer", description="test")
        bead.status = BEAD_IN_PROGRESS

        fake_payload = {
            "outcome": "completed",
            "summary": "done",
            "completed": "",
            "remaining": "",
            "risks": "",
            "verdict": "approved",
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
            "new_beads": [],
        }

        captured_prompts: list[str] = []

        def mock_exec_json(prompt, *, schema, workdir, execution_env=None):
            captured_prompts.append(prompt)
            return fake_payload

        runner = CodexAgentRunner()
        with patch.object(runner, "_exec_json", side_effect=mock_exec_json):
            result = runner.run_bead(bead, workdir=self.root, context_paths=[])

        self.assertEqual(len(captured_prompts), 1)
        actual_prompt = captured_prompts[0]
        self.assertEqual(result.telemetry["prompt_chars"], len(actual_prompt))
        self.assertEqual(result.telemetry["prompt_lines"], actual_prompt.count("\n") + 1)


class TestTelemetryArtifactStorage(_OrchestratorBase):
    """Tests for telemetry artifact storage (B0118)."""

    def test_initialize_creates_telemetry_dir(self) -> None:
        """RepositoryStorage.initialize() creates .takt/telemetry/."""
        fresh_root = Path(tempfile.mkdtemp())
        try:
            storage = RepositoryStorage(fresh_root)
            telemetry_dir = fresh_root / ".takt" / "telemetry"
            self.assertFalse(telemetry_dir.exists())
            storage.initialize()
            self.assertTrue(telemetry_dir.is_dir())
        finally:
            shutil.rmtree(fresh_root)

    def test_telemetry_dir_attribute(self) -> None:
        """RepositoryStorage.telemetry_dir points to .takt/telemetry."""
        storage = RepositoryStorage(self.root)
        self.assertEqual(storage.telemetry_dir, self.root.resolve() / ".takt" / "telemetry")

    def test_write_telemetry_artifact_creates_file(self) -> None:
        """write_telemetry_artifact writes a JSON file at the expected path."""
        path = self.storage.write_telemetry_artifact(
            bead_id="B9999",
            agent_type="developer",
            attempt=1,
            started_at="2026-03-30T10:00:00+00:00",
            finished_at="2026-03-30T10:05:00+00:00",
            outcome="completed",
            prompt_text="prompt here",
            response_text='{"result": "ok"}',
            parsed_result={"outcome": "completed"},
            metrics={"duration_ms": 300000, "source": "measured"},
            error=None,
        )
        self.assertTrue(path.exists())
        self.assertEqual(path, self.storage.telemetry_dir / "B9999" / "1.json")

    def test_write_telemetry_artifact_content(self) -> None:
        """Artifact file contains all required fields from the spec."""
        self.storage.write_telemetry_artifact(
            bead_id="B8888",
            agent_type="tester",
            attempt=2,
            started_at="2026-03-30T10:00:00+00:00",
            finished_at="2026-03-30T10:01:00+00:00",
            outcome="blocked",
            prompt_text="test prompt",
            response_text=None,
            parsed_result=None,
            metrics={"duration_ms": 60000},
            error={"stage": "parse", "message": "bad JSON"},
        )
        artifact_path = self.storage.telemetry_dir / "B8888" / "2.json"
        data = json.loads(artifact_path.read_text(encoding="utf-8"))
        self.assertEqual(data["telemetry_version"], 1)
        self.assertEqual(data["bead_id"], "B8888")
        self.assertEqual(data["agent_type"], "tester")
        self.assertEqual(data["attempt"], 2)
        self.assertEqual(data["started_at"], "2026-03-30T10:00:00+00:00")
        self.assertEqual(data["finished_at"], "2026-03-30T10:01:00+00:00")
        self.assertEqual(data["outcome"], "blocked")
        self.assertEqual(data["prompt_text"], "test prompt")
        self.assertIsNone(data["response_text"])
        self.assertIsNone(data["parsed_result"])
        self.assertEqual(data["metrics"], {"duration_ms": 60000})
        self.assertEqual(data["error"], {"stage": "parse", "message": "bad JSON"})

    def test_write_telemetry_artifact_atomic_write(self) -> None:
        """Artifact is written atomically — no .tmp file left behind."""
        self.storage.write_telemetry_artifact(
            bead_id="B7777",
            agent_type="developer",
            attempt=1,
            started_at="t0",
            finished_at="t1",
            outcome="completed",
            prompt_text="p",
            response_text="r",
            parsed_result={},
            metrics={},
            error=None,
        )
        bead_dir = self.storage.telemetry_dir / "B7777"
        tmp_files = list(bead_dir.glob("*.tmp"))
        self.assertEqual(tmp_files, [])

    def test_write_telemetry_artifact_multiple_attempts(self) -> None:
        """Multiple attempts for the same bead create separate numbered files."""
        for attempt in (1, 2, 3):
            self.storage.write_telemetry_artifact(
                bead_id="B6666",
                agent_type="developer",
                attempt=attempt,
                started_at="t0",
                finished_at="t1",
                outcome="completed",
                prompt_text=f"prompt {attempt}",
                response_text=f"response {attempt}",
                parsed_result={"attempt": attempt},
                metrics={"attempt": attempt},
                error=None,
            )
        bead_dir = self.storage.telemetry_dir / "B6666"
        self.assertTrue((bead_dir / "1.json").exists())
        self.assertTrue((bead_dir / "2.json").exists())
        self.assertTrue((bead_dir / "3.json").exists())
        data3 = json.loads((bead_dir / "3.json").read_text())
        self.assertEqual(data3["prompt_text"], "prompt 3")

    def test_write_telemetry_artifact_returns_path(self) -> None:
        """write_telemetry_artifact returns the Path to the written file."""
        result = self.storage.write_telemetry_artifact(
            bead_id="B5555",
            agent_type="review",
            attempt=1,
            started_at="t0",
            finished_at="t1",
            outcome="completed",
            prompt_text="p",
            response_text="r",
            parsed_result={},
            metrics={},
            error=None,
        )
        self.assertIsInstance(result, Path)
        self.assertTrue(result.exists())

    def test_write_telemetry_artifact_failed_attempt(self) -> None:
        """Failed attempt artifacts have null response_text/parsed_result and populated error."""
        self.storage.write_telemetry_artifact(
            bead_id="B4444",
            agent_type="developer",
            attempt=1,
            started_at="2026-03-30T10:00:00+00:00",
            finished_at="2026-03-30T10:02:00+00:00",
            outcome="blocked",
            prompt_text="run the task",
            response_text=None,
            parsed_result=None,
            metrics={"duration_ms": 120000, "source": "measured"},
            error={"stage": "execution", "message": "process exited with code 1"},
        )
        artifact_path = self.storage.telemetry_dir / "B4444" / "1.json"
        data = json.loads(artifact_path.read_text(encoding="utf-8"))
        self.assertIsNone(data["response_text"])
        self.assertIsNone(data["parsed_result"])
        self.assertIsNotNone(data["error"])
        self.assertEqual(data["error"]["stage"], "execution")
        self.assertEqual(data["error"]["message"], "process exited with code 1")
        self.assertEqual(data["prompt_text"], "run the task")
        self.assertEqual(data["outcome"], "blocked")

    def test_write_telemetry_artifact_creates_directories(self) -> None:
        """write_telemetry_artifact auto-creates bead subdirectory under telemetry/."""
        fresh_root = Path(tempfile.mkdtemp())
        try:
            storage = RepositoryStorage(fresh_root)
            storage.initialize()
            bead_dir = storage.telemetry_dir / "B3333"
            self.assertFalse(bead_dir.exists())
            storage.write_telemetry_artifact(
                bead_id="B3333",
                agent_type="tester",
                attempt=1,
                started_at="t0",
                finished_at="t1",
                outcome="completed",
                prompt_text="p",
                response_text="r",
                parsed_result={},
                metrics={},
                error=None,
            )
            self.assertTrue(bead_dir.is_dir())
            self.assertTrue((bead_dir / "1.json").exists())
        finally:
            shutil.rmtree(fresh_root)

    def test_gitignore_contains_telemetry_entry(self) -> None:
        """.gitignore includes .takt/telemetry/ to exclude heavy artifacts."""
        gitignore = (REPO_ROOT / ".gitignore").read_text()
        self.assertIn(".takt/telemetry/", gitignore)


if __name__ == "__main__":
    unittest.main()
