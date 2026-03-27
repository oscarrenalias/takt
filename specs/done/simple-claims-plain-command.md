# Claims List Human-Readable Output

## Objective

Add a plain-text output mode for `orchestrator bead claims` so operators can quickly inspect active claims without reading JSON.

## Scope

In scope:

- Add `--plain` flag to `orchestrator bead claims`.
- Keep JSON as default when `--plain` is not provided.
- Show one compact line per claim in plain mode.
- Add regression tests for both modes.
- Add short README usage note.

Out of scope:

- Colorized output.
- New filtering/sorting behavior.
- Changes to claim selection logic.

## Plain Output Format

One line per active claim, with fields in this order:

- bead id
- agent type
- feature root id
- status context (lease owner)

Example:

`B0042 | developer | feature=B0040 | lease=developer:B0042`

If there are no active claims, print:

`No active claims.`

## Acceptance Criteria

1. `orchestrator bead claims` continues to return JSON output exactly as today.
2. `orchestrator bead claims --plain` returns human-readable lines instead of JSON.
3. Plain mode includes at least `bead_id`, `agent_type`, and `feature_root_id` for each claim.
4. Plain mode prints `No active claims.` when no claims exist.
5. Tests verify default JSON compatibility and plain output behavior.
6. README includes one short example of `orchestrator bead claims --plain`.
