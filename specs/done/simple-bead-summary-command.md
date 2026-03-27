# Simple Bead Summary Command

## Objective

Add a lightweight CLI command that prints a concise summary of bead progress so operators can quickly see what is done, blocked, and next.

This is intended as a small but real end-to-end feature to validate planning, implementation, testing, and review workflow.

## Why This Matters

The current CLI has detailed commands (`bead list`, `bead show`) but they are verbose for quick operational checks.

A compact summary command provides a practical daily workflow improvement and is easy to validate in tests.

## Scope

In scope:

- add `orchestrator summary` command
- report total bead counts by status
- report top priority actionable beads (ready first, then blocked)
- include optional `--feature-root <id>` filter
- add tests for command behavior and output shape

Out of scope:

- TUI integration
- historical analytics
- sorting customization beyond the default behavior

## Functional Requirements

### 1. New CLI Command

Add:

- `orchestrator summary`

Optional flag:

- `--feature-root <bead_id>` to limit summary to one feature tree

### 2. Summary Output

The command should output JSON with:

- `counts` object with totals for: `open`, `ready`, `in_progress`, `blocked`, `done`, `handed_off`
- `next_up` array (max 5) with ready beads, sorted by bead id
- `attention` array (max 5) with blocked beads, sorted by bead id

Each bead item should include:

- `bead_id`
- `title`
- `agent_type`
- `status`
- `feature_root_id`
- `block_reason` (only for blocked beads, empty string otherwise)

### 3. Filtering

When `--feature-root` is provided:

- include only beads whose resolved `feature_root_id` matches the provided id
- include the feature root bead itself when ids match

### 4. Exit Behavior

- always exit with code `0` on successful read/output
- if an unknown feature root is provided and no beads match, return empty counts/all lists rather than failing

## Non-Functional Requirements

- implementation should reuse existing storage/model logic
- output must be deterministic for stable testing
- keep command output small and operator-friendly

## Acceptance Criteria

The feature is complete when:

1. `orchestrator summary` returns deterministic JSON with status counts.
2. `next_up` includes ready beads only, up to 5, sorted by bead id.
3. `attention` includes blocked beads only, up to 5, sorted by bead id.
4. `--feature-root` correctly limits the result set.
5. tests cover default summary, blocked/ready selection, and feature-root filtering.

## Example

Given beads with mixed statuses:

- `B0003` ready
- `B0004` blocked (`block_reason` present)
- `B0005` done

Running:

- `orchestrator summary`

Should include:

- counts reflecting each status
- `next_up` containing `B0003`
- `attention` containing `B0004`

## Deliverables

- CLI command implementation for `summary`
- tests for output structure and filtering
- README usage note for the new command
