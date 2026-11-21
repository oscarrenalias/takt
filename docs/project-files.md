# Project files contract

This document describes the files under a project's `.takt/` directory that out-of-process readers may consume. It is the contract required by [ADR-1897d241](../adr/approved/adr-1897d241-takt-fleet-reads-projects-through-a-documented-set-of-takt-files-including-from-a-read-only-web-server.md): `takt-fleet` (including `takt-fleet serve`) reads a project only through the surfaces listed here, and every such file must be documented here before the fleet reads it.

Event records in `.takt/logs/events.jsonl` are documented separately in [events.md](events.md) (see [ADR-add43ad0](../adr/approved/adr-add43ad0-events-jsonl-is-the-cross-process-integration-surface.md)). The bead JSON file on disk is the source of truth for bead state ([ADR-053081ac](../adr/approved/adr-053081ac-bead-files-on-disk-are-the-source-of-truth-git-commits-of-bead-state-are-optional.md)). Control-flow fields such as `verdict`, `criteria_status` and `assumptions` are structured and schema-validated ([ADR-102056d3](../adr/approved/adr-102056d3-structured-verdicts-and-handoffs-drive-control-flow-never-prose.md)).

Sections:

- [Beads](#beads)
- [Traces](#traces)
- [Audits](#audits)
- [Telemetry](#telemetry)
- [version.json](#versionjson)
- [Events and active markers](#events-and-active-markers)
- [Compatibility rules](#compatibility-rules)
- [Readers](#readers)

## Beads

Path: `.takt/beads/<bead_id>.json`. One JSON object per bead, written atomically by `RepositoryStorage`. The object is the output of `Bead.to_dict()` (`src/agent_takt/models.py`), so every field below is written for every bead unless marked optional. "Always" means the key is present in every file written by a current takt; "optional" means a reader must cope with its absence (older files, or keys added later) and with `null`.

### Top-level keys

| Key | Type | Presence | Meaning |
|---|---|---|---|
| `bead_id` | string | always | Unique ID. Root beads `B-<8hex>`; children append a suffix (`-test`, `-docs`, `-review`, `-corrective`, `-recovery`). |
| `title` | string | always | Short human title. |
| `agent_type` | string | always | One of `planner`, `developer`, `tester`, `documentation`, `review`, `scheduler`, `recovery`, `investigator`, `defect`. |
| `description` | string | always | Full task description given to the worker. |
| `status` | string | always | `open`, `ready`, `in_progress`, `handed_off`, `blocked` or `done`. |
| `bead_type` | string | always | `task`, `epic`, `feature`, `merge-conflict`, `recovery` or `defect`. Defaults to `task`. |
| `parent_id` | string or null | always | Bead ID of the parent, or `null` for a root bead. |
| `dependencies` | array of string | always | Bead IDs that must be `done` before this bead runs. |
| `acceptance_criteria` | array of string | always | Criteria the worker must account for in `handoff_summary.criteria_status`. |
| `linked_docs` | array of string | always | Document references (`spec:<id>`, `adr:<id>`) or legacy file paths. |
| `feature_root_id` | string or null | always | ID of the root bead of the feature tree this bead belongs to. |
| `execution_branch_name` | string | always | Git branch the bead executes on (`feature/<root id lowercased>`); empty until assigned. |
| `execution_worktree_path` | string | always | Absolute path of the shared feature worktree; empty until assigned. |
| `expected_files` | array of string | always | Files the bead is planned to touch (scope claim). |
| `expected_globs` | array of string | always | Glob patterns for the planned scope. |
| `touched_files` | array of string | always | Files the worker reported touching. Never contains `.takt/beads/` paths. |
| `changed_files` | array of string | always | Files the worker reported changing. Never contains `.takt/beads/` paths. |
| `updated_docs` | array of string | always | Documentation files the worker reported updating. |
| `handoff_summary` | object | always | Structured worker result; see [handoff_summary](#handoff_summary). Fields hold empty defaults before the bead has run. |
| `block_reason` | string | always | Why the bead is blocked; empty when not blocked. May carry suffixes such as `(transient retries exhausted: N/N)`. |
| `conflict_risks` | string | always | Free-text conflict risks for downstream agents. |
| `branch_name` | string | always | Legacy per-bead branch name; usually empty. |
| `worktree_path` | string | always | Legacy per-bead worktree path; usually empty. |
| `lease` | object or null | always | Active execution lease `{owner, expires_at}` (both strings, `expires_at` ISO-8601), or `null` when not leased. |
| `retries` | integer | always | Number of manual or automatic retries. |
| `execution_history` | array of object | always | Append-only audit trail; see [execution_history entries](#execution_history-entries). |
| `metadata` | object | always | Free-form object; only the keys listed under [metadata keys](#metadata-keys-in-the-contract) are contract. May be `{}`. |
| `labels` | array of string | always | Free-form grouping labels (for example `discovered`, `urgent`). |
| `recovery_for` | string or null | always | For `recovery` beads, the ID of the bead being recovered; otherwise `null`. |
| `priority` | string or null | optional | `"high"` or `null`/absent for normal priority. `normal` is never persisted. |

A file may contain further top-level keys written by a newer takt. Readers must ignore keys they do not know (see [Compatibility rules](#compatibility-rules)).

### execution_history entries

`execution_history` is an array in chronological order. The first entry (`created`) gives the creation time. Each entry is an object:

| Key | Type | Presence | Meaning |
|---|---|---|---|
| `timestamp` | string | always | ISO-8601 UTC timestamp. |
| `event` | string | always | Event name, for example `created`, `skills_loaded`, `started`, `guardrails_applied`, `completed`, `failed`, `retried`, `merged`, `verdict_overridden`, `discovered_issue_created`, `git_commit_failed`. The set grows over time; readers must accept unknown names. |
| `agent_type` | string | always | Who recorded it (a worker agent type or `scheduler`). |
| `summary` | string | always | One-line human summary. |
| `details` | object | always | Event-specific payload; may be `{}`. Shape depends on `event` and is not contract beyond being an object. |

### handoff_summary

`handoff_summary` is an object holding the worker's structured result. All keys listed are present in current files; before a bead has run they hold their empty defaults.

| Key | Type | Presence | Meaning |
|---|---|---|---|
| `completed` | string | always | What was done. |
| `remaining` | string | always | What is left. |
| `risks` | string | always | Risks noted by the worker. |
| `verdict` | string | always | `approved` or `needs_changes` once the bead has run; `""` before. This is the control-flow signal for review and tester beads. |
| `findings_count` | integer | always | Number of unresolved findings. |
| `requires_followup` | boolean | always | Whether follow-up work is needed. |
| `changed_files` | array of string | always | Files changed (bead-state paths stripped). |
| `updated_docs` | array of string | always | Docs updated. |
| `next_action` | string | always | Suggested next step. |
| `next_agent` | string | always | Suggested next agent type. |
| `block_reason` | string | always | Block reason reported by the worker. |
| `expected_files` | array of string | always | Scope the worker reports. |
| `expected_globs` | array of string | always | Glob scope the worker reports. |
| `touched_files` | array of string | always | Files touched (bead-state paths stripped). |
| `conflict_risks` | string | always | Conflict risks for downstream agents. |
| `design_decisions` | string | always | Non-obvious design choices. |
| `test_coverage_notes` | string | always | What the tester should verify. |
| `known_limitations` | string | always | Known gaps. |
| `assumptions` | array of object | always | See below. |
| `criteria_status` | array of object | always | See below. |
| `discovered_issues` | array of object | optional | Out-of-scope problems reported by the worker, each `{title, description, agent_type, expected_files}`. The scheduler turns them into separate `open` beads labelled `discovered` and does not currently persist the list in the bead file, so readers should expect it to be absent and use `metadata.discovered_issue_of` to find the resulting beads. Documented so a future takt may write it without a contract change. |

**`assumptions`** entries: `{statement: string, rationale: string, scope: "local" | "structural"}`. After finalize, `scope` is always one of the two values (anything else is rewritten to `structural`) and duplicate statements are collapsed. Empty list when there are none.

**`criteria_status`** entries: `{index: integer, criterion: string, status: "met" | "unmet" | "descoped", evidence: string}`. After finalize there is exactly one entry per item in `acceptance_criteria`, in order, with `index` the 0-based position. `evidence` explains where a `met` criterion is satisfied or why a criterion is `descoped`. Empty list for a bead without acceptance criteria or that has not run.

### Metadata keys in the contract

`metadata` is a free-form object. Only the keys below are part of the contract; all are optional and any other key must be ignored.

| Key | Type | Where | Meaning |
|---|---|---|---|
| `merge_records` | array of object | feature root bead only, optional | One entry per successful `takt merge`: `merged_at`, `approver`, `approver_source`, `branch`, `feature_head_sha`, `main_sha_before`, `main_sha_after`, `preflight`, `test_gate`, `audit_path` (path of the audit snapshot, see [Audits](#audits)) and `audit_sha256`. Individual keys inside an entry may be `null`. Entries are appended, never rewritten. |
| `telemetry_history` | array of object | beads that have run with telemetry, optional | One lightweight telemetry record per attempt, oldest first, capped to the configured maximum number of attempts. Each record carries an `attempt` integer (1-based, counting from the first recorded attempt) plus the runner's telemetry fields (for example duration, token counts, cost). Heavy text fields (`prompt_text`, `response_text`) are never stored here. Telemetry field names depend on the runner and are not otherwise fixed; see [Telemetry](#telemetry). |
| `transient_retry_count` | integer | optional | Number of automatic transient requeues of this bead. Present only while retries are in progress or exhausted; removed when the bead completes `done` or is retried manually. |
| `auto_corrective_for` | string | optional | Set on auto-created `-corrective` beads: the ID of the bead (the one that blocked) the corrective is fixing. |
| `discovered_issue_of` | string | optional | Set on beads created from a worker's `discovered_issues`: the ID of the reporting bead. Accompanied by `discovered_in_feature` (feature root ID of the reporting bead), which is informational. |

## Traces

Placeholder. Path `.takt/traces/<bead_id>/*`. To be documented.

## Audits

Placeholder. Path `.takt/audits/<feature_root_id>/*`. To be documented.

## Telemetry

Placeholder. Path `.takt/telemetry/<bead_id>/*`. To be documented.

## version.json

Placeholder. Path `.takt/version.json`. To be documented.

## Events and active markers

Placeholder. Paths `.takt/logs/events.jsonl` and `.takt/runs/active/*.json`. The authoritative description today is [events.md](events.md); this section will summarise how it fits the contract.

## Compatibility rules

These rules apply to every file documented here, and follow the rule already in force for `events.jsonl` (ADR-add43ad0) and ADR-1897d241:

- **Additive and optional.** New fields may be added at any level, and a new field must be optional: a reader written before it existed must still work, and a reader must not require it.
- **Nothing removed, renamed or repurposed.** An existing field keeps its name, its type and its meaning. A field that is no longer useful stays in the file (possibly with an empty default) instead of being dropped.
- **Readers ignore unknown fields.** A reader must pass unknown keys, unknown `event` names, unknown `status`/`agent_type`/`bead_type` values and unknown metadata keys through or skip them, never fail on them. Files may come from a takt version older or newer than the reader.
- **Tolerate absence.** Keys marked optional, and any key in an older file, may be missing or `null`.
- **Same-change documentation.** Any change to a documented shape, including adding an optional field, must update this document in the same change. A change that cannot follow the rules above is a breaking change and requires a new ADR.

## Readers

The consumer of these files is `takt-fleet`, including `takt-fleet serve`. It reads them as plain JSON and tolerates drift between the fleet's version and each project's pinned takt version.

Readers never write these files. The fleet does not create, modify, lock or delete anything under a project's `.takt/`; mutations go only through the `takt` CLI. Reads must not assume a file is complete or stable: the scheduler may be writing at the same time, so a reader should treat a missing or unparsable file as absent and retry on the next poll.
