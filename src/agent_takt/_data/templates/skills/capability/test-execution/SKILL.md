---
name: test-execution
description: Run targeted test commands and summarize results.
---

# test-execution

Use this skill for the mechanics of selecting, running, and reporting targeted validation commands.

## Command Selection

Before running anything:

1. Search shared memory for the project's test command:

       $TAKT_CMD memory search "test command" --namespace global --limit 3

   If a prior agent has recorded the command, use it directly.

2. If not in memory, detect from the project root (at `repo/`):
   - `repo/pyproject.toml` present → likely `uv run pytest` or `uv run python -m unittest`
   - `repo/package.json` present → likely `npm test` or `npx jest`
   - `repo/Cargo.toml` present → likely `cargo test`
   - `repo/go.mod` present → likely `go test ./...`
   - `repo/pom.xml` or `repo/build.gradle` present → likely `mvn test` or `./gradlew test`
   - Check `repo/README.md` or `repo/.takt/config.yaml` (`common.test_command`) for the authoritative command.

3. Once you have determined the command, write it to shared memory so future agents don't need to detect it:

       $TAKT_CMD memory add "Test command for this project: <command>" \
           --namespace global --source tester

4. For targeted runs, most frameworks accept a file path or module argument:
   `<test-command> <path/to/test_file>`. Use this form when the framework supports it.
   Fall back to the full command only when targeted runs are not supported.

5. Identify the production files and behaviors touched by the bead, and choose the narrowest
   targeted invocation that directly exercises that behavior. Escalate to multiple specific
   targets only when one is insufficient to cover the changed surface area.

## Execution Rules

- Never run broad discovery sweeps or the full suite when a narrower targeted command will answer the question.
- Never substitute a broad suite run for thinking through the correct target.
- Re-run only the targeted modules or files needed to confirm a fix after editing tests or minimal test-enablement code.
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
