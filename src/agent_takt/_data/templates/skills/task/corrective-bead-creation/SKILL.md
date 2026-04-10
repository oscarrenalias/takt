---
name: corrective-bead-creation
description: Turn review findings into actionable corrective developer beads.
---

# corrective-bead-creation

Use this skill when a review bead finds a concrete implementation problem that should be fixed in a follow-up developer bead.

## Goal

Produce a corrective bead request that preserves the review evidence and gives the developer a narrow, auditable fix scope.

## What to Capture

1. State the finding in concrete terms, including the file, behavior, or acceptance gap that blocked approval.
2. Explain why the issue matters to correctness, completeness, or merge readiness.
3. Distinguish confirmed repository evidence from reviewer inference; label any suspected root cause as an inference.
4. Identify the narrowest file set or subsystem that likely needs developer changes without guessing beyond the evidence.
5. Carry forward any acceptance criterion or handoff claim that the current implementation failed to satisfy.

## Bead Writing Rules

- Create a developer bead when code, configuration, or local implementation docs must change.
- Keep one corrective bead focused on one finding or one tightly coupled fix set.
- Describe the required outcome, not a full implementation design, unless the safe correction is already obvious.
- Do not absorb tester, documentation, planner, or reviewer follow-up into the corrective bead scope.
- If the issue is purely review-side clarification or another agent type owns the next step, block and hand off instead of creating a developer corrective.

## Handoff Quality Bar

- The corrective bead should let a developer start work without redoing the whole review investigation.
- The request should explain both the observed gap and the merge consequence if left unresolved.
- If multiple findings exist, keep them separable unless one fix is inseparable from another.
