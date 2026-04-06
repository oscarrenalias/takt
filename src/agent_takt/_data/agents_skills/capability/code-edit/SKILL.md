---
name: code-edit
description: Code editing capability for implementation-focused tasks.
---

# code-edit

Use this skill for the mechanics of making code changes safely and readably within an assigned bead.

## Edit Preparation

Before changing files:

1. Read the current implementation and identify the exact code path to modify.
2. Check for existing helpers, abstractions, or patterns nearby that should be reused instead of introducing a new shape.
3. Distinguish required edits from optional cleanup. Only the required edits belong in the initial patch.

## Editing Rules

- Prefer focused diffs over broad rewrites.
- Change the fewest files necessary to satisfy the acceptance criteria.
- Preserve public behavior unless the bead explicitly requires a behavior change.
- Keep names, imports, and module boundaries consistent with the surrounding code.
- Add brief comments only where the reasoning would otherwise be hard to recover from the code alone.

## Safe Change Strategy

- Make one coherent change at a time so regressions are easier to localize.
- When modifying an existing flow, keep unaffected branches structurally similar unless a broader reshape is necessary.
- Reuse existing utilities before adding new ones. If a new helper is warranted, place it where future maintainers would expect to find it.
- Avoid incidental formatting churn that obscures the functional delta.

## Self-Check Before Handoff

Review the patch with these questions:

- Does every changed line contribute directly to the bead?
- Is any behavior change intentional and easy to explain?
- Did the edit leave dead code, duplicate logic, or partially migrated call sites behind?
- Are the touched files the right ownership boundary for this bead?

## Output Discipline

Record the actual edited files precisely and describe the code-level effect in concrete terms. If the safest path would require broader surgery than the bead allows, stop and surface that as follow-up work instead of forcing a risky edit.
