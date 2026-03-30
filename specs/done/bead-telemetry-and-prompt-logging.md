# Bead Execution Telemetry

## Objective

Capture execution telemetry for every bead run so operators can track token consumption, cost, duration, and prompt size — and identify optimization opportunities such as prompt bloat, agent looping, or excessive tool use.

## Why This Matters

Claude Code's `--output-format json` response includes rich telemetry (cost, tokens, duration, turns, cache stats) that the runner currently discards. Without this data it is impossible to:

- detect prompt bloat (too many input tokens for the work being done)
- identify agents that loop or waste turns
- compare cost across agent types or feature roots
- reason about whether prompt caching is effective
- audit what prompt was actually sent to the agent

Codex does not provide equivalent metadata, but wall-clock duration can be measured for any subprocess.

## Scope

In scope:

- Capture provider-supplied telemetry from Claude Code responses
- Measure wall-clock duration for all backends
- Store lightweight metrics in bead metadata (capped history)
- Store full prompt/response text in separate telemetry files (gitignored)
- Prompt size diagnostics (chars, lines)

Out of scope:

- `orchestrator usage` CLI rollup command (separate spec)
- External telemetry backends (Datadog, OpenTelemetry)
- Token estimation for Codex (Codex doesn't provide token counts)
- Real-time dashboards

## Functional Requirements

### 1. Telemetry Capture in Runner

**Both runners** — measure wall-clock duration around the subprocess call:

```python
start = time.monotonic()
proc = subprocess.run(...)
duration_ms = int((time.monotonic() - start) * 1000)
```

**Both runners** — capture prompt size before calling the subprocess:

```python
prompt_chars = len(prompt)
prompt_lines = prompt.count("\n") + 1
```

**ClaudeCodeAgentRunner** — extract telemetry fields from the JSON response envelope before discarding it:

| Field | Source in response | Purpose |
|-------|-------------------|---------|
| `cost_usd` | `total_cost_usd` | Dollar cost of this run |
| `duration_ms` | measured | Wall-clock time |
| `duration_api_ms` | `duration_api_ms` | Time in API calls only |
| `num_turns` | `num_turns` | Turn count (looping indicator) |
| `input_tokens` | `usage.input_tokens` | Fresh input tokens |
| `output_tokens` | `usage.output_tokens` | Generated tokens |
| `cache_creation_tokens` | `usage.cache_creation_input_tokens` | Tokens written to cache |
| `cache_read_tokens` | `usage.cache_read_input_tokens` | Tokens read from cache |
| `stop_reason` | `stop_reason` | Why the run ended |
| `session_id` | `session_id` | Links to Claude Code session JSONL |
| `permission_denials` | `permission_denials` | Tool permission blocks |
| `prompt_chars` | measured | Prompt size in characters |
| `prompt_lines` | measured | Prompt size in lines |
| `source` | — | Always `"provider"` for Claude Code |

**CodexAgentRunner** — minimal telemetry (no provider data):

| Field | Source | Purpose |
|-------|--------|---------|
| `duration_ms` | measured | Wall-clock time |
| `prompt_chars` | measured | Prompt size in characters |
| `prompt_lines` | measured | Prompt size in lines |
| `source` | — | Always `"measured"` for Codex |

### 2. Return Telemetry from Runner

Add an optional field to `AgentRunResult`:

```python
@dataclass
class AgentRunResult:
    # ... existing fields ...
    telemetry: dict[str, Any] | None = None
```

Backward-compatible (defaults to `None`). The scheduler does not need to understand the telemetry structure — it stores it opaquely.

### 3. Two-Tier Storage

**Tier 1: Bead metadata (lightweight, git-tracked)**

Store metrics only — no prompt/response text. Kept in `bead.metadata`:

```json
{
  "telemetry": {
    "cost_usd": 0.21,
    "duration_ms": 45000,
    "num_turns": 5,
    "input_tokens": 18000,
    "output_tokens": 800,
    "prompt_chars": 12500,
    "source": "provider"
  },
  "telemetry_history": [
    { "attempt": 1, "cost_usd": 0.15, "duration_ms": 30000, "..." : "..." },
    { "attempt": 2, "cost_usd": 0.21, "duration_ms": 45000, "..." : "..." }
  ]
}
```

- `telemetry` — latest attempt metrics (overwritten each run)
- `telemetry_history` — array of all attempt metrics, capped at N entries (default 10). When cap is exceeded, oldest entries are removed. Pruning happens after appending the new entry.

Configurable cap via environment variable `ORCHESTRATOR_TELEMETRY_MAX_ATTEMPTS` (default 10). Invalid/zero/negative values fall back to default.

**Tier 2: Telemetry artifact files (heavy, gitignored)**

Full prompt and response text stored per attempt at:

```
.orchestrator/telemetry/<bead_id>/<attempt_number>.json
```

Contents:

```json
{
  "telemetry_version": 1,
  "bead_id": "B0100",
  "agent_type": "developer",
  "attempt": 1,
  "started_at": "2026-03-30T13:13:49+00:00",
  "finished_at": "2026-03-30T13:22:29+00:00",
  "outcome": "completed",
  "prompt_text": "You are the developer agent for a multi-agent...",
  "response_text": "{\"type\":\"result\",\"structured_output\":{...},...}",
  "parsed_result": { "outcome": "completed", "summary": "..." },
  "metrics": {
    "cost_usd": 0.21,
    "duration_ms": 45000,
    "duration_api_ms": 12000,
    "num_turns": 5,
    "input_tokens": 18000,
    "output_tokens": 800,
    "cache_creation_tokens": 5500,
    "cache_read_tokens": 12500,
    "prompt_chars": 12500,
    "prompt_lines": 180,
    "stop_reason": "end_turn",
    "session_id": "a7bcb983-...",
    "permission_denials": [],
    "source": "provider"
  },
  "error": null
}
```

For failed attempts: `response_text` and `parsed_result` may be `null`, `error` must be populated with `{"stage": "...", "message": "..."}`.

**Gitignore**: add `.orchestrator/telemetry/` to `.gitignore`.

### 4. Scheduler Integration

In `_finalize()`, after processing the agent result:

1. Store lightweight metrics in bead metadata (`telemetry` + append to `telemetry_history`)
2. Write the full artifact file to `.orchestrator/telemetry/<bead_id>/`
3. If telemetry write fails, still preserve bead outcome — add a warning to `execution_history`

Telemetry must be captured for all outcomes: completed, blocked, and failed.

### 5. Attempt Numbering

Attempt number is derived from the length of `telemetry_history` + 1 at write time. This keeps numbering sequential and simple.

### 6. Key Optimization Signals

| Metric | What it answers | Action if too high |
|--------|----------------|-------------------|
| `input_tokens` | Is the prompt too large? | Reduce context files, trim guardrails |
| `cache_read_tokens / input_tokens` | Is caching effective? | Restructure prompt for cache-friendly prefixes |
| `num_turns` | Is the agent looping? | Tighten guardrails, scope test runs |
| `duration_ms` | Is the bead too slow? | Scope validation, reduce file reads |
| `cost_usd` | What does each agent type cost? | Compare spend across agent types |
| `prompt_chars` | Prompt bloat detection | Compare across bead types |
| `output_tokens` | Is the agent too verbose? | Tighten output requirements |
| `permission_denials` | Are tools being blocked? | Update `--allowedTools` |

## Acceptance Criteria

1. Every bead execution attempt stores lightweight metrics in `bead.metadata.telemetry` and `telemetry_history`
2. Every attempt writes a full artifact file to `.orchestrator/telemetry/<bead_id>/`
3. Artifact files include exact `prompt_text` and raw `response_text`
4. Claude Code runs capture all provider fields (cost, tokens, turns, cache stats, session ID)
5. Codex runs capture wall-clock duration and prompt size
6. Failed attempts still produce telemetry (with null response/parsed_result and populated error)
7. `telemetry_history` is capped at N entries (default 10)
8. `.orchestrator/telemetry/` is gitignored
9. All existing tests pass

## Files to Modify

| File | Change |
|------|--------|
| `src/codex_orchestrator/models.py` | Add `telemetry: dict | None = None` to `AgentRunResult` |
| `src/codex_orchestrator/runner.py` | Capture timing + Claude Code response fields; populate `telemetry` on result |
| `src/codex_orchestrator/scheduler.py` | Store telemetry in bead metadata + write artifact files |
| `src/codex_orchestrator/storage.py` | Add `write_telemetry_artifact()` method |
| `.gitignore` | Add `.orchestrator/telemetry/` |
