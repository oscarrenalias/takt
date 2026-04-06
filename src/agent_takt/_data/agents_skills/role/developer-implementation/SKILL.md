---
name: developer-implementation
description: Implement assigned bead scope with minimal risk.
---

# developer-implementation

Use this skill as the main workflow for developer beads. It complements the role guardrails by describing how to execute the work, not by restating policy.

## Objective

Land the assigned implementation cleanly inside the existing architecture, leave the worktree in a reviewable state, and hand off precise status for downstream agents.

## Working Pattern

1. Read the bead carefully and extract the exact acceptance criteria, expected files, and stated dependencies.
2. Read nearby code and any linked context before editing. Confirm how the current implementation actually works instead of assuming from file names or past patterns.
3. Keep the scope narrow. Solve the bead that was assigned, not adjacent problems unless they block the assigned work.
4. Prefer the smallest change that fully satisfies the bead. Reach for larger refactors only when the existing structure makes a direct fix unsafe or much harder to reason about.
5. Keep the implementation aligned with established conventions in the touched area. Follow the local style of naming, control flow, error handling, and comments.

## During Implementation

- Maintain a running map of touched files versus merely inspected files so the final handoff is accurate.
- Preserve in-progress user or sibling-agent changes. Integrate with them when possible; do not overwrite unrelated edits.
- When requirements are ambiguous, infer from the nearest existing pattern in the repository and keep the inference conservative.
- If you discover a real blocker outside the bead's scope, stop broadening the patch and return a blocked or follow-up-ready outcome instead of folding extra work into this bead.

## Validation

After editing, perform only lightweight developer validation appropriate to this role:

- Run a quick import or syntax check relevant to the changed files.
- Use compile-style validation to catch broken imports, syntax errors, and obviously invalid module wiring.
- Do not treat this skill as permission to take over tester or reviewer responsibilities.

## Handoff Expectations

Your final result should let the next agent continue without rereading the whole diff. Include:

- what changed and why
- any remaining risks or assumptions
- precise `touched_files` and `changed_files`
- whether follow-up is required, and which agent should take it

## When to Create Follow-Up Work

Create a follow-up bead instead of silently expanding scope when you uncover:

- missing tests or broader validation work for a tester bead
- documentation updates that are useful but not required to finish implementation
- review-only concerns such as design tradeoffs or risky edge cases that need explicit signoff
- adjacent defects that should be fixed separately to keep this bead auditable
