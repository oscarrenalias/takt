---
name: Bead Prioritization
id: spec-91e606be
description: Allow users to flag beads as high priority so the scheduler processes them before other eligible beads
dependencies:
priority: medium
complexity: small
status: draft
tags: [scheduler, cli]
scope:
  in: priority field on Bead model, CLI setter, scheduler ordering within eligible pool
  out: cross-dependency priority promotion, priority inheritance by child beads, planner-assigned priority
feature_root_id:
---
# Bead Prioritization

## Objective

Users have no way to signal urgency on a specific bead. When a hotfix or critical blocker needs to jump the queue, the scheduler currently processes eligible beads in creation order. A `priority` field on beads lets operators mark specific beads as high-priority so the scheduler picks them before other eligible beads in the same cycle, without bypassing dependency rules.

## Problems to Fix

1. The scheduler selects eligible beads in creation-timestamp order with no way to express urgency. A hotfix bead created after 200 other beads will wait behind all of them.
2. There is no CLI surface to mark a bead as urgent after it has been created.

## Changes

### 1. `models.py` — Add `priority` field to Bead

Add an optional `priority: str | None = None` field to the `Bead` dataclass. Valid values: `"high"`, `"normal"` (default, equivalent to `None`). No other values are accepted. Serialises as a nullable string in JSON; missing/null values deserialise as `None` (treated as `"normal"`).

### 2. `scheduler.py` — Sort eligible beads by priority before selection

In the bead selection logic, after filtering to the eligible pool (status `ready`, dependencies met, no conflicts), sort candidates so `priority == "high"` beads come first, then `"normal"`/`None` beads — preserving creation-timestamp order within each tier. Priority does not override dependency resolution; a high-priority bead still waits for its dependencies.

### 3. `cli.py` — `takt bead set-priority <id> <high|normal>`

Add a `takt bead set-priority <bead_id> <priority>` subcommand. Accepts `high` or `normal`. Rejects invalid values with a clear error. Prints confirmation on success.

### 4. `cli.py` — `--priority <high|normal>` on `takt bead create`

Add optional `--priority` flag to `takt bead create`. Defaults to `normal` (stored as `None`).

### 5. `cli.py` — Show priority in `takt bead show` and `takt bead list --plain`

- `bead show`: include `priority: high` line only when priority is `high` (omit when normal to reduce noise).
- `bead list --plain`: add a `PRIORITY` column; show `high` or blank for normal.

## Files to Modify

| File | Change |
|---|---|
| `src/agent_takt/models.py` | Add `priority: str \| None = None` to `Bead` dataclass |
| `src/agent_takt/scheduler.py` | Sort eligible pool: high-priority first, then creation order |
| `src/agent_takt/cli.py` | `bead set-priority` subcommand, `--priority` on `bead create`, display in `bead show` and `bead list` |

## Acceptance Criteria

- `takt bead create --priority high --title "..." --agent developer --description "..."` creates a bead with `priority=high`
- `takt bead set-priority <id> high` sets priority; `takt bead set-priority <id> normal` clears it
- `takt bead set-priority <id> urgent` returns a non-zero exit code with a clear error message
- `takt bead list --plain` shows `high` in the PRIORITY column for high-priority beads; blank for normal
- `takt bead show <id>` includes `priority: high` for high-priority beads; omits the line for normal beads
- Scheduler selects a high-priority ready bead before a normal-priority ready bead created earlier, when both are eligible in the same cycle
- Existing beads without a `priority` field in JSON deserialise without error and behave as `normal`

## Pending Decisions

- Should `--priority` also be supported on `takt bead list` as a filter (e.g. `takt bead list --priority high`)? Low value initially but consistent with the labels filter pattern.
- Should the planner be able to assign priority to beads it creates? Out of scope for now — planner output schema would need updating.
