---
name: developer-implementation
description: Implement assigned bead scope with minimal risk.
---

# developer-implementation

Use this skill as the main workflow for developer beads. It complements the role guardrails by describing how to execute the work, not by restating policy.

For core workflow, scope discipline, execution expectations, and handoff contract, see `core/base-orchestrator`.

## Objective

Land the assigned implementation cleanly inside the existing architecture, leave the worktree in a reviewable state, and hand off precise status for downstream agents.

## Validation

After editing, perform only lightweight developer validation appropriate to this role:

- Run a quick import or syntax check relevant to the changed files.
- Use compile-style validation to catch broken imports, syntax errors, and obviously invalid module wiring.
- Do not treat this skill as permission to take over tester or reviewer responsibilities.

## When to Create Follow-Up Work

Create a follow-up bead instead of silently expanding scope when you uncover:

- missing tests or broader validation work for a tester bead
- documentation updates that are useful but not required to finish implementation
- review-only concerns such as design tradeoffs or risky edge cases that need explicit signoff
- adjacent defects that should be fixed separately to keep this bead auditable
