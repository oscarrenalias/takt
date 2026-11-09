---
name: Agent traceability and observability
id: spec-0657c394
description: Capture agent session transcripts into takt, normalise them into backend-neutral trace events, and analyse tool usage, command failures and spec/ADR access
dependencies: null
priority: null
complexity: null
status: planned
tags:
- observability
- telemetry
- traces
scope:
  in: null
  out: null
feature_root_id: B-05db53d6
---
# Agent traceability and observability

## Objective

takt records what goes into an agent run (the prompt) and what comes out (the structured handoff, cost and token counts), but nothing about what happens in between. There is no way to answer questions such as "did the developer look up the ADR before changing this module?", "how often do agents search for specs nobody linked?", "why did this bead take 40 turns?" or "which shell commands keep failing?". The backend CLIs already write full session transcripts, but takt neither links nor keeps them. This spec adds a traceability layer: takt captures each agent session's transcript into its own state, normalises it into a backend-neutral list of trace events, and runs analysers over it. Document access (specs and ADRs: pushed, pulled or found proactively) is the first analyser. Tool usage and command failures come with it, and the layer is designed so more analysers can be added without touching capture.

## Problems to Fix

1. **Transcripts are not linked to beads.** Claude Code writes every session to `~/.claude/projects/<encoded-cwd>/<session_id>.jsonl` (or `$CLAUDE_CONFIG_DIR/projects/...`). takt records the `session_id` of successful runs (in `metrics.session_id` of the telemetry artifact `.takt/telemetry/<bead>/<n>.json`, in `bead.metadata.telemetry_history`, and inside `response_text`), but nothing uses it to find or read the transcript.
2. **Failed attempts leave no trace link at all.** When `ClaudeCodeAgentRunner._exec_json_with_response` raises (timeout, non-zero exit, non-JSON output, no structured output), `scheduler/execution.py` builds an `AgentRunResult` with no telemetry, and `_store_telemetry` (`scheduler/finalize.py`) returns early. The failed runs are the ones that most need inspecting.
3. **Transcripts are not durable.** They live in the backend's own config directory, are shared with the operator's interactive sessions, and can be cleaned up by the backend or the operator. A trace is only useful if it still exists when someone asks.
4. **The raw format is awkward to analyse.** The Claude Code JSONL mixes session metadata, attachments, queue operations, user/assistant messages and tool results. Besides the `assistant`/`user` entries, it contains `api-request-blob` entries that repeat earlier assistant content, including `tool_use` blocks with the same id and the rewritten "wire" input (observed in session `ea83891e-…`: `cd <worktree> && ` prefixed), so naive counting double-counts tool calls. One API message is also split across several `assistant` lines with the same `message.id`, each repeating the full `usage` block, so naive summing over-counts tokens.
5. **No way to measure whether agents use specs and ADRs.** 0.1.72 pushes an ADR index and linked documents into prompts and tells agents to use `takt spec|adr show|search`. There is no way to check whether they do, or whether they fetch documents they weren't given.
6. **No aggregate view.** Operators can see cost and turns per bead (`takt telemetry`), but not tool-call counts, tool error rates, repeated commands or document access per bead, feature or agent type.

## Changes

### 1. Trace store

New module `src/agent_takt/traces/` (package) with `store.py`, `capture.py`, `normalize.py`, `analyzers.py` (or a sub-package) and `models.py`.

- Traces live under `.takt/traces/<bead_id>/`, one **trace record** per agent session, numbered `<seq>` from 1 in creation order (independent of telemetry attempt numbers):
  - `<seq>.meta.json`: `{"trace_version": 1, "bead_id", "agent_type", "feature_root_id", "spec_id", "seq", "backend", "session_id", "role", "status", "started_at", "finished_at", "outcome", "source_path", "raw_bytes", "tool_calls", "malformed_lines", "telemetry_attempt", "error"}`. `tool_calls` (count of `tool_call` events excluding `StructuredOutput`) and `malformed_lines` are filled at capture time so `trace list` never re-reads events files; both are `null` when no events were produced. `spec_id` is only set for planner records.
    - `role`: `main` for the agent run, `structured_output_retry` for the single-turn reformat call made by `_retry_structured_output`, `planner` for `propose_plan`.
    - `status`: `captured` (raw transcript copied), `missing` (the expected transcript file was not found), `unsupported` (backend has no capture support), `disabled` (capture off in config), `too_large` (transcript exceeds `trace_max_bytes`; see below), `error` (copy or normalisation failed; `error` holds the message; no events file is guaranteed).
    - `telemetry_attempt`: the telemetry artifact attempt number written for the same run, or `null` when none was written (failed runs). It is informational only: telemetry attempt numbers are not unique once `telemetry_history` reaches its cap, so nothing may use it as a key. The identity of a trace record is `(bead_id, session_id)`.
  - `<seq>.raw.jsonl`: byte-for-byte copy of the backend transcript (only when `status=captured`).
  - `<seq>.events.jsonl`: the normalised events (section 3), written at capture time.
