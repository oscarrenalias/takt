from __future__ import annotations

import json
from pathlib import Path

from ._assets import packaged_templates_dir
from .models import Bead, HandoffSummary

BUILT_IN_AGENT_TYPES = ("planner", "developer", "tester", "documentation", "review", "recovery", "investigator")
DEFAULT_TEMPLATES_DIR = packaged_templates_dir()
_EXECUTION_HISTORY_PROMPT_CAP = 5

# Inline copy of the agent output schema used for recovery prompts.
# Kept here to avoid a circular import with runner.py, which imports build_worker_prompt.
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
        "outcome", "summary", "completed", "remaining", "risks", "verdict",
        "findings_count", "requires_followup", "expected_files", "expected_globs",
        "touched_files", "changed_files", "updated_docs", "next_action", "next_agent",
        "block_reason", "conflict_risks", "new_beads",
    ],
}


def supported_agent_types(config_types: list[str] | None = None) -> tuple[str, ...]:
    return tuple(config_types) if config_types else BUILT_IN_AGENT_TYPES


def render_context_snippets(context_paths: list[Path], root: Path) -> str:
    if not context_paths:
        return "No linked repository documents were provided."
    rendered: list[str] = []
    for path in context_paths:
        try:
            label = str(path.relative_to(root))
        except ValueError:
            label = path.name
        rendered.append(f"- {label}")
    return "\n".join(rendered)


def render_agent_output_requirements(agent_type: str) -> str:
    if agent_type == "investigator":
        return (
            "Structured output requirements:\n"
            "- Set `outcome` to `completed` or `blocked`.\n"
            "- Populate `findings` with a detailed analysis of your codebase investigation.\n"
            "- Populate `recommendations` with prioritised action items derived from the findings.\n"
            "- Populate `risk_areas` with identified risks if the findings are left unaddressed.\n"
            "- Set `report_path` to the relative path of the written report file (e.g. `docs/investigator/<slug>.md`).\n"
            "- Do not include `verdict`, `changed_files`, or `next_agent` — these fields are not part of the investigator output schema.\n\n"
        )
    common_requirements = (
        "Structured output requirements:\n"
        "- always set `verdict` to `approved` or `needs_changes`.\n"
        "- Always set `findings_count`; use `0` when there are no unresolved findings in this pass.\n"
        "- Set `requires_followup` explicitly.\n"
    )
    if agent_type not in {"review", "tester"}:
        return (
            common_requirements
            + "- Use `approved` when this bead is complete without follow-up; use `needs_changes` when blocking or handing off unresolved work.\n\n"
        )
    return (
        common_requirements
        + "- For this agent type, set `findings_count` to the number of unresolved findings in this pass.\n"
        + "- Set `requires_followup` explicitly; use `false` for `approved` and `true` for `needs_changes` unless there is a documented exception.\n"
        + "- When `verdict` is `needs_changes`, include a concrete `block_reason` and hand off to the next agent when appropriate.\n"
        + "- Keep `completed`, `remaining`, and `risks` as concise narrative context only; they do not replace the structured verdict fields.\n\n"
    )

def guardrail_template_path(
    agent_type: str,
    *,
    root: Path | None = None,
    templates_dir: str | None = None,
    agent_types: list[str] | None = None,
) -> Path:
    allowed = supported_agent_types(agent_types)
    if agent_type not in allowed:
        raise ValueError(f"Unsupported agent type for worker prompt: {agent_type}")
    if root is None:
        resolved_dir = DEFAULT_TEMPLATES_DIR
    elif templates_dir is not None:
        resolved_dir = Path(root) / templates_dir
    else:
        resolved_dir = Path(root) / "templates" / "agents"
    return resolved_dir / f"{agent_type}.md"


