# agent-traces

## Requirement: Session transcripts captured per agent run
<!-- source: spec-0657c394 -->
For the Claude Code backend the scheduler SHALL copy each agent session's transcript into `.takt/traces/<bead_id>/<seq>.raw.jsonl` and write a normalised `<seq>.events.jsonl` and a `<seq>.meta.json`, for successful and failed runs alike, and capture failures MUST NOT change the bead's outcome.

Test fixtures: "a runner reporting session `X`" means a fake runner whose result (or raised exception) carries `trace_sessions=[TraceSession(backend="claude", session_id="X", role="main", ...)]`. "The Claude projects directory" is `$CLAUDE_CONFIG_DIR/projects` pointed at a temp dir.

### Scenario: Successful run is captured
- **WHEN** a developer bead runs with a runner reporting session `S1`, and `S1.jsonl` exists under a subdirectory of the Claude projects directory
- **THEN** `.takt/traces/<bead_id>/1.meta.json` has `status: captured`, `session_id: S1`, `role: main`, and `1.raw.jsonl` is byte-identical to the source
- **AND** a `trace_captured` event with `seq: 1` is appended to `.takt/logs/events.jsonl`

### Scenario: Timed-out run is still captured
- **WHEN** the runner raises `RuntimeError("Agent timed out after 900 seconds")` carrying session `S2`, whose transcript exists
- **THEN** the bead's block reason contains `Agent timed out after 900 seconds` as before, and a trace record with `status: captured`, `outcome: failed` and `telemetry_attempt: null` exists for `S2`

### Scenario: Missing transcript is recorded, not fatal
- **WHEN** a run reports session `S3` but no `S3.jsonl` exists
- **THEN** the trace record has `status: missing`, the bead finishes with its normal outcome, and no exception escapes

### Scenario: Capture disabled
- **WHEN** `common.capture_traces` is `false` and a bead runs
- **THEN** a trace record with `status: disabled` is written and no `raw.jsonl` or `events.jsonl` file exists

### Scenario: Oversized transcript
- **WHEN** `common.trace_max_bytes` is 100 and the transcript for the reported session is 500 bytes
- **THEN** the record has `status: too_large` and `raw_bytes: 500`, no `raw.jsonl` exists, and `events.jsonl` exists

### Scenario: Capture exception does not change the outcome
- **WHEN** the transcript copy raises `OSError` for a bead whose run completed
- **THEN** the bead is `done` as it would have been, and its execution history has a `trace_capture_warning` entry

### Scenario: Concurrent beads keep their own sessions
- **WHEN** two beads run in the same scheduler cycle with `max_workers=2`, reporting sessions `SA` and `SB`
- **THEN** each bead has exactly one trace record, with its own session id

### Scenario: Telemetry artifact references its traces
- **WHEN** a bead completes with telemetry and one captured session
- **THEN** its telemetry artifact has `trace_refs: [1]`

### Scenario: Codex runs are recorded as unsupported
- **WHEN** a bead runs on the Codex runner
- **THEN** one trace record exists with `status: unsupported`, `backend: codex` and `session_id: null`

### Scenario: Planner trace is linked on persist
- **WHEN** `takt plan --write` runs for spec `spec-aaaa1111` with a runner reporting planner session `SP`, creating feature root `B-root0001`
- **THEN** `.takt/traces/_planner/` has a record with `role: planner`, `spec_id: spec-aaaa1111` and `feature_root_id: B-root0001`, and `takt trace list --feature-root B-root0001` lists it

### Scenario: Staged plan links the exact planner session
- **WHEN** `takt plan --output plan.json` runs with planner session `SP1`, then a dry-run plan of the same spec runs with session `SP2`, then `takt plan --from-file plan.json` creates feature root `B-root0002`
- **THEN** the record for `SP1` has `feature_root_id: B-root0002` and the record for `SP2` has `feature_root_id: null`

### Scenario: Structured-output retry gets its own trace
- **WHEN** the main call returns prose and `_retry_structured_output` runs as a second session
- **THEN** the bead has two trace records, with roles `main` and `structured_output_retry`

## Requirement: Session id assigned before invocation
<!-- source: spec-0657c394 -->
`ClaudeCodeAgentRunner` SHALL pass `--session-id <uuid4>` to every `claude -p` invocation it makes, and MUST make the ids available to the scheduler even when the invocation raises.

### Scenario: Command line carries the session id
- **WHEN** `run_bead` is invoked with `subprocess.run` patched
- **THEN** the recorded command contains `--session-id` followed by a valid UUID4, and the returned `AgentRunResult.trace_sessions` holds that id with role `main`

### Scenario: Session id survives a raise
- **WHEN** the patched `subprocess.run` raises `TimeoutExpired`
- **THEN** `run_bead` raises with the same message as before, and the exception's `trace_sessions` holds the session id

## Requirement: Backend-neutral normalised events
<!-- source: spec-0657c394 -->
The normaliser SHALL turn a Claude Code transcript into `session`, `assistant_text`, `tool_call` and `usage` events, and MUST emit exactly one `tool_call` per `tool_use` id.

### Scenario: Duplicate tool_use entries are collapsed
- **WHEN** a transcript has an `assistant` entry with `tool_use` id `T1` (command `git status`), an `api-request-blob` entry repeating `T1` with command `cd /w && git status`, and one `tool_result` for `T1`
- **THEN** exactly one `tool_call` event for `T1` is emitted, with `input.command` `git status`

