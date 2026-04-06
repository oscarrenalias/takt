---
name: test-execution
description: Run targeted test commands and summarize results.
---

# test-execution

Use this skill for the mechanics of selecting, running, and reporting targeted validation commands.

## Command Selection

Before running anything:

1. Identify the production files and behaviors touched by the bead.
2. Choose the narrowest `unittest` module or case that directly exercises that behavior.
3. Prefer commands of the form `uv run python -m unittest tests.<module_name> -v`.
4. Escalate to multiple specific modules only when one module is insufficient to cover the changed surface area.

## Execution Rules

- Never use `uv run python -m unittest discover`.
- Never substitute a broad suite run for thinking through the correct target.
- Re-run only the targeted modules needed to confirm a fix after editing tests or minimal test-enablement code.
- Keep a record of the exact commands executed so the result can be reproduced.

## Choosing the Right Scope

- Start from the closest matching test file for each changed source file.
- If the bead changes only prompts, skills, or configuration wiring, target the test module that validates that wiring rather than unrelated end-to-end coverage.
- If a failure occurs in setup shared by multiple tests, it can justify one additional adjacent module, but do not expand to package-wide discovery.
- If no automated test exists for the behavior, add the smallest relevant test and then run only that module.

## Result Reporting

Summaries should be concrete and reproducible:

- name the targeted module or case that ran
- say whether it passed, failed, or was blocked before execution
- include the key failure reason when a command does not pass
- distinguish product defects from test harness or environment issues

## Blocking Conditions

Return a blocked or follow-up-ready result when:

- the correct validation requires production changes outside tester scope
- environment issues prevent targeted execution and cannot be resolved with minimal test-enablement work
- the only available validation path would require an unjustified broad suite run

The goal is not maximum test volume. The goal is the minimum trustworthy automated evidence for the assigned bead.
