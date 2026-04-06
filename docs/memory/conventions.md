---
name: Conventions
description: Project conventions for bead orchestration
type: project
---

# Conventions

## Bead IDs

Bead IDs use the format `B-{8 hex chars}`. Child beads append suffixes:
`B-abc12def-test`, `B-abc12def-review`, `B-abc12def-docs`.

## Running Commands

All commands must be run from the project root. Never run commands from inside a
worktree unless the bead assignment explicitly requires it.

## Memory Append-Only Rule

New memory entries are appended; existing entries are never edited in place unless
explicitly correcting an error. This preserves the audit trail.

## Feature Branches

Each feature has a dedicated branch `feature/{feature-root-id-lowercase}` and a
worktree at `.takt/worktrees/{feature-root-id}`.

## Bead Lifecycle

Beads move through: `open` → `ready` → `in_progress` → `done` | `blocked` | `handed_off`.
Only the scheduler transitions beads out of `in_progress`. Do not manually mark a
developer bead `done` — use `takt merge` after work is complete.
