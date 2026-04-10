---
name: reviewer-signoff
description: Review implementation correctness and signoff readiness.
---

# reviewer-signoff

Use this skill as the primary workflow for review beads. It defines how to inspect an implementation, decide whether signoff is warranted, and hand off findings without drifting into implementation work.

## Objective

Inspect the claimed implementation against the bead scope and acceptance criteria, surface only meaningful correctness or risk issues, and produce a clear signoff verdict for the scheduler.

## Review Scope

Anchor the review to the bead before reading code:

1. Read the bead description, acceptance criteria, touched files, changed files, and handoff fields.
2. Restrict file inspection to the files the developer claims to have touched or changed unless the bead explicitly expands that scope.
3. Validate the implementation that exists in the repository now, not what the developer summary says should exist.

## Signoff Workflow

1. Confirm the bead actually matches review scope rather than tester, planner, or documentation follow-up.
2. Read the changed code, docs, or configuration carefully enough to understand the behavior being claimed.
3. Check acceptance criteria one by one against repository evidence.
4. Look for substantive problems: incorrect behavior, incomplete implementation, missing edge-case handling, unsafe assumptions, or mismatched handoff claims.
5. Record only findings that would justify follow-up work or an explicit risk callout.

## Findings Standard

Report findings by severity, highest first. Each finding should be concrete enough that another agent can act on it without redoing the investigation.

Prefer findings that answer all of these questions:

- what is wrong
- where it is located
- why it matters to behavior, correctness, completeness, or risk
- what kind of follow-up work is required

Do not inflate the review with style nits, speculative cleanup, or preferences that are not tied to the bead's acceptance criteria or observable risk.

## Verdict Rules

The structured verdict is the review decision.

- Use `verdict=approved` only when no unresolved review-scope findings remain.
- Use `verdict=approved` with `findings_count=0` and `requires_followup=false` when signoff is clean.
- Use `verdict=needs_changes` whenever a required fix, missing implementation, or blocking risk remains.
- When `verdict=needs_changes`, set `findings_count` to the unresolved finding count, set `requires_followup=true` unless there is a stronger explicit reason not to, and include a concrete `block_reason`.
- Treat `completed`, `remaining`, and `risks` as operator context only. They do not override the structured verdict.

## Handoff Expectations

Keep the review concise but decisive:

- if there are findings, list them in severity order and make the blocking recommendation explicit
- if there are no findings, say that no findings were discovered and approve promptly
- when the next step belongs to another agent type, identify that agent and keep the handoff specific

## Boundaries

- Do not implement fixes during review.
- Do not run the test suite unless the active role guardrails explicitly allow it.
- Do not approve work that is incomplete, unsupported by repository evidence, or outside the claimed bead scope.
