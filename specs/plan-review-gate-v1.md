# Plan Review Gate V1

## Objective

Add a deterministic planning-quality loop so generated plans are automatically reviewed and revised before execution starts.

The loop should run at most two revision rounds; if still not approved, the plan is escalated for human review.

## Why This Matters

Current plans are often good, but occasionally include avoidable issues (scope ambiguity, weak dependencies, non-testable acceptance criteria).

A built-in review gate improves reliability and reduces token waste during execution.

## Scope

In scope:

- explicit planning-phase bead types (`plan`, `plan_review`, `plan_revision`)
- automated review feedback loop
- max two revision rounds
- escalation to human when review still fails
- persisted audit trail of each review/revision round

Out of scope:

- replacing feature execution beads
- human-in-the-loop UI workflow (v1 can rely on existing bead status/escalation fields)
- model-specific scoring heuristics

## Functional Requirements

### 1. Planning-Phase Bead Types

Add/support explicit bead types for planning workflow:

- `plan`: initial decomposition output
- `plan_review`: quality review of the generated plan
- `plan_revision`: planner revision task produced from review findings

These beads must be visible in normal bead list/show output and execution history.

### 2. Review Outcome Contract

`plan_review` output must be structured and include:

- `verdict`: `approved` or `needs_revision`
- `findings`: list of actionable issues (severity + rationale)
- `required_changes`: concrete changes to apply in next revision

If `approved`, execution can continue to normal feature beads.

If `needs_revision`, orchestrator must create/run a revision round automatically.

### 3. Closed-Loop Revision Flow

When verdict is `needs_revision`:

1. create a `plan_revision` bead carrying review findings
2. run planner revision
3. run `plan_review` again

Repeat until:

- approved, or
- revision round limit reached

### 4. Revision Round Limit

Set max automatic rounds to `2`.

Rules:

- first failed review -> revision round 1
- second failed review -> revision round 2
- third failed review (after round 2) -> no further auto-revisions; escalate to human

### 5. Human Escalation

On limit exhaustion:

- mark planning root with `needs_human_intervention=true`
- set `escalation_reason` summarizing unresolved findings
- keep all findings/revision summaries in execution history
- do not start feature execution beads automatically

### 6. Determinism and Safety Checks

`plan_review` must validate at least:

- dependency graph validity (no missing/invalid references)
- no contradictory or overlapping runnable scopes
- acceptance criteria are testable/verifiable
- feature container vs runnable bead semantics are consistent

Validation order and outputs should be deterministic for stable tests.

### 7. CLI and Visibility

`orchestrator plan ... --write` should report planning-phase bead progression clearly:

- created plan/review/revision beads
- current round number
- final outcome (`approved` or `escalated`)

`bead show` for planning-phase beads must expose:

- revision round metadata
- review findings payload
- escalation metadata (if any)

## Non-Functional Requirements

- no network dependency beyond existing planner/reviewer calls
- no destructive mutation of already-approved execution beads
- clear auditability in JSON bead state and execution history

## Acceptance Criteria

Feature is complete when:

1. Planning creates explicit `plan` + `plan_review` beads before execution beads are considered runnable.
2. `needs_revision` automatically triggers `plan_revision` and reruns review.
3. Automatic revision rounds stop at 2; unresolved plans are escalated to human.
4. Escalated plans are prevented from automatic execution start.
5. Tests cover:
   - approved-on-first-review path
   - one-revision approval path
   - two-revision escalation path
   - deterministic review metadata persistence

## Suggested Implementation Notes

- reuse existing blocked/corrective retry patterns where possible, but keep planning loop distinct from feature execution corrective loops
- store `plan_revision_round` in bead metadata for planning-phase beads
- avoid recursive plan-review bead spawning by explicit type guards

## Deliverables

- planner/scheduler support for `plan`, `plan_review`, `plan_revision` bead types
- automatic review/revision loop with max 2 rounds
- escalation behavior and metadata
- tests and README notes describing the planning gate behavior
