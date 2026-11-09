# Agent traces

takt can keep a normalised copy of each Claude Code session transcript that a bead (or the planner) ran, so operators can see which tools an agent called, which commands failed, and which specs and ADRs it looked at. Traces are observability data only. They never influence scheduling, verdicts or handoffs.

## Storage layout

Traces live under `.takt/traces/` in the project root (the main checkout, not the feature worktree):

```
.takt/traces/
  B-abc12def/
    1.meta.json        record metadata (always written)
    1.raw.jsonl        byte-for-byte copy of the source transcript (status=captured only)
    1.events.jsonl     normalised events, one JSON object per line
    2.meta.json
    ...
  _planner/            records for planner runs, which have no bead
```

- One record per agent session. `<seq>` starts at 1 per bead and is allocated atomically (an exclusive `os.link()` publish keeps concurrent processes from sharing a seq).
- A bead has several records when it ran more than one session, for example a `main` session plus a `structured_output_retry` session, or one record per retried attempt.
- All writes are atomic (temp file then rename).
- Deleting a bead (`takt bead delete`) removes its trace directory. `_planner/` is never deleted automatically.

### Meta file

`<seq>.meta.json` holds a `TraceRecordMeta`:

| Field | Meaning |
|---|---|
| `trace_version` | Schema version, currently `1` |
| `bead_id`, `agent_type`, `feature_root_id`, `spec_id` | Identity of the run (`bead_id` is null for planner records) |
| `seq` | Sequence number within the bead |
| `backend`, `session_id` | Backend name and its session id (`session_id` is null when unsupported) |
| `role` | `main`, `structured_output_retry` or `planner` |
| `status` | See below |
| `started_at`, `finished_at`, `outcome` | Run timing and the bead outcome, when known |
| `source_path` | Transcript file the record was read from |
| `raw_bytes` | Size of the source transcript |
| `tool_calls` | Number of tool calls in the normalised events |
| `malformed_lines` | Transcript lines that could not be parsed and were skipped |
| `telemetry_attempt` | Attempt number of the matching telemetry artifact; the artifact gains a matching `trace_refs` list |
| `error` | Failure message when `status` is `error` |

### Record statuses

| Status | Meaning | `raw.jsonl` | `events.jsonl` |
|---|---|---|---|
| `captured` | Transcript found, copied and normalised | yes | yes |
| `too_large` | Transcript exceeds `common.trace_max_bytes`; normalised events kept, raw copy skipped | no | yes |
| `missing` | The session id is known but no transcript file was found | no | no |
| `unsupported` | The backend has no trace support (Codex) or no session id | no | no |
| `disabled` | `common.capture_traces` is `false` | no | no |
| `error` | Capture failed part-way; `error` holds the reason | maybe | maybe |

Capture never raises into the scheduler. Failures become `error` records and warnings.

## Normalised event schema (`trace_version` 1)

`<seq>.events.jsonl` is backend-neutral. Each line has a `kind`:

| `kind` | Fields |
|---|---|
| `session` | `session_id`, `cwd`, `model` (when known), `started_at` |
| `assistant_text` | `ts`, `text_excerpt` (first 500 characters), `text_chars` |
| `tool_call` | `ts`, `call_id`, `tool`, `input`, `result_ts`, `duration_ms`, `is_error`, `result_excerpt` (first 1000 characters), `result_chars` |
| `usage` | `ts`, `message_id`, `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_creation_tokens` |

A `tool_call` event pairs the call with its result. Consumers must ignore kinds they do not know.

## Analysers

Analysers are pure functions `func(events, context) -> dict` registered in `REGISTRY` in `src/agent_takt/traces/analyzers/__init__.py`. They run over normalised events, never raw transcripts.

