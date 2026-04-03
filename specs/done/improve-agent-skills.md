# Improve Agent Skills

## Objective

Every skill file in `.agents/skills/` is currently 1–3 lines — effectively a name and a one-sentence description. The `memory` skill is the only exception and is the model to follow. This spec rewrites all 22 remaining skills to provide actionable, concrete guidance that agents can apply during a bead.

Skills complement guardrail templates: guardrails say *what to do and what not to do*; skills say *how to do it*. Do not duplicate guardrail content — add the procedural detail that guardrails omit.

## The `memory` Skill as the Standard

`memory` is the reference implementation. It tells agents:
- When to act (at bead start)
- How to act (append-only, dated entries, specific format)
- Access rules per agent type (table)
- What qualifies and what does not

Every rewritten skill should reach this level of specificity.

## Skills to Rewrite

### `core/base-orchestrator`

**Assigned to**: all agent types

Replace with:

```markdown
---
name: base-orchestrator
description: Core workflow rules every agent must follow on every bead.
---

# base-orchestrator

These rules apply to all agent types on every bead, regardless of other skills loaded.

## Read the Bead Before Acting

Read the bead's `description`, `acceptance_criteria`, `expected_files`, `touched_files`, and `execution_history` before doing any work. The bead scope is the contract — do not expand it.

## Stay Within Scope

Only touch files listed in `expected_files` or `expected_globs`. If you discover necessary work outside bead scope, create a sub-bead and hand it off — do not absorb it silently.

## Populate Handoff Fields Accurately

- `touched_files`: every file you read during the bead (whether modified or not)
- `changed_files`: only files you actually modified
- `risks`: concrete risks introduced by this bead (empty string if none)
- `completed`: one-line summary of what was done
- `remaining`: what was deferred, if anything

Do not leave these fields empty if there is relevant content to report.

## Return Valid Structured JSON

Your final message must be a JSON object matching the output schema. Do not wrap it in markdown code fences. Do not add commentary after the JSON. The scheduler parses the last assistant message as JSON.

## Execution History

The `execution_history` field shows what previous attempts did. Read it before starting — do not repeat work that already succeeded.
```

---

### `role/developer-implementation`

**Assigned to**: developer agents

Replace with:

```markdown
---
name: developer-implementation
description: How to implement assigned bead scope safely and completely.
---

# developer-implementation

## Implement Only What Is Assigned

Read `description` and `acceptance_criteria` first. Implement exactly that — no more, no less. If you find unrelated issues while working, note them in `risks` or create a sub-bead; do not fix them inline.

## Preferred Tool Order for Code Changes

1. `Read` — read files before editing them
2. `Edit` — make targeted edits with precise `old_string`/`new_string`
3. `Write` — only for new files or complete rewrites
4. `Bash` — for build verification only (e.g. `uv run python -m py_compile <file>`)

Never use `Bash` with `sed`, `awk`, or `grep` when `Edit`, `Grep`, or `Glob` will do.

## Verify Without Running Tests

After implementing, verify the change does not break imports or syntax:
```
uv run python -c "import codex_orchestrator"
uv run python -m py_compile src/codex_orchestrator/<changed_file>.py
```
Do not run the test suite. That is the tester agent's job.

## Report Changed Files Precisely

- `touched_files`: every file you opened, even if you did not modify it
- `changed_files`: only files you wrote to

Accurate file lists allow the scheduler to detect conflicts and route followup beads correctly.

## Create Sub-Beads for Out-of-Scope Work

If you discover work that belongs in a separate bead, add it to `new_beads` in your response. Do not implement it in the current bead.
```

---

### `role/tester-validation`

**Assigned to**: tester agents

Replace with:

