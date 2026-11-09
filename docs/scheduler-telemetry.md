# Scheduler Telemetry Integration

The scheduler captures and stores telemetry metrics for every bead execution attempt. Telemetry flows through a two-tier storage system: lightweight metrics in bead metadata (Git-tracked) and full prompt/response artifacts on disk (gitignored).

## Integration Flow

After each bead execution, `Scheduler._finalize()` calls `_store_telemetry(bead, agent_result)` before updating the bead's status. The telemetry path runs regardless of whether the bead succeeded, failed, or was blocked.

```
AgentRunner.run_bead()
  │  attaches telemetry dict to AgentRunResult
  ▼
Scheduler._finalize()
  │  stores last_agent_result in bead.metadata
  │  calls _store_telemetry()
  ▼
_store_telemetry()
  ├─ Tier 1: write lightweight metrics to bead.metadata
  │   ├─ bead.metadata["telemetry"]         (current attempt)
  │   └─ bead.metadata["telemetry_history"]  (capped list)
  ├─ Tier 2: write full artifact to .takt/telemetry/
  └─ On error: log warning, preserve bead outcome
```

If `AgentRunResult.telemetry` is `None`, the entire telemetry path is skipped silently.

## Bead Metadata Schema

### `bead.metadata["telemetry"]`

Stores lightweight metrics for the **most recent** execution attempt. Heavy fields (`prompt_text`, `response_text`) are stripped before storage. An `attempt` number is added by the scheduler.

Example:

```json
{
  "cost_usd": 0.21,
  "duration_ms": 45000,
  "duration_api_ms": 38000,
  "num_turns": 5,
  "input_tokens": 18000,
  "output_tokens": 800,
  "cache_creation_tokens": 500,
  "cache_read_tokens": 12000,
  "stop_reason": "end_turn",
  "session_id": "abc123",
  "prompt_chars": 12500,
  "prompt_lines": 340,
  "source": "provider",
  "attempt": 2
}
```

#### Model fields

Runners also record which model ran the bead:

| Field | Description |
|---|---|
| `model_requested` | Model passed via `--model` (bead `model_override`, else `config.model_for`); `null` when none was passed (backend default). For Codex this is the configured model. |
| `models` | Claude only: `{model_id: {"cost_usd", "input_tokens", "output_tokens"}}` built from the response's `modelUsage`; `{}` when absent. Omitted for Codex. |
| `model` | Primary model: the `models` key with the highest `cost_usd` (ties: highest `output_tokens`), otherwise `model_requested`. |

These fields are kept in `bead.metadata["telemetry"]`, `telemetry_history` and the artifact `metrics`.

