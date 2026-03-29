# Structured Review Verdict V1

## Objective

Stop using free-form text parsing to decide whether review/test beads are blocked or done.

Use structured verdict fields for control flow, while keeping free-form narrative text for operator context.

## Why This Matters

Current scheduler behavior can false-block beads when reviewers write benign phrases not covered by allowlists.

This creates unnecessary corrective loops, escalations, and token waste.

## Scope

In scope:

- structured verdict fields in worker output for `review` and `tester`
- scheduler decisions based on structured fields (not `remaining` text parsing)
- backward-compatible fallback for legacy outputs
- tests for false-block prevention

Out of scope:

- redesign of all handoff fields
- UI/TUI changes beyond showing new fields if already present

## Implementation Snapshot

The current repository already implements the verdict-first path described by this spec.

Implemented behavior in the current code:

- `src/codex_orchestrator/models.py` adds `verdict`, `findings_count`, and `requires_followup` to both `AgentRunResult` and `HandoffSummary`
- `src/codex_orchestrator/runner.py` accepts those fields in the worker JSON schema so review/test agents can return them directly
- `src/codex_orchestrator/scheduler.py` applies verdict-first control flow for `review` and `tester` beads through `_apply_review_test_verdict(...)`
- `src/codex_orchestrator/prompts.py` injects explicit structured verdict requirements into `review` and `tester` worker prompts
- `templates/agents/review.md` and `templates/agents/tester.md` require agents to emit `verdict`, `findings_count`, and `requires_followup`
- `approved` forces a non-failed review/test result onto the completion path and defaults `requires_followup` to `False` when the agent omits it
- `needs_changes` forces the bead onto the blocked path, fills in a default `block_reason` when needed, and defaults `requires_followup` to `True`
- `remaining` is still persisted into `handoff_summary`, but it is informational only for structured review/test results
- compatibility fallback is still enabled behind `REVIEW_TEST_VERDICT_COMPAT_MODE`; legacy outputs without `verdict` still use the older `remaining`-text heuristic for now
- when that fallback path is used, the scheduler appends a `compat_fallback_warning` execution-history record so operators can see that the bead did not use the structured verdict path
- `metadata["last_agent_result"]` and `handoff_summary` both preserve `verdict`, `findings_count`, `requires_followup`, and any resulting `block_reason`
- `tests/test_orchestrator.py` covers approved verdicts with narrative `remaining`, blocking `needs_changes` verdicts, and the legacy compatibility warning path

Still intentionally left in compatibility mode:

- the scheduler constant `REVIEW_TEST_VERDICT_COMPAT_MODE` remains enabled, so legacy review/test agents can still complete while prompts and downstream workflows finish migrating
- compat mode is now a temporary fallback path only; review/test prompts and templates require structured verdict output

## Functional Requirements

### 1. Structured Outcome Fields

Extend agent output schema with:

- `verdict`: `approved` | `needs_changes` (for `review` and `tester`)
- `findings_count`: integer >= 0
- `requires_followup`: boolean (optional, derived from verdict when absent)

Rules:

- `approved` means bead can complete.
- `needs_changes` means bead must block.
- `block_reason` required when `needs_changes`.

### 2. Scheduler Control Flow

For `review` and `tester` beads:

- if structured verdict exists, scheduler must use it as source of truth.
- `remaining` is informational only and must not drive blocked/done state.

For legacy outputs without verdict:

- keep existing fallback behavior temporarily (compat mode).

### 3. Backward Compatibility

Compat mode requirements:

- log an execution-history warning when fallback text-based behavior is used.
- allow a future flag to disable compat mode once all prompts/tools are migrated.

### 4. Prompt/Guardrail Updates

Update reviewer/tester guidance to require structured verdict output.

Free-form sections (`completed`, `remaining`, `risks`) remain available for narrative detail.

## Acceptance Criteria

1. Review/test beads with `verdict=approved` complete even if `remaining` contains arbitrary prose.
2. Review/test beads with `verdict=needs_changes` block with required `block_reason`.
3. Legacy output without verdict still works via compatibility path.
4. Tests cover:
   - approved + free-form remaining
   - needs_changes + findings
   - legacy fallback path
   - no false block for “no findings discovered” phrasing when verdict is approved

## Deliverables

- schema/model updates for structured verdict fields
- scheduler logic update (verdict-first)
- prompt/template updates for reviewer/tester
- regression tests for verdict-first behavior
