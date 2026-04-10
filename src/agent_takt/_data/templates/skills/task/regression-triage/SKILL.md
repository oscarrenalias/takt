---
name: regression-triage
description: Classify regressions so tester handoffs reflect severity and required follow-up.
---

# regression-triage

Use this skill when a test failure or manual validation result appears to be a regression and you need to classify its impact before handoff.

## Goal

Decide whether the issue is a true regression, identify what behavior changed, and describe the urgency and downstream action clearly enough for scheduler, developer, and review agents to act on it.

## Classification Steps

1. Confirm the prior expected behavior from acceptance criteria, specs, docs, or previously passing behavior visible in the branch history.
2. Describe the current behavior and the exact way it diverges from that baseline.
3. Classify the regression impact: release-blocking, acceptance-blocking, degraded-but-nonblocking, or uncertain.
4. Note the scope of impact: isolated path, shared subsystem, operator workflow, or broad cross-cutting behavior.
5. Call out whether the regression is newly introduced by the current bead, pre-existing in the branch, or not yet attributable from available evidence.

## Decision Rules

- Treat changes that break explicit acceptance criteria as acceptance-blocking regressions.
- Treat crashes, data loss, corrupt state, or impossible operator recovery as release-blocking unless evidence proves otherwise.
- Treat cosmetic or low-priority workflow drift as degraded-but-nonblocking only when the acceptance criteria still hold.
- Use uncertain only when evidence is incomplete; say what validation or investigation would resolve the uncertainty.
- If the issue is pre-existing, document that clearly so the current bead is not blamed for unrelated failures.

## Handoff Expectations

- Summarize the regression in one or two sentences that state both baseline and breakage.
- Pair the classification with the recommended next action: block for developer follow-up, continue with noted risk, or hand to review/documentation as appropriate.
- Keep the write-up evidence-based and concise so downstream agents can quote it directly in their bead outputs.
