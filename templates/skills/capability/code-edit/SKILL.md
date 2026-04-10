---
name: code-edit
description: Code editing capability for implementation-focused tasks.
---

# code-edit

Use this skill for the mechanics of making code changes safely and readably within an assigned bead.

## Edit Preparation

Distinguish required edits from optional cleanup before touching files. Only required edits belong in the initial patch; check for existing patterns to reuse before introducing new shapes.

## Editing Rules

- Prefer focused diffs over broad rewrites.
- Preserve public behavior unless the bead explicitly requires a behavior change.
- Avoid incidental formatting churn that obscures the functional delta.

## Safe Change Strategy

- Make one coherent change at a time so regressions are easier to localize.
- When modifying an existing flow, keep unaffected branches structurally similar unless a broader reshape is necessary.

## Self-Check Before Handoff

- Does every changed line contribute directly to the bead?
- Is any behavior change intentional and easy to explain?
- Did the edit leave dead code, duplicate logic, or partially migrated call sites behind?
- Are the touched files the right ownership boundary for this bead?

## Output Discipline

Record the actual edited files precisely and describe the code-level effect in concrete terms. If the safest path would require broader surgery than the bead allows, stop and surface that as follow-up work instead of forcing a risky edit.
