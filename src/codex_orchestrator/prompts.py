from __future__ import annotations

import json
from pathlib import Path

from .models import Bead


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


def role_instructions(agent_type: str) -> str:
    rules = {
        "planner": "Decompose the feature into a parent epic and child beads with dependencies. Do not implement code.",
        "developer": "Implement only the assigned bead. Do not redesign unrelated architecture. You may create sub-beads for discovered follow-up work.",
        "tester": "Write or update automated tests, run relevant checks, and report defects with clear follow-up recommendations.",
        "documentation": "Update only documentation relevant to the assigned bead and keep examples aligned with code.",
        "review": "Validate acceptance criteria, code quality, and the presence of tests and docs. Do not implement feature work.",
        "scheduler": "Coordinate work deterministically and keep handoff summaries concise and structured.",
    }
    return rules[agent_type]


def build_worker_prompt(bead: Bead, context_paths: list[Path], root: Path) -> str:
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
        f"You are the {bead.agent_type} agent for a Codex orchestration system.\n"
        f"{role_instructions(bead.agent_type)}\n\n"
        "Execution context:\n"
        "You are running inside a shared feature worktree. Sibling sub-beads may also run in this same feature tree, "
        "but only when dependencies and file-scope claims allow it. Stay within this bead's scope.\n\n"
        "Assigned bead:\n"
        f"{json.dumps(payload, indent=2)}\n\n"
        "Available repository context files:\n"
        f"{render_context_snippets(context_paths, root)}\n\n"
        "Return a JSON object matching the required schema. "
        "Always include a concise summary, structured handoff fields, actual touched files, "
        "updated scope if it changed, conflict risks for downstream agents, and any newly discovered sub-beads."
    )


def build_planner_prompt(spec_text: str) -> str:
    return (
        "Read the feature specification below and propose an orchestration plan. "
        "Return JSON with keys epic_title, epic_description, linked_docs, and children. "
        "Each child must include title, agent_type, description, acceptance_criteria, dependencies, linked_docs, "
        "expected_files, and expected_globs. Infer file scope when the spec gives enough signal; otherwise return empty arrays.\n\n"
        f"{spec_text}"
    )
