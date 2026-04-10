---
name: code-review
description: Structured review capability for correctness and risk analysis.
---

# code-review

Use this skill for the mechanics of performing a focused review. It complements `reviewer-signoff` by describing how to inspect changed material and turn observations into actionable findings.

## Inspection Setup

Before judging the patch:

1. Read the claimed `changed_files` and `touched_files` first so the review stays scoped.
2. Read the relevant acceptance criteria and developer handoff summary alongside the diff or current file contents.
3. Identify the specific behavior, contract, or workflow each changed file is supposed to support.

## What To Inspect

Review the changed code, docs, and configuration for issues that would matter after merge:

- correctness bugs or logic gaps
- incomplete acceptance-criteria coverage
- risky edge cases or invalid assumptions
- regressions introduced by the change
- handoff inaccuracies, such as claiming work is complete when repository evidence disagrees

Focus on material defects. Skip style commentary unless it directly causes maintainability or correctness risk in the changed flow.

## Review Method

- Trace the main execution path affected by the change and compare it with nearby existing patterns.
- Check imports, call sites, configuration wiring, and data-shape assumptions when the patch crosses module boundaries.
- Verify docs or comments changed with the code still match the actual behavior they describe.
- Prefer repository evidence over guesswork; if something cannot be established from the scoped files, state the uncertainty instead of overstating the claim.

## Writing Findings

Each finding should be specific, minimal, and actionable.

- Point to the file and behavior at issue.
- Explain the consequence in practical terms.
- State why the issue is unresolved now, not just theoretically risky.
- Recommend follow-up direction only to the extent needed to unblock the next agent.

When no such issues exist, say explicitly that no findings were discovered in this review pass.

## Severity And Prioritization

Order findings from most severe to least severe:

- blockers that make the bead incomplete or incorrect
- significant regressions or unaddressed risks
- lower-severity issues that still require follow-up before signoff

Do not pad the review with low-value observations once a blocking issue is already sufficient to require `needs_changes`.

## Verdict Mapping

Map the inspection result into the structured output consistently:

- no unresolved findings -> `verdict=approved`, `findings_count=0`, `requires_followup=false`
- one or more unresolved findings -> `verdict=needs_changes`, `findings_count` equals the unresolved finding count, `requires_followup=true` unless explicitly justified otherwise

If signoff is blocked, include a `block_reason` that summarizes the concrete gap or defect driving the decision.