### Scenario: Split message usage counted once
- **WHEN** one API message `M1` is logged as 3 `assistant` lines, each with `usage.output_tokens: 499`
- **THEN** exactly one `usage` event with `message_id: M1` and `output_tokens: 499` is emitted

### Scenario: Errors and durations are recorded
- **WHEN** a `tool_result` for call `T2` has `is_error: true`, and the call and result timestamps are 1.5 s apart
- **THEN** the `T2` event has `is_error: true` and `duration_ms: 1500`

### Scenario: Unknown and malformed lines are skipped
- **WHEN** a transcript contains `attachment` and `queue-operation` entries and one line of invalid JSON
- **THEN** no event is emitted for them, and the meta record has `malformed_lines: 1`

## Requirement: Document access analysis
<!-- source: spec-0657c394 -->
The `document_access` analyser SHALL classify each spec or ADR access in a trace as `linked`, `proactive`, `search` or `unresolved`, relative to the bead's `linked_docs`.

### Scenario: Linked spec fetched by ID
- **WHEN** a bead links `spec:spec-aaaa1111` and its trace has a Bash call `uv run takt spec show spec-aaaa1111`
- **THEN** the analyser reports one access with `method: cli_show`, `doc_id: spec-aaaa1111`, `class: linked`

### Scenario: Unlinked ADR read proactively
- **WHEN** a bead links no ADRs, its session cwd is `/w`, and its trace has a `Read` of `/w/adr/approved/adr-bbbb2222-some-title.md`
- **THEN** the analyser reports `doc_id: ADR-bbbb2222`, `method: read`, `class: proactive`

### Scenario: Access through $TAKT_CMD
- **WHEN** a bead links no ADRs and its trace has a Bash call `$TAKT_CMD adr show bbbb22` where `ADR-bbbb2222` exists in the main checkout
- **THEN** the analyser reports `method: cli_show`, `doc_id: ADR-bbbb2222`, `class: proactive`

### Scenario: Search is counted with its query
- **WHEN** a trace has a Bash call `takt adr search "worktree isolation"`
- **THEN** the analyser reports an access with `method: cli_search`, `class: search` and the query `worktree isolation`

### Scenario: Linked document never accessed
- **WHEN** a bead links `spec:spec-aaaa1111` and its trace has no access to it
- **THEN** `linked_not_accessed` contains `spec-aaaa1111`

## Requirement: Tool usage and command analysis
<!-- source: spec-0657c394 -->
The `tool_usage` and `commands` analysers SHALL report per-tool call, error and duration counts excluding `StructuredOutput`, and SHALL list Bash commands that failed and commands repeated 3 or more times after normalisation.

### Scenario: Tool counts exclude StructuredOutput
- **WHEN** a trace has 3 Bash calls (one an error), 2 Read calls and 1 StructuredOutput call
- **THEN** `tool_usage` reports `tool_calls: 5`, `errors: 1`, `by_tool.Bash.calls: 3` and no `StructuredOutput` key in `by_tool`

### Scenario: Repeated command detected through cd prefix
- **WHEN** a trace runs `pytest tests/x.py` twice and `cd /w && pytest tests/x.py` once
- **THEN** `commands.repeated` contains `pytest tests/x.py` with `count: 3`

## Requirement: Trace CLI
<!-- source: spec-0657c394 -->
`takt trace` SHALL provide `list`, `show`, `analyze` and `backfill` subcommands over the trace store, and `analyze --feature-root` MUST aggregate analyser results across every bead in the feature tree.

### Scenario: Show the latest trace
- **WHEN** a bead has trace records 1 and 2 (both `main`) and `takt trace show <bead>` is run
- **THEN** the events of record 2 are printed one per line, and the command exits 0

### Scenario: Feature-level analysis
- **WHEN** two beads in feature `F` each have a trace with 2 proactive document accesses, and `takt trace analyze --feature-root F --json` is run
- **THEN** the JSON has both beads under `beads` and `totals.document_access.counts.proactive: 4`

### Scenario: Backfill from existing telemetry
- **WHEN** a bead has a telemetry artifact with `metrics.session_id: S9`, `S9.jsonl` exists, and the bead has no record for `S9`
- **THEN** `takt trace backfill --bead <id>` creates a captured record with `telemetry_attempt` set and reports `captured: 1`
- **AND** running it again reports `captured: 0, skipped: 1`

### Scenario: Bead without traces
- **WHEN** `takt trace show <bead>` is run for an existing bead with no trace records
- **THEN** it prints a notice that no traces exist and exits 0

## Requirement: Traces in the feature audit
<!-- source: spec-0657c394 -->
The feature audit SHALL include, per bead, a `trace` summary of tool calls, tool errors and document-access counts, or `null` when the bead has no traces.

### Scenario: Audit includes agent activity
- **WHEN** `build_feature_audit` runs for a feature where one bead has a captured trace with 4 tool calls and 1 proactive ADR access, and another bead has no traces
- **THEN** the first bead's entry has `trace.tool_calls: 4` and `trace.documents.proactive: 1`, and the second has `trace: null`

## Requirement: Trace lifecycle and hygiene
<!-- source: spec-0657c394 -->
`.takt/traces/` SHALL be gitignored for new projects, and `takt bead delete` MUST remove the bead's trace directory.

### Scenario: Delete removes traces
- **WHEN** a bead with `.takt/traces/<bead_id>/` is deleted with `takt bead delete`
- **THEN** that directory no longer exists

### Scenario: Scaffold ignores traces
- **WHEN** `update_gitignore` runs on a new project
- **THEN** `.gitignore` contains `.takt/traces/`