def load_guardrail_template(
    agent_type: str,
    *,
    root: Path | None = None,
    templates_dir: str | None = None,
    agent_types: list[str] | None = None,
) -> tuple[Path, str]:
    path = guardrail_template_path(
        agent_type, root=root, templates_dir=templates_dir, agent_types=agent_types,
    )
    if not path.is_file():
        raise FileNotFoundError(
            f"Missing guardrail template for built-in agent '{agent_type}' at {path}. "
            "Add the matching templates/agents/<agent_type>.md file before running this worker."
        )
    return path, path.read_text(encoding="utf-8").strip()


def render_dep_handoff_context(agent_type: str, dep_handoffs: list[HandoffSummary]) -> str:
    """Render structured handoff fields from dependency results for review/tester prompts."""
    if agent_type not in {"review", "tester"}:
        return ""
    lines: list[str] = []
    if agent_type == "review":
        fields = [("design_decisions", "Design decisions")]
    else:
        fields = [
            ("test_coverage_notes", "Test coverage notes"),
            ("known_limitations", "Known limitations"),
        ]
    for attr, label in fields:
        values = [getattr(h, attr) for h in dep_handoffs if getattr(h, attr)]
        if values:
            lines.append(f"{label} from dependencies:")
            for val in values:
                lines.append(f"  {val}")
    if not lines:
        return ""
    return "Developer handoff context:\n" + "\n".join(lines) + "\n\n"


def build_worker_prompt(
    bead: Bead,
    context_paths: list[Path],
    root: Path,
    dep_handoffs: list[HandoffSummary] | None = None,
) -> str:
    guardrail_path, guardrail_text = load_guardrail_template(bead.agent_type, root=root)
    output_requirements = render_agent_output_requirements(bead.agent_type)
    dep_context = render_dep_handoff_context(bead.agent_type, dep_handoffs or [])
    payload = {
        "bead_id": bead.bead_id,
        "feature_root_id": bead.feature_root_id,
        "title": bead.title,
        "agent_type": bead.agent_type,
        "description": bead.description,
        "status": bead.status,
        "acceptance_criteria": bead.acceptance_criteria,
        "dependencies": bead.dependencies,
        "linked_docs": bead.linked_docs,
        "execution_branch_name": bead.execution_branch_name,
        "execution_worktree_path": bead.execution_worktree_path,
        "expected_files": bead.expected_files,
        "expected_globs": bead.expected_globs,
        "touched_files": bead.touched_files,
        "changed_files": bead.changed_files,
        "conflict_risks": bead.conflict_risks,
        "handoff_summary": bead.handoff_summary.__dict__,
        "execution_history": [
            {
                "timestamp": e.timestamp,
                "event": e.event,
                "agent_type": e.agent_type,
                "summary": e.summary,
                "details": e.details,
            }
            for e in bead.execution_history[-_EXECUTION_HISTORY_PROMPT_CAP:]
        ],
    }
    return (
        f"You are the {bead.agent_type} agent for a multi-agent orchestration system.\n"
        "Your role-specific guardrails come from a required local template. "
        "Follow them exactly. If the bead requires work outside that scope, return a blocked result with block_reason and next_agent.\n\n"
        + (
            "IMPORTANT: Do not run any test suite or test runner. "
            "Test execution is exclusively the tester agent's responsibility. "
            "At most, verify your changes do not break the build (e.g. syntax check or compile step). "
            "Do not run tests as a validation step.\n\n"
            if bead.agent_type == "developer" else ""
        )
        + "Agent guardrails:\n"
        f"Template: {guardrail_path}\n"
        f"{guardrail_text}\n\n"
        "Execution context:\n"
        "You are running inside a shared feature worktree. Sibling sub-beads may also run in this same feature tree, "
        "but only when dependencies and file-scope claims allow it. Stay within this bead's scope.\n\n"
        f"{output_requirements}"
        + dep_context
        + "Assigned bead:\n"
        f"{json.dumps(payload, indent=2)}\n\n"
        "Available repository context files:\n"
        f"{render_context_snippets(context_paths, root)}\n\n"
        "CRITICAL: Your final message MUST be a JSON object matching the required output schema. "
        "Do not end with a conversational summary or status update — the orchestrator parses your "
        "last message as JSON and will fail if it is not valid JSON. "
        "Always include a concise summary, structured handoff fields, actual touched files, "
        "updated scope if it changed, conflict risks for downstream agents, and any newly discovered sub-beads."
    )