| Analyser | Output |
|---|---|
| `tool_usage` | `tool_calls`, `by_tool` (`calls`, `errors`, `duration_ms` per tool), `errors`, `error_rate`, `api_messages`. `StructuredOutput` calls are excluded. |
| `commands` | Bash `commands` total, `failed` (`call_id`, `command_excerpt`) and `repeated` (`command_excerpt`, `count`; a command run 3 or more times). A leading `cd <dir> &&` is stripped before comparing. Excerpts are 200 characters. |
| `document_access` | Which specs and ADRs the agent opened, each classified as `linked` (in the bead's `linked_docs`), `proactive` (resolved but not linked, including ADRs only listed in the pushed index), `search` (`spec`/`adr search` or `list`, with the query) or `unresolved` (target could not be resolved; raw target kept). Aggregate `counts` per class. |

`document_access` detects `takt spec|adr show/search/list` calls, `Read` of files under `specs/` and `adr/`, shell reads (`cat`, `head`, `tail`, `sed`, `less`) and `grep`/`rg` over those directories. Detection (`document_access_detect.py`) is pure parsing; resolution to a document id (`document_access.py`) uses the document stores of the worktree and the main checkout.

### Adding an analyser

1. Create `src/agent_takt/traces/analyzers/<name>.py` with `analyze_<name>(events, context=None) -> dict`. Ignore event kinds you do not know. Use only JSON-serialisable values.
2. Import it in `analyzers/__init__.py` and append `Analyzer("<name>", analyze_<name>)` to `REGISTRY`.

Nothing else changes: `takt trace analyze --analyzer <name>` picks it up. `merge_totals` combines per-trace results for feature-wide totals by summing numbers and concatenating lists, so keep result shapes to counts, dicts of counts and lists. `context` (`AnalyzerContext`) carries `bead_id`, `agent_type`, `linked_docs`, `feature_root_id`, `repo_root` and `main_checkout_root`.

## CLI

```bash
uv run takt trace list [--feature-root ID] [--bead ID] [--status STATUS] [--plain | --json]
uv run takt trace show <bead_id> [--seq N] [--kind KIND] [--errors] [--full] [--plain | --json]
uv run takt trace analyze [<bead_id>] [--feature-root ID] [--analyzer NAME ...] [--by-agent] [--json]
uv run takt trace backfill [--feature-root ID] [--bead ID] [--dry-run]
```

- `list` prints one row per record (`BEAD SEQ AGENT ROLE STATUS TOOLS STARTED`). `--feature-root` includes planner records for that tree.
- `show` prints the normalised events of one record. Without `--seq` it uses the latest `main` record. `--errors` keeps only tool calls that errored; `--full` and `--plain` stop excerpt truncation.
- `analyze` runs all analysers (or each `--analyzer`, repeatable) over one bead or a whole feature tree. `--by-agent` groups results by agent type.
- `backfill` captures traces for sessions found in existing telemetry artifacts that have no trace record. It skips sessions already recorded and sessions whose transcript is gone, ignores planner artifacts, and prints `{"captured", "missing", "skipped", "dry_run"}` as JSON. `--dry-run` writes nothing. Backfill captures even when `common.capture_traces` is `false`, but still honours `common.trace_max_bytes`.

Partial bead IDs resolve as in other `bead` commands.

## Configuration

Keys under `common:` in `.takt/config.yaml`:

| Key | Default | Meaning |
|---|---|---|
| `capture_traces` | `true` | When `false`, each session still gets a `disabled` record and a `trace_captured` event, but no transcript is read |
| `trace_max_bytes` | `52428800` (50 MiB) | Transcripts larger than this are recorded as `too_large`: events are kept, the raw copy is not |

## Events and audit

Every record written emits a `trace_captured` event, described in [events.md](events.md). `takt audit` includes a per-bead trace summary in its Agent activity section, described in [audit.md](audit.md).

## Privacy

Transcripts can contain file contents, command output and secrets that appeared during a session.

- `.takt/traces/` is added to `.gitignore` by `takt init`/`takt upgrade`. Traces are never committed.
- Traces are never ingested into memory or the document index.
- Traces are never sent anywhere by takt. `trace_captured` events carry metadata only, no transcript content.
- Traces stay local to the project. Treat the directory as sensitive when sharing a project folder or archiving it.

## Limitations

- **Claude Code only.** Codex runs get an `unsupported` record; there is no normalised data for them.
- **Needs session persistence.** Transcripts are read from Claude's own session store (`$CLAUDE_CONFIG_DIR` or `~/.claude`, `projects/*/<session_id>.jsonl`). If an operator adds `--no-session-persistence` to `claude.flags`, no transcript exists and every record is `missing`.
- **`document_access` does not infer intent.** It reports what was opened or searched, not why, and it cannot see documents that were only pushed into the prompt. Targets built dynamically in shell (variables, command substitution) may be `unresolved` or not detected.
- **Excerpts only.** Normalised events keep truncated text and result excerpts; the full content exists only in `raw.jsonl` when the status is `captured`.
- **No retention or pruning.** Traces accumulate until the bead is deleted. There is no size cap beyond the per-transcript `trace_max_bytes`, and no command to prune old traces.
- **Backfill depends on the transcript still existing** in Claude's session store.
