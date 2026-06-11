---
name: defect-fix
description: Fix a post-merge defect reported by the operator, add a regression test, and return structured output.
---

# defect-fix

Use this skill as the primary workflow for defect beads. A defect bead is operator-filed for a bug discovered after the originating feature has merged to main. It is **standalone** — there is no parent bead, no prior review findings, and no upstream tester to look for. The entire problem statement lives in this bead's description.

This is the key difference from `task/corrective-implementation`, which assumes a parent bead with prior review or tester findings. When those two skills diverge, this role skill takes precedence: read the bead description, not a parent's findings.

## Objective

Reproduce the issue mentally, make the minimal production fix, add a regression test that isolates it, verify with focused tests, and return a precise structured output so the auto-spawned `-review` bead can evaluate completeness.

## Workflow

1. **Read the bead description carefully.** Identify the concrete symptom, the affected component or code path, and any reproduction steps the operator provided. Do not look for a parent bead or prior review findings — the filed description is the authoritative problem statement.

2. **Identify the root cause.** Trace the symptom to its source in the code before writing any fix. Record the root cause in `design_decisions` so the reviewer can validate the diagnosis.

3. **Make the minimal fix.** Change only the code necessary to correct the identified root cause. Do not clean up unrelated code, refactor nearby logic, or absorb scope from other known issues. File a separate defect bead for any other bugs you discover.

4. **Add a regression test.** Write a focused test that fails on the unfixed code and passes after the fix. If the bug is genuinely untestable in an automated way (e.g. pixel-level CSS rendering, hardware-specific behaviour), explicitly state that in `test_coverage_notes` rather than silently omitting it.

5. **Run focused tests for the affected module.** Execute only the tests for the file or module you changed — `pytest path/to/test_file.py`, `npm test -- path/to/test.ts`, or equivalent. Do **not** run the full test suite; full-suite verification belongs to the `-review` followup or `takt merge`'s test gate.

6. **Run a build sanity step if applicable.** Detect the project type from the worktree root and run the matching check:
   - `package.json` with a `build` script → `npm run build` (or `yarn build` / `pnpm build`)
   - `pyproject.toml` → `python -m py_compile <changed_files>` (or `uv run python -m py_compile`)
   - `Cargo.toml` → `cargo check`
   - No recognisable build target → record `build_verification: "skipped — no build target detected"` in `design_decisions`; do not leave the step silently absent.

7. **Return structured output.** Populate the required handoff fields:
   - `verdict` — `approved` when fix is complete; `needs_changes` if you were blocked.
   - `findings_count` — number of unresolved issues discovered (0 when clean).
   - `test_coverage_notes` — what the regression test covers, how to run it, and any gap (e.g. untestable scenario).
   - `design_decisions` — root cause diagnosis, why the chosen fix is correct, any alternatives considered.
   - `known_limitations` — deliberate scope constraints, deferred work, or known gaps.

## Boundaries

- Do not broaden scope beyond the filed defect. One defect bead, one fix.
- Do not run the full test suite — focused tests for the affected module only.
- Do not take over reviewer signoff. The `-review` followup handles final approval.
- Do not edit unrelated files. If the fix genuinely requires touching a file not listed in `expected_files`, call that out explicitly in `design_decisions`.
