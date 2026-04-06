---
name: defect-bead-creation
description: Turn tester-discovered defects into actionable developer beads.
---

# defect-bead-creation

Use this skill when testing finds a real defect that blocks acceptance or needs tracked follow-up work.

## Goal

Produce a defect report that gives the next developer enough information to reproduce the failure, understand the expected behavior, and implement the smallest safe fix.

## What to Capture

1. State the observed behavior in concrete terms, including the failing command, workflow, or user action.
2. State the expected behavior tied to the bead acceptance criteria, spec, or existing documented behavior.
3. Include the narrowest reliable reproduction steps. If the failure is flaky, say so explicitly and describe the known trigger pattern.
4. Name the most likely file, module, or subsystem involved when the evidence supports it. Do not guess beyond the available signal.
5. Record any error text, assertion message, or visible symptom needed to recognize the defect again.

## Bead Writing Rules

- Write the defect bead for a developer agent unless the work is clearly documentation-only.
- Keep the scope to one defect or one tightly coupled fix set so the follow-up remains auditable.
- Describe the user-visible or acceptance-impacting consequence, not just the failing test name.
- Separate confirmed facts from hypotheses. Label suspected root cause as an inference.
- If the defect blocks the current tester bead, say that explicitly in the blocked handoff.

## Handoff Quality Bar

- The report should let another agent reproduce the issue without re-running your whole investigation.
- The report should explain why this is a defect, not just that a test failed.
- The report should avoid prescribing a full implementation unless the safe fix is already obvious from evidence.
