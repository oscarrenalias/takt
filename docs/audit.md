# Feature audit and merge approval records

`takt audit` produces a read-only audit report for a feature tree. `takt merge` uses the same report to print a pre-merge summary, save an audit snapshot, and record who approved the merge.

## `takt audit`

```bash
uv run takt audit <bead_id>                    # markdown report to stdout
uv run takt audit <bead_id> --json             # JSON report
uv run takt audit <bead_id> --output audit.md  # write to a file instead of stdout
```

`<bead_id>` can be any bead (or unique prefix) in the feature tree; the report covers the whole tree. The command is read-only: it writes no beads and no events. An unknown or ambiguous ID exits 1; an unwritable `--output` path exits 1 with an error.

## Report sections

The JSON report has `audit_schema_version` plus the sections below, rendered in this order in markdown (`trace` is rendered as **Agent activity**, after `beads`):

| Section | Contents |
|---|---|
| `feature` | Root bead ID and title, linked docs, branch, worktree path, `generated_at`, takt version |
| `status` | Bead counts by status and the list of beads that are not `done` |
| `beads` | Per bead: agent type, bead type, status, verdict, findings count, corrective and recovery child counts, wall-clock seconds, cost in USD, and a `trace` summary (see below) |
| `trace` | Feature-wide total of the per-bead trace summaries; `null` when no bead has traces |
| `gates` | Tester and review beads: final verdict, count of prior `needs_changes` results, and `verdict_overridden` history entries |
| `assumptions` | All handoff assumptions tagged with their bead (structural first) and structural/local counts |
| `criteria` | Met/unmet/descoped totals plus a list of unmet and descoped gaps with evidence |
| `changed_files` | Sorted union of `changed_files` across the tree |
| `cost` | Total cost, total wall clock, and number of beads with telemetry |
| `merges` | The feature root's `merge_records` (see below) |

## Per-bead trace summary

Each entry in `beads` carries a `trace` object built from the bead's [agent traces](traces.md), or `null` when the bead has no trace records or the summary could not be computed (a failure is logged as a warning and never fails the audit):

```json
{"traces": 2, "tool_calls": 41, "tool_errors": 3,
 "documents": {"linked": 2, "proactive": 1, "search": 4}}
```

- `traces`: number of trace records for the bead (any status).
- `tool_calls`, `tool_errors`: summed from the `tool_usage` analyser across the bead's records.
- `documents`: summed `document_access` counts. `linked` are specs/ADRs the bead had linked, `proactive` are other documents the agent opened itself, `search` are `spec`/`adr` search or list calls.

The top-level `trace` section sums these across the tree. In markdown, the **Agent activity** section lists one line per bead (`N trace(s), N tool calls (N errors), documents: N linked, N proactive, N search`, or `no traces`) followed by a `Feature total` line. Summaries cover only records whose normalised events were captured; `missing`, `unsupported` and `disabled` records count toward `traces` but contribute no tool calls.

**Missing data.** The markdown renderer prints `not recorded` for values that are null or empty (for example cost for a bead without telemetry), `Not recorded.` for `criteria` when no bead has criteria accounting, and short placeholders such as `No gate beads.`, `None recorded.` and `No merge records.` for empty sections. In JSON, missing values are `null` (`criteria` is `null` when nothing was recorded) and empty sections are empty lists or objects.

## Merge approval

`takt merge <bead_id>` requires an approver:

- `--approver "Name <email>"` is used if given (`approver_source: "flag"`).
- Otherwise the approver is git `user.name` (plus `<user.email>` when set) (`approver_source: "git-config"`).
- If neither is available, the command exits 1 with `cannot determine approver; set git user.name or pass --approver`.

After the preflight and test gate pass, `takt merge` prints a condensed pre-merge summary: bead counts, gates, assumption counts, criteria totals, test gate and preflight status, and the approver. Lines are highlighted as warnings when there are non-done beads, structural assumptions, unmet criteria, or a skipped/not-run/not-configured gate. The summary is informational; merge never prompts for confirmation.

## Merge record

After a successful merge, an entry is appended to `metadata.merge_records` on the feature root bead:

| Field | Meaning |
|---|---|
| `merged_at` | UTC ISO timestamp |
| `approver` | Approver name (and email) |
| `approver_source` | `flag` or `git-config` |
| `branch` | Feature branch merged |
| `feature_head_sha` | Feature branch head before merge |
| `main_sha_before` | `main` HEAD before merge |
| `main_sha_after` | `main` HEAD after merge (may be null if it cannot be read) |
| `preflight` | `passed`, `skipped` (`--skip-rebase`) or `not_run` |
| `test_gate` | `{status, command, duration_seconds}`; status is `passed`, `skipped` (`--skip-tests`) or `not_configured` |
| `audit_path` | Repo-relative path of the JSON snapshot |
| `audit_sha256` | SHA-256 of the JSON snapshot file |

Re-merging a feature appends another record; earlier ones are kept.

## Events

Written to `.takt/logs/events.jsonl`:

- `merged` — execution-history entry on the feature root bead: `Merged <branch> into main by <approver>`.
- `feature_merged` — event with the full merge record plus `feature_root_id`.
- `merge_gate_failed` — event with `feature_root_id`, `gate` (`preflight`, `tests` or `merge`), `reason`, `approver` and `at`; the `merge` gate also carries `audit_path`. Failures in the preflight or test gate also create a `merge-conflict` bead as before.

## Snapshot location

Each merge attempt that reaches the merge step writes two files under `.takt/audits/<feature_root_id>/`, named by UTC timestamp:

- `<timestamp>.json` — the audit report (hashed into `audit_sha256`)
- `<timestamp>.md` — the same report rendered as markdown

The snapshot is written before `git merge`, so it reflects the state that was approved. Takt does not commit these files; they stay untracked in the working tree.
