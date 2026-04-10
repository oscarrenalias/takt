---
name: risk-assessment
description: Assess concrete downstream risks that matter for review handoff.
---

# risk-assessment

Use this skill when a review bead needs to explain residual risk, merge sensitivity, or operational uncertainty beyond direct code defects.

## Goal

Surface the real risks that downstream agents or operators should understand before approval or handoff, and avoid vague cautionary filler.

## Risk Assessment Workflow

1. Start from repository evidence in the scoped files and the bead acceptance criteria.
2. Identify risks that would still matter if the code merged as-is: behavior regressions, unsafe assumptions, incomplete rollout handling, or file-scope conflicts.
3. Separate present defects from residual risks. A defect belongs in findings; a risk is a credible concern that may require caution, sequencing, or follow-up.
4. State the trigger, consequence, and affected area for each risk in plain terms.
5. Name the mitigation path when one is known: additional validation, narrower rollout, follow-up bead, or explicit operator attention.

## Quality Bar

- Keep risk statements concrete and evidence-based.
- Prefer a short list of meaningful risks over exhaustive speculation.
- Call out uncertainty explicitly when the evidence is incomplete.
- Do not invent mitigation steps that require architecture changes outside the bead scope.
- If no material residual risks remain, say so plainly instead of adding generic warnings.

## Output Expectations

- Summarize risks in terms another agent can act on immediately.
- Make it clear whether a risk blocks signoff, requires tracked follow-up, or is safe to note without blocking.
- Keep the assessment aligned with review responsibilities rather than implementation planning.
