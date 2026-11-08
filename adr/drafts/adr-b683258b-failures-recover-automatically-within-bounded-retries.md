---
id: ADR-b683258b
title: Failures recover automatically within bounded retries
status: draft
created_at: 2026-11-08T15:47:40.749853+00:00
authors: ['Renalias, Oscar']
description: Transient blocks are requeued, other blocks get capped corrective beads, no-structured-output failures get one recovery bead; session/quota limits are excluded from auto-retry.
tags: [scheduler, resilience]
related_specs: [spec-0eaad5f9]
related_beads: 
supersedes: 
---
# Failures recover automatically within bounded retries

## Summary

> In the context of unattended scheduler runs where AI workers fail for reasons ranging from network blips to real defects to prose-instead-of-JSON output, facing stalled pipelines that need an operator watching and retry loops that can burn unlimited tokens, we decided to route each failure class to a specific automatic recovery path (in-place requeue for transient errors, capped `-corrective` beads for real blocks, a single `-recovery` bead for missing structured output) and to leave quota/session limits to the operator, to achieve a pipeline that heals itself without supervision, accepting extra beads in the graph and a hard escalation to a human once the budget is spent.

## Context

takt runs agents unattended through `takt run` until quiescence. Failures fall into distinct classes with different correct responses:

* **Infrastructure noise**: timeouts, connection resets, 5xx, auth hiccups. Re-running the same bead usually works.
* **Real blocks**: a review returning `needs_changes`, a failing test, an agent that cannot finish. Re-running the same bead will reproduce the failure; something has to change first.
* **No structured output**: the agent did the work, committed it to the worktree, then emitted prose instead of JSON. Before spec-0eaad5f9 the only recourse was a manual `takt retry`, which re-ran the whole agent, wasted the completed work and consumed a corrective attempt for a problem that was never a real failure.
* **Quota/session limits**: Anthropic session or usage limits (some 429 responses) that reset hours later.

A single "retry N times" policy fits none of these well, and an unbounded policy turns any persistent failure into runaway cost.

## Decision Drivers

* The pipeline must make progress without an operator watching.
* Every automatic retry path must be bounded or stop on its own.
* Work already done (commits in the worktree) must not be redone.
* A persistent failure must end in a clear escalation, not a silent loop.

## Considered Options

### Option A — Per-failure-class recovery paths with explicit budgets

* Good: each class gets the cheapest fix that can work: requeue for noise, a fresh corrective agent for real blocks, a tool-less JSON synthesis for missing output.
* Good: recovery beads reuse the committed diff and prose (`.takt/agent-runs/<bead_id>/stdout.txt`, `git diff HEAD`) instead of re-running the agent.
* Good: budgets are configuration (`scheduler.max_corrective_attempts`, `scheduler.transient_block_patterns`), not code.
* Bad: more bead types (`corrective`, `recovery`) and suffixes for operators to understand.

### Option B — Manual retry only

* Good: nothing happens without an operator decision; simplest to reason about.
* Bad: the pipeline stalls until someone notices; this was the situation spec-0eaad5f9 fixed.

### Option C — Uniform automatic retry of the same bead, N times

* Good: one mechanism.
* Bad: re-running an unchanged bead after a real block reproduces the failure; re-running after missing JSON redoes completed work.

### Option D — Treat quota/session limits as transient

* Good: no manual step once the window resets.
* Bad: rapid requeues cannot outlast a limit that resets hours later; they burn cycles and corrective budget. Rejected in the takt operator skill (`.claude/skills/takt/SKILL.md`).

## Decision

**Failures are recovered automatically, by failure class, within explicit budgets:**

* **Transient errors.** A blocked bead whose `block_reason` contains any `scheduler.transient_block_patterns` entry (default: `high demand`, `timeout`, `timed out`, `connection reset`, `service unavailable`, `unauthorized`, …) is requeued in place by `Scheduler._reevaluate_blocked` (`scheduler/core.py`, event `retried`). It does not create a corrective or consume corrective budget.
* **Real blocks.** Other blocked beads get a `-corrective` child (`_create_corrective_bead` in `scheduler/followups.py`; immediately for `review`/`tester` `needs_changes`). The parent is requeued after each corrective completes. Once `scheduler.max_corrective_attempts` (default 5) correctives exist, `_escalate_blocked_bead` sets `needs_human_intervention` and logs `escalated`. Correctives never spawn correctives, and they follow a strict scope guardrail.
* **No structured output.** When a failure's `block_reason` contains `NO_STRUCTURED_OUTPUT_SENTINEL`, `BeadFinalizer._create_recovery_bead` creates one `{bead_id}-recovery` bead (`agent_type`/`bead_type` `recovery`, `recovery_for` set). It does not consume a corrective slot (`_can_plan_corrective` returns false while the recovery is pending). On success, `_handle_recovery_completion` applies the synthesised handoff to the original bead and continues normal followups. A recovery bead that itself produces no structured output never spawns a second recovery; the original stays blocked for the operator. `takt retry` refuses while a recovery bead is pending.
* **Quota/session limits** are deliberately **not** in `transient_block_patterns`. The operator deletes any spurious correctives and runs `takt retry` after the window resets.

## Consequences

### Positive

* Infrastructure noise and missing JSON no longer need an operator.
* Completed work survives a formatting failure; recovery costs one tool-less JSON synthesis instead of a full re-run.
* Persistent real failures stop at a predictable budget and show up as escalated beads.

### Negative

* Transient requeues have no per-bead counter; they are bounded only by the run's `--max-cycles` (default 50), so an overly broad pattern can loop a bead within a run.
* Session limits still need manual cleanup and the correctives they spawn can consume budget until the operator intervenes. A deferrable-failure class with `not_before` scheduling is the intended fix but does not exist yet.
* Corrective and recovery beads add entries to listings, graphs and audit reports.
* Substring matching on `block_reason` is coarse: a pattern like `timeout` also matches agent timeouts on oversized beads.
