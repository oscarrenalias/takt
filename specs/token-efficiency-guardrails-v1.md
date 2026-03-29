# Token Efficiency Guardrails V1

## Objective

Reduce unnecessary token usage in orchestration without reducing correctness.

## Scope

In scope:

- planning granularity guardrails
- execution/review loop limits
- follow-up bead fan-out controls
- scoped runs and prompt-context minimization
- deterministic no-op completion behavior

Out of scope:

- model vendor pricing optimization
- external telemetry backends

## Functional Requirements

### 1. Small Feature Planning Mode

For small specs, planner should produce fewer runnable beads by default:

- one implementation bead
- optional one test bead
- one final review bead

Avoid over-decomposition unless explicitly required by spec complexity.

### 2. Follow-up Fan-Out Policy

Default policy:

- do **not** auto-create `-docs`, `-test`, `-review` for every developer bead.
- create follow-up beads only when:
  - required by acceptance criteria, or
  - explicitly requested by structured agent output.

### 3. Structured Verdict Control Flow

Review/test control flow must use structured verdict fields (approved/needs_changes), not free-form text parsing.

This prevents false blocks and repeated corrective loops.

Implementation note:

- the current scheduler already applies verdict-first handling for `review` and `tester` beads and only falls back to the legacy `remaining`-text heuristic when `REVIEW_TEST_VERDICT_COMPAT_MODE` is still enabled and a worker omits `verdict`
- that compatibility path records a `compat_fallback_warning` execution-history entry, which gives operators an audit trail for the temporary token-costlier behavior

### 4. Corrective Loop Budget

Set strict default:

- max 1 automatic corrective attempt per blocked bead
- optional override to 2 for selected roots
- after limit: escalate to human, stop auto-loop

### 5. No-Op Completion Rule

If a review/test pass reports:

- structured verdict = approved
- no required code/doc changes
- no unresolved findings

then mark bead done directly and do not create corrective/follow-up beads.

### 6. Scoped Execution Default

Runner/TUI operations should default to feature-scoped execution when a feature root is known.

Global runs remain available but should be explicit.

### 7. Prompt Context Budget

Prompt construction must limit context to:

- bead description + acceptance criteria
- expected files / linked docs
- minimal relevant handoff context

Avoid broad repo dumps by default.

### 8. Test Execution Budget

Testing order:

1. targeted tests for touched scope
2. broader suite only for final signoff or when targeted tests fail

## Acceptance Criteria

1. Small specs produce compact bead plans by default.
2. Automatic follow-up fan-out is disabled by default and policy-controlled.
3. Structured verdict logic eliminates free-text false-block loops.
4. Corrective retries stop at configured limit and escalate deterministically.
5. No-op approvals do not spawn extra beads.
6. Scoped run behavior and prompt-context constraints are covered by tests.

## Deliverables

- planner/scheduler policy updates
- structured verdict integration hooks
- config knobs for corrective limit and fan-out policy
- regression tests for loop prevention and no-op completion behavior
