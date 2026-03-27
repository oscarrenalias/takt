# Bead Telemetry And Prompt Logging

## Objective

Add first-class telemetry for every bead execution so operators can inspect exact agent interactions and track token consumption precisely.

The system should persist prompt/response history per bead run, expose token usage at bead level, and provide lightweight rollups for cost and efficiency monitoring.

## Why This Matters

The current orchestrator can run beads and persist handoff summaries, but it does not provide enough observability into:

- what prompt was actually sent to the agent
- what the agent returned before scheduler normalization
- how many tokens each run consumed
- how token usage accumulates by bead, agent type, feature root, and plan

Without this telemetry, it is difficult to:

- audit unexpected agent behavior
- compare cost across specs
- identify prompt bloat and optimize instructions
- reason about regressions in execution efficiency

## Scope

In scope:

- persist prompt and response artifacts for each bead execution attempt
- persist token usage metrics per execution attempt and per bead aggregate
- expose telemetry in `bead show` and a new lightweight CLI reporting command
- add deterministic tests for telemetry persistence and reporting

Out of scope:

- external telemetry backends (Datadog, OpenTelemetry, etc.)
- real-time streaming dashboards
- full pricing engine for every provider/model variation
- redaction policy framework beyond basic local safeguards

## Functional Requirements

### 1. Execution Attempt Artifacts

Each bead execution attempt should create a persisted telemetry record under `.orchestrator/`.

Recommended layout:

- `.orchestrator/telemetry/<bead_id>/<attempt_id>.json`

Minimum fields per attempt:

- `telemetry_version` (start with `1`)
- `bead_id`
- `agent_type`
- `attempt_id`
- `started_at`
- `finished_at`
- `outcome` (`completed`, `blocked`, `failed`)
- `workdir`
- `model` if available
- `prompt_text` (exact prompt sent to the agent)
- `response_text` (raw model output before scheduler normalization; may be `null` on transport failures)
- `parsed_result` (the normalized JSON payload consumed by scheduler; may be `null` on parse/transport failure)
- `token_usage` object
- `error` object (nullable) for failed attempts with at least:
  - `stage` (`transport`, `execution`, `parse`, `scheduler`)
  - `message`

Failure-path rules (required):

- telemetry must still be written for failed attempts even when parsing fails
- on failed attempts:
  - `response_text` and `parsed_result` are allowed to be `null`
  - `error` must be present and non-empty
- `finished_at` must always be written (success or failure)

### 2. Token Usage Model

Each attempt record should include:

- `token_usage.prompt_tokens`
- `token_usage.completion_tokens`
- `token_usage.total_tokens`
- `token_usage.source` (`provider`, `estimated`, `unknown`)

Behavior:

- precedence order:
  1. if provider metrics are available from runner transport metadata, persist as `source=provider`
  2. otherwise compute deterministic local estimate and persist as `source=estimated`
  3. if estimation cannot run (e.g., missing prompt/response text), persist zeros and `source=unknown`
- for `source=unknown`, include `token_usage.note` with a short reason

Implementation contract for current runner:

- v1 should not require provider metrics to be available
- v1 is valid with deterministic estimation-only behavior (`source=estimated`/`unknown`) as long as source is explicit

### 3. Bead-Level Aggregation

Bead state should expose aggregate usage across attempts.

Persist in bead metadata:

- `metadata.telemetry.attempt_count`
- `metadata.telemetry.prompt_tokens_total`
- `metadata.telemetry.completion_tokens_total`
- `metadata.telemetry.total_tokens`
- `metadata.telemetry.last_attempt_id`
- `metadata.telemetry.last_outcome`

This allows `bead show` to reveal token totals without scanning raw telemetry files.

### 4. Scheduler Integration

Telemetry must be captured for every execution path:

- successful completion
- blocked outcome
- failed execution (including runner exceptions)

If telemetry write fails:

- scheduler should still preserve bead outcome when possible
- but add a warning in execution history and set a telemetry warning flag in bead metadata

### 5. CLI Visibility

`bead show` should include aggregated telemetry fields already stored on bead metadata.

