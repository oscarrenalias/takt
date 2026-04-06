---
name: corrective-implementation
description: Implement corrective changes requested by review/testing findings.
---

# corrective-implementation

Use this skill when a developer bead is follow-up work for review or tester findings.

## Procedure

- Restate the concrete defect or acceptance gap before editing anything.
- Confirm the requested fix fits the assigned bead scope and expected files.
- Implement the smallest production change that resolves the finding.
- Preserve existing behavior outside the reported failure or missing requirement.
- Do not mix opportunistic cleanup, refactors, or unrelated migrations into the fix.
- Update adjacent local docs only when the corrective change would otherwise leave them inaccurate.
- If the finding actually requires tester, review, planner, or documentation ownership, block and hand off instead of absorbing that work.

## Output expectations

- Report exactly which finding was addressed and which files changed.
- Call out any remaining risk, follow-up bead need, or blocked dependency explicitly.
- Leave validation to the tester agent; only do the allowed compile or import check from developer guardrails.
