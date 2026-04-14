---
name: "Staged Plans: Single-Pass Plan Generation and Promotion"
id: spec-80cac16a
description: "Add --output and --from-file flags to takt plan so the planner LLM runs once, the operator reviews the output file, and beads are created from the saved plan without a second LLM call."
dependencies: null
priority: medium
complexity: low
status: planned
tags:
- cli
- planner
- ux
scope:
  in: takt plan command (--output and --from-file flags)
  out: "bead execution, scheduler, merge workflow, staged-plan directory management"
feature_root_id: B-ddc97b22
---
# Staged Plans: Single-Pass Plan Generation and Promotion

## Objective

`takt plan --write` currently re-runs the planner LLM, which wastes tokens and risks producing a different bead graph than the dry-run the operator already reviewed. This spec adds two flags — `--output` and `--from-file` — that together let the operator run the planner once, review (and optionally edit) the saved JSON, then persist the exact plan without a second LLM call. The operator owns the file and cleans it up; no managed staging directory is introduced.

## Problems to Fix

1. **Double LLM spend** — operators run `takt plan` (dry run) then `takt plan --write`, paying for two planner calls per feature.
2. **Non-determinism between runs** — the second call may produce a different bead graph (different IDs, different dependencies), invalidating the review.
3. **No review window** — there is no supported way to inspect or edit the plan JSON before committing it to storage.

## Changes

### Two new flags on `takt plan`

**`--output <file>`**

Runs the planner LLM exactly once. Writes the raw beads JSON to `<file>` (operator-chosen path). Prints the human-readable plan summary to stdout as usual. Does **not** persist any beads.

```bash
uv run takt plan --output plan.json specs/drafts/my-spec.md
# → prints plan summary to stdout
# → writes beads JSON to plan.json
```

**`--from-file <file>`**

Reads beads JSON from `<file>`. Passes it through the existing persistence logic (same code path as `--write`). Does **not** call the LLM. Prints the same confirmation output as `--write` today.

```bash
uv run takt plan --from-file plan.json
# → creates beads without LLM call
```

### Plan file format

The file written by `--output` and consumed by `--from-file` is the raw planner output list:

```json
[
  { "bead_id": "B-a3f19c2b", "title": "...", "agent_type": "developer", ... },
  ...
]
```

This is the same structure `--write` already persists internally. No wrapper object or metadata is added — keeping the file editable and diff-friendly.

### Typical operator workflow

```bash
# Step 1 — generate plan once, save for review
uv run takt plan --output plan.json specs/drafts/my-spec.md

# Step 2 — inspect (and optionally edit) the plan
cat plan.json

# Step 3 — persist when satisfied
uv run takt plan --from-file plan.json

# Step 4 — clean up (operator's responsibility)
rm plan.json
```

### Backwards compatibility

- `takt plan <spec>` (dry run, no flags) — behaviour unchanged.
- `takt plan --write <spec>` — behaviour unchanged; re-runs LLM as today.
- `--output` and `--from-file` are mutually exclusive with `--write` and with each other.

## Files to Modify

| File | Change |
|---|---|
| `src/agent_takt/cli/parser.py` | Add `--output <file>` and `--from-file <file>` flags to the `plan` subparser; mark them mutually exclusive with `--write` |
| `src/agent_takt/cli/commands/misc.py` | Handle `--output` (run planner, write JSON to file, print summary, exit) and `--from-file` (read JSON, call persist, print confirmation) branches in `command_plan` |
| `src/agent_takt/planner.py` | Extract `_persist_beads(bead_list, root)` helper so `--write` and `--from-file` share the same persistence code path |

## Acceptance Criteria

- `takt plan --output plan.json <spec>` runs the LLM once, writes beads JSON to `plan.json`, prints the human-readable summary to stdout, and does not create any beads.
- `takt plan --from-file plan.json` creates beads identical to what `--write` would have created from the same planner output, without calling the LLM.
- `takt plan --write <spec>` continues to work as before (no regression).
- `takt plan <spec>` (dry run) continues to work as before (no regression).
- `--output` and `--from-file` are mutually exclusive with `--write`; the CLI rejects invalid combinations with a clear error.
- Unit tests cover: `--output` writes correct JSON and creates no beads; `--from-file` creates expected beads from a pre-written file; invalid flag combinations are rejected.

## Pending Decisions

- None.