- `TraceStore(root)` provides `new_record(...)`, `list_records(bead_id)`, `load_events(bead_id, seq)`, `delete_bead_traces(bead_id)`. Writes are atomic (tmp + replace), as in `write_telemetry_artifact`.
- `.takt/traces/` is added to `_GITIGNORE_ENTRIES` in `onboarding/scaffold.py`. `takt bead delete` removes `.takt/traces/<bead_id>/` alongside `agent-runs` and `telemetry` (`cli/commands/bead.py`).
- Config: `common.capture_traces` (bool, default `true`) and `common.trace_max_bytes` (int, default `52428800`, 50 MB). A transcript larger than the limit is not copied: the record gets `status=too_large`, `raw_bytes` set to the source size and `source_path` set, and `<seq>.events.jsonl` is still written by streaming the source file in place (so analysers work on `captured` and `too_large` records alike; no `raw.jsonl` exists for `too_large`).

### 2. Capture (Claude Code)

- `ClaudeCodeAgentRunner` generates a UUID4 before each `claude -p` invocation and passes `--session-id <uuid>`, for both the main call and `_retry_structured_output`. The session id is therefore known even if the process times out or crashes.
- Each invocation produces a `TraceSession(backend="claude", session_id, role, started_at, finished_at)`. Sessions travel **with the call**, never as runner instance state (the scheduler shares one runner instance across worker threads):
  - on return, `AgentRunResult.trace_sessions: list[TraceSession]` (new field, default empty);
  - on raise, the runner raises an exception carrying a `trace_sessions` attribute. The exception type and message seen by existing code must not change: either set the attribute on the original exception before re-raising, or use a `RuntimeError` subclass with the identical message.
- An exception carries **every** session started during that `run_bead` call: if `_retry_structured_output` raises (e.g. retry timeout), it carries both the `main` and the `structured_output_retry` sessions.
- `scheduler/execution.py` copies `getattr(exc, "trace_sessions", [])` onto the failed `AgentRunResult` it builds. Runners that don't report sessions (e.g. `tests/helpers.py:FakeRunner`) need no changes; read the field with `getattr(..., "trace_sessions", [])`.
- **Ordering (inside finalisation, `scheduler/finalize.py`, for every outcome including failures and the recovery-completion path for the recovery bead's own sessions):**
  1. `_store_telemetry` runs as today and returns the attempt number it wrote, or `None` if it wrote nothing.
  2. `capture_traces(bead, sessions, outcome, telemetry_attempt)` writes the trace records and returns their `seq` list.
  3. If an artifact was written and the `seq` list is non-empty, the artifact is rewritten (atomically) with `trace_refs: [seq, ...]`.
- `capture_traces` processes each session as follows:
  1. Locates the transcript: `$CLAUDE_CONFIG_DIR/projects/*/<session_id>.jsonl` if `CLAUDE_CONFIG_DIR` is set in the agent's environment, else `~/.claude/projects/*/<session_id>.jsonl` (glob, so takt never depends on Claude Code's cwd-encoding scheme).
  2. Copies it to `<seq>.raw.jsonl`, normalises it to `<seq>.events.jsonl`, and writes `<seq>.meta.json`.
  3. Appends a `trace_captured` event to `.takt/logs/events.jsonl` (`bead_id`, `agent_type`, `feature_root_id`, `seq`, `role`, `status`, `backend`, `session_id`, `tool_calls`) for every record, whatever its status.
