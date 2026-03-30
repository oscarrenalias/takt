from __future__ import annotations

import json
import os
import subprocess
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path

from .models import AgentRunResult, Bead, PlanChild, PlanProposal
from .prompts import build_planner_prompt, build_worker_prompt


AGENT_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "outcome": {"type": "string", "enum": ["completed", "blocked", "failed"]},
        "summary": {"type": "string"},
        "completed": {"type": "string"},
        "remaining": {"type": "string"},
        "risks": {"type": "string"},
        "verdict": {"type": "string", "enum": ["approved", "needs_changes"]},
        "findings_count": {"type": "integer", "minimum": 0},
        "requires_followup": {"type": "boolean"},
        "expected_files": {"type": "array", "items": {"type": "string"}},
        "expected_globs": {"type": "array", "items": {"type": "string"}},
        "touched_files": {"type": "array", "items": {"type": "string"}},
        "changed_files": {"type": "array", "items": {"type": "string"}},
        "updated_docs": {"type": "array", "items": {"type": "string"}},
        "next_action": {"type": "string"},
        "next_agent": {"type": "string"},
        "block_reason": {"type": "string"},
        "conflict_risks": {"type": "string"},
        "new_beads": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "title": {"type": "string"},
                    "agent_type": {"type": "string"},
                    "description": {"type": "string"},
                    "acceptance_criteria": {"type": "array", "items": {"type": "string"}},
                    "dependencies": {"type": "array", "items": {"type": "string"}},
                    "linked_docs": {"type": "array", "items": {"type": "string"}},
                    "expected_files": {"type": "array", "items": {"type": "string"}},
                    "expected_globs": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["title", "agent_type", "description", "acceptance_criteria", "dependencies", "linked_docs", "expected_files", "expected_globs"],
            },
        },
    },
    "required": [
        "outcome",
        "summary",
        "completed",
        "remaining",
        "risks",
        "verdict",
        "findings_count",
        "requires_followup",
        "expected_files",
        "expected_globs",
        "touched_files",
        "changed_files",
        "updated_docs",
        "next_action",
        "next_agent",
        "block_reason",
        "conflict_risks",
        "new_beads",
    ],
}

PLANNER_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "epic_title": {"type": "string"},
        "epic_description": {"type": "string"},
        "linked_docs": {"type": "array", "items": {"type": "string"}},
        "feature": {"$ref": "#/$defs/plan_child"},
    },
    "$defs": {
        "plan_child": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "title": {"type": "string"},
                "agent_type": {"type": "string"},
                "description": {"type": "string"},
                "acceptance_criteria": {"type": "array", "items": {"type": "string"}},
                "dependencies": {"type": "array", "items": {"type": "string"}},
                "linked_docs": {"type": "array", "items": {"type": "string"}},
                "expected_files": {"type": "array", "items": {"type": "string"}},
                "expected_globs": {"type": "array", "items": {"type": "string"}},
                "children": {"type": "array", "items": {"$ref": "#/$defs/plan_child"}},
            },
            "required": ["title", "agent_type", "description", "acceptance_criteria", "dependencies", "linked_docs", "expected_files", "expected_globs", "children"],
        }
    },
    "required": ["epic_title", "epic_description", "linked_docs", "feature"],
}


class AgentRunner(ABC):
    @property
    @abstractmethod
    def backend_name(self) -> str: ...

    @abstractmethod
    def run_bead(
        self,
        bead: Bead,
        *,
        workdir: Path,
        context_paths: list[Path],
        execution_env: dict[str, str] | None = None,
    ) -> AgentRunResult: ...

    @abstractmethod
    def propose_plan(self, spec_text: str) -> PlanProposal: ...


class CodexAgentRunner(AgentRunner):
    @property
    def backend_name(self) -> str:
        return "codex"

    def __init__(self, codex_bin: str = "codex") -> None:
        self.codex_bin = codex_bin

    def _exec_json(
        self,
        prompt: str,
        *,
        schema: dict,
        workdir: Path,
        execution_env: dict[str, str] | None = None,
    ) -> dict:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as schema_file:
            json.dump(schema, schema_file)
            schema_path = Path(schema_file.name)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as output_file:
            output_path = Path(output_file.name)
        cmd = [
            self.codex_bin,
            "exec",
            "--skip-git-repo-check",
            "--full-auto",
            "--color",
            "never",
            "--output-schema",
            str(schema_path),
            "--output-last-message",
            str(output_path),
            "-C",
            str(workdir),
            "-",
        ]
        try:
            env = os.environ.copy()
            if execution_env:
                env.update(execution_env)
            proc = subprocess.run(cmd, input=prompt, text=True, capture_output=True, check=False, env=env)
            if proc.returncode != 0:
                raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "codex exec failed")
            return json.loads(output_path.read_text(encoding="utf-8"))
        finally:
            schema_path.unlink(missing_ok=True)
            output_path.unlink(missing_ok=True)

    def run_bead(
        self,
        bead: Bead,
        *,
        workdir: Path,
        context_paths: list[Path],
        execution_env: dict[str, str] | None = None,
    ) -> AgentRunResult:
        payload = self._exec_json(
            build_worker_prompt(bead, context_paths, workdir),
            schema=AGENT_OUTPUT_SCHEMA,
            workdir=workdir,
            execution_env=execution_env,
        )
        return AgentRunResult(**payload)

    def propose_plan(self, spec_text: str) -> PlanProposal:
        payload = self._exec_json(build_planner_prompt(spec_text), schema=PLANNER_OUTPUT_SCHEMA, workdir=Path.cwd())
        return PlanProposal(
            epic_title=payload["epic_title"],
            epic_description=payload["epic_description"],
            linked_docs=payload["linked_docs"],
            feature=self._parse_plan_child(payload["feature"]),
        )

    def _parse_plan_child(self, payload: dict) -> PlanChild:
        child_data = dict(payload)
        child_data["children"] = [self._parse_plan_child(item) for item in payload.get("children", [])]
        return PlanChild(**child_data)


