from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
from abc import ABC, abstractmethod
from pathlib import Path

from .config import BackendConfig, OrchestratorConfig, default_config
from .models import AgentRunResult, Bead, HandoffSummary, PlanChild, PlanProposal
from .prompts import build_planner_prompt, build_worker_prompt


NO_STRUCTURED_OUTPUT_SENTINEL = "claude -p produced no structured output"

_MARKDOWN_CODE_FENCE = re.compile(r'^\s*```(?:json)?\s*\n?(.*?)\n?\s*```\s*$', re.DOTALL)
_EMBEDDED_CODE_FENCE = re.compile(r'```(?:json)?\s*\n(.*?)\n?\s*```', re.DOTALL)
_EMBEDDED_JSON_OBJECT = re.compile(r'\{[\s\S]*\}')


def _strip_code_fence(text: str) -> str:
    """Strip a single markdown code fence (```json ... ```) if present."""
    m = _MARKDOWN_CODE_FENCE.match(text.strip())
    return m.group(1) if m else text


def _extract_json_from_text(text: str) -> dict | None:
    """Try multiple strategies to extract a JSON object from text.

    Strategies tried in order:
    1. Direct JSON parse of the full text.
    2. Strip outer code fence then parse.
    3. Find an embedded ```json ... ``` block then parse its contents.
    4. Find the outermost ``{...}`` substring and parse it.

    Returns the parsed dict on the first strategy that succeeds, or None.
    """
    text = text.strip()
    # Strategy 1: direct parse
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    # Strategy 2: strip outer code fence
    stripped = _strip_code_fence(text)
    if stripped != text:
        try:
            obj = json.loads(stripped)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
    # Strategy 3: find embedded code fence
    for m in _EMBEDDED_CODE_FENCE.finditer(text):
        try:
            obj = json.loads(m.group(1))
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            continue
    # Strategy 4: find outermost {...} substring
    m = _EMBEDDED_JSON_OBJECT.search(text)
    if m:
        try:
            obj = json.loads(m.group(0))
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
    return None


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
        "design_decisions": {"type": "string", "default": ""},
        "test_coverage_notes": {"type": "string", "default": ""},
        "known_limitations": {"type": "string", "default": ""},
        "new_beads": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "title": {"type": "string"},
                    "agent_type": {"type": "string", "enum": ["planner", "developer", "tester", "documentation", "review", "recovery"]},
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
                "agent_type": {"type": "string", "enum": ["planner", "developer", "tester", "documentation", "review", "recovery"]},
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
    config: OrchestratorConfig
    backend: BackendConfig

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
        dep_handoffs: list[HandoffSummary] | None = None,
    ) -> AgentRunResult: ...

    @abstractmethod
    def propose_plan(self, spec_text: str) -> PlanProposal: ...


class CodexAgentRunner(AgentRunner):
    @property
    def backend_name(self) -> str:
        return "codex"

    def __init__(
        self,
        config: OrchestratorConfig | None = None,
        backend: BackendConfig | None = None,
    ) -> None:
        if config is None:
            config = default_config()
        self.config = config
        self.backend = backend if backend is not None else config.backend("codex")

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
            self.backend.binary,
            "exec",
            *self.backend.flags,
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
            env.pop("VIRTUAL_ENV", None)
            if execution_env:
                env.update(execution_env)
            timeout = self.backend.timeout_seconds
            try:
                proc = subprocess.run(cmd, input=prompt, text=True, capture_output=True, check=False, env=env, timeout=timeout)
            except subprocess.TimeoutExpired:
                raise RuntimeError(f"Agent timed out after {timeout} seconds")
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
        dep_handoffs: list[HandoffSummary] | None = None,
    ) -> AgentRunResult:
        prompt = build_worker_prompt(bead, context_paths, workdir, dep_handoffs=dep_handoffs)
        prompt_chars = len(prompt)
        prompt_lines = prompt.count("\n") + 1

        start = time.monotonic()
        payload = self._exec_json(
            prompt,
            schema=AGENT_OUTPUT_SCHEMA,
            workdir=workdir,
            execution_env=execution_env,
        )
        duration_ms = int((time.monotonic() - start) * 1000)

        result = AgentRunResult(**payload)
        result.telemetry = {
            "duration_ms": duration_ms,
            "prompt_chars": prompt_chars,
            "prompt_lines": prompt_lines,
            "source": "measured",
            "prompt_text": prompt,
            "response_text": json.dumps(payload),
        }
        return result

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