def build_recovery_prompt(original_bead: Bead, prose_output: str, git_diff: str) -> str:
    """Build a prompt for a recovery agent to produce valid structured output for a failed bead.

    The recovery agent receives the original bead context, the prose output the prior agent
    produced (which could not be parsed), any git diff of changes already applied, and the
    required JSON output schema. Its sole job is to return a conforming JSON object.
    """
    schema_text = json.dumps(AGENT_OUTPUT_SCHEMA, indent=2)
    payload = {
        "bead_id": original_bead.bead_id,
        "feature_root_id": original_bead.feature_root_id,
        "title": original_bead.title,
        "agent_type": original_bead.agent_type,
        "description": original_bead.description,
        "acceptance_criteria": original_bead.acceptance_criteria,
        "dependencies": original_bead.dependencies,
        "expected_files": original_bead.expected_files,
        "expected_globs": original_bead.expected_globs,
        "touched_files": original_bead.touched_files,
        "changed_files": original_bead.changed_files,
    }
    return (
        "You are a recovery agent for a multi-agent orchestration system.\n"
        "A previous agent completed work on the bead below but its final message could not be "
        "parsed as valid structured JSON. Your sole task is to inspect the prior agent's prose "
        "output and the git diff of changes already applied, then emit a single valid JSON object "
        "conforming exactly to the required output schema.\n\n"
        "## Bead context\n\n"
        f"**Title**: {original_bead.title}\n\n"
        f"**Description**: {original_bead.description}\n\n"
        f"**Bead JSON**:\n```json\n{json.dumps(payload, indent=2)}\n```\n\n"
        "## Prior agent prose output\n\n"
        f"{prose_output}\n\n"
        "## Git diff of changes applied\n\n"
        f"```diff\n{git_diff}\n```\n\n"
        "## Required output schema\n\n"
        f"```json\n{schema_text}\n```\n\n"
        "CRITICAL: Your response MUST be a single valid JSON object and nothing else. "
        "Do not include any explanation, markdown formatting, or text outside the JSON object. "
        "Your entire response must be directly parseable as JSON matching the schema above. "
        "Populate `touched_files` and `changed_files` from the git diff above. "
        "Set `outcome` to `completed`, `verdict` to `approved`, and `requires_followup` to `false` "
        "unless the prior agent's prose output clearly indicates otherwise."
    )


def build_planner_prompt(spec_text: str) -> str:
    return (
        "Read the feature specification below and propose an orchestration plan. "
        "Return JSON with keys epic_title, epic_description, linked_docs, and feature. "
        "The feature value must be one top-level non-runnable feature container bead representing the shared execution root for this spec. "
        "Concrete implementation, testing, documentation, and review work must be expressed under feature.children rather than as sibling top-level beads. "
        "Keep developer implementation beads as small as practical: each developer bead should cover one focused change and fit within roughly 10 minutes of implementation work. "
        "Split broader logical units into smaller dependent developer beads instead of assigning one bead to absorb multiple distinct changes. "
        "If a change is likely to touch more than 2-3 functions, span multiple subsystems, or mix unrelated refactors with feature work, break it into smaller dependent beads with explicit ordering. "
        "When a feature needs multiple related developer beads, coalesce tester, documentation, and review work into shared follow-up beads rather than duplicating that work in each implementation bead. "
        "Those shared follow-up beads should depend on the full related implementation set they validate, document, or review so the follow-up happens after the combined change is ready. "
        "Every bead in the tree must include title, agent_type, description, acceptance_criteria, dependencies, linked_docs, "
        "expected_files, expected_globs, and children. Dependencies may reference other bead titles anywhere in the same feature tree. "
        "Infer file scope when the spec gives enough signal; otherwise return empty arrays.\n\n"
        f"{spec_text}"
    )