class ClaudeCodeAgentRunner(AgentRunner):
    @property
    def backend_name(self) -> str:
        return "claude"

    def __init__(self, claude_bin: str = "claude") -> None:
        self.claude_bin = claude_bin

    def _exec_json(
        self,
        prompt: str,
        *,
        schema: dict,
        workdir: Path,
        execution_env: dict[str, str] | None = None,
    ) -> dict:
        cmd = [
            self.claude_bin,
            "-p",
            "--dangerously-skip-permissions",
            "--allowedTools", "Edit,Write,Read,Bash,Glob,Grep,Skill,ToolSearch,Agent,WebSearch,WebFetch,NotebookEdit,TaskCreate,TaskUpdate,TaskGet,TaskList",
            "--output-format", "json",
            "--json-schema", json.dumps(schema),
        ]
        env = os.environ.copy()
        if execution_env:
            env.update(execution_env)
        proc = subprocess.run(
            cmd,
            input=prompt,
            text=True,
            capture_output=True,
            check=False,
            cwd=workdir,
            env=env,
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "claude -p failed")
        try:
            response = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"claude -p returned non-JSON output: {exc}") from exc
        # Claude Code --output-format json puts schema-validated data in "structured_output"
        structured = response.get("structured_output")
        if structured is not None:
            return structured
        # Fallback: try parsing "result" as JSON (e.g. when schema enforcement is skipped)
        result_text = response.get("result", "")
        if result_text:
            try:
                return json.loads(result_text)
            except json.JSONDecodeError:
                pass
        # Schema enforcement can fail on long agentic runs where the agent produces
        # a conversational summary instead of structured output.  Make a lightweight
        # follow-up call (no tools, single turn) to reformat the result.
        if result_text and not response.get("is_error"):
            retry_result = self._retry_structured_output(
                result_text, schema=schema, workdir=workdir, execution_env=execution_env,
            )
            if retry_result is not None:
                return retry_result
        raise RuntimeError(
            f"claude -p produced no structured output. "
            f"is_error={response.get('is_error')}, "
            f"stop_reason={response.get('stop_reason')}, "
            f"result={result_text[:200]!r}"
        )

    def _retry_structured_output(
        self,
        agent_result_text: str,
        *,
        schema: dict,
        workdir: Path,
        execution_env: dict[str, str] | None = None,
    ) -> dict | None:
        """Single-turn, no-tool retry to convert a conversational result to JSON."""
        retry_prompt = (
            "The agent run below completed successfully but returned a conversational "
            "summary instead of the required JSON schema.  Convert the agent's result "
            "into a JSON object that matches the schema.  Do not perform any tool calls "
            "or additional work — just reformat the information.\n\n"
            f"Agent result:\n{agent_result_text}\n"
        )
        cmd = [
            self.claude_bin,
            "-p",
            "--dangerously-skip-permissions",
            "--allowedTools", "Edit,Write,Read,Bash,Glob,Grep,Skill,ToolSearch,Agent,WebSearch,WebFetch,NotebookEdit,TaskCreate,TaskUpdate,TaskGet,TaskList",
            "--output-format", "json",
            "--json-schema", json.dumps(schema),
            "--max-turns", "1",
        ]
        env = os.environ.copy()
        if execution_env:
            env.update(execution_env)
        proc = subprocess.run(
            cmd, input=retry_prompt, text=True, capture_output=True,
            check=False, cwd=workdir, env=env,
        )
        if proc.returncode != 0:
            return None
        try:
            response = json.loads(proc.stdout)
        except json.JSONDecodeError:
            return None
        structured = response.get("structured_output")
        if structured is not None:
            return structured
        result_text = response.get("result", "")
        if result_text:
            try:
                return json.loads(result_text)
            except json.JSONDecodeError:
                pass
        return None

    def run_bead(
        self,
        bead: Bead,
        *,
        workdir: Path,
        context_paths: list[Path],
        execution_env: dict[str, str] | None = None,
    ) -> AgentRunResult:
        payload = self._exec_json(
            build_worker_prompt(bead, context_paths, workdir),
            schema=AGENT_OUTPUT_SCHEMA,
            workdir=workdir,
            execution_env=execution_env,
        )
        return AgentRunResult(**payload)

    def propose_plan(self, spec_text: str) -> PlanProposal:
        payload = self._exec_json(build_planner_prompt(spec_text), schema=PLANNER_OUTPUT_SCHEMA, workdir=Path.cwd())
        return PlanProposal(
            epic_title=payload["epic_title"],
            epic_description=payload["epic_description"],
            linked_docs=payload["linked_docs"],
            feature=self._parse_plan_child(payload["feature"]),
        )

    def _parse_plan_child(self, payload: dict) -> PlanChild:
        child_data = dict(payload)
        child_data["children"] = [self._parse_plan_child(item) for item in payload.get("children", [])]
        return PlanChild(**child_data)
