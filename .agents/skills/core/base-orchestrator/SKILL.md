---
name: base-orchestrator
description: Base orchestration workflow rules and response contract.
---

# base-orchestrator

This skill defines the bead execution contract shared by every agent type. Use it together with the active role guardrail template; if they conflict, the role guardrails win for scope and allowed actions.

For bead lifecycle, agent types, verdicts, and project conventions, see CLAUDE.md.

## Core Workflow

1. Read the assigned bead JSON carefully before touching files.
2. Read every linked repository document and any required shared-memory files before acting.
3. Confirm the task still fits the current bead scope, expected files, and agent role.
4. Inspect the relevant code or docs directly instead of inferring behavior from the bead text alone.
5. Complete only the work that belongs to this bead and leave unrelated issues untouched.

## Scope Discipline

- Stay inside the assigned architecture, file scope, and agent specialization.
- If the bead requires work outside your role, stop and return a blocked result with `block_reason` and `next_agent`.
- If the bead requires newly discovered files, record them in `touched_files`, `changed_files`, `expected_files`, or `expected_globs` as appropriate.
- Do not silently absorb planner, tester, documentation, or review work that should be handed off.

## Execution Expectations

- Prefer concrete repository evidence over assumptions.
- Keep edits minimal, local, and reversible.
- Preserve user changes and sibling bead work; do not revert unrelated modifications.
- When you discover follow-up work that should be handled separately, create a new bead entry in `new_beads` instead of broadening the current bead.

## Handoff Contract

Every final result must leave an actionable handoff state for the scheduler and downstream agents. Required fields: `summary`, `completed`, `remaining`, `risks`, `next_action`, `next_agent`, `touched_files`, `changed_files`, `updated_docs`, `conflict_risks`. Role guardrails define any additional required fields for that agent type.

## Output Rules

- The final message must be valid JSON matching the orchestrator schema exactly.
- Always set `outcome` to `completed`, `blocked`, or `failed`.
- If no files were touched or changed, return empty arrays rather than omitting the fields.

## Blocking Guidance

Return `blocked` when:

- the required work belongs to another agent type
- acceptance criteria depend on missing upstream changes
- the bead cannot be completed without expanding scope beyond what is safe to claim now

When blocked, make the handoff specific: explain why, identify the next agent, and name the files or scope that triggered the block.