```markdown
---
name: tester-validation
description: How to validate bead scope with targeted tests and report defects.
---

# tester-validation

## Run Targeted Tests Only

Never run `uv run python -m unittest discover`. Always target the specific module:
```
uv run python -m unittest tests.<module_name> -v
```
Use the bead's `expected_files` and `touched_files` to identify which test module is relevant. `discover` wastes time and often exceeds the agent timeout.

## Identify the Right Test Module

| Changed file | Test module |
|---|---|
| `src/codex_orchestrator/scheduler.py` | `tests.test_orchestrator` |
| `src/codex_orchestrator/tui.py` | `tests.test_tui` |
| `src/codex_orchestrator/config.py` | `tests.test_config` |
| `src/codex_orchestrator/console.py` | `tests.test_console` |
| `src/codex_orchestrator/runner.py` | `tests.test_runner_timeout` |

When in doubt, read the test file names under `tests/` with `Glob`.

## Write Tests Before Running

If the bead requires new test coverage, write the tests first, then run them. Tests must be in the appropriate `tests/test_*.py` file.

## Minimal Test-Enablement Fixes Only

If a test fails due to a missing import or fixture, fix only that. Do not refactor production code to make tests pass — block instead and create a sub-bead.

## Verdict Rules

- `verdict=approved`: all relevant tests pass, coverage is adequate
- `verdict=needs_changes`: any test fails or required coverage is missing — include `block_reason` with the specific failure
```

---

### `role/reviewer-signoff`

**Assigned to**: review agents

Replace with:

```markdown
---
name: reviewer-signoff
description: How to conduct a structured review and produce a clear verdict.
---

# reviewer-signoff

## What to Review

Read only the files in `touched_files` and `changed_files`. Do not read the whole codebase. Do not run tests.

Check:
1. Acceptance criteria — is each criterion met by the implementation?
2. Correctness — does the code do what it claims?
3. Regressions — could this break existing behaviour?
4. Test coverage — are the changes adequately tested?
5. Docs — are user-visible changes documented?

## Severity Levels

| Level | Meaning |
|---|---|
| HIGH | Must be fixed before approval — blocks merge |
| MEDIUM | Should be fixed — blocks unless explicitly waived |
| LOW | Suggestion — does not block |

## Verdict Rules

- `verdict=approved`: no HIGH or MEDIUM findings remain unresolved
- `verdict=needs_changes`: any HIGH or MEDIUM finding is unresolved — set `block_reason` to a clear summary of what must be fixed

Do not approve work with unresolved HIGH or MEDIUM findings under any circumstances.

## Keep It Concise

If there are no findings, say so in one line and approve. Do not pad the review with compliments or restatements of what the code does.
```

---

### `role/docs-agent`

**Assigned to**: documentation agents

Replace with:

