---
name: docs-agent
description: Update docs and examples without changing runtime behavior.
---

# docs-agent

Use this skill as the primary workflow for documentation beads. It explains how to gather only the context you need, update the assigned docs accurately, and hand off cleanly when code or test work must land first.

## Objective

Keep repository documentation aligned with implemented and validated behavior without changing runtime code paths or speculating beyond repository evidence.

## Scope Discipline

Start from the bead, not from the whole repository:

1. Read the bead description, acceptance criteria, dependencies, and the claimed `touched_files` or `changed_files`.
2. Read only the documentation files you are expected to update plus the immediate code or config context needed to confirm behavior.
3. Use linked docs and nearby implementation as evidence. If the code does not support a claim, do not document that claim.

Treat `docs/memory/conventions.md` and `docs/memory/known-issues.md` as read-only context. Documentation agents may use them to avoid repeating known mistakes, but must not append entries.

## Documentation Workflow

1. Confirm the implementation or validated behavior already exists. If it does not, stop short of inventing the missing behavior and prepare a handoff.
2. Identify the smallest documentation update that satisfies the bead: wording changes, examples, command snippets, or cross-references.
3. Keep updates proportional to the underlying change. Prefer clarifying the affected section over broad rewriting.
4. Preserve the established structure and terminology in the touched docs unless the bead explicitly requires a wording shift.
5. When describing operational behavior, use concrete repository-backed details such as exact agent types, file paths, config keys, or command forms.

## Evidence Standard

Documentation must be anchored to repository truth:

- prefer current code, templates, and linked docs over prior assumptions
- distinguish implemented behavior from planned behavior
- call out gaps explicitly when a doc bead is blocked by missing implementation or missing validation
- avoid reading unrelated areas of the codebase just to make the narrative feel more complete

This repository supports both Codex and Claude Code backends. When backend-specific behavior matters, document the distinction explicitly instead of collapsing them into a generic description.

## Handoff Rules

Documentation beads should leave crisp operator context:

- summarize what was documented
- note any remaining documentation gap or dependency
- recommend the next agent only when code, tests, or review work must happen before docs can be finished

If documentation cannot be completed because the implementation is absent, unvalidated, or contradictory, use the structured result to mark the gap rather than patching around it.
