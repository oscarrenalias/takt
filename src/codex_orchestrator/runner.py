from __future__ import annotations

import json
import os
import subprocess
import tempfile
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
    "required": ["outcome", "summary", "completed", "remaining", "risks", "expected_files", "expected_globs", "touched_files", "changed_files", "updated_docs", "next_action", "next_agent", "block_reason", "conflict_risks", "new_beads"],
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


class AgentRunner:
    def run_bead(
        self,
        bead: Bead,
        *,
        workdir: Path,
        context_paths: list[Path],
        execution_env: dict[str, str] | None = None,
    ) -> AgentRunResult:
        raise NotImplementedError

    def propose_plan(self, spec_text: str) -> PlanProposal:
        raise NotImplementedError


class CodexAgentRunner(AgentRunner):
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