```markdown
---
name: docs-agent
description: How to update documentation to match implemented behaviour.
---

# docs-agent

## What to Update

Read `touched_files` and `changed_files` from the bead. Update only documentation that describes the changed behaviour. Do not rewrite unrelated sections.

Common documentation targets:
- `CLAUDE.md` — operator/agent instructions (update only if behaviour changes affect how agents work)
- `docs/*.md` — reference documentation
- Inline docstrings — only if the function signature or behaviour changed

## Align With Implementation, Not Spec

Document what the code actually does, not what the spec said it would do. If they differ, note the gap in `risks`.

## Do Not Change Runtime Code

Documentation beads must not modify `.py` files. If you find a bug while documenting, create a sub-bead.

## Verdict Rules

- `verdict=approved`: documentation is complete and accurate
- `verdict=needs_changes`: implementation must land first, or a gap cannot be filled without code changes — include `block_reason`
```

---

### `role/planner-decomposition`

**Assigned to**: planner agents

Replace with:

```markdown
---
name: planner-decomposition
description: How to decompose a spec into a well-ordered bead graph.
---

# planner-decomposition

## Bead Sizing

Each developer bead should be completable in ~10 minutes of implementation work. Heuristics:
- More than 2–3 functions changed → split into dependent beads
- Work spans multiple subsystems → split
- Mix of refactor + feature work → split

Err toward smaller beads. Dependent beads run sequentially anyway; there is no cost to splitting.

## agent_type Values

Use exactly these values — no abbreviations or variations:
- `developer` — code implementation
- `tester` — test writing and validation
- `documentation` — doc updates
- `review` — review and signoff
- `planner` — planning only (rarely needed as a child bead)

## Shared Followup Beads

For features with 2+ developer beads, create **one shared** tester, documentation, and review bead — not one per developer bead. The shared bead must list all developer bead titles in its `dependencies`.

## Dependency Ordering

- Tester depends on: all developer beads it validates
- Documentation depends on: the validated developer bead set (or tester, if docs describe test outcomes)
- Review depends on: tester + documentation

Do not create circular dependencies. Every bead must eventually be reachable from the feature root.

## expected_files

Use repo-relative paths (e.g. `src/codex_orchestrator/scheduler.py`). Do not use absolute paths.
```

---

### `capability/code-edit`

**Assigned to**: developer agents

Replace with:

```markdown
---
name: code-edit
description: Precise, minimal code edits with auditable diffs.
---

# code-edit

## Use Edit, Not Bash

Always use the `Edit` tool for code changes. Never use `Bash` with `sed`, `awk`, or inline Python to rewrite files — those produce unauditable diffs.

## Read Before Editing

Always `Read` a file before editing it. The `Edit` tool requires exact `old_string` matches; reading first ensures your strings are accurate.

## Minimal Diffs

Change only what is necessary to fulfil the bead. Do not:
- Reformat code you did not change
- Rename variables unrelated to the task
- Reorganise imports unless required by the change

## Editing Large Files

For files over ~300 lines, read only the relevant section using `offset` and `limit` parameters, then make a targeted edit. Avoid reading the entire file if not necessary.

## Verify After Editing

After edits, verify the file is syntactically valid:
```
uv run python -m py_compile src/codex_orchestrator/<file>.py
```
```

---

### `capability/code-review`

**Assigned to**: review agents

Replace with:

```markdown
---
name: code-review
description: Structured code inspection methodology for correctness and risk.
---

# code-review

## Inspection Checklist

For each changed file, check:
- [ ] Does the change match the bead's acceptance criteria?
- [ ] Are edge cases handled (None, empty, out-of-range)?
- [ ] Could this break existing callers or downstream beads?
- [ ] Are error paths handled or explicitly left to propagate?
- [ ] Is the change consistent with surrounding code style and patterns?

## What to Ignore

Do not flag:
- Style preferences (variable naming, blank lines) unless they cause bugs
- Hypothetical future requirements not in the spec
- Issues in files not in `touched_files`

## Findings Format

Each finding:
```
Finding N (SEVERITY): <one-line description>. <what must change>.
```

Example:
```
Finding 1 (HIGH): `_uses_planner_owned_followups` returns True when no shared followups exist.
Add a guard: `uses_planner_owned = uses_planner_owned and any(planner_owned_followups.values())`.
```
```

---

### `capability/test-execution`

**Assigned to**: tester agents

Replace with:

```markdown
---
name: test-execution
description: Run targeted tests and report results clearly.
---

# test-execution

## Command Pattern

```
uv run python -m unittest tests.<module_name> -v
```

Never use `discover`. Always name the module explicitly.

## Reading Test Output

A passing run ends with:
```
Ran N tests in X.XXXs
OK
```

A failing run shows `FAILED (failures=N)` or `FAILED (errors=N)`. Read the traceback — it identifies the exact assertion or exception.

## Reporting Results

In your response:
- State how many tests ran and how many passed
- For each failure: test name, expected vs actual value, and file/line
- If a test was skipped, state why

## Flaky Tests

If a test fails intermittently, run it twice before reporting a defect. Note the flakiness in `risks`.
```

---

### `capability/docs-edit`

**Assigned to**: documentation agents

Replace with:

```markdown
---
name: docs-edit
description: Accurate, proportional documentation updates.
---

# docs-edit

## Edit, Don't Rewrite

Use `Edit` to make targeted changes to existing docs. Only rewrite a section if the entire section is outdated. Preserve headings, formatting, and cross-references that are still accurate.

## Factual Accuracy First

Before writing, read the relevant source file to confirm the behaviour you are documenting is correct. Do not document behaviour from memory or from the spec if the implementation differs.

## Proportionality

Documentation changes should be proportional to code changes:
- One-line code change → one-sentence doc update
- New function → new paragraph or section
- New feature → new section with examples

Do not expand documentation beyond what the change requires.
```

---

### `task/corrective-implementation`

**Assigned to**: developer agents

Replace with:

```markdown
---
name: corrective-implementation
description: How to implement a corrective fix from review or tester findings.
---

# corrective-implementation

## Read the Block Reason First

The parent bead's `block_reason` describes exactly what must be fixed. Read it before reading any code. Do not guess at what the corrective should change.

## Fix Only What Was Flagged

A corrective bead has a narrower scope than the original bead. Fix the specific finding(s) listed in `block_reason`. Do not take the opportunity to refactor or expand scope.

## Verify the Fix Addresses the Finding

After implementing, re-read the `block_reason` and confirm each point is addressed. If a finding cannot be resolved within the corrective's scope, document why in `risks` and create a new sub-bead.

## Do Not Run Tests

Test execution is the tester agent's responsibility. Verify syntax only.
```

---

### `task/corrective-bead-creation`

**Assigned to**: review agents

Replace with:

```markdown
---
name: corrective-bead-creation
description: When to create a corrective bead and how to specify it.
---

# corrective-bead-creation

## When to Create a Corrective Bead

Create a corrective bead when:
- A HIGH or MEDIUM finding requires code changes
- The fix is clearly scoped and actionable

Do not create a corrective bead for:
- LOW findings (note them in `risks` instead)
- Findings that require architectural decisions — block and escalate instead

## How to Specify the Corrective Bead

In `new_beads`, include:
- `title`: "Fix: <one-line description of the finding>"
- `description`: the exact finding text plus what must change
- `agent_type`: `developer`
- `dependencies`: the bead being corrected

The description must be specific enough that a developer can implement the fix without re-reading the review.
```

---

### `task/defect-bead-creation`

**Assigned to**: tester agents

Replace with:

```markdown
---
name: defect-bead-creation
description: When to create a defect bead and how to specify it.
---

# defect-bead-creation

## When to Create a Defect Bead

Create a defect bead when a test failure cannot be fixed within tester scope (i.e. it requires production code changes).

Do not create a defect bead for:
- Test infrastructure issues you can fix inline (missing imports, fixture setup)
- Test failures caused by your own test code

## How to Specify the Defect Bead

In `new_beads`, include:
- `title`: "Fix: <one-line description of the defect>"
- `description`: the exact test failure (test name, assertion, traceback excerpt) plus the expected behaviour
- `agent_type`: `developer`
- `dependencies`: the bead being tested

Be precise. The developer fixing the defect will use this description as their primary input.
```

---

### `task/regression-triage`

**Assigned to**: tester agents

Replace with:

```markdown
---
name: regression-triage
description: How to identify, classify, and report regressions.
---

# regression-triage

## What Is a Regression

A regression is a test that passed before the current bead's changes and now fails. It is distinct from a new test that was always expected to fail.

## Triage Steps

1. Run the relevant test module
2. For each failure, read the test and the changed code to determine: did the change break this test, or was the test already broken?
3. Classify: **regression** (caused by this bead) vs **pre-existing** (existed before)

## Reporting

- Regressions: must be in `block_reason` and trigger `verdict=needs_changes`
- Pre-existing failures: note in `risks`, do not block on them (they are not this bead's responsibility)

## Do Not Skip Tests to Make the Suite Pass

If a test is genuinely broken by the current bead, report it as a finding. Do not add `@skip` decorators to hide failures.
```

---

### `task/risk-assessment`

**Assigned to**: review agents

Replace with:

```markdown
---
name: risk-assessment
description: Identify and communicate concrete risks before signoff.
---

# risk-assessment

## What Counts as a Risk

A risk is a concrete, plausible way this change could cause problems after merge:
- Breaks an existing caller not covered by tests
- Introduces a subtle behaviour change in an edge case
- Depends on undocumented external behaviour
- Reduces error visibility (swallows exceptions, adds silent fallbacks)

## What Does Not Count as a Risk

- Hypothetical future requirements
- Style preferences
- Things that are already covered by tests

## Format

Each risk: one sentence describing the scenario and one sentence on the mitigation or why it is acceptable.

If there are no concrete risks, write "No identified risks." Do not invent risks to appear thorough.
```

---

### `task/spec-intake`

**Assigned to**: planner agents

Replace with:

```markdown
---
name: spec-intake
description: How to extract implementation-ready scope from a spec document.
---

# spec-intake

## What to Extract

From the spec, identify:
1. **Objective** — one sentence: what changes and why
2. **Acceptance criteria** — the testable conditions from the spec's acceptance criteria section
3. **Files in scope** — files explicitly named or clearly implied
4. **Dependencies** — other features or beads this work depends on
5. **Out of scope** — what the spec explicitly defers

## Ambiguity

If the spec is ambiguous on a critical implementation detail, note it in the epic description. Do not invent decisions — leave them for the developer bead.

## Do Not Over-Specify

Acceptance criteria in child beads should be verifiable, not prescriptive about implementation. "The planner output schema rejects invalid agent_type values" is good. "The `build_planner_prompt` function adds an enum field to the JSON schema at line 42" is too specific.
```

---

### `task/dependency-graphing`

**Assigned to**: planner agents

Replace with:

```markdown
---
name: dependency-graphing
description: How to construct a valid dependency graph for a bead plan.
---

# dependency-graphing

## Rules

1. A bead may only depend on beads that are siblings or ancestors in the same feature tree
2. No circular dependencies — if A depends on B, B cannot depend on A
3. Developer beads that touch the same file must be ordered sequentially (file-scope conflicts prevent parallel execution)
4. Tester, documentation, and review beads must depend on the developer beads they cover

## Parallelism Opportunities

Beads with no shared files and no dependency relationship can run in parallel. Plan for this:
- Independent subsystem changes → separate developer beads with no dependency between them
- Shared file changes → sequential dependency

## Validation Check

Before finalising: trace every leaf bead to the feature root. If any bead is unreachable, it has a missing dependency or was accidentally disconnected.
```

---

### `task/migration`

**Assigned to**: developer agents

Replace with:

```markdown
---
name: migration
description: How to implement a migration safely with compatibility notes.
---

# migration

## Compatibility First

Before changing a data format, config schema, or API contract, identify all existing callers. If a caller exists that you are not updating in this bead, the migration must be backwards-compatible or you must block and create a dependency bead.

## Migration Steps

1. Identify the old format and all places it is read/written
2. Implement the new format
3. Add a compatibility shim if both old and new must coexist during rollout
4. Document the migration path in `risks` (what breaks if the migration is not run, what the rollback path is)

## Do Not Silently Discard Data

If migrating storage, ensure existing data is either migrated or explicitly rejected with a clear error. Silent data loss is a HIGH risk.
```

---

### `task/refactor-safe`

**Assigned to**: developer agents

Replace with:

```markdown
---
name: refactor-safe
description: How to refactor code without changing observable behaviour.
---

# refactor-safe

## Behaviour Preservation Is Non-Negotiable

A refactor bead must not change observable behaviour. If a behaviour change is required, it belongs in a separate bead.

## Safe Refactor Checklist

- [ ] All existing tests pass before and after (run before starting to establish a baseline)
- [ ] No public API signatures changed
- [ ] No error messages changed (they may be depended on by tests or operators)
- [ ] No log output changed in ways that would break log parsing

## Scope Discipline

Refactor only what is assigned. Do not expand the refactor to "while I'm here" changes — those create unnecessary diff noise and risk.

## When to Stop

If the refactor cannot be done without changing behaviour, stop, document the constraint in `risks`, and create a sub-bead for the behaviour change.
```

---

### `task/release-notes`

**Assigned to**: documentation agents

Replace with:

```markdown
---
name: release-notes
description: How to capture user-visible changes in release notes.
---

# release-notes

## What Belongs in Release Notes

- New user-visible features or commands
- Changed behaviour that affects existing usage
- Removed or deprecated capabilities
- Breaking changes to config, CLI flags, or output format

## What Does Not Belong

- Internal refactors with no user-visible effect
- Bug fixes to behaviour that was never documented
- Changes visible only to agents, not operators

## Format

Group by category (Added, Changed, Removed, Fixed). One line per item. Be specific about what changed, not just that something changed.

Example:
```
### Changed
- `orchestrator run` now prints a JSON summary block after each cycle showing started/completed/blocked counts.
```
```

---

### `task/spec-sync`

**Assigned to**: documentation agents

Replace with:

```markdown
---
name: spec-sync
description: How to synchronise a spec document with the merged implementation.
---

# spec-sync

## What to Sync

After implementation merges, the spec may be out of date in these ways:
- Acceptance criteria that were modified during implementation
- File paths that changed
- Behaviour that was descoped or deferred
- New constraints discovered during implementation

## How to Sync

1. Read the spec
2. Read the relevant source files and `touched_files`/`changed_files` from the bead
3. Update spec sections where the implementation diverged from the spec text
4. Add a note at the top of the spec if the implementation was significantly different from what was planned

## Do Not Rewrite the Spec's Intent

Spec-sync updates facts, not goals. The objective section of a spec should not change during sync — only implementation details.

## Move to Done When Complete

After syncing, the spec is ready to be moved to `specs/done/`. Note this in `completed`.
```

---

## Files to Modify

| File | Change |
|---|---|
| `.agents/skills/core/base-orchestrator/SKILL.md` | Replace with full content above |
| `.agents/skills/role/developer-implementation/SKILL.md` | Replace with full content above |
| `.agents/skills/role/tester-validation/SKILL.md` | Replace with full content above |
| `.agents/skills/role/reviewer-signoff/SKILL.md` | Replace with full content above |
| `.agents/skills/role/docs-agent/SKILL.md` | Replace with full content above |
| `.agents/skills/role/planner-decomposition/SKILL.md` | Replace with full content above |
| `.agents/skills/capability/code-edit/SKILL.md` | Replace with full content above |
| `.agents/skills/capability/code-review/SKILL.md` | Replace with full content above |
| `.agents/skills/capability/test-execution/SKILL.md` | Replace with full content above |
| `.agents/skills/capability/docs-edit/SKILL.md` | Replace with full content above |
| `.agents/skills/task/corrective-implementation/SKILL.md` | Replace with full content above |
| `.agents/skills/task/corrective-bead-creation/SKILL.md` | Replace with full content above |
| `.agents/skills/task/defect-bead-creation/SKILL.md` | Replace with full content above |
| `.agents/skills/task/regression-triage/SKILL.md` | Replace with full content above |
| `.agents/skills/task/risk-assessment/SKILL.md` | Replace with full content above |
| `.agents/skills/task/spec-intake/SKILL.md` | Replace with full content above |
| `.agents/skills/task/dependency-graphing/SKILL.md` | Replace with full content above |
| `.agents/skills/task/migration/SKILL.md` | Replace with full content above |
| `.agents/skills/task/refactor-safe/SKILL.md` | Replace with full content above |
| `.agents/skills/task/release-notes/SKILL.md` | Replace with full content above |
| `.agents/skills/task/spec-sync/SKILL.md` | Replace with full content above |

The `memory` skill is already well-written and should not be changed.

## Acceptance Criteria

- All 21 skill files are updated with the content specified above
- No skill file is under 15 lines after the update
- The `memory` skill is unchanged
- All existing tests pass (skill files are not tested directly, but the skill bundling path in `skills.py` must still work)
- Skill content does not duplicate guardrail template content — it complements it
