---
name: tester-validation
description: Validate behavior with automated tests and defect reporting.
---

# tester-validation

Use this skill as the main workflow for tester beads. It complements the tester guardrails by turning them into a repeatable targeted-validation routine.

## Objective

Confirm the assigned bead with the smallest relevant automated test scope, report defects or coverage gaps clearly, and avoid wasting time on unrelated suites.

## Working Pattern

1. Read the bead carefully and extract the acceptance criteria, expected files, touched files, and any developer handoff notes.
2. Map the changed production files to the narrowest relevant test modules. Prefer the direct unit or integration module that exercises the changed behavior.
3. Update or add tests only where that coverage is required for the assigned bead.
4. Run targeted validation for those modules and capture the exact command and outcome.
5. If testing exposes a product defect or a tester-scope blocker, stop broadening the patch and return a clear blocked handoff.

## Scope Rules

- Run only the tests that directly cover the bead's changed behavior.
- Do not run `unittest discover`, the full suite, or broad package-level sweeps when a narrower module-level command will answer the question.
- Use the bead's `expected_files`, `touched_files`, and recent implementation summary as the default guide for choosing test scope.
- If no existing test module clearly matches, add the smallest new targeted coverage rather than compensating with a larger test run.

## Test Editing Boundaries

- Keep test changes focused on validating the assigned behavior.
- Make minimal test-enablement fixes only when strictly necessary to execute the targeted tests.
- Do not implement feature logic under the guise of unblocking tests; hand that work back to a developer bead instead.
- Preserve existing test style, helper usage, and fixture structure in the touched area.

## Failure Handling

- When a targeted test fails because the implementation is wrong, treat that as a developer follow-up, not a tester invitation to rewrite production code.
- When coverage is missing, describe the gap concretely and either add the missing targeted test or block with a precise reason why tester scope is insufficient.
- When the correct test target is ambiguous, choose the narrowest defensible module and state the assumption in the handoff.

## Handoff Expectations

Your result should make the next agent's job obvious. Always include:

- which targeted test modules or cases were added or run
- the exact validation scope chosen and why it was sufficient
- whether unresolved defects or coverage gaps remain
- structured verdict fields that match the guardrails: `verdict`, `findings_count`, and `requires_followup`

Use `verdict=approved` only when the targeted validation needed for this bead is complete with no unresolved tester-scope findings. Use `verdict=needs_changes` when a defect, missing prerequisite, or unresolved coverage gap still requires follow-up.
