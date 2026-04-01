from __future__ import annotations

import json
from pathlib import Path

from .models import Bead

BUILT_IN_AGENT_TYPES = ("planner", "developer", "tester", "documentation", "review")
DEFAULT_TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "templates" / "agents"


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


def build_worker_prompt(bead: Bead, context_paths: list[Path], root: Path) -> str:
    guardrail_path, guardrail_text = load_guardrail_template(bead.agent_type, root=root)
    output_requirements = render_agent_output_requirements(bead.agent_type)
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
        "conflict_risks": bead.conflict_risks,
        "handoff_summary": bead.handoff_summary.__dict__,
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
        "Assigned bead:\n"
        f"{json.dumps(payload, indent=2)}\n\n"
        "Available repository context files:\n"
        f"{render_context_snippets(context_paths, root)}\n\n"
        "CRITICAL: Your final message MUST be a JSON object matching the required output schema. "
        "Do not end with a conversational summary or status update — the orchestrator parses your "
        "last message as JSON and will fail if it is not valid JSON. "
        "Always include a concise summary, structured handoff fields, actual touched files, "
        "updated scope if it changed, conflict risks for downstream agents, and any newly discovered sub-beads."
    )


def build_planner_prompt(spec_text: str) -> str:
    return (
        "Read the feature specification below and propose an orchestration plan. "
        "Return JSON with keys epic_title, epic_description, linked_docs, and feature. "
        "The feature value must be one top-level non-runnable feature container bead representing the shared execution root for this spec. "
        "Concrete implementation, testing, documentation, and review work must be expressed under feature.children rather than as sibling top-level beads. "
        "Every bead in the tree must include title, agent_type, description, acceptance_criteria, dependencies, linked_docs, "
        "expected_files, expected_globs, and children. Dependencies may reference other bead titles anywhere in the same feature tree. "
        "Infer file scope when the spec gives enough signal; otherwise return empty arrays.\n\n"
        f"{spec_text}"
    )
