---
name: bead-telemetry
description: Query and interpret bead execution telemetry from the orchestrator CLI.
---

# bead-telemetry

The `orchestrator telemetry` command aggregates execution metrics across beads and renders a summary report.

## When to Use

Use this skill when asked about:
- Agent performance (which agent type is slowest, average wall-clock times, p95 durations)
- Retry and block rates (how often beads are corrected, what is blocking them)
- Feature progress (how many beads are done vs blocked for a given feature root)
- Identifying patterns across many beads (e.g. "which features have the most blocked beads?")

Do not use this skill for per-bead detail — use `orchestrator bead show <id>` for that.

## Command

```bash
uv run orchestrator telemetry [OPTIONS]
```

### Options

| Flag | Default | Description |
|------|---------|-------------|
| `--days N` | 7 | Look back N days from now |
| `--feature-root ID` | (none) | Restrict report to one feature tree |
| `--agent-type TYPE` | (none) | Filter by agent type (e.g. `developer`, `tester`) |
| `--status STATUS` | (none) | Filter by bead status (e.g. `done`, `blocked`) |
| `--json` | false | Emit raw JSON instead of the human-readable table |

### Examples

```bash
# Default: last 7 days, all beads, table output
uv run orchestrator telemetry

# Narrow to a single feature tree
uv run orchestrator telemetry --feature-root B-715e8f90

# Only blocked developer beads, last 14 days
uv run orchestrator telemetry --days 14 --agent-type developer --status blocked

# Machine-readable JSON (pipe to jq, scripts, etc.)
uv run orchestrator telemetry --json
```

## Table Output

A plain-text report with the following sections:

- **By status** — bead counts grouped by lifecycle status (`open`, `ready`, `in_progress`, `done`, `blocked`, `handed_off`)
- **By agent type** — counts per agent type
- **By feature root** — per-feature bead counts with truncated titles (suppressed when `--feature-root` is set)
- **Timing and quality metrics** — see table below

| Metric | Description |
|--------|-------------|
| Avg wall-clock | Mean elapsed seconds from first `started` event to last `completed`/`blocked` event |
| P95 wall-clock | 95th-percentile wall-clock seconds |
| Avg turns | Mean LLM conversation turns per bead (from telemetry artifact `metrics.num_turns`) |
| Retry rate | Fraction of beads that were retried at least once |
| Corrective beads | Count of `-corrective` suffix beads |
| Merge-conflict | Count of `bead_type == "merge-conflict"` beads |
| Timeout blocks | Count of blocked beads whose `block_reason` mentions "timeout" or "timed out" |
| Transient blocks | Count of blocked beads matching `config.scheduler.transient_block_patterns` |

## JSON Output Schema

```json
{
  "filters": {
    "days": 7,
    "feature_root": null,
    "agent_type": null,
    "status": null
  },
  "bead_count": 12,
  "aggregates": {
    "total_beads": 12,
    "by_status": {"done": 9, "blocked": 2, "in_progress": 1},
    "by_agent_type": {"developer": 4, "tester": 4, "review": 4},
    "avg_wall_clock_seconds": 142.3,
    "p95_wall_clock_seconds": 310.0,
    "avg_turns": 8.5,
    "retry_rate": 0.083,
    "corrective_bead_count": 1,
    "merge_conflict_bead_count": 0,
    "timeout_block_count": 1,
    "transient_block_count": 0
  },
  "feature_roots": [
    {"feature_root_id": "B-abc12345", "title": "Feature title", "bead_count": 6}
  ],
  "beads": [
    {
      "bead_id": "B-abc12345",
      "title": "...",
      "agent_type": "developer",
      "status": "done",
      "feature_root_id": "B-abc12345",
      "wall_clock_seconds": 95.2
    }
  ]
}
```

## Telemetry Artifacts

Per-bead turn counts are read from `.orchestrator/telemetry/<bead_id>/*.json`. Each artifact is expected to contain a `metrics.num_turns` integer field. Multiple artifacts per bead are summed. Beads without any artifact contribute no turn data to averages.

Wall-clock seconds are derived from `execution_history` events recorded in the bead JSON itself — no external artifact is needed.

## Limitations

- **Deleted beads are excluded.** Once a bead is deleted via `orchestrator bead delete`, it is removed from storage and will not appear in telemetry reports. Historical coverage is limited to beads still present in `.orchestrator/beads/`.
- Token usage columns show `N/A` when the runner did not capture usage data (Claude Code runner may not always populate this field).
- `--days` filtering is based on the bead's first `execution_history` entry timestamp; beads with no execution history are excluded from time-windowed queries.
