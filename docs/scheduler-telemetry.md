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
  ├─ Tier 2: write full artifact to .orchestrator/telemetry/
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

The cap applies only to the in-bead `telemetry_history` list. Full artifact files in `.orchestrator/telemetry/` are not pruned by this mechanism.

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

## Key Optimization Signals

The telemetry data captured by the scheduler provides several signals useful for tuning agent performance and controlling costs.

| Signal | Metric(s) | What it reveals |
|---|---|---|
| Cost per bead | `cost_usd` | Direct spend; compare across agent types or bead complexity |
| Execution time | `duration_ms`, `duration_api_ms` | Wall-clock vs API time; large gaps suggest subprocess overhead |
| Token efficiency | `input_tokens`, `output_tokens` | Token consumption per attempt; rising values may indicate prompt bloat |
| Cache utilization | `cache_creation_tokens`, `cache_read_tokens` | High read-to-creation ratio indicates effective caching |
| Agent looping | `num_turns` | Unusually high turn counts suggest the agent is stuck or retrying |
| Prompt size | `prompt_chars`, `prompt_lines` | Detects prompt growth over time; useful for template tuning |
| Stop reason | `stop_reason` | `"end_turn"` is normal; other values (`"max_tokens"`, `"tool_use"`) may need investigation |
| Permission blocks | `permission_denials` | Non-empty values indicate tool access misconfiguration |
| Retry patterns | `telemetry_history` length, attempt numbers | Multiple attempts on one bead signal flaky execution or transient failures |