def _add_numeric(target: dict, source: dict, key: str) -> None:
    """Add *source[key]* into *target[key]*, treating None/missing as 0."""
    src_val = source.get(key)
    if src_val is None:
        return
    tgt_val = target.get(key)
    target[key] = (tgt_val or 0) + src_val


class ClaudeCodeAgentRunner(AgentRunner):
    @property
    def backend_name(self) -> str:
        return "claude"

    def __init__(
        self,
        config: OrchestratorConfig | None = None,
        backend: BackendConfig | None = None,
    ) -> None:
        if config is None:
            config = default_config()
        self.config = config
        self.backend = backend if backend is not None else config.backend("claude")

    def _exec_json(
        self,
        prompt: str,
        *,
        schema: dict,
        workdir: Path,
        execution_env: dict[str, str] | None = None,
        agent_type: str | None = None,
    ) -> dict:
        payload, _response = self._exec_json_with_response(
            prompt, schema=schema, workdir=workdir,
            execution_env=execution_env, agent_type=agent_type,
        )
        return payload

    def _exec_json_with_response(
        self,
        prompt: str,
        *,
        schema: dict,
        workdir: Path,
        execution_env: dict[str, str] | None = None,
        agent_type: str | None = None,
        model: str | None = ...,
    ) -> tuple[dict, dict]:
        """Run claude -p and return (structured_payload, raw_response_dict).

        When *model* is explicitly passed (including None), it takes precedence
        over the config-based resolution.  The default sentinel ``...`` means
        "resolve from config".
        """
        # Recovery agents must not call tools — they only read supplied context and emit JSON.
        if agent_type == "recovery":
            tools = []
        else:
            tools = self.config.allowed_tools_for("claude", agent_type or "developer")
        if model is ...:
            model = self.config.model_for("claude", agent_type or "developer")
        cmd = [
            self.backend.binary,
            "-p",
            *self.backend.flags,
            "--allowedTools", ",".join(tools),
            "--output-format", "json",
            "--json-schema", json.dumps(schema),
        ]
        if model is not None:
            cmd.extend(["--model", model])
        env = os.environ.copy()
        env.pop("VIRTUAL_ENV", None)
        if execution_env:
            env.update(execution_env)
        timeout = self.backend.timeout_seconds
        try:
            proc = subprocess.run(
                cmd,
                input=prompt,
                text=True,
                capture_output=True,
                check=False,
                cwd=workdir,
                env=env,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"Agent timed out after {timeout} seconds")
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "claude -p failed")
        try:
            response = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"claude -p returned non-JSON output: {exc}") from exc
        # Claude Code --output-format json puts schema-validated data in "structured_output"
        structured = response.get("structured_output")
        if structured is not None:
            return structured, response
        # Fallback: try parsing "result" as JSON (e.g. when schema enforcement is skipped)
        result_text = response.get("result", "")
        if result_text:
            extracted = _extract_json_from_text(result_text)
            if extracted is not None:
                return extracted, response
        # Schema enforcement can fail on long agentic runs where the agent produces
        # a conversational summary instead of structured output.  Make a lightweight
        # follow-up call (no tools, single turn) to reformat the result.
        if result_text and not response.get("is_error"):
            retry_result, retry_response = self._retry_structured_output(
                result_text, schema=schema, workdir=workdir, execution_env=execution_env,
                agent_type=agent_type, model=model,
            )
            if retry_result is not None:
                # Merge retry cost/duration into the main response so telemetry
                # reflects the total spend while keeping the main run's turn
                # count, token usage, and session_id.
                if retry_response is not None:
                    _add_numeric(response, retry_response, "total_cost_usd")
                    _add_numeric(response, retry_response, "duration_api_ms")
                return retry_result, response
        raise RuntimeError(
            f"{NO_STRUCTURED_OUTPUT_SENTINEL}. "
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
        agent_type: str | None = None,
        model: str | None = ...,
    ) -> tuple[dict | None, dict | None]:
        """Single-turn, no-tool retry to convert a conversational result to JSON.

        Returns (structured_payload, retry_response_envelope).  Both are None
        when the retry fails.

        When *model* is explicitly passed (including None), it takes precedence
        over the config-based resolution.  The default sentinel ``...`` means
        "resolve from config".
        """
        required_fields = schema.get("required", list(schema.get("properties", {}).keys()))
        fields_hint = (
            f"\nRequired JSON fields: {', '.join(required_fields)}" if required_fields else ""
        )
        retry_prompt = (
            "The agent run below completed successfully but returned a conversational "
            "summary instead of the required JSON schema.  Convert the agent's result "
            "into a JSON object that matches the schema.  Do not perform any tool calls "
            f"or additional work — just reformat the information.{fields_hint}\n\n"
            f"Agent result:\n{agent_result_text}\n"
        )
        if model is ...:
            model = self.config.model_for("claude", agent_type or "developer")
        # The retry is a pure text-reformatting step: no tools, single turn.
        # Pass --allowedTools "" (empty string) to disable all tools so Claude
        # cannot invoke tools and is forced to produce the JSON directly.
        cmd = [
            self.backend.binary,
            "-p",
            "--allowedTools", "",
            "--output-format", "json",
            "--json-schema", json.dumps(schema),
            "--max-turns", "1",
        ]
        if model is not None:
            cmd.extend(["--model", model])
        env = os.environ.copy()
        env.pop("VIRTUAL_ENV", None)
        if execution_env:
            env.update(execution_env)
        retry_timeout = self.backend.retry_timeout_seconds
        try:
            proc = subprocess.run(
                cmd, input=retry_prompt, text=True, capture_output=True,
                check=False, cwd=workdir, env=env, timeout=retry_timeout,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"Agent retry timed out after {retry_timeout} seconds")
        if proc.returncode != 0:
            return None, None
        try:
            response = json.loads(proc.stdout)
        except json.JSONDecodeError:
            return None, None
        structured = response.get("structured_output")
        if structured is not None:
            return structured, response
        result_text = response.get("result", "")
        if result_text:
            extracted = _extract_json_from_text(result_text)
            if extracted is not None:
                return extracted, response
        return None, None

    def run_bead(
        self,
        bead: Bead,
        *,
        workdir: Path,
        context_paths: list[Path],
        execution_env: dict[str, str] | None = None,
        dep_handoffs: list[HandoffSummary] | None = None,
    ) -> AgentRunResult:
        prompt = build_worker_prompt(bead, context_paths, workdir, dep_handoffs=dep_handoffs)
        prompt_chars = len(prompt)
        prompt_lines = prompt.count("\n") + 1

        # Resolution order: bead metadata override > config per-agent > config default > none
        bead_model = bead.metadata.get("model_override") if bead.metadata else None
        model_kwarg: dict = {"model": bead_model} if bead_model else {}

        start = time.monotonic()
        payload, response = self._exec_json_with_response(
            prompt,
            schema=AGENT_OUTPUT_SCHEMA,
            workdir=workdir,
            execution_env=execution_env,
            agent_type=bead.agent_type,
            **model_kwarg,
        )
        duration_ms = int((time.monotonic() - start) * 1000)

        usage = response.get("usage", {})
        result = AgentRunResult(**payload)
        result.telemetry = {
            "cost_usd": response.get("total_cost_usd"),
            "duration_ms": duration_ms,
            "duration_api_ms": response.get("duration_api_ms"),
            "num_turns": response.get("num_turns"),
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "cache_creation_tokens": usage.get("cache_creation_input_tokens"),
            "cache_read_tokens": usage.get("cache_read_input_tokens"),
            "stop_reason": response.get("stop_reason"),
            "session_id": response.get("session_id"),
            "permission_denials": response.get("permission_denials"),
            "prompt_chars": prompt_chars,
            "prompt_lines": prompt_lines,
            "source": "provider",
            "prompt_text": prompt,
            "response_text": json.dumps(response),
        }
        return result

    def propose_plan(self, spec_text: str) -> PlanProposal:
        payload = self._exec_json(
            build_planner_prompt(spec_text),
            schema=PLANNER_OUTPUT_SCHEMA,
            workdir=Path.cwd(),
            agent_type="planner",
        )
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