Fields vary by runner backend. See [Runner Telemetry](multi-backend-agents.md#runner-telemetry) for per-backend field tables.

### `bead.metadata["telemetry_history"]`

An append-only list of lightweight metric snapshots, one per execution attempt. Each entry is identical in structure to `bead.metadata["telemetry"]` and includes an `attempt` field (1-based, derived from `len(history) + 1`).

```json
[
  {"attempt": 1, "cost_usd": 0.15, "duration_ms": 30000, "source": "provider", "...": "..."},
  {"attempt": 2, "cost_usd": 0.21, "duration_ms": 45000, "source": "provider", "...": "..."}
]
```

The list is capped to prevent unbounded growth (see next section).

## History Capping

The `telemetry_history` list is capped after each append. When the list exceeds the cap, only the **most recent** entries are retained (oldest are dropped).

- **Default cap:** 10 entries
- **Configuration:** `ORCHESTRATOR_TELEMETRY_MAX_ATTEMPTS` environment variable
- **Invalid values:** non-integer, zero, or negative values fall back to the default

```bash
# Keep only the 5 most recent telemetry entries per bead
export ORCHESTRATOR_TELEMETRY_MAX_ATTEMPTS=5
```

The cap applies only to the in-bead `telemetry_history` list. Full artifact files in `.takt/telemetry/` are not pruned by this mechanism.

## Failure Handling

Telemetry storage is wrapped in a `try`/`except` block. If any part of the telemetry write fails (metadata update or artifact write), the scheduler:

1. **Preserves the bead outcome** -- the bead's status transition (to `done`, `blocked`, or `failed`) proceeds normally.
2. **Records a warning** in `bead.execution_history` with event type `"telemetry_write_warning"` and the exception message.
3. **Does not retry** -- telemetry loss is accepted to avoid blocking the pipeline.

For failed bead executions, the artifact's `error` field captures the failure context:

```json
{
  "stage": "agent_execution",
  "message": "Permission denied: could not read config file"
}
```

Successful executions set `error` to `null`.

## `takt telemetry` CLI Output

The `takt telemetry` command aggregates telemetry across beads. Use `--feature-root` to scope to a single feature tree.

```bash
uv run takt telemetry                          # all beads, plain text
uv run takt telemetry --feature-root <id>      # scoped to one feature (enables per-bead table)
uv run takt telemetry --json                   # JSON output
uv run takt telemetry --feature-root <id> --json
```

### Aggregate Fields

When output as JSON, the `aggregates` object includes:

| Field | Description |
|---|---|
| `total_beads` | Total number of beads in scope |
| `done_count` | Beads with status `done` |
| `avg_wall_clock_seconds` | Mean wall-clock duration across all beads |
| `p95_wall_clock_seconds` | 95th-percentile wall-clock duration |
| `avg_turns` | Mean agent turn count |
| `total_cost_usd` | Sum of `cost_usd` across all beads that have cost data (rounded to 4 decimal places) |
| `avg_cost_usd_per_bead` | Mean `cost_usd` across beads with cost data (rounded to 4 decimal places) |

`aggregates.by_model` maps each primary model to `count`, `total_duration_ms`, `median_duration_ms`, `total_cost_usd` and `median_num_turns` (one count per telemetry artifact). For artifacts written before `metrics.model` existed, the primary model is derived from the artifact's `response_text` `modelUsage` using the same rule, then `model_requested`, then `unknown`. The plain-text report shows it as a "By model" table.

Both cost fields are `null` when no beads in scope have `cost_usd` in their telemetry metadata.

### Per-Bead JSON List

Each entry in the `beads` array includes a `cost_usd` field (`null` when unavailable):

```json
{
  "bead_id": "B-f22201b6",
  "agent_type": "developer",
  "status": "done",
  "feature_root_id": "B-f22201b6",
  "wall_clock_seconds": 120.5,
  "cost_usd": 0.0312
}
```

### Per-Bead Breakdown (Plain Text)

When `--feature-root` is provided and output is plain text, a per-bead table is appended after the aggregate summary:

```
Per-bead breakdown:
  bead_id               agent_type      status        duration   cost_usd
  -----------------------------------------------------------------------
  B-f22201b6            developer       done            120.5s    $0.0312
  B-f22201b6-test       tester          done             45.2s    $0.0118
  B-f22201b6-docs       documentation   done             22.1s    $0.0071
  B-f22201b6-review     review          done             38.4s    $0.0095
```

Beads without cost or duration data show `-` in the respective column.

## Key Optimization Signals

The telemetry data captured by the scheduler provides several signals useful for tuning agent performance and controlling costs.

| Signal | Metric(s) | What it reveals |
|---|---|---|
| Cost per bead | `cost_usd` | Direct spend; compare across agent types or bead complexity. For Claude Code, includes retry overhead when a structured-output retry occurs |
| Execution time | `duration_ms`, `duration_api_ms` | Wall-clock vs API time; large gaps suggest subprocess overhead. `duration_api_ms` includes retry time for Claude Code structured-output retries |
| Token efficiency | `input_tokens`, `output_tokens` | Token consumption per attempt; rising values may indicate prompt bloat |
| Cache utilization | `cache_creation_tokens`, `cache_read_tokens` | High read-to-creation ratio indicates effective caching |
| Agent looping | `num_turns` | Unusually high turn counts suggest the agent is stuck or retrying |
| Prompt size | `prompt_chars`, `prompt_lines` | Detects prompt growth over time; useful for template tuning |
| Stop reason | `stop_reason` | `"end_turn"` is normal; other values (`"max_tokens"`, `"tool_use"`) may need investigation |
| Permission blocks | `permission_denials` | Non-empty values indicate tool access misconfiguration |
| Retry patterns | `telemetry_history` length, attempt numbers | Multiple attempts on one bead signal flaky execution or transient failures |