- Capture must never change a bead's outcome. Any exception during capture is caught, recorded as `status=error` (if the meta file can be written) and as a `trace_capture_warning` execution-history entry on the bead. The bead is then finalised normally.
- The telemetry artifact's `trace_refs` field is additive; `telemetry_version` stays 1.
- **Codex:** when the bead ran on the Codex runner (`getattr(runner, "backend_name", None) == "codex"`, passed from the executor, which already holds the runner, into finalisation), the scheduler writes one record per run with `status: unsupported`, `backend: codex`, `session_id: null`, `role: main`. Runners other than Claude and Codex that report no sessions (test fakes) produce no records.
- **Planner:** `propose_plan` also passes `--session-id`. Its sessions come back as `PlanProposal.trace_sessions`, or on the raised exception when planning fails, in which case the CLI still writes the record (`outcome: failed`) before exiting. Planner traces are always stored under `.takt/traces/_planner/<seq>.*` with `bead_id: null`, `role: planner`, and `spec_id` when known, for every plan mode (dry-run, `--output`, `--write`). `--output` writes the planner `session_id` into the plan JSON (`planner_session_id`). When beads are persisted, the planner record for the exact session gets `feature_root_id` set to the new feature root: with `--write`, the session of that same run; with `--from-file`, the `planner_session_id` in the plan file (no link if the field is absent or no such record exists). `spec_id` is derived from the spec path via `spec_ref_from_path`. `trace list --feature-root` and `trace analyze --feature-root` include planner records with that `feature_root_id`. `takt bead delete` does not remove `_planner` records.
- Recovery beads are captured like any other bead.
- `--session-id` is only ever passed to new sessions; takt never combines it with `--resume`/`--continue`. If an operator adds `--no-session-persistence` to `claude.flags`, every record will be `missing`; `docs/traces.md` says so.

### 3. Normalised trace events (backend-neutral)

`normalize_claude_transcript(lines) -> list[TraceEvent]`. Each event is one JSON object per line in `<seq>.events.jsonl`:

| `kind` | Fields |
|---|---|
| `session` | `session_id`, `cwd`, `model` (if present), `started_at` |
| `assistant_text` | `ts`, `text_excerpt` (first 500 chars), `text_chars` |
| `tool_call` | `ts`, `call_id`, `tool` (e.g. `Bash`, `Read`, `Grep`, `Edit`, `Write`, `StructuredOutput`), `input`, `result_ts`, `duration_ms` (result_ts − ts, when both are known), `is_error` (bool; `false` when the result has no error flag), `result_excerpt` (first 1000 chars of the result text), `result_chars` |
| `usage` | `ts`, `message_id`, `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_creation_tokens` |