Add a new read-only command for detailed usage reporting:

- `orchestrator usage`

Minimum command behavior:

- list top beads by `total_tokens`
- show totals by `agent_type`
- show totals by `feature_root_id`
- optional `--bead <id>` to show attempt-level records for one bead

Required `orchestrator usage` output shape (JSON):

- `totals`:
  - `prompt_tokens`
  - `completion_tokens`
  - `total_tokens`
- `by_bead`: array of `{bead_id, total_tokens, prompt_tokens, completion_tokens, attempt_count}`
- `by_agent_type`: array of `{agent_type, total_tokens, prompt_tokens, completion_tokens, attempt_count}`
- `by_feature_root`: array of `{feature_root_id, total_tokens, prompt_tokens, completion_tokens, attempt_count}`
- `attempts` (only when `--bead` is passed): attempt records for that bead

Determinism rules:

- sort `by_bead` by `total_tokens` desc, then `bead_id` asc
- sort `by_agent_type` by `total_tokens` desc, then `agent_type` asc
- sort `by_feature_root` by `total_tokens` desc, then `feature_root_id` asc
- `attempts` sorted by `attempt_id` asc
- empty datasets return empty arrays, not errors

### 6. Prompt Size Insight

For each attempt, persist prompt size diagnostics:

- `prompt_chars`
- `prompt_lines`
- `context_file_count`
- `context_bytes` (sum of linked context file sizes used for prompt construction)

This should help identify prompt growth even before tokenization details are perfect.

### 7. Retention Controls

Add basic retention controls to avoid unbounded growth.

Minimum v1 behavior:

- keep all aggregated bead totals
- configurable cap for raw attempt artifacts per bead (default 50)
- when cap exceeded, remove oldest raw attempt files for that bead

Configuration contract:

- default cap: `50`
- configuration source: `ORCHESTRATOR_TELEMETRY_MAX_ATTEMPTS` environment variable (optional override)
- invalid/zero/negative override values should fall back to default
- pruning should happen after successful write of the new attempt artifact
- pruning must not modify bead-level aggregated totals

## Non-Functional Requirements

- telemetry persistence must remain local and deterministic
- implementation should not require network access
- write overhead should remain small relative to agent runtime
- data format should be JSON for easy audit and tooling reuse

## Acceptance Criteria

The feature is complete when all of the following are true:

1. Every bead execution attempt writes a telemetry artifact containing prompt text, raw response text (nullable on failure), normalized response payload (nullable on failure), and token usage fields.
2. Every bead exposes aggregated token totals in `bead show`.
3. `orchestrator usage` reports per-bead/per-agent/per-feature totals using the defined deterministic JSON shape.
4. Token usage source is explicit (`provider`, `estimated`, or `unknown`).
5. Telemetry is captured for completed, blocked, and failed outcomes.
6. Tests cover telemetry write/read, failure-path nullability, aggregation updates, deterministic CLI usage output, and retention cap behavior.

## Suggested Implementation Notes

- extend `AgentRunResult` with optional raw output and token usage fields
- capture telemetry in runner where prompt and raw response are naturally available
- persist attempt artifacts through storage service methods, not ad hoc file writes in scheduler
- keep aggregation updates in one place to avoid drift between scheduler paths
- ensure prompt/response text in telemetry files remain plaintext for auditability

## Example Scenario

Given a developer bead run:

- scheduler starts bead `B0012`
- runner sends prompt, receives response, and returns parsed result plus token data
- storage writes `.orchestrator/telemetry/B0012/attempt-0001.json`
- bead metadata telemetry totals update in `B0012.json`

Given a blocked review bead:

- review returns `outcome=blocked` with unresolved findings
- telemetry record still persists prompt, response, and token usage
- `orchestrator usage --bead B0012-review` shows that blocked attempt cost

## Deliverables

- telemetry storage format and persistence for per-attempt prompt/response usage records
- bead metadata aggregation of token usage totals
- `orchestrator usage` CLI command for reporting
- tests for persistence, aggregation, blocked/failed capture, and retention behavior