Rules:
- Only entries with top-level `type` `assistant` or `user` produce `assistant_text`, `tool_call` and `usage` events; the `session` event comes from the first entry carrying `sessionId`/`cwd`. All other entry types (`api-request-blob`, `attachment`, `queue-operation`, `session-owner`, …) are skipped, and so are malformed JSON lines (counted into the meta record's `malformed_lines`).
- Tool calls are keyed by `call_id` (the `tool_use` id). As a defensive rule, if the same id appears in more than one `assistant` entry, the first occurrence wins and later ones are ignored; `input` is that first occurrence's input. A `tool_result` with no matching call is ignored.
- One `usage` event per distinct `message.id`, taken from the first line with that id; later lines with the same id are ignored.
- `thinking` blocks are not emitted.
- Events are ordered by their position in the transcript.
- `trace_version` 1 fixes this schema. New kinds may be added later. Analysers must ignore kinds they don't know.

### 4. Analysers

`analyzers.py` defines `Analyzer` objects as a name plus a function `(events, context) -> dict`. `context` carries the bead (agent type, `linked_docs`, feature root) and the repository root. A registry lists the built-in analysers in order. Adding one means adding an entry; capture and storage don't change. Three ship in this spec.

**a. `tool_usage`:** `{"tool_calls": N, "by_tool": {tool: {"calls", "errors", "duration_ms"}}, "errors": N, "error_rate": float, "api_messages": N}`, where `api_messages` is the number of `usage` events (distinct API messages). It is deliberately not called `turns`, to avoid confusion with telemetry's `num_turns`. `StructuredOutput` is excluded from all counts.

**b. `commands`:** applies to `Bash` calls (`input.command`). It returns `{"commands": N, "failed": [{"call_id", "command_excerpt"}], "repeated": [{"command_excerpt", "count"}]}`. `repeated` lists commands issued 3 or more times with the same normalised command string (whitespace collapsed, and a leading `cd <path> && ` removed).

**c. `document_access`:** classifies every access to a spec or ADR:
- Detection:
  - `Bash` commands invoking `spec|adr (show|search|list)` through any of these prefixes: `takt`, `uv run takt`, `uv run --directory <path> takt`, `$TAKT_CMD`, `${TAKT_CMD}`, or an absolute path ending in `/takt`. Agents are told to use `$TAKT_CMD` (`prompts.py`, `templates/agents/*.md`), so that form is the main path. A command line may contain several invocations (`&&`, `;`, `|`); each counts. IDs come from `show` arguments (prefixes allowed, resolved through the document store) and the query from `search` arguments;
  - `Read` calls on a path under `specs/` or `adr/`. Absolute paths are first made relative to the session `cwd` (from the `session` event), else to the main checkout root; paths outside both are ignored;
  - `Bash` commands that read such a path (`cat`, `sed -n`, `head`, `grep` with the path as an argument);
  - `Grep`/`Glob` calls whose path is under `specs/` or `adr/`.
- Each access resolves to a document id where possible:
  - IDs and prefixes, via the existing resolvers in `documents/`;
  - ADR paths: the ID is parsed from the filename (`adr-<8hex>-….md` → `ADR-<8hex>`, case-insensitive);
  - spec paths: by the `id` in the file's frontmatter if the file exists in the session cwd or main checkout, else by filename stem across lifecycle folders in the main checkout (via `doc_refs` path matching); an ambiguous stem is `unresolved`.
  - An access that cannot be resolved records `doc_id: null` and keeps the raw target.
- Classification, relative to the bead:
  - `linked`: the document is in the bead's `linked_docs` (after reference normalisation in `doc_refs.py`), so it was pushed into the prompt;
  - `proactive`: the document is not linked to the bead, including ADRs that appeared only in the pushed ADR index;
  - `search`: a `search` or `list` call (no single document; the query and the result count, if parseable, are recorded);
  - `unresolved`: a spec/ADR path or ID that does not resolve.
- Output: `{"accesses": [{"call_id", "method": "cli_show"|"cli_search"|"cli_list"|"read"|"shell_read"|"grep", "doc_type": "spec"|"adr"|null, "doc_id", "target", "class"}], "counts": {"linked": N, "proactive": N, "search": N, "unresolved": N}, "linked_not_accessed": [doc_id, ...]}`.
- Accesses made by tests or by the code under change are counted the same way. The analyser does not try to infer intent; that is a known limitation, recorded in the docs.

Analyser output is computed on demand from `<seq>.events.jsonl` and never persisted, so analyser changes apply retroactively.

### 5. CLI: `takt trace`

New command group `takt trace` (`cli/commands/trace.py`, registered in `cli/parser.py` and dispatch):

```
takt trace list [--feature-root ID] [--bead ID] [--status S] [--plain|--json]
takt trace show <bead_id> [--seq N] [--kind tool_call] [--errors] [--full] [--plain|--json]
takt trace analyze (<bead_id> | --feature-root ID) [--analyzer NAME ...] [--by-agent] [--json]
takt trace backfill [--feature-root ID] [--bead ID] [--dry-run]
```

- `list`: one row per trace record (bead, seq, agent type, role, status, tool calls, started_at).
- `show`: prints the normalised events of one trace, by default the latest `main` trace of the bead. Default output is one line per event (`[ts] tool_call Bash  ✗  <input excerpt>`); `--full` shows full inputs and result excerpts; `--json` emits the events as a JSON array.
- `analyze`: runs the analysers over one bead's traces, or over all beads in a feature tree. With `--feature-root` it prints totals plus a per-bead table; `--by-agent` groups by agent type. `--json` emits `{"beads": {bead_id: {analyzer: result}}, "totals": {...}}`. The totals sum counts and concatenate lists; `error_rate` is recomputed.
- `backfill`: for each telemetry artifact, takes the session id from `metrics.session_id`, falling back to a `"session_id"` value inside `response_text`. If no record with that `(bead_id, session_id)` exists, it locates the transcript as in section 2 and captures it (`role: main`, `telemetry_attempt` set from the artifact). It reports `captured`, `missing` and `skipped` counts. `--dry-run` reports without writing. It is idempotent: sessions that already have a record are skipped. Planner sessions are not backfilled.
- Partial bead IDs resolve like the other bead commands. Exit code is 1 when the bead does not exist, 0 otherwise. "No traces" is not an error: it prints a notice and exits 0.

### 6. Integration with existing surfaces

- `takt audit` (`audit.py`): each bead entry gains a `trace` object: `{"traces": N, "tool_calls": N, "tool_errors": N, "documents": {"linked": N, "proactive": N, "search": N}}`, or `null` when no traces exist. The Markdown report gets a short "Agent activity" line per bead and a feature-level total. Audit snapshots written by `takt merge` include it automatically.
- `takt telemetry` is unchanged. Trace data must not alter existing telemetry aggregates.
- `docs/events.md` documents `trace_captured`. A new `docs/traces.md` covers the storage layout, the event schema, the analysers, the CLI, privacy and the limitations. `CLAUDE.md` gets a short "Traces" section.

### 7. Privacy and safety

Transcripts contain prompts, file contents and command output, which may include secrets. Traces therefore stay under the gitignored `.takt/` state and are never committed, never ingested into memory or the document index, and never sent anywhere. `common.capture_traces: false` disables copying. Records are still written with `status=disabled`, so the gap is visible, but `raw.jsonl` and `events.jsonl` are not.

## Requirements

### ADDED: agent-traces / Session transcripts captured per agent run
For the Claude Code backend the scheduler SHALL copy each agent session's transcript into `.takt/traces/<bead_id>/<seq>.raw.jsonl` and write a normalised `<seq>.events.jsonl` and a `<seq>.meta.json`, for successful and failed runs alike, and capture failures MUST NOT change the bead's outcome.

Test fixtures: "a runner reporting session `X`" means a fake runner whose result (or raised exception) carries `trace_sessions=[TraceSession(backend="claude", session_id="X", role="main", ...)]`. "The Claude projects directory" is `$CLAUDE_CONFIG_DIR/projects` pointed at a temp dir.

#### Scenario: Successful run is captured
- **WHEN** a developer bead runs with a runner reporting session `S1`, and `S1.jsonl` exists under a subdirectory of the Claude projects directory
- **THEN** `.takt/traces/<bead_id>/1.meta.json` has `status: captured`, `session_id: S1`, `role: main`, and `1.raw.jsonl` is byte-identical to the source
- **AND** a `trace_captured` event with `seq: 1` is appended to `.takt/logs/events.jsonl`

#### Scenario: Timed-out run is still captured
- **WHEN** the runner raises `RuntimeError("Agent timed out after 900 seconds")` carrying session `S2`, whose transcript exists
- **THEN** the bead's block reason contains `Agent timed out after 900 seconds` as before, and a trace record with `status: captured`, `outcome: failed` and `telemetry_attempt: null` exists for `S2`

#### Scenario: Missing transcript is recorded, not fatal
- **WHEN** a run reports session `S3` but no `S3.jsonl` exists
- **THEN** the trace record has `status: missing`, the bead finishes with its normal outcome, and no exception escapes

#### Scenario: Capture disabled
- **WHEN** `common.capture_traces` is `false` and a bead runs
- **THEN** a trace record with `status: disabled` is written and no `raw.jsonl` or `events.jsonl` file exists

#### Scenario: Oversized transcript
- **WHEN** `common.trace_max_bytes` is 100 and the transcript for the reported session is 500 bytes
- **THEN** the record has `status: too_large` and `raw_bytes: 500`, no `raw.jsonl` exists, and `events.jsonl` exists

#### Scenario: Capture exception does not change the outcome
- **WHEN** the transcript copy raises `OSError` for a bead whose run completed
- **THEN** the bead is `done` as it would have been, and its execution history has a `trace_capture_warning` entry

#### Scenario: Concurrent beads keep their own sessions
- **WHEN** two beads run in the same scheduler cycle with `max_workers=2`, reporting sessions `SA` and `SB`
- **THEN** each bead has exactly one trace record, with its own session id

#### Scenario: Telemetry artifact references its traces
- **WHEN** a bead completes with telemetry and one captured session
- **THEN** its telemetry artifact has `trace_refs: [1]`

#### Scenario: Codex runs are recorded as unsupported
- **WHEN** a bead runs on the Codex runner
- **THEN** one trace record exists with `status: unsupported`, `backend: codex` and `session_id: null`

#### Scenario: Planner trace is linked on persist
- **WHEN** `takt plan --write` runs for spec `spec-aaaa1111` with a runner reporting planner session `SP`, creating feature root `B-root0001`
- **THEN** `.takt/traces/_planner/` has a record with `role: planner`, `spec_id: spec-aaaa1111` and `feature_root_id: B-root0001`, and `takt trace list --feature-root B-root0001` lists it

#### Scenario: Staged plan links the exact planner session
- **WHEN** `takt plan --output plan.json` runs with planner session `SP1`, then a dry-run plan of the same spec runs with session `SP2`, then `takt plan --from-file plan.json` creates feature root `B-root0002`
- **THEN** the record for `SP1` has `feature_root_id: B-root0002` and the record for `SP2` has `feature_root_id: null`

#### Scenario: Structured-output retry gets its own trace
- **WHEN** the main call returns prose and `_retry_structured_output` runs as a second session
- **THEN** the bead has two trace records, with roles `main` and `structured_output_retry`

### ADDED: agent-traces / Session id assigned before invocation
`ClaudeCodeAgentRunner` SHALL pass `--session-id <uuid4>` to every `claude -p` invocation it makes, and MUST make the ids available to the scheduler even when the invocation raises.

#### Scenario: Command line carries the session id
- **WHEN** `run_bead` is invoked with `subprocess.run` patched
- **THEN** the recorded command contains `--session-id` followed by a valid UUID4, and the returned `AgentRunResult.trace_sessions` holds that id with role `main`

#### Scenario: Session id survives a raise
- **WHEN** the patched `subprocess.run` raises `TimeoutExpired`
- **THEN** `run_bead` raises with the same message as before, and the exception's `trace_sessions` holds the session id

### ADDED: agent-traces / Backend-neutral normalised events
The normaliser SHALL turn a Claude Code transcript into `session`, `assistant_text`, `tool_call` and `usage` events, and MUST emit exactly one `tool_call` per `tool_use` id.

#### Scenario: Duplicate tool_use entries are collapsed
- **WHEN** a transcript has an `assistant` entry with `tool_use` id `T1` (command `git status`), an `api-request-blob` entry repeating `T1` with command `cd /w && git status`, and one `tool_result` for `T1`
- **THEN** exactly one `tool_call` event for `T1` is emitted, with `input.command` `git status`

#### Scenario: Split message usage counted once
- **WHEN** one API message `M1` is logged as 3 `assistant` lines, each with `usage.output_tokens: 499`
- **THEN** exactly one `usage` event with `message_id: M1` and `output_tokens: 499` is emitted

#### Scenario: Errors and durations are recorded
- **WHEN** a `tool_result` for call `T2` has `is_error: true`, and the call and result timestamps are 1.5 s apart
- **THEN** the `T2` event has `is_error: true` and `duration_ms: 1500`

#### Scenario: Unknown and malformed lines are skipped
- **WHEN** a transcript contains `attachment` and `queue-operation` entries and one line of invalid JSON
- **THEN** no event is emitted for them, and the meta record has `malformed_lines: 1`

### ADDED: agent-traces / Document access analysis
The `document_access` analyser SHALL classify each spec or ADR access in a trace as `linked`, `proactive`, `search` or `unresolved`, relative to the bead's `linked_docs`.

#### Scenario: Linked spec fetched by ID
- **WHEN** a bead links `spec:spec-aaaa1111` and its trace has a Bash call `uv run takt spec show spec-aaaa1111`
- **THEN** the analyser reports one access with `method: cli_show`, `doc_id: spec-aaaa1111`, `class: linked`

#### Scenario: Unlinked ADR read proactively
- **WHEN** a bead links no ADRs, its session cwd is `/w`, and its trace has a `Read` of `/w/adr/approved/adr-bbbb2222-some-title.md`
- **THEN** the analyser reports `doc_id: ADR-bbbb2222`, `method: read`, `class: proactive`

#### Scenario: Access through $TAKT_CMD
- **WHEN** a bead links no ADRs and its trace has a Bash call `$TAKT_CMD adr show bbbb22` where `ADR-bbbb2222` exists in the main checkout
- **THEN** the analyser reports `method: cli_show`, `doc_id: ADR-bbbb2222`, `class: proactive`

#### Scenario: Search is counted with its query
- **WHEN** a trace has a Bash call `takt adr search "worktree isolation"`
- **THEN** the analyser reports an access with `method: cli_search`, `class: search` and the query `worktree isolation`

#### Scenario: Linked document never accessed
- **WHEN** a bead links `spec:spec-aaaa1111` and its trace has no access to it
- **THEN** `linked_not_accessed` contains `spec-aaaa1111`

### ADDED: agent-traces / Tool usage and command analysis
The `tool_usage` and `commands` analysers SHALL report per-tool call, error and duration counts excluding `StructuredOutput`, and SHALL list Bash commands that failed and commands repeated 3 or more times after normalisation.

#### Scenario: Tool counts exclude StructuredOutput
- **WHEN** a trace has 3 Bash calls (one an error), 2 Read calls and 1 StructuredOutput call
- **THEN** `tool_usage` reports `tool_calls: 5`, `errors: 1`, `by_tool.Bash.calls: 3` and no `StructuredOutput` key in `by_tool`

#### Scenario: Repeated command detected through cd prefix
- **WHEN** a trace runs `pytest tests/x.py` twice and `cd /w && pytest tests/x.py` once
- **THEN** `commands.repeated` contains `pytest tests/x.py` with `count: 3`

### ADDED: agent-traces / Trace CLI
`takt trace` SHALL provide `list`, `show`, `analyze` and `backfill` subcommands over the trace store, and `analyze --feature-root` MUST aggregate analyser results across every bead in the feature tree.

#### Scenario: Show the latest trace
- **WHEN** a bead has trace records 1 and 2 (both `main`) and `takt trace show <bead>` is run
- **THEN** the events of record 2 are printed one per line, and the command exits 0

#### Scenario: Feature-level analysis
- **WHEN** two beads in feature `F` each have a trace with 2 proactive document accesses, and `takt trace analyze --feature-root F --json` is run
- **THEN** the JSON has both beads under `beads` and `totals.document_access.counts.proactive: 4`

#### Scenario: Backfill from existing telemetry
- **WHEN** a bead has a telemetry artifact with `metrics.session_id: S9`, `S9.jsonl` exists, and the bead has no record for `S9`
- **THEN** `takt trace backfill --bead <id>` creates a captured record with `telemetry_attempt` set and reports `captured: 1`
- **AND** running it again reports `captured: 0, skipped: 1`

#### Scenario: Bead without traces
- **WHEN** `takt trace show <bead>` is run for an existing bead with no trace records
- **THEN** it prints a notice that no traces exist and exits 0

### ADDED: agent-traces / Traces in the feature audit
The feature audit SHALL include, per bead, a `trace` summary of tool calls, tool errors and document-access counts, or `null` when the bead has no traces.

#### Scenario: Audit includes agent activity
- **WHEN** `build_feature_audit` runs for a feature where one bead has a captured trace with 4 tool calls and 1 proactive ADR access, and another bead has no traces
- **THEN** the first bead's entry has `trace.tool_calls: 4` and `trace.documents.proactive: 1`, and the second has `trace: null`

### ADDED: agent-traces / Trace lifecycle and hygiene
`.takt/traces/` SHALL be gitignored for new projects, and `takt bead delete` MUST remove the bead's trace directory.

#### Scenario: Delete removes traces
- **WHEN** a bead with `.takt/traces/<bead_id>/` is deleted with `takt bead delete`
- **THEN** that directory no longer exists

#### Scenario: Scaffold ignores traces
- **WHEN** `update_gitignore` runs on a new project
- **THEN** `.gitignore` contains `.takt/traces/`

## Files to Modify

| File | Change |
|---|---|
| `src/agent_takt/traces/` (new) | Trace store, capture, Claude transcript normaliser, analysers registry and the three analysers, models |
| `src/agent_takt/runner.py` | `--session-id` on every `claude -p` call (main, retry, planner); `trace_sessions` on results and raised exceptions |
| `src/agent_takt/models.py` | `AgentRunResult.trace_sessions` |
| `src/agent_takt/scheduler/execution.py` | Copy `trace_sessions` from a raised exception onto the failed `AgentRunResult` |
| `src/agent_takt/scheduler/finalize.py` | `_store_telemetry` returns the attempt; call `capture_traces` after it for every outcome (and in the recovery-completion path); rewrite the artifact with `trace_refs`; Codex `unsupported` records |
| `src/agent_takt/storage.py` | `write_telemetry_artifact` accepts optional `trace_refs` |
| `src/agent_takt/planner.py` / `cli/commands/misc.py` | Capture the planner session |
| `src/agent_takt/config.py` | `common.capture_traces`, `common.trace_max_bytes` |
| `src/agent_takt/cli/commands/trace.py` (new), `cli/parser.py`, `cli/__init__.py` | `takt trace list/show/analyze/backfill` |
| `src/agent_takt/cli/commands/bead.py` | Remove `.takt/traces/<id>/` on delete |
| `src/agent_takt/audit.py` | Per-bead and feature `trace` summary in JSON and Markdown |
| `src/agent_takt/onboarding/scaffold.py` | `.takt/traces/` in `_GITIGNORE_ENTRIES` |
| `docs/traces.md` (new), `docs/events.md`, `docs/audit.md`, `CLAUDE.md` | Documentation |
| `tests/` | Normaliser fixtures (including duplicate tool_use and malformed lines), capture with fake transcripts, analysers, CLI, audit, delete, gitignore |

## Acceptance Criteria

- Every `claude -p` invocation made by takt carries `--session-id <uuid4>`. Session ids travel on `AgentRunResult.trace_sessions` or on the raised exception, never as shared runner state, and existing exception messages are unchanged.
- Capture runs during finalisation after `_store_telemetry`, and the telemetry artifact gets `trace_refs`.
- Each agent session (main and structured-output retry, successful or failed, including recovery beads and the planner) produces a trace record under `.takt/traces/`. Its status is one of `captured`, `too_large`, `missing`, `unsupported`, `disabled` or `error`. The record identity is `(bead_id, session_id)`.
- A captured record's `raw.jsonl` is byte-identical to the backend transcript, and its `events.jsonl` follows the schema in section 3.
- Trace capture errors never change a bead's outcome, status or telemetry; they appear as `trace_capture_warning` history entries.
- One `tool_call` event per `tool_use` id and one `usage` event per `message.id`; `api-request-blob` and other non-message entries produce no events. `StructuredOutput` is excluded from analyser counts.
- `document_access` recognises the `$TAKT_CMD`, `${TAKT_CMD}`, `takt`, `uv run [--directory P] takt` and absolute `/takt` invocation forms, and makes absolute paths relative to the session cwd or main checkout.
- `document_access` classifies accesses as `linked`, `proactive`, `search` or `unresolved`, and lists `linked_not_accessed`.
- `takt trace list/show/analyze/backfill` behave as described in section 5. `analyze --feature-root` aggregates across the feature tree and supports `--by-agent`.
- `takt trace backfill` captures historical transcripts using `metrics.session_id` (falling back to `response_text`) and is idempotent on `(bead_id, session_id)`.
- Planner sessions are captured under `.takt/traces/_planner/` and linked to the feature root on persist.
- Codex beads get records with `status: unsupported`, and nothing else about Codex runs changes.
- `takt audit` and merge audit snapshots include the per-bead `trace` summary. `takt telemetry` output is unchanged.
- `.takt/traces/` is in the scaffold gitignore entries, and `takt bead delete` removes the bead's traces.
- Traces are never committed, ingested into memory or the document index, or sent to any external service.
- `docs/traces.md` exists, and `docs/events.md` documents `trace_captured`.
- The full test suite passes.

## Pending Decisions

- ~~Copy transcripts or only reference them?~~ **Resolved: copy** into `.takt/traces/`, so traces outlive backend cleanup.
- ~~Scope: document access only, or a general trace layer?~~ **Resolved: a general traceability layer**, with document access as one of three initial analysers.
- ~~Codex support~~ **Resolved: deferred.** The event schema is backend-neutral, but this spec implements only the Claude Code capture and normaliser. Codex records are written with `status: unsupported`. A follow-up spec adds a Codex adapter once its session log format has been checked against a real run.
- ~~Persist analyser results?~~ **Resolved: no.** They are computed on demand from `events.jsonl`, so analyser improvements apply to past traces.
- ~~Retention / pruning of old traces~~ **Resolved: out of scope.** Traces are removed only by `takt bead delete`; `trace_max_bytes` bounds single files. A prune command can follow if disk use becomes a problem.
- ~~Fleet-level aggregation (`takt-fleet trace ...`)~~ **Resolved: out of scope**; a follow-up once per-project traces exist.
